"""AI 调用预算计数与熔断 — 保护 API 额度。

- 每次真实 API 调用记录：时间 / model / prompt_chars / reply_chars / 估算 token / 估算费用
- 日/月累计追加 data/usage/usage.jsonl
- 阈值熔断（config / 环境变量可配）：
  - 日花费超 AC_BUDGET_DAILY（默认 ¥2）→ 后续调用返回降级提示
  - 月花费超 AC_BUDGET_MONTHLY（默认 ¥20）→ 同样降级（只读保护）
- 估算：中文按 1 token≈1.5 字符粗略换算；费用按 AC_AI_PRICE_PER_1K（元/1K tokens）
"""

from __future__ import annotations
import json

import logging

logger = logging.getLogger("ac.usage")

#: 同一类失败**每进程只喊一次**（不刷屏，但不许静默 —— 准则⑤）。
_WARNED: set = set()


def _warn_once(key: str, msg: str, exc: BaseException | None = None) -> None:
    if key in _WARNED:
        return
    _WARNED.add(key)
    logger.warning("%s", msg, exc_info=exc is not None)


import os
from pathlib import Path
import time
from typing import Optional


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    # 粗略估算：CJK 字符计 1 token，其余按 4 字符 1 token
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return cjk + other // 4


class AIUsage:
    def __init__(self, usage_dir: str = ""):
        self._usage_dir = usage_dir or os.environ.get("AC_AI_USAGE_DIR", "")
        self._daily_budget = float(os.environ.get("AC_BUDGET_DAILY", "2.0"))
        self._monthly_budget = float(os.environ.get("AC_BUDGET_MONTHLY", "20.0"))
        self._price_per_1k = float(os.environ.get("AC_AI_PRICE_PER_1K", "0.0"))
        self._path = ""
        if self._usage_dir:
            self._path = os.path.join(self._usage_dir, "usage.jsonl")

    @property
    def daily_budget(self) -> float:
        return self._daily_budget

    @daily_budget.setter
    def daily_budget(self, v: float):
        self._daily_budget = float(v)

    @property
    def monthly_budget(self) -> float:
        return self._monthly_budget

    @monthly_budget.setter
    def monthly_budget(self, v: float):
        self._monthly_budget = float(v)

    @property
    def price_per_1k(self) -> float:
        return self._price_per_1k

    @price_per_1k.setter
    def price_per_1k(self, v: float):
        self._price_per_1k = float(v)

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime())

    def _month(self) -> str:
        return time.strftime("%Y-%m", time.localtime())

    def _load_records(self) -> list[dict]:
        if not self._path or not os.path.exists(self._path):
            return []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                return [json.loads(ln) for ln in f if ln.strip()]
        except Exception:
            return []

    def _append(self, rec: dict):
        if not self._path:
            return
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[ai-usage] 落盘失败: {str(e)[:100]}", flush=True)

    def record(
        self,
        model: str,
        prompt_text: str,
        reply_text: str,
    ) -> float:
        """记录一次真实调用，返回本次估算费用（元）。"""
        p_tok = _estimate_tokens(prompt_text)
        r_tok = _estimate_tokens(reply_text)
        cost = (p_tok + r_tok) / 1000.0 * self._price_per_1k
        rec = {
            "ts": time.time(),
            "date": self._today(),
            "month": self._month(),
            "model": model,
            "prompt_chars": len(prompt_text),
            "reply_chars": len(reply_text),
            "prompt_tokens": p_tok,
            "completion_tokens": r_tok,
            "cost": round(cost, 6),
        }
        self._append(rec)
        return cost

    def spent_daily(self) -> float:
        day = self._today()
        return sum(float(r.get("cost", 0)) for r in self._load_records() if r.get("date") == day)

    def spent_monthly(self) -> float:
        mon = self._month()
        return sum(float(r.get("cost", 0)) for r in self._load_records() if r.get("month") == mon)

    def blocked(self) -> bool:
        """是否触发熔断（日/月超限）。"""
        return (
            self._daily_budget > 0 and self.spent_daily() >= self._daily_budget
        ) or (
            self._monthly_budget > 0 and self.spent_monthly() >= self._monthly_budget
        )


_default_usage: Optional[AIUsage] = None


