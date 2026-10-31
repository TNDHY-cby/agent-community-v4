#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""**夜间审忆员**（设计 `V117` §4 / 架构师排雷②，P4 批）。

作用：把白天的工作日志压成"3 行结论"，重写记忆索引；**绝不碰工作区（根B）**。

```
python tools/mind_reviewer.py --mind-root <AC_MIND_ROOT> --dry-run     # 只报会做什么（默认）
python tools/mind_reviewer.py --mind-root <AC_MIND_ROOT> --apply       # 真写（必须显式给）
python tools/mind_reviewer.py --mind-root <AC_MIND_ROOT> --date 2026-10-27 --apply
```

## 四条红线（一条都不许破）

1. **默认关**：不给 `--apply` 就一个字节都不写（老板既定"自动兜底默认关"）；
2. **只读根A**：路径白名单 —— 命根B（工作区路径）的输入直接拒；
3. **不可真删**：改动前把原文整段移到 `_archive/<日期>/`，随时能回溯；
4. **重大修改进待审**：动 `00_核心基模.md` 一律写 `_pending/*.diff`，**不自动生效**，
   等用户 `--approve <id>` 才落地（记 `approver`/时间/前后 hash）。

## 它不做的事（如实说）

- **不判断哪条结论"重要"给人看**：它只做机械压缩（取每份日志的要点行）+ 索引重写；
- **不生成新的认知**：压缩文本来自日志原文（抽取行），不调用模型、不编造；
- **不碰任务、产物、成员**：那些在根B，和它无关。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CORE = "00_核心基模.md"
INDEX = "01_记忆索引表.md"
LOG_DIR = "02_任务日志"
DEAD_DIR = "06_死胡同"
ARCHIVE = "_archive"
PENDING = "_pending"
META = "_meta"

MAX_LINES = 3          # 每份日志压成几行
MAX_INDEX_ROWS = 120   # 索引最多多少行（超了按最新保留）


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _safe(p: Path) -> bool:
    """只允许动 `minds/` 下的东西：路径里不许出现工作区常见目录（防误传根B）。"""
    s = p.as_posix()
    bad = ("/ws_", "/workshop", "/workspaces/", "/_inbox/", "/产物/", "/任务/")
    return not any(b in s for b in bad)


def _points(text: str, limit: int = MAX_LINES) -> list:
    """机械抽取"要点行"：以小标题/短句为候选，取最长的几行（**不改写、不总结**）。"""
    lines = [ln.strip() for ln in text.splitlines()]
    bullets = [ln for ln in lines if ln.startswith(("- ", "* ", "## ")) and len(ln) > 3]
    cands = bullets or [ln for ln in lines if len(ln) > 8 and not ln.startswith("#")]
    cands = sorted(cands, key=len, reverse=True)[:limit]
    # 保持原文顺序
    order = {ln: i for i, ln in enumerate(lines)}
    return sorted(cands, key=lambda x: order.get(x, 0))


def _core_patch(core_path: Path, date: str, reason: str) -> dict:
    """把「最近修正」那一行改成一条**事实记录**，返回真 unified diff。

    为什么只改这一行：这是**机械改写**（日期 + 做了什么），不是让审忆员"发表观点" ——
    `V116` §21.8 那条"审忆压缩不是智能摘要、不生成新结论"仍然成立。
    人批准之后才落地（红线不变）。
    """
    import difflib

    old = core_path.read_text(encoding="utf-8", errors="replace")
    lines = old.splitlines(keepends=True)
    hit = None
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("- 最近修正"):
            hit = i
            break
    if hit is None:
        # 没有这一行 ⇒ **追加**一行（机械、可回滚；人批前后都能看 diff）
        if not lines or not lines[-1].endswith("\n"):
            lines = lines + ["\n"]
        new_line = "- 最近修正：%s 夜间审忆（%s）\n" % (date, reason)
        new_lines = list(lines) + [new_line]
        diff = "".join(difflib.unified_diff(
            lines, new_lines, fromfile="a/%s" % CORE, tofile="b/%s" % CORE, n=3))
        if not diff:
            return {"ok": False, "why": "算出来的补丁为空"}
        return {"ok": True, "diff": diff, "new_text": "".join(new_lines),
                "line_no": len(new_lines), "new_line": new_line.strip(),
                "appended": True,
                "why": "这份基模没有「最近修正」行 ⇒ 追加一行（未改动原文任何字节）"}
    new_line = "- 最近修正：%s 夜间审忆（%s）\n" % (date, reason)
    if lines[hit].strip() == new_line.strip():
        return {"ok": False, "why": "「最近修正」已经是今天这条，不用再改"}
    new_lines = list(lines)
    new_lines[hit] = new_line
    diff = "".join(difflib.unified_diff(
        lines, new_lines, fromfile="a/%s" % CORE, tofile="b/%s" % CORE, n=3))
    if not diff:
        return {"ok": False, "why": "算出来的补丁为空"}
    return {"ok": True, "diff": diff, "new_text": "".join(new_lines),
            "line_no": hit + 1, "new_line": new_line.strip()}


