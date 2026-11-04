"""
MCP 协调层 — 将 agent_community-v4 平台能力暴露为 MCP Server（stdio 传输）。

设计目标：
1. 零第三方依赖（仅标准库），可直接被 Claude Code / Cursor / 任意 MCP 客户端挂载。
2. 最小侵入：不改 server.py 主线，通过 HTTP 调用平台 REST API（默认 127.0.0.1:18920）。
3. 工具设计对齐 Routa mcp-tools：覆盖注册发现 / 状态查询 / 工作间协作全流程。

挂载示例（Claude Code，在项目根目录即 agent_community 包的父目录）：
    claude --mcp-config '{"servers":{"ac4":{"command":"python","args":["-m","agent_community.mcp_server"]}}}'

⚠️ 本模块刻意放在 `agent_community/` 顶层而不是 `agent_community/platform/`：
   `platform/__init__.py` 里有 `from .server import app, ...`，任何 `platform.*` 子模块一被导入
   就会把整个 FastAPI 服务（fastapi/uvicorn + 静态前端挂载）拽进来，并向 **stdout 打印启动横幅**——
   而 MCP 走的就是 stdout，协议流会被横幅污染，客户端直接解析失败。
   放在顶层包后本模块保持真正的「零第三方依赖 + stdout 纯净」。

环境变量：
    AC4_BASE_URL  平台 REST 基址，默认 http://127.0.0.1:18920
    AC4_TOKEN     平台访问 Token（本地放行时可省略）
"""

from __future__ import annotations

from .platform_address import base_url_from_env
import base64
import json
import os
import re
import sys
import urllib.request
import urllib.error
import urllib.parse
import uuid

BASE_URL = base_url_from_env().rstrip("/")
TOKEN = os.environ.get("AC4_TOKEN", "")
SERVER_NAME = "agent-community-v4-mcp"
SERVER_VERSION = "4.0.0"
# MCP 协议版本：列表按「新 → 旧」排列，首个即服务器默认（最高）版本。
# 规范（MCP Lifecycle, 2025-06-18）要求版本协商：客户端在 initialize 里带
# protocolVersion，服务器支持该版本就回同一个，否则回一个自己支持的版本。
SUPPORTED_PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]
PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]


# ── 平台 HTTP 调用 ─────────────────────────────────────────────

def _headers() -> dict:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    # ★2026-10-25（V104 全链路判据抓到）：这个 harness 一旦被签发了 Agent-Token，
    #   平台的 /api/harness/task-result 就要求带 X-Agent-Token —— 原来这里不带，
    #   于是**外部 agent 通过 MCP 交回执一律 401**（看着该通的，其实交不上）。
    #   令牌从环境变量读（不写进配置文件、不经过模型）。
    ht = os.environ.get("AC_HARNESS_TOKEN", "").strip()
    if ht:
        h["X-Agent-Token"] = ht
    return h


def _call(method: str, path: str, payload: dict | None = None, timeout: int = 30):
    url = BASE_URL + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(), method=method)
    # ★2026-10-25（V104 全链路判据抓到）：4xx/5xx 原来直接抛 HTTPError，
    #   客户端只看到一句 "HTTP Error 409: Conflict"，**平台说的原因全丢了**
    #   （比如闸门成员都还没接上：m0、“消息不能为空”这类可执行的下一步）。
    #   现在把响应体解析出来一并返回，让调用方（模型/人）看得到真实原因。
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace") or ""
        try:
            body = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            body = {"raw": raw}
        if isinstance(body, dict):
            body.setdefault("http_status", int(e.code))
            body.setdefault("error", body.get("error") or ("HTTP %s" % e.code))
        return body
    try:
        out = json.loads(raw)
    except json.JSONDecodeError:
        out = {"raw": raw}
    if isinstance(out, dict) and status >= 400:
        out.setdefault("http_status", int(status))
    return out


def _get(path: str, timeout: int = 20):
    return _call("GET", path, timeout=timeout)


def _post(path: str, payload: dict, timeout: int = 60):
    return _call("POST", path, payload, timeout=timeout)


