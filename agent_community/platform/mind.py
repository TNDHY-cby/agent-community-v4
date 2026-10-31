# -*- coding: utf-8 -*-
"""`platform/mind`：**心智根（根A）**—— agent 的永久记忆（设计 `V117` §1，P2.5 批）。

## 一句话

工作区（根B）是可以整根丢的；**记忆不能跟着丢**。所以每个 agent 有一份永久目录：

```
<AC_MIND_ROOT>/<agent_key>/
├─ 00_宪章.md          身份与规矩（人写，审忆员不改）
├─ 00_核心基模.md      当前认知（**只有审忆员能改**，且重大修正是待审）
├─ 01_记忆索引表.md    触发词 → 记忆文件（审忆员维护）
├─ memories/           一条一文件（append-only；按索引按需读）
├─ 02_任务日志/         日结写这里（<日期>.md）
├─ 06_死胡同/           走错的路 + 为什么
└─ _meta/mind.json     机器读：agent_key / 版本 / 创建时间
```

## 三条硬规则

1. **只增不改**：`memories/`、`02_任务日志/`、`06_死胡同/` 只追加（同一天重跑不会写第二份）；
2. **根A 不许放进根B**：`mind_path` 若在工作区内 ⇒ 闸门 G0 直接拦（`manifest.M005`）；
3. **原则：根A 只存认知，不存实体**：任务与产物永远留工作区（对方 2026-10-27 答案 3）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

CONSTITUTION = "00_宪章.md"
CORE_MODEL = "00_核心基模.md"
INDEX = "01_记忆索引表.md"

CONSTITUTION_DEFAULT = """# 00_宪章 · {agent_key}

> 这份宪章由用户/负责人维护，**审忆员不得改写**。

1. 我只做被交代的活，不自作主张扩大范围。
2. 事实与推测分开写；不确定就写"不确定"。
3. 卡住了要说清卡在哪（`kind=block`），不假装干完了。
4. 我写的产物只落在工作区的 `产物/`；我的记忆只在心智根里追加。
5. 记忆只增不改：走错的路也要留着（写 `06_死胡同/`）。
"""

CORE_DEFAULT = """# 00_核心基模 · {agent_key}

> 我的当前认知基线。**只有夜间审忆员能改**，重大修正要走待审队列（用户拍板）。
> 初始为空是正常的：跑过几个项目、审忆员压过几次日志，它才会长出来。

- 我擅长：<待填>
- 我踩过的坑：见 `06_死胡同/`
- 最近修正：无
"""

INDEX_DEFAULT = """# 01_记忆索引表 · {agent_key}

> 触发词 → 记忆文件。**读记忆前先查这张表**，只读匹配的 1~3 个文件（禁止全盘扫描）。

