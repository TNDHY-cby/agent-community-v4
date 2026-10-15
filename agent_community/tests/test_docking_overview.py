# -*- coding: utf-8 -*-
"""对接中心总览端点（`V34` P1）：`GET /api/docking/overview` 的守卫。

判据（`V34` §4.1 / §9）：
  ① **补上那个洞**：能看到"这个 harness 接在哪个工作间、哪个岗位"（现在没有任何端点能回答）；
  ② **只读、无副作用**：不触发激活、不改成员/通道状态（对齐 V17 教训）；
  ③ **不新建存储**：绑定关系由工作间成员表反查，不与真实状态打架；
  ④ **单间范围真的收窄**（总控/单间是同一份实现，靠 `ws` 参数分范围）；
  ⑤ **悬空引用要说话**：成员引用了已注销/不存在的 harness ⇒ 必须报出来，不能界面显示"接上了"却找不到人；
  ⑥ **不造第二个词**（规则 20）：harness 的接入事实一律取 `tier_guidance`，**不许输出 `access_tier`**
     —— 它已被裁定为"只写不读、界面话术一律不引用"。
"""
from __future__ import annotations

import copy
import os
import sys
import types

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

from agent_community.platform import tier_guidance as tg  # noqa: E402
from agent_community.platform.routers import docking  # noqa: E402


def _m(mid, hid, *, role="组员", state="active", seq=1, status="working",
       name="", display="示例Harness-A"):
    return types.SimpleNamespace(
        member_id=mid, role=role, display_name=display, harness_ids=[hid],
        status=status, channel_state=state, channel_seq=seq, channel_since=1728000000.0,
        session_aliases=[name] if name else [], session_ref={})


def _ws(wid, name, members):
    return types.SimpleNamespace(workshop_id=wid, name=name, status="running", members=members)


def _session(hid, *, wm="http_api", status="online", name=None):
    return {"harness_id": hid, "harness_name": name or ("harness-" + hid),
            "harness_type": "generic", "status": status, "transport": "http",
            "registered_at": "2026-10-15T00:00:00+08:00",
            "wakeup": {"wakeup_method": wm}}


def _fixture():
    wmap = {
        "ws1": _ws("ws1", "合作间一", [
            _m("m0", "H-1", role="组长", state="active", name="ws1-项目-组长-m0"),
            _m("m1", "H-2", role="组员", state="pending", status="idle"),
        ]),
        "ws2": _ws("ws2", "合作间二", [
            _m("m0", "H-2", role="组员", state="active", seq=2, name="ws2-项目-组员-m0"),
            _m("m1", "H-9", role="组员", state="retired", status="offline"),   # H-9 未注册 ⇒ 悬空
        ]),
    }
    sessions = [_session("H-1", wm="acp"), _session("H-2", wm="clipboard", status="offline")]
    return wmap, sessions


# ── ① 补上那个洞 + ③ 由成员表反查 ────────────────────────────────────────────
def test_global_overview_answers_who_is_docked_where():
    wmap, sessions = _fixture()
    ov = docking.build_overview(None, workshop_map=wmap, sessions=sessions, generated_at="T")
    assert ov["scope"] == {"type": "global", "ws": None}
    by_id = {h["harness_id"]: h for h in ov["harnesses"]}
    assert set(by_id) == {"H-1", "H-2"}
    # H-1 只接在 ws1/组长；H-2 接在两个间（ws1 未接上、ws2 已接上）
    b1 = by_id["H-1"]["bindings"]
    assert [(b["ws_id"], b["role"], b["channel_state"]) for b in b1] == [("ws1", "组长", "active")]
    assert by_id["H-1"]["docked_count"] == 1
    b2 = by_id["H-2"]["bindings"]
    assert sorted((b["ws_id"], b["channel_state"]) for b in b2) == [("ws1", "pending"), ("ws2", "active")]
    assert by_id["H-2"]["docked_count"] == 1
    # 会话名要带出来（界面就靠它找那个会话）
    assert b1[0]["session_name"] == "ws1-项目-组长-m0"
    assert b2[0]["channel_seq"] in (1, 2)


def test_workshop_rows_give_four_separate_counts():
    wmap, sessions = _fixture()
    ov = docking.build_overview(None, workshop_map=wmap, sessions=sessions, generated_at="T")
    rows = {r["ws_id"]: r for r in ov["workshops"]}
    assert rows["ws1"]["member_count"] == 2 and rows["ws1"]["docked"] == 1 and rows["ws1"]["pending"] == 1
    assert rows["ws2"]["retired"] == 1 and rows["ws2"]["offline"] == 1
    assert rows["ws2"]["docked"] == 1


# ── ④ 单间范围真的收窄 ───────────────────────────────────────────────────────
def test_workshop_scope_narrows_harnesses_and_bindings():
    wmap, sessions = _fixture()
    ov = docking.build_overview("ws2", workshop_map=wmap, sessions=sessions, generated_at="T")
    assert ov["scope"] == {"type": "workshop", "ws": "ws2"}
    assert [r["ws_id"] for r in ov["workshops"]] == ["ws2"]
    # H-1 与 ws2 无关 ⇒ 单间视角**不应**出现（否则"单间"名不副实）
    assert [h["harness_id"] for h in ov["harnesses"]] == ["H-2"]
    assert all(b["ws_id"] == "ws2" for b in ov["harnesses"][0]["bindings"])