def _text(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


# ── 工具实现 ───────────────────────────────────────────────────

def t_pending_session_notes(args: dict) -> str:
    """取「平台请你自己发到你会话里」的话（**由你来发，平台不代发**）。"""
    lim = int(args.get("limit", 5) or 5)
    return _text(_get("/api/mcp/session-notes?pending=1&limit=%d" % max(1, min(lim, 20))))


def t_report_session_sent(args: dict) -> str:
    """回报「这条我已经在自己的会话里发了」（平台记下时间与你的客户端名；这是你的自报）。"""
    nid = str(args.get("note_id", "")).strip()
    if not nid:
        return _text({"error": "缺少 note_id（先用 pending_session_notes 取一条）"})
    payload = {"text": str(args.get("text", "")).strip(),
               "harness_id": str(args.get("harness_id", "")).strip(),
               "client": "mcp-client"}
    return _text(_post("/api/mcp/session-notes/%s/sent" % urllib.parse.quote(nid), payload))


def t_report_progress(args: dict) -> str:
    """**干活时每步报一次**：让用户直接在「工作过程」里看到你在干嘛。"""
    payload = {"text": str(args.get("text", "")).strip(),
               "harness_id": str(args.get("harness_id", "")).strip(),
               "task_id": str(args.get("task_id", "")).strip(),
               "kind": str(args.get("kind", "") or "step").strip()}
    if not payload["text"]:
        return _text({"error": "缺少 text（一句话说明你这步干了什么）"})
    return _text(_post("/api/progress", payload))


def t_status(_: dict) -> str:
    return _text(_get("/api/status"))


def _limit_of(args: dict, default: int = 50) -> int:
    """从工具参数取 limit（1~200，越界夹住）。MCP 的返回直接进模型上下文，不许一次吐全量。"""
    try:
        n = int(args.get("limit") or default)
    except Exception:                                          # noqa: BLE001 — 传了非数字就用默认
        n = default
    return max(1, min(200, n))


def _cut(payload, key: str, n: int) -> dict:
    """把 payload[key] 列表截到 n 条，并**如实说明截了**（别让人以为就这么多）。"""
    if not isinstance(payload, dict):
        return payload
    rows = payload.get(key)
    if not isinstance(rows, list) or len(rows) <= n:
        return payload
    out = dict(payload)
    out[key] = rows[:n]
    out["truncated"] = True
    out["total"] = len(rows)
    out["note"] = "只返回前 %d 条（共 %d 条）；要更多就传更大的 limit（上限 200）" % (n, len(rows))
    return out


def t_list_harnesses(args: dict) -> str:
    return _text(_cut(_get("/api/harness/list"), "harnesses", _limit_of(args)))


def t_list_agents(args: dict) -> str:
    return _text(_cut(_get("/api/agents"), "agents", _limit_of(args)))


def t_list_tasks(args: dict) -> str:
    return _text(_cut(_get("/api/tasks"), "tasks", _limit_of(args)))


def t_get_task(args: dict) -> str:
    task_id = str(args.get("task_id", "")).strip()
    if not task_id:
        return _text({"error": "缺少 task_id"})
    return _text(_get(f"/api/task/{urllib.parse.quote(task_id)}"))


def t_create_workshop(args: dict) -> str:
    """建工作间 + **同时建出背后那条任务**（V104 全链路判据抓到：原来只建空房间）。

    /api/command（大厅发布）会**广播并叫 AI**，不适合外部 agent 按协议自己建活；
    所以这里走 /api/workshop + create_task: true：只建任务与关联，不广播。
    """
    title = str(args.get("title", "")).strip()
    if not title:
        return _text({"error": "缺少 title"})
    desc = str(args.get("description", "")).strip()
    payload = {
        "name": title[:40],
        "hall_content": desc or title,
        "create_task": True,
        "task_title": title,
        "task_description": desc or title,
    }
    if args.get("max_participants"):
        payload["max_participants"] = int(args.get("max_participants") or 0)
    return _text(_post("/api/workshop", payload))



def t_workshop_review(args: dict) -> str:
    ws_id = str(args.get("ws_id", "")).strip()
    if not ws_id:
        return _text({"error": "缺少 ws_id"})
    payload = {
        "content": str(args.get("content", "")).strip(),
        "verdict": str(args.get("verdict", "")).strip() or "approved",
        "agent": str(args.get("agent", "")).strip() or "user",
    }
    return _text(_post(f"/api/workshop/{urllib.parse.quote(ws_id)}/review", payload))


def t_workshop_continue(args: dict) -> str:
    ws_id = str(args.get("ws_id", "")).strip()
    if not ws_id:
        return _text({"error": "缺少 ws_id"})
    return _text(_post(f"/api/workshop/{urllib.parse.quote(ws_id)}/continue", {}))


def t_workshop_interject(args: dict) -> str:
    ws_id = str(args.get("ws_id", "")).strip()
    content = str(args.get("content", "")).strip()
    if not ws_id or not content:
        return _text({"error": "缺少 ws_id 或 content"})
    payload = {
        "content": content,
        "priority": int(args.get("priority", 0) or 0),
        "agent": str(args.get("agent", "")).strip() or "user",
    }
    return _text(_post(f"/api/workshop/{urllib.parse.quote(ws_id)}/interject", payload))


def t_submit_task_result(args: dict) -> str:
    harness_id = str(args.get("harness_id", "")).strip()
    task_id = str(args.get("task_id", "")).strip()
    if not harness_id or not task_id:
        return _text({"error": "缺少 harness_id 或 task_id"})
    payload = {
        "task_id": task_id,
        "ok": bool(args.get("ok", True)),
        # 平台的正文字段叫 `result`（另兼容 text/reply/content）；两个都带最稳。
        "result": str(args.get("content", "")).strip(),
        "content": str(args.get("content", "")).strip(),
        "artifacts": list(args.get("artifacts", []) or []),
        "confidence": float(args.get("confidence", 1.0) or 1.0),
    }
    # ★2026-10-25（V104）：带上 workshop_id（如果这条活是在某个工作间里）回执才会落进讨论区、
    #   才可能推进任务；不带就只有留痕（平台会在返回里如实说状态不会变）。
    for _k in ("workshop_id", "member_id"):
        _v = str(args.get(_k, "")).strip()
        if _v:
            payload[_k] = _v
    return _text(_post("/api/harness/task-result", payload))


# ── 工具注册表 ─────────────────────────────────────────────────

TOOLS: list[dict] = [
    {
        "name": "report_progress",
        "description": "干活过程中每完成一步就报一次（一句话说清这步做了什么、结果怎样）。什么时候用：开始执行任务后，每推进一步调一次。返回：平台记下这一步，用户在「工作过程」面板里能立刻看到。kind 可选 step（一般推进）/ note（说明）/ block（卡住了）/ done（这活完了）。",
        "inputSchema": {"type": "object",
                        "properties": {"text": {"type": "string", "description": "一句话：这步干了什么、结果怎样"},
                                       "task_id": {"type": "string", "description": "可选：属于哪条任务"},
                                       "harness_id": {"type": "string", "description": "可选：你的 harness_id"},
                                       "kind": {"type": "string", "description": "step / note / block / done"}},
                        "required": ["text"]},
        "fn": t_report_progress,
    },
    {
        "name": "pending_session_notes",
        "description": "取「平台希望你亲自发到自己会话里」的那些话（平台不代发，只提示你发）。什么时候用：会话刚开始、或平台提示有待办时调一次。返回：待办列表；你发完后用 report_session_sent 回报已发。",
        "inputSchema": {"type": "object",
                        "properties": {"limit": {"type": "integer", "description": "最多几条，默认 5"}},
                        "required": []},
        "fn": t_pending_session_notes,
    },
    {
        "name": "report_session_sent",
        "description": "回报「这句话我已经在自己会话里发给用户了」。什么时候用：把 pending_session_notes 里的内容真发出去之后。返回：平台只记录你的自报与时间（不代写、不核对内容），别拿它当「已送达」的证据。",
        "inputSchema": {"type": "object",
                        "properties": {"note_id": {"type": "string", "description": "待办 id"},
                                       "text": {"type": "string", "description": "你实际发出去的原话（可选）"},
                                       "harness_id": {"type": "string", "description": "可选：你的 harness_id"}},
                        "required": ["note_id"]},
        "fn": t_report_session_sent,
    },
    {
        "name": "status",
        "description": "查平台运行状态。什么时候用：刚开始接入、想确认平台活着、需要版本号时。返回：平台版本、已注册 Agent 数、在线数、任务总数、Harness 数。只读，无副作用。",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "fn": t_status,
    },
    {
        "name": "list_harnesses",
        "description": "列出平台已注册的外部 Agent 接入实例（harness）及其能力标签。什么时候用：想知道平台上现在有哪些 agent 可用时。返回：harness_id / 名称 / 唤醒方式 / 是否在线 / 能力摘要。只读。（与 list_agents 的区别：这个只列 harness 接入，不含平台内部 agent。）",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "最多返回几条（1~200，默认 50）"}}, "required": []},
        "fn": t_list_harnesses,
    },
    {
        "name": "list_agents",
        "description": "列出平台全部 Agent（含平台内部的编排 agent 与所有 harness）。什么时候用：要看「谁在线、谁能干」的全景时。返回：agent_id / 名称 / 在线状态 / 能力。只读。（与 list_harnesses 的区别：这个含平台内部 agent，范围更大。）",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "最多返回几条（1~200，默认 50）"}}, "required": []},
        "fn": t_list_agents,
    },
    {
        "name": "list_tasks",
        "description": "列出平台全部任务及其状态、进度、所属工作间。什么时候用：想知道现在有哪些活在跑 / 卡住 / 完成了时。返回：任务列表（task_id / 状态 / 工作间 / 标题）。只读。要单条细节用 get_task。",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "最多返回几条（1~200，默认 50）"}}, "required": []},
        "fn": t_list_tasks,
    },
    {
        "name": "get_task",
        "description": "按 task_id 查一条任务的完整详情。什么时候用：从 list_tasks 拿到 id 后、或用户问那条活怎么样了时。返回：任务状态、讨论消息、委托记录、执行结果。只读。",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "任务 ID"}},
            "required": ["task_id"],
        },
        "fn": t_get_task,
    },
    {
        "name": "create_workshop",
        "description": "新建一个协作工作间并发起一条任务（平台会广播，其它 Agent 可以举手加入）。什么时候用：要开一件需要多人协作的活时。返回：workshop_id 与 task_id —— 后续提到这条活都用这两个 id。注意：会真的建目录与任务，不是试运行。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "任务标题"},
                "description": {"type": "string", "description": "任务描述"},
                "task_id": {"type": "string", "description": "可选，关联已有任务"},
                "max_participants": {"type": "integer", "description": "可选，参与上限"},
            },
            "required": ["title"],
        },
        "fn": t_create_workshop,
    },
    {
        "name": "workshop_review",
        "description": "对某个工作间当前的产出一句话审查收敛。什么时候用：这一轮干完了、要下判断时。返回：平台记下这次审查；verdict 用 approved / rejected，另附你的意见。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ws_id": {"type": "string", "description": "工作间 ID"},
                "content": {"type": "string", "description": "审查意见"},
                "verdict": {"type": "string", "description": "approved|rejected"},
                "agent": {"type": "string", "description": "可选，审查人"},
            },
            "required": ["ws_id"],
        },
        "fn": t_workshop_review,
    },
    {
        "name": "workshop_continue",
        "description": "推进某个工作间进入下一轮（继续讨论或继续执行）。什么时候用：审查通过、还有下一段活要干时。返回：平台登记这次推进；同一轮里别重复调（会重复烧钱）。",
        "inputSchema": {
            "type": "object",
            "properties": {"ws_id": {"type": "string", "description": "工作间 ID"}},
            "required": ["ws_id"],
        },
        "fn": t_workshop_continue,
    },
    {
        "name": "workshop_interject",
        "description": "往工作间插一条高优先级打断意见（可以打断当前流程）。什么时候用：发现方向错了、要立刻纠偏时；平时别用，会打断别人。返回：平台记下这条插话并带优先级。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ws_id": {"type": "string", "description": "工作间 ID"},
                "content": {"type": "string", "description": "打断内容"},
                "priority": {"type": "integer", "description": "优先级，默认 0"},
                "agent": {"type": "string", "description": "可选，插入人"},
            },
            "required": ["ws_id", "content"],
        },
        "fn": t_workshop_interject,
    },
    {
        "name": "submit_task_result",
        "description": "以 Harness 身份回报一条任务的执行结果。什么时候用：活干完了（或失败/放弃）时调用，让平台账本从进行中变成已交付。返回：平台记下结果与时间；ok=是否成功，content=结果正文，artifacts=产物路径列表。注意：这只是你报的结果，验收结论由人给，不是你能定的。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "harness_id": {"type": "string", "description": "Harness ID"},
                "task_id": {"type": "string", "description": "任务 ID"},
                "ok": {"type": "boolean", "description": "是否成功"},
                "content": {"type": "string", "description": "结果正文"},
                "artifacts": {"type": "array", "items": {"type": "string"}, "description": "产物路径"},
                "confidence": {"type": "number", "description": "置信度 0~1"},
            },
            "required": ["harness_id", "task_id"],
        },
        "fn": t_submit_task_result,
    },
]

