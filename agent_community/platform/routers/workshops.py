"""platform/routers/workshops：工作间端点组聚合（V-13 拆分后）。

原 workshops.py（1,398 行 / 70KB / 45 端点）按域拆为四个子模块：

    workshops_lifecycle.py   创建/列表/改名/pin/删除/stale/模式/决策模式
    workshops_discuss.py     三级讨论/完成/启动/插话/状态机
    workshops_assign.py      组长与成员编排
    workshops_task.py        任务树/资源/halt-and-reset
    workshops_common.py      共享辅助（_sv / _leader_* / _platform_base_url_safe）

拆分依据：拆分前实测「无跨组调用」——10 处跨组引用全部指向 common。
本文件只负责聚合，server.py 的 `from .routers.workshops import router` 保持不变。
"""
from fastapi import APIRouter

from .workshops_lifecycle import router as _lifecycle_router
from .workshops_discuss import router as _discuss_router
from .workshops_assign import router as _assign_router
from .workshops_task import router as _task_router
from .workshops_common import router as _common_router

router = APIRouter()
# 扁平展开而非 include_router：保持 router.routes / app.routes 自省可见
for _sub in (_lifecycle_router, _discuss_router, _assign_router, _task_router, _common_router):
    router.routes.extend(_sub.routes)
