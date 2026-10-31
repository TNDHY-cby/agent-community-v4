"""
A2A 暴露层 — 将 agent_community-v4 平台暴露为 A2A（Agent-to-Agent）服务端。

设计目标：
1. 零第三方依赖（仅标准库），实现 A2A 协议服务端：Agent Card 发现 + JSON-RPC 任务/消息。
2. 最小侵入：不改 server.py 主线，通过 HTTP 调用平台 REST API（默认 127.0.0.1:18920）。
3. 价值：外部 A2A 生态 Agent（Google ADK、LangGraph、微软 AutoGen 等）可通过标准协议
   发现本平台并派发任务；平台内部仍走原有三级讨论/共识链路。

启动（在项目根目录，即 agent_community 包的父目录）：
    python -m agent_community.a2a_server            # 默认 127.0.0.1:9104（回环；非回环需鉴权就绪）
    python -m agent_community.a2a_server --port 9105

⚠️ 与 mcp_server.py 同理，本模块刻意放在 `agent_community/` 顶层而非 `platform/`：
   `platform/__init__.py` 会 `from .server import app, ...`，把整个 FastAPI 服务拽进来
   （并污染 stdout），而本层同样宣称「零第三方依赖」。

发现（供外部 Agent 注册）：
    GET http://<host>:9104/.well-known/agent-card.json   # A2A v1.0 标准路径
    GET http://<host>:9104/.well-known/agent.json         # v0.2 兼容路径

环境变量：
    AC4_BASE_URL  平台 REST 基址，默认 http://127.0.0.1:18920
    AC4_TOKEN     平台访问 Token（本地放行时可省略）
"""

from __future__ import annotations

from .platform_address import base_url_from_env
import argparse
import codecs
import json
import os
import re
import subprocess
import sys
import threading
import urllib.request
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_URL = base_url_from_env().rstrip("/")
TOKEN = os.environ.get("AC4_TOKEN", "")

# V18/候选C 边缘鉴权：复用平台 `identity`（**不造新轮**，31 号要求）。
# ⚠️ 2026-10-06 起 `platform/__init__` 已改惰性导出，此处导入 identity 不会拽入 FastAPI。
from .platform import identity as _identity  # noqa: E402
# A2A 接力插件（示例Harness-C 注入）：自动回执 + 回复落盘 A2A_inbox 信箱
from . import a2a_relay as _a2a_relay  # noqa: E402

# 入站请求体上限：镜像平台 `server.py` 的 `MAX_BODY_BYTES`（1MB）——
# 鉴权之前也要限长，**未认证方不能无限塞**。
_MAX_BODY_BYTES = 1024 * 1024

# ── 入站请求体编码（2026-10-07 示例Harness-E，47 号后续取证）────────────────────
# 缺陷：原实现 `read(length).decode("utf-8", "replace")` ——
#   ① **完全忽略** `Content-Type` 里声明的 charset（对端声明 gbk 也照样按 utf-8 解）；
#   ② 解不出来就 **静默替换** 成 U+FFFD 继续往下走，于是平台会拿着**被污染的任务标题**
#      去建工作间、写审计、落盘。
# 实测证据：A2A 送来的中文任务在平台侧落成字面 `?`（`data/audit.json` 的
#   `workshop.create` 明细为 `47????????,?????????`，而 workshop 名取自 `title[:20]`），
#   说明**到达平台时文本已被污染**；本层的静默替换让这种污染无从察觉。
# 处理原则（沿用本文件既有的 fail-closed 风格）：**宁可明确拒绝，不可静默降级**。
_CHARSET_RE = re.compile(r"charset\s*=\s*\"?([\w\-:.]+)\"?", re.I)


def _client_charset(content_type: str) -> str:
    """从 `Content-Type` 取 charset；缺省 `utf-8`（JSON 规范默认，RFC 8259 §8.1）。"""
    m = _CHARSET_RE.search(content_type or "")
    return (m.group(1) if m else "utf-8").strip().lower()


def decode_body(raw: bytes, content_type: str) -> tuple[str, str]:
    """按对端声明的 charset **严格**解码请求体。

    返回 `(text, error)`：`error` 非空表示应当拒绝（调用方据此回 415/400）。
    **绝不用 `errors="replace"`** —— 静默替换会把乱码变成"看起来正常"的文本，
    污染下游的任务标题/工作间名/审计明细，且事后无法区分"原文如此"与"解错了"。
    """
    cs = _client_charset(content_type)
    try:
        codecs.lookup(cs)
    except LookupError:
        return "", f"不支持的请求体 charset: {cs}"
    try:
        return raw.decode(cs), ""
    except UnicodeDecodeError as e:
        return "", f"请求体不是合法的 {cs} 编码（第 {e.start} 字节处解码失败）"


