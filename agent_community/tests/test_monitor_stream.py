# -*- coding: utf-8 -*-
"""守卫：**监控室连续画面流**（V89：零依赖 PNG 分帧 + 帧率夹紧）。

判据（真跑生成器，不查源码文本）：
1. 桩抓帧下产出多帧，每帧都是**合法 PNG**（`\x89PNG` 起头、含 IHDR/IDAT/IEND），`seq` 递增；
2. **帧率夹紧**：`?fps=1000` 不许真的狂刷（硬上限 10），下限 0.2，缺省读 `AC_MONITOR_FPS`；
3. **黑图/抓不到不发帧**：连续失败到上限就结束（宁可断，不给"黑屏但看起来在播"）；
4. **有上限时长**：到点自己收尾（`max_seconds` 极小值真跑）；
5. 端点契约：没开监控 ⇒ **403**（不是 200 空流）；非 Windows ⇒ **501** 且说"只支持 Windows"
   （**不许**甩锅给"没装 Pillow"）。
"""
from __future__ import annotations

import ctypes
import struct
import sys
import zlib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import monitor as mn  # noqa: E402


def _png_magic_ok(png: bytes) -> bool:
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    tags = set()
    pos = 8
    while pos + 8 <= len(png):
        (ln,) = struct.unpack(">I", png[pos:pos + 4])
        tags.add(png[pos + 4:pos + 8])
        pos += 12 + ln
    return {b"IHDR", b"IDAT", b"IEND"} <= tags


def _stub_frame(w=8, h=8):
    rgb = bytes([17, 34, 51] * (w * h))
    return mn.png_encode(w, h, rgb)


def test_stream_yields_valid_png_frames():
    png = _stub_frame()

    def grabber(_hwnd, _max_w):
        return png, {"ok": True, "w": 8, "h": 8, "method": "stub"}

    frames = list(mn.stream_frames(0, want_fps=10, max_seconds=0.05,
                                   grabber=grabber, sleeper=lambda s: None))
    assert len(frames) >= 3, "帧太少（上限时长内应当产多帧）：%d" % len(frames)
    for png_bytes, meta in frames:
        assert _png_magic_ok(png_bytes), "产出的不是合法 PNG"
        assert meta["ok"] is True and meta["seq"] >= 1
    assert [m["seq"] for _, m in frames] == list(range(1, len(frames) + 1)), "seq 没递增"


def test_fps_is_clamped_at_both_ends(monkeypatch):
    assert mn.fps_of(1000) == 10.0, "上限没夹住：写着 10 却能狂刷"
    assert mn.fps_of(0.001) == 0.2, "下限没夹住"
    assert mn.fps_of("abc") == mn.fps_of(None), "非法值应回落到默认"
    monkeypatch.setenv("AC_MONITOR_FPS", "2")
    assert mn.fps_of(None) == 2.0


def test_stream_paces_frames(monkeypatch):
    """帧率不许"写完就算"：sleep 的间隔必须接近 1/fps（允许 20% 误差）。"""
    png = _stub_frame()
    slept = []

    def grabber(_h, _m):
        return png, {"ok": True, "w": 8, "h": 8, "method": "stub"}

    list(mn.stream_frames(0, want_fps=5, max_seconds=0.4, grabber=grabber,
                          sleeper=lambda s: slept.append(s)))
    assert slept, "一帧都没等，说明节奏没控制"
    # 桩抓帧几乎不耗时 ⇒ 每次 sleep 应当 ≈ 1/5 秒
    assert min(slept) >= 0.2 * 0.8, "间隔太短（在狂刷）：%s" % slept[:5]


def test_black_frames_end_the_stream():
    def grabber(_h, _m):
        return None, {"ok": False, "why": "stub 抓不到（例如 GPU 合成窗口）"}

    out = list(mn.stream_frames(0, want_fps=10, max_seconds=10, grabber=grabber,
                                sleeper=lambda s: None))
    assert out, "连续抓不到时应当给一条**说明性**收尾，而不是什么都不说"
    last_png, last_meta = out[-1]
    assert last_png is None and last_meta["ok"] is False
    assert "抓不到" in (last_meta.get("why") or "")
    assert last_meta["fails"] >= mn.STREAM_MAX_FAILS


