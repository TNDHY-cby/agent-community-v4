# -*- coding: utf-8 -*-
"""生成 `design-docs/索引.md`：**102 个设计稿现在没有入口，这就是入口**。

## 为什么有它

老板 2026-10-18 问「历史设计稿在哪里看」——`design-docs/` 里躺着 100+ 份 `.md`，
`ls` 出来只有文件名，**看不出哪份是现行口径、哪份已落地、哪份等拍板**。这份索引自动生成：

- **一行一份**：编号 / 标题 / **状态** / 文件；
- **状态**从文档自己的状态行解析（"已拍板并落地" / "待老板拍板" / "规划" …），解析不到就**如实写"无状态行"**；
- 分成四张表：**① 现行口径三件（先读这个）**、**② V 系列**、**③ FE 系列**、**④ 其它（RUNBOOK / 契约 / 清单）**；
- 末尾给**统计**（已落地 / 待拍板 / 规划 / 无状态）——**"待拍板"多少份一眼看到**。

## 跑法

```bash
python tools/gen_design_index.py            # 重新生成 design-docs/索引.md
python tools/gen_design_index.py --check    # 只校验索引有没有漂移（守卫用；漂移则 exit 1）
```

守卫：`agent_community/tests/test_design_index.py`（漂移即红，且**每份非备份 .md 都必须在索引里**）。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "design-docs"
INDEX = DOCS / "索引.md"

#: 先读这三份（顺序就是读的顺序）
ENTRY = [
    ("项目完整模型.md", "**现行真相的总图**：角色表 / 九步 / 端点 / §11 欠账表（要看「现在到底有什么」，读它）"),
    ("平台设计总纲.md", "顶层蓝图：平台定位、协作层模型、分层与边界"),
    ("前端工作手册.md", "给前端实现方的现行状态与写域（MiMo 的第一入口）"),
]

#: 状态归一（按优先级匹配；同一份文档取**第一个**命中）
#: ⚠️ 2026-10-18 补：把「实施中 / 已实测通过 / 暂缓 / 已完成 / 设计阶段」也认了 ——
#:   这些是文档自己的原话，**不该被归一掉**（第一版只认"已实施"，于是 20 份被误判成"无状态行"）。
STATUS_RULES = [
    (r"已拍板.*(落地|实施|实现)", "已拍板并落地"),
    (r"已实测通过", "已实测通过"),
    (r"实施中", "实施中"),
    (r"暂缓|⛔", "暂缓"),
    (r"拍板「|已拍板", "已拍板"),
    (r"已实施|已落地|已实现", "已实施"),
    (r"明确授权|明示界面方向|落档", "已授权（落档）"),
    (r"待.{0,24}拍板", "待拍板"),
    (r"存档|归档", "存档"),
    (r"现行", "现行"),
    (r"规划", "规划"),
    (r"设计稿", "设计稿"),
    (r"设计阶段", "设计阶段"),
    (r"已完成", "已完成"),
    (r"定稿", "定稿"),
    (r"契约已定", "契约已定"),
]

V_RE = re.compile(r"^V(\d+)")
FE_RE = re.compile(r"^FE-(\d+)")
_VERIFY_DIR = ROOT / "agent_community" / "frontend" / "verify"


#: 少数 FE 分册没有同名验收脚本，但**代码里确实有对应实现** —— 依据写死在这里（可复核），不猜
FE_EVIDENCE = {
    "FE-05_推进提示设计.md": "代码在（`action_source`／`action_notice_check.js`）",
    "FE-06_共享资料库可见化设计.md": "代码在（`booths.html` 资料库面板）",
    "FE-11_对接操作台设计.md": "代码在（`docking.js` 操作台）",
}

#: V 系列里"文档自己还写着待拍板、但代码里已经落地"的那几份 —— 依据同样写死在案（2026-10-18 实核）
#: ⚠️ 这里**只写证据、不改文档状态**：两列并排，读者自己看到"文档说没做、代码里有"这个缺口本身。
V_EVIDENCE = {
    "V25_唤醒与寻址设计.md": "代码在（`/api/wakeup` 一族 + `visual_launch`）",
    "V26_通用脚本框架设计.md": "代码在（`/api/docking/run-script` + `tools/inject_window.ps1`）",
    "V29_项目建模设计.md": "代码在（`library/plan.md`、`framework.md` 由平台生成）",
    "V37_接入功能产品化设计.md": "已由 V54/对接中心承接（`/api/harness/register-prompt`、预检与首跑判据）",
    "V38_陌生人自服务架桥设计.md": "已由 V54/对接中心承接（六种协议话术 + 桥模板端点）",
    "取长补短成果回收设计.md": "代码在（`identity.py`、`mcp_server.py`、`a2a_server.py` 均在包里）",
    "多协议接入与前端落地设计.md": "代码在（`mcp_server.py` / `a2a_server.py` / `grpc_gateway.py`）",
    "FE-01_审计时间线与在线看板设计.md": "代码在（`ops.html` + `/api/audit`）",
    "FE-02_交付回执台设计.md": "代码在（`handover.html`）",
}


def _evidence(rec: dict) -> str:
    """**我核到的证据**（与"文档自己的状态"分两列，互不替代）。

    FE 系列：同名验收脚本 `verify/feNN_check.js` 在 ⇒ 「验收脚本 ✓」；
            没有脚本的少数几支，用 `FE_EVIDENCE` 里写明的代码依据；
    V 系列：`V_EVIDENCE` 里写明"文档说没做但代码里有"的那几份；
    其它：不瞎猜，返回「—」。
    """
    if rec["file"] in FE_EVIDENCE:
        return FE_EVIDENCE[rec["file"]]
    if rec["file"] in V_EVIDENCE:
        return V_EVIDENCE[rec["file"]]
    m = FE_RE.match(rec["file"])
    if m:
        script = _VERIFY_DIR / ("fe%02d_check.js" % int(m.group(1)))
        return "验收脚本 ✓" if script.exists() else "—"
    return "—"


def _status_of(text: str) -> str:
    """从**文档自己的状态行**解析状态；没有状态行就如实写「无状态行」。

    ⚠️ 两个坑（都踩过，都进了用例）：
      ① 第一版扫"前 1500 字里出现 `待拍板`" ⇒ 把正文写了"拍板后再写码"的老稿也标成待拍板；
      ② 只认"已实施"这一种写法 ⇒ 20 份写着「实施中 / 已实测通过 / 暂缓」的被误判成"无状态行"。
    现在：**只看带「状态」二字的那一行**（跳过 `#` 标题行 —— `# …状态依赖注入` 里的"状态"不是状态行），
    并认全常见写法；认不出来就承认没有（**不替它推断**）。
    """
    head = text.splitlines()[:25]
    for idx, ln in enumerate(head):
        if ln.lstrip().startswith("#") or "状态" not in ln:
            continue
        seg = ln
        if idx + 1 < len(head):          # 状态值可能写在下一行
            seg += " " + head[idx + 1]
        for pat, label in STATUS_RULES:
            if re.search(pat, seg):
                return label
        return "其它（见原文）"          # 有状态字样但措辞未归一 ⇒ 如实说"看原文"
    return "无状态行"


def _title_of(path: Path, text: str) -> str:
    for ln in text.splitlines():
        if ln.startswith("# "):
            return ln[2:].strip()
    return path.stem


def collect() -> dict:
    items = {"entry": [], "V": [], "FE": [], "other": []}
    for p in sorted(DOCS.glob("*.md")):
        if ".bak" in p.name or p.name == INDEX.name:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        rec = {"file": p.name, "title": _title_of(p, text),
               "status": _status_of(text), "size": p.stat().st_size}
        if p.name in [e[0] for e in ENTRY]:
            rec["why"] = next(w for f, w in ENTRY if f == p.name)
            items["entry"].append(rec)
        elif V_RE.match(p.name):
            rec["no"] = int(V_RE.match(p.name).group(1))
            items["V"].append(rec)
        elif FE_RE.match(p.name):
            rec["no"] = int(FE_RE.match(p.name).group(1))
            items["FE"].append(rec)
        else:
            items["other"].append(rec)
    items["V"].sort(key=lambda r: r["no"])
    items["FE"].sort(key=lambda r: r["no"])
    order = {f: i for i, (f, _) in enumerate(ENTRY)}
    items["entry"].sort(key=lambda r: order.get(r["file"], 99))
    items["other"].sort(key=lambda r: r["file"])
    return items


def render(items: dict) -> str:
    all_recs = items["entry"] + items["V"] + items["FE"] + items["other"]
    stat = {}
    for r in all_recs:
        stat[r["status"]] = stat.get(r["status"], 0) + 1

    L = []
    L.append("# 设计稿索引（自动生成，别手改）")
    L.append("")
    L.append("> 由 `python tools/gen_design_index.py` 生成；`pytest agent_community/tests/test_design_index.py`")
    L.append("> 会在**索引与文件不一致**时判红。看完要改，就改生成器或改文档的状态行，别直接编辑本文件。")
    L.append("")
    L.append("**共 %d 份**（不含 `.bak` 备份）：%s" % (
        len(all_recs), " ｜ ".join("%s %d" % (k, v) for k, v in sorted(stat.items(), key=lambda kv: -kv[1]))))
    L.append("")
    L.append("## ① 先读这三份（现行口径）")
    L.append("")
    L.append("| 文件 | 是什么 |")
    L.append("|---|---|")
    for r in items["entry"]:
        L.append("| [`%s`](%s) | %s |" % (r["file"], r["file"], r["why"]))
    L.append("")
    L.append("## ② V 系列（按编号；编号就是当时的推进顺序）")
    L.append("")
    L.append("| # | 标题 | 文档状态 | 我核 | 文件 |")
    L.append("|---|---|---|---|---|")
    for r in items["V"]:
        L.append("| V%d | %s | %s | %s | [`%s`](%s) |"
                 % (r["no"], r["title"][:60], r["status"], _evidence(r), r["file"], r["file"]))
    L.append("")
    L.append("## ③ FE 系列（前端界面分册）")
    L.append("")
    L.append("| # | 标题 | 文档状态 | 我核 | 文件 |")
    L.append("|---|---|---|---|---|")
    for r in items["FE"]:
        L.append("| FE-%d | %s | %s | %s | [`%s`](%s) |"
                 % (r["no"], r["title"][:60], r["status"], _evidence(r), r["file"], r["file"]))
    L.append("")
    L.append("## ④ 其它（RUNBOOK / 契约 / 清单 / 说明）")
    L.append("")
    L.append("| 文件 | 标题 | 状态 |")
    L.append("|---|---|---|")
    for r in items["other"]:
        L.append("| [`%s`](%s) | %s | %s |" % (r["file"], r["file"], r["title"][:60], r["status"]))
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 怎么用这份索引")
    L.append("")
    L.append("1. **想知道「现在到底有什么」** ⇒ 读 ① 的 [`项目完整模型.md`](项目完整模型.md)（含 §11 欠账表）；")
    L.append("2. **想找某个功能的来龙去脉** ⇒ 在 ② 里按编号找（V55＝信息搜索者/防信息污染、V56＝巡逻员…）；")
    L.append("3. **想知道还有多少没拍板** ⇒ 看「文档状态」列，`待拍板` 就是排队等老板的；")
    L.append("4. **两列别混**：**文档状态**是**文档自己写的**（我不替它改口径）；**我核**是我这边查到的证据")
    L.append("   （验收脚本 / 代码在 / 已由哪份承接）——**两列打架就是缺口**，例如「文档说待拍板、我核写着代码在」")
    L.append("   ⇒ 说明那份文档的状态行过时了，该补；")
    L.append("5. **状态行怎么来的** ⇒ 从文档头部带「状态」二字的那一行解析（跳过 `#` 标题行）；写着「无状态行」的，")
    L.append("   是我还没给它标状态（该补）。")
    L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验是否漂移（不写文件）")
    a = ap.parse_args()
    text = render(collect())
    if a.check:
        cur = INDEX.read_text(encoding="utf-8") if INDEX.exists() else ""
        if cur.strip() != text.strip():
            print("★索引已漂移：跑 `python tools/gen_design_index.py` 重新生成")
            return 1
        print("OK：索引与文件一致")
        return 0
    INDEX.write_text(text, encoding="utf-8", newline="")
    n = sum(len(v) for v in collect().values())
    print("已生成 %s（%d 份）" % (INDEX.name, n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
