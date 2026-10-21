# -*- coding: utf-8 -*-
"""守卫：**AI 相关失败必须开口**（老板准则⑤「失败要说话」）。

背景（外部审查 P3-7 + 本次 P0-3）：`ai_provider` / `orchestrator` 里曾有 8 处
`except …: pass` —— AI 回了非 JSON、直连失败、读配置失败，**全被无声吞掉**，
用户看到的是"回了个空结构"或"悄悄走了另一条路"，却不知道 AI 这一路已经坏了。
这正是老板最反感的"看起来成功"。现在它们都走 `_warn_once(key, msg, exc)`。

判据：
  ① **`_warn_once` 真的只喊一次**（同一 key 第二次不再记）—— 真跑 logging，用 caplog 看；
  ② 失败信息里带**真实原因**（异常文本），不是"出错了"三个字；
  ③ 两处路径里**不许再出现裸 `except …: pass`**（源码级兜底，防回退）；
  ④ 四个关键 key 必须都在源码里被调用（防止有人删了调用、只留 helper）。
"""
from __future__ import annotations

import ast
import io
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import ai_provider as aip  # noqa: E402

AI_PATH = _ROOT / "agent_community" / "platform" / "ai_provider.py"
ORCH_PATH = _ROOT / "agent_community" / "platform" / "orchestrator.py"
GUI_PATH = _ROOT / "agent_community" / "gui.py"

#: 防回退：这些 key 必须真的在对应文件里被调用
REQUIRED_KEYS = {
    AI_PATH: ["reply_not_json", "cfg_read_failed"],
    ORCH_PATH: ["direct_exec_failed", "direct_review_failed"],
    GUI_PATH: ["wait_status_failed", "kill_failed"],
}


def test_warn_once_really_warns_only_once(caplog):
    aip._WARNED.clear()
    with caplog.at_level(logging.WARNING, logger="ac.ai"):
        aip._warn_once("k_demo", "第一类失败：AI 回复不是合法 JSON（示例）")
        aip._warn_once("k_demo", "第一类失败：AI 回复不是合法 JSON（示例）")
        aip._warn_once("k_demo2", "第二类失败：读配置失败（示例）")
    recs = [r for r in caplog.records if r.name == "ac.ai"]
    assert len(recs) == 2, "同类失败应当只记一次，实际 %d 条" % len(recs)
    assert "不是合法 JSON" in caplog.text and "读配置失败" in caplog.text, caplog.text


def test_warn_once_carries_exception_detail(caplog):
    aip._WARNED.clear()
    err = ValueError("Expecting value: line 1 column 1 (char 0)")
    with caplog.at_level(logging.WARNING, logger="ac.ai"):
        aip._warn_once("k_exc", "AI 回复不是合法 JSON：%s" % err, err)
    assert "Expecting value" in caplog.text, ("日志里要看得到**真实原因**，"
                                             "不能只说'出错了'：%r" % caplog.text)
    rec = [r for r in caplog.records if r.name == "ac.ai"][0]
    assert rec.exc_info is not None, "带异常进来时应当把栈也记下来（exc_info=True）"


def test_no_bare_silent_except_left_in_ai_paths():
    bad = []
    for p in (AI_PATH, ORCH_PATH, GUI_PATH):
        src = io.open(p, encoding="utf-8").read()
        lines = src.splitlines()
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.ExceptHandler):
                continue
            body = [b for b in node.body
                    if not (isinstance(b, ast.Expr) and isinstance(getattr(b, "value", None), ast.Constant))]
            if len(body) != 1 or not isinstance(body[0], ast.Pass):
                continue
            chunk = "\n".join(lines[max(0, node.lineno - 3):getattr(node, "end_lineno", node.lineno) + 1])
            if "warn_once" in chunk or "logger" in chunk or "noqa" in chunk.lower():
                continue
            bad.append("%s:%d" % (p.name, node.lineno))
    assert not bad, "这些地方还在无声吞掉 AI/派活失败：%s" % bad


def test_required_warn_keys_are_called():
    for p, keys in REQUIRED_KEYS.items():
        src = io.open(p, encoding="utf-8").read()
        missing = [k for k in keys if k not in src]
        assert not missing, "%s 里少了这些失败 key 的调用（有人删了守护？）：%s" % (p.name, missing)
