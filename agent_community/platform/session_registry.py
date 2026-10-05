# -*- coding: utf-8 -*-
"""platform/session_registry：会话注册表（V-15 步骤1）。

设计稿：design-docs/V15_工作间会话联动与三层桥设计.md §12

**它解决什么**：现状「工作间 ↔ 会话」的关联事实只存在于桥进程内存
（`acp_harness_bridge.py` 的 `_sessions`），桥一重启全丢 —— 负责人所说的"失忆"。
本模块把它变成**平台侧的一等公民**：登记、查询、状态流转、独立落盘。

三条纪律（全部来自本项目已踩过的坑）：

1. **就地变更，绝不重新绑定** —— `_records` 是模块级单例的成员，
   所有读方持有的是同一个 dict；`self._records = []` 会让旧引用指向空对象
   （同 `state.py` 的双副本教训、`policy.py` 的浅拷贝教训）。

2. **独立落盘 `data/sessions.json`** —— 不与用户可编辑的 `policy.json` 混存。
   混存会被读-改-写覆盖用户的编辑（同「harnesses.json 被写空」那类事故）。

3. **失败不阻塞** —— 读/写盘异常只打印并继续，绝不让注册表把业务流程搞崩
   （同 `audit.py` 的原则）。

**会话身份**：key = `(harness_id, session_id)`。两个 harness 理论上可能返回同一个
session_id，故不能只用 session_id 作 key。
**人工进入**（§11.1 方案）没有 session_id —— 此时**合成**一个：
`human:{workshop}:{member}:{ts}`，并在 `source` 标 `human`，
**绝不冒充机器报的会话**（验证矩阵 #19）。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Optional

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# 会话来源（与唤醒档位对应，见 V-15 §六）
SRC_ACP = "acp"          # 桥协议层建
SRC_MCP = "mcp"          # MCP 工具层补偿（B 档，静默发现）
SRC_PLUGIN = "plugin"    # 插件市场插出的协议（D 档）
SRC_HUMAN = "human"      # 人工点击进入（§11.1）
SRC_UNKNOWN = "unknown"

# 会话状态
ST_ACTIVE = "active"                 # 已进入、可用
ST_IDLE = "idle"                     # 工作间结束，保留待复用（拍板 #4）
ST_CLOSED = "closed"                 # 已发起关闭
ST_CLOSE_REQ = "close_requested"     # 已请求关闭，等 harness 自述确认
ST_STALE = "stale"                   # 长时间无回报（只影响可见性，不自动关）

SOURCES = (SRC_ACP, SRC_MCP, SRC_PLUGIN, SRC_HUMAN, SRC_UNKNOWN)
STATUSES = (ST_ACTIVE, ST_IDLE, ST_CLOSED, ST_CLOSE_REQ, ST_STALE)

# ── V-17 关闭兜底：宽限期（秒）────────────────────────────────────
# `close_requested` 是**两段式关闭**的中间态（等 harness 自述确认）。此前没有出口：
# 对面不来消息就永远停在这里（实测 18 个会话里 17 个卡住）。
#
# 取值依据（锚定平台既有常量，不拍脑袋）：
#   waker_protocol.WAKER_TIMEOUT       = 30s   单次唤醒投递
#   harness_adapter.heartbeat_timeout  = 60s   心跳超时 → **判 harness 离线**
# 判据：平台已认定"60s 无心跳 = 离线"，那么一个连心跳都没有的 harness，再给
# **10 倍心跳窗（600s）** 仍不自述关闭，就不该继续占着"等待中"的名分。
# 反过来 600s 远大于任何单次调用上限（AI 120s / MCP 120s），足够覆盖一次收尾。
# 可用配置键 `session_close_grace_seconds` 覆盖。
CLOSE_GRACE_SECONDS = 600.0

# 兜底关闭的原因标记（与"harness 自述关闭"区分，便于审计统计）
CLOSE_REASON_GRACE = "grace_expired"
CLOSE_REASON_SELF = "self_reported"


def _now() -> float:
    return time.time()


class SessionRegistry:
    """会话注册表。线程安全、独立落盘、失败不阻塞。"""

    def __init__(self, data_dir: Path = DEFAULT_DATA_DIR, autoload: bool = True):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "sessions.json"
        self._lock = threading.RLock()
        self._records: list[dict] = []
        if autoload:
            self._load()

    # ── 落盘 ────────────────────────────────────────────────
    def _load(self) -> None:
        try:
            import json
            if self.path.exists():
                # ⚠️ 必须用 **utf-8-sig** 而不是 utf-8：
                # PowerShell 的 `Set-Content -Encoding UTF8` 会写入 **UTF-8 BOM**，
                # 而 `read_text(encoding="utf-8")` 不剥 BOM -> json.loads 得到
                # "\ufeff[...]" -> **直接抛** -> 被 except 吞掉 -> **静默回落空表**。
                # 文件里**一旦真的有会话数据，重启就等于全丢**，且只留一行日志。
                # utf-8-sig 对「有 BOM」和「无 BOM」两种输入都正确。
                data = json.loads(self.path.read_text(encoding="utf-8-sig"))
                if isinstance(data, list):
                    self._records = [r for r in data if isinstance(r, dict)]
        except Exception as e:
            print(f"[session_registry] 读 sessions.json 失败（忽略）: {e}", flush=True)

    def _save(self) -> None:
        try:
            import json
            self.data_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._records, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.path)          # 原子替换，防半截 JSON
        except Exception as e:
            print(f"[session_registry] 写 sessions.json 失败（忽略）: {e}", flush=True)

    def _persist(self) -> None:
        """统一持久化入口 —— 纪律3：**任何**持久化异常都吞掉，绝不冒泡。

        `_save()` 内部已有 try/except 覆盖 IO 错误；本方法再兜一层，
        覆盖「`_save` 本身异常」（如实现被替换、序列化器故障等）。
        少了这一层，`register()` 会在写盘失败时把异常抛给上游，
        与本模块「失败不阻塞业务」的纪律直接冲突。
        """
        try:
            self._save()
        except Exception as e:
            print(f"[session_registry] 持久化失败（忽略，内存登记仍有效）: {e}", flush=True)

    # ── 身份 ────────────────────────────────────────────────
    @staticmethod
    def _key(harness_id: str, session_id: str) -> str:
        return f"{harness_id}::{session_id}"

    @staticmethod
    def _ensure_session_id(
        session_id: str, workshop_id: str, member_id: str, source: str
    ) -> str:
        """人工进入没有 session_id → 合成一个，但标 source 以示区别。

        **同秒重复点击 = 合并为同一会话**：合成 id 带 `int(_now())`（秒级），
        同一 (workshop, member) 在同一秒内点两次，会合成**同一个 id** →
        `register()` 走幂等更新分支，**不会产生两条记录**。这是有意的：
        重复点击本就是同一个逻辑会话，不该裂开。

        跨秒再点则是**另一条**记录（见 `find()` 按 last_seen 降序，
        复用时取最新那条）。
        """
        if session_id:
            return session_id
        return f"human:{workshop_id or '-'}:{member_id or '-'}:{int(_now())}"

    # ── 登记 / 更新 ─────────────────────────────────────────
    def register(
        self,
        *,
        harness_id: str,
        session_id: str = "",
        workshop_id: str = "",
        member_id: str = "",
        role: str = "",
        workspace_dir: str = "",
        source: str = SRC_UNKNOWN,
        context_turns: Optional[int] = None,
        status: str = ST_ACTIVE,
    ) -> dict:
        """登记（或更新）一个会话。返回该记录的副本。

        幂等：同 `(harness_id, session_id)` 再次登记 = 更新（如工作间变更、
        context_turns 增长），不会产生重复行。
        """
        if not harness_id:
            return {"error": "harness_id 必填"}
        sid = self._ensure_session_id(session_id, workshop_id, member_id, source)
        now = _now()
        with self._lock:
            key = self._key(harness_id, sid)
            for r in self._records:
                if self._key(r.get("harness_id", ""), r.get("session_id", "")) == key:
                    # 更新：只覆盖传入的字段，保留原始 created_at
                    if workshop_id:
                        r["workshop_id"] = workshop_id
                    if member_id:
                        r["member_id"] = member_id
                    if role:
                        r["role"] = role
                    if workspace_dir:
                        r["workspace_dir"] = workspace_dir
                    if source and source != SRC_UNKNOWN:
                        r["source"] = source
                    if context_turns is not None:
                        r["context_turns"] = context_turns
                    r["last_seen"] = now
                    if status:
                        r["status"] = status
                    self._persist()
                    return dict(r)
            rec = {
                "harness_id": harness_id,
                "session_id": sid,
                "workshop_id": workshop_id,
                "member_id": member_id,
                "role": role,
                "workspace_dir": workspace_dir,
                "source": source,
                "status": status,
                "context_turns": context_turns,
                "created_at": now,
                "last_seen": now,
                "close_requested_at": None,
            }
            self._records.append(rec)
            self._persist()
            return dict(rec)

    def touch(self, harness_id: str, session_id: str) -> bool:
        """活跃心跳：只更新 last_seen。"""
        if not session_id:
            return False
        with self._lock:
            for r in self._records:
                if r.get("harness_id") == harness_id and r.get("session_id") == session_id:
                    r["last_seen"] = _now()
                    self._persist()
                    return True
        return False

    def set_status(
        self, harness_id: str, session_id: str, status: str, note: str = ""
    ) -> Optional[dict]:
        """状态流转。只接受已知状态（打字错误不会静默创建新状态）。"""
        if status not in STATUSES:
            print(f"[session_registry] 未知状态 {status!r}，忽略", flush=True)
            return None
        with self._lock:
            for r in self._records:
                if r.get("harness_id") == harness_id and r.get("session_id") == session_id:
                    r["status"] = status
                    r["last_seen"] = _now()
                    if status in (ST_CLOSED, ST_CLOSE_REQ):
                        r["close_requested_at"] = _now()
                    if status == ST_CLOSED:
                        # V-17：区分"对面自述关闭"与"平台兜底关闭"（审计可统计比例）
                        r["close_reason"] = CLOSE_REASON_SELF
                        r["closed_at"] = _now()
                    if note:
                        r["note"] = note
                    self._persist()
                    return dict(r)
        return None

    # ── 查询 ────────────────────────────────────────────────
    def get(self, harness_id: str, session_id: str) -> Optional[dict]:
        if not session_id:
            return None
        with self._lock:
            for r in self._records:
                if r.get("harness_id") == harness_id and r.get("session_id") == session_id:
                    return dict(r)
        return None

    def find(self, workshop_id: str, member_id: str) -> list[dict]:
        """按 (workshop, member) 找会话 —— 复用判定用（拍板 #4）。

        **按 last_seen 降序**（最新的在前）：人工进入跨秒重复点击会留下多条记录，
        调用方直接取 `[0]` 即为"当前那条"，语义确定、不依赖遍历顺序。
        """
        with self._lock:
            out = [
                dict(r)
                for r in self._records
                if r.get("workshop_id") == workshop_id
                and r.get("member_id") == member_id
                and r.get("status") != ST_CLOSED
            ]
        out.sort(key=lambda r: r.get("last_seen") or 0, reverse=True)
        return out

    def list(
        self,
        *,
        harness_id: str = "",
        workshop_id: str = "",
        status: str = "",
        only_open: bool = False,
    ) -> list[dict]:
        with self._lock:
            out = [dict(r) for r in self._records]
        if harness_id:
            out = [r for r in out if r.get("harness_id") == harness_id]
        if workshop_id:
            out = [r for r in out if r.get("workshop_id") == workshop_id]
        if status:
            out = [r for r in out if r.get("status") == status]
        if only_open:
            out = [r for r in out if r.get("status") not in (ST_CLOSED,)]
        # 最近活动的排前面
        out.sort(key=lambda r: r.get("last_seen") or 0, reverse=True)
        return out

    def counts(self) -> dict:
        with self._lock:
            c = {s: 0 for s in STATUSES}
            c["total"] = len(self._records)
            for r in self._records:
                s = r.get("status")
                if s in c:
                    c[s] += 1
            return c

    # ── 生命周期（对齐 V-15 §12.2）─────────────────────────
    def on_workshop_ended(self, workshop_id: str) -> int:
        """工作间结束 → **保留待复用**（拍板 #4），标 idle，不关。"""
        n = 0
        with self._lock:
            for r in self._records:
                if r.get("workshop_id") == workshop_id and r.get("status") == ST_ACTIVE:
                    r["status"] = ST_IDLE
                    r["last_seen"] = _now()
                    n += 1
            if n:
                self._persist()
        return n

    def on_workshop_deleted(self, workshop_id: str) -> list[dict]:
        """工作间删除 → 工作区进回收站、cwd 失效 → **发起 close**（§九 + §12.2）。"""
        changed = []
        with self._lock:
            for r in self._records:
                if r.get("workshop_id") == workshop_id and r.get("status") not in (
                    ST_CLOSED,
                ):
                    r["status"] = ST_CLOSE_REQ
                    r["close_requested_at"] = _now()
                    changed.append(dict(r))
            if changed:
                self._persist()
        return changed

    def on_harness_unregistered(self, harness_id: str) -> int:
        """注销 harness → 宿主没了，会话全部移除（先标 closed 再移除，留审计轨迹）。"""
        with self._lock:
            before = len(self._records)
            for r in self._records:
                if r.get("harness_id") == harness_id:
                    r["status"] = ST_CLOSED
                    r["close_requested_at"] = _now()
            self._records = [r for r in self._records if r.get("harness_id") != harness_id]
            removed = before - len(self._records)
            if removed:
                self._persist()
            return removed

    def sweep_expired_close_requests(
        self, now: float | None = None, grace_seconds: float | None = None
    ) -> list[dict]:
        """惰性收敛：把超过宽限期的 `close_requested` 置为 `closed`（V-17）。

        设计稿：`design-docs/V17_会话关闭兜底设计.md`

        **纯规则、事件触发**：不启线程、不注册定时器、不发网络、不调 LLM。
        调用方只在"资源被访问 / 回收"的时机调它（`GET /api/sessions`、
        工作间删除·stale 回收、harness 注销前）。

        语义（逐条对应设计稿 §4）：
        - 只处理 `close_requested`；`closed` / `active` / `idle` / `stale` **一律不碰**
          （`idle` 是拍板 #4 的"保留待复用"，不得被收敛）；
        - `close_requested_at + grace < now` → `closed` + `closed_at` + `close_reason`；
        - **记录保留、不删除**：晚到的 harness 自述仍能更新它，也留住审计轨迹；
        - 老记录缺 `close_requested_at`（V-17 之前落盘的）→ **补记当前时间**，
          宽限期从此刻起算，**不立即关闭** —— 避免上线瞬间把历史数据成批误清。

        返回**本次真正被关闭**的记录（补记起算点的不算关闭，不返回）。
        """
        if now is None:
            now = _now()
        if grace_seconds is None:
            grace_seconds = _close_grace_seconds()

        closed: list[dict] = []
        backfilled = False
        with self._lock:
            for r in self._records:
                if r.get("status") != ST_CLOSE_REQ:
                    continue
                started = r.get("close_requested_at")
                if not started:
                    r["close_requested_at"] = now
                    backfilled = True
                    continue
                if now - started < grace_seconds:
                    continue
                r["status"] = ST_CLOSED
                r["closed_at"] = now
                r["close_reason"] = CLOSE_REASON_GRACE
                closed.append(dict(r))
            if closed or backfilled:
                self._persist()
        return closed

    def clear(self) -> int:
        """清空（测试用）。就地清空，不重新绑定。"""
        with self._lock:
            n = len(self._records)
            self._records.clear()          # ← 必须 clear，不能 = []（旧引用会失效）
            self._persist()
            return n

    # ── 自省 ────────────────────────────────────────────────
    def describe(self) -> dict:
        return {
            "path": str(self.path),
            "counts": self.counts(),
            "sources": list(SOURCES),
            "statuses": list(STATUSES),
        }


