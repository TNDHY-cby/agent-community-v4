# -*- coding: utf-8 -*-
"""隔间「可被继任员工继承」的内核（V53，老板 2026-10-17 拍板「可以」）。

## 一句话

员工换人时，把**旧隔间里的产物**复制给继任者的隔间，并在继任者隔间里写一份
**`legacy.md`（继承文）**，如实写明：从谁继承 / 搬了哪些 / **没搬哪些与原因** / 逐项核对结论。

## 本模块的分层（照 `debate.py` 的规矩：**判定与搬运可单测，路由只做编排**）

- `plan_inherit()`  —— **只读**：算出要搬什么、跳过什么（不写盘）；
- `perform_inherit()` —— 真复制 + **逐项核对**（存在且 sha256 一致才算搬成功）；
- `render_legacy()` / `write_legacy()` —— 继承文（**继承的"交代"和搬运本身同等重要**）。

## 五条钉死的口径（V53 §2；改这里等于改口径，要同步文档）

1. **追加，不接管**：只往新隔间放东西，**绝不删旧隔间、绝不改旧产物**；
2. **来源留在继任者隔间里**：`legacy.md` 是硬要求，不是可选项；
3. **搬完逐项核对**：核对通过才 `verified=True`（平台不采信"我搬过了"这类声明）；
4. **跳过必须给原因**：超限 / 撞名不一致 / 属于"不可继承"类 —— 每条都进 `skipped` 与 `legacy.md`；
5. **跨工作间禁止**：隔间属于各自的工作间（由路由把关，内核用 `is_inside` 兜底）。

## ⚠️ 明确**不继承**的东西（V53 的"产物与上下文"不等于"身份"）

| 不继承 | 为什么 |
|---|---|
| `inbox/` `replies/`（邮箱） | 邮箱是**寻址**（"谁收到就是谁的"）。把旧成员的任务邮件搬给继任者＝**伪造他已经收到过**，那是假状态。 |
| `bridge.py` / `connect_contract.json` / `activation_prompt.txt` | 这些是**旧成员的桥与身份产物**（绑在它的会话上）。搬过来＝新成员拿着**别人的桥**，必错。新成员的桥由平台按他自己的会话重新派生。 |
| `temp/` | 草稿区（声明为"临时"）。搬它只会把噪声带进继任者的工作区；**如实写进"未搬运"**而不静默丢。 |
| 超过上限的大文件 | 见参数 `max_file_mb` / `max_total_mb`；跳过但**记原因**。 |
"""
from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path

from .compartment import is_inside

# 上限默认值（V53 拍板点③：5 MiB / 50 MiB）。
# 它们是**操作参数**（端点可覆盖），**不是部署差异** ⇒ 不做环境变量、不登记 env_spec。
DEFAULT_MAX_FILE_MB = 5.0
DEFAULT_MAX_TOTAL_MB = 50.0

NEVER_COPY_DIRS = ("inbox", "replies", "temp")
NEVER_COPY_FILES = ("bridge.py", "connect_contract.json", "activation_prompt.txt")

_NEVER_DIR_REASON = {
    "inbox": "邮箱（寻址，不继承）——把旧成员的任务搬过来等于伪造“他已收到”",
    "replies": "邮箱（寻址，不继承）",
    "temp": "草稿区（声明为临时）——不搬，避免把噪声带进继任者工作区",
}
_NEVER_FILE_REASON = "旧成员的桥/身份产物（绑在它的会话上）——继任者的桥要按他自己的会话重新派生"


def human_mb(n_bytes: float) -> str:
    return "%.2f MiB" % (n_bytes / 1024.0 / 1024.0)


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _walk_files(root: Path):
    """产出 (相对 posix 路径, 绝对路径)，**顺序稳定**（按相对路径排序）⇒ 结果可复现。"""
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out.append((p.relative_to(root).as_posix(), p))
    return out


