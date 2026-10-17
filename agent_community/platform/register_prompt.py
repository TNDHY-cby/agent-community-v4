# -*- coding: utf-8 -*-
"""platform/register_prompt：**「把这段话发给你的 AI，让它自己注册」的话术生成器**（V54）。

## 为什么要有它

老板 2026-10-17：「**之前不是通过复制模板发给外端 agent 去注册的吗**」——对，那是接入的**主路径**：
用户复制一段话术贴给外端 AI，AI 自己探测、自己调 `POST /api/harness/register`。
但这轮入口搬到「对接中心」时**只搬了"平台替它填"的表单**，把这条自服务路径落下了；
而话术原本是 `register.html` 里的一段**静态文本**（注册之前没有任何端点生成它）。

## 三条口径（老板 2026-10-17 拍板「做，记得不同协议对应不同模板」）

1. **不同协议 → 不同模板**：`wakeup_method=acp / file_poll / http_api / http / clipboard / mcp`
   各给各的（要几步、配哪种桥、活怎么到它面前、平台能/不能做什么），**不许一套糊所有**；
   不指定协议时给「先自测四项」的通用版 + 六种对照表。
2. **一处真相**：所有"哪几步/哪种桥/等级/边界"都从 `interface_guidance` 取（它已是唯一写死的地方），
   本模块**只做拼装**，不新增第二套口径。
3. **话术必须真能用**：示例 JSON 直接能被 `POST /api/harness/register` 接受（守卫会**真发一遍**）；
   地址走 `platform_address`（不写死端口）；不许出现本机绝对路径。
"""
from __future__ import annotations

from typing import Any

from . import interface_guidance as ig

#: 支持的协议（顺序即界面建议顺序：快的在前，兜底在后）
SUPPORTED = ("http_api", "mcp", "acp", "file_poll", "clipboard", "http")

#: 每种协议的最小注册 JSON 里**除公共字段外**要补的那一项（没有就 None）
_EXTRA = {
    "acp": {"acp_command": "<你自测出来的那条能拉起新实例的命令>"},
    "file_poll": {"wakeup_dir": "<你真正在盯的那个目录（绝对路径）>"},
    "http_api": {"api_base_url": "<你自己的 HTTP 地址，如 http://127.0.0.1:PORT/agent>"},
    # ⚠️ `http`（E 档兜底）注册时**仍要求 acp_command** —— 这是端点既有的口径
    #   （`harness_register.py` 的 `_need_acp`：非 api 的 http 唤醒，桥走兜底轮询，仍要一条叫醒命令）。
    #   话术里必须如实带上，否则 agent 会卡在 400。
    "http": {"acp_command": "<你自测出来的那条能拉起新实例的命令>"},
    "clipboard": {},
    "mcp": {"mcp_server": "<你的 MCP 服务地址或启动命令>"},
}

#: 注册前**必须让 agent 自己确认**的一件事（各协议不同；这是"别问用户技术细节"的落点）
_SELF_CHECK_FIRST = {
    "acp": "先自测：**一条命令能不能把你自己拉起来**（能带参数就带；Windows 路径用正斜杠）。",
    "file_poll": "先自测：**你盯的是哪个目录**（绝对路径），以及**谁在读它**。",
    "http_api": "先自测：**你的 HTTP 服务在哪个端口/路径**，并确认它此刻真能应答。",
    "http": "先自测四项（能不能被命令拉起 / 盯不盯目录 / 有没有常驻 HTTP / 只能人工）；"
            "测不出来就如实选 `clipboard`（一定不会错）。",
    "clipboard": "这一档**没有叫醒命令**：任务由用户手动粘给你（`mode:manual` 即正常完成）。",
    "mcp": "先自测：**你的 MCP 服务能不能被连上并列出工具**。",
}


def _minimal_json(wakeup: str, harness_id: str = "demo-cli-laptop-ui") -> dict:
    """最小可注册 JSON（守卫会拿它**真发一次** `POST /api/harness/register`）。

    ⚠️ 名字**要能看出是谁**（老板 2026-10-17：「不能只是 harness1，看了都不知道是谁」）：
    `harness_id` 用「工具-机器/账号-角色」的可读短横线名，`harness_name` 给人看（可中文）。
    示例一律用**虚拟名**（对外文档不许出现真实产品名，规则 19）。
    """
    base = {
        "harness_id": harness_id,
        "harness_name": "示例CLI @ 笔记本（前端）",
        "harness_type": "desktop-agent",
        "wakeup_method": wakeup,
        "ai": {"model_name": "示例模型"},
    }
    base.update(_EXTRA.get(wakeup) or {})
    return base


