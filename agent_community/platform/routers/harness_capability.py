# -*- coding: utf-8 -*-
"""platform/routers/harness_capability：**能力与证据来源**（只读，V73 §9 落地）。

`GET /api/harness/{harness_id}/capability`

返回三段状态（桥配置 / harness 在跑桥 / agent 能回话）＋ 能力清单（逐条标 `declared` / `probed` /
`verified` / `failed`）。**只读、无副作用**：不探活、不写记录、不发任何外网请求
（`probed` 那部分读的是登记时落下的 `metadata.probe_hints`）。

为什么单独放一个端点而不是塞进 `/api/docking/harness/{id}`：那个端点是**档案读写**契约
（`FE-26` §2b，含可编辑字段白名单），而这里是**判读视图**——混在一起会让"档案"和"结论"两件事
互相牵连（规则 20：一件事一处口径）。
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/harness/{harness_id}/capability")
async def harness_capability(harness_id: str):
    """这个 harness 有什么能力、凭什么这么说（只读）。"""
    from ..server import Utf8JSONResponse
    from ..harness_adapter import harness_manager
    from .. import harness_capability as hc

    sess = harness_manager.sessions.get(harness_id)
    if sess is None:
        return Utf8JSONResponse(
            {"error": "没有这个 harness（可能已注销或 id 写错）",
             "how_to_fix": "先在对接中心登记；或 GET /api/harness/list 看现有 id"},
            status_code=404)
    try:
        dump = sess.info.model_dump()
    except Exception as e:                                    # noqa: BLE001
        return Utf8JSONResponse({"error": "读档案失败：%s: %s" % (type(e).__name__, e)},
                                status_code=500)
    return hc.build_provenance(dump)
