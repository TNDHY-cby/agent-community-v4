# -*- coding: utf-8 -*-
"""V-15 步骤5 测试：L3 接入产物按唤醒方式派生 + 平台自算路径通路。

对应设计稿 §十四 步骤5 的验证要求：
    「单测：产物落进工作区；**V-6 护栏仍拦 caller-supplied**」

安全边界（§八，本步骤存在的根本原因）：
    ❌ caller 传 out_dir      -> 调用方定路径 ≈ 任意文件写入（V-6 护栏拦，原样保留）
    ✅ 平台从 ws.workspace_dir 派生 -> 路径不可被请求输入影响 -> 可安全进工作区
"""
from __future__ import annotations

import asyncio
import json

import pytest

from agent_community.platform import connect_artifact as ca
from agent_community.platform.protocol import HarnessInfo, WakeupMethod
from agent_community.platform.workshop import Workshop, WorkshopMember


def _ws(tmp_path, wid="w1"):
    # ⚠️ 必须先拼字符串再交给 / ：`tmp_path / "ws_" + wid` 是 Path+str，直接 TypeError
    d = tmp_path / ("ws_" + wid)
    return Workshop(workshop_id=wid, name="t", workspace_dir=str(d),
                    hall_content="hall")


def _info(wm, **kw):
    base = dict(harness_id="示例Harness-A", harness_name="示例Harness-A",
                wakeup_method=wm, acp_command="npx -y acp-bridge",
                acp_cwd="D:\\bridge", wakeup_dir="", description="示例",
                ai={"model_name": "示例模型"})
    base.update(kw)
    return HarnessInfo(**base)


class FakeReq:
    def __init__(self, body=None, headers=None):
        self.headers = headers or {}
        self._b = body if body is not None else {}

    async def json(self):
        return self._b


def run(coro):
    return asyncio.run(coro)


# ── A. 四档派生（§5.3 表）─────────────────────────────────────────
class TestArtifactKindDerivation:
    @pytest.mark.parametrize("wm,kind,fn", [
        ("acp", "script", "bridge.py"),
        ("file_poll", "script", "bridge.py"),
        ("http_api", "contract", "connect_contract.json"),
        ("clipboard", "prompt", "activation_prompt.txt"),
    ])
    def test_four_tiers(self, wm, kind, fn):
        assert ca.artifact_kind(wm) == kind, f"{wm} 应派生 {kind}"
        assert ca.FILENAME[kind] == fn

    def test_unknown_falls_back_to_script(self):
        """不在表里的按有桥处理（与 auto-connect else 分支一致）。"""
        assert ca.artifact_kind("mcp") == "script"
        assert ca.artifact_kind("") == "script"

    def test_plan_target_dir_comes_only_from_ws(self, tmp_path):
        """**路径只来自 ws.workspace_dir** —— 请求体塞路径也无人读。"""
        ws = _ws(tmp_path)
        info = _info(WakeupMethod.ACP)
        pl = ca.plan(ws, info)
        assert pl["target_dir"] == str(ws.workspace_dir)
        assert pl["complete"] is True
        assert "WORKSPACE_DIR" in pl["params"]

    def test_plan_has_no_user_path_input(self, tmp_path):
        """plan() 的签名不接受任何路径参数。"""
        import inspect
        params = inspect.signature(ca.plan).parameters
        for banned in ("out_dir", "target_dir", "path", "workspace_dir"):
            assert banned not in params, f"plan() 不得接受路径参数 {banned}"


# ── B. 产物真的落进工作区 ────────────────────────────────────────
class TestArtifactLandsInWorkspace:
    def test_contract_lands_in_workspace(self, tmp_path):
        ws = _ws(tmp_path, "wc")
        info = _info(WakeupMethod.HTTP_API, api_base_url="http://127.0.0.1:9500")
        pl = ca.plan(ws, info)
        assert pl["artifact_kind"] == "contract"
        out = ca.render(pl)
        assert out["ok"], out.get("error")
        p = out["path"]
        assert p.startswith(str(ws.workspace_dir)), f"产物必须落在工作区，实得 {p}"
        data = json.loads(open(p, encoding="utf-8").read())
        assert data["workspace_dir"] == ws.workspace_dir
        assert "activation-result" in data["callbacks"]["activation"]
        assert data["activation_body"]["context_turns"] == 1, "回执契约字段要写进契约"

    def test_prompt_lands_in_workspace(self, tmp_path):
        ws = _ws(tmp_path, "wp")
        info = _info(WakeupMethod.CLIPBOARD)
        pl = ca.plan(ws, info)
        assert pl["artifact_kind"] == "prompt"
        out = ca.render(pl)
        assert out["ok"], out.get("error")
        txt = open(out["path"], encoding="utf-8").read()
        assert str(ws.workspace_dir) in txt, "提示词要含工作区坐标"
        assert "人工激活提示词" in txt, "要标明这是人工粘贴用的"

    def test_script_lands_in_workspace(self, tmp_path):
        """有桥档：走 bridge_factory 渲染 L1 模板，同样落工作区。"""
        ws = _ws(tmp_path, "ws")
        info = _info(WakeupMethod.ACP)
        pl = ca.plan(ws, info)
        out = ca.render(pl)
        assert out["ok"], out.get("error")
        assert out["path"].startswith(str(ws.workspace_dir))
        assert out["bytes"] > 100, "脚本应有实际内容"

    def test_workspace_created_if_missing(self, tmp_path):
        """工作区目录还不存在时，产物自建目录。"""
        ws = _ws(tmp_path, "wnew")
        import os
        assert not os.path.isdir(ws.workspace_dir)
        pl = ca.plan(ws, _info(WakeupMethod.CLIPBOARD))
        out = ca.render(pl)
        assert out["ok"]
        assert os.path.isdir(ws.workspace_dir)


