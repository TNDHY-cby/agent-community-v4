# -*- coding: utf-8 -*-
"""platform/harness_probe：**平台侧主动探测** —— 由平台去测外端 agent 有什么接口。

设计稿：`design-docs/V42_接入建模_低耗验证间与模板回写.md` §4.5（老板 2026-10-11 22:30 定稿）
老板原话：「**要是平台有 agent 可以去拉桥就好了**，这样就不怕外端 agent 因为**沙箱等限制
无法检测自身接口**了」

**为什么必须由平台来测**
──────────────────────
外端 agent 常被沙箱限制：它**看不到自己的进程、端口、安装路径**
（实测：一个**桌面型**外端 agent 被问"怎么被叫醒"时回"**我什么接口都没有**" ——
那是**它看不到**，不一定是**真没有**）。而平台跑在宿主上，**看得见它**。
（★这里刻意不写具体产品名：对外源码只用形态描述，见规则 6「对外文档与示例一律用虚拟名」。）

**三层（前两层纯脚本、零 token —— 老板："如果全脚本的话另说"）**
    ① 进程：有没有像它的进程在跑（命令行 / 进程名）
    ② 端口·HTTP：有没有在监听、能不能接一次 POST
    ③ 文件·可执行：安装路径存不存在、给定的命令是否可执行

**纪律（写进代码，不只是文档）**
    1. **只探本机/用户自己的东西**（接入本来就发生在同一台机器）；
    2. **用户点了才探**（本模块不自我调度、不做后台轮询）；
    3. **只给候选与证据，不直接定论** —— 结论必须来自"跑通"（自检），不是来自探测；
    4. **零 token**：本模块**不调用任何 AI**（要 AI 判断是上层的事，且须如实告知花了 token）。
"""
from __future__ import annotations

import os
import socket
from typing import Any, Optional

#: 探测结论里每项的"证据等级"，避免把"像"说成"是"
EVIDENCE_STRONG = "strong"     # 直接证据（端点真的应答了）
EVIDENCE_WEAK = "weak"         # 间接证据（进程/端口像，但没验证过）
EVIDENCE_NONE = "none"         # 没探到


def _probe_http(url: str, *, message_path: str = "/message", timeout: float = 3.0) -> dict:
    """探 HTTP 端点（复用既有的 `api_wakeup.probe_http_api`，别另写一套）。"""
    try:
        from .api_wakeup import probe_http_api
        ok, detail = probe_http_api(url, message_path, timeout=timeout)
        return {"ok": bool(ok), "detail": str(detail)[:300], "url": url}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "url": url}


def _probe_port(host: str, port: int, timeout: float = 1.5) -> bool:
    """能不能连上（只做 TCP 连接测试，不发数据）。"""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:  # noqa: BLE001
        return False


def _probe_process(name_or_cmd: str, timeout: float = 30.0) -> list:
    """扫本机进程，返回命令行里含该关键词的候选（只读，不杀）。

    ⚠️ 与 `_find_bridge_processes` 同源但**不同用途**：这里是"找你的 agent"，
    所以关键词由调用方给（进程名/命令行线索），并**排除平台自己的子进程**
    （否则会把探测动作本身算进去 —— 这个坑我今天在别处踩过）。
    """
    needle = (name_or_cmd or "").strip()
    if not needle:
        return []
    try:
        import json as _json
        import subprocess as _sp
        ps = ("$needle = $env:AC_NEEDLE; $me = [int]$env:AC_SELF_PID; "
              "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and "
              "$_.ParentProcessId -ne $me -and $_.CommandLine -like ('*' + $needle + '*') } | "
              "Select-Object -First 8 ProcessId,Name,CommandLine | ConvertTo-Json -Compress")
        env = dict(os.environ)
        env["AC_NEEDLE"] = needle
        env["AC_SELF_PID"] = str(os.getpid())
        r = _sp.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                    text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)
        out = (r.stdout or "").strip()
        if not out or out == "null":
            return []
        data = _json.loads(out)
        if isinstance(data, dict):
            data = [data]
        return [{"pid": d.get("ProcessId"), "name": d.get("Name"),
                 "cmd": str(d.get("CommandLine") or "")[:200]} for d in data]
    except Exception as e:  # noqa: BLE001
        return [{"error": f"{type(e).__name__}: {e}"}]