# ── 工具分面（V75 P1-3）：默认只挂 `api`，按需加挂，减少上下文污染 ─────────
#: 组名 → 该组包含的工具名。**每一只工具都必须落在某一组里**（守卫会查孤儿）。
#: `browser` / `devices` / `plan` 目前**还没有工具** —— 如实留空组，不编占位工具。
TOOLSETS: dict = {
    "api": ['status', 'list_harnesses', 'list_agents'],
    "task": ['list_tasks', 'get_task', 'create_workshop', 'workshop_review',
             'workshop_continue', 'workshop_interject', 'submit_task_result',
             # 2026-10-25：**让它自己发**那两个工具归"干活"这组（不另开组，免得用户多记一个开关）
             'pending_session_notes', 'report_session_sent', 'report_progress'],
    "bridge": [],
    "browser": [],
    "devices": [],
    "plan": [],
}


def enabled_toolset_names() -> list:
    """本进程启用的组：`AC_MCP_TOOLSETS`（逗号分隔，默认 `api`；`all` = 全部）。"""
    # 默认 api,task = 当前全部现有工具（**不回归**）；以后加工具时再让用户按需收窄
    raw = (os.environ.get("AC_MCP_TOOLSETS") or "api,task").strip()
    if raw.lower() in ("all", "*"):
        return [k for k, v in TOOLSETS.items() if v]
    picked = [x.strip() for x in raw.split(",") if x.strip()]
    return [x for x in picked if TOOLSETS.get(x)] or ["api", "task"]


