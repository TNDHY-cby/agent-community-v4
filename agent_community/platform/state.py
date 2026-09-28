"""platform/state：跨模块共享可变状态（V-9 单体拆分后统一存放）。

背景：官方入口 `python -m agent_community.platform.server` 启动时，server.py 会以
`agent_community.platform.server`（经 platform/__init__.py 的 from .server import）
与 `__main__`（runpy）两个名字各执行一遍，构成模块双副本。可变状态若留在
server 模块内会各自独立，导致 routers 读到的与服务器主体维护的状态分裂
（历史教训：--token 不生效即双副本 ALLOWED_TOKENS 分裂）。

集中到本单例模块后，所有副本与 routers 均引用同一组对象，彻底消除分裂。

类型注解保持宽松（dict/list），运行时仅作容器使用；具体元素类型以
platform/protocol.py、platform/workshop.py 中的模型为准。
"""
from __future__ import annotations

from .interject_store import InterjectStore
from .task_state_machine import TaskStateMachine

# ── V-5 核心运行时状态（原 server.py L377-387）───────────────────
# tasks: dict[str, Task] / discussion_rooms: dict[str, DiscussionRoom]
# agents: dict[str, AgentCard] / assistant_history: list[dict]
tasks: dict = {}
discussion_rooms: dict = {}
agents: dict = {}
assistant_history: list = []
assistant_history_max: int = 60

# ── 工作间（V5 最小竖切，原 server.py L4546-4549）────────────────
# workshops: dict[str, Workshop]
workshops: dict = {}
interject_store = InterjectStore()
task_state_machine = TaskStateMachine()

# 待激活队列：harness_id → [激活任务]，由各 harness 桥轮询领取
pending_activations: dict = {}
# 待执行任务队列：harness_id → [工作任务]，由各 harness 桥轮询领取
pending_tasks: dict = {}
# 待测试桥队列：harness_id → [桥测试任务]，由各 harness 桥轮询领取
pending_bridge_tests: dict = {}
# ── V-17 桥测试回报归属校验（2026-09-20）──────────────────────────
# 平台发出但尚未回报的桥测试：harness_id → {"test_id","sent_at","expire_ts","reported"}
_bridge_tests_inflight: dict = {}
# ── V-16 唤醒举手回报归属校验（2026-09-20）────────────────────────
# 正在进行的唤醒流程：task_id → {"hids": set[str], "expire_ts": float}
_wakeup_inflight: dict = {}
_WAKEUP_TTL: float = 1800.0        # 唤醒流程有效期（秒）
_BRIDGE_TEST_TTL: float = 600.0    # 桥测试回报有效期（秒）
# ── R2 harness 掉线回收（2026-09-08）──────────────────────────────
# 掉线补派队列：harness_id → [(kind, payload)]；harness 心跳恢复 ONLINE 后由恢复循环回填原 pending 队列
_offline_redispatch: dict = {}
# 幂等防抖：harness_id → 上次"组长补激活"处理时间（限频，避免每 20s 重复唤醒同一掉线组长）
_r2_handled_at: dict = {}
_R2_HANDLE_GAP: float = 300.0        # 组长补激活限频（秒）
# 待探测的初步注册：harness_id → 初步声明（阶段1 pre-register 写入，阶段2 probe-register 消费）
pending_pre_register: dict = {}