def plan_inherit(src_dir, dst_dir, max_file_mb: float = DEFAULT_MAX_FILE_MB,
                 max_total_mb: float = DEFAULT_MAX_TOTAL_MB) -> dict:
    """**只读**：算出"要搬什么、跳过什么"。不写盘、不改任何文件。

    返回：`{"move": [{"path","bytes"}], "skipped": [{"path","reason"}], "total_bytes": int}`
    """
    src = Path(str(src_dir or ""))
    dst = Path(str(dst_dir or ""))
    out = {"move": [], "skipped": [], "total_bytes": 0,
           "src": str(src), "dst": str(dst),
           "max_file_mb": float(max_file_mb), "max_total_mb": float(max_total_mb)}
    if not src.is_dir():
        out["error"] = "来源隔间不存在：%s" % src
        return out
    max_file = float(max_file_mb) * 1024 * 1024
    max_total = float(max_total_mb) * 1024 * 1024
    total = 0

    for rel, abspath in _walk_files(src):
        parts = rel.split("/")
        top = parts[0]
        if top in NEVER_COPY_DIRS:
            out["skipped"].append({"path": rel, "reason": _NEVER_DIR_REASON.get(top, "不继承的目录")})
            continue
        if rel in NEVER_COPY_FILES:
            out["skipped"].append({"path": rel, "reason": _NEVER_FILE_REASON})
            continue
        try:
            size = abspath.stat().st_size
        except OSError as e:  # noqa: BLE001
            out["skipped"].append({"path": rel, "reason": "读不到大小：%s: %s" % (type(e).__name__, e)})
            continue
        if size > max_file:
            out["skipped"].append({"path": rel, "reason": "超过单文件上限（%s）" % human_mb(max_file)})
            continue
        # 目标已存在：**内容一致 ⇒ 跳过（幂等）**；内容不同 ⇒ **不覆盖**（不许悄悄改掉继任者的东西）
        target = dst / rel
        if target.is_file():
            try:
                same = target.stat().st_size == size and sha256_of(target) == sha256_of(abspath)
            except OSError:
                same = False
            if same:
                out["skipped"].append({"path": rel, "reason": "目标已存在且内容一致（幂等跳过）"})
            else:
                out["skipped"].append({"path": rel, "reason": "目标已存在且内容不同（不覆盖：那是继任者自己的东西）"})
            continue
        if total + size > max_total:
            out["skipped"].append({"path": rel, "reason": "超过总量上限（%s）" % human_mb(max_total)})
            continue
        total += size
        out["move"].append({"path": rel, "bytes": size})

    out["total_bytes"] = total
    return out


def perform_inherit(plan: dict, dst_dir=None) -> dict:
    """按 `plan` 真复制，并**逐项核对**（存在 + 字节一致）。返回三态如实的结果。"""
    src = Path(plan.get("src") or "")
    dst = Path(str(dst_dir)) if dst_dir is not None else Path(plan.get("dst") or "")
    moved, failed = [], []
    for item in plan.get("move", []):
        rel = item["path"]
        s, d = src / rel, dst / rel
        try:
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
        except OSError as e:  # noqa: BLE001
            failed.append({"path": rel, "reason": "%s: %s" % (type(e).__name__, e)})
            continue
        try:
            ok = d.is_file() and d.stat().st_size == s.stat().st_size and sha256_of(d) == sha256_of(s)
        except OSError as e:  # noqa: BLE001
            ok = False
            failed.append({"path": rel, "reason": "核对出错：%s: %s" % (type(e).__name__, e)})
        if ok:
            moved.append({"path": rel, "bytes": item.get("bytes", 0)})
        elif not any(f["path"] == rel for f in failed):
            failed.append({"path": rel, "reason": "搬到目标后核对不一致（字节或长度不同）"})
    verified = bool(moved or not plan.get("move")) and not failed
    return {"moved": moved, "failed": failed, "skipped": list(plan.get("skipped") or []),
            "verified": verified, "total_bytes": sum(m["bytes"] for m in moved)}


