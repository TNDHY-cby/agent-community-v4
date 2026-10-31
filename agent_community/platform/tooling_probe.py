# -*- coding: utf-8 -*-
"""`platform/tooling_probe`：**实测 harness 能不能写文件**（`V116` §9 的组长门槛落地）。

## 为什么需要它

`V116` §9 写死了"`lead_eligible` 由平台按**实测** tooling 算出来，不许手填"。
但注册信息里往往没有 `tools` ⇒ `fs_write=null` ⇒ 组长候选永远是 0（真机实测就是这样）。

## 怎么测（只做安全的、确定的事）

- 在 `<AC_DATA_DIR>/probe/<harness_id>/` 下**真写一个文件再读回**；
- 这是**平台自己的目录**，不碰用户数据、不碰工作区；
- 结果落 `AC_DATA_DIR/harness_tooling.json`，`lead_roster` 优先用实测值。

注意边界：这一步测的是**平台能不能在这个目录写**，不是"那个 agent 有没有 fs 工具"。
所以：实测为真只说明"平台这里可写"，**要证明 agent 自己能写**，仍然要看它报的 `tools`
或它真写出产物（P4 的目录 diff）。**两条都留痕，不混为一谈。**
"""
from __future__ import annotations

import json
import time
from pathlib import Path


def _path() -> Path:
    from .env_spec import data_dir

    return Path(data_dir()) / "harness_tooling.json"


def _load() -> dict:
    p = _path()
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8") or "{}")
        return d if isinstance(d, dict) else {}
    except Exception as e:                                     # noqa: BLE001 — 读坏要说，不能当没测过
        print("[probe] 实测结果读不出来（%s: %s）" % (type(e).__name__, e), flush=True)
        return {}


def _save(d: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def plateau_dir(harness_id: str) -> Path:
    from .env_spec import data_dir

    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(harness_id))[:60] or "h"
    return Path(data_dir()) / "probe" / safe


def probe_platform_fs(harness_id: str) -> dict:
    """**真写一次再读回**，判断"平台能不能给它准备可写落点"。"""
    d = plateau_dir(harness_id)
    at = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        d.mkdir(parents=True, exist_ok=True)
        f = d / "probe.txt"
        payload = "ok %s" % at
        f.write_text(payload, encoding="utf-8")
        back = f.read_text(encoding="utf-8")
        ok = back == payload
        return {"ok": ok, "at": at, "dir": d.as_posix(),
                "detail": "写入并读回一致" if ok else "读回与写入不一致（落点不可靠）"}
    except OSError as e:
        return {"ok": False, "at": at, "dir": d.as_posix(),
                "detail": "写不进去（%s: %s）—— 换一个有写权限的数据目录" % (type(e).__name__, e)}


def record(harness_id: str, result: dict) -> dict:
    d = _load()
    d[str(harness_id)] = {"fs_write": bool(result.get("ok")), "at": result.get("at"),
                          "dir": result.get("dir"), "detail": result.get("detail"),
                          "scope": "这是**平台侧落点**可写性；不等于那个 agent 自己有 fs 工具"}
    _save(d)
    return {"ok": True, "harness_id": harness_id, "measured": d[str(harness_id)]}


def measured(harness_id: str) -> dict:
    return _load().get(str(harness_id)) or {}