def active_tools() -> list:
    """只回启用组里的工具（顺序按 TOOLSETS 定义，稳定）。"""
    want = set()
    for name in enabled_toolset_names():
        want.update(TOOLSETS.get(name) or [])
    return [t for t in TOOLS if t["name"] in want]


def tool_enabled(name: str) -> bool:
    return any(t["name"] == name for t in active_tools())

_TOOL_MAP = {t["name"]: t for t in TOOLS}


# ── MCP Resources（只读；V127，老板 2026-11-04 拍板）─────────────
#
# 为什么有这一层：客户端此前只能**调工具**才知道平台里有什么；Resources 给它一个
# 「按 URI 看」的只读目录（资源在规范里就是只读的，写一律走工具）。
#
# 四条纪律（评审与实测逼出来的）：
#   ① 如实声明 `subscribe=false` / `listChanged=false` —— 我们没有事件总线，不假声明；
#   ② **不能原样转发平台响应**：`GET /api/status` 里带 `pipe_dir`（真实绝对路径）
#      ⇒ 一律先**白名单投影**再 `_scrub()` 兜底，令牌/路径统统不出去；
#   ③ 上限**可配**（`AC_MCP_RESOURCE_MAX_BYTES`，默认 64 KB）；清单类超限**先翻页**，
#      真截断时必须带 `truncated/full_size`，不许"悄悄截一半"；
#   ④ 两套游标别混：协议层管"多少个资源条目"，资源层（`?cursor=`）管"一个资源里多少条记录"。