def render_legacy(from_member_id: str, from_role: str, from_display: str,
                  src_dir, dst_dir, ws_id: str, result: dict, when: float | None = None) -> str:
    """继承文（写进**继任者**隔间根）。**这张表就是"交代"**：搬了什么、没搬什么、为什么。"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(when or time.time()))
    moved, skipped = result.get("moved") or [], result.get("skipped") or []
    failed = result.get("failed") or []
    lines = [
        "# 继承文（隔间移交索引）",
        "",
        "- 来源成员：`%s`（职位：%s，显示名：%s）" % (from_member_id, from_role or "—", from_display or "—"),
        "- 继承时间：%s" % ts,
        "- 由谁触发：用户（工作间 %s）" % ws_id,
        "- 来源隔间：`%s`" % src_dir,
        "- 核对结论：**已搬运 %d 项 / 跳过 %d 项 / 失败 %d 项**（verified=%s）"
        % (len(moved), len(skipped), len(failed), str(bool(result.get("verified"))).lower()),
        "",
        "## 已搬运（逐项核对：存在且 sha256 一致）",
        "",
        "| 相对路径 | 字节 |",
        "|---|---|",
    ]
    lines += ["| `%s` | %s |" % (m["path"], m.get("bytes", "")) for m in moved] or ["| （无） | |"]
    lines += ["", "## 未搬运（如实列原因）", "", "| 相对路径 | 原因 |", "|---|---|"]
    lines += ["| `%s` | %s |" % (s["path"], s["reason"]) for s in skipped] or ["| （无） | |"]
    if failed:
        lines += ["", "## 失败（要人看）", "", "| 相对路径 | 原因 |", "|---|---|"]
        lines += ["| `%s` | %s |" % (f["path"], f["reason"]) for f in failed]
    lines += [
        "",
        "## 说明",
        "- 本次**只复制**，旧隔间**原样保留**（`%s`），可随时回查。" % src_dir,
        "- **不继承**的东西：`inbox/`、`replies/`（邮箱＝寻址，不是产物）、"
        "`bridge.py` / `connect_contract.json` / `activation_prompt.txt`（旧成员的桥与身份，"
        "继任者的桥要按他自己的会话重新派生）、`temp/`（草稿区）。",
        "- 本文件是**交接索引**，不是\"完成声明\"（那套词汇在 `platform/handover.py`），"
        "也不是辩论陈述（`platform/debate.py`）。",
        "",
    ]
    return "\n".join(lines)


def write_legacy(dst_dir, text: str) -> Path:
    p = Path(str(dst_dir)) / "legacy.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return p


def inherit(src_dir, dst_dir, ws_dir, from_meta: dict, ws_id: str,
            max_file_mb: float = DEFAULT_MAX_FILE_MB,
            max_total_mb: float = DEFAULT_MAX_TOTAL_MB) -> dict:
    """一步到位：**找（plan）→ 搬 → 核对 → 写继承文**。失败也返回结构（不抛栈给调用者）。

    `ws_dir` 用来兜住"不许跨工作间"：**来源与目标都必须在同一个工作间目录内**。
    """
    src, dst, root = Path(str(src_dir or "")), Path(str(dst_dir or "")), Path(str(ws_dir or ""))
    if not src.is_dir():
        return {"success": False, "verified": False, "reason": "来源隔间不存在：%s" % src,
                "moved": [], "skipped": [], "failed": []}
    if not (is_inside(src, root) and is_inside(dst, root)):
        return {"success": False, "verified": False,
                "reason": "隔间属于各自的工作间，不能跨间继承（来源或目标不在本工作间目录内）",
                "moved": [], "skipped": [], "failed": []}
    plan = plan_inherit(src, dst, max_file_mb, max_total_mb)
    if plan.get("error"):
        return {"success": False, "verified": False, "reason": plan["error"],
                "moved": [], "skipped": [], "failed": []}
    result = perform_inherit(plan, dst)
    text = render_legacy(from_meta.get("member_id", ""), from_meta.get("role", ""),
                         from_meta.get("display_name", ""), src, dst, ws_id, result)
    legacy = write_legacy(dst, text)
    result.update({"success": True, "reason": "", "legacy_path": str(legacy),
                   "legacy_text": text, "src": str(src), "dst": str(dst)})
    return result
