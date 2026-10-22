# -*- coding: utf-8 -*-
"""对外文档的可信度守卫（2026-10-22 加，起因是一次真实事故）。

事故一：`README.md` 里 5 个函数名被**控制字符顶掉了首字母**
（`\\x07pproval` / `\\x07rtifacts` / `ule_gate` / `un_budget` / `un_snapshot`），
另一个测试文件里 `\\x0crom` 本该是 `from` —— 都是某次用带转义的字符串批量写文件造成的，
**肉眼看 diff 时不容易发现**，但懂行的人一眼就看出这是坏掉的代码引用。

事故二：README 宣称「8 种协议」，而 `platform/protocol_catalog.py` 实测
只有 5 种 `available`（HTTP/PIPE/ACP/MCP/A2A）、WS `partial`、SSE `not_implemented`、
gRPC 需可选依赖且是独立进程。**对外夸大数字是最廉价的失信**。

⇒ 本文件把两件事钉成判据：
1. 仓库里的文本文件**不许有 C0 控制字符**（除 `\\t` `\\n` `\\r`）；
2. README 里的**协议数量与设计稿份数**必须与实测一致（不是"有没有写"，是"数对不对"）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"

#: 只扫"人读的文本"，且跳过运行时/临时/生成物目录
TEXT_SUFFIXES = frozenset({
    ".md", ".py", ".txt", ".js", ".html", ".css", ".json", ".yml", ".yaml",
    ".ps1", ".bat", ".cmd", ".sh", ".toml", ".cfg", ".ini",
})
SKIP_DIRS = frozenset({
    ".git", "__pycache__", ".pytest_cache", "node_modules",
    "data", "temp", "runtime_logs", "library", "OutAgentsCommunity",
})


def _text_files():
    for f in ROOT.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in f.relative_to(ROOT).parts):
            continue
        yield f


def test_no_stray_control_characters_in_text_files():
    """文本文件里出现 C0 控制字符 ⇒ 几乎一定是"批量改写把首字母吃掉了"。"""
    bad = []
    for f in _text_files():
        try:
            raw = f.read_bytes()
        except Exception:                                    # noqa: BLE001 — 读不了的文件不是本判据的对象
            continue
        for i, ch in enumerate(raw):
            if ch < 0x20 and ch not in (0x09, 0x0A, 0x0D):
                bad.append("%s @byte %d (0x%02X)" % (f.relative_to(ROOT).as_posix(), i, ch))
                break
    assert not bad, "这些文件里有控制字符（像是首字母被吃掉）：%s" % bad[:6]


def _matrix_block(text: str) -> str:
    """取「## 协议矩阵」到下一个二级标题之间的那段（判据只看这一段里的状态标记）。"""
    m = re.search(r"(?ms)^## 协议矩阵.*?(?=^## )", text)
    assert m, "README 里找不到「## 协议矩阵」这一节"
    return m.group(0)


def test_readme_does_not_overclaim_protocol_count():
    """README 正文里的「N 种协议」不得超过「协议矩阵」里真正 ✅ 的行数。"""
    text = README.read_text(encoding="utf-8", errors="replace")
    ok_rows = _matrix_block(text).count("✅")
    assert ok_rows >= 5, "协议矩阵里 ✅ 的行数异常（%d）——矩阵被改坏了？" % ok_rows

    claims = [int(n) for n in re.findall(r"(\d+)\s*种协议", text)]
    over = [n for n in claims if n > ok_rows]
    assert not over, ("README 宣称 %s 种协议，但协议矩阵里只有 %d 条 ✅ —— "
                      "别把「部分实现/规划中」算进「已通」（对外夸大一次，信任就没了）"
                      % (over, ok_rows))


def test_readme_design_doc_count_matches_reality():
    """README 写的设计稿份数必须与 `design-docs/` 实测一致（含 `model/` 分册，口径同 `gen_design_index`）。"""
    text = README.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"(\d+)\s*份设计稿", text)
    assert m, "README 里找不到「N 份设计稿」"
    actual = len([p for p in (ROOT / "design-docs").rglob("*.md")
                  if p.name != "索引.md" and ".bak" not in p.name])
    assert int(m.group(1)) == actual, \
        "README 写 %s 份设计稿，实测 %d 份（改了文档就一起改 README）" % (m.group(1), actual)