RESOURCE_MAX_BYTES_DEFAULT = 65536
RESOURCE_PAGE_SIZE = 50

#: 键名里带这些的一律不出资源（令牌、密钥、凭据）
_SENSITIVE_KEY_RE = re.compile(r"(token|secret|password|passwd|api[_-]?key|credential)", re.I)
#: Windows 盘符路径 / POSIX 深路径。
#: ⚠️ 别把 URI 当路径：`ac://workshop/x/y` 里 `/x` 前面是 `/`、`/y` 前面是字母 ⇒ 都不算；
#:    `https://example.com/a/b` 同理（`/a` 前面是域名里的字母）。所以 lookbehind 排除 `:`、`/`、词字符。
_WIN_PATH_RE = re.compile(r"[A-Za-z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)*[^\\/:*?\"<>|\r\n]*")
_POSIX_PATH_RE = re.compile(r"(?<![:\w/])/(?:[^\s/\"']+/){2,}[^\s\"',;)}\]]*")


class ResourceError(Exception):
    """资源读不了（不存在 / 语法不对）。带 MCP 错误码，别让上层猜。"""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _max_bytes() -> int:
    raw = (os.environ.get("AC_MCP_RESOURCE_MAX_BYTES") or "").strip()
    try:
        n = int(raw)
        return n if n >= 1024 else RESOURCE_MAX_BYTES_DEFAULT
    except Exception:                                          # noqa: BLE001 — 配错就用默认，不拖垮启动
        return RESOURCE_MAX_BYTES_DEFAULT


