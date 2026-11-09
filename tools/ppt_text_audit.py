# -*- coding: utf-8 -*-
"""参赛 PPT 文本审查器（可复用，不放进 temp/）。

用法：
    python tools/ppt_text_audit.py --ppt "output/xxx.pptx"
    python tools/ppt_text_audit.py --ppt "output/xxx.pptx" --fix   # 只报告不建议自动改

审查规则来自老板 2026-11-09/10 的历次批评，共 6 类：
  A 内部黑话（sha256 / reports_to / L1-L5 / 判据 / 桥型 / 证据档 …）
  B 无意义概念（签字 / 一句话 slogan / 如果只记一条）
  C 虚报与不可核实指标（具体测试数、接口数、已跑通比例）
  D 能力越界（网页类 AI 被当成可接入）
  E 术语不统一（分队 vs 小队、双闸 vs 两道关卡）
  F 自造词没给白话解释（账本 / 凭据 / 指纹 / 冠层 / 根系 四档…）

退出码：0 全清；1 有 BLOCKER；2 只有 WARN。
"""
import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── A 内部黑话：一律不许出现在对外材料里 ──
JARGON = ["sha256", "reports_to", "retryable", "reusable", "L0", "L1", "L2", "L3",
          "L4", "L5", "判据", "桥型", "证据档", "在途", "manifest", "fs_write",
          "session_post", "pending_poll", "file_poll", "cli_acp", "script_framework"]

# ── B 无意义概念 / 口号 ──
SLOGAN = ["签字", "不替你干活", "替你验收", "一句话：", "如果只记一条", "赛程对齐",
          "为什么这套东西是真的", "这页不重要", "重不重要"]

# ── C 虚报与不可核实指标 ──
VANITY = ["2,671", "2671", "433 个", "433个", "439 个", "123 项", "123项",
          "2,671 个测试", "测试 2,671"]

# ── D 能力越界（网页类 AI 不许写成可接入）──
OVERREACH = ["浏览器里的助手", "浏览器插件", "IDE 助手", "网页 AI 也能", "网页版也能接"]

# ── E 术语不统一：左侧是错的写法 ──
INCONSISTENT = {"分队": "小队", "分队长": "带队的", "双闸": "两道关卡",
                "两道闸": "两道关卡", "验收闸": "验收关卡", "验收标准层": "验收层"}

# ── F 自造词：必须在本行或邻近给出白话解释（括号）──
COINED = {"账本": "总账（任务台账）", "凭据": "凭据（有文件 / 有截图 / 只有口头）",
          "指纹": "指纹（哈希值）", "冠层": "冠层（看得见的组织）",
          "根系": "看不见的知识", "主根": "宪章", "侧根": "审忆",
          "须根": "日志", "储藏根": "经验"}


def iter_text(prs):
    """逐段产出 (页号, 形状名, 段落文本)。"""
    for i, s in enumerate(prs.slides, 1):
        for sh in s.shapes:
            if not sh.has_text_frame:
                continue
            for p in sh.text_frame.paragraphs:
                t = "".join(r.text for r in p.runs).strip()
                if t:
                    yield i, sh, t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ppt", required=True)
    a = ap.parse_args()
    if not os.path.exists(a.ppt):
        print("找不到文件:", a.ppt)
        return 3

    from pptx import Presentation
    prs = Presentation(a.ppt)

    blockers, warns, infos = [], [], []
    explained_on_page = {}
    explained_anywhere = {}
    pending = []

    for pg, sh, t in iter_text(prs):
        for k in JARGON:
            if k in t:
                blockers.append(("A 内部黑话", pg, k, t[:52]))
        for k in SLOGAN:
            if k in t:
                blockers.append(("B 无意义概念", pg, k, t[:52]))
        for k in VANITY:
            if k in t:
                blockers.append(("C 虚报指标", pg, k, t[:52]))
        for k in OVERREACH:
            if k in t:
                blockers.append(("D 能力越界", pg, k, t[:52]))
        for bad, good in INCONSISTENT.items():
            if bad in t:
                blockers.append(("E 术语不统一", pg, "%s→%s" % (bad, good), t[:52]))
        for word, hint in COINED.items():
            if word in t:
                explained_on_page.setdefault(pg, set())
                explained_anywhere.setdefault(word, False)
                if "（" in t and "）" in t:
                    explained_on_page[pg].add(word)
                    explained_anywhere[word] = True
                    infos.append(("F 自造词有括号", pg, word, t[:40]))
                else:
                    pending.append((pg, word, hint))

    # 同一页只要有一处给了括号解释，其余引用算通过
    for pg, word, hint in pending:
        if word in explained_on_page.get(pg, set()) or explained_anywhere.get(word):
            infos.append(("F 自造词同页已解释", pg, word, ""))
        else:
            warns.append(("F 自造词缺解释", pg, word, "建议写成 " + hint))

    print("=" * 66)
    print("参赛 PPT 文本审查：", os.path.basename(a.ppt))
    print("页数：", len(prs.slides), "| 段落数：", sum(1 for _ in iter_text(prs)))
    print("=" * 66)

    if blockers:
        print("\n【BLOCKER %d 处 —— 必须改】" % len(blockers))
        for kind, pg, k, t in blockers:
            print("  P%-2d [%s] %-14s | %s" % (pg, kind, k, t))
    else:
        print("\n【BLOCKER】0 处 ✅")

    if warns:
        print("\n【WARN %d 处 —— 人工确认】" % len(warns))
        seen = set()
        for kind, pg, k, hint in warns:
            if (pg, k) in seen:
                continue
            seen.add((pg, k))
            print("  P%-2d %-12s %s" % (pg, k, hint))

    if infos:
        print("\n【已带解释的自造词 %d 处 ✅】" % len(set((p, k) for _, p, k, _ in infos)))

    print("\n结论：", "不通过，先改 BLOCKER" if blockers else
          ("通过（有 WARN 待确认）" if warns else "通过 ✅"))
    return 1 if blockers else (2 if warns else 0)


if __name__ == "__main__":
    sys.exit(main())
