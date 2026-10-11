# -*- coding: utf-8 -*-
"""V29 §2「路径 B」：AI 生成**项目模型草案** 的测试。

要点（都是准则⑤"失败要说话"的体现）：
- **未配 AI ⇒ 明确 400 + 手工指引**，不假装生成；
- **草案不落盘**：`library/model.md` 不许被草案偷偷写出来（人要确认了才写）；
- AI 返回非 JSON / 调用失败 ⇒ **如实报错**（带原文片段），不许当成功。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
from types import SimpleNamespace

from agent_community.platform.routers import workshops_common as wc


class _AiStub:
    """假 AI Provider：`chat()` 返回预设文本（或抛错）。"""

    def __init__(self, reply: str = "", exc: Exception | None = None):
        self._reply = reply
        self._exc = exc
        self.seen: list = []

    async def chat(self, system, user):
        self.seen.append((system, user))
        if self._exc:
            raise self._exc
        return self._reply


def _wire(monkeypatch, tmp_path, ai):
    from agent_community.platform.routers import workshops_lifecycle as wl
    from agent_community.platform.state import workshops
    from agent_community.platform.workshop import Workshop, WorkshopMember

    ws = Workshop(workshop_id="wd1", name="看板项目", workspace_dir=str(tmp_path / "ws"),
                  hall_content="做一个看板", created_at="now",
                  members=[WorkshopMember(member_id="m0", role="码农", display_name="小码",
                                          harness_ids=["h1"])])
    ws.discussion = [{"role": "user", "content": "要能拖拽", "timestamp": "t"},
                     {"role": "orchestrator", "content": "先定模块", "timestamp": "t"}]
    workshops[ws.workshop_id] = ws
    monkeypatch.setattr(wc, "_sv", lambda: SimpleNamespace(ai_provider=ai), raising=False)
    return wl, ws


def test_no_ai_is_honest_400(tmp_path, monkeypatch):
    """未配 AI ⇒ 400 + 手工指引 + 五块标题；**不生成、不落盘**。"""
    wl, ws = _wire(monkeypatch, tmp_path, None)
    r = asyncio.run(wl.workshop_model_draft(ws.workshop_id))
    body = json.loads(bytes(r.body).decode("utf-8"))
    assert r.status_code == 400
    assert "未配置 AI" in body["error"] and body["hint"]
    assert len(body["sections"]) == 5
    assert not (pathlib.Path(ws.workspace_dir) / "library" / "model.md").exists()


def test_draft_success_and_no_file_written(tmp_path, monkeypatch):
    """配了 AI ⇒ 返回五块草案 + filled/missing；**关键：草案不落盘**。"""
    ai = _AiStub(json.dumps({"goal": "做出看板", "parts": "前端+后端", "roles": "码农做前端",
                             "deps": "", "outputs": "码农 output/"}, ensure_ascii=False))
    wl, ws = _wire(monkeypatch, tmp_path, ai)
    r = asyncio.run(wl.workshop_model_draft(ws.workshop_id))
    assert r["success"] is True
    assert set(r["draft"]) == {"goal", "parts", "roles", "deps", "outputs"}
    assert r["draft"]["goal"] == "做出看板"
    assert r["filled"] == ["goal", "parts", "roles", "outputs"]
    assert len(r["missing"]) == 1 and "依赖" in r["missing"][0]
    assert r["verified"] is False and "草案" in r["note"]
    # ★草案**不许**自己落盘（人要确认后才写）
    assert not (pathlib.Path(ws.workspace_dir) / "library" / "model.md").exists()
    # 提示词里确实带了大厅内容与讨论历史（不是空调用）
    system, user = ai.seen[0]
    assert "严格输出 JSON" in system and "做一个看板" in user


def test_non_json_reply_is_reported(tmp_path, monkeypatch):
    wl, ws = _wire(monkeypatch, tmp_path, _AiStub("抱歉，我先聊聊思路…"))
    r = asyncio.run(wl.workshop_model_draft(ws.workshop_id))
    assert r["success"] is False and "不是合法 JSON" in r["error"] and r["raw"]


def test_ai_error_is_reported(tmp_path, monkeypatch):
    wl, ws = _wire(monkeypatch, tmp_path, _AiStub(exc=RuntimeError("HTTP 401")))
    r = asyncio.run(wl.workshop_model_draft(ws.workshop_id))
    assert r["success"] is False and "AI 调用失败" in r["error"] and "401" in r["error"]


def test_ai_error_text_is_reported(tmp_path, monkeypatch):
    """讨论那边同款口径：`[Error:` / `[AI 讨论失败:` 开头的文本当失败，不当草案。"""
    wl, ws = _wire(monkeypatch, tmp_path, _AiStub("[Error: HTTP 401]"))
    r = asyncio.run(wl.workshop_model_draft(ws.workshop_id))
    assert r["success"] is False and "401" in r["error"]


def test_missing_workshop_404(tmp_path, monkeypatch):
    from agent_community.platform.routers import workshops_lifecycle as wl
    monkeypatch.setattr(wc, "_sv", lambda: SimpleNamespace(ai_provider=_AiStub("{}")), raising=False)
    r = asyncio.run(wl.workshop_model_draft("nope"))
    assert r.status_code == 404
