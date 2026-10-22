# -*- coding: utf-8 -*-
"""V88 监控室：画面墙 / 观察 / 闸门。

原则（与设计稿 §5 一致）：**判据必须是真的**，但 CI 里没有"某个窗口一定在"这种前提 ⇒
平台相关的那部分**如实 skip**，纯逻辑部分（编码/黑图/变化/名单/闸门/端点）全部真跑。
"""
from __future__ import annotations

import json
import struct
import sys
import zlib

import pytest

from agent_community.platform import monitor as mn


@pytest.fixture()
def mdir(tmp_path, monkeypatch):
    monkeypatch.setattr(mn, "MONITOR_DIR", tmp_path / "monitor", raising=True)
    return tmp_path / "monitor"


# ── 编码与像素（纯逻辑，零依赖路径）──────────────────────────────────────
def test_png_encode_roundtrips_pixels():
    """PNG 必须能被标准库解回来（不引 Pillow 就得自己保证格式对）。"""
    w = h = 4
    rgb = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255] * 4)
    png = mn.png_encode(w, h, rgb)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    # 逐块解析：IHDR/IDAT/IEND 都在，且 IDAT 解压后 = 每行滤波字节 + 原始像素
    pos, idat, ihdr = 8, b"", b""
    while pos < len(png):
        (ln,) = struct.unpack(">I", png[pos:pos + 4])
        tag = png[pos + 4:pos + 8]
        data = png[pos + 8:pos + 8 + ln]
        if tag == b"IHDR":
            ihdr = data
        elif tag == b"IDAT":
            idat += data
        pos += 12 + ln
    assert ihdr and idat, "缺 IHDR/IDAT"
    assert struct.unpack(">II", ihdr[:8]) == (w, h)
    raw = zlib.decompress(idat)
    assert len(raw) == h * (1 + w * 3), "解压长度不对"
    for y in range(h):
        assert raw[y * (1 + w * 3)] == 0, "每行滤波类型应为 0"
    assert raw[1:1 + 12] == rgb[:12], "第一行像素没对上"


def test_png_encode_rejects_bad_input():
    with pytest.raises(ValueError):
        mn.png_encode(0, 4, b"")
    with pytest.raises(ValueError):
        mn.png_encode(4, 4, b"\x00" * 10)          # 像素字节数不够


def test_black_frame_is_detected_not_shown():
    """抓黑（Marvis 那类窗口）必须判成失败 —— 判据 1。"""
    assert mn._looks_black(bytes(64 * 64 * 3)) is True
    colorful = bytes([200, 30, 30] * (64 * 64))
    assert mn._looks_black(colorful) is False


def test_change_detection_is_stable_and_sensitive():
    a = bytes([10, 20, 30] * (64 * 64))
    b = bytes([10, 20, 30] * (64 * 64))
    c = bytes([250, 250, 250] * (64 * 64))
    sa, sb, sc = (mn.block_signature(x, 64, 64) for x in (a, b, c))
    assert sa == sb, "同一张画面签名必须一样"
    assert mn.diff_ratio(sa, sb) == 0.0
    assert mn.diff_ratio(sa, sc) >= mn.CHANGE_THRESHOLD, "明显不同的画面必须判定为变了"
    assert mn.diff_ratio("", sc) == 1.0, "没有可比对象时按'变了'算（宁可多看一次）"


# ── 名单：挂在 agent 上，可开可关 ────────────────────────────────────────
def _info(hid="示例Harness-A", name="示例Harness-A", pid=0):
    return {"harness_id": hid, "harness_name": name, "metadata": {"pid": pid}}


def test_watched_is_per_agent_and_persists(mdir):
    assert mn.watched() == []
    r = mn.set_watched("示例Harness-A", True, title="示例窗口")
    assert r["ok"] and r["on"] is True and r["entries"] == 1
    rows = mn.watched()
    assert rows and rows[0]["harness_id"] == "示例Harness-A" and rows[0]["on"] is True
    assert (mdir / "watched.json").exists(), "名单要落盘（重启后恢复但状态是 stopped）"
    r2 = mn.set_watched("示例Harness-A", False)
    assert r2["ok"] and mn.watched()[0]["on"] is False