def test_stream_stops_at_max_seconds():
    png = _stub_frame()
    t = [0.0]

    def clock():
        t[0] += 0.05
        return t[0]

    def grabber(_h, _m):
        return png, {"ok": True, "w": 8, "h": 8, "method": "stub"}

    frames = list(mn.stream_frames(0, want_fps=10, max_seconds=0.3,
                                   grabber=grabber, clock=clock, sleeper=lambda s: None))
    assert 1 <= len(frames) < 40, "上限时长没生效（帧数 %d）" % len(frames)


# ── 端点契约（TestClient，确定性：不依赖真的有窗口）──────────────────────
def _client():
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    return TestClient(srv.app, client=("127.0.0.1", 54321))


def test_stream_requires_monitoring_on(monkeypatch):
    """没开监控 ⇒ 403（不是"200 但一条流也没有"）。"""
    monkeypatch.setattr(mn, "IS_WINDOWS", True, raising=True)
    monkeypatch.setattr(mn, "watched", lambda: [], raising=True)
    r = _client().get("/api/monitor/示例Harness-A/stream")
    assert r.status_code == 403, r.text
    assert "没开监控" in r.text


def test_stream_on_non_windows_says_so(monkeypatch):
    """非 Windows ⇒ 501 且**说清是平台不支持**（不许甩锅给 Pillow）。"""
    monkeypatch.setattr(mn, "IS_WINDOWS", False, raising=True)
    r = _client().get("/api/monitor/示例Harness-A/stream")
    assert r.status_code == 501, r.text
    body = r.text
    assert "只支持 Windows" in body, body
    assert "Pillow" not in body, "别拿 Pillow 当借口（这一版根本不需要它）：%s" % body


def test_stream_endpoint_really_pushes_multipart_png(monkeypatch):
    """端点级真跑：开监控 + 有窗口 ⇒ **200 + multipart + 段头 + PNG 首帧**。

    这里把"抓窗口"换成桩（真窗口在 CI 上不一定有），但**响应体是真的走 StreamingResponse
    生成出来的** —— 分帧边界、段头、PNG 魔数都在真字节里验，不是查源码文本。
    """
    from agent_community.platform import routers

    png = _stub_frame(16, 12)

    def fake_frames(_hwnd, max_w=320, want_fps=None, **_kw):
        for seq in (1, 2):
            yield png, {"ok": True, "w": 16, "h": 12, "method": "stub", "bytes": len(png), "seq": seq}

    monkeypatch.setattr(mn, "IS_WINDOWS", True, raising=True)
    monkeypatch.setattr(mn, "health", lambda: {"supported": True}, raising=True)
    monkeypatch.setattr(mn, "watched", lambda: [{"harness_id": "示例Harness-A", "on": True}], raising=True)
    monkeypatch.setattr(mn, "frame_png", lambda hwnd, w: (png, {"ok": True, "w": 16, "h": 12, "method": "stub"}),
                        raising=True)
    monkeypatch.setattr(mn, "stream_frames", fake_frames, raising=True)
    monkeypatch.setattr(mn, "note_viewer", lambda hid: None, raising=True)
    monkeypatch.setattr(mn, "touch", lambda hid, **kw: None, raising=True)
    monkeypatch.setattr(mn, "auto_stop_idle", lambda *a, **kw: [], raising=True)
    monkeypatch.setattr(routers.monitor, "_hwnd_for", lambda hid: (4321, "桩窗口"), raising=True)

    r = _client().get("/api/monitor/示例Harness-A/stream?fps=5&max_w=16")
    assert r.status_code == 200, r.text
    ct = (r.headers.get("content-type") or "").lower()
    assert "multipart/x-mixed-replace" in ct, ct
    body = r.content
    assert b"--acframe" in body, "没有分帧边界：%r" % body[:120]
    assert b"Content-Type: image/png" in body
    assert b"X-Monitor-Seq: 1" in body and b"X-Monitor-Seq: 2" in body, "段头缺帧序号"
    assert body.count(b"\x89PNG\r\n\x1a\n") == 2, "两帧应当是两段 PNG"
