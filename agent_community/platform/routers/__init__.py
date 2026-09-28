"""platform/routers：按域拆分的 API 路由模块（V-9 单体拆分）。

每个模块持有一个 APIRouter，由 server.py include_router 挂载。
共享可变状态（harness_manager 等）在函数内延迟引用 server 单例，避免循环导入。
"""
