# -*- coding: utf-8 -*-
"""V-16 §四 连通性自检单测（`platform/ai_verify.py`）。

锁定的是**负责人拍板的行为边界**，不是实现细节：
1. **默认不产生计费流量** —— 两个开关默认 False，且 manual/off/local 不发云端探针；
2. **TTL 缓存** —— 同指纹 10 分钟内不重复出网；换 key/base/model 视为不同指纹；
3. **可读原因** —— 401/403/404/429/5xx/超时/连不上各有说法，不吐原始异常；
4. **绝不抛异常** —— 任何失败都转成 ok=False + reason（调用点在保存链路，
   抛异常会毁掉主流程）；
5. **出网走 V-14 闸门** —— 被拦/待批时如实回报，不硬发。

⚠️ 全部用 httpx 打桩，**不发真实请求**（测试不该产生计费流量）。
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from agent_community.platform import ai_verify  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_cache():
    ai_verify.clear_cache()
    yield
    ai_verify.clear_cache()


class _FakeResp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    """替代 httpx.AsyncClient：记录被调用次数与请求体，返回预设响应。"""

    calls: list[dict] = []
    response: _FakeResp = _FakeResp(200, '{"choices":[]}')
    raise_exc: Exception | None = None

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        type(self).calls.append({"url": url, "json": json, "headers": headers or {}})
        if type(self).raise_exc is not None:
            raise type(self).raise_exc
        return type(self).response


@pytest.fixture
def fake_httpx(monkeypatch):
    import httpx
    _FakeClient.calls = []
    _FakeClient.response = _FakeResp(200, '{"choices":[]}')
    _FakeClient.raise_exc = None
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    return _FakeClient


def _run(**kw):
    param = {"provider_type": "openai", "base_url": "https://api.example.com",
             "model": "example-model-flash", "api_key": "sk-test-key"}
    param.update(kw)
    param.setdefault("cfg", {"ai_verify_cache_ttl": 600})
    return asyncio.run(ai_verify.verify_provider_async(**param))


# ── 1. 默认不产生计费流量 ────────────────────────────────────────
class TestNoBillByDefault:
    def test_config_defaults_are_off(self):
        """两个自检开关必须默认 False（负责人要求：避免默认产生计费流量）。"""
        from agent_community.config import DEFAULT_CONFIG
        assert DEFAULT_CONFIG["ai_verify_on_save"] is False
        assert DEFAULT_CONFIG["ai_verify_on_startup"] is False
        assert int(DEFAULT_CONFIG["ai_verify_cache_ttl"]) == 600
        # 探针超时默认 45s —— 实测真实 chat 调用要 37.8s，8s 会误判"连不上"
        assert int(DEFAULT_CONFIG["ai_verify_timeout"]) == 45

    def test_manual_mode_sends_nothing(self, fake_httpx):
        r = _run(provider_type="manual")
        assert r["ok"] is False and "不需要" in r["reason"]
        assert fake_httpx.calls == [], "manual 模式不得发任何请求"

    def test_off_and_local_modes_send_nothing(self, fake_httpx):
        for pt in ("off", "local"):
            assert _run(provider_type=pt)["ok"] is False
        assert fake_httpx.calls == []

    def test_missing_base_url_or_model_sends_nothing(self, fake_httpx):
        assert _run(base_url="")["ok"] is False
        assert _run(model="")["ok"] is False
        assert fake_httpx.calls == []


# ── 2. 探针本身：形态、归一化、max_tokens=1 ─────────────────────
class TestProbeShape:
    def test_success_and_probe_is_minimal(self, fake_httpx):
        r = _run()
        assert r["ok"] is True and r["status"] == 200
        assert len(fake_httpx.calls) == 1
        call = fake_httpx.calls[0]
        assert call["url"] == "https://api.example.com/v1/chat/completions"
        assert call["json"]["max_tokens"] == 1, "探针必须是 max_tokens=1（费用可忽略）"
        assert call["json"]["model"] == "example-model-flash"
        assert call["headers"]["Authorization"] == "Bearer sk-test-key"

    def test_base_url_with_v1_is_normalized(self, fake_httpx):
        r = _run(base_url="https://api.example.com/v1")
        assert r["ok"] is True
        url = fake_httpx.calls[0]["url"]
        assert "/v1/v1/" not in url, "归一化失效会拼出 /v1/v1/"
        assert url == "https://api.example.com/v1/chat/completions"

    def test_empty_key_still_probes_without_auth_header(self, fake_httpx):
        r = _run(api_key="")
        assert r["ok"] is True
        assert "Authorization" not in fake_httpx.calls[0]["headers"]


# ── 3. 可读原因 ─────────────────────────────────────────────────
class TestReadableReason:
    @pytest.mark.parametrize("status,expect", [
        (401, "密钥"), (403, "无权"), (404, "路径或模型"), (429, "限流"),
        (500, "服务端"), (502, "服务端"),
    ])
    def test_http_status_maps_to_readable_reason(self, fake_httpx, status, expect):
        fake_httpx.response = _FakeResp(status, "boom")
        r = _run()
        assert r["ok"] is False and r["status"] == status
        assert expect in r["reason"], f"HTTP {status} 的原因里应含 {expect!r}"

    def test_unsupported_model_body_is_explained(self, fake_httpx):
        fake_httpx.response = _FakeResp(400, '{"error":"Unsupported model xxx"}')
        r = _run()
        assert r["ok"] is False
        assert "模型名" in r["reason"]

    def test_timeout_is_explained(self, fake_httpx):
        fake_httpx.raise_exc = TimeoutError("timed out")
        r = _run()
        assert r["ok"] is False and "超时" in r["reason"]

    def test_connection_error_is_explained(self, fake_httpx):
        fake_httpx.raise_exc = ConnectionError("connect failed")
        r = _run()
        assert r["ok"] is False and "连不上" in r["reason"]


# ── 4. TTL 缓存与指纹 ───────────────────────────────────────────
class TestCache:
    def test_second_call_hits_cache_without_network(self, fake_httpx):
        first = _run()
        second = _run()
        assert first["ok"] is True and second["ok"] is True
        assert len(fake_httpx.calls) == 1, "TTL 内第二次不得再出网"
        assert second["cached"] is True and first["cached"] is False

    def test_use_cache_false_always_probes(self, fake_httpx):
        _run(use_cache=False)
        _run(use_cache=False)
        assert len(fake_httpx.calls) == 2

    def test_different_key_or_model_is_a_different_fingerprint(self, fake_httpx):
        _run()
        _run(api_key="sk-other-key")
        _run(model="other-model")
        assert len(fake_httpx.calls) == 3, "换 key/model 必须视为不同指纹（否则旧结论冒充新配置）"

    def test_ttl_zero_never_hits_cache(self, fake_httpx):
        _run(cfg={"ai_verify_cache_ttl": 0})
        _run(cfg={"ai_verify_cache_ttl": 0})
        assert len(fake_httpx.calls) == 2

    def test_failure_result_is_also_cached(self, fake_httpx):
        """失败结论也要缓存 —— 否则"保存→连不上→再保存"会反复出网。"""
        fake_httpx.response = _FakeResp(401, "no")
        _run()
        r2 = _run()
        assert r2["ok"] is False and r2["cached"] is True
        assert len(fake_httpx.calls) == 1

    def test_clear_cache_forces_probe(self, fake_httpx):
        _run()
        ai_verify.clear_cache()
        _run()
        assert len(fake_httpx.calls) == 2


# ── 5. 出网闸门 + 绝不抛异常 ────────────────────────────────────
class TestGateAndNeverRaises:
    def test_blocked_gate_returns_reason_and_no_request(self, fake_httpx, monkeypatch):
        from agent_community.platform import policy

        class _Ev:
            blocked = True
            needs_approval = False

        monkeypatch.setattr(policy, "check", lambda *a, **kw: (_Ev(), None))
        monkeypatch.setattr(policy, "blocked_message", lambda ev: "策略拒绝出网")
        r = _run()
        assert r["ok"] is False and "策略拒绝出网" in r["reason"]
        assert fake_httpx.calls == [], "闸门拦下就不该再出网"

    def test_pending_approval_returns_reason(self, fake_httpx, monkeypatch):
        from agent_community.platform import policy

        class _Ev:
            blocked = False
            needs_approval = True

        monkeypatch.setattr(policy, "check", lambda *a, **kw: (_Ev(), {"id": "x"}))
        monkeypatch.setattr(policy, "pending_message", lambda ev, p: "等待人工审批")
        r = _run()
        assert r["ok"] is False and "审批" in r["reason"]
        assert fake_httpx.calls == []

    def test_gate_exception_does_not_abort_probe(self, fake_httpx, monkeypatch):
        """闸门自己出错不能被当成"被拦" —— 诊断优先，继续探。"""
        from agent_community.platform import policy
        monkeypatch.setattr(policy, "check",
                            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("gate boom")))
        r = _run()
        assert r["ok"] is True and len(fake_httpx.calls) == 1

    def test_never_raises_on_weird_exception(self, fake_httpx):
        fake_httpx.raise_exc = RuntimeError("some weird failure")
        r = _run()
        assert r["ok"] is False and r["reason"]

    def test_sync_entry_inside_running_loop_reports_instead_of_deadlock(self, fake_httpx):
        """同步入口被从事件循环内调用时必须**明确报错**，不能死锁。"""
        async def _inner():
            return ai_verify.verify_provider("openai", "https://api.example.com",
                                             "example-model-flash", "k")
        r = asyncio.run(_inner())
        assert r["ok"] is False
        assert "verify_provider_async" in r["reason"]


# ── 6. 自省信息不含明文 ─────────────────────────────────────────
class TestIntrospection:
    def test_cache_info_has_no_plaintext_key(self, fake_httpx):
        _run(api_key="sk-super-secret-value")
        info = ai_verify.cache_info()
        assert info["size"] == 1
        assert "sk-super-secret-value" not in str(info)
        assert info["ttl_s"] == 600
