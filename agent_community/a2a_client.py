# -*- coding: utf-8 -*-
"""A2A 公司级客户端 — 公司 B 侧（远程/双实例模拟）访问公司 A 的 9104。

M-2 公司间对等协作演示：公司 B 持本公司 company token（HMAC(secret, "company:"+company_id)，
与成员 token 同族同库），经 A2A 协议访问公司 A 的 9104：
  1. 读 Agent Card（公开，无需凭据）→ 确认 A 的公司身份与鉴权声明；
  2. tasks/send 发任务书 → A 平台创建工作间；
  3. tasks/get 回查任务状态；
  4. message/send 追加讨论/回执消息。

设计约束：
- 零第三方依赖（仅标准库 urllib/json），与 a2a_server.py 对称、可单文件分发；
- 密钥只在本端持有：token 由 A 侧平台 `identity.issue_company_token(company_id)` 签发后
  通过安全通道（Tailscale/人工）交付 B 侧，B 侧**只持有 token 不持有 secret**；
- 本文件不引入平台内部模块（防 A 侧代码泄漏到 B 侧），token 直接以参数/环境变量注入。

用法：
    python -m agent_community.a2a_client \
        --company co-remote-b --token <公司 token> --url http://127.0.0.1:9104 \
        send "公司 B 向 A 发送的任务书标题"
    python -m agent_community.a2a_client ... get <task_id>
    python -m agent_community.a2a_client ... msg <task_id> "回执：已收到"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

# 环境变量注入（--token 优先；便于远端无交互场景）
# ★2026-10-14（可移植性）：对端地址从环境读，端口默认值取自一处真相
DEFAULT_URL = os.environ.get("AC4_PEER_URL") or (
    "http://127.0.0.1:" + (os.environ.get("AC4_A2A_PORT") or "9104"))


class A2AError(RuntimeError):
    """A2A 调用失败（含 HTTP 401/403 鉴权拒绝与 JSON-RPC error）。"""


def _request(url: str, body: dict | None, token: str | None,
             timeout: int = 30) -> tuple[int, dict]:
    """发 POST 到 A2A JSON-RPC 端点；返回 (http_status, parsed_json)。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return resp.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        try:
            parsed = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            parsed = {"error": str(e)}
        return e.code, parsed
    except Exception as e:  # noqa: BLE001  网络层异常
        raise A2AError(f"网络调用失败: {e}") from e


def _request_any_path(url: str, body: dict, token: str | None,
                      timeout: int = 30) -> tuple[int, dict, str]:
    """POST 到 A2A 端点，**容忍端点写法差异**（2026-11-01 真对端实测）。

    ★为什么要有它：A2A 规范没规定 RPC 端点必须带尾斜杠，实现之间真的不一样 ——
      · 我们自己的 `a2a_server` 收以 `/` 结尾的路径；
      · `@hanphone/dsh-a2a` 的入站 server **只认基址本身**（带尾斜杠直接 404 Not Found）。
    只试一种写法就会把"对端没问题"误判成"对端不通"。所以：先试基址，404 再试基址+`/`。
    返回 `(status, parsed, 实际用过的 url)`，便于把真实地址写进错误信息（不让人猜）。
    """
    base = url.rstrip("/")
    tried: list[str] = []
    last: tuple[int, dict] | None = None
    for candidate in (base, base + "/"):
        if candidate in tried:
            continue
        tried.append(candidate)
        status, resp = _request(candidate, body, token, timeout)
        if status == 404:
            last = (status, resp)
            continue
        return status, resp, candidate
    status, resp = last or (404, {"error": "not found"})
    raise A2AError("A2A 端点不存在（试过 %s）" % " ｜ ".join(tried))


#: Agent Card 的候选路径 —— **顺序有讲究**：
#: ① `agent-card.json` 是 A2A v1.0 的标准路径（我们自己服务端报的就是它）；
#: ② `agent.json` 是 v0.2 时代的路径，**第三方实现里非常常见**；
#: ③ `agent-card.json` **挂在端点基址上**（不套 `.well-known/`）—— 这是
#:    `@hanphone/dsh-a2a` 的做法（`cardPath = /a2a/<id>/agent-card.json`），
#:    2026-11-01 真对端实测：只试 ①② 会把一个**完全正常**的 A2A agent 判成"对端不通"。
#:    规范没禁止这种挂法，所以这里当第三条候选（前两条命中就不会走到它）。
CARD_PATHS = ("/.well-known/agent-card.json", "/.well-known/agent.json", "/agent-card.json")


