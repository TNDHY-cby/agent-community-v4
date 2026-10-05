# -*- coding: utf-8 -*-
"""V-13 拆分产物测试：`platform/routers/workshops_task.py`（任务树与资源）。

**为什么先补这块**：`platform/` 65 个模块里 28 个零测试引用，其中
`workshops_assign` / `workshops_discuss` / `workshops_task` 是**工作间主链路**的三个大块
（合计 31 个端点）。本文件覆盖第三个。

拆分纪律（文件头原文）：**「路由路径、参数、响应体与拆分前逐字一致（纯搬迁，零行为变更）」**
—— 所以这里锁的正是"搬迁时最容易搬歪"的行为差异。

本文件重点锁死的不变量：
- `create_tree_node` 对非法 `kind`/`status` **静默归一化**，
  而 `update_tree_node_status` 对非法 status **直接拒绝 400** ——
  **两个端点语义故意不同**，这是最容易被"统一一下"改坏的地方；
- 资源挂载是**双向**的（`node.resources` ↔ `res.task_node`）且**幂等**；
- `halt-and-reset` 的擦除受 `wipe_members` / `wipe_leader` 控制，默认只擦员工不擦组长；
- 删除/更新"不存在的对象"必须 404，不能静默成功（否则前端以为删掉了）。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from agent_community.platform import state as state_mod
from agent_community.platform.routers import workshops_task as wt
from agent_community.platform.workshop import Workshop, WorkshopMember

VALID_STATUSES = ("active", "done", "bypassed", "dropped")


def run(coro):
    return asyncio.run(coro)


def unpack(resp):
    if hasattr(resp, "status_code") and hasattr(resp, "body"):
        return resp.status_code, json.loads(resp.body)
    return 200, resp


class FakeRequest:
    def __init__(self, body=None):
        self.headers = {}
        self._body = body if body is not None else {}

    async def json(self):
        return self._body


@pytest.fixture()
def ws(tmp_path):
    """隔离工作间：workspace_dir 指向 tmp_path，绝不写真实工作区。"""
    w = Workshop(
        workshop_id="w-task", name="任务测试间",
        workspace_dir=str(tmp_path / "ws_task"),
        hall_content="把首页做出来",
        members=[WorkshopMember(member_id="m1", role="员工", display_name="示例Harness-A")],
        status="running",
    )
    state_mod.workshops[w.workshop_id] = w
    try:
        yield w
    finally:
        state_mod.workshops.pop(w.workshop_id, None)


def _add_node(ws, label="子任务", parent_id="", kind="task", status="active"):
    """经端点建节点（比直接改 ws.task_tree 更贴近真实用法）。"""
    return unpack(run(wt.create_tree_node(
        ws.workshop_id,
        FakeRequest({"label": label, "parent_id": parent_id,
                     "kind": kind, "status": status}))))[1]["node"]


def _add_resource(ws, name="产出.md", path="/tmp/out.md", **kw):
    body = {"name": name, "path": path}
    body.update(kw)
    return unpack(run(wt.add_resource(ws.workshop_id, FakeRequest(body))))[1]["resource"]


# ══════════════════════════════════════════════════════════════════
# halt-and-reset（弯路复位）
# ══════════════════════════════════════════════════════════════════
class TestHaltAndReset:
    def test_missing_workshop_404(self):
        status, out = unpack(run(wt.workshop_halt_and_reset(
            "w-ghost", FakeRequest({"reason": "走错了"}))))
        assert status == 404 and "不存在" in out["error"]

    def test_missing_reason_400(self, ws):
        status, out = unpack(run(wt.workshop_halt_and_reset(
            ws.workshop_id, FakeRequest({}))))
        assert status == 400
        assert "reason" in out["error"], "错误信息应点名缺的是 reason"

    def test_blank_reason_rejected(self, ws):
        """只有空格也算缺 —— 否则复盘文档会写进一片空白。"""
        status, _ = unpack(run(wt.workshop_halt_and_reset(
            ws.workshop_id, FakeRequest({"reason": "   "}))))
        assert status == 400

    def test_resets_workshop_and_members(self, ws):
        ws.members[0].status = "working"
        status, out = unpack(run(wt.workshop_halt_and_reset(
            ws.workshop_id, FakeRequest({"reason": "方案选错了"}))))
        assert status == 200 and out["success"] is True
        assert ws.status == "draft", "复位后应回到 draft 可重新开始"
        assert out["status"] == "draft"
        assert ws.members[0].status == "pending", "成员状态应回到 pending"

    def test_creates_correction_node_pair(self, ws):
        """纠偏要同时留「错误路(dropped)」与「正路(active)」两个节点。"""
        _status, out = unpack(run(wt.workshop_halt_and_reset(
            ws.workshop_id, FakeRequest({"reason": "用了错误接口", "summary": "改用 v2"}))))
        created = {n["node_id"]: n for n in ws.task_tree}
        wrong = created[out["results"]["tree"]["wrong_node"]]
        right = created[out["results"]["tree"]["right_node"]]
        assert wrong["kind"] == "correction" and wrong["status"] == "dropped"
        assert right["kind"] == "correction" and right["status"] == "active"
        assert "错误接口" in wrong["label"]

    def test_archives_detour_summary_file(self, ws):
        _status, out = unpack(run(wt.workshop_halt_and_reset(
            ws.workshop_id, FakeRequest({"reason": "走弯路原因"}))))
        from pathlib import Path
        p = Path(out["detour_path"])
        assert p.exists(), "应归档 DETOUR_SUMMARY.md"
        text = p.read_text(encoding="utf-8")
        assert "走弯路原因" in text

    def test_wipes_discussion_by_default(self, ws):
        ws.discussion = [{"role": "员工", "content": "旧结论", "timestamp": "t"}]
        _status, out = unpack(run(wt.workshop_halt_and_reset(
            ws.workshop_id, FakeRequest({"reason": "重来"}))))
        assert out["results"]["discussion_cleared"] is True
        assert ws.discussion == [], "默认应清空讨论上下文（擦除记忆）"

    def test_wipe_disabled_keeps_discussion(self, ws):
        ws.discussion = [{"role": "员工", "content": "旧结论", "timestamp": "t"}]
        _status, out = unpack(run(wt.workshop_halt_and_reset(
            ws.workshop_id,
            FakeRequest({"reason": "重来", "wipe_members": False, "wipe_leader": False}))))
        assert out["results"]["discussion_cleared"] is False
        assert ws.discussion, "显式关掉擦除时不得清空讨论"

    def test_clears_transient_internal_attrs(self, ws):
        """那些 `_xxx_done` 内部标记必须清掉，否则复位后不会被重新触发。"""
        ws._auto_review_done = True
        ws._parallel_done = True
        run(wt.workshop_halt_and_reset(
            ws.workshop_id, FakeRequest({"reason": "重来"})))
        assert not hasattr(ws, "_auto_review_done")
        assert not hasattr(ws, "_parallel_done")

    @pytest.mark.parametrize("restart,expect", [(True, "重新开始"), (False, "保留在 draft")])
    def test_restart_flag_changes_note(self, ws, restart, expect):
        _status, out = unpack(run(wt.workshop_halt_and_reset(
            ws.workshop_id, FakeRequest({"reason": "重来", "restart": restart}))))
        assert expect in out["note"]


# ══════════════════════════════════════════════════════════════════
# 资源库 CRUD
# ══════════════════════════════════════════════════════════════════
class TestResources:
    def test_list_missing_workshop_404(self):
        status, _ = unpack(run(wt.list_resources("w-ghost")))
        assert status == 404

    def test_list_backfills_default_kind(self, ws):
        """历史资源可能没有 kind 字段 —— 读出时必须补默认值，前端才不炸。"""
        ws.resources = [{"rid": "r1", "name": "老资源", "path": "/x"}]
        out = run(wt.list_resources(ws.workshop_id))
        assert out["resources"][0]["kind"] == "file"

    def test_add_missing_workshop_404(self):
        status, _ = unpack(run(wt.add_resource("w-ghost", FakeRequest(
            {"name": "a", "path": "/p"}))))
        assert status == 404

    def test_add_empty_name_400(self, ws):
        status, out = unpack(run(wt.add_resource(ws.workshop_id, FakeRequest(
            {"name": "  ", "path": "/p"}))))
        assert status == 400 and "名称" in out["error"]

    def test_add_empty_path_400(self, ws):
        status, out = unpack(run(wt.add_resource(ws.workshop_id, FakeRequest(
            {"name": "产出", "path": ""}))))
        assert status == 400 and "路径" in out["error"]

    def test_add_invalid_kind_normalized_to_file(self, ws):
        """非法 kind 是**归一化**为 file，不是报错（与节点状态端点语义不同）。"""
        res = _add_resource(ws, kind="banana")
        assert res["kind"] == "file"

    def test_add_accepts_three_legit_kinds(self, ws):
        for k in ("file", "dir", "link"):
            assert _add_resource(ws, name=f"r-{k}", path=f"/p/{k}", kind=k)["kind"] == k

    def test_add_unknown_node_id_400(self, ws):
        status, out = unpack(run(wt.add_resource(ws.workshop_id, FakeRequest(
            {"name": "产出", "path": "/p", "node_id": "t_nope"}))))
        assert status == 400 and "节点不存在" in out["error"]

    def test_add_with_node_links_both_ways(self, ws):
        node = _add_node(ws, label="阶段一")
        res = _add_resource(ws, name="设计稿", path="/p/d.md", node_id=node["node_id"])
        assert res["task_node"] == node["node_id"]
        stored = next(n for n in ws.task_tree if n["node_id"] == node["node_id"])
        assert res["rid"] in stored["resources"], "节点侧也要记 rid（双向关联）"

    def test_add_returns_row_contract(self, ws):
        res = _add_resource(ws, name="产出.md", path="/p/out.md",
                            note="备注", uploader="组长")
        for f in ("rid", "name", "kind", "path", "note", "uploader",
                  "task_node", "created_at"):
            assert f in res, f"资源行缺字段 {f}"
        assert res["uploader"] == "组长" and res["created_at"]

    def test_remove_missing_workshop_404(self):
        status, _ = unpack(run(wt.remove_resource("w-ghost", "r1")))
        assert status == 404

    def test_remove_unknown_rid_404(self, ws):
        """删不存在的资源必须 404 —— 静默成功会让前端以为删掉了。"""
        status, out = unpack(run(wt.remove_resource(ws.workshop_id, "r-nope")))
        assert status == 404 and "不存在" in out["error"]

    def test_remove_existing(self, ws):
        res = _add_resource(ws)
        status, out = unpack(run(wt.remove_resource(ws.workshop_id, res["rid"])))
        assert status == 200 and out["success"] is True
        assert all(r.get("rid") != res["rid"] for r in ws.resources)


# ══════════════════════════════════════════════════════════════════
# 任务树
# ══════════════════════════════════════════════════════════════════
class TestTaskTree:
    def test_get_missing_workshop_404(self):
        status, _ = unpack(run(wt.get_task_tree("w-ghost")))
        assert status == 404

    def test_get_returns_tree(self, ws):
        _add_node(ws, label="阶段一")
        out = run(wt.get_task_tree(ws.workshop_id))
        assert out["success"] is True and out["workshop_id"] == ws.workshop_id
        assert isinstance(out["tree"], list) and out["tree"]

    def test_create_missing_workshop_404(self):
        status, _ = unpack(run(wt.create_tree_node("w-ghost", FakeRequest({"label": "x"}))))
        assert status == 404

    def test_create_empty_label_400(self, ws):
        status, out = unpack(run(wt.create_tree_node(
            ws.workshop_id, FakeRequest({"label": "   "}))))
        assert status == 400 and "名称" in out["error"]

    def test_create_unknown_parent_400(self, ws):
        status, out = unpack(run(wt.create_tree_node(
            ws.workshop_id, FakeRequest({"label": "子", "parent_id": "t_nope"}))))
        assert status == 400 and "父节点" in out["error"]

    def test_create_with_valid_parent(self, ws):
        parent = _add_node(ws, label="父")
        child = _add_node(ws, label="子", parent_id=parent["node_id"])
        assert child["parent_id"] == parent["node_id"]

    def test_create_invalid_kind_normalized(self, ws):
        assert _add_node(ws, kind="weird")["kind"] == "task"

    def test_create_invalid_status_normalized(self, ws):
        """建节点时非法 status **被归一化**为 active。"""
        assert _add_node(ws, status="whatever")["status"] == "active"

    def test_create_accepts_all_documented_kinds(self, ws):
        for k in ("task", "correction", "phase"):
            assert _add_node(ws, label=f"n-{k}", kind=k)["kind"] == k

    def test_create_node_row_contract(self, ws):
        node = _add_node(ws, label="节点")
        for f in ("node_id", "label", "parent_id", "kind", "status",
                  "note", "resources", "created_at"):
            assert f in node, f"节点缺字段 {f}"
        assert node["resources"] == []

    def test_update_missing_workshop_404(self):
        status, _ = unpack(run(wt.update_tree_node_status(
            "w-ghost", "t1", FakeRequest({"status": "done"}))))
        assert status == 404

    def test_update_unknown_node_404(self, ws):
        status, out = unpack(run(wt.update_tree_node_status(
            ws.workshop_id, "t_nope", FakeRequest({"status": "done"}))))
        assert status == 404 and "节点" in out["error"]

    def test_update_invalid_status_rejected_not_normalized(self, ws):
        """⚠️ 关键差异：**更新**端点对非法 status 是**拒绝 400**，
        而**创建**端点是静默归一化为 active。两者语义故意不同，别统一。"""
        node = _add_node(ws)
        status, out = unpack(run(wt.update_tree_node_status(
            ws.workshop_id, node["node_id"], FakeRequest({"status": "banana"}))))
        assert status == 400
        assert "非法状态" in out["error"]
        assert next(n for n in ws.task_tree
                    if n["node_id"] == node["node_id"])["status"] == "active", \
            "被拒绝时不得改动原状态"

    def test_update_missing_status_rejected(self, ws):
        node = _add_node(ws)
        status, _ = unpack(run(wt.update_tree_node_status(
            ws.workshop_id, node["node_id"], FakeRequest({}))))
        assert status == 400

    @pytest.mark.parametrize("target", VALID_STATUSES)
    def test_update_accepts_all_valid_statuses(self, ws, target):
        node = _add_node(ws)
        status, out = unpack(run(wt.update_tree_node_status(
            ws.workshop_id, node["node_id"], FakeRequest({"status": target}))))
        assert status == 200 and out["node"]["status"] == target


# ══════════════════════════════════════════════════════════════════
# 资源挂载到节点
# ══════════════════════════════════════════════════════════════════
class TestAttachResource:
    def test_missing_workshop_404(self):
        status, _ = unpack(run(wt.attach_resource_to_node(
            "w-ghost", "t1", FakeRequest({"rid": "r1"}))))
        assert status == 404

    def test_unknown_node_404(self, ws):
        res = _add_resource(ws)
        status, out = unpack(run(wt.attach_resource_to_node(
            ws.workshop_id, "t_nope", FakeRequest({"rid": res["rid"]}))))
        assert status == 404 and "节点" in out["error"]

    def test_unknown_rid_400(self, ws):
        node = _add_node(ws)
        status, out = unpack(run(wt.attach_resource_to_node(
            ws.workshop_id, node["node_id"], FakeRequest({"rid": "r-nope"}))))
        assert status == 400 and "资源" in out["error"]

    def test_attaches_bidirectionally(self, ws):
        node = _add_node(ws)
        res = _add_resource(ws)
        status, out = unpack(run(wt.attach_resource_to_node(
            ws.workshop_id, node["node_id"], FakeRequest({"rid": res["rid"]}))))
        assert status == 200
        assert res["rid"] in out["node"]["resources"]
        assert out["resource"]["task_node"] == node["node_id"], "资源侧也要回写节点"

    def test_attach_is_idempotent(self, ws):
        """重复挂载不得产生重复 rid —— 否则任务树渲染会出重影。"""
        node = _add_node(ws)
        res = _add_resource(ws)
        for _ in range(3):
            run(wt.attach_resource_to_node(
                ws.workshop_id, node["node_id"], FakeRequest({"rid": res["rid"]})))
        stored = next(n for n in ws.task_tree if n["node_id"] == node["node_id"])
        assert stored["resources"].count(res["rid"]) == 1
