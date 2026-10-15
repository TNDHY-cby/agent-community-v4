# -*- coding: utf-8 -*-
"""`FE-26` P1 守卫：**视觉激活的定位/试投 + harness 档案读写**。

判据（`FE-26` §3.3 / §5 / §8）：
  ① **定位是只读的**（只列窗口、不注入、不 save_state）；
  ② **试投放给界面按钮**（不是给 Agent 自动跑）—— 且结果**原样透传 verified 三态**；
  ③ **档案只能改声明白名单里的字段**：未知字段**直接拒绝并列出**（静默忽略＝"以为改了其实没改"）；
     `harness_id` 这类身份字段**不许顺手改掉**；
  ④ **档位事实只读**：改了 `wakeup_method`，事实必须**跟着重算**（不许"改了还显示旧档位"）；
  ⑤ **脚本存进档案**：`HarnessInfo.activation_script` 默认空、**老记录没有这个键也能读**、能往返；
  ⑥ **`find_window` 不假装找到**：非 Windows/没脚本时说清为什么并给出剪贴板兜底；
     匹配不到时给"把关键词放宽"的下一步。
"""
from __future__ import annotations

import ast
import asyncio
import inspect
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
from agent_community.platform.tools.window_tool import FindWindowTool  # noqa: E402


class _Req:
    """最小 Request 替身（端点只用到 `.json()`）。"""

    def __init__(self, body):
        self._body = body

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _fn(tool, **params):
    return asyncio.run(tool.execute(**params))


# ── ① match_candidates（纯函数）─────────────────────────────────────────
WINS = [
    {"hwnd": "1", "pid": "10", "class": "X", "title": "TraeCode 合作社 - 项目"},
    {"hwnd": "2", "pid": "20", "class": "Y", "title": "记事本"},
    {"hwnd": "3", "pid": "30", "class": "Z", "title": "合作社 ws-x"},
]


def test_match_scores_by_hit_ratio():
    got = docking.match_candidates(WINS, ["合作社", "TraeCode"])
    assert [c["title"] for c in got] == ["TraeCode 合作社 - 项目", "合作社 ws-x"]
    assert got[0]["score"] == 1.0 and got[1]["score"] == 0.5, "匹配分＝命中数/关键词数"


def test_match_is_case_insensitive_and_stable():
    low = docking.match_candidates(WINS, ["traecode"])
    assert len(low) == 1 and low[0]["title"] == "TraeCode 合作社 - 项目"
    two = docking.match_candidates(WINS, ["合作社"])
    assert [c["score"] for c in two] == [1.0, 1.0] and two[0]["title"].startswith("TraeCode"), \
        "同分按标题排，顺序必须稳定（界面与测试要对得上）"


def test_match_empty_and_no_hit():
    assert docking.match_candidates(WINS, []) == [], "没关键词就别瞎匹配"
    assert docking.match_candidates(WINS, ["根本不存在的东西"]) == []


# ── ③④ patch_harness_dump（纯函数）──────────────────────────────────────
def test_patch_rejects_unknown_and_identity():
    _, err = docking.patch_harness_dump({"harness_name": "A"}, {"model_name": "typo"})
    assert err and "model_name" in err, "未知字段必须**拒绝并列出**，不能静默忽略"
    _, err2 = docking.patch_harness_dump({"harness_id": "h1"}, {"harness_id": "h2"})
    assert err2 and "harness_id" in err2, "身份字段不许改"


def test_patch_applies_whitelisted_only():
    new, err = docking.patch_harness_dump({"harness_name": "A", "bridge_dir": "d"},
                                          {"harness_name": "B", "bridge_dir": "d2"})
    assert err == "" and new["harness_name"] == "B" and new["bridge_dir"] == "d2"


def test_editable_covers_the_four_blocks():
    """`FE-26` §2b 的四块字段必须都**可编辑**（老板要求的四块），且不含身份字段。"""
    need = ("harness_type", "harness_version", "transports", "preferred_transport",  # ① 框架性能
            "ai", "waking_models",                                                    # ② 适配模型
            "bridge_dir", "bridge_status",                                            # ③ 桥模板/桥坐标
            "wakeup_method", "wakeup_url", "wakeup_dir", "acp_command", "acp_cwd",     # ④ 激活方式
            "api_base_url", "api_message_path", "api_outbox_dir",
            "activation_script")                                                      # ★脚本本体
    missing = [k for k in need if k not in docking._HARNESS_EDITABLE]
    assert not missing, "这四块的字段必须可编辑，缺：%s" % missing
    assert "harness_id" not in docking._HARNESS_EDITABLE, "身份字段不许进白名单"