def plan(mind_root: Path, date: str) -> dict:
    """**只算不写**：列出会动哪些文件、动成什么样。"""
    acts = {"date": date, "minds": [], "refused": []}
    if not mind_root.is_dir():
        acts["error"] = "心智根目录不存在：%s" % mind_root
        return acts
    for mind in sorted(p for p in mind_root.iterdir() if p.is_dir()):
        if not _safe(mind):
            acts["refused"].append({"mind": mind.as_posix(), "why": "路径像工作区，拒绝处理"})
            continue
        log = mind / LOG_DIR / ("%s.md" % date)
        dead = mind / DEAD_DIR / ("%s.md" % date)
        if not log.is_file() and not dead.is_file():
            continue
        item = {"mind": mind.name, "mind_path": mind.as_posix(), "logs": [], "compress": None,
                "core_change": None}
        texts = []
        for f, kind in ((log, "任务日志"), (dead, "死胡同")):
            if f.is_file():
                raw = f.read_text(encoding="utf-8", errors="replace")
                pts = _points(raw)
                item["logs"].append({"file": f.as_posix(), "kind": kind, "chars": len(raw),
                                     "points": pts})
                texts.append((kind, raw, pts))
        if texts:
            body = ["# 日结压缩 %s" % date, ""]
            for kind, raw, pts in texts:
                body.append("## %s（原文 %d 字，已归档）" % (kind, len(raw)))
                body += ["- %s" % x.lstrip("-*# ") for x in pts] or ["- （没有可抽取的要点）"]
                body.append("")
            item["compress"] = "\n".join(body).strip() + "\n"
        # 索引重写：记忆文件名 + 关键词（取文件名里的标签）
        mems = sorted((mind / "memories").glob("*.md"))
        if mems:
            rows = []
            for m in mems[-MAX_INDEX_ROWS:]:
                tag = m.stem.split("-", 2)[-1].replace("_", " ")
                rows.append("| %s | memories/%s | %s |" % (tag or m.stem, m.name, m.stem))
            item["index_rows"] = rows
        if (mind / CORE).is_file():
            item["core_change"] = {"file": (mind / CORE).as_posix(), "reason": "本轮日结压缩已完成",
                                   "before_hash": _sha((mind / CORE).read_text(encoding="utf-8"))}
        acts["minds"].append(item)
    return acts


