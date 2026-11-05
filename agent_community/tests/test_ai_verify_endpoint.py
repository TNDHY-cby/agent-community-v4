# -*- coding: utf-8 -*-
"""守卫：**主动验证连通端点** `POST /api/ai/verify`（V60 §7，前端「验证连通」按钮要用）。

判据（按真行为）：
  ① 没配 provider/model ⇒ **400 且不发任何网络请求**（不白烧 token）；
  ② 非联网模式（ai_mode≠remote）⇒ 400 并说清去哪儿看；
  ③ 配置齐全 ⇒ 真跑一次（mock 的是**网络层** `_probe_once`，指纹/缓存/审计全走真代码）：
     成功 ⇒ `ok=true`、`reachable=true`；失败 ⇒ `ok=false` 且 **reason 原样带回**；
  ④ **验证之后 `/api/status` 的 `ai.reachable` 立刻有值**（这是整条链的契约：
     status 只读缓存、verify 负责产生结论）；
  ⑤ 非本机来源 ⇒ 403；
  ⑥ 只读纪律：验证连通**不许创建配置文件**（`peek_config` 而非 `load_config`）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

CFG = {"ai_provider": "openai", "ai_model": "demo-model",
       "ai_base_url": "http://127.0.0.1:9/v1", "ai_api_key": "sk-example",
       "ai_mode": "remote"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    from fastapi.testclient import TestClient
    from agent_community.platform import ai_verify
    from agent_community.platform.server import app
    ai_verify.clear_cache()
    yield TestClient(app, client=("127.0.0.1", 54321))
    ai_verify.clear_cache()


def _set_cfg(monkeypatch, **kw):
    from agent_community import config as cfgmod
    base = dict(CFG)
    base.update(kw)
    monkeypatch.setattr(cfgmod, "peek_config", lambda *a, **k: dict(base), raising=False)


def test_not_configured_is_400_and_makes_no_network_call(client, monkeypatch):
    from agent_community.platform import ai_verify
    calls = []
    monkeypatch.setattr(ai_verify, "_probe_once",
                        lambda *a, **k: calls.append(1) or {"ok": True}, raising=False)
    _set_cfg(monkeypatch, ai_provider="", ai_model="")
    r = client.post("/api/ai/verify")
    assert r.status_code == 400
    body = r.json()
    assert body.get("how_to_fix"), "必须说清怎么配：%s" % body
    assert not calls, "没配置就去探活＝白烧 token"


def test_non_remote_mode_is_400(client, monkeypatch):
    _set_cfg(monkeypatch, ai_mode="local")
    r = client.post("/api/ai/verify")
    assert r.status_code == 400
    assert "本地" in r.json().get("error", "")


def test_success_probe_reports_ok_and_reason_on_failure(client, monkeypatch):
    from agent_community.platform import ai_verify

    async def ok_probe(norm, model, api_key, timeout):
        return {"ok": True, "status": 200, "elapsed_ms": 12, "reason": ""}

    monkeypatch.setattr(ai_verify, "_probe_once", ok_probe, raising=False)
    _set_cfg(monkeypatch)
    r = client.post("/api/ai/verify")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["reachable"] is True
    assert body["status"] == 200 and "验证成功" in body["detail"]

    ai_verify.clear_cache()

    async def fail_probe(norm, model, api_key, timeout):
        return {"ok": False, "status": 401, "elapsed_ms": 9,
                "reason": "密钥无效或未授权（HTTP 401）"}

    monkeypatch.setattr(ai_verify, "_probe_once", fail_probe, raising=False)
    r2 = client.post("/api/ai/verify")
    b2 = r2.json()
    assert b2["ok"] is False and "密钥无效" in b2["reason"] and b2["how_to_fix"]


def test_verify_then_status_shows_reachable(client, monkeypatch):
    """整条链的契约：verify 产生结论 → status 只读缓存就能亮。"""
    from agent_community.platform import ai_verify

    async def ok_probe(norm, model, api_key, timeout):
        return {"ok": True, "status": 200, "elapsed_ms": 5, "reason": ""}

    monkeypatch.setattr(ai_verify, "_probe_once", ok_probe, raising=False)
    _set_cfg(monkeypatch)

    before = client.get("/api/status").json()["ai"]
    assert before["reachable"] is None, "验之前必须是未知（否则就是假绿）"

    assert client.post("/api/ai/verify").json()["ok"] is True

    after = client.get("/api/status").json()["ai"]
    assert after["reachable"] is True and after["source"] == "cache"
    assert after["checked_at"], "结论时间要能显示：%s" % after


def test_foreign_origin_is_403(client):
    r = client.post("/api/ai/verify", headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403


def test_verify_does_not_create_config_file(client, monkeypatch):
    from agent_community import config as cfgmod
    f = Path(cfgmod.CONFIG_FILE)
    existed = f.exists()
    before = f.stat().st_mtime_ns if existed else None
    monkeypatch.setattr(cfgmod, "peek_config",
                        lambda *a, **k: dict(CFG, ai_provider=""), raising=False)
    for _ in range(2):
        client.post("/api/ai/verify")            # 未配置分支也会读配置
    assert f.exists() == existed, "验证连通创建了配置文件：%s" % f
    if existed:
        assert f.stat().st_mtime_ns == before

def test_verify_cache_hits_when_base_url_has_path(client, monkeypatch):
    """base_url 带路径（`.../v1` 这种最常见写法）时，「验证连通」的结论也必须能在状态页查到。

    起因（2026-11-05 实测）：`verify` 存缓存用归一化后的 base_url（`.../v1` → `...`），
    而 `cached_verdict` 用原始值算指纹 ⇒ 永远 miss，界面一直说"还没验证过"。
    """
    from agent_community.platform import ai_verify

    async def ok_probe(norm, model, api_key, timeout):
        return {"ok": True, "status": 200, "elapsed_ms": 3, "reason": ""}

    monkeypatch.setattr(ai_verify, "_probe_once", ok_probe, raising=False)
    _set_cfg(monkeypatch, ai_base_url="http://127.0.0.1:9/v1")
    assert client.post("/api/ai/verify").json()["ok"] is True
    ai = client.get("/api/status").json()["ai"]
    assert ai["reachable"] is True and ai["source"] == "cache", ai
