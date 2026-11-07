# -*- coding: utf-8 -*-
"""`platform/protocol_plan`：**多协议选中之后到底能干什么**（老板 2026-11-06「做多协议选中适合的功能」）。

## 为什么要有这个东西

接入向导的协议已经能多选了（前端 `091f4cd`），但多选本身不产生任何约束：
必填项只按"首选"算、`transports` 由前端自己拼、派活走哪条路没人回答。
本模块把"选中的集合"算成一份**可执行计划**：

| 回答的问题 | 看哪个字段 |
|---|---|
| 每个协议现在能不能用（缺什么） | `picked[].usable` / `missing` |
| 这套组合整体能干什么 | `capabilities`（派活/收回执/读会话/它能调我们/能自动叫醒/要人） |
| 派活到底走哪条路 | `route.dispatch_via` + `why` + `honest_note` |
| 两个协议是不是重复了 | `duplicates` |
| 用户首选选错了怎么办 | `first_advice` + `warnings`（**只建议，不改用户的选择**） |
| 每个协议各自怎么自验 | `probes` |
| 注册该发什么 | `register_payload`（前端别自己拼，否则又会撞上 `TransportType` 枚举） |

## 三条纪律

1. **能力事实只从 `protocol_tree` 取**（`role` / `can_*` / `transport`）——本模块只做算法，不另写一份矩阵；
2. **只读**：纯函数，不写盘、不改档案；
3. **不假装**：`can_dispatch=false` 的协议一定在 `why` 里给出人话原因（MCP 就是"工具面没有派活语义"）。

## 两种"协议 id"别混（这是 2026-11-06 那个 400 的根因）

- **向导层 id**（界面语言）：`a2a` / `mcp` / `http_api` / `acp` / `file_poll` / `clipboard` / `http`
- **传输层** `TransportType`（协议实现语言）：`ws` / `http` / `sse` / `pipe` / `grpc` / `mcp` / `a2a`

`http_api` / `acp` / `file_poll` / `clipboard` 在传输层**没有对应值** ⇒ 只记进 `protocols`，
`transports` 只放真能表达的（见 `protocol_tree.TRANSPORT_BY_ID`）。
"""
from __future__ import annotations

from . import protocol_tree as tree

#: 注册时必须有的字段（每个协议都列，合并时去重保序）
BASE_REQUIRED = ("harness_id", "ai.model_name")

#: 人话：某个能力"有 / 部分 / 没有"
_YES, _PARTIAL, _NO, _TOOL = "yes", "partial", "no", "tool"


def _s(v) -> str:
    return str(v or "").strip().lower()


def _missing(req: list, fields: dict) -> list:
    """按 `fields` 里有没有真值算缺项（`ai.model_name` 支持嵌套字典）。"""
    out = []
    for key in req:
        if key == "ai.model_name":
            ai = fields.get("ai") if isinstance(fields.get("ai"), dict) else {}
            ok = bool(_s(ai.get("model_name")))
        else:
            ok = bool(_s(fields.get(key)))
        if not ok:
            out.append(key)
    return out


def _label(pid: str) -> str:
    return str((tree.BY_ID.get(pid) or {}).get("label") or pid)


def declared_ids(info) -> list:
    """从一个接入档案里**读出它到底声明了哪些向导层协议**（首选在前）。

    这是"多协议"的读取口径（一处真相）：`protocols` 字段 > `wakeup_method` > `transports`。
    老档案没有 `protocols`（2026-11-06 之前注册的）也能读出来。
    """
    ids: list = []
    for x in (getattr(info, "protocols", None) or []):
        v = _s(x)
        if v and v in tree.PROTOCOL_IDS and v not in ids:
            ids.append(v)
    wm = _s(getattr(getattr(info, "wakeup_method", None), "value",
                    getattr(info, "wakeup_method", "")))
    if wm in tree.PROTOCOL_IDS and wm not in ids:
        ids.insert(0, wm)
    for t in (getattr(info, "transports", None) or []):
        tv = _s(getattr(t, "value", t))
        # 传输层的 a2a/mcp/http 能映射回向导 id（http 也可能是 http_api 来的，这里不猜）
        if tv in tree.PROTOCOL_IDS and tv not in ids:
            ids.append(tv)
    return ids