# ── M-2 网络层（45 号：公司 B 经 Tailscale 虚拟 IP 访问公司 A 的 9104）──
_TAILSCALE_CMD = ("tailscale", "ip", "-4")
_TAILSCALE_CGNAT_PREFIX = "100."   # Tailscale 虚拟地址落在 100.64.0.0/10
AGENT_NAME = "External Agent Community v4"
AGENT_DESCRIPTION = (
    "本地运行的外端 Agent 生产协作平台：注册/发现、工作间三级讨论、"
    "提案投票共识、委托执行与审查收敛。"
    "对外以 A2A 标准协议提供任务协作能力。"
)
AGENT_VERSION = "4.0.0"
AGENT_URL_TEMPLATE = "http://{host}:{port}/"


# ── 平台 HTTP 调用 ─────────────────────────────────────────────

def _headers() -> dict:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def _call(method: str, path: str, payload: dict | None = None, timeout: int = 30):
    url = BASE_URL + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _get(path: str, timeout: int = 20):
    return _call("GET", path, timeout=timeout)


def _post(path: str, payload: dict, timeout: int = 60):
    return _call("POST", path, payload, timeout=timeout)


def _harness_skills() -> list[dict]:
    """汇总平台各 Harness 的能力标签为 A2A skill 列表。"""
    data = _get("/api/harness/list")
    skills: list[dict] = []
    items = data if isinstance(data, list) else []
    if isinstance(data, dict):
        items = data.get("harnesses") or data.get("data") or []
    if isinstance(items, list):
        for h in items:
            caps = h.get("capabilities") or []
            for c in caps:
                skills.append({
                    "id": f"{h.get('harness_id') or h.get('id') or 'harness'}:{c}",
                    "name": c,
                    "description": f"由 Harness {h.get('name') or h.get('harness_id')} 提供",
                })
    # 平台自身能力
    builtin = [
        "workshop_discussion", "proposal_voting", "consensus",
        "delegation", "review", "interject",
    ]
    for b in builtin:
        skills.append({"id": f"platform:{b}", "name": b, "description": "平台内建协作能力"})
    return skills


def _agent_card(host: str, port: int) -> dict:
    base_url = AGENT_URL_TEMPLATE.format(host=host, port=port)
    return {
        "name": AGENT_NAME,
        "description": AGENT_DESCRIPTION,
        "url": base_url,
        "provider": {
            "organization": "External Agent Community",
            "url": "",
            # M-1：公司级身份声明（候选 A 升公司级：本地合作社自身 company_id）。
            # 远端公司 B 持本公司 token 访问；卡片公开可读，POST 才需凭据。
            "company_id": os.environ.get("AC4_COMPANY_ID", ""),
        },
        "version": AGENT_VERSION,
        "documentationUrl": "",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": True,
            # ★V119 B2：**明确"不流式，但可长轮询"** —— 卡上写清楚，别让客户端猜。
            "statusLongPoll": {
                "supported": True,
                "method": "tasks/watch",
                "maxWaitSeconds": 30,
                "note": "长轮询（非 SSE/流式）：任务状态、产物清单或验收结论任一变化即返回；"
                        "超时如实返回 changed=false，不假装有更新",
            },
        },
        "skills": _harness_skills(),
        # 候选C §4：schemes **必须非空** —— 卡上写"无需鉴权"属误导性暴露。
        # 卡本身仍公开可读（A2A 设计：先读卡、再按卡指示鉴权）；调用方见 do_POST 的 401/403。
        "authentication": {
            "schemes": [
                {
                    "scheme": "bearer",
                    "credentials": (
                        "Authorization: Bearer <agent_token | company_token>（"
                        "成员 token=HMAC(secret, harness_id)；公司 token=HMAC(secret, company:company_id)，"
                        "两者皆可；读卡无需凭据，POST 调用未持 token 按 401、token 不匹配按 403 拒绝）"
                    ),
                }
            ],
            "credentials": None,
        },
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        # A2A v1.0: supportedInterfaces[]（替代 v0.3 的 preferredTransport + additionalInterfaces）
        # 每个接口声明 url + protocolBinding + protocolVersion，客户端按需选择
        "supportedInterfaces": [
            {"url": base_url, "protocolBinding": "JSONRPC", "protocolVersion": "1.0"},
        ],
    }


# ── A2A JSON-RPC 处理 ─────────────────────────────────────────