def test_watched_respects_max_watch(mdir, monkeypatch):
    monkeypatch.setenv("AC_MONITOR_MAX_WATCH", "2")
    for hid in ("h1", "h2"):
        assert mn.set_watched(hid, True)["ok"]
    r = mn.set_watched("h3", True)
    assert r["ok"] is False and "最多 2 路" in r["why"], r
    assert len([e for e in mn.watched() if e["on"]]) == 2


def test_idle_viewers_auto_stop(mdir):
    mn.set_watched("示例Harness-A", True)
    mn.note_viewer("示例Harness-A")
    assert mn.watched()[0]["state"] == "running"
    assert mn.auto_stop_idle(idle_seconds=60) == [], "刚看过不该停"
    mn.touch("示例Harness-A", last_view_at=0)           # 假装很久没人看
    stopped = mn.auto_stop_idle(idle_seconds=60)
    assert stopped == ["示例Harness-A"], stopped
    row = mn.watched()[0]
    assert row["state"] == "stopped" and "没人看" in row["why"], row


# ── AI 观察闸门：默认关 + 至少 30 分钟一次 ────────────────────────────────
def test_ai_observe_is_off_by_default(mdir, monkeypatch):
    monkeypatch.delenv("AC_MONITOR_AI_OBSERVE", raising=False)
    ok, why = mn.observe_ai_gate("示例Harness-A")
    assert ok is False and "默认关闭" in why, why


def test_ai_observe_interval_is_half_an_hour_minimum(mdir, monkeypatch):
    monkeypatch.setenv("AC_MONITOR_AI_OBSERVE", "1")
    monkeypatch.delenv("AC_MONITOR_AI_INTERVAL_MIN", raising=False)
    assert mn.ai_interval_seconds() == 30 * 60, "老板原话：好歹 30 分钟一次"
    ok, why = mn.observe_ai_gate("示例Harness-A")
    assert ok is True, why                            # 首次允许
    mn.mark_ai_run("示例Harness-A")
    ok2, why2 = mn.observe_ai_gate("示例Harness-A")
    assert ok2 is False and "30 分钟" in why2, why2   # 立刻再来一次必须被挡
    monkeypatch.setenv("AC_MONITOR_AI_INTERVAL_MIN", "120")
    assert mn.ai_interval_seconds() == 120 * 60


# ── 端点（真请求）────────────────────────────────────────────────────────
@pytest.fixture()
def client(mdir):
    from fastapi.testclient import TestClient

    from agent_community.platform.server import app

    return TestClient(app, client=("127.0.0.1", 54321))


def test_health_tells_the_truth(client):
    d = client.get("/api/monitor/health").json()
    assert "supported" in d and "encoding" in d
    assert d["ai_observe"]["enabled"] in (True, False)
    assert d["ai_observe"]["interval_min"] >= 30, "AI 观察间隔至少 30 分钟"


def test_watched_endpoint_requires_agent_not_window(client):
    assert client.post("/api/monitor/watched", json={"hwnd": 123}).status_code == 400, "必须按 agent 传"
    assert client.post("/api/monitor/watched", json={"harness_id": "nope", "on": True}).status_code == 404


def test_frame_is_403_when_agent_not_watched(client):
    r = client.get("/api/monitor/不存在的/../frame")
    assert r.status_code in (403, 404)


def test_stream_is_501_without_pillow(client):
    if mn.jpeg_available():                              # 装了 Pillow 就跳过这条
        pytest.skip("本机装了 Pillow")
    r = client.get("/api/monitor/x/stream")
    assert r.status_code == 501, r.text
    assert "Pillow" in r.text, "要告诉用户装什么：%s" % r.text


def test_monitor_is_never_exposed_to_external_readonly():
    from agent_community.platform import server as srv

    assert any("/api/monitor".startswith(x) for x in srv.EXTERNAL_READONLY_DENY), \
        "监控画面必须进只读档 DENY（画面不出本机）"
    assert not any("/api/monitor".startswith(x) for x in srv.EXTERNAL_READONLY_PREFIX)


