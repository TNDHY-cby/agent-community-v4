# -*- coding: utf-8 -*-
"""V53 接线层守卫：隔间继承的**两个端点**必须"失败要说话"、且只读的那个真不写盘。

内核判据在 `test_compartment_inherit.py`（11 条）；这里守接口层：
  · 不传来源 ⇒ **400 + 明说"平台不会替你猜"**（不是 200 假装成功）；
  · 来源/目标成员不存在 ⇒ 404；来源隔间还没开工 ⇒ 409（如实说"没有可继承的产物"）；
  · 来源与目标是同一人 ⇒ 400；
  · 成功 ⇒ 成员三个字段**落盘**（`inherited_from/inherited_at/inherit_note`）+ 审计一条；
  · `GET` ⇒ **不写盘**（前后隔间文件清单一致），且把继承文原样读回。

⚠️ 直接调用端点函数（不启服务）——与本项目其它接线守卫同一做法，快且不依赖端口。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

from agent_community.platform import state as _state                    # noqa: E402
from agent_community.platform.compartment import compartment_dir        # noqa: E402
from agent_community.platform.routers import workshops_inherit as wi    # noqa: E402
from agent_community.platform.workshop import Workshop, WorkshopMember  # noqa: E402


class _Req:
    """最小 Request 替身（端点只用 `await request.json()`）。"""

    def __init__(self, body=None):
        self._body = body if body is not None else {}
        self.raise_on_json = False

    async def json(self):
        if self.raise_on_json:
            raise ValueError("坏 body")
        return self._body


def _call(coro):
    return asyncio.run(coro)


def _resp_json(resp):
    """端点可能返回 dict 或 Utf8JSONResponse ⇒ 统一拿 (status, body-dict)。"""
    if isinstance(resp, dict):
        return 200, resp
    body = getattr(resp, "body", b"")
    try:
        return getattr(resp, "status_code", 200), json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return getattr(resp, "status_code", 200), {}


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    ws_dir = tmp_path / "ws_api"
    ws_dir.mkdir()
    old = compartment_dir(ws_dir, "码农", "m_old")
    new = compartment_dir(ws_dir, "码农", "m_new")
    for d in (old, new):
        d.mkdir(parents=True)
    (old / "output").mkdir()
    (old / "output" / "plan.md").write_text("旧方案\n", encoding="utf-8")
    (old / "inbox").mkdir()
    (old / "inbox" / "t.json").write_text("{}", encoding="utf-8")
    w = Workshop(workshop_id="ws_api", name="接口测试间", workspace_dir=str(ws_dir),
                 hall_content="h",
                 members=[WorkshopMember(member_id="m_old", role="码农", display_name="老王"),
                          WorkshopMember(member_id="m_new", role="码农", display_name="小新")])
    _state.workshops["ws_api"] = w
    monkeypatch.setattr(wi, "save_state", lambda *a, **k: None, raising=False)
    yield {"ws": w, "dir": ws_dir, "old": old, "new": new}
    _state.workshops.pop("ws_api", None)


def _post(ws, body, mid="m_new"):
    return _resp_json(_call(wi.inherit_member("ws_api", mid, _Req(body))))


def _get(ws, mid="m_new"):
    return _resp_json(_call(wi.inherit_status("ws_api", mid)))


# ── 接口①：不传来源 ⇒ 400，且**不写盘** ─────────────────────────────────────────
def test_post_without_source_is_400_and_writes_nothing(ws):
    code, body = _post(ws, {})
    assert code == 400, body
    assert body["success"] is False and "不会替你猜" in body["reason"]
    assert list(ws["new"].iterdir()) == []


def test_post_bad_body_still_answers(ws):
    r = _Req({})
    r.raise_on_json = True
    code, body = _resp_json(_call(wi.inherit_member("ws_api", "m_new", r)))
    assert code == 400 and "from_member_id" in body["reason"]


# ── 接口②：成员不存在 ⇒ 404；来源隔间未开工 ⇒ 409；自己继承自己 ⇒ 400 ───────────────
def test_post_unknown_members_are_404(ws):
    assert _post(ws, {"from_member_id": "m_old"}, mid="m_ghost")[0] == 404
    assert _post(ws, {"from_member_id": "m_ghost"})[0] == 404


def test_post_self_inherit_is_400(ws):
    code, body = _post(ws, {"from_member_id": "m_new"})
    assert code == 400 and "同一个成员" in body["reason"]


def test_post_source_without_compartment_is_409(ws):
    (ws["old"] / "output" / "plan.md").unlink()
    ws["old"].rmdir() if not any(ws["old"].iterdir()) else None
    import shutil
    shutil.rmtree(ws["old"])
    code, body = _post(ws, {"from_member_id": "m_old"})
    assert code == 409, body
    assert body["success"] is False
    assert "来源隔间" in body["reason"], "要如实说清是「来源还没有可继承的产物」"


# ── 接口③：成功 ⇒ 三字段落盘 + 结果三态 + 继承文写入 ─────────────────────────────
def test_post_success_persists_fields_and_reports_counts(ws):
    code, body = _post(ws, {"from_member_id": "m_old"})
    assert code == 200 and body["success"] is True and body["verified"] is True
    m = next(x for x in ws["ws"].members if x.member_id == "m_new")
    assert m.inherited_from == "m_old" and m.inherited_at > 0
    assert "搬 1 项" in m.inherit_note
    assert (ws["new"] / "legacy.md").is_file()
    assert (ws["new"] / "output" / "plan.md").read_text(encoding="utf-8") == "旧方案\n"
    # 落盘口径：序列化里必须有这三项（否则重启即丢）
    d = ws["ws"].to_dict()
    mnew = next(x for x in d["members"] if x["member_id"] == "m_new")
    assert mnew["inherited_from"] == "m_old" and mnew["inherited_at"] > 0 and mnew["inherit_note"]


def test_post_audit_recorded(ws, monkeypatch):
    seen = {}
    monkeypatch.setattr(wi._audit_log, "record",
                        lambda action, **kw: seen.update({"action": action, **kw}))
    _post(ws, {"from_member_id": "m_old"})
    assert seen.get("action") == "workshop.member_inherit"
    assert seen.get("extra", {}).get("verified") is True


# ── 接口④：GET 只读（不写盘）+ 读回继承文 ─────────────────────────────────────
def test_get_is_read_only_and_returns_legacy_text(ws):
    before = {p.name for p in ws["new"].rglob("*")}
    code, body = _get(ws)
    assert code == 200 and body["inherited_from"] == "" and body["legacy_exists"] is False
    assert "没有继承过" in body["note"]
    _post(ws, {"from_member_id": "m_old"})
    after_post = {p.name for p in ws["new"].rglob("*")}
    code2, body2 = _get(ws)
    assert code2 == 200 and body2["inherited_from"] == "m_old" and body2["legacy_exists"] is True
    assert "继承文" in body2["legacy_text"] and "m_old" in body2["legacy_text"]
    assert body2["legacy_truncated"] is False
    assert {p.name for p in ws["new"].rglob("*")} == after_post, "GET 不许写盘"
    assert before != after_post


def test_get_unknown_member_is_404(ws):
    assert _get(ws, mid="m_ghost")[0] == 404
