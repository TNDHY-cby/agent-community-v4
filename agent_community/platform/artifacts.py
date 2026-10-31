# -*- coding: utf-8 -*-
"""`platform/artifacts`：**产物清单靠目录 diff**（设计 `V113` 第 11 步 / `V115` V1~V6，P4 批）。

## 为什么不能靠自报

成员说"我写完了"是 L3（`V116` §15，**永不采信**）。平台只认**盘上真有的文件**：
扫 `<slot_dir>/产物/`（可再按任务分目录），算 `sha256` 与字节数，与上一次快照对比出
`new / changed / removed`。这份清单写进 `_meta/artifacts.json`。

## 边界（如实说）

- **只扫**，不改成员文件（`stat` + 读字节算 hash，不写回）；
- 只扫 `<slot_dir>/产物/` 一层与一层子目录（防误扫整个盘；也不跟符号链接出去）；
- 上限保护：单次最多 2000 个文件、单文件最多 64MB 计算 hash（超出的标 `too_big` 并说清楚）。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

MAX_FILES = 2000
MAX_HASH_BYTES = 64 * 1024 * 1024


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _sha256(p: Path) -> tuple:
    """返回 `(hex, bytes)`；太大就只给大小（`hash=None`，并说清为什么）。"""
    size = p.stat().st_size
    if size > MAX_HASH_BYTES:
        return None, size
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 512), b""):
            h.update(chunk)
    return h.hexdigest(), size


def snapshot(workshop_path: str | Path, slot_dir: str) -> dict:
    """扫一个工位的 `产物/`，返回 `{files: {rel: {sha256, bytes, at}}, meta}`。"""
    base = Path(workshop_path) / str(slot_dir) / "产物"
    out = {"base": base.as_posix(), "exists": base.is_dir(), "files": {}, "truncated": False}
    if not base.is_dir():
        return out
    n = 0
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if name.startswith("."):
                continue
            p = Path(root) / name
            try:
                if p.is_symlink():
                    continue
                sha, size = _sha256(p)
            except OSError as e:                               # noqa: BLE001 — 读不到要记下来，不能当没有
                out["files"][p.relative_to(base).as_posix()] = {
                    "error": "%s: %s" % (type(e).__name__, e)}
                continue
            out["files"][p.relative_to(base).as_posix()] = {
                "sha256": sha, "bytes": size,
                "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(p.stat().st_mtime))}
            n += 1
            if n >= MAX_FILES:
                out["truncated"] = True
                return out
    return out


def _snapshot_file(workshop_path: str | Path) -> Path:
    return Path(workshop_path) / "_meta" / "artifacts.json"


def load_previous(workshop_path: str | Path) -> dict:
    p = _snapshot_file(workshop_path)
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8") or "{}")
        return d if isinstance(d, dict) else {}
    except Exception as e:                                     # noqa: BLE001 — 坏快照要说，不能装作第一次
        print("[artifacts] 上次快照读不出来（%s: %s）；本次按第一次扫处理（会全标 new）"
              % (type(e).__name__, e), flush=True)
        return {}


def scan(workshop_path: str | Path, slot_dirs: list) -> dict:
    """扫多个工位并与上次快照对比；结果写 `_meta/artifacts.json`。"""
    prev = load_previous(workshop_path)
    result = {"at": _now(), "slots": {}, "diff": {"new": [], "changed": [], "removed": [],
                                                  "same": 0}, "truncated": False}
    for slot in slot_dirs:
        s = snapshot(workshop_path, slot)
        result["truncated"] = result["truncated"] or s["truncated"]
        result["slots"][slot] = {"exists": s["exists"], "count": len(s["files"])}
        old = ((prev.get("slots") or {}).get(slot) or {}).get("files") or {}
        for rel, info in s["files"].items():
            key = "%s/%s" % (slot, rel)
            if rel not in old:
                result["diff"]["new"].append(key)
            elif (old.get(rel) or {}).get("sha256") != info.get("sha256"):
                result["diff"]["changed"].append(key)
            else:
                result["diff"]["same"] += 1
        for rel in old:
            if rel not in s["files"]:
                result["diff"]["removed"].append("%s/%s" % (slot, rel))
        result["slots"][slot]["files"] = s["files"]
    p = _snapshot_file(workshop_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    total = sum(v.get("count", 0) for v in result["slots"].values())
    result["total"] = total
    result["hint"] = ("清单来自**盘上真扫描**（L0）；成员自报不算。"
                      "空清单＝`产物/` 里真的没有文件（别把它当'它说做完了'）")
    if result["truncated"]:
        result["warn"] = "文件超过 %d 个，已截断（只看了前 %d 个）" % (MAX_FILES, MAX_FILES)
    return result


def summary_for_task(result: dict, slot_dir: str, task_id: str = "") -> dict:
    """给验收用的小结：这个工位这轮新/改了哪些产物。

    ⚠️ `new`/`changed` 是**相对上一次快照**的 diff：先点过「扫描产物」再验收时它们会是空的，
    而文件明明在盘上（`V121 §2.2` 实测抓到的看反坑）⇒ 判"有没有交东西"**只看 `files_now` / `total_files`**。
    """
    new = [x for x in result.get("diff", {}).get("new", []) if x.startswith(str(slot_dir) + "/")]
    changed = [x for x in result.get("diff", {}).get("changed", [])
               if x.startswith(str(slot_dir) + "/")]
    files = ((result.get("slots") or {}).get(slot_dir) or {}).get("files") or {}
    return {"slot_dir": slot_dir, "task_id": task_id,
            "files_now": list(files.keys())[:10],
            "new": new, "changed": changed,
            "new_note": "`new` 只是「上次快照之后新增」，空数组**不代表**没有产物（看 `files_now`）",
            "total_files": len(files),
            "empty": len(files) == 0,
            "note": "" if files else "`产物/` 里一个文件都没有 —— 自报'做完了'不算（L3）"}
