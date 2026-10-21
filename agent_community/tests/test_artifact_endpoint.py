# -*- coding: utf-8 -*-
"""守卫：**产物统一出口** `GET /api/artifact`（V75 P1-4）。

判据（真请求，`AC_DATA_DIR` 指到临时目录）：
① 根内文件 → 200 且内容一字不差；
② `../` 穿越 → 403（**不许**读到根外文件）；
③ 绝对路径越界（系统临时目录里的文件）→ 403；
④ 不存在 → 404；
⑤ 目录 → 400 且说清用哪个端点列目录；
⑥ 非回环由平台全局鉴权拦（403），且产物口在外部只读白名单里（远端只读可看）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


def _client(host="127.0.0.1"):
    from fastapi.testclient import TestClient

    from agent_community.platform.server import app

    return TestClient(app, client=(host, 54321))


def _data_dir() -> Path:
    from agent_community.platform.env_spec import data_dir

    return Path(data_dir())


def test_inside_root_served(monkeypatch, tmp_path):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AC_ARTIFACT_TOKEN", raising=False)
    p = tmp_path / "workshops" / "w1" / "结果.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("产物内容", encoding="utf-8")

    r = _client().get("/api/artifact", params={"path": "workshops/w1/结果.md"})
    assert r.status_code == 200, r.text
    assert r.content.decode("utf-8") == "产物内容"


def test_traversal_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "secret.txt").write_text("根外秘密", encoding="utf-8")
    r = _client().get("/api/artifact", params={"path": "../secret.txt"})
    assert r.status_code == 403, r.text
    assert "允许的目录" in r.text


def test_absolute_outside_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    r = _client().get("/api/artifact", params={"path": str(outside)})
    assert r.status_code == 403, r.text


def test_missing_404_and_dir_400(monkeypatch, tmp_path):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    c = _client()
    assert c.get("/api/artifact", params={"path": "nope.txt"}).status_code == 404
    (tmp_path / "sub").mkdir(exist_ok=True)
    r = c.get("/api/artifact", params={"path": "sub"})
    assert r.status_code == 400 and "目录" in r.text, r.text


def test_remote_goes_through_platform_auth(monkeypatch, tmp_path):
    """非回环**不另设令牌**：由平台全局鉴权拦（一处真相），并断言产物口在只读白名单里。"""
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    (tmp_path / "a.txt").write_text("ok", encoding="utf-8")
    r = _client("10.0.0.9").get("/api/artifact", params={"path": "a.txt"})
    assert r.status_code == 403, r.text

    from agent_community.platform import server as srv

    allowed = (
        any("/api/artifact".startswith(x) for x in srv.EXTERNAL_READONLY_PREFIX)
        or "/api/artifact" in srv.EXTERNAL_READONLY_EXACT
        or "/api/artifact" in srv.EXTERNAL_READONLY_PAGES
    )
    assert allowed, "产物口要进外部只读档白名单，远端只读才能取产物"
