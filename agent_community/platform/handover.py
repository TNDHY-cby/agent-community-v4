# -*- coding: utf-8 -*-
"""platform/handover：交接体系的「证据闸门」（2026-10-07）。

设计来源 = **项目自有文档，不是我发明的**：
  《防幻觉纪律与修复方案_20261007.md》§四 把「任务 1 交接体系」拆成 4 件配套物，
  本模块落地其中两件：
    ① 任务书/回执模板加「证据栏」——每项完成声明必须附 commit hash / 文件路径 / 测试输出，
       **无证据不回执**；
    ③ 送达回执协议——消息必须等对方**可读回复**才可宣称送达。

本模块只做三件事（纯规则、零 token、失败不炸）：
  1. 生成带「证据栏」的**任务书模板**与**回执模板**；
  2. **校验回执**：每条完成声明至少带一种证据（commit / file / test），否则拒绝并逐条报错；
  3. **送达判定**：`delivered=True` 时必须给 `reply_ref`（对方可读回复的凭据），否则拒绝。

不做：不改任何既有端点；不新建数据库（回执落 `DATA_DIR/handover_receipts.json`，ring 上限 500）。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

# 证据类型：一条声明至少要带其中一种
EVIDENCE_KEYS = ("commit", "file", "test")
# 「证据栏」的人类可读说明（进模板，也进错误消息）
EVIDENCE_HINT = {
    "commit": "commit 号（如 76964df）",
    "file": "文件路径（相对仓库根）",
    "test": "测试输出摘要（命令 + 计数）",
}
MAX_RECEIPTS = 500

_DEFAULT_DATA_DIR = Path(os.environ.get("AC_DATA_DIR") or (Path(__file__).resolve().parent.parent / "data"))


def template_task_order(task_id: str = "", title: str = "", items: Optional[list] = None) -> dict:
    """任务书模板。**每项都带空的证据栏**——领取方必须逐项补齐才能回执。"""
    items = items or [{"claim": "", "evidence": {k: "" for k in EVIDENCE_KEYS}}]
    return {
        "kind": "task_order",
        "task_id": task_id,
        "title": title,
        "created_at": time.time(),
        "items": [
            {
                "n": i + 1,
                "claim": (it.get("claim") or "") if isinstance(it, dict) else str(it),
                "evidence": {
                    k: ((it.get("evidence") or {}).get(k) or "") if isinstance(it, dict) else ""
                    for k in EVIDENCE_KEYS
                },
            }
            for i, it in enumerate(items)
        ],
        "evidence_hint": EVIDENCE_HINT,
        "rule": "每项完成声明至少带一种证据（commit/file/test）；无证据的回执一律拒收。",
    }


def template_receipt(task_id: str = "", who: str = "", items: Optional[list] = None,
                     title: str = "") -> dict:
    """回执模板（与任务书同构，另加送达段）。

    ⚠️ `title` 必须**透传**：2026-10-07 MiMo 实测 `?kind=receipt&title=X` 回显空串
    （task_order 正常），就是路由层漏传这个参数导致的。
    """
    out = template_task_order(task_id=task_id, title=title, items=items)
    out["kind"] = "receipt"
    out["who"] = who
    out["delivery"] = {"delivered": False, "reply_ref": "", "note": ""}
    out["delivery_rule"] = "delivered=True 时必须给 reply_ref（对方可读回复的凭据），否则视为未送达。"
    return out


def _nonempty(v: Any) -> bool:
    return bool(str(v or "").strip())


def validate_receipt(payload: dict) -> tuple[bool, list[str], dict]:
    """校验一份回执。返回 `(ok, errors, normalized)`。

    规则（对应上面 ①③ 两条）：
      - `items` 不能为空；每项必须有 `claim`；
      - 每项**至少**带一种非空证据（commit / file / test）——**这是「无证据不回执」的落点**；
      - `delivery.delivered=True` 时**必须**给 `delivery.reply_ref`。
    本函数**不抛异常**（失败不炸），只返回错误列表。
    """
    errors: list[str] = []
    payload = payload if isinstance(payload, dict) else {}
    task_id = str(payload.get("task_id") or "").strip()
    who = str(payload.get("who") or payload.get("from") or "").strip()
    if not task_id:
        errors.append("缺少 task_id")

    items_in = payload.get("items")
    if not isinstance(items_in, list) or not items_in:
        errors.append("items 不能为空（回执必须逐项对应任务书条目）")
        items_in = []

    items: list[dict] = []
    for i, it in enumerate(items_in, 1):
        it = it if isinstance(it, dict) else {}
        claim = str(it.get("claim") or "").strip()
        ev_in = it.get("evidence") if isinstance(it.get("evidence"), dict) else {}
        ev = {k: str(ev_in.get(k) or "").strip() for k in EVIDENCE_KEYS}
        if not claim:
            errors.append(f"第 {i} 项缺少 claim（完成声明）")
        if not any(ev.values()):
            errors.append(
                f"第 {i} 项无证据：至少给一种（{ '/'.join(EVIDENCE_KEYS) }）"
                f"——{EVIDENCE_HINT['commit']}／{EVIDENCE_HINT['file']}／{EVIDENCE_HINT['test']}"
            )
        items.append({"n": i, "claim": claim, "evidence": ev})

    dl_in = payload.get("delivery") if isinstance(payload.get("delivery"), dict) else {}
    delivered = bool(dl_in.get("delivered"))
    reply_ref = str(dl_in.get("reply_ref") or "").strip()
    if delivered and not reply_ref:
        errors.append("delivered=True 但缺 reply_ref：送达必须有「对方可读回复」的凭据，否则视为未送达")

    normalized = {
        "task_id": task_id,
        "who": who,
        "items": items,
        "delivered": delivered,
        "reply_ref": reply_ref,
        "note": str(dl_in.get("note") or "").strip(),
    }
    # ⚠️ 2026-10-07 MiMo 实测报回：存入/查列表的是**拍平结构**（delivered/reply_ref 在顶层），
    #    与**提交体的嵌套 `delivery`** 不一致 —— 两边形状不同迟早咬人。
    #    处理：两条形状**都给**（顶层拍平保留，另附同值的嵌套 `delivery`）。
    #    这样已按拍平渲染的客户端不受影响，新客户端可以照提交体的形状读。
    normalized["delivery"] = {
        "delivered": normalized["delivered"],
        "reply_ref": normalized["reply_ref"],
        "note": normalized["note"],
    }
    return (not errors), errors, normalized


class HandoverLedger:
    """回执台账（ring buffer + 惰性落盘，与 audit.py 同原则：失败不阻塞业务）。"""

    def __init__(self, data_dir: Path = None, max_records: int = MAX_RECEIPTS):
        self.data_dir = Path(data_dir or _DEFAULT_DATA_DIR)
        self.max_records = max_records
        self.records: list[dict] = []
        self._path = self.data_dir / "handover_receipts.json"
        self._load()

    def _load(self) -> None:
        try:
            if self._path.exists():
                d = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(d, list):
                    self.records = d[-self.max_records:]
        except Exception as e:  # noqa: BLE001
            print(f"[handover] 载入回执失败（忽略）: {e}", flush=True)

    def _save(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self.records[-self.max_records:], ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except Exception as e:  # noqa: BLE001
            print(f"[handover] 写回执失败（忽略）: {e}", flush=True)

    def add(self, normalized: dict) -> dict:
        rec = {"ts": time.time(), "accepted": True, **normalized}
        self.records.append(rec)
        if len(self.records) > self.max_records:
            self.records = self.records[-self.max_records:]
        self._save()
        return rec

    def query(self, limit: int = 100, task_id: Optional[str] = None) -> list[dict]:
        out = self.records
        if task_id:
            out = [r for r in out if r.get("task_id") == task_id]
        return out[-limit:][::-1]

    def reset(self) -> None:
        self.records.clear()


handover_ledger = HandoverLedger()


# ══════════════════════════════════════════════════════════════════════
# V-24 上下文老化 + 交接单（老板 D7 / 43 号「交接体系」后半）
#
# 借鉴来源 —— 《竞品雷达巡检_第2期_20261007.md》里点出的对象名：
#   · OpenAI Agents SDK v0.23.0：**memory consolidation turns**（记忆整合轮次）
#     ＋ **durable checkpointing**（持久化检查点，已转 GA）
#   · Google ADK v2.9.0：5 个 breaking change 之一就是 **resume 时「工作流节点重跑」**
#     —— 说明"恢复后哪些步骤重跑"必须显式声明，否则恢复语义不可靠
#   · CrewAI v1.13.0：**RuntimeState 统一状态序列化**（整套运行态可序列化带走）
#   · MS Agent Framework 1.0：会话管理 + 标准 agent 抽象（术语口径）
#
# 落成一条硬闸门（老板 D7 原话：同一 agent 3~4 次上下文压缩后新建会话，需交接单）：
#   压缩计数到阈值 → `can_open_new_session()` 拒绝 → 必须先落一份 checkpoint（交接单）
#   并显式写明 **resume 语义**（哪些已完成不重跑 / 哪些要重跑）→ 才准开新会话。
# ══════════════════════════════════════════════════════════════════════

COMPACT_THRESHOLD = 3          # 老板原话 3~4 次；取 3 为下限，超过即必须交接
CHECKPOINT_REQUIRED = (
    "agent_id", "wake_word", "workspace_dir", "plan", "resume_semantics",
)
CHECKPOINT_REQUIRED_HINT = {
    "agent_id": "哪个 agent（harness_id 或角色名）",
    "wake_word": "唤醒词（新会话用它接续）",
    "workspace_dir": "文件夹路径（工作区真实绝对路径）",
    "plan": "工作安排（这一轮要做什么）",
    "resume_semantics": "恢复语义：哪些已完成不重跑、哪些必须重跑（借 Google ADK 的 resume 教训）",
}


class AgingTracker:
    """上下文老化计数 + 开新会话闸门（纯内存 + 惰性落盘，失败不炸）。"""

    def __init__(self, threshold: int = COMPACT_THRESHOLD, data_dir: Optional[Path] = None):
        self.threshold = int(threshold)
        self.data_dir = Path(data_dir or _DEFAULT_DATA_DIR)
        self.state: dict[str, dict] = {}          # session_key -> {"compactions","last_ts","checkpoints"}
        self._path = self.data_dir / "aging_state.json"
        self._load()

    def _load(self) -> None:
        try:
            if self._path.exists():
                d = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(d, dict):
                    self.state = d
        except Exception as e:  # noqa: BLE001
            print(f"[handover] 载入老化状态失败（忽略）: {e}", flush=True)

    def _save(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            print(f"[handover] 写老化状态失败（忽略）: {e}", flush=True)

    def _rec(self, key: str) -> dict:
        return self.state.setdefault(
            key, {"compactions": 0, "last_ts": 0.0, "checkpoints": 0, "budget_spent": 0}
        )

    def note_compaction(self, session_key: str) -> dict:
        """记一次上下文压缩。返回计数与「是否必须交接」。"""
        key = str(session_key or "").strip()
        if not key:
            return {"ok": False, "error": "缺少 session_key"}
        r = self._rec(key)
        r["compactions"] = int(r.get("compactions", 0)) + 1
        r["last_ts"] = time.time()
        self._save()
        return {
            "ok": True, "session_key": key, "compactions": r["compactions"],
            "threshold": self.threshold,
            "needs_checkpoint": r["compactions"] >= self.threshold,
            "note": ("已达阈值：开新会话前必须先交一份 checkpoint（交接单）"
                     if r["compactions"] >= self.threshold else "未到阈值"),
        }

    def can_open_new_session(self, session_key: str) -> tuple[bool, str, dict]:
        """**开新会话闸门**。未到阈值 → 放行；到阈值 → 必须已有交接单。"""
        key = str(session_key or "").strip()
        r = self._rec(key)
        n = int(r.get("compactions", 0))
        cps = int(r.get("checkpoints", 0))
        if n < self.threshold:
            return True, f"压缩 {n}/{self.threshold} 次，未到阈值", r
        if cps > 0:
            return True, f"压缩 {n} 次但已交 {cps} 份交接单，放行", r
        return False, (f"压缩 {n} 次（阈值 {self.threshold}）且**未交交接单**："
                       f"必须先 POST /api/handover/checkpoint"), r

    def note_checkpoint(self, session_key: str) -> dict:
        """记一次交接单提交（计数归零，开启新一轮）。"""
        key = str(session_key or "").strip()
        if not key:
            return {"ok": False, "error": "缺少 session_key"}
        r = self._rec(key)
        r["checkpoints"] = int(r.get("checkpoints", 0)) + 1
        r["compactions"] = 0
        r["last_ts"] = time.time()
        self._save()
        return {"ok": True, "session_key": key, "checkpoints": r["checkpoints"], "compactions": 0}

    def reset(self) -> None:
        self.state.clear()


def validate_checkpoint(payload: dict) -> tuple[bool, list[str], dict]:
    """校验一份交接单（checkpoint）。规则来自老板 D7 的字段清单，外加 resume 语义。"""
    errors: list[str] = []
    payload = payload if isinstance(payload, dict) else {}
    norm: dict = {}
    for k in CHECKPOINT_REQUIRED:
        v = str(payload.get(k) or "").strip()
        norm[k] = v
        if not v:
            errors.append(f"缺少 {k}（{CHECKPOINT_REQUIRED_HINT[k]}）")
    done = payload.get("done") if isinstance(payload.get("done"), list) else []
    todo = payload.get("todo") if isinstance(payload.get("todo"), list) else []
    norm["done"] = [str(x) for x in done if str(x).strip()]
    norm["todo"] = [str(x) for x in todo if str(x).strip()]
    if not norm["done"] and not norm["todo"]:
        errors.append("done 与 todo 不能同时为空（交接单必须说清进度）")
    norm["session_key"] = str(payload.get("session_key") or norm.get("agent_id") or "").strip()
    norm["ts"] = time.time()
    return (not errors), errors, norm


aging_tracker = AgingTracker()


def build_resume_prompt(cp: dict) -> str:
    """由交接单拼出**新会话的续作提示词**（D7 执行端）。

    只在这里拼一次：`_activation_prompt` / 组长路径 / 本函数是本项目里第三处"给外端 agent 的
    提示词文案"，分别内联必然漂移（本项目的老毛病），故一律走函数。
    """
    cp = cp or {}
    done = cp.get("done") or []
    todo = cp.get("todo") or []
    lines = [
        "【续作：你接的是上一会话的班】",
        f"唤醒词：{cp.get('wake_word') or '（未提供）'}",
        f"你的工作目录（真实路径）：{cp.get('workspace_dir') or '（未提供）'}",
        f"本轮工作安排：{cp.get('plan') or '（未提供）'}",
        "",
        "已完成（**不要重跑**）：" + ("；".join(done) if done else "（无）"),
        "待办（**按顺序继续**）：" + ("；".join(todo) if todo else "（无）"),
        "",
        f"恢复语义：{cp.get('resume_semantics') or '（未提供）'}",
        "",
        "要求：先按上面的工作目录读取实际文件状态核对一遍，再继续；"
        "**不要重跑「已完成」里的东西**；有任何与交接单不符的地方，先报告再动手。",
    ]
    return "\n".join(lines)