def test_unknown_workshop_id_matches_nothing_not_everything():
    """精确比 id：给个不存在/前缀相同的 id，**绝不能**退化成全局。"""
    wmap, sessions = _fixture()
    ov = docking.build_overview("ws", workshop_map=wmap, sessions=sessions, generated_at="T")
    assert ov["workshops"] == [] and ov["harnesses"] == []


# ── ⑤ 悬空引用要说话 ────────────────────────────────────────────────────────
def test_dangling_binding_is_reported():
    wmap, sessions = _fixture()
    ov = docking.build_overview(None, workshop_map=wmap, sessions=sessions, generated_at="T")
    assert [d["harness_id"] for d in ov["dangling_bindings"]] == ["H-9"], ov["dangling_bindings"]
    b = ov["dangling_bindings"][0]["bindings"][0]
    assert b["ws_id"] == "ws2" and b["channel_state"] == "retired"


# ── ② 只读：不改数据、可重复 ────────────────────────────────────────────────
def test_overview_is_side_effect_free():
    wmap, sessions = _fixture()
    before = copy.deepcopy({k: vars(v) for k, v in wmap.items()})
    a = docking.build_overview(None, workshop_map=wmap, sessions=sessions, generated_at="T")
    b = docking.build_overview(None, workshop_map=wmap, sessions=sessions, generated_at="T")
    after = copy.deepcopy({k: vars(v) for k, v in wmap.items()})
    assert a == b, "同一输入两次结果不一致 ⇒ 有隐藏状态"
    assert before == after, "总览改了工作间数据（只读端点不许有副作用）"


# ── ⑥ 一处真相：接入事实取自 tier_guidance，且不许出现 access_tier ────────────
def test_interface_facts_come_from_tier_guidance():
    wmap, sessions = _fixture()
    ov = docking.build_overview(None, workshop_map=wmap, sessions=sessions, generated_at="T")
    for h in ov["harnesses"]:
        shim = types.SimpleNamespace(wakeup_method=h["wakeup_method"])
        assert h["interface_class"] == tg.interface_class(shim)
        assert h["interface_class_label"] == tg.CLASS_LABEL[tg.interface_class(shim)]
        assert h["can_be_woken_by_platform"] == tg.can_be_woken_by_platform(shim)
    by_id = {h["harness_id"]: h for h in ov["harnesses"]}
    assert by_id["H-1"]["interface_class"] == "has_command"
    assert by_id["H-2"]["interface_class"] == "manual_only"
    assert by_id["H-2"]["can_be_woken_by_platform"] is False


def test_payload_never_exposes_the_retired_access_tier_field():
    """★规则 20：`access_tier` 已被裁定"只写不读、界面话术一律不引用" —— 新端点不许把它漏出去。
    （`V34` §4.1 的形状提案里写过它，那是 2026-10-10 的旧写法；以现行裁定为准。）"""
    import json
    wmap, sessions = _fixture()
    ov = docking.build_overview(None, workshop_map=wmap, sessions=sessions, generated_at="T")
    blob = json.dumps(ov, ensure_ascii=False)
    assert "access_tier" not in blob, "把已退休的字段名漏给了界面（会造出第三份口径）"


# ── 端点接线：真的挂进 app 了（聚合器改了但没 include 是典型静默失败）─────────
def test_endpoint_is_mounted_as_get():
    from agent_community.platform.server import app
    paths = {}
    for r in app.routes:
        if type(r).__name__ == "_IncludedRouter":
            for sub in getattr(getattr(r, "original_router", None), "routes", []):
                paths.setdefault(getattr(sub, "path", ""), set()).update(getattr(sub, "methods", set()) or set())
        else:
            paths.setdefault(getattr(r, "path", ""), set()).update(getattr(r, "methods", set()) or set())
    assert "/api/docking/overview" in paths, "端点没挂进 app（界面会 404）"
    assert "GET" in paths["/api/docking/overview"]


def test_source_has_no_write_calls_in_the_readonly_path():
    """**只读的那几个函数**里不许出现 `save_state` / 激活调用（结构判据，不看文本行号）。

    用 AST 找**只读函数体内**的调用名 —— 与"扫描器要按结构写"同一条教训。

    ⚠️ 2026-10-15 收窄过一次：本模块后来**也放了写端点**（`FE-26` P1 的试投与改档案），
    原来扫"整个模块"的写法会把它一并判红。**保证不丢、范围收窄** ——
    只读函数（`_interface_facts` / `build_overview` / `docking_overview` /
    `docking_locate` / `docking_harness_get` / `match_candidates` / `patch_harness_dump`）
    仍然一个写动作都不许有。
    """
    import ast
    import pathlib
    src = pathlib.Path(docking.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned = {"save_state", "establish_session", "deliver_bridge", "onboard_members"}
    readonly_fns = {"_interface_facts", "build_overview", "docking_overview",
                    "docking_locate", "docking_harness_get",
                    "match_candidates", "patch_harness_dump"}
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in readonly_fns:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                fn = sub.func
                name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                if name in banned:
                    found.append((node.name, name, getattr(sub, "lineno", 0)))
    assert not found, "只读函数里出现了写动作：%r" % found


def test_readonly_functions_are_still_covered():
    """反面对照：上面那张只读函数表**不许变成空转**（函数改名/删了要一起改，否则守卫失效）。"""
    import ast
    import pathlib
    src = pathlib.Path(docking.__file__).read_text(encoding="utf-8")
    names = {n.name for n in ast.walk(ast.parse(src))
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for want in ("build_overview", "docking_overview", "docking_locate", "docking_harness_get"):
        assert want in names, "只读函数 %s 不在了 —— 守卫会静默空转，请同步更新列表" % want
