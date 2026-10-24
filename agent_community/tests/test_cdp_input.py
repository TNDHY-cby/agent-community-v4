# -*- coding: utf-8 -*-
"""守卫：**浏览器级投递（CDP）**—— 老板 2026-10-23「不能是真鼠标」。

真机判据是 `tools/browser_input_check.py`（自己拉 headless Chromium，三档投递 + 读回核对）；
这里钉住**不依赖浏览器**的部分，让它们在双平台（含 Linux）都能跑：

| # | 判据 |
|---|---|
| ① | 调试端口从 `AC_CDP_PORT` 读（可逗号分隔），默认 9222~9225；**不写死** |
| ② | 没有可用端口时 `probe()` **如实失败**，并说清"目标要带 --remote-debugging-port 启动" |
| ③ | `_js_str` 正确转义引号/换行（把文本嵌进 JS 时不许把脚本拼坏） |
| ④ | 输入框 idx 越界 ⇒ **快失败**且说清"只有 N 个"（不许拿 activeElement 冒充、不许谎报成功） |
| ⑤ | `enter` 默认不发（有些聊天框 Enter＝发送）；`set_web/get_web` 与"表层交互的位置"**分开存** |
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


@pytest.fixture()
def client():
    from agent_community.platform import server as srv

    return TestClient(srv.app, client=("127.0.0.1", 54321))


def test_ports_come_from_env_not_hardcoded(monkeypatch):
    from agent_community.platform import cdp_input as c

    monkeypatch.delenv("AC_CDP_PORT", raising=False)
    assert c.debug_ports() == c.DEFAULT_PORTS
    monkeypatch.setenv("AC_CDP_PORT", "9333, 9222")
    assert c.debug_ports() == (9333, 9222)
    monkeypatch.setenv("AC_CDP_PORT", "不是数字")
    assert c.debug_ports() == c.DEFAULT_PORTS, "环境变量写错时要退回默认，不能崩"


def test_probe_without_port_explains_how_to_enable():
    from agent_community.platform import cdp_input as c

    r = c.probe([9999])
    assert r["ok"] is False and r["ports"] == []
    assert "remote-debugging-port" in r["why"], r["why"]


def test_js_string_escaping_is_safe():
    from agent_community.platform import cdp_input as c

    for s in ('a"b', "a'b\nc", "反斜杠\\", "</script>"):
        out = c._js_str(s)
        assert out.startswith('"') and out.endswith('"'), out
        assert "\n" not in out[1:-1], "换行必须被转义，否则 JS 会被拼坏：%r" % out


def test_index_out_of_range_fails_fast_and_honestly(monkeypatch):
    """idx 越界 ⇒ 立刻失败、说清"只有 N 个"，**不许**退到 activeElement 冒充成功。"""
    import asyncio

    from agent_community.platform import cdp_input as c

    class FakeCDP:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def evaluate(self, expr):
            return {"ok": True, "value": '[{"idx":0,"tag":"TEXTAREA","editable":false}]', "error": None}

        async def call(self, *a, **kw):
            raise AssertionError("越界时不该再发任何投递命令")

    monkeypatch.setattr(c, "CDP", FakeCDP)
    out = asyncio.run(c.deliver("ws://x", "hello", idx=5, mode="insert"))
    assert out["ok"] is False and out["verified"] is False, out
    assert "不存在" in out["detail"] and "1 个" in out["detail"], out["detail"]
    assert out["next_step"]


def test_web_target_stored_separately_from_pointer_anchor(tmp_path, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    from agent_community.platform import input_anchor as ia

    ia.set_anchor("示例Harness-W", 0.5, 0.9, title="示例窗口")
    ia.set_web("示例Harness-W", ws="ws://127.0.0.1:9222/devtools/page/1", idx=2)
    assert ia.get_web("示例Harness-W")["idx"] == 2
    assert ia.get("示例Harness-W")["rel_y"] == 0.9, "两条路各记各的，别互相覆盖"
    assert ia.get_web("没记过的") is None


def test_web_probe_endpoint_is_honest(client):
    """接口：没开调试端口时如实说"怎么开"，不是 200 假成功。"""
    r = client.get("/api/docking/web/probe")
    assert r.status_code == 200, r.text
    body = r.json()
    if not body.get("ok"):
        assert "remote-debugging-port" in body.get("why", ""), body
    r2 = client.post("/api/docking/web-send", json={"text": "x"})
    assert r2.status_code == 400 and "ws" in r2.json()["error"], r2.text
    r3 = client.post("/api/docking/web-send", json={"ws": "ws://x", "text": ""})
    assert r3.status_code == 400, r3.text
