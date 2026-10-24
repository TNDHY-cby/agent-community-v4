# -*- coding: utf-8 -*-
"""「教一次位置」的存储：每个 harness 记住**它在窗口里的输入框位置**（相对坐标 0~1）。

## 为什么存相对坐标（而不是屏幕坐标）

窗口会被拖动、换显示器、改缩放。存**相对客户区的比例**（0~1）就与这些无关：
`(0.5, 0.9)` ＝ "窗口水平中间、竖直 90% 处"，窗口挪多大都还落在同一个位置。

## 为什么单独一个文件

它属于"用户教的一次手工操作"，跟平台主状态（任务/工作间/会话）没有关系：
放 `DATA_DIR/input_anchors.json` —— **不进 `save_state()` 那一坨**，
就不会因为主状态的空覆盖保护/降级实例那套逻辑被牵连；也方便单独删。

## 诚实边界

教过就教过，没教过就**没有**（`get()` 返回 `None`），绝不猜一个位置出来 ——
乱点会点坏别人的东西。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

FILE_NAME = "input_anchors.json"


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
    except Exception as e:                                   # noqa: BLE001 — 坏了要说话，但也别炸掉调用方
        print("[input_anchor] 读 %s 失败（当作没有教过）：%s: %s" % (p.name, type(e).__name__, e), flush=True)
        return {}


def _save(data: dict) -> None:
    p = _path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def get(harness_id: str) -> dict | None:
    """取这个 harness 教过的位置（没教过 ⇒ `None`，**不猜**）。"""
    rec = _load().get(str(harness_id or ""))
    if not isinstance(rec, dict):
        return None
    if "rel_x" not in rec or "rel_y" not in rec:
        return None
    return rec


def set_anchor(harness_id: str, rel_x: float, rel_y: float, *, title: str = "",
               source: str = "manual", frame: str = "", win_w: int = 0,
               win_h: int = 0) -> dict:
    """记住位置并落盘（`rel_x/rel_y` 会被夹到 0~1，越界要如实说而不是硬塞）。"""
    hid = str(harness_id or "").strip()
    if not hid:
        return {"ok": False, "why": "没给 harness_id"}
    raw_x, raw_y = float(rel_x), float(rel_y)
    clamped = not (0.0 <= raw_x <= 1.0 and 0.0 <= raw_y <= 1.0)
    x = min(max(raw_x, 0.0), 1.0)
    y = min(max(raw_y, 0.0), 1.0)
    data = _load()
    prev = data.get(hid) if isinstance(data.get(hid), dict) else {}
    # 历史（最近 3 次）：界面改版后旧位置会失准，留几条让用户能换回上一条
    hist = [h for h in (prev.get("history") or []) if isinstance(h, dict)][-2:]
    if "rel_x" in prev and "rel_y" in prev:
        hist.append({"rel_x": prev.get("rel_x"), "rel_y": prev.get("rel_y"),
                     "frame": prev.get("frame") or "", "at": prev.get("at") or ""})
    data[hid] = {"rel_x": round(x, 4), "rel_y": round(y, 4), "title": str(title or ""),
                 "source": str(source or "manual"), "frame": str(frame or ""),
                 "win_w": int(win_w or 0), "win_h": int(win_h or 0),
                 "history": hist, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _save(data)
    out = {"ok": True, "harness_id": hid, "rel_x": data[hid]["rel_x"], "rel_y": data[hid]["rel_y"],
           "title": data[hid]["title"], "at": data[hid]["at"]}
    if clamped:
        out["warning"] = ("你给的位置 (%s, %s) 越界了，已夹到 (%.3f, %.3f) —— "
                          "位置应该是窗口内的**相对比例** 0~1" % (raw_x, raw_y, x, y))
    return out


def clear(harness_id: str) -> bool:
    """忘掉这个位置（返回是否原来有）。"""
    hid = str(harness_id or "")
    data = _load()
    had = hid in data
    if had:
        data.pop(hid, None)
        _save(data)
    return had


def set_web(harness_id: str, *, ws: str, idx: int, target_id: str = "", title: str = "") -> dict:
    """记住"浏览器级投递"选中的目标（页面 ws 地址 + 第几个输入框）。

    为什么和"表层交互的位置"分开存：一个 harness 可能两种路子都用过；
    两者混在一起会让"到底按哪条走"变得说不清（一处真相：两条路各记各的）。
    """
    hid = str(harness_id or "").strip()
    if not hid:
        return {"ok": False, "why": "没给 harness_id"}
    data = _load()
    rec = data.get(hid) if isinstance(data.get(hid), dict) else {}
    rec["web"] = {"ws": str(ws or ""), "idx": int(idx), "target_id": str(target_id or ""),
                  "title": str(title or ""), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    data[hid] = rec
    _save(data)
    return {"ok": True, "harness_id": hid, "web": rec["web"]}


def get_web(harness_id: str) -> dict | None:
    """取这个 harness 记住的浏览器级投递目标（没记过 ⇒ `None`）。"""
    rec = _load().get(str(harness_id or ""))
    if not isinstance(rec, dict):
        return None
    web = rec.get("web")
    if not isinstance(web, dict) or not web.get("ws"):
        return None
    return web


def set_bind(harness_id: str, *, hwnd: int = 0, title: str = "", ws: str = "", idx: int = 0,
             source: str = "manual") -> dict:
    """**手动绑定**：用户直接指定"这位员工的界面就是这个窗口/这个页面"。

    为什么需要它（2026-10-23，前端反馈"已登记员工还没有会话名 ⇒ 送不了话"）：
    自动定位靠"会话名 ≈ 窗口标题"；可会话名要用户走完命名流程才有。
    绑定让用户**在候选列表里点一下**就定下来 —— 不猜、不依赖命名、且是通用动作（与具体应用无关）。
    """
    hid = str(harness_id or "").strip()
    if not hid:
        return {"ok": False, "why": "没给 harness_id"}
    if not int(hwnd or 0) and not str(ws or "").strip():
        return {"ok": False, "why": "要么给窗口句柄 hwnd，要么给页面 ws"}
    data = _load()
    rec = data.get(hid) if isinstance(data.get(hid), dict) else {}
    rec["bind"] = {"hwnd": int(hwnd or 0), "title": str(title or ""),
                    "ws": str(ws or ""), "idx": int(idx or 0),
                    "source": str(source or "manual"),
                    "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    data[hid] = rec
    _save(data)
    return {"ok": True, "harness_id": hid, "bind": rec["bind"]}


def get_bind(harness_id: str) -> dict | None:
    """取这个 harness 的手动绑定（没绑过 ⇒ None）。"""
    rec = _load().get(str(harness_id or ""))
    if not isinstance(rec, dict):
        return None
    b = rec.get("bind")
    if not isinstance(b, dict) or (not b.get("hwnd") and not b.get("ws")):
        return None
    return b


def clear_bind(harness_id: str) -> bool:
    """取消绑定（返回原来有没有）。"""
    hid = str(harness_id or "")
    data = _load()
    rec = data.get(hid)
    had = isinstance(rec, dict) and isinstance(rec.get("bind"), dict)
    if had:
        rec.pop("bind", None)
        data[hid] = rec
        _save(data)
    return had
