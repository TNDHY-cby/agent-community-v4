# -*- coding: utf-8 -*-
"""**派活时顺手通知一句**（可选）：把要派的话，用统一投递层送到对方的界面里。

## 为什么单独一层、而且默认关

- 派活本身写数据就够了（收件箱 / pending / HTTP 回调）。**往别人界面上打字是有副作用的动作**
  （可能落在别人正在编辑的地方），所以默认 **关**：`AC_DELIVERY_NOTIFY=0`。
- 打开后它也只是"顺手补一句"，**绝不影响派活结果**：失败只记账 + 打日志，不让派活变失败
  （否则会出现"因为通知失败所以活没派出去"的荒唐状态）。
- 通道选择、读回核对、找目标的规则**全部复用** `platform/delivery.py`（一处真相）；
  本模块只负责：开关、取文本、失败不许炸、记账。

## 记账

每次通知都返回一个 dict（`{ok, skipped, channel, verified, why, at}`），
调用方把它写进自己的审计/日志即可；**不要**在这里偷偷写状态文件（状态落盘归 state_store）。
"""
from __future__ import annotations

import os
import time

_ON = ("1", "true", "yes", "on")


def enabled() -> bool:
    """是否允许"派活顺手通知"。默认关（自动兜底默认关是老板定的产品原则）。"""
    return (os.environ.get("AC_DELIVERY_NOTIFY") or "0").strip().lower() in _ON


def text_of(payload: dict, kind: str = "task") -> str:
    """从派发载荷里取出"要说的那句话"（取不到就返回空，调用方据此跳过）。"""
    p = payload or {}
    for key in ("message", "task_text", "content", "text"):
        v = p.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def notify_dispatch(harness_id: str, payload: dict, *, kind: str = "task",
                    dispatched: bool = True) -> dict:
    """派发之后顺手投一份到对方界面（**永不抛异常**，永远返回可记账的 dict）。"""
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    hid = str(harness_id or "").strip()
    out = {"ok": False, "skipped": True, "channel": "", "verified": None, "why": "", "at": now,
           "harness_id": hid, "kind": kind}
    if not enabled():
        out["why"] = "未开启（AC_DELIVERY_NOTIFY=0）：只派数据，不往界面打字"
        return out
    if not hid:
        out["why"] = "没有 harness_id，不知道通知谁"
        return out
    text = text_of(payload, kind)
    if not text:
        out["why"] = "这次派发没有可投的文本（只有结构化数据）"
        return out
    out["skipped"] = False
    try:
        from . import delivery

        res = delivery.deliver_to_harness(hid, text)
        out.update({"ok": bool(res.get("ok")), "channel": res.get("channel") or "",
                    "verified": res.get("verified"),
                    "why": (res.get("detail") or "")[:200]})
    except Exception as e:                                   # noqa: BLE001 — 通知失败绝不许影响派活
        out["why"] = "通知时出错（已记账，不影响派活）：%s: %s" % (type(e).__name__, e)
    if (not out["ok"]) and (not out["why"]):
        out["why"] = "投递层没给出原因（这本身就是个 bug，请报上来）"
    return out


def summary_line(result: dict) -> str:
    """一行日志（给审计/日志用，不刷屏）。"""
    if result.get("skipped"):
        return "[delivery-notify] 跳过：%s" % result.get("why", "")
    return ("[delivery-notify] %s ⇒ channel=%s verified=%s ｜ %s"
            % ("成功" if result.get("ok") else "没成",
               result.get("channel") or "-", result.get("verified"), (result.get("why") or "")[:120]))
