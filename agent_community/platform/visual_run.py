# -*- coding: utf-8 -*-
"""**一次跑完的视觉激活**：新建会话 → 点输入框 → 粘贴话术 → 发送（老板 2026-10-24 规格）。

## 为什么不复用"分步调用"

老板原话：「新建会话之后就没办法发送通知了」—— 分两个按钮、两次请求去测，
中间那一段（新建会话已经点了、界面正在切到新会话）没人管：
下一次请求去粘贴时，前台/焦点/布局可能已经变了，于是"新建会话成了、通知发不出去"。
⇒ 整条链**一次请求跑完**，中间只在这里等、只在这里自证。

## 顺序（固定，不许颠倒）

```
① 抬到前台并自证（前台不是它 ⇒ 如实失败，不盲点）
② 点「新建会话」（框过才做；点完等 settle_new 秒让界面切过去）
③ 点「输入框」→ 设剪贴板 → Ctrl+V → 抓图看框里变没变（band_diff）
④ 发送：框过「发送按钮」就点它，否则按回车（`enter` 为真时）
⑤ 收尾：把前台还回调用前那个窗口（`restore_focus` 默认真，老板规格 ③②）
```

任一步失败就**停在原地并说清是哪一步、为什么**，不继续往下点（下一步的前提已经不成立了）。
"""
from __future__ import annotations

import time


def run(hwnd: int, regions: dict, text: str, *, enter: bool = True,
        with_new_session: bool = False, settle_new: float = 1.2, settle_click: float = 0.35,
        restore_focus: bool = True) -> dict:
    """按上面五步跑一次；返回 `{ok, steps[], state, focus_restored, restored_hwnd}`。

    **默认不新建会话**（`with_new_session=False`）—— 老板 2026-10-24 定：
    「不做会话选择了……他们只配同时接入一个会话进行工作……**要求用户自己把那个会话点开**就行，
    平台只用把属于它的通知按照脚本给到」。⇒ 平台认的是"**你现在开着的那个会话**"，
    不去新建、也不去列表里挑；要新建的场合显式传 `with_new_session=True`。
    """
    from . import input_regions, input_sim, screen_vision
    from .wakeup_bridge import wakeup_bridge

    out = {"ok": False, "steps": [], "state": "not_sent", "focus_restored": None}
    h = int(hwnd or 0)
    if not h:
        out["why"] = "没有窗口句柄 —— 先把窗口绑给这个 harness"
        return out
    reg = regions or {}

    def step(name: str, ok: bool, why: str = "", **extra) -> dict:
        item = {"name": name, "ok": bool(ok), "why": why}
        item.update(extra)
        out["steps"].append(item)
        return item

    prev_fg = input_sim.foreground_window()
    try:
        # ① 前台（自证）
        fg = input_sim.ensure_foreground(h)
        if not fg.get("ok"):
            step("前台", False, fg.get("why") or "抬不到前台")
            out["why"] = fg.get("why") or "抬不到前台"
            return out
        step("前台", True)

        # ② 新建会话（可选）
        if with_new_session and reg.get("new_session"):
            pt = input_regions.input_point(reg["new_session"])
            ck = input_sim.click_in_window(h, pt["rel_x"], pt["rel_y"])
            if not ck.get("ok"):
                step("新建会话", False, ck.get("why") or "点不到")
                out["why"] = "点「新建会话」失败：%s" % (ck.get("why") or "")
                return out
            time.sleep(max(0.0, float(settle_new)))       # 等界面切到新会话
            step("新建会话", True, "点了，等了 %.1fs" % float(settle_new))
        elif with_new_session:
            step("新建会话", True, "没框过「新建会话」—— 跳过（不新建）")

        # ③ 输入框：点 → 剪贴板 → Ctrl+V → 看画面
        if not reg.get("input"):
            step("输入框", False, "没框过输入框")
            out["why"] = "没框过输入框 —— 先框一次"
            return out
        pt = input_regions.input_point(reg["input"])
        ck = input_sim.click_in_window(h, pt["rel_x"], pt["rel_y"])
        if not ck.get("ok"):
            step("输入框", False, ck.get("why") or "点不到")
            out["why"] = "点输入框失败：%s" % (ck.get("why") or "")
            return out
        time.sleep(max(0.0, float(settle_click) * 0.6))
        before = screen_vision.grab_band(h, pt["rel_y"], half=0.12)
        wakeup_bridge._set_clipboard(str(text or ""))
        k = input_sim.send_keys("ctrl+v")
        if not k.get("ok"):
            step("输入框", False, "没按成 Ctrl+V：%s" % (k.get("why") or ""))
            out["why"] = "没按成 Ctrl+V"
            return out
        time.sleep(0.35)
        after = screen_vision.grab_band(h, pt["rel_y"], half=0.12)
        d = screen_vision.band_diff(before, after, pt["rel_y"])
        pasted = bool(d.get("changed"))
        step("输入框", True, "已粘贴", pasted=pasted, ratio=d.get("ratio"))

        # ④ 发送
        if enter:
            if reg.get("send"):
                s = input_regions.center_of(reg["send"])
                ck2 = input_sim.click_in_window(h, s["rel_x"], s["rel_y"])
                step("发送", bool(ck2.get("ok")), ck2.get("why") or "点了框好的发送按钮",
                     how="click-send")
                if not ck2.get("ok"):
                    out["why"] = "点发送按钮失败：%s" % (ck2.get("why") or "")
                    return out
            else:
                k2 = input_sim.send_keys("enter")
                step("发送", bool(k2.get("ok")), "按了回车" if k2.get("ok") else (k2.get("why") or ""),
                     how="enter")
                if not k2.get("ok"):
                    out["why"] = "按回车失败：%s" % (k2.get("why") or "")
                    return out
        else:
            step("发送", True, "没要求发送（enter=false）")

        out["ok"] = True
        out["state"] = "sent_unverified"                 # 读不回文字，只能到这一档
        out["why"] = ""
        return out
    finally:
        if restore_focus and prev_fg and prev_fg != h:
            r = input_sim.set_foreground(prev_fg)
            out["focus_restored"] = bool(r.get("ok"))
            out["restored_hwnd"] = prev_fg
            if not r.get("ok"):
                out["focus_note"] = r.get("why") or ""
