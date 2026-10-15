# -*- coding: utf-8 -*-
"""`FE-26` §11 守卫：**候选关键词（只读）** 与 **脚本副本导出**。

来历：老板 2026-10-15 定「前端外包」，本会话只做后端＋契约＋验收；继承书 §7.2 把这两件列为
「`FE-26` 里与界面无关的收尾」。两件**都是 GET（只读）**⇒ 不进写请求白名单。

判据：
  ① **候选必须有出处**（每条带 `source` ＋ 给人看的 `why`）—— 编不出出处的不给
     （用户点了没来由的词、找不到窗口，只会以为平台在瞎猜）；去重、按优先级、限量；
  ② **只读**：`probe=0` **真的不碰**本机（不扫进程、不列窗口）；`probe=1` 才扫；
  ③ **探不到 ≠ 真没有**：非 Windows / 扫不到窗口 ⇒ `supported=false` ＋ `probe_note` 说清为什么；
  ④ **导出的是副本**：正文逐字＝档案里的脚本，注释头写明"真相在档案"、**三态如实**（不是 True 就写
     "未核实"，**绝不许出现"已完成"**）；
  ⑤ **没存过脚本 ⇒ 404 ＋ 中文 error ＋ 下一步**（不许给"下载了个空文件"的死状态）；
  ⑥ 导出的 `.ps1` 含中文 ⇒ **必须带 UTF-8 BOM**（PS 5.1 无 BOM 按 GBK 读 ⇒ ParserError，本项目已有同款事故）。
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

from agent_community.platform import visual_launch  # noqa: E402
from agent_community.platform.protocol import HarnessAI, HarnessInfo  # noqa: E402
from agent_community.platform.routers import docking  # noqa: E402


def _fake_session(hid="H-1", *, name="示例Harness-A", hints=None, script=None):
    meta = {"probe_hints": dict(hints)} if hints else {}
    info = HarnessInfo(harness_id=hid, harness_name=name, ai=HarnessAI(model_name="m"),
                       wakeup_method="clipboard", metadata=meta,
                       activation_script=dict(script or {}))
    return types.SimpleNamespace(info=info)


def _body(res) -> str:
    raw = getattr(res, "body", b"")
    return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)


# ── ① suggest_keywords（纯函数）──────────────────────────────────────────
def test_every_suggestion_has_a_source_and_a_reason():
    """**编不出出处的不给** —— 这是这个函数存在的全部意义。"""
    got = docking.suggest_keywords(
        {"harness_id": "H-1", "harness_name": "示例Harness-A"},
        probe_hints={"process_hint": "demo-agent.exe", "path_hint": r"D:\apps\demo-agent"},
        session_names=["ws-x-组员-m0"],
        process_names=["demo-agent.exe"],
        window_titles=["示例Harness-A - 会话"],
    )
    assert got, "有这么多事实却一条候选都没推出来"
    for item in got:
        assert item["text"].strip() and item["source"].strip() and item["why"].strip(), \
            "每条候选都必须带 text/source/why：%r" % (item,)
    texts = [i["text"] for i in got]
    assert texts[0] == "ws-x-组员-m0", "会话名优先（它最可能出现在窗口标题里）"
    assert "示例Harness-A" in texts and "示例Harness-A - 会话" in texts
    assert "demo-agent" in texts, "路径线索要取最后一段（整条路径当关键词没用）"


def test_suggestions_dedupe_and_limit():
    got = docking.suggest_keywords({"harness_name": "同名"}, session_names=["同名", " 同名 "],
                                   window_titles=["同名"], limit=2)
    assert [i["text"] for i in got] == ["同名"], "同名不同来源必须去重（大小写/空白不敏感）"
    many = docking.suggest_keywords({"harness_name": "n"},
                                    window_titles=["w%d" % i for i in range(20)], limit=3)
    assert len(many) <= 3, "limit 必须真的限量"


def test_suggestions_never_fabricate_when_there_is_nothing():
    assert docking.suggest_keywords({}) == [], "什么事实都没有 ⇒ 不许瞎编（宁可空手回去）"


def test_window_titles_related_to_the_employee_come_first():
    """扫到的窗口标题里，**跟这位员工有关的排在前面** —— "哪个最像"要一眼看出来（§3.2 屏 2）。"""
    got = docking.suggest_keywords({"harness_name": "示例Harness-A"},
                                   window_titles=["无关的记事本", "示例Harness-A - 会话", "另一个窗口"])
    texts = [i["text"] for i in got]
    assert texts.index("示例Harness-A - 会话") < texts.index("无关的记事本"), \
        "有关的标题被无关的挤到后面了"
    rel = got[texts.index("示例Harness-A - 会话")]
    assert "有关" in rel["source"], "有关的标题要标出来源（界面照实显示）：%r" % rel
    assert "无关的记事本" in texts, "无关的标题也不能藏起来（用户可能就是要点它）"


# ── ②③ 候选端点：只读 / 探不到要说清 ──────────────────────────────────────
def test_suggestions_endpoint_needs_a_harness():
    from agent_community.platform.server import Utf8JSONResponse
    res = asyncio.run(docking.docking_keyword_suggestions(""))
    assert isinstance(res, Utf8JSONResponse) and res.status_code == 400
    assert "哪一位员工" in _body(res), "缺参数要说清缺什么（准则⑤）"


def test_suggestions_endpoint_404_speaks(monkeypatch):
    from agent_community.platform.server import Utf8JSONResponse
    monkeypatch.setattr(docking, "_find_harness", lambda h: None)
    res = asyncio.run(docking.docking_keyword_suggestions("没这个"))
    assert isinstance(res, Utf8JSONResponse) and res.status_code == 404
    assert "没有这个 harness" in _body(res)


def test_probe0_really_touches_nothing(monkeypatch):
    """★`probe=0` **真的**不碰本机 —— 按行为判（不是查源码文本）。"""
    called = {"proc": 0, "win": 0}

    def _no_proc(*a, **k):
        called["proc"] += 1
        return []

    def _no_win(*a, **k):
        called["win"] += 1
        return {"ok": True, "supported": True, "detail": "", "windows": []}

    monkeypatch.setattr(docking, "_find_harness",
                        lambda h: _fake_session(h, hints={"process_hint": "demo.exe"}))
    monkeypatch.setattr(visual_launch, "list_windows", _no_win)
    import agent_community.platform.sysprobe as sp
    monkeypatch.setattr(sp, "find_processes", _no_proc)

    out = asyncio.run(docking.docking_keyword_suggestions("H-1", probe=0))
    assert called == {"proc": 0, "win": 0}, "probe=0 却去扫了本机：%r" % called
    assert out["suggestions"] and out["probe_note"], "快路径也要给候选并说明没扫本机"

    out2 = asyncio.run(docking.docking_keyword_suggestions("H-1", probe=1))
    assert called == {"proc": 1, "win": 1}, "probe=1 才该扫：%r" % called
    assert out2["suggestions"]


def test_unsupported_probe_is_reported_not_silently_empty(monkeypatch):
    """探不到 ≠ 真没有：`supported=false` 时**必须**在 `probe_note`/`notes` 里说清为什么。"""
    monkeypatch.setattr(docking, "_find_harness", lambda h: _fake_session(h))
    monkeypatch.setattr(visual_launch, "list_windows",
                        lambda *a, **k: {"ok": False, "supported": False,
                                         "detail": "非 Windows：消息式界面注入只在 Windows 上存在",
                                         "windows": []})
    out = asyncio.run(docking.docking_keyword_suggestions("H-1", probe=1))
    assert out["supported"] is False
    assert "非 Windows" in out["probe_note"], "说清为什么探不到，别只给个空列表"
    assert any("如实说" in n or "手填" in n for n in out["notes"]), \
        "要说清这台机器上该怎么往下走（不许留死状态）"


# ── ④ render_script_export（纯函数）──────────────────────────────────────
def test_export_is_a_verbatim_copy_with_provenance_header():
    script = {"text": "Write-Host '送去开会'\n", "method": "wmpaste",
              "keywords": ["示例Harness-A"], "verified": True}
    body = docking.render_script_export(
        {"harness_id": "H-1", "harness_name": "示例Harness-A"}, script,
        generated_at="2026-10-15T23:30:00+08:00")
    assert "Write-Host '送去开会'" in body, "正文必须是档案里那份脚本的**原样**副本"
    assert "# 员工：示例Harness-A（harness_id=H-1）" in body
    assert "wmpaste" in body and "示例Harness-A" in body
    assert "副本" in body and "真相在平台的 harness 档案里" in body, \
        "注释头必须写明'这是副本、真相在档案'（免得有人拿副本当正本改）"
    assert "2026-10-15T23:30:00+08:00" in body


def test_export_is_honest_about_unverified_and_never_says_done():
    """★准则⑤：`verified` 不是 True ⇒ **一律**"未核实"；**任何情况都不许出现"已完成"**。"""
    for script, want in (({"text": "x", "verified": False}, "未核实"),
                         ({"text": "x", "verified": None}, "未核实"),
                         ({"text": "x"}, "未核实"),
                         ({"text": "x", "verified": True}, "已自证")):
        body = docking.render_script_export({"harness_id": "H-1"}, script, generated_at="t")
        assert want in body, "三态没如实标：%r ⇒ %r" % (script, want)
        assert "已完成" not in body, "没自证过却出现'已完成'：%r" % script
    assert "未核实" not in docking.render_script_export(
        {"harness_id": "H-1"}, {"text": "x", "verified": True}, generated_at="t")


def test_export_has_utf8_bom_when_chinese():
    """含中文 ⇒ **必须带 BOM**（不然 PS 5.1 按 GBK 读 ⇒ ParserError）。"""
    body = docking.render_script_export({"harness_id": "H-1"}, {"text": "Write-Host '中文'"},
                                        generated_at="t")
    assert body.startswith("\ufeff"), "含中文的 .ps1 没带 UTF-8 BOM"
    enc = body.encode("utf-8")
    assert enc.startswith(b"\xef\xbb\xbf"), "字节层面也要有 BOM（不是只有内存里那个字符）"
    pure = docking.render_script_export({"harness_id": "H-1"}, {"text": "echo ok"},
                                        generated_at="t")
    assert pure.endswith("\n"), "纯 ASCII 也要以换行收尾"


# ── ⑤ 导出端点 ─────────────────────────────────────────────────────────
def test_export_without_script_says_what_to_do_next(monkeypatch):
    from agent_community.platform.server import Utf8JSONResponse
    monkeypatch.setattr(docking, "_find_harness", lambda h: _fake_session(h))
    res = asyncio.run(docking.docking_harness_export_ps1("H-1"))
    assert isinstance(res, Utf8JSONResponse) and res.status_code == 404
    msg = _body(res)
    assert "没有存过激活脚本" in msg and "脚本激活" in msg, \
        "不许给'下载了个空文件'的死状态 —— 要指出下一步去哪做一份"


def test_export_unknown_harness_speaks(monkeypatch):
    from agent_community.platform.server import Utf8JSONResponse
    monkeypatch.setattr(docking, "_find_harness", lambda h: None)
    res = asyncio.run(docking.docking_harness_export_ps1("没这个"))
    assert isinstance(res, Utf8JSONResponse) and res.status_code == 404
    assert "没有这个 harness" in _body(res)


def test_export_returns_a_downloadable_ps1(monkeypatch):
    script = {"text": "Write-Host '送去开会'", "method": "clipboard",
              "keywords": ["示例Harness-A"], "verified": False}
    monkeypatch.setattr(docking, "_find_harness", lambda h: _fake_session(h, script=script))
    res = asyncio.run(docking.docking_harness_export_ps1("H-1"))
    assert getattr(res, "status_code", 200) == 200
    body = res.body.decode("utf-8")
    assert body.startswith("\ufeff") and "Write-Host '送去开会'" in body
    assert "未核实" in body and "已完成" not in body
    cd = res.headers.get("content-disposition", "")
    assert "attachment" in cd and ".ps1" in cd and "filename*=UTF-8''" in cd, \
        "下载头要给出 .ps1 文件名（中文名走 RFC 5987）：%r" % cd
    assert "text/plain" in res.media_type and "utf-8" in res.media_type


# ── 契约：两件都是 GET ⇒ **不进写白名单**（白名单管的是非 GET）──────────────
def test_new_endpoints_are_get_only():
    paths = {getattr(r, "path", ""): set(getattr(r, "methods", set()) or set())
             for r in docking.router.routes}
    for p in ("/api/docking/keyword-suggestions", "/api/docking/harness/{harness_id}/export.ps1"):
        assert p in paths, "端点没挂上：%s" % p
        assert paths[p] == {"GET"}, "%s 必须只有 GET（它只读；写请求要走白名单）：%r" % (p, paths[p])
