# -*- coding: utf-8 -*-
"""守卫：V81 三条。

① 探针过期（staleness 24h 口径）：新鲜 ok⇒`ok`；过期 ok⇒`stale`（不是 ok、也不是 unverified）；
② 第三方代码登记：`tools/third_party_check.py` 必须过（带 SPDX 头的文件都得在 NOTICES 里）；
③ README 里写明"鉴权请前置"这一定位口径（对外话术不许漂移）。
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import harness_capability as hc  # noqa: E402
from agent_community.platform import reply_probe as rp  # noqa: E402


def _rec(meta):
    return {"harness_id": "示例Harness-A", "harness_name": "示例Harness-A",
            "harness_type": "custom", "wakeup_method": "file_poll",
            "ai": {"model_name": "m", "provider": "p"}, "tools": [], "metadata": meta}


def test_fresh_probe_is_ok():
    now = datetime.now().isoformat(timespec="seconds")
    seg = hc.build_provenance(_rec({"reply_probe": {"state": "ok", "source": "auto", "at": now}}))["segments"]["agent_can_reply"]
    assert seg["state"] == "ok", seg


def test_old_probe_is_stale_not_ok(monkeypatch):
    monkeypatch.setenv("AC_PROBE_STALENESS_HOURS", "24")
    old = (datetime.now() - timedelta(hours=30)).isoformat(timespec="seconds")
    seg = hc.build_provenance(_rec({"reply_probe": {"state": "ok", "source": "auto", "at": old}}))["segments"]["agent_can_reply"]
    assert seg["state"] == "stale", seg
    assert "小时" in (seg.get("why") or ""), seg
    assert "验一次" in (seg.get("how_to_fix") or ""), seg


def test_unverified_stays_unverified():
    seg = hc.build_provenance(_rec({}))["segments"]["agent_can_reply"]
    assert seg["state"] == "unverified", seg


def test_third_party_notices_consistent():
    r = subprocess.run([sys.executable, str(_ROOT / "tools" / "third_party_check.py")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(_ROOT), timeout=120)
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")


def test_readme_states_auth_is_fronted():
    readme = (_ROOT / "README.md").read_text(encoding="utf-8", errors="replace")
    assert "鉴权请前置" in readme, "README 必须写明这一定位口径（V81 §1）"
