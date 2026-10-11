# -*- coding: utf-8 -*-
"""platform/bridge_selfcheck：**"你的 agent 到底通没通"** —— 一次可判定的动作。

设计稿：`V38_陌生人自服务架桥设计.md` §3「第 3 步：自检」（★最要紧的一步）
        `V37_接入功能产品化设计.md` §4.2「第 4 步：验证它能回话」

**它回答的问题**（老板 2026-10-11 的真事：接了 agent，唤不醒、不回话，却不知道卡在哪）：
    平台发了话 → ① 桥取走了没？ → ② 你的 agent 回话了没？

**做法（复用既有约定，不发明新协议）**
1. 往**该成员的邮箱**写一条测试任务 `task_<id>.json`（与 `_file_poll_send` 同一文件约定）；
2. 等待（默认 8 秒），然后看三处**磁盘事实**：
   · `inbox/delivered/` 里有这条 probe 的痕迹 ⇒ **桥取走了**
   · `inbox/replies/` 里出现 `reply_<probe_id>*.json` ⇒ **agent 回话被桥收下**
   · 有 `replies/failed/` 的痕迹 ⇒ 桥转发了但**平台没收下**
3. 给**三态结论 + 人话原因 + 下一步**（准则⑤：失败要说话）。

⚠️ **不猜、不假装**：三态各说各的；**没证据时不说"通了"**。
⚠️ **不写死任何路径**：邮箱路径由调用方按 `compartment.mailbox_dir` 给。
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from typing import Any

#: probe 文件名的可识别前缀（用于在 delivered/ 与 replies/ 里找它的痕迹）
PROBE_PREFIX = "probe_"


def _listdir(p: str) -> list:
    try:
        return os.listdir(p) if p and os.path.isdir(p) else []
    except OSError:
        return []


def send_probe(inbox_dir: str, *, harness_id: str, workshop_id: str, member_id: str,
               role: str = "") -> dict:
    """往成员邮箱写一条**自检测试任务**。返回 `{ok, probe_id, path, payload, error}`。

    载荷里写清了「请怎么回话」——**用人话写，agent 读到就知道该做什么**，
    并且回话文件名带 `probe_<id>`，便于平台精确识别（不靠猜内容）。
    """
    probe_id = "%s%d" % (PROBE_PREFIX, int(time.time() * 1000))
    reply_name = "reply_%s.json" % probe_id
    payload = {
        "type": "self_check",
        "probe_id": probe_id,
        "harness_id": harness_id,
        "workshop_id": workshop_id,
        "member_id": member_id,
        "role": role,
        "sent_at": datetime.now().isoformat(timespec="seconds"),
        # ↓ 给人（或 agent）看的**自检指令**：照做即等于回话
        "message": (
            "这是平台的**接入自检**，不是干活任务。请确认你能收到这条消息，然后回话：\n"
            "在同一个邮箱目录的 `replies/` 子目录里新建一个文件，文件名必须是：\n"
            "    %s\n"
            "内容（照抄即可）：\n"
            '    {"type":"self_check_ack","probe_id":"%s","from":"<你的名字>","ok":true}\n'
            "写完后不用做别的 —— 平台的桥会把这条回话转发回去。"
            % (reply_name, probe_id)
        ),
        "reply_file": reply_name,
    }
    path = ""
    try:
        if not inbox_dir:
            return {"ok": False, "probe_id": probe_id, "error": "该成员还没有邮箱目录（先去工作间给它架桥）"}
        os.makedirs(inbox_dir, exist_ok=True)
        # ⚠️ 文件名必须同时满足两点（实测教训）：
        #   ① 匹配桥的扫描模式 `task_*.json`（桥按**文件名**移动任务）
        #   ② 带上 probe_id，否则移动进 delivered/ 后**认不出来是谁的**
        #   ⇒ 用 `task_<probe_id>.json`（= task_probe_<ms>.json），两点都满足。
        path = os.path.join(inbox_dir, "task_%s.json" % probe_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "probe_id": probe_id, "error": "%s: %s" % (type(e).__name__, e)}
    return {"ok": True, "probe_id": probe_id, "path": path, "payload": payload}


def inspect_probe(inbox_dir: str, probe_id: str) -> dict:
    """看这条 probe 走到哪一步了（纯读）。"""
    inbox = inbox_dir or ""
    delivered = os.path.join(inbox, "delivered")
    replies = os.path.join(inbox, "replies")
    failed = os.path.join(replies, "failed")

    # 桥把 task_*.json 移进 delivered/（文件名不变）⇒ 用我们的 probe_id 去认
    def _hit(names: list) -> list:
        return [n for n in names if probe_id in n]

    still_in_inbox = _hit(_listdir(inbox))
    in_delivered = _hit(_listdir(delivered))
    # 回话：reply_<probe_id>.json（桥成功转发后移到 replies/sent/）
    reply_here = _hit(_listdir(replies))
    reply_sent = _hit(_listdir(os.path.join(replies, "sent")))
    reply_failed = _hit(_listdir(failed))
    return {
        "probe_id": probe_id,
        "still_in_inbox": still_in_inbox,
        "delivered": in_delivered,
        "reply_in_replies": reply_here,
        "reply_sent": reply_sent,
        "reply_failed": reply_failed,
        "picked_up": bool(in_delivered or reply_here or reply_sent or reply_failed),
        "answered": bool(reply_here or reply_sent),
    }


def verdict(st: dict, *, inbox_exists: bool = True) -> dict:
    """把 `inspect_probe` 的结果翻成**三态 + 人话原因 + 下一步**。

    三态：`ok`（通了）/ `no_reply`（桥取走了、agent 没回话）/ `not_picked`（桥没取走）
    """
    if not inbox_exists:
        return {
            "state": "not_set_up",
            "ok": False,
            "reason": "这个成员还没有邮箱 —— 还没为它架过桥",
            "advice": "在工作间里给它「架桥」，平台会按它的隔间建好邮箱",
        }
    if st.get("reply_failed"):
        return {
            "state": "no_reply", "ok": False,
            "reason": "你的 agent 回话了，但**平台没收下**这条回报",
            "advice": "去 `<隔间>/inbox/replies/failed/` 看那个文件，平台会在里面说明拒绝原因",
        }
    if st.get("answered"):
        where = "replies/sent/（已转发给平台）" if st.get("reply_sent") else "replies/（等桥转发）"
        return {
            "state": "ok", "ok": True,
            "reason": "收到回话了 —— 平台 → 桥 → 你的 agent → 回报，这条路是通的",
            "advice": "接入成功，可以进入下一步（让它到工作间里干活）",
            "detail": {"reply_location": where},
        }
    if st.get("picked_up"):
        return {
            "state": "no_reply", "ok": False,
            "reason": "桥取走了测试消息，但**没等到你的 agent 回话**",
            "advice": ("说明桥在跑、消息送到了 agent 那边，卡在 agent 侧。查两处："
                       "① 桥里那条「叫醒命令」填了没（没填就只是落盘、不会叫它）；"
                       "② 你 agent 有没有真的去看那个邮箱目录"),
        }
    return {
        "state": "not_picked", "ok": False,
        "reason": "测试消息**还躺在邮箱里没人取** —— 桥没有在运行",
        "advice": ("启动那只桥：在成员隔间目录里运行 `bridge.py`"
                   "（平台生成的脚本；同目录 `deliver_text.md` 里有现成命令）"),
    }


def selfcheck(inbox_dir: str, *, harness_id: str, workshop_id: str, member_id: str,
              role: str = "", wait: float = 8.0) -> dict:
    """**一步到位**：发试探 → 等 → 给结论（供端点直接用）。

    `wait` 上限夹在 0~30 秒（别把请求挂死）。
    """
    ok_dir = bool(inbox_dir) and os.path.isdir(inbox_dir)
    sent = send_probe(inbox_dir, harness_id=harness_id, workshop_id=workshop_id,
                      member_id=member_id, role=role)
    if not sent.get("ok"):
        return {"success": False, "state": "not_set_up", "error": sent.get("error", ""),
                "advice": "在工作间里给这个成员「架桥」（平台会按它的隔间建好邮箱）"}
    probe_id = sent["probe_id"]
    try:
        w = max(0.0, min(float(wait or 0), 30.0))
    except Exception:  # noqa: BLE001
        w = 8.0
    st = inspect_probe(inbox_dir, probe_id)
    deadline = time.time() + w
    while time.time() < deadline and not (st["picked_up"] and st["answered"]):
        time.sleep(0.5)
        st = inspect_probe(inbox_dir, probe_id)
        # 桥在跑的话，取件通常 1 个轮询内完成；取件了但没回话就等满窗口
        if st["answered"]:
            break
    v = verdict(st, inbox_exists=ok_dir)
    return {
        "success": bool(v["ok"]),
        "state": v["state"],
        "reason": v["reason"],
        "advice": v["advice"],
        "detail": dict(v.get("detail") or {}, **{
            "probe_id": probe_id,
            "picked_up": st["picked_up"],
            "answered": st["answered"],
            "waited_seconds": w,
        }),
    }
