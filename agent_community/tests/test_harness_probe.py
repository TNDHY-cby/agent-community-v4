# -*- coding: utf-8 -*-
"""V42 §4.5 **平台侧主动探测**：由平台去测外端 agent 有什么接口（零 token）。

老板原话：「**要是平台有 agent 可以去拉桥就好了**，这样就不怕外端 agent 因为
**沙箱等限制无法检测自身接口**了」（2026-10-11 22:30）

**为什么这条必须有**：外端 agent 常被沙箱限制，**看不到自己的进程/端口/路径**
（实测：一个**桌面型**外端 agent 回"我什么接口都没有" —— 那是**它看不到**）。
平台跑在宿主上、看得见它。（★对外源码不写具体产品名：规则 6「一律用虚拟名」。）

本文件锁五条纪律（都写进了实现）：
1. **零 token**：探测全程脚本，返回体 `token_cost=0` / `from_ai=False`；
2. **只给线索，不下定论**：候选带证据等级（`strong` / `weak`），
   且**必须说明"要写进模板得跑通一次"**；
3. **探不到也要说清为什么**（不许留白、不许暗示"它没有"）；
4. **线索可以显式给**（agent 最清楚自己是谁），也可以从 `metadata["probe_hints"]` 取；
5. **不给线索时不瞎猜**（不产生候选）。

⚠️ 全部用桩：**绝不真去扫本机进程/连端口**（那会让测试依赖环境、还可能打到真实端口）。
"""
from __future__ import annotations

import pytest

from agent_community.platform import harness_probe as hp


class _Info:
    """最小 HarnessInfo 替身（本模块只按属性/字典读它）。"""

    def __init__(self, **kw):
        self.harness_id = kw.get("harness_id", "h1")
        self.wakeup_dir = kw.get("wakeup_dir", "")
        self.acp_command = kw.get("acp_command", "")
        self.api_base_url = kw.get("api_base_url", "")
        self.metadata = kw.get("metadata", {}) or {}


@pytest.fixture(autouse=True)
def _no_real_probing(monkeypatch):
    """把三个探测动作全部打桩 —— 测试**不许**真扫进程 / 真连端口 / 真发 HTTP。"""
    monkeypatch.setattr(hp, "_probe_process", lambda *a, **k: [], raising=False)
    monkeypatch.setattr(hp, "_probe_port", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(hp, "_probe_http", lambda *a, **k: {"ok": False, "detail": "stub"}, raising=False)


def test_zero_token_and_not_from_ai():
    """★纪律 1：探测**不调用 AI**（老板："如果全脚本的话另说"）。"""
    out = hp.probe_harness(_Info())
    assert out["from_ai"] is False
    assert out["token_cost"] == 0


def test_no_hints_says_why_and_does_not_pretend_absence():
    """★纪律 3+5：没线索 ⇒ 无候选，且**明说"这不等于它没有"**。"""
    out = hp.probe_harness(_Info())
    assert out["candidates"] == []
    assert "不等于它没有" in out["note"], out["note"]


def test_port_open_yields_weak_candidate_not_a_verdict(monkeypatch):
    """★纪律 2：端口能连上只是**弱证据**，不许当成"它就是 HTTP API 型"。"""
    monkeypatch.setattr(hp, "_probe_port", lambda *a, **k: True, raising=False)
    out = hp.probe_harness(_Info(), do_http=False, hints_in={"port_hint": "18920"})
    kinds = [c["kind"] for c in out["candidates"]]
    assert "http_api" in kinds
    c = next(c for c in out["candidates"] if c["kind"] == "http_api")
    assert c["evidence"] == hp.EVIDENCE_WEAK
    assert "还要探端点" in c["why"], c["why"]
    assert "跑通" in out["note"], "必须提示：写进模板前要跑通一次"


def test_http_endpoint_answering_is_strong_evidence(monkeypatch):
    """端点真的应答了 ⇒ 强证据（但仍需跑通才算数）。"""
    monkeypatch.setattr(hp, "_probe_http",
                        lambda *a, **k: {"ok": True, "detail": "200 OK"}, raising=False)
    out = hp.probe_harness(_Info(api_base_url="http://127.0.0.1:1234"), do_http=True)
    c = next((x for x in out["candidates"] if x["kind"] == "http_api"), None)
    assert c is not None and c["evidence"] == hp.EVIDENCE_STRONG
    assert "无需桥" in c["why"]


def test_process_hit_suggests_cli_but_needs_a_command(monkeypatch):
    """进程像它 ⇒ 弱证据，且要**明确"仍需你给一条命令"**（不能替他编）。"""
    monkeypatch.setattr(hp, "_probe_process",
                        lambda *a, **k: [{"pid": 1, "name": "x.exe", "cmd": "x --go"}], raising=False)
    out = hp.probe_harness(_Info(), do_http=False, hints_in={"process_hint": "x.exe"})
    c = next((x for x in out["candidates"] if x["kind"] == "cli_acp"), None)
    assert c is not None and c["evidence"] == hp.EVIDENCE_WEAK
    assert "仍需你给一条命令" in c["why"], c["why"]


def test_hints_can_come_from_metadata(monkeypatch):
    """线索也可从 `metadata["probe_hints"]` 取（此前只写在文档里、从没落盘过）。"""
    seen = {}

    def _spy(needle, *a, **k):
        seen["needle"] = needle
        return []

    monkeypatch.setattr(hp, "_probe_process", _spy, raising=False)
    hp.probe_harness(_Info(metadata={"probe_hints": {"process_hint": "from-meta"}}), do_http=False)
    assert seen.get("needle") == "from-meta"


def test_inbox_directory_is_checked_when_present(monkeypatch, tmp_path):
    """有邮箱目录且可写 ⇒ 弱候选（file_poll），并说清"要看它会不会取"。"""
    d = tmp_path / "inbox"
    d.mkdir()
    out = hp.probe_harness(_Info(wakeup_dir=str(d)), do_http=False)
    c = next((x for x in out["candidates"] if x["kind"] == "file_poll"), None)
    assert c is not None and c["evidence"] == hp.EVIDENCE_WEAK
    assert "要跑通才算" in c["why"]


def test_probe_never_raises_on_broken_info(monkeypatch):
    """任何怪输入都不许把探测搞崩（探测是"辅助"，不是主流程的必经之路）。"""
    class _Weird:
        pass

    out = hp.probe_harness(_Weird())
    assert isinstance(out["candidates"], list)
    assert out["token_cost"] == 0


def test_evidence_levels_are_distinct_constants():
    """三档证据要互不相同（免得有人把 weak 写成 strong）。"""
    assert len({hp.EVIDENCE_STRONG, hp.EVIDENCE_WEAK, hp.EVIDENCE_NONE}) == 3
