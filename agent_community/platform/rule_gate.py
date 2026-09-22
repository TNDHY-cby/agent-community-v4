"""规则闸门 RuleGate — LLM 调用前置纯规则拦截，命中即短路，零 token。

设计原则：
1. 只短路高置信度规则；低置信度一律放行 LLM（宁多花 token 不误判）。
2. 有副作用的动作模板必须校验参数（未知实体回退 LLM）。
3. 每次命中打日志 `[rule-gate] hit=<kind> cost=0`，保证可观测。
4. 规则源 = 内置默认规则 + 配置扩展（RULE_GATE_PATTERNS / TRIGGER_KEYWORDS）。
5. 纯标准库，无平台耦合，可直接随仓库开源。

用法：
    from platform.rule_gate import RuleGate

    gate = RuleGate()
    hit = gate.match("你好", ctx={})
    if hit:
        return hit.reply
"""

from __future__ import annotations
import ast
import datetime
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional


# ═══════════════════════════════════════════════════════════════
# 命中结构
# ═══════════════════════════════════════════════════════════════

@dataclass
class GateHit:
    """一次规则命中的结果。
    - kind: 命中类型标识（greeting / version / status / help / time / thanks /
            bye / empty / ack / math / trigger / profanity / classify_*）
    - reply: 直接返回给调用方的文本（chat 路径）
    - action: 动作标识（预留：参数化模板 / 固定流程，P1+ 使用）
    - data: 附加数据（如 classify 的选择结果）
    """
    kind: str
    reply: Optional[str] = None
    action: Optional[str] = None
    data: Optional[dict] = None


# ═══════════════════════════════════════════════════════════════
# 内置默认规则
# ═══════════════════════════════════════════════════════════════

# 问候（精确/短句，高置信度；"在吗"等状态查询归 status）
_GREETING_RE = re.compile(
    r"^(你?好|您好|hi|hello|嗨|哈喽|喂|hey)[!！。.？?~～\s]*$",
    re.IGNORECASE,
)
# 状态查询
_STATUS_RE = re.compile(
    r"^(在吗|在不在|在么|在不在线|忙吗|在线吗|还在吗|你醒着吗|你在吗)[!！。.？?~～\s]*$",
)
# 版本查询
_VERSION_RE = re.compile(
    r"^(什么版本|版本号|版本|你是哪个版本|platform\s*version|v\d)"
    r"[!！。.？?~～\s]*$",
    re.IGNORECASE,
)
# 帮助 / 能力
_HELP_RE = re.compile(
    r"^(帮助|help|能做什么|你会什么|你会啥|有什么功能|功能列表|怎么用|使用说明)"
    r"[!！。.？?~～\s]*$",
    re.IGNORECASE,
)
# 时间 / 日期
_TIME_RE = re.compile(
    r"^(现在几点|几点了|几点|现在时间|时间|日期|今天几号|今天星期几|星期几)"
    r"[!！。.？?~～\s]*$",
)
# 感谢
_THANKS_RE = re.compile(
    r"^(谢谢|多谢|感谢|辛苦了|thank\s*you|thanks)[!！。.~～\s]*$",
    re.IGNORECASE,
)
# 再见
_BYE_RE = re.compile(
    r"^(再见|拜拜|bye|88|晚安|先忙吧)[!！。.~～\s]*$",
    re.IGNORECASE,
)
# 单字确认
_ACK_RE = re.compile(
    r"^(好|行|嗯|哦|ok|好的|收到|可以|没问题)[!！。.~～\s]*$",
    re.IGNORECASE,
)
# 纯标点 / 空消息
_EMPTY_RE = re.compile(r"^[\s\W_]+$")
# 简单算术：仅数字 / 四则运算符 / 括号 / 空格
_MATH_RE = re.compile(r"^[\d\s+\-*/()%.]+$")
_MATH_OP_RE = re.compile(r"[+\-*/%]")
# 无意义短文本（非单字、无实义）：长度 < 2 且不是 ack / 数字
_NONSENSE_RE = re.compile(r"^[\s\W_]{0,1}$")