def _scrub(obj):
    """递归脱敏：敏感键名 → `<hidden>`；字符串里的绝对路径 → `<path>`。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _SENSITIVE_KEY_RE.search(str(k)):
                out[k] = "<hidden>"
            else:
                out[k] = _scrub(v)
        return out
    if isinstance(obj, list):
        return [_scrub(x) for x in obj]
    if isinstance(obj, str):
        s = _WIN_PATH_RE.sub("<path>", obj)
        s = _POSIX_PATH_RE.sub("<path>", s)
        return s
    return obj


#: 固定资源（`resources/list` 直接列出；全部不带参数）
RESOURCES: list[dict] = [
    {"uri": "ac://status", "name": "平台状态",
     "description": "只读：版本、在线数、任务数、数据健康摘要（不含任何本机路径）",
     "mimeType": "application/json", "fn": "_res_status"},
    {"uri": "ac://harnesses", "name": "接入清单",
     "description": "只读：已接入的 harness 及其档位/接口类别（令牌一律隐藏）",
     "mimeType": "application/json", "fn": "_res_harnesses"},
    {"uri": "ac://agents", "name": "员工清单",
     "description": "只读：平台登记的 agent 与在线状态",
     "mimeType": "application/json", "fn": "_res_agents"},
    {"uri": "ac://workshops", "name": "工作间清单",
     "description": "只读：工作间 id/名称/状态/成员数",
     "mimeType": "application/json", "fn": "_res_workshops"},
    {"uri": "ac://tasks", "name": "任务清单",
     "description": "只读：平台任务（默认 50 条，再多按正文里的 next 翻页）",
     "mimeType": "application/json", "fn": "_res_tasks"},
    {"uri": "ac://protocols", "name": "协议目录",
     "description": "只读：每条协议的层（layer）与语义（semantics）",
     "mimeType": "application/json", "fn": "_res_protocols"},
]

#: 模板资源（`resources/templates/list` 列出，客户端按需展开）
RESOURCE_TEMPLATES: list[dict] = [
    {"uriTemplate": "ac://workshop/{workshop_id}", "name": "一个工作间",
     "description": "只读：工作间主体（状态、组长、成员、阶段）",
     "mimeType": "application/json", "fn": "_res_workshop"},
    {"uriTemplate": "ac://workshop/{workshop_id}/members", "name": "工作间成员",
     "description": "只读：成员清单（工位/角色/状态/是否已接上）",
     "mimeType": "application/json", "fn": "_res_members"},
    {"uriTemplate": "ac://workshop/{workshop_id}/tasks", "name": "工作间任务",
     "description": "只读：该工作间的任务清单（序号/标题/账本状态）",
     "mimeType": "application/json", "fn": "_res_ws_tasks"},
    {"uriTemplate": "ac://workshop/{workshop_id}/tasks/{seq}", "name": "一条任务",
     "description": "只读：单条任务的证据四步与验收结论",
     "mimeType": "application/json", "fn": "_res_ws_task"},
    {"uriTemplate": "ac://workshop/{workshop_id}/manifest", "name": "产物清单",
     "description": "只读：产物清单 + sha256（不搬文件内容）",
     "mimeType": "application/json", "fn": "_res_manifest"},
]

_PAGE_KEYS = ("workshops", "tasks", "agents", "harnesses", "items", "protocols")


def _cursor_encode(offset: int) -> str:
    return base64.urlsafe_b64encode(str(int(offset)).encode()).decode().rstrip("=")


def _cursor_decode(cursor: str) -> int:
    """游标是**不透明**字符串（base64 的整数偏移）；坏了就报参数错，别当 0 糊过去。"""
    if not cursor:
        return 0
    try:
        pad = "=" * (-len(cursor) % 4)
        return max(0, int(base64.urlsafe_b64decode((cursor + pad).encode()).decode()))
    except Exception as e:                                     # noqa: BLE001 — 非法游标要如实拒
        raise ResourceError(-32602, "cursor 不合法：%s（%s: %s）" % (cursor, type(e).__name__, e))


def list_resources(cursor: str = "") -> dict:
    """协议层列表（分页；条目不足一页时不给 nextCursor）。"""
    start = _cursor_decode(cursor)
    page = RESOURCES[start:start + RESOURCE_PAGE_SIZE]
    out = {"resources": [{k: v for k, v in r.items() if k != "fn"} for r in page]}
    nxt = start + RESOURCE_PAGE_SIZE
    if nxt < len(RESOURCES):
        out["nextCursor"] = _cursor_encode(nxt)
    return out


def list_resource_templates(cursor: str = "") -> dict:
    start = _cursor_decode(cursor)
    page = RESOURCE_TEMPLATES[start:start + RESOURCE_PAGE_SIZE]
    out = {"resourceTemplates": [{k: v for k, v in r.items() if k != "fn"} for r in page]}
    nxt = start + RESOURCE_PAGE_SIZE
    if nxt < len(RESOURCE_TEMPLATES):
        out["nextCursor"] = _cursor_encode(nxt)
    return out


# ── 取数（**白名单投影**，不是原样转发）────────────────────────

def _res_status() -> dict:
    raw = _get("/api/status") or {}
    keep = ("status", "version", "agents_registered", "agents_online",
            "tasks_total", "tasks_active", "harnesses")
    out = {k: raw.get(k) for k in keep if k in raw}
    dh = raw.get("data_health")
    if isinstance(dh, dict):                                   # 只留摘要，不搬细节
        out["data_health"] = {k: dh.get(k) for k in ("empty", "has_history", "note") if k in dh}
    return out


def _res_harnesses(cursor: str = "") -> dict:
    raw = _get("/api/harness/list") or {}
    items = raw.get("harnesses") if isinstance(raw, dict) else raw
    items = items if isinstance(items, list) else []
    keep = ("harness_id", "name", "status", "kind", "access_facts")
    proj = []
    for it in items:
        if not isinstance(it, dict):
            continue
        one = {k: it.get(k) for k in keep if k in it}
        one["has_token"] = bool((it.get("metadata") or {}).get("agent_token")
                               if isinstance(it.get("metadata"), dict) else False)
        proj.append(one)
    return _page({"harnesses": proj, "total": len(proj)})


def _res_agents() -> dict:
    raw = _get("/api/agents") or {}
    items = raw.get("agents") if isinstance(raw, dict) else raw
    return _page({"agents": items if isinstance(items, list) else []})


def _res_workshops() -> dict:
    raw = _get("/api/workshops") or {}
    items = raw.get("workshops") if isinstance(raw, dict) else raw
    items = items if isinstance(items, list) else []
    proj = []
    for it in items:
        if not isinstance(it, dict):
            continue
        one = {k: it.get(k) for k in ("workshop_id", "name", "status", "leader", "member_count") if k in it}
        members = it.get("members")
        if isinstance(members, list):
            one["member_count"] = len(members)
        proj.append(one)
    return _page({"workshops": proj, "total": len(proj)})


def _res_tasks() -> dict:
    raw = _get("/api/tasks") or {}
    items = raw.get("tasks") if isinstance(raw, dict) else raw
    items = items if isinstance(items, list) else []
    proj = []
    for it in items:
        if not isinstance(it, dict):
            continue
        one = {k: it.get(k) for k in ("id", "task_id", "title", "status", "workshop_id",
                                      "assigned_to", "created_at") if k in it}
        proj.append(one)
    return _page({"tasks": proj, "total": len(proj)})


def _res_protocols() -> dict:
    raw = _get("/api/protocols") or {}
    items = raw.get("protocols") if isinstance(raw, dict) else raw
    items = items if isinstance(items, list) else []
    proj = []
    for it in items:
        if not isinstance(it, dict):
            continue
        one = {k: it.get(k) for k in ("id", "name", "layer", "semantics", "status",
                                      "available", "transport") if k in it}
        proj.append(one)
    return _page({"protocols": proj, "total": len(proj)})


def _res_workshop(workshop_id: str) -> dict:
    return _get("/api/workshop/%s" % urllib.parse.quote(workshop_id))


def _res_members(workshop_id: str) -> dict:
    return _get("/api/workshop/%s/members-status" % urllib.parse.quote(workshop_id))


def _res_ws_tasks(workshop_id: str) -> dict:
    return _get("/api/workshop/%s/tasks" % urllib.parse.quote(workshop_id))


def _res_ws_task(workshop_id: str, seq: str) -> dict:
    return _get("/api/workshop/%s/tasks/%s/evidence-view" % (urllib.parse.quote(workshop_id),
                                                             urllib.parse.quote(str(seq))))


def _res_manifest(workshop_id: str) -> dict:
    return _get("/api/workshop/%s/manifest" % urllib.parse.quote(workshop_id))


def _page(payload: dict) -> dict:
    """把清单形态标出来，供 read 决定"翻页还是截断"。"""
    for k in _PAGE_KEYS:
        if isinstance(payload.get(k), list):
            payload["_list_key"] = k
            break
    return payload


# ── resources/read ─────────────────────────────────────────────

def _parse_uri(uri: str):
    """`ac://workshop/ws-1/tasks/3?cursor=x` → (kind, parts, query)。语法不对抛 -32602。"""
    try:
        sp = urllib.parse.urlsplit(uri)
    except Exception as e:                                     # noqa: BLE001 — 坏 URI 要如实拒
        raise ResourceError(-32602, "URI 不合法：%s（%s）" % (uri, e))
    if sp.scheme != "ac":
        raise ResourceError(-32602, "只支持 ac:// 命名空间，收到：%s" % uri)
    kind = sp.netloc or (sp.path.lstrip("/").split("/")[0] if sp.path else "")
    parts = [p for p in sp.path.split("/") if p]
    if not kind:
        raise ResourceError(-32602, "URI 缺少资源类型：%s" % uri)
    query = urllib.parse.parse_qs(sp.query or "")
    return kind, parts, query


def _dump(payload, uri: str, cursor: str) -> tuple[str, bool, int, str]:
    """序列化并按上限处理；返回 (text, truncated, full_size, next_uri)。

    顺序：整份能装下就原样 → 装不下且是清单 ⇒ **先翻页** → 单页仍超 ⇒ 缩小页 → 最后才硬截断。
    """
    limit = _max_bytes()
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    size = len(text.encode("utf-8"))
    if size <= limit:
        return text, False, size, ""

    key = payload.get("_list_key") if isinstance(payload, dict) else None
    if key:
        items = payload.get(key) or []
        start = int(payload.get("_offset") or 0)
        size_n = RESOURCE_PAGE_SIZE
        while size_n >= 1:
            chunk = items[start:start + size_n]
            page = {key: chunk, "total": payload.get("total", len(items)), "_list_key": key}
            ptext = json.dumps(page, ensure_ascii=False, indent=1)
            if len(ptext.encode("utf-8")) <= limit:
                nxt = start + size_n
                next_uri = ""
                if nxt < len(items):
                    next_uri = "%s?cursor=%s" % (uri.split("?")[0], _cursor_encode(nxt))
                return ptext, False, size, next_uri
            size_n //= 2
    # 单条对象本身就超限：硬截断，但把"被截了"和"原来多大"写在正文里
    head = text.encode("utf-8")[: max(1024, limit - 256)].decode("utf-8", "ignore")
    out = json.dumps({"truncated": True, "full_size": size, "uri": uri,
                      "text_head": head,
                      "next": "这个对象单条就超上限；要完整内容请用工具（如 get_task）取"},
                     ensure_ascii=False)
    return out, True, size, ""


def read_resource(uri: str) -> dict:
    """按 URI 取一份只读内容。找不到 → -32002；URI 不对 → -32602。"""
    kind, parts, query = _parse_uri(uri)
    cursor = (query.get("cursor") or [""])[0]
    fn = None
    args: list = []
    if kind == "status" and not parts:
        fn = _res_status
    elif kind == "harnesses" and not parts:
        fn = _res_harnesses
    elif kind == "agents" and not parts:
        fn = _res_agents
    elif kind == "workshops" and not parts:
        fn = _res_workshops
    elif kind == "tasks" and not parts:
        fn = _res_tasks
    elif kind == "protocols" and not parts:
        fn = _res_protocols
    elif kind == "workshop" and len(parts) == 1:
        fn, args = _res_workshop, [parts[0]]
    elif kind == "workshop" and len(parts) == 2 and parts[1] == "members":
        fn, args = _res_members, [parts[0]]
    elif kind == "workshop" and len(parts) == 2 and parts[1] == "tasks":
        fn, args = _res_ws_tasks, [parts[0]]
    elif kind == "workshop" and len(parts) == 2 and parts[1] == "manifest":
        fn, args = _res_manifest, [parts[0]]
    elif kind == "workshop" and len(parts) == 3 and parts[1] == "tasks":
        fn, args = _res_ws_task, [parts[0], parts[2]]
    if fn is None:
        raise ResourceError(-32002, "没有这个资源：%s（可选：%s）" % (
            uri, "、".join(r["uri"] for r in RESOURCES) + " 及 workshop 模板"))

    try:
        payload = fn(*args)
    except ResourceError:
        raise
    except Exception as e:                                     # noqa: BLE001 — 平台侧失败要如实说
        raise ResourceError(-32002, "取资源失败：%s（%s: %s）" % (uri, type(e).__name__, str(e)[:160]))

    payload = _scrub(payload if isinstance(payload, (dict, list)) else {"value": payload})
    # ⚠️ 平台用 HTTP 状态码说"没有"，`_get` **不抛异常**（它把 404 也当正常返回体）。
    #   第一版因此把 `{"error":"工作间不存在","http_status":404}` 当成**内容**发了出去——
    #   客户端会以为"这个资源存在、内容是这句话"（判据第 ⑦ 格抓到的"看起来成功"）。
    if isinstance(payload, dict):
        if int(payload.get("http_status") or 0) >= 400:
            raise ResourceError(-32002, "平台读不到这个资源：%s（HTTP %s %s）" % (
                uri, payload.get("http_status"), payload.get("error") or ""))
        if payload.get("error") and len(payload) <= 3:
            raise ResourceError(-32002, "%s：%s" % (uri, payload.get("error")))
    if isinstance(payload, dict):
        key = next((k for k in _PAGE_KEYS if isinstance(payload.get(k), list)), None)
        if key:
            payload["_list_key"] = key
            # ⚠️ cursor 是 base64 不透明串，**不是整数** —— 第一版直接 int(cursor) 抛 ValueError，
            #    在 HTTP 入口变成 500（判据第 ⑨ 格第一次就抓到了）。
            payload["_offset"] = _cursor_decode(cursor)
            payload["total"] = payload.get("total", len(payload.get(key) or []))
            if payload["_offset"]:
                payload[key] = (payload.get(key) or [])[payload["_offset"]:]
    text, truncated, full_size, next_uri = _dump(payload, uri, cursor)
    # 内部字段（`_list_key` / `_offset`）**一律不外泄**；next/truncated 只在真发生时出现。
    try:
        body = json.loads(text)
        if isinstance(body, dict):
            body.pop("_list_key", None)
            body.pop("_offset", None)
            if next_uri:
                body.setdefault("next", next_uri)
            if truncated:
                body.setdefault("truncated", True)
                body.setdefault("full_size", full_size)
            text = json.dumps(body, ensure_ascii=False, indent=1)
    except Exception:                                          # noqa: BLE001 — 拼不上就保持原样（不丢内容）
        pass
    return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}



# ── MCP JSON-RPC 处理 ─────────────────────────────────────────

def _rpc_error(code: int, message: str, data=None) -> dict:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "error": err}


def _handle(msg: dict) -> dict | None:
    method = msg.get("method", "")
    msg_id = msg.get("id")
    params = msg.get("params", {}) or {}

    if method == "initialize":
        # 版本协商（MCP Lifecycle）：支持客户端请求的版本就回同一个，否则回本服务器最高支持版本
        _requested = str(params.get("protocolVersion") or "").strip()
        _negotiated = _requested if _requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": _negotiated,
                # ★V127（老板 2026-11-04）：如实声明资源能力 —— 两个可选能力我们都**不支持**
                #   （没有事件总线，不假声明 true 然后不实现）。
                "capabilities": {"tools": {},
                                 "resources": {"subscribe": False, "listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None  # 通知无响应
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        # P1-3：只回**本进程启用的组**（默认 api），减少上下文污染
        tools = [{k: v for k, v in t.items() if k != "fn"} for t in active_tools()]
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}}
    # ── V127：只读资源面（三处分支只做分发，取数全在 read_resource 一处）──
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": msg_id,
                "result": list_resources(str(params.get("cursor") or ""))}
    if method == "resources/templates/list":
        return {"jsonrpc": "2.0", "id": msg_id,
                "result": list_resource_templates(str(params.get("cursor") or ""))}
    if method == "resources/read":
        try:
            return {"jsonrpc": "2.0", "id": msg_id,
                    "result": read_resource(str(params.get("uri") or ""))}
        except ResourceError as e:
            return _rpc_error(e.code, e.message, {"uri": params.get("uri")})
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments", {}) or {}
        tool = _TOOL_MAP.get(name)
        if not tool:
            return _rpc_error(-32602, f"未知工具: {name}")
        try:
            out = tool["fn"](args)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": out}], "isError": False},
            }
        except Exception as e:  # noqa: BLE001
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"[平台调用失败] {e}"}],
                    "isError": True,
                },
            }
    return _rpc_error(-32601, f"未知方法: {method}")


def main() -> None:
    """stdio 循环：逐行读 JSON-RPC 请求，逐行回响应。"""
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = _handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
