# -*- coding: utf-8 -*-
"""守卫：`/api/ai/models` **不许把底层异常糊到用户脸上**（EXT-20261025-01）。

外部审查（P2）打回的事实：
- 出厂默认 `ai_base_url=https://api.example.com`（保留示例域名，DNS 永远解析不到）；
- 设置页打开就拉清单 ⇒ 以前回 `ConnectError: [Errno 11001] getaddrinfo failed`，
  而界面还固定追加"**不影响保存与调用**"（对"调用"是假的）。

| # | 判据 |
|---|---|
| ① | 占位符地址 ⇒ `placeholder=true`，`error` 里**不出现** `ConnectError` / `Errno` / `getaddrinfo` |
| ② | 占位符命中时**不出网**（`tried_url` 为空）——否则等于又去撞一次 DNS |
| ③ | 解析不到的域名 ⇒ 说人话（"这个地址解析不到…"），并带 `tried_url` 说明试了哪个地址 |
| ④ | 都给出可执行的 `hint`（该填什么、或改用 `ai_mode=off`） |
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

_JARGON = ("ConnectError", "Errno", "getaddrinfo")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    from fastapi.testclient import TestClient

    from agent_community.platform.server import app

    return TestClient(app, client=("127.0.0.1", 54321))


def test_placeholder_base_url_short_circuits_without_jargon(client):
    r = client.get("/api/ai/models", params={"base_url": "https://api.example.com"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("placeholder") is True, d
    blob = str(d)
    for word in _JARGON:
        assert word not in blob, "报错里还带着底层术语 %s：%s" % (word, blob[:200])
    assert d.get("tried_url") in (None, ""), "占位符不该真去发请求（tried_url 应为空）：%s" % d
    assert d.get("hint"), "必须告诉用户该填什么"


def test_unresolvable_host_says_it_in_plain_words(client):
    r = client.get("/api/ai/models", params={"base_url": "https://no-such-host-xyz-20261025.invalid"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("ok") is False and d.get("fallback") is True, d
    assert "解析不到" in d.get("error", ""), d.get("error")
    assert "11001" not in d.get("error", "") and "Errno" not in d.get("error", ""), d.get("error")
    assert d.get("tried_url"), "要说清实际请求了哪个地址"
    assert d.get("hint")
