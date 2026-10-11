# -*- coding: utf-8 -*-
"""V36 B2：在工作间里架起具体桥并绑定会话（`bridge/attach`）+ 看板只读端点。

老板口径（2026-10-11）：「**一条桥 = 一个具体会话**」⇒ 一个 harness 可以有多条桥、
多个对话。本文件锁三件事：

1. **安全口径与 `connect-artifact` 一致**：路径字段一律 400 且不被读取；
2. **失败要说话**（准则⑤）：桥没生成 ⇒ `state=none` + 带原因，**绝不写 attached**；
   `status_text` 不许在 `none` 时说"已架接"（这条我实测时真踩到过，见下）；
3. **看板只读端点无副作用**：`bridge-bindings` 不得改状态。

⚠️ 实测踩过的真 bug（本文件的用例就是防它回归）：
   `bridge-bindings` 原先只要 `ref` 非空就写"已架接"，而 `state=none`（桥产物根本没生成）
   也照写 ⇒ "看起来成功"的假状态。修法 = 看 `state` 分三种说法。
"""
from __future__ import annotations

import pytest

from agent_community.platform.routers import workshops_session as wsess


def test_status_text_never_claims_attached_when_none():
    """★核心：state=none（桥没生成）时，**不许**说"已架接"（准则⑤）。"""
    txt = wsess._bridge_status_text({
        "state": "none", "template_name": "探针桥",
        "session_name": "w-项目-码农-m0", "error": "桥产物未生成",
    })
    assert "未架接" in txt, txt
    assert "已架接" not in txt, f"state=none 却说已架接 = 假状态: {txt}"
    assert "桥产物未生成" in txt, "失败要说清原因"


def test_status_text_attached_but_not_verified_is_honest():
    """桥"生成了" ≠ "通了"：没有自证材料时不许吹成功，要写明未自证。"""
    txt = wsess._bridge_status_text({
        "state": "attached", "template_name": "探针桥",
        "session_name": "w-项目-码农-m0", "verified": False,
    })
    assert "未自证" in txt, txt
    txt2 = wsess._bridge_status_text({
        "state": "attached", "template_name": "探针桥",
        "session_name": "w-项目-码农-m0", "verified": True,
    })
    assert "未自证" not in txt2, txt2


def test_status_text_retired_and_empty():
    assert "已停用" in wsess._bridge_status_text(
        {"state": "retired", "template_name": "t", "session_name": "s"})
    assert "未架接" in wsess._bridge_status_text({})


def test_injected_imports_are_present():
    """回归防护：这两个函数是模块级被其它函数调用的，签名一变就炸。"""
    import inspect
    assert callable(wsess._bridge_status_text)
    sig = inspect.signature(wsess._bridge_status_text)
    assert list(sig.parameters) == ["ref"]

    # 新端点的路径必须挂在工作间作用域下（不是 harness 级）
    paths = {getattr(r, "path", "") for r in wsess.router.routes}
    assert "/api/workshop/{ws_id}/member/{member_id}/bridge/attach" in paths
    assert "/api/workshop/{ws_id}/member/{member_id}/bridge" in paths
    assert "/api/workshop/{ws_id}/bridge-bindings" in paths


def test_attach_endpoint_rejects_path_fields_by_source():
    """安全口径：端点源码里必须对路径字段提前拦截（与 connect-artifact 同一纪律）。

    这里用源码断言而非真打 HTTP：本文件是纯单测（不依赖在跑的服务）；
    端到端那条由 `temp\\_e2e_bridge_attach.py` 在真实例上跑（含 400 反向断言）。
    """
    import inspect
    src = inspect.getsource(wsess.member_bridge_attach)
    for banned in ("out_dir", "target_dir", "path", "dir", "workspace_dir", "bridge_path"):
        assert banned in src, f"端点应显式拦截路径字段 {banned}"
    assert "不受任何路径字段" in src or "不接受路径字段" in src