| 触发词 | 记忆文件 | 一句话 |
|---|---|---|
| （示例）打包 | memories/示例-打包踩坑.md | 打完包要验体积 |
"""


def mind_root() -> Path:
    """根A 的根目录：`AC_MIND_ROOT` > `<AC_DATA_DIR>/minds`（**唯一解析点**）。"""
    env = (os.environ.get("AC_MIND_ROOT") or "").strip()
    if env:
        return Path(env)
    from .server import DATA_DIR

    return Path(DATA_DIR) / "minds"


def safe_key(agent_key: str) -> str:
    out = re.sub(r"[^\w\-.\u4e00-\u9fff]+", "_", str(agent_key or "")).strip("._") or "agent"
    return out[:80]


def mind_path(agent_key: str) -> Path:
    return mind_root() / safe_key(agent_key)


def ensure(agent_key: str, *, display_name: str = "") -> dict:
    """建（或确认）一个心智根。**幂等**：已有的文件一个都不动。"""
    p = mind_path(agent_key)
    created = []
    for d in ("memories", "02_任务日志", "06_死胡同", "_meta"):
        sub = p / d
        if not sub.is_dir():
            sub.mkdir(parents=True, exist_ok=True)
            created.append(d + "/")
    files = {CONSTITUTION: CONSTITUTION_DEFAULT, CORE_MODEL: CORE_DEFAULT, INDEX: INDEX_DEFAULT}
    for name, tpl in files.items():
        f = p / name
        if not f.is_file():
            f.write_text(tpl.format(agent_key=safe_key(agent_key)), encoding="utf-8")
            created.append(name)
    meta = p / "_meta" / "mind.json"
    info = {"agent_key": safe_key(agent_key), "display_name": display_name,
            "mind_path": str(p), "created_at": "", "schema": "coop.mind/1"}
    if meta.is_file():
        try:
            info.update(json.loads(meta.read_text(encoding="utf-8") or "{}"))
        except Exception as e:                                 # noqa: BLE001 — 读坏要说，不能装作没建过
            print("[mind] %s 读不出来（%s: %s）；按新建处理" % (meta, type(e).__name__, e), flush=True)
    else:
        info["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        meta.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        created.append("_meta/mind.json")
    return {"ok": True, "agent_key": safe_key(agent_key), "mind_path": str(p),
            "created": created, "files": describe(agent_key)}


def describe(agent_key: str) -> dict:
    """心智根实况（给人看/给判据用）：有哪些文件、多少条记忆。"""
    p = mind_path(agent_key)
    if not p.is_dir():
        return {"exists": False, "mind_path": str(p)}
    files = {}
    for name in (CONSTITUTION, CORE_MODEL, INDEX):
        f = p / name
        files[name] = f.stat().st_size if f.is_file() else 0
    memories = sorted((p / "memories").glob("*.md"))
    logs = sorted((p / "02_任务日志").glob("*.md"))
    dead = sorted((p / "06_死胡同").glob("*.md"))
    return {"exists": True, "mind_path": str(p), "files": files,
            "counts": {"memories": len(memories), "task_logs": len(logs), "dead_ends": len(dead)},
            "memories": [m.name for m in memories][-20:],
            "task_logs": [m.name for m in logs][-10:]}


def add_memory(agent_key: str, text: str, *, tags: list | None = None,
               source: str = "agent") -> dict:
    """agent 自己追加一条记忆（**append-only**，一条一文件）。"""
    body = str(text or "").strip()
    if not body:
        return {"ok": False, "code": "EMPTY", "error": "记忆内容不能为空"}
    ensure(agent_key)
    p = mind_path(agent_key) / "memories"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    slug = safe_key("-".join(tags or []) or "note")[:30]
    f = p / ("%s-%s.md" % (stamp, slug))
    i = 1
    while f.exists():                                          # 同一秒多条：加序号，不覆盖
        i += 1
        f = p / ("%s-%s-%d.md" % (stamp, slug, i))
    f.write_text("# 记忆 %s\n\n- 来源: %s\n- 标签: %s\n- 时间: %s\n\n%s\n"
                 % (f.stem, source, "、".join(tags or []) or "-",
                    time.strftime("%Y-%m-%dT%H:%M:%S"), body), encoding="utf-8")
    return {"ok": True, "file": f.name, "mind_path": str(mind_path(agent_key)),
            "note": "记忆只增不改；索引表由夜间审忆员更新（`01_记忆索引表.md`）"}


def append_daily(agent_key: str, *, date: str, body: str, kind: str = "task_log") -> dict:
    """日结写根A：`02_任务日志/<日期>.md` 或 `06_死胡同/<日期>.md`（**同一天追加，不覆盖**）。"""
    ensure(agent_key)
    sub = "02_任务日志" if kind == "task_log" else "06_死胡同"
    f = mind_path(agent_key) / sub / ("%s.md" % date)
    head = "" if f.is_file() else (
        "# %s · %s\n\n" % ("任务日志" if kind == "task_log" else "死胡同", date))
    with f.open("a", encoding="utf-8") as fh:
        fh.write(head + str(body).rstrip() + "\n")
    return {"ok": True, "file": "%s/%s.md" % (sub, date),
            "mind_path": str(mind_path(agent_key))}


# ── 待审队列（`_pending/*.diff`）：**只读**，给界面看，不在这里批准（2026-10-29）─────
#
# 为什么要有它：`tools/mind_reviewer.py` 把「改 `00_核心基模.md`」的建议写成
# `<mind>/_pending/<id>.diff` 等拍板，但那时只有 CLI —— **界面拿不到**，用户看不见待审项。
# 这两个函数只做「列」与「读」，**一个字节都不写**；批准仍走 CLI（`--approve`）。
PENDING_DIR = "_pending"


def _pending_dir(agent_key: str) -> Path:
    return mind_path(agent_key) / PENDING_DIR


def _safe_pending_name(name: str) -> str:
    """只接受 `_pending/` 下的**单层文件名**：不给路径分隔符、不给 `..`、不给绝对路径。"""
    n = str(name or "").strip()
    if not n or n in (".", "..") or "/" in n or "\\" in n or ":" in n:
        raise ValueError("待审项名字只能是一个文件名（不许带路径）：%r" % name)
    if not n.endswith(".diff"):
        raise ValueError("待审项必须是 `.diff` 文件：%r" % n)
    return n


def pending_list(agent_key: str) -> dict:
    """列 `_pending/` 下的待审项（**只读**）。带状态：`pending` 或 `approved`（有同名 `.approved.json`）。"""
    d = _pending_dir(agent_key)
    items = []
    if d.is_dir():
        for f in sorted(d.glob("*.diff")):
            st = f.stat()
            rec = f.with_suffix(".approved.json")
            app = f.with_suffix(".applied.json")
            status, approver, approved_at = "pending", "", ""
            applied_meta = {}
            if rec.is_file():
                try:
                    info = json.loads(rec.read_text(encoding="utf-8") or "{}")
                    status = "approved"
                    approver = str(info.get("approved_by") or "")
                    approved_at = str(info.get("at") or "")
                except Exception as e:                          # noqa: BLE001 — 审批记录读坏要说
                    print("[mind] 审批记录读不出来 %s（%s: %s）" % (rec, type(e).__name__, e),
                          flush=True)
                    status = "approved_record_unreadable"
            # ★2026-10-30：**落地记录优先** —— 已写进基模的项，状态必须是 `applied`（读真文件，不猜）
            if app.is_file():
                try:
                    applied_meta = json.loads(app.read_text(encoding="utf-8") or "{}")
                    status = "applied"
                except Exception as e:                      # noqa: BLE001 — 落地记录读坏要说
                    print("[mind] 落地记录读不出来 %s（%s: %s）" % (app, type(e).__name__, e),
                          flush=True)
                    status = "applied_record_unreadable"
                    applied_meta = {}
            text = ""
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError as e:                                # noqa: BLE001 — 读不到就只给元数据
                print("[mind] 待审项读不出来 %s（%s: %s）" % (f, type(e).__name__, e), flush=True)
            items.append({
                "name": f.name,
                "id": f.stem,
                "bytes": st.st_size,
                "modified_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)),
                "status": status,
                "approved_by": approver,
                "approved_at": approved_at,
                "applied": bool(applied_meta),
                "applied_by": str(applied_meta.get("applied_by") or ""),
                "applied_at": str(applied_meta.get("at") or ""),
                "before_sha256": str(applied_meta.get("before_sha256") or ""),
                "after_sha256": str(applied_meta.get("after_sha256") or ""),
                "archive": str(applied_meta.get("archive") or ""),
                # 目标文件：diff 里第一处 `--- a/<名字>` 或 `+++ b/<名字>`，取不到就空（不编）
                "target": _diff_target(text),
                "diff_lines": text.count("\n") + (1 if text and not text.endswith("\n") else 0),
                "note": "只读列表；批准走 `python tools/mind_reviewer.py --approve <id>`，平台不代批",
            })
    return {"ok": True, "agent_key": safe_key(agent_key), "mind_path": str(mind_path(agent_key)),
            "pending_dir": str(d), "count": len(items), "items": items,
            "hint": ("状态：`pending` 还没批 / `approved` 已批可落地 / `applied` **已写进基模**（不可再批再落地）/ "
                     "`*_record_unreadable` 记录坏了（如实标）。待审项来自夜间审忆员（默认关）。这里一个字节都不写；"
                     "要它生成待审项就跑 `python tools/mind_reviewer.py --apply`")}


def _diff_target(text: str) -> str:
    """从 diff 头部取目标文件名（`+++ b/00_核心基模.md`）；取不到返回空串。"""
    for line in str(text or "").splitlines()[:12]:
        if line.startswith("+++ ") or line.startswith("--- "):
            raw = line[4:].strip()
            if raw.startswith("b/") or raw.startswith("a/"):
                raw = raw[2:]
            return raw
    return ""


def pending_read(agent_key: str, name: str) -> dict:
    """读一条待审 diff 的原文（**只读**）。名字非法 ⇒ `ValueError`；不存在 ⇒ `None`。"""
    safe = _safe_pending_name(name)
    f = _pending_dir(agent_key) / safe
    if not f.is_file():
        return {"ok": False, "code": "NO_SUCH_PENDING", "name": safe,
                "pending_dir": str(_pending_dir(agent_key)),
                "next": "先 GET /api/mind/{agent_key}/pending 看有哪些"}
    text = f.read_text(encoding="utf-8", errors="replace")
    return {"ok": True, "name": safe, "id": f.stem, "bytes": f.stat().st_size,
            "target": _diff_target(text), "text": text,
            "note": "只读；平台不代批（批准走 POST …/pending/{name}/approve 或 CLI --approve）"}


def _apply_unified_diff(old: str, diff_text: str) -> tuple:
    """把**平台自己生成的** unified diff 应用到 `old`。返回 `(new_text, err)`。

    **只用最小实现**（不引第三方）：认 `--- a/x` / `+++ b/x` / `@@ -l,n +l,n @@` / ` ` / `-` / `+`。
    只要有一处对不上，就整篇拒（**不做模糊匹配**）—— 宁可让人手工应用，也不悄悄猜。
    """
    old_lines = old.splitlines(keepends=True)
    out: list = []
    cur = 0                     # 已复制到原文件的哪一行（0-based）
    i = 0
    lines = diff_text.splitlines(keepends=True)
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("--- ") or ln.startswith("+++ "):
            i += 1
            continue
        if not ln.startswith("@@"):
            i += 1
            continue
        m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", ln)
        if not m:
            return "", "diff 头看不懂：%r" % ln.strip()[:60]
        start_old = int(m.group(1)) - 1
        if start_old < cur:
            return "", ("hunk 起始行 %d 小于已处理位置 %d（diff 顺序不对）"
                        % (start_old + 1, cur + 1))
        if start_old > len(old_lines):
            return "", "hunk 起始行 %d 超出原文（共 %d 行）" % (start_old + 1, len(old_lines))
        out.extend(old_lines[cur:start_old])          # 上下文原样搬
        cur = start_old
        i += 1
        while i < len(lines) and not lines[i].startswith("@@"):
            body = lines[i]
            if body.startswith("--- ") or body.startswith("+++ "):
                break
            tag = body[:1]
            content = body[1:] if body[:1] in (" ", "-", "+") else body
            if tag == " ":
                if cur >= len(old_lines) or old_lines[cur].rstrip("\r\n") != content.rstrip("\r\n"):
                    return "", ("上下文对不上（原文第 %d 行）：期望 %r，实际 %r"
                                % (cur + 1, content.rstrip("\r\n")[:60],
                                   (old_lines[cur].rstrip("\r\n") if cur < len(old_lines) else "")[:60]))
                out.append(old_lines[cur])
                cur += 1
            elif tag == "-":
                if cur >= len(old_lines) or old_lines[cur].rstrip("\r\n") != content.rstrip("\r\n"):
                    return "", ("删除行对不上（原文第 %d 行）：期望 %r，实际 %r"
                                % (cur + 1, content.rstrip("\r\n")[:60],
                                   (old_lines[cur].rstrip("\r\n") if cur < len(old_lines) else "")[:60]))
                cur += 1
            elif tag == "+":
                out.append(content if content.endswith("\n") else content + "\n")
            i += 1
    out.extend(old_lines[cur:])
    return "".join(out), ""


def apply_pending(agent_key: str, name: str, *, approver: str) -> dict:
    """**人拍板之后**把待审 diff 真正写进 `00_核心基模.md`（不可逆操作，所以处处校验）。

    前提（不满足就拒，绝不半途改文件）：
      ① 已 `approve`（有 `.approved.json`）—— 未批 ⇒ `NOT_APPROVED`；
      ② 当前基模 hash == 提案时的 `core_before_sha256` —— 变过 ⇒ `CORE_CHANGED_SINCE_PROPOSAL`；
      ③ diff 能干净套上 —— 否则 `DIFF_DOES_NOT_APPLY`（附具体行，不猜）。
    成功时：原文先归档 `_archive/<日期>/00_核心基模.md.<stamp>.bak`，再写新内容，
    最后写 `<id>.applied.json`（before/after hash 都给）。
    """
    safe = _safe_pending_name(name)
    who = str(approver or "").strip()
    if not who:
        raise ValueError("批准人必填（谁落的这一笔必须留痕）")
    f = _pending_dir(agent_key) / safe
    rec = f.with_suffix(".approved.json")
    if not f.is_file():
        return {"ok": False, "code": "NO_SUCH_PENDING", "name": safe,
                "next": "先 GET /api/mind/{agent_key}/pending 看有哪些"}
    if not rec.is_file():
        return {"ok": False, "code": "NOT_APPROVED", "name": safe,
                "error": "这条还没批，不能落到基模",
                "next": "先 POST …/pending/%s/approve（批准人必填）" % safe}
    try:
        approval = json.loads(rec.read_text(encoding="utf-8") or "{}")
    except Exception as e:                                      # noqa: BLE001 — 读坏要说，不能当没批
        return {"ok": False, "code": "APPROVAL_UNREADABLE", "name": safe,
                "error": "审批记录读不出来（%s: %s）" % (type(e).__name__, e),
                "next": "重新 approve 一次（会覆盖坏记录）"}
    core = mind_path(agent_key) / CORE_MODEL
    if not core.is_file():
        return {"ok": False, "code": "NO_CORE_MODEL", "name": safe,
                "error": "这个心智根没有 `%s`" % CORE_MODEL,
                "next": "先 POST /api/mind/%s 建骨架" % agent_key}
    before_bytes = core.read_bytes()
    before_hash = hashlib.sha256(before_bytes).hexdigest()
    want = str(approval.get("core_before_sha256") or "")
    if want and want != before_hash:
        return {"ok": False, "code": "CORE_CHANGED_SINCE_PROPOSAL", "name": safe,
                "error": "批准之后基模又被改过（提案时 %s…，现在 %s…）" % (want[:12], before_hash[:12]),
                "next": "重新跑审忆员生成新的待审项，别把旧 diff 硬套上去"}
    diff_text = f.read_text(encoding="utf-8", errors="replace")
    # ★2026-10-29：审忆员写出来的 `_pending/*.diff` 是**建议稿**（`--- 待审：…` / 说明 / 改前 hash），
    #   不是可套用的补丁。没有 hunk（`@@ …`）就明确说「这是建议稿」，别当成"内容没变"。
    if not any(ln.startswith("@@") for ln in diff_text.splitlines()):
        return {"ok": False, "code": "NOT_A_UNIFIED_DIFF", "name": safe,
                "error": "这条是**建议稿**（说明 + 改前 hash），不是可套用的补丁",
                "next": "让人按它手改 `00_核心基模.md`，或让上游生成真正的 unified diff 再放进 _pending/"}
    new_text, err = _apply_unified_diff(before_bytes.decode("utf-8", "replace"), diff_text)
    if err:
        return {"ok": False, "code": "DIFF_DOES_NOT_APPLY", "name": safe,
                "error": "diff 套不上：%s" % err,
                "next": "让人按 diff 手工改，或重新生成待审项（平台不猜）"}
    if new_text == before_bytes.decode("utf-8", "replace"):
        return {"ok": False, "code": "NO_CHANGE", "name": safe,
                "error": "套完之后内容没变（diff 可能是空改动）",
                "next": "检查待审项内容"}
    # ① 先归档原文（绝不真删，可回溯）
    stamp = time.strftime("%Y%m%d-%H%M%S")
    arc_dir = mind_path(agent_key) / "_archive" / time.strftime("%Y-%m-%d")
    arc_dir.mkdir(parents=True, exist_ok=True)
    arc = arc_dir / ("%s.%s.bak" % (CORE_MODEL, stamp))
    arc.write_bytes(before_bytes)
    # ② 再写新内容
    core.write_text(new_text, encoding="utf-8")
    after_hash = hashlib.sha256(core.read_bytes()).hexdigest()
    applied = {"applied_by": who, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "approved_by": str(approval.get("approved_by") or ""),
               "approved_at": str(approval.get("at") or ""),
               "diff": f.as_posix(), "archive": arc.as_posix(),
               "before_sha256": before_hash, "after_sha256": after_hash,
               "scope": "已按人批准的 diff 真正写入核心基模；原文已归档，可回溯"}
    f.with_suffix(".applied.json").write_text(
        json.dumps(applied, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "name": safe, "applied_by": who, "applied_at": applied["at"],
            "before_sha256": before_hash, "after_sha256": after_hash,
            "archive": arc.as_posix(),
            "note": "已写入核心基模（原文已归档，可回溯）；这是**人拍板之后**的落地，平台没有自己改认知"}


def approve_pending(agent_key: str, name: str, *, approver: str) -> dict:
    """**记审批**（不改基模）：写 `<id>.approved.json`，带批准人/时间/diff 指纹/改前 hash。

    这是「待审队列批准」的**唯一实现**（HTTP 端点与 CLI `--approve` 都走它 —— 一处真相）。

    四条边界（写死，不许放宽）：
    1. **只记审批**：`00_核心基模.md` **一个字节都不动**（真正写入仍要人按 diff 手工应用）；
    2. **不许覆盖已有审批**：已批过就返回 `already=True` 与**原批准人/时间**，不悄悄改成新的；
    3. **批准人必填**：没有 `approver` 就拒（谁批的必须留痕）；
    4. 名字必须是单层 `.diff`（防穿越），不存在 ⇒ `NO_SUCH_PENDING`。
    """
    safe = _safe_pending_name(name)
    who = str(approver or "").strip()
    if not who:
        raise ValueError("批准人必填（谁批的必须留痕）：传 `approver`，例如 `boss` / `user`")
    f = _pending_dir(agent_key) / safe
    if not f.is_file():
        return {"ok": False, "code": "NO_SUCH_PENDING", "name": safe,
                "pending_dir": str(_pending_dir(agent_key)),
                "next": "先 GET /api/mind/{agent_key}/pending 看有哪些"}
    rec = f.with_suffix(".approved.json")
    if rec.is_file():                                           # 已批过：不覆盖，如实回原记录
        try:
            info = json.loads(rec.read_text(encoding="utf-8") or "{}")
        except Exception as e:                                  # noqa: BLE001 — 记录读坏要说
            print("[mind] 审批记录读不出来 %s（%s: %s）" % (rec, type(e).__name__, e), flush=True)
            info = {}
        return {"ok": True, "already": True, "name": safe, "id": f.stem,
                "approved_by": str(info.get("approved_by") or ""),
                "approved_at": str(info.get("at") or ""),
                "approved_record": rec.as_posix(),
                "note": "这条已经批过了；**没有覆盖**原审批记录（要改就先把 .approved.json 删掉重批）"}
    diff_text = f.read_text(encoding="utf-8", errors="replace")
    core = mind_path(agent_key) / CORE_MODEL
    before = core.read_bytes() if core.is_file() else b""
    info = {"approved_by": who, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "diff": f.as_posix(),
            "diff_sha256": hashlib.sha256(diff_text.encode("utf-8")).hexdigest(),
            "target": _diff_target(diff_text),
            "core_before_sha256": hashlib.sha256(before).hexdigest(),
            "scope": "只记审批；核心基模未改（真正写入要人按 diff 手工应用）"}
    rec.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "already": False, "name": safe, "id": f.stem,
            "approved_by": who, "approved_at": info["at"],
            "approved_record": rec.as_posix(),
            "note": "已记审批；**核心基模仍未改**（平台不代改认知，真正写入要人按 diff 手工应用）"}