def script_key(info) -> str:
    """视觉脚本的**定位键**（标题最精确，其次第一个关键词）。没有就空串。"""
    sc = getattr(info, "activation_script", None) or {}
    if not isinstance(sc, dict):
        return ""
    key = str(sc.get("title") or "").strip()
    if key:
        return key
    for k in (sc.get("keywords") or []):
        s = str(k).strip()
        if s:
            return s
    return ""


def script_ready(info) -> tuple:
    """它档案里的**视觉脚本**能不能用：返回 `(ok, 人话)`。

    ★2026-11-06（老板：「clipboard 是不是可以用视觉脚本，记得说明」）：
    `clipboard` 的 `can_be_woken=False` 是**开箱事实**（没脚本叫不醒）；
    一旦存了脚本（标题/关键词 + 一句要送的话），平台就能自动把话送进它的窗口。
    **但"送进去了" ≠ "它执行了"** —— 这条边界写回给调用方，不许当成功。
    """
    sc = getattr(info, "activation_script", None) or {}
    if not isinstance(sc, dict):
        return False, "档案里的视觉脚本结构不对"
    text = str(sc.get("text") or "").strip()
    key = str(sc.get("title") or "").strip()
    if not key:
        kws = [str(k).strip() for k in (sc.get("keywords") or []) if str(k).strip()]
        key = kws[0] if kws else ""
    if not text:
        return False, "它的档案里还没有视觉脚本（`activation_script.text` 为空）"
    if not key:
        return False, "视觉脚本里既没有窗口标题也没有关键词，平台不知道该找哪个窗口"
    verified = bool(sc.get("verified"))
    return True, ("视觉脚本可用（定位键 `%s`；%s）"
                  % (key, "**自证过**" if verified else "**还没自证过** —— 投递可能定位不到窗口"))


def dispatch_route(info) -> tuple:
    """派活该走哪条路。返回 `(route, why)`。

    `route` 取值：`a2a` / `http` / `acp` / **`visual`** / `none`。

    - `visual`：它只有「桌面型 / 落盘 / 人工」这类接入，**但档案里有可用的视觉脚本**
      ⇒ 平台能把"看下 T-003"这句话**自动送进它的窗口**；**结果仍核不到**（要人看产物）；
    - `none`：连视觉脚本都没有（或只有工具面 MCP —— 协议里没有派活语义）。

    ★这条函数**代替**过去"只看 transports 里有没有 a2a"的判断，多协议时按
      `protocol_tree.DISPATCH_ORDER` 取第一个真能派活的；一个都不能派活就说清为什么。
    """
    ids = declared_ids(info)
    for pid in tree.DISPATCH_ORDER:
        if pid in ids:
            if pid == "a2a":
                return "a2a", "它声明了 A2A（任务语义），按 `tasks/send` 派活"
            if pid in ("http_api", "http"):
                return "http", "它声明了 %s（平台直接 POST）" % _label(pid)
            if pid == "acp":
                return "acp", "它声明了 ACP（平台拉起子进程，按会话轮次推进）"
    # ── 桌面型 / 落盘 / 人工：有视觉脚本就能"把话送进去"，但只到"送进去"为止 ──
    if any(x in ids for x in ("clipboard", "file_poll", "http")):
        ok, why = script_ready(info)
        if ok:
            return "visual", ("这是桌面型/人工接入，但%s —— 平台能把「看下 T-003」这句话"
                              "**自动送进它的窗口**；**它有没有执行核不到**，结果要人看产物" % why)
        return "none", ("它声明的是「%s」——这类接入平台叫不醒也派不出去，而且%s；"
                        "先给它在「脚本激活」工作台做一份视觉脚本"
                        % (" / ".join(_label(x) for x in ids
                                      if x in ("clipboard", "file_poll", "http")), why))
    if "mcp" in ids:
        return "none", ("它只声明了 MCP —— MCP 是**工具面**（它来调平台的工具），"
                        "协议里没有'派活'语义；要派活得让它再提供一个能派活的协议，或人工转达")
    return "none", "它没有声明任何能派活的协议"