# ── 平台相关：只有 Windows 且有窗口时才真抓（否则如实 skip）──────────────
@pytest.mark.skipif(not mn.IS_WINDOWS, reason="窗口抓取只支持 Windows（非 Windows 如实跳过）")
def test_real_window_capture_or_clear_reason():
    wins = [w for w in mn.sources() if not w.get("minimized")]
    if not wins:
        pytest.skip("这台机器没有可抓的窗口（无桌面会话），如实跳过")
    target = wins[0]
    png, meta = mn.frame_png(target["hwnd"], max_w=160)
    if png is None:
        assert meta.get("why"), "抓不到时必须给原因，不许静默返回空帧"
        assert not meta.get("ok")
    else:
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert meta["w"] <= 160 and meta["h"] > 0
        assert meta.get("method") in ("printwindow", "wgc"), "每帧要能追溯用了哪种方法"


@pytest.mark.skipif(not mn.IS_WINDOWS, reason="只在 Windows 上有窗口概念")
def test_resolve_hwnd_never_guesses_wrong_window(mdir):
    hwnd, why = mn.resolve_hwnd(["一个绝对不存在的标题-zzz"], 0)
    assert hwnd == 0 and why, "找不到就要返回 0 并说清，绝不乱抓别的窗口"


# -*- coding: utf-8 -*-
"""补 V88 的判据：监控室自己就能开/关（候选列表 + 资格口径 + 不现场抓图）。"""

import pytest

from agent_community.platform import monitor as mn


def test_eligibility_only_counts_tested_bridge():
    """「完整注册架桥后」的唯一口径：`bridge_status == "tested"`（protocol.py 的词表）。"""
    assert mn.eligible("tested")[0] is True
    ok, why = mn.eligible("reported")
    assert ok is False and "还没跑通测试" in why, why
    ok2, why2 = mn.eligible("")
    assert ok2 is False and "还没架桥" in why2, why2
    assert mn.eligible("connected")[0] is False, "没见过的值不许当成通（宁可不开）"


def test_can_capture_is_honest_and_never_guesses(mdir_fixture):
    """没试过就说"没试过"，不猜；窗口最小化/不存在直接给 False + 原因。"""
    able, why = mn.can_capture("示例Harness-A", 0)
    assert able is False and "还没认出" in why, why

    # 用一个一定不存在的句柄：exists=False
    able2, why2 = mn.can_capture("示例Harness-A", 0x7FFFFFF)
    if mn.IS_WINDOWS:
        assert able2 is False and ("窗口已关闭" in why2 or "还没认出" in why2), why2


def test_window_state_reports_missing_window(mdir_fixture):
    ws = mn.window_state(0)
    assert ws["exists"] is False and ws["minimized"] is False


def test_agents_endpoint_lists_candidates_with_reasons(mdir_fixture, monkeypatch):
    """监控室页面自己就能拿到"能开哪几个、为什么不能开"——不靠对接中心。"""
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    class _Info:
        metadata = {}

        def __init__(self, hid, name, bridge):
            self._d = {"harness_id": hid, "harness_name": name, "bridge_status": bridge,
                       "status": "offline"}

        def model_dump(self):
            return dict(self._d)

    class _Sess:
        def __init__(self, info):
            self.info = info

    fake = {
        "h-bridged": _Sess(_Info("h-bridged", "已架桥的", "tested")),
        "h-reported": _Sess(_Info("h-reported", "只登记路径的", "reported")),
        "h-raw": _Sess(_Info("h-raw", "没架桥的", "")),
    }
    from agent_community.platform.harness_adapter import harness_manager

    monkeypatch.setattr(harness_manager, "sessions", fake, raising=True)
    mn.set_watched("h-bridged", True)                      # 已经开着的那个

    c = TestClient(srv.app, client=("127.0.0.1", 54321))
    d = c.get("/api/monitor/agents").json()
    rows = {r["harness_id"]: r for r in d["agents"]}
    assert set(rows) == set(fake), "三个 agent 都要出现（不能只列已开的）"
    assert rows["h-bridged"]["eligible"] is True and rows["h-bridged"]["monitor"]["on"] is True
    assert rows["h-reported"]["eligible"] is False and "还没跑通测试" in rows["h-reported"]["bridge"]["why"]
    assert rows["h-raw"]["eligible"] is False
    assert d["agents"][0]["harness_id"] == "h-bridged", "能开的要排在最前面"
    assert d["watched_on"] == 1
    assert "POST /api/monitor/watched" in d["note"], "要把开关就地在监控室这件事说出来"


@pytest.fixture()
def mdir_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(mn, "MONITOR_DIR", tmp_path / "monitor", raising=True)
    return tmp_path / "monitor"
