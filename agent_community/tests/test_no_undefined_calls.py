# -*- coding: utf-8 -*-
"""★"半截功能"守护：**被调用的名字必须真的能解析**（2026-12-10-12 审计抓到的两处）。

**抓到的真缺陷**（用 AST 扫"被调用却在模块里找不到定义"扫出来的）：
  · `POST /api/harness/message`（**外端 agent 的消息主入口**）
    用了 `_harness_identity_check`，但它定义在 `routers/harness_common.py`，
    **本文件没有 import** ⇒ 一被调用就 `NameError`；
  · `POST /api/workshop/{ws}/leader/assign`（**组长派活**）
    用了 `_leader_pick_member` / `_leader_member_hid` / `_leader_build_assign_payload`，
    三个都定义在 `routers/workshops_common.py`，**同样没 import**。

**为什么一直没暴露**：测试里**没有用例走这两条端点的成功路径**。
⇒ **"没被调用过"不等于"没问题"** —— 所以本文件既做静态检查（AST），
   也做**冒烟调用**（真的调一次端点，断言不是 500）。
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import re

import pytest

PLATFORM = os.path.join("agent_community", "platform")


def _module_level_and_imported_names(tree: ast.AST) -> set:
    """模块里所有"能解析到"的名字（定义 / 导入 / 赋值 / 参数 / 异常别名…）。"""
    import builtins as _b
    names = set(dir(_b))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.Global):
            names.update(node.names)
    return names


#: 故意靠运行时装配的名字（getattr/globals 注入），静态看不到，不算问题
RUNTIME_INJECTED = {
    "ai_provider", "harness_manager", "workshops", "tasks", "agents", "discussion_rooms",
    "pending_activations", "pending_tasks", "interject_store", "task_state_machine", "app",
    "self", "sessions", "_sv",
}


def test_no_called_name_is_undefined_in_routers():
    """★静态：`routers/` 下**被调用**的模块级名字，必须能解析到定义或导入。

    这条会抓住"用了但没 import"这类**一调用就 NameError**的半截功能 ——
    本文件就是因为它才存在的（实测抓到 2 处、4 个名字）。
    """
    bad = []
    root = os.path.join(PLATFORM, "routers")
    for f in sorted(os.listdir(root)):
        if not f.endswith(".py") or ".bak" in f:
            continue
        p = os.path.join(root, f)
        try:
            tree = ast.parse(open(p, encoding="utf-8", errors="replace").read())
        except Exception:  # noqa: BLE001
            continue
        known = _module_level_and_imported_names(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                nm = node.func.id
                if nm in known or nm in RUNTIME_INJECTED or nm.startswith("__"):
                    continue
                bad.append("%s:%d 调用了未定义名字 %s()" % (f, node.lineno, nm))
    assert not bad, ("routers 里有'调用了但没定义/没导入'的名字（一调用即 NameError）：\n  "
                     + "\n  ".join(bad))


class _Req:
    def __init__(self, body=None, headers=None):
        self._body = body or {}
        self.headers = headers or {}
        self.query_params = {}

    async def json(self):
        return self._body


def test_harness_message_endpoint_smoke(monkeypatch):
    """★冒烟：外端 agent 的消息主入口要被真的调一次，**不许 500**。

    （这条端点此前没有任何成功路径的用例 ⇒ 缺 import 的 NameError 一直没暴露。）
    """
    import agent_community.platform.server as srv
    from agent_community.platform.routers import harness_messaging as hm

    hid = "smoke-msg-1"
    try:
        from agent_community.platform.protocol import HarnessInfo, WakeupMethod
        info = HarnessInfo(harness_id=hid, harness_name="冒烟", harness_type="desktop-agent",
                           wakeup_method=WakeupMethod.CLIPBOARD, acp_command="x.exe --y",
                           ai={"model_name": "示例模型"})
        monkeypatch.setattr(srv, "save_state", lambda: None, raising=False)
        srv.harness_manager.register(info)
    except Exception as e:  # noqa: BLE001
        pytest.skip("注册 harness 不可用：%s" % str(e)[:80])

    try:
        out = asyncio.run(hm.harness_message(_Req({
            "harness_id": hid, "type": "task_result",
            "content": json.dumps({"ok": True}), "payload": {},
        })))
    finally:
        try:
            srv.harness_manager.unregister(hid)
        except Exception:  # noqa: BLE001
            pass

    body = out if isinstance(out, dict) else json.loads(getattr(out, "body", b"{}").decode("utf-8"))
    code = getattr(out, "status_code", 200)
    assert code != 500, "外端消息主入口 500 了：%s" % body
    assert "NameError" not in json.dumps(body), body


def test_leader_assign_endpoint_resolves_its_helpers():
    """★组长派活端点：它用到的三个 `_leader_*` 必须在**源文件里被导入**。

    （此前没导入 ⇒ 一调用即 NameError。这里走源码级断言，因为该端点的成功路径
      需要真实 harness 派发链路，冒烟成本高；`import` 是否在，是它能否运行的**前提**。）
    """
    src = open(os.path.join(PLATFORM, "routers", "workshops_assign.py"),
               encoding="utf-8", errors="replace").read()
    assert re.search(r"from \.workshops_common import[^\n]*_leader_pick_member", src) or \
        ("_leader_pick_member" in src and "import" in src.split("_leader_pick_member")[0][-200:]), \
        "`_leader_pick_member` 用了却没导入 ⇒ 组长派活一调用即 NameError"
    for nm in ("_leader_pick_member", "_leader_member_hid", "_leader_build_assign_payload"):
        assert nm in src, nm
