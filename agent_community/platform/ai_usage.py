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
import os
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
            else:
                usage_dir = os.path.join(os.path.expanduser("~"), ".agent_community", "data", "usage")
        except Exception:
            pass
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
        except Exception:
            pass
    return _default_usage