def discover_card(url: str, timeout: int = 10) -> dict:
    """读 A2A Agent Card（公开，无需凭据）。

    按 `CARD_PATHS` **依次尝试**（标准路径优先，v0.2 兼容路径兜底）；
    全部失败时把**试过哪些路径**和每条的错误一起抛出来，不让人去猜。
    """
    tried = []
    last_err = ""
    for path in CARD_PATHS:
        card_url = url.rstrip("/") + path
        req = urllib.request.Request(card_url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8", "replace"))
            if path != CARD_PATHS[0]:
                # 用兼容路径读到了：留痕（对端是 v0.2 口径，或把卡挂在端点基址上）
                print("[a2a] 用兼容路径读到 Agent Card：%s" % path, flush=True)
            return body
        except Exception as e:                               # noqa: BLE001 — 换下一个路径
            tried.append("%s -> %s: %s" % (path, type(e).__name__, str(e)[:80]))
            last_err = str(e)
    raise A2AError("读 Agent Card 失败（试过 %d 个路径）：%s"
                   % (len(tried), " ｜ ".join(tried))) from A2AError(last_err)


#: v0.2 方法名 → v1.0 方法名（同一件事，两代规范各叫各的）。
#: ★2026-11-01 真对端实测（`@hanphone/dsh-a2a` 0.5.2）：它只认 v1.0 的方法名，
#:   `tasks/send` 会被回 JSON-RPC `-32601 Unknown method`；而我们自己的 `a2a_server`
#:   只认 v0.2 的 `tasks/send`。⇒ 先按 v0.2 发（对自家与老实现最稳），
#:   **只在明确"方法不认识"时**才换 v1.0 重发，且把"换过"写进错误信息。
_V1_METHODS = {
    "tasks/send": "SendMessage",
    "message/send": "SendMessage",
    "tasks/get": "GetTask",
    "tasks/cancel": "CancelTask",
}
#: "方法不认识"一族：JSON-RPC 标准是 -32601；另有实现回 -32600 / -32004（unsupported operation）
_UNKNOWN_METHOD_CODES = (-32601, -32600, -32004)


def _v1_params(method: str, params: dict) -> dict:
    """把 v0.2 的参数形状翻成 v1.0：`taskId`→`id`；SendMessage 带 `configuration`。

    `returnImmediately=True`：**不让平台卡在"等它跑完"上** —— 先拿到 task id，
    之后用 `GetTask` 回查状态（派活接口不该被对端的一次长任务拖死）。
    """
    if method in ("tasks/send", "message/send"):
        msg = params.get("message") or {"role": "user", "parts": []}
        return {"message": msg, "configuration": {"returnImmediately": True}}
    out: dict = {"id": params.get("taskId") or params.get("id") or ""}
    if "historyLength" in params:
        out["historyLength"] = params["historyLength"]
    return out


def rpc(url: str, method: str, params: dict, token: str | None,
        timeout: int = 30) -> dict:
    """发一条 A2A JSON-RPC 请求，校验传输与协议两层错误。

    ★2026-11-01（真对端联调）：两处实现差异都在这里兜住 ——
      ① 端点写法（带不带尾斜杠）交给 `_request_any_path`；
      ② 方法名（v0.2 `tasks/send` vs v1.0 `SendMessage`）按错误码回退。
    """
    msg_id = uuid.uuid4().hex[:12]

    def _post(m: str, p: dict) -> tuple[int, dict, str]:
        body = {"jsonrpc": "2.0", "id": msg_id, "method": m, "params": p}
        return _request_any_path(url, body, token, timeout=timeout)

    status, resp, used = _post(method, params)
    if status in (401, 403):
        raise A2AError(f"鉴权被拒（HTTP {status}）：{resp.get('error', '')}；"
                       f"请确认 token 为对端签发的那一个（见 Agent Card schemes）")
    if status >= 400:
        raise A2AError(f"HTTP {status}（{used}）: {resp}")
    err = resp.get("error")
    if err is None:
        return resp.get("result", {})

    code = err.get("code")
    alt = _V1_METHODS.get(method)
    if alt and code in _UNKNOWN_METHOD_CODES:
        status2, resp2, used2 = _post(alt, _v1_params(method, params))
        if status2 in (401, 403):
            raise A2AError(f"鉴权被拒（HTTP {status2}，v1.0 `{alt}`）：{resp2.get('error', '')}")
        if status2 >= 400:
            raise A2AError(f"HTTP {status2}（{used2}，v1.0 `{alt}`）: {resp2}")
        err2 = resp2.get("error")
        if err2 is not None:
            raise A2AError(
                f"JSON-RPC error {err2.get('code')}: {err2.get('message')}"
                f"（对端不认 v0.2 `{method}`，已改试 v1.0 `{alt}` 仍失败；端点 {used2}）")
        return resp2.get("result", {})
    raise A2AError(f"JSON-RPC error {code}: {err.get('message')}")


def send_task(url: str, title: str, token: str | None, task_id: str = "",
              timeout: int = 60, context_id: str = "") -> dict:
    """A2A tasks/send：向对端发任务书（无 taskId=新建任务）。

    `context_id`：A2A 的**上下文/会话标识**（2026-11-01 真机实测过的行为）——
      · 不传 ⇒ 对端**自己生成**一个（每次都是新上下文）；
      · 传同一个 ⇒ 对端回显同一个（协议上把它当"同一上下文"）；
      · 传新的 ⇒ 新上下文。
    ⚠️ **边界（别对用户过度承诺）**：这只控制"A2A 的上下文标识"；对端会不会把它映射到
       **同一个客户端会话窗口**，是**对端实现**的事 ——
       实测：同一个 contextId 的两条任务，各自 `history` 仍互不交叉。
    """
    params: dict = {"message": {"role": "user", "parts": [{"text": title}]}}
    if context_id:
        params["message"]["contextId"] = context_id
    if task_id:
        params["taskId"] = task_id
    return rpc(url, "tasks/send", params, token, timeout=timeout)


def get_task(url: str, task_id: str, token: str | None, timeout: int = 30) -> dict:
    """A2A tasks/get：回查任务状态。"""
    return rpc(url, "tasks/get", {"taskId": task_id}, token, timeout=timeout)


def send_message(url: str, task_id: str, content: str, token: str | None,
                 timeout: int = 30) -> dict:
    """A2A message/send：向既有任务追加消息（回执/讨论）。"""
    params = {
        "taskId": task_id,
        "message": {"role": "user", "parts": [{"text": content}]},
    }
    return rpc(url, "message/send", params, token, timeout=timeout)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="公司 B 侧 A2A 客户端（M-2 演示）")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--company", default=os.environ.get("AC4_COMPANY_ID", "co-remote-b"),
                        help="本公司 company_id")
    common.add_argument("--token", default=os.environ.get("AC4_COMPANY_TOKEN", ""),
                        help="公司 token（由 A 侧平台签发，安全通道交付）")
    common.add_argument("--url", default=DEFAULT_URL, help=f"对方公司 A2A 基址（默认 {DEFAULT_URL}）")
    ap.add_argument("--company", default=os.environ.get("AC4_COMPANY_ID", "co-remote-b"),
                    help="本公司 company_id")
    ap.add_argument("--token", default=os.environ.get("AC4_COMPANY_TOKEN", ""),
                    help="公司 token（由 A 侧平台签发，安全通道交付）")
    ap.add_argument("--url", default=DEFAULT_URL, help=f"对方公司 A2A 基址（默认 {DEFAULT_URL}）")
    sub = ap.add_subparsers(dest="cmd", required=True,
                            parser_class=lambda **kw: argparse.ArgumentParser(parents=[common], **kw))

    p_send = sub.add_parser("send", help="发任务书（tasks/send）")
    p_send.add_argument("title", help="任务书标题/内容")

    p_get = sub.add_parser("get", help="回查任务（tasks/get）")
    p_get.add_argument("task_id")

    p_msg = sub.add_parser("msg", help="追加消息（message/send）")
    p_msg.add_argument("task_id")
    p_msg.add_argument("content")

    p_card = sub.add_parser("card", help="读对方 Agent Card")
    args = ap.parse_args(argv)

    if not args.token:
        print("[a2a_client] ✗ 缺少公司 token：请用 --token 或环境变量 AC4_COMPANY_TOKEN 提供。",
              file=sys.stderr)
        return 2

    try:
        if args.cmd == "card":
            card = discover_card(args.url)
            print(json.dumps(card, ensure_ascii=False, indent=2))
        elif args.cmd == "send":
            result = send_task(args.url, args.title, args.token)
            print(json.dumps({"ok": True, "company": args.company, "result": result},
                             ensure_ascii=False, indent=2))
        elif args.cmd == "get":
            result = get_task(args.url, args.task_id, args.token)
            print(json.dumps({"ok": True, "company": args.company, "result": result},
                             ensure_ascii=False, indent=2))
        elif args.cmd == "msg":
            result = send_message(args.url, args.task_id, args.content, args.token)
            print(json.dumps({"ok": True, "company": args.company, "result": result},
                             ensure_ascii=False, indent=2))
    except A2AError as e:
        print(f"[a2a_client] ✗ {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