def apply(acts: dict, *, mind_root: Path, date: str, dry: bool) -> dict:
    """真写（`--apply` 才进来）。**同日幂等**：已存在压缩文件就跳过。"""
    done = []
    for item in acts.get("minds", []):
        mind = Path(item["mind_path"])
        if not _safe(mind):
            item["skipped"] = "路径像工作区"
            continue
        arch = mind / ARCHIVE / date
        stamp = time.strftime("%H%M%S")
        # ① 先归档原文（**不可真删**）
        for lg in item["logs"]:
            src = Path(lg["file"])
            if not src.is_file():
                continue
            arch.mkdir(parents=True, exist_ok=True)
            # ★分区名要带进文件名：任务日志与死胡同可能同日同名（否则后一个覆盖前一个 = 悄悄丢原文）
            zone = "任务日志" if lg.get("kind") == "任务日志" else "死胡同"
            dst = arch / ("%s-%s.%s.bak" % (zone, src.stem, stamp))
            if not dry:
                dst.write_text(src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
            lg["archived_to"] = dst.as_posix()
        # ② 写压缩（同日幂等）
        if item.get("compress"):
            target = mind / "02_压缩" / ("%s.md" % date)
            if target.is_file():
                item["compress_skipped"] = "已存在（同日幂等）：%s" % target.as_posix()
            else:
                if not dry:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(item["compress"], encoding="utf-8")
                item["compress_written"] = target.as_posix()
        # ③ 索引重写（保留旧版本）
        if item.get("index_rows"):
            idx = mind / INDEX
            old = idx.read_text(encoding="utf-8", errors="replace") if idx.is_file() else ""
            head = ("# 01_记忆索引表 · %s\n\n> 触发词 → 记忆文件。**只读匹配的 1~3 个文件**"
                    "（禁止全盘扫描）。由夜间审忆员重写，旧版留 `%s.bak`。\n\n"
                    "| 触发词 | 记忆文件 | 一句话 |\n|---|---|---|\n" % (mind.name, INDEX))
            body = head + "\n".join(item["index_rows"]) + "\n"
            if not dry:
                if old:
                    (mind / (INDEX + ".%s.bak" % stamp)).write_text(old, encoding="utf-8")
                idx.write_text(body, encoding="utf-8")
            item["index_written"] = idx.as_posix()
        # ④ 核心基模：**只进待审**，绝不自动改
        if item.get("core_change"):
            pend = mind / PENDING
            pid = "%s-core-%s" % (date, stamp)
            core_path = Path(item["core_change"]["file"])
            patch = _core_patch(core_path, date, str(item["core_change"]["reason"]))
            if not patch.get("ok"):
                item["pending_skipped"] = patch.get("why")
                item["note"] = "核心基模**没有出待审**：%s" % patch.get("why")
            else:
                diff = patch["diff"]
                p = pend / (pid + ".diff")
                if not dry:
                    pend.mkdir(parents=True, exist_ok=True)
                    p.write_text(diff, encoding="utf-8")
                    (pend / (pid + ".note.md")).write_text(
                        "# 审忆员说明（人批前必看）\n\n%s\n\n"
                        "- 改的是：`%s` 第 %d 行（「最近修正」）\n"
                        "- 改成：`%s`\n"
                        "- 改前 hash：`%s`\n"
                        "- 这是**机械改写**（日期 + 做了什么），不是新结论；批准后走 `apply` 才落地。\n"
                        % (item["core_change"]["reason"], CORE, patch["line_no"],
                           patch["new_line"], item["core_change"]["before_hash"]),
                        encoding="utf-8")
                item["pending"] = p.as_posix()
                item["note"] = ("核心基模**没有改**；真补丁在待审队列（%s）："
                                "`--approve %s` 记审批 → `--apply-pending %s --agent <key>` 才真正写进基模"
                                "（先归档原文，可回滚）" % (pid, pid, pid))
        done.append(item)
        # ⑤ 记账
        if not dry:
            meta = mind / META
            meta.mkdir(parents=True, exist_ok=True)
            with (meta / "reviewer-log.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"at": _now(), "date": date, "mind": mind.name,
                                    "logs": len(item.get("logs") or []),
                                    "compress": item.get("compress_written", ""),
                                    "pending": item.get("pending", "")},
                                   ensure_ascii=False) + "\n")
    return {"date": date, "minds": done, "dry_run": dry}


