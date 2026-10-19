# -*- coding: utf-8 -*-
"""守卫：**只读日志尾端点** `GET /api/logs/tail`（V59）的安全面。

判据（按真行为）：
  ① 白名单外的名字（含任何路径字符）⇒ 400，且**不返回任何日志内容**；
  ② 凭据样式被遮成 `***`，响应正文里**搜不到原值**；
  ③ 日志不存在 ⇒ 404 且**不新建文件**（只读端点的底线）；
  ④ 非本机来源 ⇒ 403；
  ⑤ 行数上限 200（请求 999 也只给 200，并标 truncated）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

FAKE = "sk-" + ("guard" * 5)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "logs").mkdir(parents=True)
    monkeypatch.setenv("AC_DATA_DIR", str(data))
    from fastapi.testclient import TestClient
    from agent_community.platform.server import app
    yield TestClient(app, client=("127.0.0.1", 54321)), data


def _write_log(data: Path, n: int = 260, extra: str = "") -> Path:
    p = data / "logs" / "server_stdout.log"
    rows = ["row-%03d" % i for i in range(n)]
    rows.append("带凭据的一行 X-Agent-Token: " + FAKE)
    if extra:
        rows.append(extra)
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


def test_rejects_non_whitelisted_names(client):
    c, data = client
    _write_log(data, 5)
    for bad in ("../secret", "C:\\x.log", "server_stdout.log", "etc/passwd", "watchdog"):
        r = c.get("/api/logs/tail", params={"name": bad})
        assert r.status_code in (400, 404), "%r 不该被接受：%s" % (bad, r.text[:120])
        assert "row-000" not in r.text, "%r 泄漏了日志内容" % bad
    # 说明字段必须在（失败要说话）
    body = c.get("/api/logs/tail", params={"name": "../secret"}).json()
    assert "只支持这四个名字" in body["error"] and body["allowed"]


def test_redacts_credentials(client):
    c, data = client
    _write_log(data, 5)
    r = c.get("/api/logs/tail", params={"name": "server", "lines": 20})
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    text = json.dumps(body, ensure_ascii=False)
    assert FAKE not in text, "凭据原值不许出现在响应里"
    assert body["redacted"] >= 1
    assert "***" in text


def test_missing_log_is_404_and_creates_nothing(client):
    c, data = client
    r = c.get("/api/logs/tail", params={"name": "watchdog"})
    assert r.status_code == 404, r.text[:200]
    assert not (data / "logs" / "watchdog.log").exists(), "只读端点不许新建文件"


def test_rejects_foreign_origin(client):
    c, data = client
    _write_log(data, 5)
    r = c.get("/api/logs/tail", params={"name": "server"},
              headers={"Origin": "https://evil.example"})
    assert r.status_code == 403, r.text[:200]


def test_caps_lines_at_200(client):
    c, data = client
    _write_log(data, 900)
    r = c.get("/api/logs/tail", params={"name": "server", "lines": 999})
    body = r.json()
    assert r.status_code == 200 and len(body["lines"]) <= 200
    assert body["truncated"] is True and body["max_lines"] == 200