# ── C. V-6 护栏原样保留（绝不拆护栏）────────────────────────────
class TestV6GuardStillWorks:
    def test_bridge_generate_rejects_outside_out_dir(self, tmp_path):
        """caller 传越界 out_dir -> 仍 400（护栏未被本步骤削弱）。"""
        from agent_community.platform import harness_adapter
        from agent_community.platform.routers import harness_bridge as hb

        info = _info(WakeupMethod.ACP, harness_id="示例Harness-G",
                     harness_name="示例Harness-G")
        harness_adapter.harness_manager.register(info)
        try:
            req = FakeReq({"out_dir": str(tmp_path / "escape")})
            resp = run(hb.harness_bridge_generate("示例Harness-G", req))
            assert getattr(resp, "status_code", 0) == 400, \
                f"越界 out_dir 应被 400，实得 {getattr(resp, 'status_code', resp)}"
            body = json.loads(resp.body.decode("utf-8"))
            assert "bridges" in body.get("error", ""), "应说明必须在 bridges 目录内"
        finally:
            harness_adapter.harness_manager.unregister("示例Harness-G")

    def test_new_endpoint_rejects_path_field(self, tmp_path):
        """新通路的硬约束：请求体带路径字段 -> 400，根本不读它。"""
        from agent_community.platform.routers import workshops_lifecycle as wl

        ws = _ws(tmp_path, "wg")
        from agent_community.platform.state import workshops as _wsmap
        _wsmap[ws.workshop_id] = ws
        try:
            for bad in ("out_dir", "target_dir", "workspace_dir", "path", "dir"):
                req = FakeReq({bad: str(tmp_path / "escape")})
                resp = run(wl.workshop_connect_artifact(ws.workshop_id, req))
                assert getattr(resp, "status_code", 0) == 400, \
                    f"带 {bad} 应 400，实得 {getattr(resp, 'status_code', resp)}"
                assert bad in json.loads(resp.body.decode("utf-8")).get("error", "")
        finally:
            _wsmap.pop(ws.workshop_id, None)

class _AllowEv:
    """放行的 Evaluation 替身 —— 让端点穿过闸门走到 render。"""
    decision = "allow"
    blocked = False
    needs_approval = False
    rule_id = "test.allow"
    op = "bridge.write"
    note = ""


def _allow_check(*a, **k):
    return _AllowEv(), None


def test_new_endpoint_never_reads_body_path(tmp_path, monkeypatch):
    """产物必须落 `ws.workspace_dir`，而不是 body 里的任何东西。

    ⚠️ 本用例**必须放行策略闸门** —— 否则端点 202 返回，断言会空转，
    "测试通过"但什么都没验证（比没有测试更糟）。
    """
    from agent_community.platform import harness_adapter, policy as pm
    from agent_community.platform.routers import workshops_lifecycle as wl
    from agent_community.platform.state import workshops as _wsmap

    monkeypatch.setattr(pm, "check", _allow_check)

    info = _info(WakeupMethod.CLIPBOARD, harness_id="示例Harness-P",
                 harness_name="示例Harness-P")
    harness_adapter.harness_manager.register(info)
    ws = _ws(tmp_path, "wp2")
    ws.members.append(WorkshopMember(member_id="m1", role="员工",
                                     display_name="甲", harness_ids=["示例Harness-P"]))
    _wsmap[ws.workshop_id] = ws
    try:
        out = run(wl.workshop_connect_artifact(ws.workshop_id, FakeReq({})))
        # dict 形态返回 = 成功走完全程（不是 Utf8JSONResponse 的错误分支）
        assert isinstance(out, dict), f"应返回 dict，实得 {type(out)}"
        assert out["success"] is True, out
        assert out["plan"]["target_dir"] == str(ws.workspace_dir), \
            "产物计划的落点必须是 ws.workspace_dir"
        assert out["artifact"]["path"].startswith(str(ws.workspace_dir)), \
            f"产物必须落在工作区，实得 {out['artifact']['path']}"
        assert out["artifact"]["artifact_kind"] == "prompt", "clipboard 档应出提示词"
    finally:
        _wsmap.pop(ws.workshop_id, None)
        harness_adapter.harness_manager.unregister("示例Harness-P")