#: README 协议矩阵里那行标签 → 协议目录 id（改名就一起改这里，否则下面会红）
_ROW2ID = {"HTTP": "http", "WS": "ws", "PIPE": "pipe", "ACP": "acp",
           "MCP": "mcp", "A2A": "a2a", "SSE": "sse", "GRPC": "grpc"}


def test_readme_protocol_matrix_matches_catalog():
    """README 的协议矩阵必须与 `protocol_catalog` 的**派生结果**逐行一致。

    为什么要有这条：矩阵原来是**手写**的，代码里的状态是另一份 ⇒ 两处必然漂移，
    而漂移的方向永远是"往好里写"（把部分/规划中写成已通）。2026-10-22 就出过这事。
    """
    from agent_community.platform import protocol_catalog as pc

    text = README.read_text(encoding="utf-8", errors="replace")
    block = _matrix_block(text)
    rows = {}
    for line in block.splitlines():
        m = re.match(r"^\|\s*\*\*([A-Za-z0-9]+)\*\*\s*\|([^|]*)\|([^|]*)\|\s*(.+?)\s*\|\s*$", line)
        if m:
            rows[m.group(1).upper()] = m.group(4)
    assert rows, "README 协议矩阵一行都没解析出来（表格格式被改坏了？）"

    catalog = {p["id"]: p for p in pc.get_protocol_catalog(host="h", http_port=1)}
    bad = []
    for label, cell in rows.items():
        pid = _ROW2ID.get(label)
        if not pid or pid not in catalog:
            bad.append("%s：README 有这行，协议目录里没有对应条目" % label)
            continue
        entry = catalog[pid]
        want_icon = pc.OPENBOX_ICON[pc.openbox_mark(entry)]
        want_text = pc.OPENBOX_TEXT[pc.openbox_mark(entry)]
        if want_icon not in cell or want_text not in cell:
            bad.append("%s：README 写「%s」，代码派生是「%s %s」（status=%s, mode=%s）"
                       % (label, cell, want_icon, want_text,
                          pc._runtime_status(entry), entry.get("mode")))
    missing = [i for i in catalog if i not in [v for v in (rows.keys() and [_ROW2ID.get(k) for k in rows])]]
    assert not bad, "协议矩阵与代码不一致：%s" % bad
    assert not missing, "协议目录里的这些协议 README 矩阵没写：%s" % missing


def test_readme_positioning_chain_has_real_backing():
    """README 定位段宣称的「一条链」必须**每环都有真身**（模块可导入 + 有真端点）。

    宣称是：approval（审批封驳）→ run_budget（按节点算钱）→ workflow_engine（拓扑/幂等/重路由）
            → run_snapshot + artifacts（可回放、可交付）。
    任何一环被删/改名，README 那句话就必须跟着改 —— 否则这里红。
    """
    import importlib

    from agent_community.platform.server import app

    chain = {
        "approval": ("agent_community.platform.routers.approval", "/approval"),
        "run_budget": ("agent_community.platform.run_budget", "/budget"),
        "workflow_engine": ("agent_community.platform.workflow_engine", "/workflow"),
        "run_snapshot": ("agent_community.platform.routers.snapshots", "/snapshot"),
        "artifacts": ("agent_community.platform.routers.artifacts", "/api/artifact"),
    }
    paths = []
    for r in app.routes:
        inner = getattr(r, "original_router", None)
        for route in (inner.routes if (inner is not None and getattr(r, "path", None) is None) else [r]):
            p = getattr(route, "path", None)
            if p:
                paths.append(p)

    bad = []
    for name, (mod, needle) in chain.items():
        try:
            importlib.import_module(mod)
        except Exception as e:                                # noqa: BLE001 — 导入失败就是要报的结论
            bad.append("%s 模块导不进来（%s）：%s" % (name, mod, e))
            continue
        if not any(needle in p for p in paths):
            bad.append("%s 没有对应端点（路径里找不到 %r）" % (name, needle))
    assert not bad, ("README 宣称的「一条链」有环节没落地：%s —— "
                     "要么补上，要么把 README 那句话删掉（不许宣称不存在的能力）" % bad)
