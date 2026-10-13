"""Agent 侧周期心跳上报客户端（2026-10-07 示例Harness-C，47 号任务书 示例Harness-C 侧）。

配合平台端 POST /api/heartbeat（platform/server.py）使用：
  - 周期上报 alive/idle/busy，让平台知道"我还活着、在忙/空闲"；
  - 平台按 interval × timeout_scale(2) 判定失联，失联列表见 GET /api/heartbeat/stale；
  - 与既有唤醒链路并存：唤醒负责"叫醒"，心跳负责"打卡"。

用法::

    python -m agent_community.tools.heartbeat_client \\
        --url http://127.0.0.1:18920 \\
        --harness-id <harness_id> --agent-id <agent_id> --interval 30

    --once        只上报一次（供 cron/守护脚本周期调用）
    --status busy 上报忙碌态（可选 alive/idle/busy）
    --note ...    附加说明（可选）

零第三方依赖（仅标准库 urllib/json/time/signal），与 a2a_client 同风格。
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:18920"


def send_beat(url: str, harness_id: str, agent_id: str, status: str,
              interval: float, note: str = "", timeout: float = 5.0) -> tuple[bool, str]:
    """向平台 POST /api/heartbeat。返回 (ok, detail)。"""
    body = json.dumps({
        "agent_id": agent_id,
        "harness_id": harness_id,
        "status": status,
        "interval": interval,
        "note": note,
    }).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + "/api/heartbeat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8", "replace")
        return True, payload
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace") if e.fp else str(e)
        return False, f"HTTP {e.code}: {detail}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _loop(url: str, harness_id: str, agent_id: str, status: str,
          interval: float, note: str) -> int:
    stop = False

    def _on_signal(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    first = True
    while not stop:
        ok, detail = send_beat(url, harness_id, agent_id, status, interval, note)
        print(f"[heartbeat] {'✓' if ok else '✗'} {time.strftime('%H:%M:%S')} {detail}",
              flush=True)
        if first:
            first = False
        if ok:
            time.sleep(max(1.0, interval))
        else:
            time.sleep(5.0)  # 上报失败慢速重试，不空转
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Agent 周期心跳上报客户端")
    p.add_argument("--url", default=DEFAULT_URL, help=f"平台地址（默认 {DEFAULT_URL}）")
    p.add_argument("--harness-id", required=True, help="harness_id（平台注册的 harness）")
    p.add_argument("--agent-id", default="", help="agent_id（缺省用 harness_id）")
    p.add_argument("--interval", type=float, default=30.0, help="心跳周期秒（默认 30）")
    p.add_argument("--status", default="alive", choices=("alive", "idle", "busy"),
                   help="状态（默认 alive）")
    p.add_argument("--note", default="", help="附加说明（可选）")
    p.add_argument("--once", action="store_true", help="只上报一次后退出")
    args = p.parse_args(argv)

    agent_id = args.agent_id or args.harness_id
    if args.once:
        ok, detail = send_beat(args.url, args.harness_id, agent_id,
                               args.status, args.interval, args.note)
        print(f"[heartbeat] {'✓' if ok else '✗'} {detail}", flush=True)
        return 0 if ok else 1
    return _loop(args.url, args.harness_id, agent_id, args.status,
                 args.interval, args.note)


if __name__ == "__main__":
    sys.exit(main())