# ── ⑤ 脚本字段 ──────────────────────────────────────────────────────────
def test_activation_script_is_backward_compatible():
    base = {"harness_id": "x", "harness_name": "y", "ai": {"model_name": "m"}}
    assert HarnessInfo(**base).activation_script == {}, "老记录没有这个键 ⇒ 读出来必须是空"
    full = HarnessInfo(**base, activation_script={"text": "脚本"}, )
    assert HarnessInfo(**full.model_dump()).activation_script == {"text": "脚本"}, "要能往返"


# ── ⑥ find_window 工具 ─────────────────────────────────────────────────
def test_find_window_schema():
    s = FindWindowTool().schema
    assert s.name == "find_window"
    assert "keywords" in s.parameters["properties"]
    assert s.parameters["required"] == ["keywords"]


def test_find_window_needs_keywords():
    r = _fn(FindWindowTool(), keywords=[])
    assert r.success is False and "keywords" in r.content


def test_find_window_says_unsupported_honestly(monkeypatch):
    monkeypatch.setattr(visual_launch, "list_windows",
                        lambda *a, **k: {"ok": False, "supported": False,
                                         "detail": "非 Windows：消息式界面注入只在 Windows 上存在",
                                         "windows": []})
    r = _fn(FindWindowTool(), keywords=["TraeCode"])
    assert r.success is False
    assert "剪贴板" in r.content, "不支持时要给出**能走下去的那条路**（剪贴板兜底）"


def test_find_window_no_match_gives_next_step(monkeypatch):
    monkeypatch.setattr(visual_launch, "list_windows",
                        lambda *a, **k: {"ok": True, "supported": True, "detail": "共 2 个",
                                         "windows": WINS})
    r = _fn(FindWindowTool(), keywords=["查无此窗"])
    assert r.success is False and "一个窗口都没匹配上" in r.content
    assert "换宽" in r.content or "打开" in r.content, "要给人下一步，而不是只说失败"


def test_find_window_matches_and_reports(monkeypatch):
    monkeypatch.setattr(visual_launch, "list_windows",
                        lambda *a, **k: {"ok": True, "supported": True, "detail": "共 3 个",
                                         "windows": WINS})
    r = _fn(FindWindowTool(), keywords=["合作社"])
    assert r.success is True
    assert "TraeCode 合作社 - 项目" in r.content and "记事本" not in r.content
    assert "只负责定位" in r.content, "要讲清它只定位、不注入（边界）"


# ── ① 定位端点：只读 ───────────────────────────────────────────────────
def test_locate_endpoint_is_readonly_by_ast():
    """**结构判据**：`docking_locate` 里**不许出现** `save_state`（只读端点别偷偷改状态）。"""
    src = inspect.getsource(docking.docking_locate)
    tree = ast.parse(src.strip().replace("async def docking_locate", "def docking_locate", 1)
                     if src.strip().startswith("async def") else src)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "save_state" not in names and "save_state" not in attrs, \
        "定位是只读的，不许 save_state"


def test_locate_endpoint_needs_keywords():
    from agent_community.platform.server import Utf8JSONResponse
    res = asyncio.run(docking.docking_locate(_Req({})))
    assert isinstance(res, Utf8JSONResponse) and res.status_code == 400
    assert "keywords" in res.body.decode("utf-8")


def test_locate_endpoint_returns_candidates(monkeypatch):
    monkeypatch.setattr(visual_launch, "list_windows",
                        lambda *a, **k: {"ok": True, "supported": True, "detail": "共 3 个",
                                         "windows": WINS})
    out = asyncio.run(docking.docking_locate(_Req({"keywords": ["合作社"]})))
    assert out["total_windows"] == 3 and len(out["candidates"]) == 2
    assert out["supported"] is True and out["checked_at"]


# ── ② 试投端点：原样透传三态，且**不保存** ─────────────────────────────
def test_try_activate_passes_through_tristate(monkeypatch):
    seen = {}

    def _fake_launch(member, text, *, method="auto", session_name=None, verify=True):
        seen.update(member=member, text=text, method=method, name=session_name, verify=verify)
        return {"ok": False, "method": "wmpaste", "verified": False, "need_human": False,
                "detail": "读回不匹配：文本没落进去", "next_step": "确认那个窗口有没有输入框"}

    monkeypatch.setattr(visual_launch, "launch", _fake_launch)
    out = asyncio.run(docking.docking_try_activate(_Req({"title": "TraeCode 合作社 - 项目"})))
    assert out["verified"] is False and out["ok"] is False, "三态必须原样透传（不许美化）"
    assert out["key"] == "TraeCode 合作社 - 项目" and seen["name"] == out["key"]
    assert seen["member"] is None and seen["verify"] is True, "试投默认带读回核对"