def approve(mind_root: Path, pending_id: str, *, approver: str) -> dict:
    """用户拍板：把 `_pending/<id>.diff` 标记为已批（**这里只记审批，不改基模**）。"""
    hits = list(mind_root.glob("*/%s/%s.diff" % (PENDING, pending_id)))
    if not hits:
        return {"ok": False, "error": "找不到待审项：%s" % pending_id,
                "next": "先跑 --dry-run 看有哪些 _pending/*.diff"}
    p = hits[0]
    rec = p.with_suffix(".approved.json")
    rec.write_text(json.dumps({"approved_by": approver, "at": _now(),
                               "diff": p.as_posix()}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    return {"ok": True, "approved": rec.as_posix(),
            "note": "已记审批；**基模仍未改** —— 真正写入要由人或法 AI 按 diff 手工应用"}


def main() -> int:
    ap = argparse.ArgumentParser(description="夜间审忆员（默认 dry-run；--apply 才写）")
    ap.add_argument("--mind-root", default=os.environ.get("AC_MIND_ROOT") or "")
    ap.add_argument("--date", default="")
    ap.add_argument("--apply", action="store_true", help="真写（不给就只报告）")
    ap.add_argument("--dry-run", action="store_true", help="显式 dry-run（与默认一致）")
    ap.add_argument("--approve", default="", help="把某个 _pending 项标记为已批（记审批，不改基模）")
    ap.add_argument("--apply-pending", default="",
                    help="把**已批**的 _pending 项真正写进核心基模（先归档原文；走平台同一份实现）")
    ap.add_argument("--approver", default="user")
    ap.add_argument("--agent", default="", help="心智根的 agent_key（--apply-pending 必填）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    root = a.mind_root.strip()
    if not root:
        print("[审忆员] 没给 --mind-root，也没有 AC_MIND_ROOT ⇒ 不知道去哪找心智根。\n"
              "        用法：--mind-root <你的 AC_MIND_ROOT>（默认是 <AC_DATA_DIR>/minds）",
              file=sys.stderr)
        return 2
    mind_root = Path(root)
    if a.apply_pending:
        # 2026-10-29：落地走平台同一份实现（source of truth = platform/mind.apply_pending）
        from agent_community.platform import mind as _mind
        agent_key = a.agent if getattr(a, "agent", "") else ""
        if not agent_key:
            print("[审忆员] 要指定 --agent <agent_key>（落地是给某个心智根写的）")
            return 2
        out = _mind.apply_pending(agent_key, a.apply_pending, approver=a.approver)
        print(json.dumps(out, ensure_ascii=False, indent=1) if a.json else
              ("✓ 已落地：%s → %s（原文归档 %s）"
               % (out.get("before_sha256", "")[:12], out.get("after_sha256", "")[:12],
                  out.get("archive", "")) if out.get("ok") else
               "✗ 没落地：%s · %s" % (out.get("code"), out.get("error"))))
        return 0 if out.get("ok") else 1
    if a.approve:
        out = approve(mind_root, a.approve, approver=a.approver)
        print(json.dumps(out, ensure_ascii=False, indent=1) if a.json else out)
        return 0 if out.get("ok") else 1

    date = a.date or time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
    acts = plan(mind_root, date)
    if acts.get("error"):
        print("[审忆员] %s" % acts["error"], file=sys.stderr)
        return 2
    dry = not a.apply
    out = apply(acts, mind_root=mind_root, date=date, dry=dry)
    if a.json:
        # ★`--json` 要给结构化结果（判据/自动化都靠它）；提示照旧走 stderr，别污染 stdout
        print(json.dumps({"date": date, "dry_run": dry,
                          "minds": out.get("minds", []),
                          "refused": acts.get("refused", [])}, ensure_ascii=False, indent=1))
        return 0
    print("=" * 74)
    print("夜间审忆员 · %s · %s" % (date, "DRY-RUN（未写任何文件）" if dry else "APPLY（已写）"))
    print("=" * 74)
    if not out["minds"]:
        print("  这个日期没有需要处理的心智根（没有当天日志）")
    for m in out["minds"]:
        print("  · %s（%d 份日志）" % (m["mind"], len(m.get("logs") or [])))
        if m.get("compress_written"):
            print("      压缩 → %s" % m["compress_written"])
        elif m.get("compress_skipped"):
            print("      压缩：%s" % m["compress_skipped"])
        if m.get("index_written"):
            print("      索引 → %s" % m["index_written"])
        if m.get("pending"):
            print("      核心基模改动 → %s（**待审，未生效**）" % m["pending"])
        if m.get("skipped"):
            print("      跳过：%s" % m["skipped"])
    if acts.get("refused"):
        print("  拒绝处理（路径像工作区）：%s" % acts["refused"])
    print("-" * 74)
    print("口径：只读根A；原文先进 `_archive/`（不真删）；核心基模改动只进 `_pending/` 等拍板。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
