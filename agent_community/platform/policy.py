# -*- coding: utf-8 -*-
"""platform/policy：ALLOW / DENY / ASK 策略引擎（V-14 第一片）。

设计稿：design-docs/V14_安全治理策略引擎设计.md（已拍板）

三条核心原则（拍板明确要求，改动前先读设计稿 §八）：

1. **代码里不写死任何策略**
   `_SEED_CONFIG` 只在**首次运行**用于生成 `data/policy.json`；此后一切判定都读配置文件。
   用户改文件即改策略 —— 不同团队对「哪些操作必须人工确认」容忍度不同。

2. **信任根不可替代**（本片最重要的一条）
   第一片审批权**仅限人工**：`resolve()` 拒绝 AI actor（返回 forbidden）。
   第二通道的平台 AI 在第二片也只给「建议权」，不给决定权。
   理由：第二通道 AI 自身可能被 prompt injection 污染，若它握有批准 `shell.exec` 的权力，
   攻击者只需先污染其输入再触发 ASK，就可能拿到执行权。人的判断慢，但不可被注入绕过。

3. **失败不阻塞**（沿用 audit.py 同原则）
   策略文件损坏/缺失 → 回落内置种子并打印，绝不中断业务。

决策顺序（先匹配先生效）::

    1. rules（有序列表；内置黑名单 builtin.danger 排第一，DENY 天然优先）
    2. actor_overrides（按 actor 覆盖分类默认 —— 解决 auto-connect 的安全/产品冲突）
    3. default_by_op（出厂策略 C：分类别默认）
    4. 兜底 ALLOW（未显式配置的类别不阻断开发）

待审批队列**单独落 `data/policy_pending.json`，不与配置混存**。
理由：配置是用户可编辑的只读态；运行时状态若写回同一文件，会重演
「harnesses.json 被写空」那类事故（读-改-写把用户刚做的编辑覆盖掉）。
"""

from __future__ import annotations

import copy
import fnmatch
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ── 决策三态 ──────────────────────────────────────────────────────
ALLOW = "allow"
DENY = "deny"
ASK = "ask"
DECISIONS = (ALLOW, DENY, ASK)

# ── 操作类别（接入点见设计稿 §3.2）────────────────────────────────
SHELL_EXEC = "shell.exec"
FILE_WRITE = "file.write"
FILE_DELETE = "file.delete"
NETWORK_EGRESS = "network.egress"
PLUGIN_INVOKE = "plugin.invoke"
PLUGIN_INSTALL = "plugin.install"
CONFIG_MUTATE = "config.mutate"
BRIDGE_WRITE = "harness.bridge.write"     # 桥脚本落盘（实测会写可执行文件）
BRIDGE_LAUNCH = "harness.bridge.launch"   # 桥进程启动（实测 Popen）
HARNESS_LAUNCH = "harness.launch"         # 拉起 harness 本体进程（harness_launcher.Popen）
# 说明：ProbeHarnessTool 的 subprocess.check_output 用**硬编码常量命令**（只读扫进程/端口），
# 无用户可控命令串、无注入面 → 不属执行类高危，本片不纳入闸门（记录于设计稿 §九）。

# ── actor（威胁模型是「AI 驱动的代码执行」，不是「操作者点按钮」）──
ACTOR_OPERATOR = "operator"        # 本机前端 / 人发起
ACTOR_PLATFORM_AI = "platform_ai"  # 平台自身 AI（助手 react_loop 的工具调用，见下）
ACTOR_EXTERNAL_AI = "external_ai"  # 外端 AI
ACTOR_HARNESS = "harness"          # harness 本体回调
ACTOR_UNKNOWN = "unknown"

# 关于 ACTOR_PLATFORM_AI：工具层的唯一调用方是 react_loop → tool_registry.execute
# → tool.execute(**params)，**参数里不带调用方身份**，而这条链永远是平台 AI 在决策。
# 所以工具内部固定用本 actor —— 命名精确是为了审计可追溯（§3.5 要求），
# 安全语义与 external_ai 一致：**都不是人，都不吃 operator 的 actor_override**。

# 有审批决定权的 actor：**只有人**（拍板结论，第二片也不变）
HUMAN_ACTORS = frozenset({ACTOR_OPERATOR})

# 哨兵：指向 core/security.py 现有的危险命令黑名单
DANGER_SENTINEL = "__builtin_dangerous__"

