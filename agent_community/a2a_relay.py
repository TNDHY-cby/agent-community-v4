#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A2A 接力插件（原为 示例Harness-C 侧编写，注入本仓 a2a_server 使用）
============================================================

⚠️ **状态：2026-10-07 起默认停用（示例Harness-C 已退役，该通道作废）**
   老板决定：示例Harness-C 退役 → 停掉这条通道。本插件原先的两个作用（任务工作间自动回执 +
   把讨论回复抄进 示例Harness-C 时代信箱目录）都只为「示例Harness-C 无法后台常驻」服务，现已无对象。
   - **默认不启用**：不起后台线程、不写自动回执、不向任何信箱落盘。
   - **要启用**：设环境变量 `AC4_A2A_RELAY=1`（将来做跨公司 A2A 协作时可能复用）。
   - 顺手去掉的隐患：原 `INBOX_DIR` 是硬编码的 示例Harness-C 时代绝对路径，现改为
     `AC4_RELAY_INBOX` 可配、默认落在平台数据目录下（仅在启用时才写）。

目标（启用时）：补上 9104 A2A server 的"自动处理闭环"：

  1) on_task_created：任务工作间创建后自动写入一条回执讨论（user 角色），
     使工作间从 draft 进入 discussing 态，并触发平台 AI/Orchestrator 参与讨论；
  2) 后台扫描线程：定期把工作间讨论中 示例Harness-E/平台（非 user 角色）的回复落盘到
     老板可见的书信信箱  D:\\合作社开发\\协作对话记录\\A2A_inbox\\<task_id>.md，
     供 示例Harness-C 被唤醒时读取接力（示例Harness-C 无法后台常驻，闭环以"落盘即闭环"实现）。

依赖：仅标准库（与 a2a_server.py 保持一致风格）。
用法：
  python -m agent_community.a2a_relay --scan     # 手动扫描一次并落盘
  （a2a_server main() 里调 start() 启动后台线程）
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.request
from datetime import datetime