def probe_harness(info: Any, *, base_url: str = "http://127.0.0.1:18920",
                  do_http: bool = True, hints_in: Optional[dict] = None) -> dict:
    """**平台主动探测一个 harness**：他能怎么被叫醒？返回候选 + 证据（**不下结论**）。

    `info`：`HarnessInfo`（读 `process_hint` / `port_hint` / `path_hint` /
            `wakeup_dir` / `acp_command` / `api_base_url` 这些**线索字段**）。
    返回结构：
        {
          "candidates": [{"kind": "cli_acp|file_poll|http_api|pending_poll",
                          "evidence": "strong|weak|none", "why": "...", "detail": {...}}],
          "found": {...},          # 原始探测结果，供界面如实展示
          "note": "...",           # 一句话：探到了什么 / 或者为什么探不到
from_ai": False              # ★本函数**不调用 AI**（零 token）
        }
    """
    # ★2026-10-11 实测发现：`HarnessInfo` **根本没有** `process_hint`/`port_hint`/`path_hint`
    #   这三个字段（`pre-register` 的文档却把它们写成可选入参）⇒ 线索从来没落过盘。
    #   ⇒ 线索来源改为三处按优先级合并（**显式传入 > harness.metadata 里存过的 > 空**）。
    md = getattr(info, "metadata", None) or {}
    md_hints = md.get("probe_hints") if isinstance(md, dict) else None
    md_hints = md_hints if isinstance(md_hints, dict) else {}
    src = dict(hints_in or {})

    def _h(key):
        for bag in (src, md_hints):
            v = bag.get(key)
            if v not in (None, ""):
                return str(v).strip()
        return ""

    hints = {
        "process_hint": _h("process_hint") or os.path.basename(
            str(getattr(info, "acp_command", "") or "").split(" ")[0].strip('"')),
        "port_hint": _h("port_hint") or "",
        "path_hint": _h("path_hint"),
        "wakeup_dir": str(getattr(info, "wakeup_dir", "") or "").strip(),
        "acp_command": str(getattr(info, "acp_command", "") or "").strip(),
        "api_base_url": str(getattr(info, "api_base_url", "") or "").strip(),
    }
    found: dict = {"hints": hints}
    cands: list = []

    # ── ① 进程：有没有像它的东西在跑 ──────────────────────────────────────
    kw = hints["process_hint"]
    if not kw and hints["path_hint"]:
        kw = os.path.basename(hints["path_hint"])
    if kw:
        procs = _probe_process(kw)
        procs = [p for p in procs if "error" not in p]
        found["processes"] = procs
        if procs:
            cands.append({"kind": "cli_acp", "evidence": EVIDENCE_WEAK,
                          "why": f"本机有 {len(procs)} 个进程像它（命令行含「{kw}」）——"
                                 f"说明它**是个可被拉起的进程**，但「怎么拉起」仍需你给一条命令",
                          "detail": procs[:3]})
    else:
        found["processes"] = []

    # ── ② 端口 / HTTP：有没有常驻接口 ────────────────────────────────────
    if hints["port_hint"]:
        try:
            port = int(hints["port_hint"])
        except Exception:  # noqa: BLE001
            port = 0
        if port:
            open_now = _probe_port("127.0.0.1", port)
            found["port_open"] = {"port": port, "open": open_now}
            if open_now:
                cands.append({"kind": "http_api", "evidence": EVIDENCE_WEAK,
                              "why": f"127.0.0.1:{port} **能连上** —— 说明有东西在监听；"
                                     f"但「能不能收平台的消息」还要探端点",
                              "detail": {"port": port}})
    if do_http:
        bu = hints["api_base_url"]
        if bu:
            hp = _probe_http(bu)
            found["http_probe"] = hp
            cands.append({"kind": "http_api",
                          "evidence": EVIDENCE_STRONG if hp["ok"] else EVIDENCE_WEAK,
                          "why": ("端点**真的应答了** ⇒ 平台可直接推送（无需桥）" if hp["ok"]
                                  else f"探了但没通：{hp['detail'][:120]}"),
                          "detail": hp})
    else:
        found["http_probe"] = {"skipped": "调用方要求跳过 HTTP 探测"}

    # ── ③ 文件 / 可执行 ─────────────────────────────────────────────────
    if hints["wakeup_dir"]:
        d = hints["wakeup_dir"]
        exists = os.path.isdir(d)
        writable = os.access(d, os.W_OK) if exists else False
        found["inbox"] = {"dir": d, "exists": exists, "writable": writable}
        if exists and writable:
            cands.append({"kind": "file_poll", "evidence": EVIDENCE_WEAK,
                          "why": f"邮箱目录存在且可写（{d}）⇒ 平台能把任务写进去；"
                                 f"但「它的 agent 会不会看这个目录」要跑通才算",
                          "detail": found["inbox"]})
    if hints["acp_command"]:
        exe = hints["acp_command"].split()[0].strip('"')
        if os.path.isabs(exe):
            ok = os.path.isfile(exe)
            found["acp_exe"] = {"exe": exe, "exists": ok}
            if ok:
                cands.append({"kind": "cli_acp", "evidence": EVIDENCE_WEAK,
                              "why": f"命令里的可执行文件存在（{exe}）⇒ 这条命令**可能**能拉起它",
                              "detail": found["acp_exe"]})
        else:
            found["acp_exe"] = {"exe": exe, "exists": None,
                                "why": "不是绝对路径，无法直接判断（PATH 里可能能找到）"}

    # ── 结论：只给候选，明确"这还不是定论" ──────────────────────────────
    if not cands:
        note = ("没探到可用接口。**这不等于它没有** —— 你给的线索（进程名/端口/路径）不够，"
                "或者它确实只能人工中转（那就选 clipboard/pending_poll 先接上）。")
    else:
        kinds = "、".join(sorted({c["kind"] for c in cands}))
        note = (f"探到 {len(cands)} 条候选（{kinds}）。"
                f"★这些只是**线索**：要写进模板必须**跑通一次**（自检通过）。")
    return {"candidates": cands, "found": found, "note": note,
            "from_ai": False, "token_cost": 0}
