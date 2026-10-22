# -*- coding: utf-8 -*-
"""守卫：**Agent Manager**（V80 §A.4 六格）。

① 清单可读且字段齐；② 开关没开时 install → 403 且说怎么开；③ 没 confirm → 400；
④ dry_run 只回命令不执行；⑤ 检出/检出失败都有人话；⑥ 自定义清单能加载，坏 JSON 报错不静默。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from agent_community.platform import agent_catalog as cat  # noqa: E402
from agent_community.platform import server as srv  # noqa: E402

C = TestClient(srv.app, client=("127.0.0.1", 54321))


def test_catalog_has_required_fields():
    d = C.get("/api/agents/catalog").json()
    assert d["count"] >= 2 and d["agents"], d
    for row in d["agents"]:
        for k in ("id", "name", "kind", "detect", "install_supported"):
            assert k in row, (k, row)


def test_install_blocked_when_switch_off(monkeypatch):
    monkeypatch.delenv("AC_AGENT_INSTALL_ALLOW", raising=False)
    r = C.post("/api/agents/pip-cli/install", json={"confirm": True, "package": "requests"})
    assert r.status_code == 403 and "AC_AGENT_INSTALL_ALLOW" in r.text, r.text


def test_install_requires_confirm(monkeypatch):
    monkeypatch.setenv("AC_AGENT_INSTALL_ALLOW", "1")
    r = C.post("/api/agents/pip-cli/install", json={"package": "requests"})
    assert r.status_code == 400 and "confirm" in r.text, r.text


def test_dry_run_does_not_execute(monkeypatch):
    monkeypatch.setenv("AC_AGENT_INSTALL_ALLOW", "1")
    r = C.post("/api/agents/pip-cli/install",
               json={"confirm": True, "dry_run": True, "package": "示例包"}).json()
    assert r["executed"] is False and r["cmd"][:2] == ["pip", "install"], r


def test_detect_both_paths():
    ok = C.post("/api/agents/pip-cli/detect").json()
    assert "installed" in ok and "why" in ok, ok
    missing = C.post("/api/agents/no-such-agent/detect")
    assert missing.status_code == 404 and "catalog" in missing.text, missing.text


def test_custom_catalog_and_bad_json(monkeypatch, tmp_path):
    good = tmp_path / "agents.json"
    good.write_text(json.dumps([{"id": "my-cli", "name": "我的 CLI", "detect": "python",
                                 "version_args": ["--version"], "install_cmd": ["pip", "install", "%PACKAGE%"]}]),
                    encoding="utf-8")
    monkeypatch.setenv("AC_AGENT_CATALOG_EXTRA", str(good))
    assert cat.by_id("my-cli") is not None

    bad = tmp_path / "bad.json"
    bad.write_text("{不是 json", encoding="utf-8")
    monkeypatch.setenv("AC_AGENT_CATALOG_EXTRA", str(bad))
    items, err = cat.load()
    assert items and err and "读不出来" in err, (items[:1], err)
    d = C.get("/api/agents/catalog").json()
    assert d.get("catalog_error"), "坏清单要在响应里说出来：%r" % d
