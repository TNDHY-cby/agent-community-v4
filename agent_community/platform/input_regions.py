# -*- coding: utf-8 -*-
"""「框选区域」：用户在一张窗口截图上框出**输入框**和**发送按钮**（相对坐标 0~1）。

老板 2026-10-23 夜的设计：
「关键词可以让用户直接提供；输入框检测不出也可以让用户截图上传，
 平台最好能提供类似作业帮框题的功能，让用户框出输入框和发送按钮。」

为什么用**相对坐标**：窗口会被拖动/缩放/换屏，相对比例才对得上；
为什么存**两个框**：投递要"点输入框 → 粘贴 →（可选）点发送"，两个框各管一段。

结构（`DATA_DIR/input_regions.json`）：
```
{"<harness_id>": {"input": {"x":0.1,"y":0.8,"w":0.8,"h":0.08},
                  "send":  {"x":0.9,"y":0.8,"w":0.06,"h":0.06},
                  "keywords": ["在这里输入", "发消息"],
                  "frame": {"w": 1442, "h": 902}, "at": "2026-10-24T01:20:00"}}
```
"""
from __future__ import annotations

import json
import time
from pathlib import Path

FILE_NAME = "input_regions.json"


def _path() -> Path:
    from .env_spec import data_dir

    d = Path(data_dir())
    d.mkdir(parents=True, exist_ok=True)
    return d / FILE_NAME


def _load() -> dict:
    p = _path()
    if not p.is_file():
        return {}
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
        return j if isinstance(j, dict) else {}
    except Exception as e:                                   # noqa: BLE001 — 坏了要说话，但别炸调用方
        print("[input_regions] 读 %s 失败（当作没有框选）：%s: %s" % (p.name, type(e).__name__, e),
              flush=True)
        return {}


def _save(data: dict) -> None:
    p = _path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _clamp_box(box: dict) -> dict | None:
    """夹到 0~1 并保证 w/h > 0；给不全就返回 None（不猜）。"""
    try:
        x = float(box.get("x"))
        y = float(box.get("y"))
        w = float(box.get("w"))
        h = float(box.get("h"))
    except (TypeError, ValueError, AttributeError):
        return None
    if w <= 0 or h <= 0:
        return None
    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    w = min(w, 1.0 - x)
    h = min(h, 1.0 - y)
    if w <= 0.001 or h <= 0.001:
        return None
    return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}


def center_of(box: dict) -> dict:
    """框的中心（相对坐标）—— 点击就用它。"""
    return {"rel_x": round(box["x"] + box["w"] / 2.0, 4), "rel_y": round(box["y"] + box["h"] / 2.0, 4)}


def input_point(box: dict, *, at: float | None = None) -> dict:
    """**输入框该点哪儿**：默认就是**框的正中**（老板 2026-10-24 拍板：「脚本点击时默认点击中间」）。

    > 2026-10-24 我一度把它改成"上三分之一"（因为用户常把底下那排小按钮一起框进来，点正中有踩到按钮的风险）；
    > 老板的规格明确要求**默认点中间**，并按"框选时不要过分超出目标大小"来解决这个风险 —— 已改回正中。
    > `at` 保留给以后按目标微调（`0.5` 就是正中；夹在框内，防止给到 0 或 1 落到边上）。
    """
    a = 0.5 if at is None else min(max(float(at), 0.05), 0.95)
    return {"rel_x": round(box["x"] + box["w"] / 2.0, 4),
            "rel_y": round(box["y"] + box["h"] * a, 4)}


def get(harness_id: str) -> dict | None:
    """读回框选档案；**四块框里任意一块有**就算"框过"（2026-10-24 加了会话列表 / 新建会话）。"""
    rec = _load().get(str(harness_id or ""))
    if not isinstance(rec, dict):
        return None
    return rec if any(rec.get(k) for k in ("input", "send", "sessions", "new_session")) else None


def set_regions(harness_id: str, *, input_box=None, send_box=None, keywords=None,
                frame=None, source: str = "box-select",
                sessions_box=None, new_session_box=None,
                activation_message=None, tests=None) -> dict:
    """保存框选。

    2026-10-24 扩了口径（老板口述的"视觉脚本注册模板"）：
    - `input_box` / `send_box`：老的两块；
    - `sessions_box` / `new_session_box`：**会话列表**与**新建会话按钮**（视觉脚本"测试新建会话"要用）；
    - `activation_message`：这套激活方式**固定的那句提示话术**（提醒查邮箱）；
    - `tests`：上次"测试新建会话/测试发送消息"的结果（**只记录**，替人判断的事不做）；
    - `frame`：相对比例的基准（真实客户区；最小化占位值会被端点拒掉）。
    "至少给一个框"的规矩不变（四块框任意一块都算）。
    """
    hid = str(harness_id or "").strip()
    if not hid:
        return {"ok": False, "why": "没给 harness_id"}
    ib = _clamp_box(input_box) if input_box else None
    sb = _clamp_box(send_box) if send_box else None
    ssb = _clamp_box(sessions_box) if sessions_box else None
    nsb = _clamp_box(new_session_box) if new_session_box else None
    if input_box and not ib:
        return {"ok": False, "why": "输入框那个框给得不全（要 x/y/w/h，且 w、h 大于 0）"}
    if send_box and not sb:
        return {"ok": False, "why": "发送按钮那个框给得不全（要 x/y/w/h，且 w、h 大于 0）"}
    if sessions_box and not ssb:
        return {"ok": False, "why": "会话列表那个框给得不全（要 x/y/w/h，且 w、h 大于 0）"}
    if new_session_box and not nsb:
        return {"ok": False, "why": "新建会话那个框给得不全（要 x/y/w/h，且 w、h 大于 0）"}
    if not any((ib, sb, ssb, nsb)):
        return {"ok": False, "why": "至少要框出一个：输入框 / 发送按钮 / 会话列表 / 新建会话"}
    data = _load()
    prev = data.get(hid) if isinstance(data.get(hid), dict) else {}
    kws = [str(k).strip() for k in (keywords or prev.get("keywords") or []) if str(k).strip()]
    msg = activation_message if activation_message is not None else prev.get("activation_message")
    rec = {"input": ib or prev.get("input"), "send": sb or prev.get("send"),
           "sessions": ssb or prev.get("sessions"),
           "new_session": nsb or prev.get("new_session"),
           "keywords": kws,
           "frame": frame or prev.get("frame") or {},
           "activation_message": str(msg).strip() if msg else "",
           "tests": tests if isinstance(tests, dict) else (prev.get("tests") or {}),
           "source": str(source or "box-select"),
           "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    data[hid] = rec
    _save(data)
    return {"ok": True, "harness_id": hid, "regions": rec}


def clear(harness_id: str) -> bool:
    hid = str(harness_id or "")
    data = _load()
    had = hid in data
    if had:
        data.pop(hid, None)
        _save(data)
    return had