# ── 出厂种子配置（仅首次生成 policy.json 用；不是运行时策略）──────
_SEED_CONFIG: dict[str, Any] = {
    "version": 1,
    "_comment": "策略配置文件。改这里即改策略，代码不硬编码。default_by_op=出厂策略C（分类别）。",
    "default_by_op": {
        SHELL_EXEC: ASK,
        PLUGIN_INSTALL: ASK,
        FILE_DELETE: ASK,
        BRIDGE_WRITE: ASK,
        BRIDGE_LAUNCH: ASK,
        HARNESS_LAUNCH: ASK,
        NETWORK_EGRESS: ALLOW,
        PLUGIN_INVOKE: ALLOW,
        FILE_WRITE: ALLOW,
        CONFIG_MUTATE: ALLOW,
    },
    "actor_overrides": {
        # 人从本机 UI 点「一键注册」→ 放行；AI 自主生成并启动脚本 → 走 ASK
        ACTOR_OPERATOR: {
            BRIDGE_WRITE: ALLOW,
            BRIDGE_LAUNCH: ALLOW,
        },
    },
    "rules": [
        {
            "id": "builtin.danger",
            "op": SHELL_EXEC,
            "match": DANGER_SENTINEL,
            "decision": DENY,
            "note": "危险命令黑名单（复用 core/security._is_dangerous）",
        },
    ],
}


@dataclass
class Evaluation:
    """一次判定的完整结果 —— 带 rule_id 与理由，供审计满足「决策可追溯」。"""

    decision: str
    op: str
    actor: str = ACTOR_UNKNOWN
    target: str = ""
    rule_id: str = ""
    note: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision == ALLOW

    @property
    def needs_approval(self) -> bool:
        return self.decision == ASK

    @property
    def blocked(self) -> bool:
        return self.decision == DENY

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "op": self.op,
            "actor": self.actor,
            "target": self.target,
            "rule_id": self.rule_id,
            "note": self.note,
        }


def _dangerous(command: str) -> Optional[str]:
    """惰性调用现有黑名单（避免模块级循环导入）。返回命中的模式或 None。"""
    try:
        from .core.security import _is_dangerous
        return _is_dangerous(command)
    except Exception as e:  # 黑名单不可用时不因此放行危险判定
        print(f"[policy] 黑名单不可用（{e}），本规则跳过", flush=True)
        return None


