# -*- coding: utf-8 -*-
"""V54 守卫：**登记前话术**（复制给 AI 让它自己注册）—— 6 种协议各一套，且**真能用**。

老板 2026-10-17：「做，**记得不同协议对应不同模板**」⇒ 本支把这句话变成可断言的判据：

1. **不同协议不同模板**：六种协议生成的话术**内容真的不同**（步数/桥型/边界），
   且**不许张冠李戴**（`http_api` 的话术里不能说"要起桥"，`clipboard` 不能给"叫醒命令"）；
2. **一处真相**：话术里的步数逐条等于 `interface_guidance.for_wakeup()` 的输出（不是另写一套）；
3. **话术真能用**（端到端）：把话术里的 `minimal_json` **真的 POST 给注册端点**，六种协议都要成功；
4. **地址不写死**：平台地址跟着 `platform_address` 走（换端口 ⇒ 话术里跟着变）；
5. **未知协议如实说**：不猜，给"先自测四项"的通用版。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

from agent_community.platform import interface_guidance as ig          # noqa: E402
from agent_community.platform import register_prompt as rp            # noqa: E402
from agent_community.platform.routers import harness_register as hr    # noqa: E402
from agent_community.platform import state as _state                  # noqa: E402

BASE = "http://127.0.0.1:29417"


class _Req:
    def __init__(self, body=None, query=None):
        self._body = body or {}
        self.query_params = query or {}
        self.headers = {"host": "127.0.0.1:29417"}

    async def json(self):
        return self._body


def _resp(resp):
    if isinstance(resp, dict):
        return 200, resp
    try:
        return getattr(resp, "status_code", 200), json.loads(getattr(resp, "body", b"{}").decode("utf-8"))
    except Exception:  # noqa: BLE001
        return getattr(resp, "status_code", 200), {}


# ── ① 六种协议各一套，且内容真的不同 ────────────────────────────────────────
def test_each_protocol_gets_its_own_template():
    prompts = {w: rp.build(w, platform_url=BASE)["prompt"] for w in rp.SUPPORTED}
    assert len(prompts) == 6
    assert len(set(prompts.values())) == 6, "六种协议生成的话术不许重复"


def test_http_api_prompt_says_no_bridge():
    p = rp.build("http_api", platform_url=BASE)["prompt"]
    assert "不需要桥" in p, "http_api 这一档**不需要桥**，话术必须这么写（否则用户会去起多余的桥）"
    assert "起桥：不适用" in p or "第 3 步 起桥：不适用" in p


def test_acp_prompt_requires_command_and_bridge():
    d = rp.build("acp", platform_url=BASE)
    p = d["prompt"]
    assert d["bridge_kind"] == "cli_acp"
    assert "acp_command" in p, "acp 必须让 agent 自己给出那条拉起命令"
    assert "需要" in p and "第 3 步 起桥：需要" in p


def test_clipboard_prompt_says_manual_no_wake_command():
    p = rp.build("clipboard", platform_url=BASE)["prompt"]
    assert "没有叫醒命令" in p
    assert "人工" in p


def test_file_poll_prompt_asks_for_dir():
    p = rp.build("file_poll", platform_url=BASE)["prompt"]
    assert "wakeup_dir" in p and "盯" in p


# ── ② 一处真相：步数逐条等于 interface_guidance ─────────────────────────────
def test_steps_come_from_interface_guidance():
    for w in rp.SUPPORTED:
        d = rp.build(w, platform_url=BASE)
        assert d["steps"] == ig.for_wakeup(w)["steps"], "%s 的步数与 interface_guidance 不一致" % w
        for step, verdict in d["steps"].items():
            assert "%s：%s" % (ig.STEP_LABEL[step], verdict) in d["prompt"], \
                "%s 的话术里缺「%s=%s」这一行" % (w, ig.STEP_LABEL[step], verdict)


# ── ③ 端到端：话术里的最小 JSON 真能注册（六种协议都要过）───────────────────
@pytest.mark.parametrize("wake", rp.SUPPORTED)
def test_minimal_json_actually_registers(wake, monkeypatch):
    d = rp.build(wake, platform_url=BASE, harness_id="H_%s" % wake)
    body = {k: v for k, v in d["minimal_json"].items()}
    # 只保留真实可用的值（把占位说明换成一个能过校验的值）
    # ⚠️ `wakeup_dir` 必须是**绝对路径**（平台真校验）；`http` 档仍要 `acp_command`（端点既有口径）
    import tempfile
    _abs = os.path.join(tempfile.gettempdir(), "ac_inbox")
    for k, v in list(body.items()):
        if isinstance(v, str) and v.startswith("<"):
            body[k] = {"acp_command": "示例Agent --noop",
                       "wakeup_dir": _abs,
                       "api_base_url": "http://127.0.0.1:18080/agent",
                       "mcp_server": "http://127.0.0.1:18081/mcp"}.get(k, "x")
    monkeypatch.setattr(hr, "_save_state", lambda *a, **k: None, raising=False)
    code, out = _resp(asyncio.run(hr.register_harness(_Req(body))))
    assert code in (200, 201), "话术里的最小 JSON 注册失败（%s）：%s" % (wake, out)
    _state.agents.pop(body["harness_id"], None)


# ── ④ 地址跟着平台解析点走（不许写死端口）────────────────────────────────
def test_prompt_uses_resolved_platform_address(monkeypatch):
    import agent_community.platform_address as pa
    monkeypatch.setattr(pa, "base_url", lambda *a, **k: "http://127.0.0.1:29417", raising=True)
    code, out = _resp(asyncio.run(hr.harness_register_prompt(_Req(query={"wakeup_method": "acp"}))))
    assert code == 200 and "29417" in out["prompt"], "话术里的平台地址必须来自解析点"
    assert ":18920" not in out["prompt"], "不许出现写死的 18920"


def test_prompt_has_no_local_absolute_paths():
    p = rp.build("acp", platform_url=BASE)["prompt"]
    assert not re.search(r"[A-Za-z]:\\\\", p), "话术里不许出现本机盘符路径"


# ── ⑤ 未知协议：如实说不知道 + 先自测四项 ────────────────────────────────
def test_unknown_protocol_is_honest_and_gives_self_test():
    d = rp.build("wat", platform_url=BASE)
    assert d["known_protocol"] is False
    assert "先自测四项" in d["prompt"]
    for kw in ("acp", "file_poll", "http_api", "clipboard", "mcp", "http"):
        assert kw in d["prompt"], "通用版要把六种选择都摆出来（%s 缺）" % kw


def test_json_format_returns_structured_fields():
    code, out = _resp(asyncio.run(hr.harness_register_prompt(
        _Req(query={"wakeup_method": "file_poll", "format": "json"}))))
    assert code == 200
    for k in ("prompt", "minimal_json", "steps", "bridge_kind", "supported_protocols", "source"):
        assert k in out, "json 形态缺字段 %s" % k
    assert out["source"] == "interface_guidance"