def normalize_register_body(body: dict) -> dict:
    """把界面发来的注册体**规整成 `HarnessInfo` 认得的形状**（修 2026-11-06 那个 400）。

    做三件事，全部是"翻译"不是"改语义"：

    1. 把向导层 id 收进 `protocols`（首选在最前）；
    2. `transports` **只放 `TransportType` 真有的值**（前端原先直接把向导 id 塞进来，
       于是 `http_api`/`acp`/`file_poll`/`clipboard` 一律 400）；
    3. `preferred_transport` 同样翻译；翻译不了就**不写**（不硬塞语义不同的值）。
    """
    if not isinstance(body, dict):
        return body
    from .protocol import TransportType

    legal = {t.value for t in TransportType}
    raw_t = [_s(x) for x in (body.get("transports") or []) if _s(x)]
    # ★只有当 body **没有** `protocols` 时才从 `transports` 反推协议（老客户端兼容）。
    #   为什么：`transports` 里的 `http` 既可能是"HTTP 兜底"这个向导协议，也可能是
    #   `http_api` 映射来的传输层值 —— 两者分不开。一旦前端按计划带了 `protocols`，
    #   就以 `protocols` 为准，否则会把 `http_api` 偷偷读成"还选了 http 兜底"（实测踩过）。
    infer_from_transports = not (body.get("protocols") or [])
    picked: list = []
    src = list(body.get("protocols") or []) + [body.get("wakeup_method")]
    if infer_from_transports:
        src += raw_t
    for x in src:
        v = _s(x)
        if v and v in tree.PROTOCOL_IDS and v not in picked:
            picked.append(v)
    # 首选（wakeup_method）必须在最前：`declared_ids` 也按这个顺序读
    wm = _s(body.get("wakeup_method"))
    if wm in picked:
        picked.remove(wm)
        picked.insert(0, wm)
    if picked:
        body["protocols"] = picked

    transports: list = []
    for pid in picked:
        t = tree.TRANSPORT_BY_ID.get(pid)
        if t and t not in transports:
            transports.append(t)
    for t in raw_t:                      # 前端显式给的、本身就是合法传输层的值要保留
        if t in legal and t not in transports:
            transports.append(t)
    if picked:
        # ★必须**显式覆盖**：只给 acp/file_poll/clipboard 时上面算出来是空列表，
        #   若不覆盖就会保留前端原始值（`["acp","file_poll"]`）⇒ 仍然 400（实测踩过）。
        #   空列表是**事实**：这些协议在传输层表达不了，别硬塞。
        body["transports"] = transports

    pref = _s(body.get("preferred_transport"))
    if pref:
        mapped = pref if pref in legal else tree.TRANSPORT_BY_ID.get(pref)
        if mapped:
            body["preferred_transport"] = mapped
        else:
            body.pop("preferred_transport", None)
    return body


