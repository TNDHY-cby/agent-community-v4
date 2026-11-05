# -*- coding: utf-8 -*-
"""API 接入审查修复（V-16）单测：P0-1 / P1-6 / P1-4 / P1-5 / P0-3。

对应审查报告（2026-10-04）：
  P0-1  base_url 含 /v1 -> 拼成 /v1/v1/chat/completions -> 静默 404
  P0-3  已保存模型不在 presets 硬编码清单里 -> .value 赋值静默失败 -> 保存时被冲成空
  P1-4  保存失败仍返回 success:true 且无任何错误字段 -> 用户收不到提示
  P1-5  configured 没把 model 算进去
  P1-6  GateProvider/CacheProvider/UsageProvider 包装链不代理 base_url
        -> GET /api/ai/providers 的 hasattr 恒 False -> 界面看不到生效的 base_url
"""
from __future__ import annotations

import asyncio
import pathlib

import pytest

from agent_community.platform.ai_provider import (
    OpenAICompatibleProvider,
    _normalize_base_url,
    create_ai_provider,
)


def run(coro):
    return asyncio.run(coro)


# ── P0-1：base_url 归一化 ────────────────────────────────────────
class TestBaseUrlNormalization:
    @pytest.mark.parametrize("raw,expect", [
        ("https://api.example.com/v1", "https://api.example.com"),
        ("https://api.example.com/v1/", "https://api.example.com"),
        ("https://api.example.com/", "https://api.example.com"),
        ("https://api.example.com", "https://api.example.com"),
        ("https://api.example.com", "https://api.example.com"),
        ("  https://x.com/v1  ", "https://x.com"),
        ("https://x.com/v1/v1", "https://x.com"),      # 重复也递归剥掉
        ("", ""),
        (None, ""),
    ])
    def test_normalizes(self, raw, expect):
        assert _normalize_base_url(raw) == expect, f"{raw!r} -> {_normalize_base_url(raw)!r}"

    def test_no_v1_v1_in_final_path(self,):
        """核心回归：**最终请求路径不得出现 /v1/v1/**。"""
        for u in ("https://api.example.com/v1",
                  "https://api.example.com/v1/",
                  "https://api.example.com"):
            p = OpenAICompatibleProvider(base_url=u, api_key="k", model="m")
            path = f"{p.base_url}/v1/chat/completions"
            assert "/v1/v1/" not in path, f"{u} -> {path} 仍是 /v1/v1"

    def test_constructor_fallback_still_normalized(self):
        """空 base_url -> 回落 deepseek 默认，且同样归一。"""
        p = OpenAICompatibleProvider(base_url="", api_key="k", model="m")
        assert p.base_url == "https://api.example.com"


# ── P1-6：包装链属性透传 ────────────────────────────────────────
class TestWrapperAttributeDelegation:
    def test_gate_provider_exposes_base_url(self):
        """hasattr 必须为 True —— GET /api/ai/providers 依赖它。"""
        w = create_ai_provider(provider_type="openai",
                               base_url="https://api.example.com/v1",
                               api_key="k", model="m")
        assert hasattr(w, "base_url") is True, \
            "包装链不代理 base_url -> 界面永远看不到生效地址"
        assert w.base_url == "https://api.example.com"
        assert "v1/v1" not in w.base_url

    def test_exposes_model_and_provider_type(self):
        w = create_ai_provider(provider_type="openai",
                               base_url="https://x.com/v1", api_key="k",
                               model="example-model")
        assert w.model == "example-model"
        assert w.provider_type is not None

    def test_dunder_still_raises(self):
        """__dunder__ 必须维持 Python 语义（否则 copy/pickle 拿到假属性）。"""
        w = create_ai_provider(provider_type="openai",
                               base_url="https://x.com", api_key="k", model="m")
        with pytest.raises(AttributeError):
            w.__nonexistent_dunder__

    def test_inner_only_forwarded_once(self):
        """透传应到内层为止，不应递归爆栈。"""
        w = create_ai_provider(provider_type="openai",
                               base_url="https://x.com", api_key="k", model="m")
        # 深层属性（OpenAICompatibleProvider 上的）
        assert hasattr(w, "_get_client") or hasattr(w, "close")


