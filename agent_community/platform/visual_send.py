# -*- coding: utf-8 -*-
"""`platform/visual_send`：**按视觉脚本给某个接入投一段话**（一处真相）。

两个使用者：
- `routers/docking.py`：`POST /api/docking/harness/{id}/send-text`（把任意一段话送进它的窗口）；
- `dispatch.activate()` 的 `c_visual` 分支（派活时送"看下 T-003"）。

## 三条边界（全是实测事实，别改口径）

1. **能送进什么**：按脚本里的**窗口标题/关键词**定位，然后按 `cdp → 原生 → UIA → 半自动` 四通道投递；
   `verified=true` 的含义是「**输入框里读回来的内容与我送的那段一致**」（WM_GETTEXT / cdp 读 DOM）——
   这只证明"话确实进去了"，**不证明对方看见、执行、或回复**。
2. **读不到回复**：平台的"读回"只读自己刚送进去的那段字，**不读对方的回答**。
   对方回复的可靠来源只有三条：① 人看 ② 它自己交回执（`task-result` / 桥文件）③ 平台抓帧（图片，不结构化）。
3. **不假装**：没有脚本就说"没有脚本"并指路去「脚本激活」工作台；投递失败给真实原因，不给"已发送"。
"""
from __future__ import annotations

from . import protocol_plan as _plan

#: 对外固定口径（界面/文档/接口都引用这一句，别各写一份）
VISION_REPLY_NOTE = (
    "平台只核实「**话确实送进了它的输入框**」；**读不到它的回复**。"
    "回复要么人看它的窗口，要么它自己交回执（`/api/harness/task-result` / 桥文件）。"
)


def send_text(info, text: str, *, press_enter: bool = False) -> dict:
    """按它的视觉脚本把 `text` 送进窗口。返回 `{ok, code?, delivered, verified, channel, target, detail, note}`。

    - 没有脚本 / 脚本没定位键 ⇒ `ok=False` + `code=NO_VISUAL_SCRIPT` + `next`（**不许当成功**）；
    - 投递失败 ⇒ `ok=False` + `detail`（带真实原因）；
    - 永远带 `note`（说明"这只是送进去了，读不到回复"）。
    """
    ok, why = _plan.script_ready(info)
    if not ok:
        return {
            "ok": False, "code": "NO_VISUAL_SCRIPT", "delivered": False, "verified": False,
            "channel": "", "target": "",
            "error": "这位接入还没有可用的视觉脚本：%s" % why,
            "next": ("去对接中心「脚本激活」工作台：填窗口标题或关键词 → 让平台找一次 → "
                     "确认试投 → 保存成它的激活方式；保存后才能自动投递"),
            "note": VISION_REPLY_NOTE,
        }
    key = _plan.script_key(info)
    try:
        from . import delivery

        res = dict(delivery.deliver_by_name(key, str(text or ""), press_enter=press_enter))
    except Exception as e:                                     # noqa: BLE001 — 投递失败要如实说，不假装已发
        return {
            "ok": False, "code": "DELIVERY_FAILED", "delivered": False, "verified": False,
            "channel": "", "target": key,
            "error": "视觉投递没跑起来（%s: %s）" % (type(e).__name__, str(e)[:160]),
            "next": "回「脚本激活」工作台换一档（cdp / 原生 / UIA / 半自动），或人工转达",
            "note": VISION_REPLY_NOTE,
        }
    sent_ok = bool(res.get("ok"))
    return {
        "ok": sent_ok,
        "delivered": sent_ok,
        "verified": bool(res.get("verified")),
        "channel": str(res.get("channel") or ""),
        "target": key,
        "detail": str(res.get("detail") or "")[:300],
        "next": str(res.get("next_step") or "")[:300] if not sent_ok else "",
        "note": VISION_REPLY_NOTE,
    }
