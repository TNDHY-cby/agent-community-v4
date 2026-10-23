# -*- coding: utf-8 -*-
"""守卫：`POST /api/workshop` 的**字段口径**（2026-10-22 夜场三加，起因是我自己踩的坑）。

事故：我拿 `{"title": "…"}` 建工作间，接口 200、没有任何提示，大厅里却出现一排
**「未命名工作间」**——名字字段其实叫 `name`，`title` 被**静默忽略**。
对用户/前端来说这就是「失败不说话」：以为命名成功了，实际没有。

⇒ 两条判据（都是真请求，真拿响应体）：
1. 传 `name` ⇒ 名字就是它，**不许带 warnings**（别把正常调用也吵一遍）；
2. 传认不出的字段（如 `title`）⇒ 200 照建，但响应里**必须**有 `warnings`，
   且那句话要点出**认不出的字段名**和**该用的字段名**（用户照着就能改对）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from agent_community.platform import server as srv  # noqa: E402

C = TestClient(srv.app, client=("127.0.0.1", 54321))


def _create(body: dict) -> dict:
    r = C.post("/api/workshop", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out.get("success") is True, out
    assert out.get("workshop_id"), out
    # 收尾：删掉，别把测试工作间留在真实数据目录里（隔离靠 conftest，但删除也要真跑一次）
    d = C.delete("/api/workshop/%s" % out["workshop_id"])
    assert d.status_code == 200, d.text
    return out


def test_name_field_sets_workshop_name_without_warnings():
    out = _create({"name": "示例工作间-A", "hall_content": "把资料整理成周报"})
    assert out.get("name") == "示例工作间-A", out
    assert not out.get("warnings"), "正常调用不该有 warning（别把用户吵麻了）：%s" % out


def test_unknown_field_is_reported_not_silently_ignored():
    out = _create({"title": "用错字段建间", "hall_content": "把资料整理成周报"})
    warns = out.get("warnings") or []
    assert warns, "传了认不出的字段却没给 warning —— 用户会以为命名成功了：%s" % out
    joined = " ".join(warns)
    assert "title" in joined, "warning 要点出**认不出的那个字段名**：%s" % joined
    assert "name" in joined, "warning 要告诉用户**该用哪个字段**：%s" % joined


def test_workshop_detail_exposes_gate_reason():
    """详情页也要能说清「为什么不能开工」。

    ★2026-10-23：列表 `/api/workshops` 早就带 `gate`，但详情是**逐字段映射**，漏了它 ⇒
    用户在详情页看不到原因，只有点了"开工"才发现 409。这条把 `gate` 钉在详情上。
    """
    r = C.post("/api/workshop", json={"name": "示例工作间-G", "hall_content": "空成员闸门检查"})
    assert r.status_code == 200, r.text
    wid = r.json()["workshop_id"]
    try:
        d = C.get("/api/workshop/%s" % wid).json()
        gate = d.get("gate") or {}
        assert gate, "详情里没有 gate（前端没法提前显示横幅）：%s" % list(d)
        assert gate.get("ok") is False and gate.get("state") == "no_members", gate
        assert "没有成员" in str(gate.get("why") or ""), gate
        assert gate.get("next_step"), "next_step 必须可执行（告诉用户去哪加成员）：%s" % gate
    finally:
        C.delete("/api/workshop/%s" % wid)
