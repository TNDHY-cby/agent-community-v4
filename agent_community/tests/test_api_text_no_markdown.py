# -*- coding: utf-8 -*-
"""守卫：**API 返回给界面的文案里不许带 markdown**（前端会照原样显示）。

起因（MiMo 通道 227 第 3 条，第三个同类问题）：`GET /api/harness/{id}/capability` 的
`verdict_text` / `segments[].why` 里带了 `**`（加粗），界面上就真的是两个星号。
本守卫跑**真函数**，断言返回结构里任何字符串都不含 `**`。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import harness_capability as hc  # noqa: E402
from agent_community.platform import reply_probe as rp  # noqa: E402


def _rec(**kw):
    base = {"harness_id": "示例Harness-A", "harness_name": "示例Harness-A",
            "harness_type": "desktop-agent", "wakeup_method": "clipboard",
            "ai": {"model_name": "m", "provider": "p", "capabilities": ["coding"]},
            "tools": [], "metadata": {}}
    base.update(kw)
    return base


def test_capability_texts_have_no_markdown():
    # 三种状态都过一遍：只有桥配置 / 验过（自动）/ 验过（人工确认）
    cases = [
        _rec(metadata={"bridge_test": {"ok": True, "echo": "alive"}}),
        _rec(wakeup_method="file_poll",
             metadata={"reply_probe": {"state": "ok", "source": "auto", "at": "2026-10-21T23:00:00",
                                       "evidence": {"echo": "回执"}}}),
        _rec(metadata={"reply_probe": {"state": "ok", "source": "human_confirm",
                                       "at": "2026-10-21T23:00:00"}}),
        _rec(metadata={"reply_probe": {"state": "failed", "why": "超时"}}),
    ]
    for rec in cases:
        blob = json.dumps(hc.build_provenance(rec), ensure_ascii=False)
        assert "**" not in blob, "capability 返回里还有 markdown 加粗：%s" % blob[:200]


def test_reply_probe_texts_have_no_markdown():
    # 人工中转型的拒绝文案（前端会原样上屏）
    class _Info:
        harness_id = "示例Harness-B"
        wakeup_method = "clipboard"
        metadata = {}

    class _Sess:
        info = _Info()
        metadata = {}

    out = rp.start(_Sess())
    assert out.get("ok") is False
    assert "**" not in json.dumps(out, ensure_ascii=False), out

    rec = rp.human_confirm(_Sess(), note="我发了，它回了")["source"]
    assert rec == "human_confirm"
