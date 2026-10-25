#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：这个 MCP/harness **能不能在它自己的会话里说话**（`session_post` 那类工具）。

老板 2026-10-25 定的口径：
> 「平台不用从它的输入框把字发给它，只需要对方能够在它的会话里面发消息。」

所以"用户在它自己的会话里看见它干活"这件事，判据只有三条字段：

| 字段 | 意思 |
|---|---|
| `tool` | 平台实际调的是哪个工具 |
| `in_session` | **true** ＝ 这个工具是"往它自己会话里发一条"（名字命中 `session_post` 那族） |
| `verified` | 平台发的话里带**一次性口令**，它回显里带回口令 ⇒ 这条消息它真接住了 |

用法：
    python tools/in_session_check.py                      # 列出所有 harness 的判断结果（只读列工具表）
    python tools/in_session_check.py --harness <harness_id> --send   # 真发一句、真验（会调对方的工具）
    python tools/in_session_check.py --harness <id> --send --text "请回复：收到"

退出码：0＝有 harness 能会话内发言（或 --send 时三项全对）；1＝没有/不对；2＝连不上平台。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_BASE = "http://127.0.0.1:18920"
#: 与后端 `_SESSION_POST_HINTS` 保持同一族（这里只用来做"**只读**判断"，真判定以后端返回为准）
SESSION_POST_HINTS = ("session_post", "post_to_session", "session_message", "announce", "say",
                      "speak", "reply_in_session", "post_message", "send_message", "notify",
                      "broadcast")


def _get(base: str, path: str, timeout: float = 20) -> dict:
    with urllib.request.urlopen(base + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def _post(base: str, path: str, body: dict, timeout: float = 90) -> dict:
    req = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE, help="平台地址（默认本机 18920）")
    ap.add_argument("--harness", default="", help="只查这一个 harness_id")
    ap.add_argument("--send", action="store_true", help="真发一句话去验（会调对方的工具）")
    ap.add_argument("--text", default="请回复：收到", help="要发的那句话")
    a = ap.parse_args()

    try:
        ov = _get(a.base, "/api/docking/overview")
    except Exception as e:                                     # noqa: BLE001 — 连不上要明说
        print("[FAIL] 连不上平台 %s：%s: %s" % (a.base, type(e).__name__, e))
        return 2

    rows = ov.get("harnesses") or []
    if a.harness:
        rows = [h for h in rows if h.get("harness_id") == a.harness]
    if not rows:
        print("[FAIL] 没有匹配的 harness（用 --harness 指定，或先注册一个）")
        return 1

    print("=" * 78)
    print("判据：能不能在「它自己的会话」里说话 —— %s" % a.base)
    print("=" * 78)
    any_ok = False        # 只读判断：有同名工具就算"可能有"
    any_verified = False  # --send 时：**只有读回核对通过**才算
    for h in rows:
        hid = h.get("harness_id") or ""
        kind = h.get("wakeup_method") or ""
        tools = h.get("tools") or []
        names = [str((t.get("name") if isinstance(t, dict) else t) or "") for t in tools]
        hit = [n for n in names if any(k in n.lower() for k in SESSION_POST_HINTS)]
        print("\n· %s（协议 %s）" % (hid, kind or "?"))
        print("   工具表：%s" % ("、".join(n for n in names if n) or "（空 / 还没拉过）"))
        if kind not in ("mcp", "http", "http_api", "a2a", "grpc", "ws"):
            print("   ⇒ 纯桌面/桥类：平台不点它的输入框；它自己发不发言要看它有没有这类工具")
        if hit:
            print("   ⇒ 看起来有「会话内发言」工具：%s（**以后端返回的 in_session 为准**）" % "、".join(hit))
            any_ok = True
        else:
            print("   ⇒ 没看到这类工具：这条回复只会回到平台，**不会出现在它的会话里**")
        if a.send and kind == "mcp":
            try:
                r = _post(a.base, "/api/docking/harness/%s/test-now" % hid, {"text": a.text})
            except urllib.error.HTTPError as e:
                print("   真发失败：HTTP %s %s" % (e.code, e.read().decode("utf-8", "replace")[:160]))
                continue
            print("   真发结果：tool=%s in_session=%s verified=%s"
                  % (r.get("tool"), r.get("in_session"), r.get("verified")))
            if r.get("reply"):
                print("   它回了：%s" % str(r.get("reply"))[:200])
            if r.get("in_session_note"):
                print("   说明：%s" % str(r.get("in_session_note"))[:220])
            # ★2026-10-25：**只认读回核对过的**（in_session_verified）；只有自报的 in_session
            #   不算能在自己会话里说话（老板真机：也不知道真假）。
            if r.get("in_session_verified") is True:
                any_ok = True
                any_verified = True
            elif r.get("in_session") and r.get("verified"):
                print("   ⚠ 它自报 in_session=true，但平台没读回核对（缺 session_messages 之类）"
                      "⇒ 按口径**不算验证通过**")

    print("\n" + "-" * 78)
    if a.send:
        print("结论：%s" % ("**读回核对通过：它确实把话写进了自己的会话**" if any_verified
                          else "**还不算通**：发出去了，但平台读不回（缺 session_messages 之类），按口径不算验证通过"))
    else:
        print("结论：%s（只读判断；要真验加 --send）"
              % ("有会话内发言工具" if any_ok else "没有会话内发言工具"))
    if a.send:
        return 0 if any_verified else 1
    return 0 if any_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
