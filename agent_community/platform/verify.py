# -*- coding: utf-8 -*-
"""`platform/verify`：**目录校验**（设计 `V109` §7 / `V115` V1~V3，P4 批）。

只做 `stat`，**不代建、不代写**（平台不搬运内容）。缺什么就报什么，并给四要素（怎么补）。
"""
from __future__ import annotations

import re
from pathlib import Path

INDEX_LEAD = "_lead"
DIVISION = "分工.md"
NOTICE = "_notice.md"

_VERSION_RE = re.compile(r"^\s*-\s*版本\s*[:：]\s*(\d+)\s*$", re.M)


def _four(who: str, what: str, where: str, how: str) -> dict:
    return {"who": who, "what": what, "where": where, "how": how}


def read_division_version(path: Path) -> int | None:
    """读 `分工.md` 里的 `- 版本: <int>`；没有/读不出返回 None（**不猜**）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _VERSION_RE.search(text)
    return int(m.group(1)) if m else None


def verify_dirs(workshop_path: str | Path, slots: list, *, require_notice: bool = True) -> dict:
    """校验工作区目录与文件（`V109` §7 的四条 + 分工.md 版本行）。

    `slots`：`[{"slot_dir": "前端组/员工01", "harness_id": "...", "lead": False}, ...]`
    """
    wp = Path(workshop_path)
    missing, groups = [], {}
    checked = 0

    def add(slot: str, what: str, path: str, how: str, who: str = "组长"):
        missing.append({"slot_dir": slot, "what": what, "path": path,
                        "four": _four(who, what, path, how)})

    if not wp.is_dir():
        return {"ok": False, "checked": 0, "missing": [{
            "slot_dir": "", "what": "工作区目录不存在", "path": wp.as_posix(),
            "four": _four("用户", "工作区目录不存在", wp.as_posix(), "先把工作区路径设对（或让组长建）")}],
            "next": "先把工作区路径设对"}

    # 组长区
    checked += 1
    if not (wp / INDEX_LEAD).is_dir():
        add("", "组长工作区目录", (wp / INDEX_LEAD).as_posix(),
            "让组长在 workshop_path 下建 `_lead/` 并写建模文档")

    # 每个工位
    for s in slots or []:
        slot = str(s.get("slot_dir") or "").strip("/")
        if not slot:
            continue
        is_lead = bool(s.get("lead"))
        group = slot.split("/")[0]
        if group not in groups:
            groups[group] = (wp / group)
            checked += 1
            if not (wp / group).is_dir():
                add("", "组目录", (wp / group).as_posix(),
                    "让组长在 workshop_path 下建 `%s/`" % group)
            division = wp / group / DIVISION
            checked += 1
            if not division.is_file():
                add("", "分工.md", division.as_posix(),
                    "让组长写 `%s/分工.md`（第一段要有 `- 版本: 1`）" % group)
            else:
                ver = read_division_version(division)
                checked += 1
                if ver is None:
                    add("", "分工.md 缺版本行", division.as_posix(),
                        "在 `分工.md` 顶部加一行 `- 版本: 1`（平台靠它判断文件比库新）")
            notice = wp / group / NOTICE
            if require_notice:
                checked += 1
                if not notice.is_file():
                    add("", "组变更通知 _notice.md", notice.as_posix(),
                        "让组长建 `%s/_notice.md`（只追加；每次派活前平台会写一行）" % group)

        for sub in ("任务", "产物"):
            checked += 1
            d = wp / slot / sub
            if not d.is_dir():
                add(slot, "成员%s目录" % sub, d.as_posix(),
                    "让组长建 `%s/%s/`（派活与产物都落这里）" % (slot, sub))
        if is_lead:
            checked += 1
            if not (wp / slot / NOTICE).is_file():
                add(slot, "组长 _notice.md", (wp / slot / NOTICE).as_posix(),
                    "让组长建 `%s/_notice.md`" % slot)

    ok = not missing
    out = {"ok": ok, "checked": checked, "missing": missing,
           "slots_checked": len(slots or []),
           "next": "" if ok else ("让组长补齐这 %d 项，然后点「重新校验」（平台不代建）"
                                  % len(missing))}
    return out