class PolicyEngine:
    """策略引擎。判定纯计算；`request/resolve` 维护待审批队列。"""

    def __init__(
        self,
        data_dir: Path = DEFAULT_DATA_DIR,
        config: Optional[dict] = None,
        autoload: bool = True,
    ):
        self.data_dir = Path(data_dir)
        self.config_path = self.data_dir / "policy.json"
        self.pending_path = self.data_dir / "policy_pending.json"
        self._lock = threading.RLock()
        # **必须 deepcopy**：`dict(_SEED_CONFIG)` 是浅拷贝，嵌套的 default_by_op /
        # actor_overrides / rules 与模块级种子共享同一批对象——实例一旦改配置
        # （用户改策略、或测试改类别默认），就会污染全局种子，让后续实例拿到被改过的默认值。
        # 这是 2026-10-03 由 test_policy_wiring 的两个失败用例暴露出来的真 bug。
        self._config: dict = copy.deepcopy(_SEED_CONFIG)
        self._pending: list[dict] = []
        self.config_source = "seed"          # seed | file | injected
        if config is not None:
            self._config = copy.deepcopy(config)
            self.config_source = "injected"
        elif autoload:
            self._load_config()
            self._load_pending()

    # ── 配置加载 ────────────────────────────────────────────────
    def _load_config(self) -> None:
        try:
            if self.config_path.exists():
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    # 用户文件里的 pending 键（若有）不参与运行时，队列独立存放
                    data.pop("pending", None)
                    self._config = data
                    self.config_source = "file"
                    return
                print("[policy] policy.json 结构不是对象，回落内置种子", flush=True)
        except Exception as e:
            print(f"[policy] 读 policy.json 失败（回落内置种子）: {e}", flush=True)
        # 首次运行：把种子写到磁盘，让用户看得见、改得动
        self._save_config()

    def _save_config(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(_SEED_CONFIG, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[policy] 写 policy.json 失败（忽略）: {e}", flush=True)

    def reload(self) -> dict:
        """重新读配置（供 GET /api/policy/reload 与测试证明「改文件即改策略」）。"""
        with self._lock:
            self._load_config()
            return {"config_source": self.config_source,
                    "rules": len(self._config.get("rules") or [])}

    # ── 待审批队列（独立落盘）──────────────────────────────────
    def _load_pending(self) -> None:
        try:
            if self.pending_path.exists():
                with open(self.pending_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._pending = data
        except Exception as e:
            print(f"[policy] 读 policy_pending.json 失败（忽略）: {e}", flush=True)

    def _save_pending(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with open(self.pending_path, "w", encoding="utf-8") as f:
                json.dump(self._pending[-200:], f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[policy] 写 policy_pending.json 失败（忽略）: {e}", flush=True)

    # ── 判定 ────────────────────────────────────────────────────
    def _rule_hit(self, rule: dict, op: str, target: str) -> bool:
        rop = rule.get("op")
        if rop and rop != op:
            return False
        pat = rule.get("match") or "*"
        if pat == "*":
            return True
        if pat == DANGER_SENTINEL:
            if op != SHELL_EXEC:
                return False
            return _dangerous(target or "") is not None
        return fnmatch.fnmatch(target or "", pat)

    def evaluate(
        self,
        op: str,
        *,
        actor: str = ACTOR_UNKNOWN,
        target: str = "",
        detail: Optional[dict] = None,
    ) -> Evaluation:
        """按 §决策顺序 给出 ALLOW / DENY / ASK。纯计算，不落盘、不入队。"""
        cfg = self._config
        actor = actor or ACTOR_UNKNOWN

        # 1. rules（有序，先匹配先生效；黑名单排第一 → DENY 天然优先）
        for rule in cfg.get("rules") or []:
            if self._rule_hit(rule, op, target):
                return Evaluation(
                    decision=rule.get("decision", ALLOW),
                    op=op, actor=actor, target=target,
                    rule_id=rule.get("id", ""),
                    note=rule.get("note", ""),
                )

        # 2. actor_overrides（按 actor 覆盖分类默认）
        overrides = cfg.get("actor_overrides") or {}
        for key in (actor, "*"):
            ov = overrides.get(key) or {}
            if op in ov:
                return Evaluation(
                    decision=ov[op], op=op, actor=actor, target=target,
                    rule_id=f"actor_override:{key}",
                    note=f"{key} 对该操作显式覆盖",
                )

        # 3. default_by_op（出厂策略 C）
        d = (cfg.get("default_by_op") or {}).get(op)
        if d in DECISIONS:
            return Evaluation(
                decision=d, op=op, actor=actor, target=target,
                rule_id="default_by_op",
                note="分类别默认（策略 C）",
            )

        # 4. 兜底：未配置的类别不阻断开发
        return Evaluation(
            decision=ALLOW, op=op, actor=actor, target=target,
            rule_id="fallback",
            note="未配置的操作类别默认放行",
        )

    # ── 待审批：入队 / 审批 ─────────────────────────────────────
    def request(
        self,
        op: str,
        *,
        actor: str = ACTOR_UNKNOWN,
        target: str = "",
        detail: Optional[dict] = None,
        reason: str = "",
    ) -> dict:
        """ASK 时把操作挂起入队，返回带 id 的记录。"""
        with self._lock:
            rec = {
                "id": uuid.uuid4().hex[:12],
                "op": op,
                "actor": actor,
                "target": target,
                "detail": detail or {},
                "reason": reason,
                "created": time.time(),
                "status": "pending",
                "resolved_by": "",
                "resolved_at": None,
                "resolve_note": "",
            }
            self._pending.append(rec)
            self._save_pending()
            return dict(rec)

    def resolve(
        self,
        pending_id: str,
        approve: bool,
        by: str = ACTOR_OPERATOR,
        note: str = "",
    ) -> dict:
        """审批一个待办。**仅人工**可批（拍板结论；AI 只有第二片的建议权）。"""
        if by not in HUMAN_ACTORS:
            return {
                "error": "forbidden",
                "detail": f"审批权仅限人工；by={by!r} 无决定权（AI 仅有建议权，见设计稿 §3.3）",
            }
        with self._lock:
            for rec in self._pending:
                if rec.get("id") != pending_id:
                    continue
                if rec.get("status") != "pending":
                    return {"error": "already_resolved", "record": dict(rec)}
                rec["status"] = "approved" if approve else "rejected"
                rec["resolved_by"] = by
                rec["resolved_at"] = time.time()
                rec["resolve_note"] = note
                self._save_pending()
                return {"ok": True, "record": dict(rec)}
        return {"error": "not_found", "detail": f"无此待办 id: {pending_id}"}

    def list_pending(self, only_pending: bool = True) -> list[dict]:
        with self._lock:
            out = [dict(r) for r in self._pending]
        if only_pending:
            out = [r for r in out if r.get("status") == "pending"]
        return out

    def get_pending(self, pending_id: str) -> Optional[dict]:
        with self._lock:
            for rec in self._pending:
                if rec.get("id") == pending_id:
                    return dict(rec)
        return None

    def clear_pending(self) -> int:
        """清空队列（测试用）。"""
        with self._lock:
            n = len(self._pending)
            self._pending = []
            self._save_pending()
            return n

    # ── 自省 ────────────────────────────────────────────────────
    def describe(self) -> dict:
        cfg = self._config
        return {
            "config_source": self.config_source,
            "config_path": str(self.config_path),
            "pending_path": str(self.pending_path),
            "version": cfg.get("version", 0),
            "default_by_op": cfg.get("default_by_op") or {},
            "actor_overrides": cfg.get("actor_overrides") or {},
            "rules": cfg.get("rules") or [],
            "pending_count": len(self.list_pending(only_pending=True)),
            "human_actors": sorted(HUMAN_ACTORS),
        }


# 模块级单例（与 audit.py 的 audit_log 同约定）
policy_engine = PolicyEngine()


# ── 审计与工具层便捷入口 ──────────────────────────────────────────
def _audit(ev: Evaluation, pending: Optional[dict] = None) -> None:
    """把判定写进审计（失败不阻塞，沿用 audit.py 原则）。"""
    try:
        from .audit import audit_log
        detail = f"op={ev.op} decision={ev.decision} rule={ev.rule_id}"
        if pending:
            detail += f" pending={pending['id']}"
        if ev.note:
            detail += f" note={ev.note}"
        audit_log.record(
            event="policy.decision",
            actor=ev.actor,
            target=(ev.target or "")[:200],
            detail=detail,
        )
    except Exception as e:
        print(f"[policy] 记审计失败（忽略）: {e}", flush=True)


def check(
    op: str,
    *,
    target: str = "",
    actor: str = ACTOR_PLATFORM_AI,
    detail: Optional[dict] = None,
) -> tuple[Evaluation, Optional[dict]]:
    """工具层/路由层便捷入口：**判定 → ASK 自动入队 → 记审计**，一次做完。

    返回 ``(Evaluation, pending_record_or_None)``。调用方按 ``ev.decision`` 分支：

    - ``ev.allowed``       → 继续执行
    - ``ev.blocked``       → 拒绝，把 ``ev.note``/``ev.rule_id`` 回给调用方
    - ``ev.needs_approval``→ **不要执行**，把 ``pending['id']`` 回给调用方等人批

    统一走这里是为了让「每个接入点各写一遍判定+入队+审计」的重复与遗漏都消失。
    """
    ev = policy_engine.evaluate(op, actor=actor, target=target, detail=detail)
    pending = None
    if ev.decision == ASK:
        pending = policy_engine.request(
            op, actor=actor, target=target, detail=detail,
            reason=ev.note or f"{op} 需人工审批",
        )
    _audit(ev, pending)
    return ev, pending


def blocked_message(ev: Evaluation) -> str:
    """DENY 时给调用方的统一文案。"""
    why = ev.note or ev.rule_id or "策略拒绝"
    return f"已被安全策略拒绝（{why}）。如需执行，请由人工在本地终端处理。"


def pending_message(ev: Evaluation, pending: dict) -> str:
    """ASK 时给调用方的统一文案（含待办 id，便于前端/人跟进）。"""
    return (
        f"该操作需人工审批后才能执行（{ev.note or ev.op}）。"
        f"待办 id: {pending['id']}——请在平台「待审批」中处理后重试。"
    )


def actor_from_request(request) -> str:
    """路由层 actor 推断（尽力而为，用于**审计**与「是否吃 operator override」）。

    - 带 `X-Agent-Token` 头 → ``harness``（外端 AI / harness 在调）
    - 否则 → ``operator``（本机前端 / 人工）

    ⚠️ **这是保守推断，不是强认证**：真正的身份校验在 `identity.check_request_token`。
    第一片只做到「有 token 头就当 AI 看」；本地单机场景下，前端一键注册不带 token，
    若默认成更严的 actor 会把 V-12 的一键流程堵死，故无 token 时判为 operator。
    该局限已记入设计稿 §九 实施记录（第二片接强身份）。
    """
    try:
        for key in getattr(request, "headers", {}) or {}:
            if str(key).lower() == "x-agent-token":
                if request.headers.get(key):
                    return ACTOR_HARNESS
    except Exception:
        pass
    return ACTOR_OPERATOR