# ═══════════════════════════════════════════════════════════════
# classify 能力词硬匹配（仅高置信度使用）
# ═══════════════════════════════════════════════════════════════

# 能力词表：query 含这些强词时才可能规则匹配（避免"代码"等泛词误伤）
_CAPABILITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "coding": ("写代码", "编码", "编程", "实现一个", "开发一个", "修复bug", "修bug", "debug", "重构代码"),
    "writing": ("写文案", "写文章", "写周报", "写日报", "写总结", "写报告", "起草", "润色"),
    "translation": ("翻译", "译成", "中译英", "英译中"),
    "search": ("搜索", "查找资料", "查资料", "搜一下", "检索", "查一下"),
    "testing": ("测试用例", "跑测试", "单元测试", "冒烟测试", "回归测试", "写测试"),
    "data": ("数据分析", "处理数据", "统计", "图表", "可视化", "爬数据", "抓数据"),
    "doc": ("整理文档", "写文档", "文档", "readme", "说明书", "笔记"),
}


# ═══════════════════════════════════════════════════════════════
# 规则闸门
# ═══════════════════════════════════════════════════════════════

class RuleGate:
    """LLM 调用前置规则闸门：命中即短路，零 token。"""

    def __init__(
        self,
        patterns: Optional[dict] = None,
        trigger_keywords: Optional[list[str]] = None,
        enabled: bool = True,
    ):
        self.enabled = enabled
        # 配置扩展规则：{pattern: reply}，pattern 为正则字符串
        self.patterns: dict[str, str] = dict(patterns or {})
        # 测试触发词清单（用户给定关键词，命中仅简单回复）
        self.trigger_keywords: list[str] = list(
            trigger_keywords
            or _env_list("AC_TRIGGER_KEYWORDS")
            or []
        )

    # ── 对外主入口 ──────────────────────────────────────────

    def match(self, text: str, ctx: Optional[dict] = None) -> Optional[GateHit]:
        """对用户文本做规则匹配，命中返回 GateHit，未命中返回 None。"""
        if not self.enabled:
            return None
        ctx = ctx or {}
        raw = text or ""
        s = raw.strip()

        # 1. 空消息 / 纯标点
        if not s or _EMPTY_RE.match(s):
            return self._hit("empty", "请说具体需求")

        # 2. 测试触发词（最高优先，行为：仅简单回复）
        for kw in self.trigger_keywords:
            if kw and kw in raw:
                return self._hit("trigger", _env("AC_TRIGGER_REPLY", "收到"))

        # 3. 问候
        if _GREETING_RE.match(s):
            return self._hit("greeting", "你好，我在线")

        # 4. 状态查询
        if _STATUS_RE.match(s):
            return self._hit("status", "在线，待命中")

        # 5. 版本查询
        if _VERSION_RE.match(s):
            return self._hit("version", "外端Agent生产合作社（External Agent Community）v4")

        # 6. 帮助
        if _HELP_RE.match(s):
            return self._hit(
                "help",
                "我能处理：任务编排 / 多 Agent 协作 / Harness 接入 / 文件与工具调用。"
                "发送具体需求即可。",
            )

        # 7. 时间 / 日期
        if _TIME_RE.match(s):
            now = datetime.datetime.now()
            return self._hit(
                "time",
                now.strftime("%Y-%m-%d %H:%M:%S") + "（北京时间）",
            )

        # 8. 感谢
        if _THANKS_RE.match(s):
            return self._hit("thanks", "不客气")

        # 9. 再见
        if _BYE_RE.match(s):
            return self._hit("bye", "随时找我")

        # 10. 单字确认
        if _ACK_RE.match(s):
            return self._hit("ack", "收到")

        # 11. 简单算术（安全 eval：先 AST 校验节点类型）
        if _MATH_RE.match(s) and _MATH_OP_RE.search(s):
            val = self._safe_math(s)
            if val is not None:
                return self._hit("math", str(val))

        # 12. 无意义短文本
        if _NONSENSE_RE.match(s):
            return self._hit("nonsense", "没听懂，换个说法")

        # 13. 配置扩展规则（正则匹配）
        for pat, reply in self.patterns.items():
            try:
                if re.search(pat, raw, re.IGNORECASE):
                    return self._hit("custom", reply)
            except re.error:
                continue

        return None

    def match_classify(
        self,
        query: str,
        candidates: list[dict],
        context: str = "",
    ) -> Optional[GateHit]:
        """classify 规则短路：只处理高置信度场景。

        规则：
        - 候选为空 → 直接返回空选择
        - 候选唯一 → 直接选中
        - 能力词硬匹配：query 含强能力词，且恰好一个候选能力命中 → 选中；
          多个候选命中 / 无命中 → 放行 LLM（不误伤）。
        """
        if not self.enabled:
            return None
        cands = candidates or []

        # 候选为空
        if not cands:
            return self._hit("classify_empty", data={"selected": [], "reason": "无候选"})

        # 候选唯一
        if len(cands) == 1:
            c = cands[0]
            return self._hit(
                "classify_single",
                data={
                    "selected": [c.get("id")],
                    "reason": f"唯一候选 {c.get('name', c.get('id'))}",
                },
            )

        # 能力词硬匹配（高置信度才短路）：同一能力词下命中多个候选 → 放行 LLM
        q = (query or "").lower()
        matched_ids: list[str] = []
        matched_reason = ""
        for cap, kws in _CAPABILITY_KEYWORDS.items():
            if not any(kw.lower() in q for kw in kws):
                continue
            hits = [c for c in cands if cap in " ".join(c.get("capabilities") or []).lower()]
            if len(hits) == 1:
                matched_ids = [hits[0].get("id")]
                matched_reason = f"{hits[0].get('name', hits[0].get('id'))} 能力匹配 {cap}"
                break
            # 多个候选命中同一能力 → 不短路，放行 LLM
            if len(hits) > 1:
                return None
        if len(matched_ids) == 1:
            return self._hit(
                "classify_capability",
                data={"selected": matched_ids, "reason": matched_reason},
            )
        return None

    # ── 内部工具 ────────────────────────────────────────────

    def _hit(self, kind: str, reply: Optional[str] = None, data: Optional[dict] = None) -> GateHit:
        print(f"[rule-gate] hit={kind} cost=0", flush=True)
        return GateHit(kind=kind, reply=reply, data=data)

    @staticmethod
    def _safe_math(expr: str) -> Optional[float]:
        """安全计算四则表达式：AST 白名单校验，禁止任意代码执行。"""
        try:
            tree = ast.parse(expr, mode="eval")
            for node in ast.walk(tree):
                if not isinstance(
                    node,
                    (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
                     ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
                     ast.USub, ast.UAdd, ast.Pow),
                ):
                    return None
                if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
                    return None
            return float(eval(expr, {"__builtins__": {}}, {}))
        except Exception:
            return None


# ═══════════════════════════════════════════════════════════════
# 环境变量小工具
# ═══════════════════════════════════════════════════════════════

def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default) or default


def _env_list(name: str, sep: str = ",") -> list[str]:
    val = os.environ.get(name, "")
    if not val:
        return []
    return [x.strip() for x in val.split(sep) if x.strip()]


# 模块级单例（默认不启用触发词，由 config / 环境变量注入）
_default_gate: Optional[RuleGate] = None


def get_default_gate() -> RuleGate:
    """返回全局默认闸门（惰性创建，读取 config 注入触发词/扩展规则）。"""
    global _default_gate
    if _default_gate is None:
        _default_gate = RuleGate()
        try:
            from ..config import load_config
            cfg = load_config()
            _default_gate.enabled = bool(cfg.get("rule_gate_enabled", True))
            kws = cfg.get("trigger_keywords") or []
            if isinstance(kws, list):
                _default_gate.trigger_keywords = list(kws) or _default_gate.trigger_keywords
            pats = cfg.get("rule_gate_patterns") or {}
            if isinstance(pats, dict):
                _default_gate.patterns = dict(pats) or _default_gate.patterns
        except Exception:
            pass  # config 不可用时保持默认
    return _default_gate