#: 各家桥回报里用量字段的**别名**（认多种写法；认不出就不猜）
_USAGE_ALIASES = {
    "prompt_tokens": ("prompt_tokens", "input_tokens", "promptTokenCount", "in"),
    "completion_tokens": ("completion_tokens", "output_tokens", "candidatesTokenCount", "out"),
    "total_tokens": ("total_tokens", "totalTokenCount", "tokens", "total"),
    "cached_tokens": ("cached_tokens", "cache_hit_tokens", "cachedTokenCount", "cache_hits"),
    "cost": ("cost", "total_cost", "price", "cost_usd"),
    "elapsed_ms": ("elapsed_ms", "latency_ms", "duration_ms", "elapsed"),
    "model": ("model", "model_name"),
}


def extract_usage(raw) -> dict:
    """把一份桥回报里的用量**归一**成界面能直接显示的字段；**没有就返回 {}**（不编数）。

    `raw` 可以是整个回报 body，也可以是 body["usage"]；两种都能认。
    """
    if not isinstance(raw, dict) or not raw:
        return {}
    src = raw.get("usage") if isinstance(raw.get("usage"), dict) else raw
    if not isinstance(src, dict):
        return {}
    out: dict = {}
    for canon, keys in _USAGE_ALIASES.items():
        for k in keys:
            if k in src and src[k] not in (None, ""):
                out[canon] = src[k]
                break
    # 只保留看得懂的数值/字符串，别把整包塞进消息（消息是要落盘的）
    if not out:
        return {}
    for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "cost", "elapsed_ms"):
        if k in out:
            try:
                out[k] = float(out[k])
                if out[k].is_integer():
                    out[k] = int(out[k])
            except (TypeError, ValueError):
                out.pop(k, None)
    if "model" in out:
        out["model"] = str(out["model"])[:60]
    if "total_tokens" not in out and ("prompt_tokens" in out or "completion_tokens" in out):
        out["total_tokens"] = int(out.get("prompt_tokens", 0)) + int(out.get("completion_tokens", 0))
    return out


def get_default_usage() -> AIUsage:
    """全局默认用量器（惰性创建，data_dir/usage 优先）。"""
    global _default_usage
    if _default_usage is None:
        usage_dir = ""
        try:
            from ..config import load_config
            cfg = load_config()
            root = cfg.get("data_dir") or ""
            if root:
                usage_dir = os.path.join(root, "usage")
        except Exception as _e:            # 读配置失败 ⇒ 用量目录仍按一处真相走（见下）
            _warn_once("usage_cfg_read_failed",
                       "读 AI 用量配置失败：%s: %s" % (type(_e).__name__, str(_e)[:120]), _e)
        # ★2026-10-21 反私人定制：用量目录**只认一处真相**（AC_AI_USAGE_DIR > AC_DATA_DIR/usage），
        #   不再回落到 ~/.agent_community/...（那等于把账记在某一台机器的某一个用户名下）
        # 优先级：显式 AC_AI_USAGE_DIR > 配置里的 data_dir > 一处真相（AC_DATA_DIR/usage）
        # ⚠️用户的显式环境变量必须最高优先，否则"换数据目录不改代码"就不成立（守卫抓过这条）
        if os.environ.get("AC_AI_USAGE_DIR"):
            usage_dir = os.environ["AC_AI_USAGE_DIR"]
        if not usage_dir:
            try:
                from .env_spec import data_dir as _data_dir
                usage_dir = str(Path(_data_dir()) / "usage")
            except Exception as _e:        # noqa: BLE001
                _warn_once("usage_dir_resolve_failed",
                           "解析 AI 用量目录失败，本次不记账：%s: %s"
                           % (type(_e).__name__, str(_e)[:120]), _e)
                usage_dir = ""
        _default_usage = AIUsage(usage_dir=usage_dir)
        # 预算与单价从 config 注入（构造时环境变量优先，这里用 config 覆盖默认）
        try:
            from ..config import load_config as _lc
            _cfg = _lc()
            if _cfg.get("budget_daily") is not None:
                _default_usage.daily_budget = float(_cfg["budget_daily"])
            if _cfg.get("budget_monthly") is not None:
                _default_usage.monthly_budget = float(_cfg["budget_monthly"])
            if _cfg.get("ai_price_per_1k") is not None:
                _default_usage.price_per_1k = float(_cfg["ai_price_per_1k"])
        except Exception as _e:            # ★预算/单价读不出来 ⇒ 熔断与花费都不准，必须说
            _warn_once("usage_budget_read_failed",
                       "读 AI 预算/单价失败（预算熔断与花费统计可能不准）：%s: %s"
                       % (type(_e).__name__, str(_e)[:120]), _e)
    return _default_usage