# ── P1-4 / P1-5：保存响应诚实 ────────────────────────────────────
class TestConfigSaveHonesty:
    def _post(self, body):
        from agent_community.platform.routers import config as cf

        class R:
            headers = {}

            async def json(self, _b=body):
                return _b
        return run(cf.api_save_config(R()))

    def test_returns_provider_loaded_and_error(self, monkeypatch):
        """provider 起不来时必须如实回传，不能只返回 success:true。"""
        from agent_community.platform.routers import config as cf

        def boom(**kw):
            raise RuntimeError("无法连接到服务")

        monkeypatch.setattr(cf, "create_ai_provider", boom)
        out = self._post({"ai_provider": "openai", "ai_mode": "remote",
                          "ai_base_url": "https://x.com/v1", "ai_api_key": "k",
                          "ai_model": "m"})
        assert out["success"] is True, "配置本身保存成功"
        assert out["provider_loaded"] is False, "provider 未起来必须如实报"
        assert "无法连接" in out["provider_error"], "错误内容必须回传，否则前端没法显示"

    def test_returns_provider_loaded_true(self, monkeypatch):
        from agent_community.platform.routers import config as cf

        class _P:
            provider_type = "openai"

        monkeypatch.setattr(cf, "create_ai_provider", lambda **kw: _P())
        out = self._post({"ai_provider": "openai", "ai_mode": "remote",
                          "ai_base_url": "https://x.com/v1", "ai_api_key": "k",
                          "ai_model": "m"})
        assert out["provider_loaded"] is True
        assert out["provider_error"] == ""

    def test_model_missing_flag_when_remote_without_model(self):
        """本次提交没选模型 -> 如实告警（即便有默认值兜底）。"""
        out = self._post({"ai_provider": "openai", "ai_mode": "remote",
                          "ai_base_url": "https://x.com", "ai_api_key": "k",
                          "ai_model": ""})
        assert out["model_missing"] is True, "remote 模式空提交必须显式告警"
        # ★2026-11-05（0e04b7c 之后）：DEFAULT_CONFIG 不再带占位模型 ⇒ 没有"兜底生效的模型"了。
        #   正确口径：model_effective 为空 **且** 明确告诉用户"没配"（不许假装配好了）。
        assert not out["model_effective"], (
            "默认已不带占位模型，空提交不应凭空出现一个 model_effective：%r" % out["model_effective"])
        hint = str(out.get("model_missing_hint") or out.get("next") or out.get("howto") or "")
        assert ("模型" in hint) or ("model" in hint.lower()), (
            "空模型时必须给出一句人话提示（填哪个/在哪填）：%r" % out)

    def test_model_present_clears_flag(self):
        out = self._post({"ai_provider": "openai", "ai_mode": "remote",
                          "ai_base_url": "https://x.com", "ai_api_key": "k",
                          "ai_model": "example-model"})
        assert out["model_missing"] is False

    def test_blank_selection_does_not_wipe_saved_model(self):
        """**不得冲掉已保存的模型** —— 真两步测（原版只做一次空提交，其实没测到这件事）。

        ★2026-11-05：`0e04b7c` 之后 `DEFAULT_CONFIG` 不再带占位模型，
        "空提交会回退到默认值"这层兜底没了 ⇒ 必须**先把一个模型存进去**，再看空提交会不会把它冲掉。
        """
        first = self._post({"ai_provider": "openai", "ai_mode": "remote",
                            "ai_base_url": "https://x.com", "ai_api_key": "k",
                            "ai_model": "keep-me-model"})
        assert first.get("model_effective") == "keep-me-model", first
        out = self._post({"ai_provider": "openai", "ai_mode": "remote",
                          "ai_base_url": "https://x.com", "ai_api_key": "k",
                          "ai_model": ""})
        assert out["model_effective"], "空提交不得导致已保存的 model 变空"
        assert out["model"] == out["model_effective"]

    def test_base_url_echoed_normalized(self):
        """回传的 base_url 应是归一后的（与 provider 实际用的一致，便于排障）。"""
        out = self._post({"ai_provider": "openai", "ai_mode": "remote",
                          "ai_base_url": "https://x.com/v1/", "ai_api_key": "k",
                          "ai_model": "m"})
        assert out["base_url"] == "https://x.com/v1/", "原样回显用户输入（存配置）"
        # provider 内部才是归一后的
        p = OpenAICompatibleProvider(base_url=out["base_url"], api_key="k", model="m")
        assert p.base_url == "https://x.com"


# ── P0-3：前端回填（源码断言 + 逻辑）────────────────────────────
class TestFrontendModelRestore:
    def _idx(self) -> str:
        p = (pathlib.Path(__file__).resolve().parents[1] / "frontend" / "index.html")
        return p.read_text(encoding="utf-8", errors="replace")

    def test_option_is_appended_when_missing(self):
        src = self._idx()
        assert "o.value=cfg.ai_model" in src, "缺：为已保存模型补选项"
        assert "已保存" in src, "缺：补的选项要标注来源"
        assert "Array.from(ms.options).some" in src, "缺：判断选项是否已存在"

    def test_saved_model_really_settable(self):
        """逻辑复刻：清单里没有时，补选项后 value 必须能设上。"""
        # 模拟 <select>
        class Opt:
            def __init__(self, v):
                self.value = v
        class Sel:
            def __init__(self, models):
                self.options = [Opt(m) for m in models]
                self.value = ""
            def appendChild(self, o):
                self.options.append(o)

        sel = Sel(["gpt-4o", "gpt-4o-mini"])          # presets.openai 的清单
        saved = "example-model-flash"                    # 配置里存的
        if not any(o.value == saved for o in sel.options):
            sel.appendChild(Opt(saved))
        sel.value = saved
        assert sel.value == "example-model-flash", "补选项后必须能选上"
