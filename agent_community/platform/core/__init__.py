"""platform/core：平台共享核心层（V-9 单体拆分的第一步）。

server.py 单体拆分策略：
1. 纯函数/无状态工具 → core/（本包，安全、持久化、通用工具）
2. 端点组 → routers/（按域拆分为独立 APIRouter，server.py 仅 include_router）
3. 共享可变状态（harness_manager 等）保持单例，由端点模块延迟引用，避免循环导入
"""