def build(wakeup_method: Any = "", *, platform_url: str = "", harness_id: str = "",
          fmt: str = "text") -> dict:
    """生成话术。`fmt="json"` 时同样返回 `prompt`，并附结构化字段供界面分块渲染。"""
    wake = ig.normalize(wakeup_method)
    known = wake in ig.BY_WAKEUP
    g = ig.for_wakeup(wake)
    m = ig.match_for(wake) if known else {}
    base = (platform_url or "").rstrip("/") or "<平台地址>"
    hid = harness_id or "demo-cli-laptop-ui"

    steps = g.get("steps") or {}
    step_lines = [f"  · {ig.STEP_LABEL[k]}：{v}" for k, v in steps.items()]
    bridge = m.get("primary_bridge") if known else None

    head = []
    if known:
        head += [f"【你的接口：{g['label']}】{g['tier_hint']}",
                 "【照这一份做】你的接口已经确定，按下面这份做就行，别照别的接口的流程抄。"]
    else:
        head += ["【你的接口：还不知道？】先自测四项再往下走（别猜）：",
                 "  ① 能不能被一条命令拉起？→ 能 ⇒ acp",
                 "  ② 是不是盯着某个目录读任务？→ 是 ⇒ file_poll",
                 "  ③ 有没有一个常驻 HTTP 地址能收任务？→ 有 ⇒ http_api",
                 "  ④ 是不是只能人工粘贴？→ 是 ⇒ clipboard",
                 "  ⑤ 你是一个 MCP 服务？→ mcp；⑥ 都测不出来 → http（兜底：平台只落盘、不叫醒）"]

    body = [
        "",
        "━━ 〇、先给自己起个名字（登记表上要能一眼看出「是谁」）━━",
        "  · harness_id：机器用。全小写 + 短横线，建议「工具-机器/账号-角色」，例如 demo-cli-laptop-ui；",
        "  · harness_name：给人看的显示名，可中文/空格，例如「示例CLI @ 笔记本（前端）」；",
        "  · 不要用 harness1 / agent-a 这种看不出人是谁的名字 —— 以后工作间点名、看板、审计全靠它。",
        "",
        f"━━ 一、把你自己登记进来（POST {base}/api/harness/register，JSON body）━━",
        "最小可用示例（把示例值换成你自己的；缺的字段平台会告诉你缺哪个）：",
        _json_pretty(_minimal_json(wake if known else "clipboard", hid)),
        "",
        "━━ 二、你的接口要做哪几步 ━━",
        *step_lines,
        "",
        f"  · 活怎么到你面前：{g.get('inbox')}",
        f"  · 你要配的桥：{(bridge) if bridge else '不需要桥（配了就是多余链路）'}",
        f"  · 怎么持久：{g.get('persist_how')}",
        "",
        "━━ 三、自检（没跑过就不许说通了）━━",
        f"  1) 先拉机器可读简报：GET {base}/api/protocol-brief（平台是什么、你该做什么）",
        f"  2) 注册后自检：POST {base}/api/harness/{hid}/verify",
        f"  3) 卡住了：POST {base}/api/assistant/chat（第二通道，直接问平台 AI，别猜）",
        "",
        "━━ 四、你这一档的边界（平台能做 / 不能做，如实说）━━",
        *[f"  ✓ {x}" for x in (g.get("platform_can") or [])],
        *[f"  ✗ {x}" for x in (g.get("platform_cannot") or [])],
        "",
        "━━ 五、先自测的一件事 ━━",
        f"  {_SELF_CHECK_FIRST.get(wake, _SELF_CHECK_FIRST['http'])}",
        "",
        "━━ 六、纪律 ━━",
        "  · 不要编字段：平台不认识的字段会被记下来但不会生效；",
        "  · 不要写死别人的端口/路径：那是你自己的坐标，填你自己的；",
        "  · 注册完回报一句「已注册 harness_id=…」即可，其余平台会自己探。",
    ]

    # ★2026-10-17（老板看着页面指出两处）：
    #   ① 开头不能说「注册为平台的**员工**（harness）」—— 概念错位：
    #      **登记的是 harness（一个可被派活的接入实例）**，成为某个工作间的**员工/成员**是后面选人那一步；
    #   ② 纯文本话术里**不许漏 markdown 星号**（页面上会原样显示 `**`）：统一在末尾剥掉。
    intro = ["请把你自己登记为「外端Agent生产合作社（External Agent Community）」平台上的 **harness**"
             "（＝一个可被派活的接入实例）。",
             "注意：harness ≠ 员工 —— 登记只是让平台认得你、能给你派活；"
             "成为某个工作间的「员工/成员」是之后在工作间里选人时才发生的。",
             f"平台地址：{base}",
             "",
             *head]
    prompt = "\n".join(intro + body).replace("**", "")
    return {
        "ok": True,
        "prompt": prompt,
        "wakeup_method": wake,
        "known_protocol": known,
        "protocol_label": g.get("label"),
        "platform_url": base,
        "minimal_json": _minimal_json(wake if known else "clipboard", hid),
        "steps": steps,
        "bridge_kind": bridge,
        "supported_protocols": list(SUPPORTED),
        "source": "interface_guidance",
        "note": ("话术里的步数/桥型/边界都来自 `interface_guidance`（唯一真相）；"
                 "换协议就换模板——**不要用 A 协议的流程去接 B 协议的 agent**。"),
    }


def _json_pretty(d: dict) -> str:
    import json
    return json.dumps(d, ensure_ascii=False, indent=2)


def text(wakeup_method: Any = "", *, platform_url: str = "", harness_id: str = "") -> str:
    return build(wakeup_method, platform_url=platform_url, harness_id=harness_id)["prompt"]
