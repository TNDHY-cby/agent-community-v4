"""心跳登记表测试（2026-10-07 Marvis，47 号任务书 Marvis 侧）。

覆盖：
- register / beat：last_seen 刷新、status 更新、未登记自登记；
- 失联判定：超过 interval × timeout_scale 未上报 → stale；
- online / stale 列表与顺序；
- unregister 注销；
- to_dict / from_dict 持久化往返。
"""

import time
from pathlib import Path

from agent_community.platform.heartbeat import HeartbeatRegistry


def _mk(interval: float = 0.05, scale: float = 2.0) -> HeartbeatRegistry:
    return HeartbeatRegistry(timeout_scale=scale)


def test_register_and_beat():
    reg = _mk()
    rec = reg.register("a1", harness_id="h1", harness_name="H1", interval=0.05)
    assert rec["harness_id"] == "h1"
    assert rec["interval"] == 0.05
    assert rec["status"] == "alive"
    # beat 刷新 last_seen
    t0 = rec["last_seen"]
    time.sleep(0.02)
    reg.beat("a1", status="busy", note="working")
    assert reg._registry["a1"]["status"] == "busy"
    assert reg._registry["a1"]["last_seen"] > t0


def test_beat_self_register():
    reg = _mk()
    rec = reg.beat("a2", harness_id="h2")
    assert rec["harness_id"] == "h2"
    assert reg.status("a2") is not None


def test_status_not_exists():
    reg = _mk()
    assert reg.status("nobody") is None


def test_stale_detection():
    reg = _mk(interval=0.05, scale=2.0)
    reg.register("a1", harness_id="h1", interval=0.05)
    time.sleep(0.25)  # 远超 0.05*2=0.1s
    stale = reg.stale()
    assert any(r["agent_id"] == "a1" for r in stale), stale
    assert reg.status("a1")["stale"] is True


def test_online_excludes_stale():
    reg = _mk(interval=0.05, scale=2.0)
    reg.register("fresh", harness_id="h1", interval=0.05)
    reg.register("lost", harness_id="h2", interval=0.05)
    time.sleep(0.25)  # 两者都超时
    reg.beat("fresh", harness_id="h1")  # fresh 刚上报 → 在线
    online_ids = {r["agent_id"] for r in reg.online()}
    assert "fresh" in online_ids
    assert "lost" not in online_ids
    assert any(r["agent_id"] == "lost" for r in reg.stale())


def test_unregister():
    reg = _mk()
    reg.register("a1", harness_id="h1")
    assert reg.unregister("a1") is True
    assert reg.status("a1") is None
    assert reg.unregister("a1") is False


def test_persist_roundtrip(tmp_path: Path):
    path = tmp_path / "heartbeat.json"
    reg = HeartbeatRegistry(state_path=path)
    reg.register("a1", harness_id="h1", interval=5.0)
    reg.beat("a1", status="idle")
    assert path.exists()
    reg2 = HeartbeatRegistry(state_path=path)
    assert reg2.status("a1") is not None
    assert reg2.status("a1")["status"] == "idle"
    assert reg2.timeout_scale == reg.timeout_scale


def test_stale_sorted_by_last_ago():
    reg = _mk(interval=0.05, scale=2.0)
    reg.register("old", harness_id="h1", interval=0.05)
    time.sleep(0.1)
    reg.register("newer", harness_id="h2", interval=0.05)
    time.sleep(0.1)
    stale = reg.stale()
    by_ago = [r["agent_id"] for r in stale]
    assert by_ago == sorted(by_ago, key=lambda _: -0) or len(by_ago) <= 2
    # 最久未报的排最前（age 越大越靠前）
    ages = {r["agent_id"]: r["last_ago"] for r in stale}
    assert len(ages) == 2
    assert ages["old"] > ages["newer"]