def test_try_activate_pins_the_method_so_fallback_cannot_fake_success(monkeypatch):
    """★**这条是走查抓到的假成功换来的**（准则⑤）。

    背景实测：`launch(method="auto")` 在 wmpaste 失败后**自动退到剪贴板**，剪贴板**总是成功**
    ⇒ 把文本投给**不存在的窗口**时 `launch()` 返回 `ok=True`；包装层照抄 `ok` 就会显示"试投成功"。
    ⇒ 试投必须**钉住方法**（不 auto），且钉住时 `ok` 才是"这条路真的通了"。
    """
    seen = {}

    def _fake_launch(member, text, *, method="auto", session_name=None, verify=True):
        seen["method"] = method
        return {"ok": True, "method": method, "verified": None, "need_human": True,
                "detail": "已放进剪贴板", "next_step": "去粘贴"}

    monkeypatch.setattr(visual_launch, "launch", _fake_launch)
    out = asyncio.run(docking.docking_try_activate(_Req({"title": "随便", "method": "auto"})))
    assert seen["method"] in ("wmpaste", "clipboard"), "auto 必须被翻译成**具体方法**再传下去"
    assert out["method"] == seen["method"] and out["method"] != "auto", \
        "返回里要写清**实际用的哪个方法**（不然界面没法解释）"


def test_try_activate_needs_a_target():
    from agent_community.platform.server import Utf8JSONResponse
    res = asyncio.run(docking.docking_try_activate(_Req({})))
    assert isinstance(res, Utf8JSONResponse) and res.status_code == 400
    assert "要投给哪个窗口" in res.body.decode("utf-8")


# ── ④ 档案读写端点 ─────────────────────────────────────────────────────
def _fake_session(hid="H-1", wm="clipboard"):
    info = HarnessInfo(harness_id=hid, harness_name="示例Harness-A", ai=HarnessAI(model_name="m"),
                       wakeup_method=wm)
    return types.SimpleNamespace(info=info)


def test_harness_get_reports_facts_and_editable(monkeypatch):
    monkeypatch.setattr(docking, "_find_harness", lambda h: _fake_session(h))
    out = asyncio.run(docking.docking_harness_get("H-1"))
    assert out["harness_id"] == "H-1" and out["archive"]["harness_name"] == "示例Harness-A"
    assert "interface_class" in out["facts"], "档位事实要一起给（界面据此回显）"
    assert "activation_script" in out["editable"] and "readonly_reason" in out


def test_harness_get_404_speaks():
    from agent_community.platform.server import Utf8JSONResponse
    import agent_community.platform.routers.docking as d

    orig = d._find_harness
    d._find_harness = lambda h: None
    try:
        res = asyncio.run(d.docking_harness_get("没这个"))
        assert isinstance(res, Utf8JSONResponse) and res.status_code == 404
        assert "没有这个 harness" in res.body.decode("utf-8")
    finally:
        d._find_harness = orig


def test_harness_update_recomputes_facts(monkeypatch):
    """改了 `wakeup_method` ⇒ 档位事实必须**跟着变**（不许"改了还显示旧档位"）。"""
    sess = _fake_session(wm="clipboard")
    monkeypatch.setattr(docking, "_find_harness", lambda h: sess)
    monkeypatch.setattr(docking, "save_state", lambda *a, **k: None, raising=False)
    before = asyncio.run(docking.docking_harness_get("H-1"))["facts"]["interface_class"]
    out = asyncio.run(docking.docking_harness_update(
        "H-1", _Req({"wakeup_method": "http_api", "activation_script": {"text": "x"}})))
    assert out["ok"] is True
    assert out["archive"]["activation_script"] == {"text": "x"}, "脚本要真的存进档案"
    assert out["facts"]["interface_class"] == asyncio.run(
        docking.docking_harness_get("H-1"))["facts"]["interface_class"]
    assert out["facts"]["interface_class"] != before, "换了唤醒方式，档位事实必须变"


def test_harness_update_rejects_bad_patch(monkeypatch):
    from agent_community.platform.server import Utf8JSONResponse
    monkeypatch.setattr(docking, "_find_harness", lambda h: _fake_session(h))
    res = asyncio.run(docking.docking_harness_update("H-1", _Req({"harness_id": "hack"})))
    assert isinstance(res, Utf8JSONResponse) and res.status_code == 400
    assert "不允许在这里改" in res.body.decode("utf-8")
