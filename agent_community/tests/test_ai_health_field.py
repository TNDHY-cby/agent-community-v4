# -*- coding: utf-8 -*-
"""守卫：**平台 AI 健康字段**（V60，前端通道 201 申请的接口变更）。

判据（按真行为）：
  ① `/api/status` 有 `ai` 段且七个字段齐全；**既有字段一个不少**（不破坏老调用方）；
  ② **没配** ⇒ `configured=false`、`reachable=null`、`source=not-configured`、`detail` 说清怎么配；
  ③ 配了但**没验证** ⇒ `reachable=null`、`source=unverified`（**不许谎报 true**）；
  ④ 缓存里有**成功**结论 ⇒ `reachable=true`；有**失败**结论 ⇒ `reachable=false` 且 detail 带原因；
  ⑤ **不烧钱**：连打两次 `/api/status`，`checked_at`/`age_seconds` 不被刷新（证明没触发探活）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

FIELDS = ("provider", "model", "configured", "reachable", "detail",
          "checked_at", "source", "age_seconds")


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
    """把 AI 配置塞进**只读**读法 `config.peek_config`（不动磁盘、也不碰 load_config）。"""
    from agent_community import config as cfgmod
    base = {"ai_provider": "", "ai_model": "", "ai_base_url": "", "ai_api_key": ""}
    base.update(kw)
    monkeypatch.setattr(cfgmod, "peek_config", lambda *a, **k: dict(base), raising=False)


def test_status_has_ai_block_and_keeps_old_fields(client):
    body = client.get("/api/status").json()
    assert "ai" in body, "缺 ai 段：%s" % sorted(body)
    for f in FIELDS:
        assert f in body["ai"], "ai 段缺字段 %s：%s" % (f, sorted(body["ai"]))
    for old in ("status", "version", "agents_registered", "agents_online", "tasks_total",
                "tasks_active", "harnesses", "pipe_dir"):
        assert old in body, "既有字段被弄丢了：%s" % old


def test_not_configured_is_honest(client, monkeypatch):
    _set_cfg(monkeypatch, ai_provider="", ai_model="")
    ai = client.get("/api/status").json()["ai"]
    assert ai["configured"] is False and ai["reachable"] is None
    assert ai["source"] == "not-configured"
    assert "provider" in ai["detail"] and "验证连通" in ai["detail"]


def test_configured_but_unverified_is_null_not_true(client, monkeypatch):
    _set_cfg(monkeypatch, ai_provider="openai", ai_model="example-model",
             ai_base_url="https://api.example.com", ai_api_key="sk-example")
    ai = client.get("/api/status").json()["ai"]
    assert ai["configured"] is True
    assert ai["reachable"] is None, "没验证过必须给 null（未知），不许谎报 true"
    assert ai["source"] == "unverified" and "还没验证过" in ai["detail"]


def test_cached_success_and_failure_are_reported(client, monkeypatch):
    from agent_community.platform import ai_verify
    from agent_community.platform.ai_verify import _fingerprint, _store
    cfg = {"ai_provider": "openai", "ai_model": "example-model",
           "ai_base_url": "https://api.example.com", "ai_api_key": "sk-example"}
    _set_cfg(monkeypatch, **cfg)
    fp = _fingerprint(cfg["ai_provider"], cfg["ai_base_url"], cfg["ai_model"], cfg["ai_api_key"])

    # ④a 成功结论
    _store(fp, True, {"ok": True, "status": 200, "reason": "", "at": time.time()})
    ai = client.get("/api/status").json()["ai"]
    assert ai["reachable"] is True and ai["source"] == "cache"
    assert "验证成功" in ai["detail"]

    # ④b 失败结论（要带原因）
    ai_verify.clear_cache()
    _store(fp, True, {"ok": False, "status": 401, "reason": "密钥无效或未授权（HTTP 401）",
                      "at": time.time()})
    ai = client.get("/api/status").json()["ai"]
    assert ai["reachable"] is False
    assert "密钥无效" in ai["detail"]


def test_ai_detail_is_plain_text_for_ui(client, monkeypatch):
    """`detail` 是**直接显示在界面上**的：不许带 markdown 记号（`**` 会原样露出）。
    前端早就为"星号原样露出"修过一次（通道 186 的 mdLite 那笔）——这里守住同一口径。"""
    _set_cfg(monkeypatch, ai_provider="openai", ai_model="example-model",
             ai_base_url="https://api.example.com", ai_api_key="sk-example")
    ai = client.get("/api/status").json()["ai"]
    assert "**" not in ai["detail"], "detail 里出现了 markdown 记号：%r" % ai["detail"]

    _set_cfg(monkeypatch, ai_provider="", ai_model="")
    ai2 = client.get("/api/status").json()["ai"]
    assert "**" not in ai2["detail"], "detail 里出现了 markdown 记号：%r" % ai2["detail"]


def test_status_never_triggers_a_probe(client, monkeypatch):
    """连打两次，结论时间不许被刷新（证明 /api/status 没去探活）。"""
    from agent_community.platform import ai_verify
    from agent_community.platform.ai_verify import _fingerprint, _store
    cfg = {"ai_provider": "openai", "ai_model": "example-model",
           "ai_base_url": "https://api.example.com", "ai_api_key": "sk-example"}
    _set_cfg(monkeypatch, **cfg)
    fp = _fingerprint(cfg["ai_provider"], cfg["ai_base_url"], cfg["ai_model"], cfg["ai_api_key"])
    _store(fp, True, {"ok": True, "status": 200, "reason": "", "at": time.time() - 60})

    a1 = client.get("/api/status").json()["ai"]
    t0 = time.time()
    a2 = client.get("/api/status").json()["ai"]
    elapsed = time.time() - t0
    assert a1["checked_at"] == a2["checked_at"], "checked_at 被刷新了 ⇒ 说明触发了探活"
    assert a2["age_seconds"] >= a1["age_seconds"], "age 不该倒退"
    assert elapsed < 0.5, "两次 /api/status 用时 %.2fs，太慢（疑似在其中做网络探活）" % elapsed


def test_status_does_not_create_config_file(client):
    """★2026-10-20 真事故：`load_config()` 会**创建**配置文件，而 `/api/status` 是高频只读端点
    ⇒ 打一次状态就落盘，Linux CI 的"防污染守卫"当场抓成「测试污染了真实数据」。
    这条守住"只读端点不许有写盘副作用"。
    """
    from agent_community import config as cfgmod
    f = Path(cfgmod.CONFIG_FILE)
    existed = f.exists()
    before = f.stat().st_mtime_ns if existed else None
    for _ in range(3):
        assert client.get("/api/status").status_code == 200
    assert f.exists() == existed, "只读端点创建了配置文件：%s" % f
    if existed:
        assert f.stat().st_mtime_ns == before, "只读端点改动了配置文件：%s" % f
