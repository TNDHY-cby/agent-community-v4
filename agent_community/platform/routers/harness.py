"""platform/routers/harness：Harness 端点组聚合（V-13 拆分后）。

原 harness.py（1,316 行 / 66KB / 31 端点）按域拆为四个子模块：

    harness_register.py    注册与生命周期（pre/probe/register/launch/heartbeat/DELETE）
    harness_messaging.py   消息与任务回报（message/task-result/pending-*/auto-connect）
    harness_bridge.py      桥与激活（bridge-test/generate/path/verify/prefill/activate/peer-route）
    harness_common.py      共享辅助 + /ws/harness WebSocket

拆分依据：拆分前实测「无跨组调用」——三个业务组只依赖 harness_common。
本文件只负责把四个子 router 聚合成一个，server.py 的
`from .routers.harness import router as _harness_router` 保持不变。
"""
from fastapi import APIRouter

from .harness_register import router as _register_router
from .harness_messaging import router as _messaging_router
from .harness_bridge import router as _bridge_router
from .harness_common import router as _common_router

router = APIRouter()

# 扁平展开，而非 include_router：FastAPI 的 include_router 在 router 层生成
# _IncludedRouter 懒包含对象，router.routes 里看不到真实路径 —— 会让
# test_routers_smoke 与任何路由自省（含 app.routes）误判为「没有 harness 路由」。
# 直接 extend 子 router 的 route 对象，等价于拆分前逐条注册，HTTP 行为与自省均不变。
for _sub in (_register_router, _messaging_router, _bridge_router, _common_router):
    router.routes.extend(_sub.routes)
