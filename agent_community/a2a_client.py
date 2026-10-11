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
DEFAULT_URL = os.environ.get("AC4_PEER_URL", "http://127.0.0.1:9104")


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


def discover_card(url: str, timeout: int = 10) -> dict:
    """读 A2A Agent Card（公开，无需凭据）。"""
    card_url = url.rstrip("/") + "/.well-known/agent-card.json"
    req = urllib.request.Request(card_url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        raise A2AError(f"读 Agent Card 失败: {e}") from e


def rpc(url: str, method: str, params: dict, token: str | None,
        timeout: int = 30) -> dict:
    """发一条 A2A JSON-RPC 请求，校验传输与协议两层错误。"""
    msg_id = uuid.uuid4().hex[:12]
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}
    status, resp = _request(url.rstrip("/") + "/", body, token, timeout=timeout)
    if status in (401, 403):
        raise A2AError(f"鉴权被拒（HTTP {status}）：{resp.get('error', '')}；"
                       f"请确认 token 为公司级 company token（见 Agent Card schemes）")
    if status >= 400:
        raise A2AError(f"HTTP {status}: {resp}")
    if "error" in resp:
        err = resp["error"]
        raise A2AError(f"JSON-RPC error {err.get('code')}: {err.get('message')}")
    return resp.get("result", {})


def send_task(url: str, title: str, token: str | None, task_id: str = "",
              timeout: int = 60) -> dict:
    """A2A tasks/send：向对方公司发任务书（无 taskId=新建任务）。"""
    params: dict = {"message": {"role": "user", "parts": [{"text": title}]}}
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