BASE_URL = os.environ.get("AC4_BASE_URL", "http://127.0.0.1:18920").rstrip("/")
# 相对定位 data/workshops.json：本文件在 agent_community/ 顶层 → 父目录是 agent_community
WS_JSON = os.environ.get(
    "AC4_WS_JSON",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "workshops.json"),
)
# ── 启用开关（2026-10-07：示例Harness-C 退役，通道作废 → **默认关闭**）──────────
# 关闭时：不起后台扫描线程、不写自动回执、不向任何信箱落盘。设 AC4_A2A_RELAY=1 启用。
ENABLED = os.environ.get("AC4_A2A_RELAY", "0") == "1"
# 信箱目录不再硬编码 示例Harness-C 时代的绝对路径；默认落在平台数据目录下（仅启用时才写）
_DEFAULT_INBOX = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "a2a_inbox"
)
INBOX_DIR = os.environ.get("AC4_RELAY_INBOX", _DEFAULT_INBOX)
SCAN_INTERVAL = 60  # 秒


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _headers() -> dict:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    token = os.environ.get("AC4_TOKEN", "")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _post(path: str, payload: dict, timeout: int = 30) -> dict:
    url = BASE_URL + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def load_workshops() -> dict:
    """读取平台工作间数据：优先平台 API（内存态实时），失败兜底读落盘文件。

    平台讨论消息只写内存、落盘文件有延迟，读文件会漏掉新回复，必须走 API。
    """
    try:
        req = urllib.request.Request(BASE_URL + "/api/workshops", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        items = data.get("workshops") if isinstance(data, dict) else []
        if items:
            return {it.get("workshop_id"): it for it in items if it.get("workshop_id")}
    except Exception:  # noqa: BLE001
        pass
    if not os.path.exists(WS_JSON):
        return {}
    try:
        with open(WS_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def on_task_created(ws_id: str, task_id: str, text: str) -> dict:
    """任务工作间创建后写一条自动回执（user 角色），使工作间进入讨论态。

    注意：消息避免触发敏感扫描与点名钩子（不带敏感词、不点名员工）。
    停用时**不做任何动作**，直接返回 disabled（调用方 a2a_server 有 try/except 包裹）。
    """
    if not ENABLED:
        return {"ok": False, "disabled": True, "reason": "a2a_relay 已停用（需 AC4_A2A_RELAY=1）"}
    msg = (
        "【A2A 自动回执】来自 示例Harness-C 的任务已送达本工作间。"
        f"task_id={task_id}，ws_id={ws_id}，任务内容见大厅 hall_content。"
        "示例Harness-E 侧收到后请按任务要求处理；处理回复将由本插件自动落盘 A2A_inbox 信箱。"
    )
    resp = _post(f"/api/workshop/{ws_id}/discuss", {"message": msg})
    if "error" in resp:
        return {"ok": False, "error": resp["error"]}
    return {"ok": True, "ws_id": ws_id}


def relay_ws_reply(ws_id: str, ws: dict) -> str | None:
    """把工作间里非 user 角色的讨论回复落盘到信箱；无新回复返回 None。"""
    disc = ws.get("discussion") or []
    replies = [d for d in disc if d.get("role") not in ("user",)]
    if not replies:
        return None
    os.makedirs(INBOX_DIR, exist_ok=True)
    # 任务 id 优先从回执消息中解析（平台 Workshop 模型不存 task_id），兜底用 ws_id
    task_id = str(ws.get("task_id") or ws_id)
    import re as _re
    _m = _re.search(r"task_id=([0-9a-zA-Z]{6,})", json.dumps(disc, ensure_ascii=False))
    if _m:
        task_id = _m.group(1)
    fname = os.path.join(INBOX_DIR, f"{task_id}.md")
    lines = [
        f"# A2A 回复信箱 · {task_id}",
        f"（由 a2a_relay 插件于 {_now()} 落盘）",
        "",
        f"- ws_id: {ws_id}",
        f"- name: {ws.get('name', '')}",
        f"- status: {ws.get('status', '')}",
        "",
        "## 讨论记录（非 user 角色）",
        "",
    ]
    for d in replies:
        lines.append(f"**[{d.get('role')}] {d.get('timestamp', '')}**")
        lines.append("")
        lines.append(str(d.get("content", "")))
        lines.append("")
    body = "\n".join(lines)
    # 防覆盖：只有新内容变多才覆盖（避免重复扫描覆盖更新的回复）
    if os.path.exists(fname):
        try:
            with open(fname, encoding="utf-8") as f:
                old = f.read()
            if len(old) >= len(body):
                return None
        except Exception:  # noqa: BLE001
            pass
    with open(fname, "w", encoding="utf-8") as f:
        f.write(body)
    return fname


def scan_once() -> list[str]:
    """扫描全部工作间，把有新回复的落盘。返回落盘文件列表。"""
    ws = load_workshops()
    written: list[str] = []
    for ws_id, item in ws.items():
        try:
            f = relay_ws_reply(ws_id, item)
            if f:
                written.append(f)
        except Exception:  # noqa: BLE001
            continue
    return written


def start() -> "threading.Thread | None":
    """后台线程：定期扫描落盘（daemon，随进程退出）。

    停用时**不起线程、不写盘**，只打一行说明并返回 None（2026-10-07 示例Harness-C 退役后为默认行为）。
    """
    if not ENABLED:
        print("[a2a_relay] 已停用（示例Harness-C 退役、该通道作废）；如需启用请设 AC4_A2A_RELAY=1", flush=True)
        return None

    def _loop() -> None:
        while True:
            try:
                scan_once()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(SCAN_INTERVAL)

    t = threading.Thread(target=_loop, daemon=True, name="a2a-relay-scan")
    t.start()
    return t


def main() -> None:
    ap = argparse.ArgumentParser(description="A2A 接力插件（自动回执 + 回复落盘信箱）")
    ap.add_argument("--scan", action="store_true", help="手动扫描一次并落盘")
    args = ap.parse_args()
    if not ENABLED:
        # 默认停用（示例Harness-C 退役）：连手动扫描也不写盘，避免误往已作废的通道投递
        print("[a2a_relay] 已停用（示例Harness-C 退役、该通道作废）。如需使用请设 AC4_A2A_RELAY=1。")
        return
    if args.scan:
        written = scan_once()
        for f in written:
            print(f"[落盘] {f}")
        print(f"[扫描完成] 新回复 {len(written)} 封")
        return
    ap.print_help()


if __name__ == "__main__":
    main()