def _rpc_success(msg_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _text_of(message: dict) -> str:
    parts = message.get("parts", []) or []
    texts = [p.get("text", "") for p in parts if p.get("text")]
    return "\n".join(texts) or str(message)


#: `task_id → 工作间 id` 映射（V64）。为什么需要：
#: `tasks/get` 原来只认 `task["room_id"]`，而 a2a 建出来的是**工作间**不是讨论室 ⇒ room_id 为空
#: ⇒ `ws_id` 退回成 `task_id` ⇒ `/api/workshop/{task_id}/outputs` 404 ⇒ **回执里永远没有产物**。
#: 进程内映射 + 扫工作间列表兜底（进程重启后靠扫；扫不到就退化成"没有产物"，如实）。
_TASK_TO_WS: dict[str, str] = {}


def _remember_task_ws(task_id: str, ws_id: str) -> None:
    if task_id and ws_id:
        _TASK_TO_WS[str(task_id)] = str(ws_id)


def _find_ws_by_task(task_id: str) -> str:
    """扫工作间列表，找出包含这个 task 的工作间（兜底，只读）。"""
    if not task_id:
        return ""
    try:
        data = _get("/api/workshops")
        items = (data.get("workshops") if isinstance(data, dict) else data) or []
        for w in items:
            if not isinstance(w, dict):
                continue
            if str(w.get("task_id") or "") == task_id or task_id in (w.get("task_ids") or []):
                return str(w.get("workshop_id") or w.get("ws_id") or "")
            # 有些工作间把 task 放在 members/任务树里 —— 用宽松匹配（id 出现在结构里即可）
            if task_id and task_id in str(w.get("tasks") or ""):
                return str(w.get("workshop_id") or w.get("ws_id") or "")
    except Exception:                                        # noqa: BLE001
        return ""
    return ""


def _v5_artifacts(ws_id: str) -> list:
    """v5 产物清单（`V119` 批 B1）：读 P4 落下的 `_meta/artifacts.json`，**与平台同源**。

    ★2026-10-28：V64 那版只从 v4 的 `/outputs`（成员 output/ 目录）取，
      v5 的产物走的是**目录 diff**（`platform/artifacts.py` → `_meta/artifacts.json`，带 sha256）。
      两条路都要，否则"用 A2A 接入的对端"看不到 v5 的产物 —— 那就是协议落地不完整。
    **边界**：只给清单 + sha256，**不搬文件内容**（文件在谁那台机器上就在那）。
    """
    out = []
    try:
        data = _get("/api/workshop/%s/artifacts" % urllib.parse.quote(str(ws_id)))
        if not isinstance(data, dict) or not data.get("ok"):
            print("[a2a] v5 产物清单读不到 ws=%s → %s" % (ws_id, str(data)[:160]), file=sys.stderr)
            return []
        for slot, info in (data.get("slots") or {}).items():
            for rel, meta in ((info or {}).get("files") or {}).items():
                out.append({
                    "name": "%s/%s" % (slot, rel),
                    "parts": [],
                    "metadata": {"bytes": (meta or {}).get("bytes"),
                                 "sha256": (meta or {}).get("sha256"),
                                 "slot_dir": slot, "source": "v5_dir_diff",
                                 "note": "目录 diff 得出的清单；文件不在本侧，不自动搬运"},
                })
        if not out:
            print("[a2a] v5 产物清单是空的 ws=%s（空就是空，不当成有）" % ws_id, file=sys.stderr)
    except Exception as e:                                   # noqa: BLE001 — 说清楚，不吞
        print("[a2a] 读 v5 产物异常 ws=%s: %s: %s" % (ws_id, type(e).__name__, e), file=sys.stderr)
    return out[:50]


def _v4_artifacts(ws_id: str) -> list:
    """v4 产物清单（成员 `output/` 目录），保留兼容。"""
    artifacts = []
    try:
        outs = _get("/api/workshop/%s/outputs" % urllib.parse.quote(str(ws_id)))
        if not isinstance(outs, dict) or not outs.get("success"):
            # ⚠️ 不许静默：列不出产物要说出来（第一版这里 except 直接吞掉，害我查了半天）
            print("[a2a] 列产物失败 ws=%s → %s" % (ws_id, str(outs)[:160]), file=sys.stderr)
        for f in (outs.get("files") or [])[:50] if isinstance(outs, dict) else []:
            artifacts.append({
                "name": "%s/%s" % (f.get("member_id") or "?", f.get("name") or "?"),
                "parts": [],          # 不带内容（不传文件）
                "metadata": {
                    "bytes": f.get("bytes"),
                    "member_id": f.get("member_id"),
                    "role": f.get("role"),
                    "owner": "remote",       # 归属：对端机器
                    "note": "产物在对端那台机器上；本侧只看到清单，不自动下载",
                },
            })
    except Exception as e:                                   # noqa: BLE001
        print("[a2a] 列产物异常 ws=%s: %s: %s" % (ws_id, type(e).__name__, e), file=sys.stderr)
    return artifacts


def _merge_artifacts(ws_id: str) -> list:
    """v5 目录 diff + v4 output/ 合并（按 name 去重；v5 优先，因为它带 sha256）。"""
    seen, out = set(), []
    for a in _v5_artifacts(ws_id) + _v4_artifacts(ws_id):
        key = str(a.get("name") or "")
        if key and key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out[:60]


def _ledger_task_id(row: dict) -> str:
    """从账本一行里取"任务号"：优先 `task_id`，否则从任务文件名 `T-001.md` 里取。

    为什么需要：账本第一版只记 `task_seq` 和 `file`，没记 `task_id`；
    不兜这一层，"派到已有工作间"就永远绑不上验收结论（2026-10-28 判据抓到）。
    """
    if not isinstance(row, dict):
        return ""
    tid = str(row.get("task_id") or "").strip()
    if tid:
        return tid
    m = re.search(r"(T-\d+)\.md$", str(row.get("file") or ""))
    return m.group(1) if m else ""


def _v5_tasks(ws_id: str) -> list:
    """读 v5 任务账本（只读）；读不到就空列表并**说一句**。"""
    if not ws_id:
        return []
    try:
        data = _get("/api/workshop/%s/tasks" % urllib.parse.quote(str(ws_id)))
    except Exception as e:                                   # noqa: BLE001
        print("[a2a] 取任务账本失败 ws=%s: %s: %s" % (ws_id, type(e).__name__, e), file=sys.stderr)
        return []
    tasks = (data or {}).get("tasks") or []
    return tasks if isinstance(tasks, list) else []


def _v5_task(ws_id: str, task_id: str = "") -> dict:
    """按 task_id 在账本里找那条；找不到返回 `{}`（**不编**）。"""
    if not task_id:
        return {}
    for t in _v5_tasks(ws_id):
        if not isinstance(t, dict):
            continue
        if (_ledger_task_id(t) or str(t.get("task_id") or "")) == str(task_id) \
                or task_id in str(t.get("file") or "") \
                or (str(t.get("title") or "") and str(t.get("title")) == str(task_id)):
            return t
    return {}


def _v5_review(ws_id: str, task_id: str = "") -> dict:
    """把 v5 任务账本里的**验收结论**取出来（`V119` 批 B3）。

    匹配顺序：① 账本里的 `task_id` 字段 ② 任务文件路径里含这个 id ③ 标题相等。
    找不到就返回空 dict（**不编**），调用方据此不塞 `metadata.review`。
    """
    t = _v5_task(ws_id, task_id)
    if not t:
        return {}
    return {"task_id": t.get("task_id"), "task_seq": t.get("task_seq"), "state": t.get("state"),
            "result": t.get("review_result"), "evidence_level": t.get("evidence_level"),
            "note": t.get("review_note"), "reviewed_at": t.get("reviewed_at"),
            "artifact_note": "验收只针对清单与证据等级；平台不判断内容好坏"}


def _status_from_review(review: dict, default: str = "working") -> str:
    """账本状态 → A2A 状态（**一一对应，不猜**）。"""
    state = str((review or {}).get("state") or "")
    return {"accepted": "completed", "rejected": "failed", "rework": "input-required"}.get(state, default)


def _task_to_a2a(ws_id: str, task: dict | None, status: str = "working",
                 a2a_id: str = "", review: dict | None = None) -> dict:
    """把平台工作间/任务映射为 A2A Task 对象。

    - **artifacts**：v5 目录 diff（带 sha256）+ v4 `output/` 清单合并（`V119` B1）；
    - **metadata.review**：v5 任务账本的验收结论（`V119` B3），没有就不塞；
    - **status**：账本 `accepted/rejected/rework` → `completed/failed/input-required`。
    **边界（诚实）**：只带清单，**不带文件内容**——文件不在本侧就不搬。
    """
    artifacts = _merge_artifacts(ws_id)
    review = review or {}
    if review:
        status = _status_from_review(review, status)
    return {
        # ★2026-10-25：A2A 的 id 必须是**任务 id**（客户端拿它调 tasks/get）；
        #   原来这里塞的是**工作间 id** ⇒ 客户端按自己拿到的 id 回查永远查不到（真对端判据抓到）。
        #   ws_id 保留在 metadata 里，产物清单仍按它取。
        "id": (a2a_id or ws_id),
        "status": status,  # submitted / working / completed / failed / input-required / canceled
        "artifacts": artifacts,     # V64 + V119：v5 目录 diff（sha256）+ v4 清单
        "history": [],
        "metadata": {
            "ws_id": ws_id,
            "task_title": (task or {}).get("title", ""),
            "task_status": (task or {}).get("status", ""),
            "ws_status": (task or {}).get("ws_status", ""),
            "artifacts_note": "只给清单（v5 带 sha256）；文件不在本侧就不搬",
            "review": review or None,
            "review_note": "验收结论来自平台任务账本；平台不判断内容好坏，只记证据等级",
        },
    }


# ── 状态订阅（`V119` B2）：**长轮询**，不是 A2A 流式 ────────────────────────
#: `task_id → (status, artifacts 指纹, review 指纹)`；用于判断"这次查询有没有变化"
_TASK_SNAPSHOT: dict[str, tuple] = {}


def _snapshot_of(task_obj: dict) -> tuple:
    arts = task_obj.get("artifacts") or []
    fp = ",".join(sorted(str(a.get("name")) + str((a.get("metadata") or {}).get("sha256") or "")
                         for a in arts))
    rv = (task_obj.get("metadata") or {}).get("review") or {}
    return (task_obj.get("status"), fp, json.dumps(rv, sort_keys=True, ensure_ascii=False))


def _watch_task(task_id: str, wait_seconds: float = 10.0) -> dict:
    """**长轮询**等状态/产物/验收三者任一变化；超时就如实说"没变"。

    为什么是长轮询：`capabilities.streaming=false`（我们不假装支持 SSE/流式）。
    卡上写了 `statusLongPoll`，客户端按它用 —— **协议边界写清楚比假装支持强**。
    """
    import time as _time

    deadline = _time.time() + max(0.5, min(float(wait_seconds or 10), 30))
    first = None
    while True:
        task = _task_to_a2a(_TASK_TO_WS.get(task_id) or _find_ws_by_task(task_id) or task_id,
                            _get("/api/task/%s" % urllib.parse.quote(task_id)),
                            a2a_id=task_id,
                            review=_v5_review(_TASK_TO_WS.get(task_id) or "", task_id))
        snap = _snapshot_of(task)
        if first is None:
            first = snap
            prev = _TASK_SNAPSHOT.get(task_id)
            _TASK_SNAPSHOT[task_id] = snap
            if prev is not None and prev != snap:
                task["metadata"]["changed"] = True
                return task
        elif snap != first:
            _TASK_SNAPSHOT[task_id] = snap
            task["metadata"]["changed"] = True
            return task
        if _time.time() >= deadline:
            task["metadata"]["changed"] = False
            task["metadata"]["wait_note"] = ("长轮询 %ss 内没有变化（超时如实返回，不假装有更新）"
                                             % int(wait_seconds or 10))
            return task
        _time.sleep(0.5)


def _declared_skill_ids() -> set:
    """Agent Card 上**已声明**的能力 id 集合（`V119` B4 的 fail-closed 依据）。"""
    try:
        return {str(s.get("id") or "") for s in _harness_skills() if s.get("id")}
    except Exception as e:                                   # noqa: BLE001 — 取不到就空集（等于什么都不许调）
        print("[a2a] 取已声明能力失败（%s: %s）⇒ 本次按空集处理（拒绝所有指定 skill 的调用）"
              % (type(e).__name__, e), file=sys.stderr)
        return set()


def handle_rpc(msg: dict, host: str, port: int) -> dict:
    method = msg.get("method", "")
    msg_id = msg.get("id")
    params = msg.get("params", {}) or {}

    # ── A2A v1.0 方法名 → v0.3 旧名兼容映射（规范名优先，旧名保留过渡）──
    # v1.0 把 tasks/send + message/send 合并为 SendMessage：有 taskId=发消息，无=建任务
    if method == "SendMessage":
        method = "message/send" if str(params.get("taskId", "")).strip() else "tasks/send"
    elif method == "GetTask":
        method = "tasks/get"
    elif method == "CancelTask":
        method = "tasks/cancel"
    elif method == "GetExtendedAgentCard":
        method = "agent/get"
    elif method == "ListTasks":
        # v1.0 新增：列出全部任务
        resp = _get("/api/tasks")
        items = resp.get("tasks", []) if isinstance(resp, dict) else []
        tasks = [_task_to_a2a(t.get("task_id", ""), t, t.get("status", "working")) for t in items[:50]]
        return _rpc_success(msg_id, {"tasks": tasks, "total": len(items)})

    if method == "agent/get":
        return _rpc_success(msg_id, _agent_card(host, port))

    if method == "tasks/send":
        message = params.get("message", {}) or {}
        title = _text_of(message).strip()
        if not title:
            return _rpc_error(msg_id, -32602, "message 缺少文本内容")
        # ── V119 B4：**未声明能力不许调**（fail-closed）──
        want_skill = str(params.get("skillId") or (params.get("metadata") or {}).get("skill") or "")
        if want_skill:
            declared = _declared_skill_ids()
            if want_skill not in declared:
                return _rpc_error(
                    msg_id, -32004,
                    "未声明能力（%s）：本侧 Agent Card 的 skills 里没有它。可用示例：%s"
                    % (want_skill, "、".join(sorted(declared)[:5]) or "（卡上暂时没有 skills）"))
        task_id = str(params.get("taskId", "")).strip() or uuid.uuid4().hex[:8]
        # V119：允许**派到已有工作间**（`params.workshopId`）—— 外部 A2A 客户端把活
        # 派进一个已经建好名单/看板/产物的 v5 工作间时用；不传就照旧新建一个。
        attach_ws = str(params.get("workshopId") or (params.get("metadata") or {}).get("workshopId") or "").strip()
        if attach_ws:
            probe = _get("/api/workshop/%s" % urllib.parse.quote(attach_ws))
            if not isinstance(probe, dict) or not (probe.get("workshop_id") or probe.get("ws_id")):
                return _rpc_error(msg_id, -32004,
                                  "指派的 workshopId 不存在或读不到：%s（平台返回：%s）"
                                  % (attach_ws, str(probe)[:160]))
            _remember_task_ws(task_id, attach_ws)
            # `V119` B3：把 A2A 任务**绑到账本里的那条**——客户端给了 taskId 就按它对，
            # 没给就取这个工作间**唯一的一条**（多于一条时不猜，如实说"要先给 taskId"）。
            ledger = _v5_tasks(attach_ws)
            if not str(params.get("taskId", "")).strip():
                if len(ledger) == 1:
                    task_id = _ledger_task_id(ledger[0]) or task_id
                    _remember_task_ws(task_id, attach_ws)
                elif len(ledger) > 1:
                    return _rpc_error(
                        msg_id, -32602,
                        "这个工作间有 %d 条任务，必须用 `taskId` 指明是哪一条（不替你猜）：%s"
                        % (len(ledger), "、".join(_ledger_task_id(t) or "?" for t in ledger[:5])))
            review = _v5_review(attach_ws, task_id)
            return _rpc_success(msg_id, _task_to_a2a(attach_ws, probe, "working",
                                                    a2a_id=task_id, review=review))
        # 平台侧：创建工作间（含任务），触发广播与讨论
        # V-42 遗留修复：平台 /api/workshop 实际字段为 name/hall_content（title/description 导致未命名工作间）
        # ★2026-10-25：**必须让平台真建出任务**（原来只建了空工作间，	ask_id 被忽略
        #   ⇒ 客户端拿到的 id 查不到、也没处回报结果 —— 与 MCP 那条是同一个洞）
        resp = _post("/api/workshop", {
            "name": title[:20],
            "hall_content": title,
            "task_id": task_id,
            "create_task": True,
            "task_title": title[:80],
        })
        if not resp.get("ok", True):
            return _rpc_error(msg_id, -32000, f"平台创建任务失败: {resp}")
        ws_id = (resp.get("workshop_id") or resp.get("ws_id")
                 or (resp.get("data") or {}).get("ws_id") or "")
        # 插件：自动回执讨论（工作间 draft→discussing，触发平台 AI 参与），失败不影响主流程
        try:
            _a2a_relay.on_task_created(ws_id, task_id, title)
        except Exception:  # noqa: BLE001
            pass
        _remember_task_ws(task_id, ws_id)      # V64：记住 task→工作间，回查时才能列出产物
        _real_tid = str(resp.get("task_id") or task_id)
        return _rpc_success(msg_id, _task_to_a2a(ws_id, resp, "working", a2a_id=_real_tid))

    if method == "tasks/get":
        task_id = str(params.get("taskId", "")).strip()
        if not task_id:
            return _rpc_error(msg_id, -32602, "缺少 taskId")
        # 先按 task_id 查任务，再尝试按工作间 id 查
        task = _get(f"/api/task/{urllib.parse.quote(task_id)}")
        # V64：工作间 id 的解析顺序 —— ①进程内映射 ②任务的 room_id ③扫工作间列表 ④退回 task_id
        ws_id = _TASK_TO_WS.get(task_id) or ""
        if not ws_id and isinstance(task, dict) and task.get("room_id"):
            ws_id = str(task.get("room_id"))
        if not ws_id:
            ws_id = _find_ws_by_task(task_id)
        if not ws_id:
            ws_id = task_id
        detail = _get(f"/api/room/by-task/{urllib.parse.quote(task_id)}")
        status = "working"
        if isinstance(task, dict) and task.get("status") in ("completed", "failed"):
            status = task["status"]
        # V119 B3：把 v5 账本的验收结论一并带出（状态也按账本映射）
        review = _v5_review(ws_id, task_id)
        return _rpc_success(msg_id, _task_to_a2a(ws_id, task, status, a2a_id=task_id,
                                                review=review))

    if method in ("tasks/watch", "tasks/resubscribe"):
        # V119 B2：长轮询订阅（**不是流式**；卡上 capabilities.statusLongPoll 已声明）
        task_id = str(params.get("taskId", "")).strip()
        if not task_id:
            return _rpc_error(msg_id, -32602, "缺少 taskId")
        wait = params.get("waitSeconds", 10)
        try:
            wait_f = float(wait)
        except (TypeError, ValueError):                       # 给个默认 10 秒，别在这儿炸
            wait_f = 10.0
        return _rpc_success(msg_id, _watch_task(task_id, wait_f))

    if method == "tasks/cancel":
        return _rpc_success(msg_id, {"id": str(params.get("taskId", "")), "status": "canceled"})

    if method == "message/send":
        task_id = str(params.get("taskId", "")).strip()
        message = params.get("message", {}) or {}
        content = _text_of(message)
        if not task_id or not content:
            return _rpc_error(msg_id, -32602, "缺少 taskId 或 message 文本")
        # 先解析任务 → 工作间（与 tasks/get 同源），再转发为平台工作间讨论消息
        ws_id = task_id
        task = _get(f"/api/task/{urllib.parse.quote(task_id)}")
        if isinstance(task, dict):
            room = task.get("room_id")
            if room:
                ws_id = room
        resp = _post(f"/api/workshop/{urllib.parse.quote(str(ws_id))}/discuss", {
            "message": content,
        })
        if not resp.get("ok", True):
            return _rpc_error(msg_id, -32000, f"平台转发消息失败: {resp}")
        return _rpc_success(msg_id, {"taskId": task_id, "messageId": uuid.uuid4().hex[:12]})

    if method == "message/get":
        return _rpc_success(msg_id, {"id": str(params.get("taskId", "")), "messages": []})

    return _rpc_error(msg_id, -32601, f"未知方法: {method}")


# ── HTTP 服务 ─────────────────────────────────────────────────

class A2AHandler(BaseHTTPRequestHandler):
    server_version = "AC4-A2A/4.0.0"

    def _send_json(self, obj: dict, code: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        _p = self.path.rstrip("/")
        # v1.0 标准发现路径 + v0.2 兼容路径
        if _p in ("/.well-known/agent-card.json", "/.well-known/agent.json"):
            host = self.headers.get("Host", "localhost").split(":")[0]
            port = self.server.server_port
            self._send_json(_agent_card(host, port))
        elif _p == "/health":
            self._send_json({"status": "ok"})
        else:
            self._send_json({"error": "not found", "hint": "/.well-known/agent-card.json"}, 404)

    def do_POST(self):
        # 候选C §3.1：**先鉴权后处理**（401 缺凭据 / 403 校验失败，语义与平台 P1 一致）。
        # 顺序：**先读尽请求体 → 按声明 charset 严格解码（只记错不返回）→ 再鉴权 → 最后校验编码/解析**。
        #   ① HTTP/1.1 keep-alive 必须消费完请求体，否则提前返回会让连接错位
        #      （实测 401 早退导致客户端 `WinError 10053`）；
        #   ② 读体前先限长（镜像平台 `MAX_BODY_BYTES` 1MB），**未认证方不能无限塞**；
        #   ③ 鉴权失败仍不进入 JSON-RPC 解析（稿B §3.1 先鉴权后处理）；
        #   ④ **编码错误在鉴权之后才回报**（2026-10-07 补）：未认证方不该借错误码
        #      探测服务行为；且绝不静默替换，见 `decode_body` 的说明。
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > _MAX_BODY_BYTES:
            self._send_json({"error": f"请求体过大（上限 {_MAX_BODY_BYTES} 字节）"}, 413)
            return
        raw_bytes = self.rfile.read(length) if length else b""
        raw, enc_error = decode_body(raw_bytes, self.headers.get("Content-Type", ""))

        auth = self.headers.get("Authorization", "")
        bearer = auth[7:].strip() if auth.startswith("Bearer ") else ""
        verdict = _identity.check_edge_bearer(bearer)
        if verdict:
            code, msg = verdict
            self._send_json({"error": msg, "hint": "见 /.well-known/agent-card.json 的 authentication.schemes"}, code)
            return
        if self.path.split("?")[0].rstrip("/") not in ("", "/"):
            self._send_json({"error": "not found"}, 404)
            return
        if enc_error:
            # 415 = charset 本身不认识；400 = charset 认识但字节不是该编码
            self._send_json(
                {"error": enc_error,
                 "hint": "请以 `Content-Type: application/json; charset=utf-8` 发送 UTF-8 JSON；"
                         "若确实使用其他编码，请在 Content-Type 中如实声明 charset"},
                415 if enc_error.startswith("不支持") else 400,
            )
            return
        try:
            msg = json.loads(raw or "{}")
        except Exception:  # noqa: BLE001
            self._send_json({"jsonrpc": "2.0", "error": {"code": -32700, "message": "解析失败"}}, 400)
            return
        host = self.headers.get("Host", "localhost").split(":")[0]
        port = self.server.server_port
        resp = handle_rpc(msg, host, port)
        self._send_json(resp)

    def log_message(self, fmt, *args):  # noqa: A003
        sys.stderr.write("[a2a] " + (fmt % args) + "\n")


def resolve_tailscale_ipv4(runner=None) -> str:
    """M-2（45 号）：解析本机 **Tailscale 虚拟 IPv4**（`tailscale ip -4`）。

    安全约束（45 号 §二.2）：9104 要绑 **Tailscale 虚拟 IP**，而不是 `0.0.0.0`。
    因此这里**fail-closed**：取不到（未安装/未登录/无输出）就**拒绝启动**，
    绝不当场退化成 `0.0.0.0` —— 否则"避免公网扫描"的约束就白写了。

    只接受 **100.64.0.0/10（CGNAT 段）** 地址，避免误绑到真实网卡的内网/公网 IP。
    `runner` 供测试注入（默认真的调 `tailscale`）。
    """
    if runner is None:

        def runner(cmd):  # type: ignore[misc]
            try:
                return subprocess.run(  # noqa: S603
                    cmd, capture_output=True, text=True, timeout=10
                ).stdout or ""
            except Exception:  # noqa: BLE001  未安装(FileNotFoundError)/超时/权限一律按取不到
                return ""

    # runner 自身抛错（命令不存在 / 超时 / 权限）也必须走 fail-closed，不把异常泄给上层
    try:
        out = (runner(list(_TAILSCALE_CMD)) or "").strip()
    except Exception:  # noqa: BLE001
        out = ""
    ip = out.splitlines()[0].strip() if out else ""
    if not ip or not ip.startswith(_TAILSCALE_CGNAT_PREFIX):
        print(
            "[a2a] ✗ 未取到 Tailscale 虚拟 IP（`tailscale ip -4` 无有效输出）。\n"
            "      M-2 要求绑 Tailscale 虚拟 IP 而非 0.0.0.0；为不退化成公网暴露，按 fail-closed 拒绝启动。\n"
            "      处理：安装并登录 Tailscale（登录后 `tailscale ip -4` 应返回 100.x.y.z）；\n"
            "      仅本机调试请用默认 `--host 127.0.0.1`。",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return ip


def auth_startup_guard(host: str) -> str:
    """稿B §5 / 稿A C4：**鉴权先于暴露**（fail-closed）—— 纯判定，便于测试。

    返回实际生效的绑定地址：
    - 鉴权配置可用（`agent_token_secret` 非空）→ 原样返回 `host`；
    - 否则**拒绝监听**（`SystemExit(1)`），而不是"先起再说"。
    """
    if _identity.auth_secret_ready():
        return host
    print(
        "[a2a] ✗ 鉴权未就绪：config.agent_token_secret 为空，按 fail-closed **拒绝监听**。\n"
        "      启用方式：先在平台侧签发 agent_token（identity.issue_agent_token 会生成并持久化 secret），"
        "或由负责人提供鉴权配置后再启动。",
        file=sys.stderr,
    )
    raise SystemExit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description="A2A 暴露层（agent_community-v4）")
    # 稿A C1 / 拍板 D-C4：默认改绑 **回环**（原 0.0.0.0 会把"无鉴权暴露面"直接送到网络上；
    # 18920 保持 0.0.0.0+token 不动，见拍板 D-A2）。
    ap.add_argument("--host", default="127.0.0.1",
                    help="绑定地址；传 `tailscale` 则自动解析 Tailscale 虚拟 IP（M-2，fail-closed）")
    ap.add_argument("--port", type=int, default=9104)
    args = ap.parse_args()

    # M-2：`--host tailscale` → 解析虚拟 IP（取不到就拒绝启动，不退化成 0.0.0.0）
    if args.host == "tailscale":
        args.host = resolve_tailscale_ipv4()
        print(f"[a2a] Tailscale 模式：绑定虚拟 IP {args.host}（仅经 Tailscale 网络可达）", file=sys.stderr)

    args.host = auth_startup_guard(args.host)

    loopback = args.host in ("127.0.0.1", "localhost", "::1")
    if not loopback:
        print(f"[a2a] ⚠ 非回环地址 {args.host}：入站将强制 Bearer 鉴权（401/403）", file=sys.stderr)

    httpd = ThreadingHTTPServer((args.host, args.port), A2AHandler)
    # 插件：启动后台扫描线程（回复自动落盘 A2A_inbox 信箱）
    try:
        _a2a_relay.start()
    except Exception:  # noqa: BLE001
        pass
    print(f"[a2a] Agent Card: http://{args.host}:{args.port}/.well-known/agent-card.json")
    print(f"[a2a] JSON-RPC : POST http://{args.host}:{args.port}/  （入站 Bearer 鉴权已启用）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
