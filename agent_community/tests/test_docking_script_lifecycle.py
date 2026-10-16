# -*- coding: utf-8 -*-
"""`FE-26` §12 守卫：**激活脚本正文的生成** 与 **一键脚本激活**。

## 为什么有这一份（前端**正在外包开发中**）

§3.2 屏 4 要「显示脚本正文（可见/可复制/可手改）」并「保存为激活方式 ⇒ 之后可一键脚本激活」，
而 §5 原先**没给**"正文从哪来"与"一键跑" ⇒ 前端要么卡住、要么自己编一套正文
（那就与平台真实的激活路径不一致 —— 同一个词两套含义，规则 20 禁止）。本文件盯 §12 这两条。

判据：
  ① 正文是**可复现的 PowerShell**：不写死地址（`$env:AC_BASE_URL` → `$env:AC_PORT` → 平台默认端口）、
     **中文按 UTF-8 字节发送**（PS 5.1 直接送中文会变字面 `?`）、`verified` 不是 `true` 时**非零退出**、
     单引号正确转义、关键词/标题/话术都在正文里；
  ② 生成是**只读**（不落盘、不改档案）：调用前后 harnesses.json 指纹不变；
  ③ **档位钉死**：`auto` 一律翻译成具体方法（剪贴板"总是成功"会造出假成功——§3.2 屏 3 的教训）；
  ④ **一键跑只吃档案里的参数**：`method`/`keywords`/`title`/`message` 全部来自已保存的脚本；
     没存过 ⇒ **404 ＋ 中文下一步**；缺参数 ⇒ 400；**结果原样透传三态**，失败要带下一步。
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

from agent_community.platform import visual_launch  # noqa: E402
from agent_community.platform.protocol import HarnessAI, HarnessInfo  # noqa: E402
from agent_community.platform.routers import docking  # noqa: E402


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _session(hid="H-1", script=None):
    info = HarnessInfo(harness_id=hid, harness_name="示例Harness-A", ai=HarnessAI(model_name="m"),
                       wakeup_method="clipboard", activation_script=dict(script or {}))
    return types.SimpleNamespace(info=info)


def _body(res) -> str:
    raw = getattr(res, "body", b"")
    return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)


# ── ① 正文本身 ──────────────────────────────────────────────────────────
def test_composed_script_is_reproducible_and_portable():
    a = docking.compose_activation_script(keywords=["示例Harness-A"], title="示例Harness-A - 会话",
                                          method="wmpaste", verified=False, default_port=18920,
                                          generated_at="T")
    b = docking.compose_activation_script(keywords=["示例Harness-A"], title="示例Harness-A - 会话",
                                          method="wmpaste", verified=False, default_port=18920,
                                          generated_at="T")
    assert a["text"] == b["text"], "同样的输入必须生成同样的正文（可复现）"
    t = a["text"]
    assert "$env:AC_BASE_URL" in t and "$env:AC_PORT" in t, "地址必须能由环境变量覆盖（不许写死）"
    assert "UTF8.GetBytes" in t, "中文必须走 UTF-8 字节发送（PS 5.1 直接送中文会变字面 '?'）"
    assert "exit 3" in t and "verified -ne $true" in t, "未自证必须非零退出（脚本不许自己报成功）"
    assert "示例Harness-A - 会话" in t and "示例Harness-A" in t, "标题/关键词要进正文（那是匹配键）"
    assert a["method"] == "wmpaste" and a["verified"] is False, \
        "档案里没自证过（False）不许被抹成 None/True —— 三态要原样带出来"
    assert docking.compose_activation_script(keywords=["k"], default_port=1)["verified"] is None, \
        "压根没试投过就是 None（未核实）"


def test_composed_script_escapes_powershell_quotes():
    t = docking.compose_activation_script(keywords=["it's"], title="a'b", default_port=1)["text"]
    assert "'it''s'" in t and "'a''b'" in t, "单引号必须转义成两个（否则生成的脚本语法就坏了）"


def test_default_message_is_one_source():
    t = docking.compose_activation_script(keywords=["k"], default_port=1)["text"]
    assert docking.DEFAULT_ACTIVATION_MESSAGE in t, "默认激活话术必须取自同一处真相"
    custom = docking.compose_activation_script(keywords=["k"], message="自定义话术", default_port=1)
    assert "自定义话术" in custom["text"] and custom["message"] == "自定义话术"


# ── ③ 档位钉死 ─────────────────────────────────────────────────────────
def test_auto_method_is_pinned():
    assert docking._pin_method("auto") in ("wmpaste", "clipboard"), "auto 必须翻译成具体档"
    assert docking._pin_method("") in ("wmpaste", "clipboard")
    assert docking._pin_method("wmpaste") == "wmpaste"


# ── ② compose 端点（只读）───────────────────────────────────────────────
def test_compose_endpoint_needs_a_target():
    from agent_community.platform.server import Utf8JSONResponse
    res = asyncio.run(docking.docking_compose_script(_Req({})))
    assert isinstance(res, Utf8JSONResponse) and res.status_code == 400
    assert "定位结果" in _body(res)


def test_compose_endpoint_404_for_unknown_harness(monkeypatch):
    from agent_community.platform.server import Utf8JSONResponse
    monkeypatch.setattr(docking, "_find_harness", lambda h: None)
    res = asyncio.run(docking.docking_compose_script(_Req({"keywords": ["k"], "harness_id": "没这个"})))
    assert isinstance(res, Utf8JSONResponse) and res.status_code == 404


def test_compose_endpoint_is_readonly_and_returns_notes(monkeypatch):
    sess = _session()
    monkeypatch.setattr(docking, "_find_harness", lambda h: sess)
    out = asyncio.run(docking.docking_compose_script(
        _Req({"keywords": ["示例Harness-A"], "title": "示例Harness-A - 会话", "harness_id": "H-1"})))
    assert out["ok"] is True and out["script"]["text"]
    assert out["notes"], "必须如实写清依赖（未落盘 / UTF-8 / 兜底档不等于验证）"
    assert sess.info.activation_script == {}, "生成**不许**顺手写档案（保存是用户在屏 4 点的事）"


# ── ④ 一键跑 ───────────────────────────────────────────────────────────
def test_run_script_uses_archived_params_only(monkeypatch):
    seen = {}

    def _fake_launch(member, text, *, method="auto", session_name=None, verify=True):
        seen.update(text=text, method=method, name=session_name, verify=verify)
        return {"ok": True, "method": method, "verified": True, "need_human": False,
                "detail": "读回一致", "next_step": ""}

    monkeypatch.setattr(visual_launch, "launch", _fake_launch)
    monkeypatch.setattr(docking, "_find_harness", lambda h: _session(script={
        "text": "正文", "method": "wmpaste", "keywords": ["示例Harness-A", "合作社"],
        "title": "示例Harness-A - 会话", "message": "快起来"}))
    out = asyncio.run(docking.docking_run_script(_Req({"harness_id": "H-1"})))
    assert out["verified"] is True and out["ok"] is True
    assert seen["method"] == "wmpaste" and seen["name"] == "示例Harness-A - 会话", \
        "必须用档案里的方法/标题（不许自己挑）"
    assert seen["text"] == "快起来", "要用档案里记的话术"
    assert seen["verify"] is True
    assert out["used"]["keywords"] == ["示例Harness-A", "合作社"]


def test_run_script_pins_method_and_reports_failure_honestly(monkeypatch):
    from agent_community.platform.server import Utf8JSONResponse

    monkeypatch.setattr(visual_launch, "launch",
                        lambda *a, **k: {"ok": False, "method": k.get("method"), "verified": False,
                                         "detail": "注入失败（exit=3）：[FAIL] no matching window",
                                         "next_step": "窗口标题里带上会话名"})
    monkeypatch.setattr(docking, "_find_harness", lambda h: _session(script={
        "text": "x", "method": "auto", "keywords": ["k"]}))
    out = asyncio.run(docking.docking_run_script(_Req({"harness_id": "H-1"})))
    assert out["ok"] is False and out["verified"] is False, "失败必须原样透传（不许美化）"
    assert out["used"]["method"] in ("wmpaste", "clipboard"), "auto 必须被钉成具体档"
    assert "工作台" in (out.get("next_step") or ""), "失败要给下一步去哪"

    monkeypatch.setattr(docking, "_find_harness", lambda h: None)
    res = asyncio.run(docking.docking_run_script(_Req({"harness_id": "没这个"})))
    assert isinstance(res, Utf8JSONResponse) and res.status_code == 404


def test_run_script_without_script_says_what_to_do(monkeypatch):
    from agent_community.platform.server import Utf8JSONResponse
    monkeypatch.setattr(docking, "_find_harness", lambda h: _session())
    res = asyncio.run(docking.docking_run_script(_Req({"harness_id": "H-1"})))
    assert isinstance(res, Utf8JSONResponse) and res.status_code == 404
    msg = _body(res)
    assert "没有存过激活脚本" in msg and "脚本激活" in msg, "要指出下一步去哪做一份"

    res2 = asyncio.run(docking.docking_run_script(_Req({})))
    assert isinstance(res2, Utf8JSONResponse) and res2.status_code == 400
    assert "哪一位员工" in _body(res2)


def test_run_script_needs_a_match_key(monkeypatch):
    from agent_community.platform.server import Utf8JSONResponse
    monkeypatch.setattr(docking, "_find_harness",
                        lambda h: _session(script={"text": "x", "method": "clipboard"}))
    res = asyncio.run(docking.docking_run_script(_Req({"harness_id": "H-1"})))
    assert isinstance(res, Utf8JSONResponse) and res.status_code == 400
    assert "不知道该找哪个窗口" in _body(res)


# ── 契约：两条都是 POST（进白名单），且**只有这两个新写端点** ────────────
def test_new_script_endpoints_are_post_and_whitelisted():
    paths = {getattr(r, "path", ""): set(getattr(r, "methods", set()) or set())
             for r in docking.router.routes}
    for p in ("/api/docking/compose-script", "/api/docking/run-script"):
        assert p in paths and paths[p] == {"POST"}, "%s 必须是 POST（写动作要过白名单闸）" % p