# 模块级单例（同 audit.py / policy.py 约定）
session_registry = SessionRegistry()


def _close_grace_seconds() -> float:
    """读配置里的关闭宽限期；缺失 / 非法 / 读取失败都回退常量（失败不阻断）。"""
    try:
        from ..config import load_config

        raw = load_config().get("session_close_grace_seconds")
        val = float(raw)
        if val > 0:
            return val
    except Exception:
        pass
    return CLOSE_GRACE_SECONDS


def sweep_and_audit(
    now: float | None = None, grace_seconds: float | None = None
) -> list[dict]:
    """惰性收敛 + 审计 —— **三个调用点共用这一个入口**，避免三处逻辑漂移。

    V-17 设计稿 §3.3 的三个触发时机（都用本函数）：
    1. `GET /api/sessions`：人来看的那一刻必须是真相，而不是陈旧等待态；
    2. 工作间删除 / stale 回收：回收时机天然适合收敛上一批遗留；
    3. harness 注销前：宿主消失前先把到期项标掉，审计更完整。

    **绝不抛异常**：收敛或审计失败都只打印并继续 —— 它挂在可见性查询这类
    只读链路上，绝不能因为兜底逻辑把主流程搞崩（同 audit.py 的纪律）。
    """
    try:
        closed = session_registry.sweep_expired_close_requests(
            now=now, grace_seconds=grace_seconds
        )
    except Exception as e:  # noqa: BLE001
        print(f"[session_registry] 关闭兜底收敛失败（不阻断）: {e}", flush=True)
        return []

    for rec in closed:
        try:
            from .audit import audit_log

            waited = ""
            started = rec.get("close_requested_at")
            if started and rec.get("closed_at"):
                waited = f" 等待 {rec['closed_at'] - started:.0f}s"
            audit_log.record(
                "session.close",
                actor="system",
                target=str(rec.get("harness_id") or ""),
                detail=(
                    f"宽限期到期（harness 未自述），强制置 closed；"
                    f"session_id={rec.get('session_id')} workshop_id={rec.get('workshop_id')}"
                    f"{waited}"
                ),
            )
        except Exception:  # noqa: BLE001
            pass
    return closed