def build_plan(ids: list, fields: dict | None = None) -> dict:
    """算计划。`ids` 是向导层协议 id；未知 id 不在这里拒（路由层 400），这里只做交集。"""
    fields = dict(fields or {})
    picked_ids: list = []
    for x in (ids or []):
        v = _s(x)
        if v in tree.PROTOCOL_IDS and v not in picked_ids:
            picked_ids.append(v)

    picked = []
    merged: list = list(BASE_REQUIRED)
    warnings: list = []
    for pid in picked_ids:
        p = tree.BY_ID.get(pid) or {}
        req = list(dict.fromkeys(list(BASE_REQUIRED) + list(p.get("required") or [])))
        for k in req:
            if k not in merged:
                merged.append(k)
        miss = _missing(req, fields)
        picked.append({
            "id": pid,
            "label": _label(pid),
            "role": p.get("role") or "",
            "role_label": tree.ROLE_LABELS.get(str(p.get("role") or ""), ""),
            "can_be_woken": bool(p.get("can_be_woken")),
            "needs_bridge": bool(p.get("needs_bridge")),
            "can_dispatch": bool(p.get("can_dispatch")),
            "can_receive_result": p.get("can_receive_result") or _NO,
            "can_call_our_tools": bool(p.get("can_call_our_tools")),
            "can_read_session": p.get("can_read_session") or _NO,
            "transport": tree.TRANSPORT_BY_ID.get(pid),
            # ★视觉脚本：这类协议（桌面型/人工）**配了脚本**就能被平台自动投递一句话
            #   （不等于能派活，也不等于能核实结果；见 protocol_tree.VISION）
            "visual_script": bool(p.get("visual_script")),
            "required": req,
            "missing": miss,
            "usable": not miss,
            "why": ("" if not miss else "缺 %s；补上才算这套组合里的这一条能用"
                    % "、".join(miss)),
        })

    def _any(key: str, want=True) -> bool:
        return any(bool(p.get(key)) is want for p in picked)

    # ★能力只按**能用**的协议汇总（缺必填的那条等于现在用不了；把它算进来就是假能力）
    usable = [p for p in picked if p["usable"]]
    if picked and not usable:
        warnings.append("这套组合里**没有一条现在能用**（都缺必填项）：%s"
                        % "、".join(p["label"] + "（缺 " + "、".join(p["missing"]) + "）"
                                   for p in picked))
    else:
        for p in picked:
            if not p["usable"]:
                warnings.append("「%s」现在不可用：缺 %s（其余协议不受影响）"
                                % (p["label"], "、".join(p["missing"])))
    capabilities = {
        "can_dispatch": any(p["can_dispatch"] for p in usable),
        "can_receive_result": any(p["can_receive_result"] == _YES for p in usable),
        "can_read_session": any(p["can_read_session"] in (_YES, _TOOL) for p in usable),
        "can_call_our_tools": any(p["can_call_our_tools"] for p in usable),
        "can_wake_automatically": any(p["can_be_woken"] for p in usable),
        # ★这类接入**配了视觉脚本**后，平台能自动把话送进窗口（但只能证明"送进去了"）
        "can_wake_with_script": any(p["role"] in ("manual", "bridge") for p in usable),
        "needs_human": any(p["role"] == "manual" for p in picked),
        "can_be_woken": _any("can_be_woken"),
    }
    capabilities["closed_loop"] = bool(capabilities["can_dispatch"]
                                      and capabilities["can_receive_result"])

    # ── 派活路线：按 DISPATCH_ORDER；首选若能派活就优先用首选 ─────────────
    preferred = picked_ids[0] if picked_ids else ""
    dispatch_via, why = "none", "没有选中任何能派活的协议"
    if capabilities["can_dispatch"]:
        order = list(tree.DISPATCH_ORDER)
        if preferred in order:
            order.remove(preferred)
            order.insert(0, preferred)
        for pid in order:
            if pid in picked_ids:
                if pid == "a2a":
                    dispatch_via, why = "a2a", "按 A2A `tasks/send` 派活（首选里能派活的就是它）"
                elif pid in ("http_api", "http"):
                    dispatch_via, why = "http", "按普通 HTTP POST 派活（%s）" % _label(pid)
                elif pid == "acp":
                    dispatch_via, why = "acp", "平台拉起子进程，按会话轮次推进"
                break
    else:
        why = "；".join(p["why"] or ("%s：不能派活" % p["label"]) for p in picked) or \
              "这套组合里没有能派活的协议"
        if capabilities["can_wake_with_script"]:
            # ★老板 2026-11-06：「clipboard 是不是可以用视觉脚本，记得说明」
            why = ("这类接入靠**视觉脚本**：在「脚本激活」工作台存了脚本"
                   "（窗口标题/关键词 + 一句要送的话）后，平台能把「看下 T-003」**自动送进它的窗口**；"
                   "**但只能证明话送进去了，它有没有执行核不到** —— 结果仍要人看产物或它自己回报。")
            warnings.append("「人工粘贴 / 落盘」这一档：**配了视觉脚本**平台就能自动投递一句话"
                            "（不是只能手点），但**投递成功 ≠ 任务完成**。")
    # MCP 的定位必须常驻提示（选了它不等于能派活）：它只是工具面
    if any(p["id"] == "mcp" for p in picked):
        if dispatch_via != "none":
            warnings.append("MCP 只用于调它的工具（工具面）；派活走「%s」，**不走 MCP**"
                            % (dispatch_via if dispatch_via == "a2a" else _label(dispatch_via)))
        else:
            warnings.append("MCP 是工具面：平台**不能**给它派活，只用于调它的工具。"
                            "要派活得再加一个 A2A / HTTP_api / ACP。")
    if picked_ids and not any(p["can_dispatch"] for p in picked):
        if capabilities["can_wake_with_script"]:
            warnings.append("这套组合整体**不能按任务语义派活**；能做的只是"
                            "「把话送进窗口」（视觉脚本）或「落盘等它来取」——"
                            "任务执行与结果仍需人盯。")
        else:
            warnings.append("这套组合整体无法派活（只有工具面 / 落盘 / 人工），"
                            "任务只能靠人去它那边说一句。")

    honest = []
    if dispatch_via != "none" and preferred and preferred != dispatch_via:
        _pref_dispatch = bool((tree.BY_ID.get(preferred) or {}).get("can_dispatch"))
        if not _pref_dispatch:
            honest.append("首选是「%s」（它不能派活），派活会走「%s」"
                          % (_label(preferred), _label(dispatch_via)
                             if dispatch_via in tree.PROTOCOL_IDS else dispatch_via))
    if dispatch_via == "none" and capabilities["can_call_our_tools"]:
        honest.append("它可以来调平台的工具，但平台不能反过来给它派活")

    # ── 同层重复（提醒，不是错误）────────────────────────────────────────
    duplicates = []
    if {"http", "http_api"} <= set(picked_ids):
        duplicates.append({"kind": "same_channel", "ids": ["http", "http_api"],
                           "why": "两个都是 HTTP 通道：`http` 只落盘、`http_api` 真推",
                           "suggestion": "选一个作首选即可；另一个只会被当成备用"})
    if {"file_poll", "clipboard"} <= set(picked_ids):
        duplicates.append({"kind": "same_class", "ids": ["file_poll", "clipboard"],
                           "why": "两套都要桥/人工，部署成本翻倍",
                           "suggestion": "通常只留一个"})
    if {"acp", "http_api"} <= set(picked_ids):
        duplicates.append({"kind": "two_dispatch", "ids": ["acp", "http_api"],
                           "why": "两种都能派活，但通道完全不同",
                           "suggestion": "只留你真正会用的那条；另一条留着当备用也行"})

    # ── 首选建议（只建议，不改用户选择）──────────────────────────────────
    advice = {}
    if picked_ids:
        if preferred not in tree.DISPATCH_ORDER and capabilities["can_dispatch"]:
            cand = next((p for p in picked_ids if p in tree.DISPATCH_ORDER), "")
            advice = {"recommended": cand, "why": "首选「%s」不能派活；「%s」能派活"
                      % (_label(preferred), _label(cand))}
        else:
            advice = {"recommended": preferred, "keep": True,
                      "why": "首选「%s」可以派活，保持不变" % _label(preferred)}

    probes = [{"id": p["id"], "how": str((tree.BY_ID.get(p["id"]) or {}).get("test_how") or ""),
               "endpoint": "/api/docking/harness/{harness_id}/test-now", "method": "POST"}
              for p in picked]

    payload = {}
    if picked_ids:
        body = normalize_register_body({"wakeup_method": preferred,
                                        "protocols": picked_ids,
                                        "transports": [x for x in picked_ids
                                                       if tree.TRANSPORT_BY_ID.get(x)]})
        payload = {"wakeup_method": preferred,
                   "protocols": picked_ids,
                   "transports": list(body.get("transports") or []),
                   "preferred_transport": (body.get("preferred_transport")
                                           or tree.TRANSPORT_BY_ID.get(preferred) or "")}

    return {
        "ok": True,
        "ids": picked_ids,
        "picked": picked,
        "merged_required": merged,
        "missing_all": list(dict.fromkeys([m for p in picked for m in p["missing"]])),
        "capabilities": capabilities,
        "route": {"dispatch_via": dispatch_via, "why": why,
                  "honest_note": "；".join(honest)},
        "duplicates": duplicates,
        "first_advice": advice,
        "probes": probes,
        "register_payload": payload,
        "warnings": warnings,
        "how_to_read": ("这套组合的账单：`capabilities` 说整体能干什么，`route` 说派活走哪条路，"
                        "`picked[].missing` 说每条还缺什么；MCP 只用于平台调它的工具，"
                        "**不代表**平台能给它派活。"),
    }
