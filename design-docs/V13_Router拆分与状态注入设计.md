# V-13 技术债清偿设计：Router 二次拆分 + 全局状态依赖注入

> 状态：**待拍板**（本稿只做设计，未写实现代码）
> 日期：2026-10-03
> 依据：外部代码审阅意见 + 源码实测核验
> 原则：纯重构、零行为变更、可分步回退、每步回归全绿

---

## 一、审阅意见核验（先纠正再动手）

外部审阅提出两个方向：拆 `harness.py`、改造 `.state` 依赖注入。**方向正确，但事实基础需修正**：

| 审阅论断 | 实测 | 结论 |
|---|---|---|
| `harness.py` 65,294B 过大 | 66,610 B / 1,316 行 / **31 端点** | ✅ 准确 |
| （未提） | **`workshops.py` 70,259 B / 1,398 行 / 45 端点** | ❌ **审阅漏判，这才是最大块** |
| 测试"自包含、不依赖预启服务" | `security_regression.py` **依赖 18920 已起服务**；仅 `test_plugins.py` 自带起服务 | ❌ 不准确 |
| 193 项测试 | 源自 CHANGELOG V-18 历史；当前实测 78 用例函数 / 139 项通过 | ⚠️ 过期数字 |
| `.state` "广泛导入" | 仅 **5 条 import、4 个生产文件** | ⚠️ **数量不广**，归因有误 |

### `.state` 的真问题（归因修正）

不是 import 语句多，而是两条：

1. **单条语句拖拽大量可变符号**（如 `harness.py` 一行 import 9 个符号），调用方与符号表紧耦合
2. **状态本体是全局单例**（`state.py` 模块级 `tasks = {}` 等），导致：
   - 单元测试**无法隔离**（改一个测试污染另一个）
   - **无法多 Worker**（每进程一份状态，不共享）
   - 理解成本高（谁改了 `tasks` 无法静态追踪）

**注**：`state.py` 的全局设计是**故意的**——它解决的是 `platform/__init__.py` 导致的模块双副本分裂（`__main__` 与 `platform.server` 各持一份状态）。**改造必须保留这个语义**，否则会把已修好的 bug 重新引入。

---

## 二、目标与非目标

### 目标
1. **Router 二次拆分**：把 70KB/45 端点与 66KB/31 端点拆到"单文件 ≤ 25KB / ≤ 15 端点"粒度
2. **状态访问收口**：把"直接 import 9 个符号"变成"通过一个显式状态对象访问"，让依赖可见、可替换、可测试
3. **零行为变更**：所有 139 项回归全绿，API 契约完全不变

### 非目标
- 不做多进程/多 Worker 部署（只消除障碍，不实现）
- 不改 `state.py` 的双副本语义（那是修好的 bug）
- 不引入新的第三方依赖

---

## 三、方案 A：Router 二次拆分

### 3.1 `workshops.py`（70KB / 45 端点 → 按生命周期阶段拆 4 个）

| 新文件 | 承载端点 | 估行数 |
|---|---|---|
| `workshops_lifecycle.py` | 创建 / 列表 / 重命名 / pin / 删除 / stale / recycle | ~350 |
| `workshops_discuss.py` | discuss / review / continue / complete / start / 插话 6 端点 | ~450 |
| `workshops_assign.py` | select-members / confirm-members / leader-status / 成员名单 | ~350 |
| `workshops_task.py` | 任务树 4 端点 / resource / halt-and-reset / state-machine(sm/resume/drop/status) | ~350 |

### 3.2 `harness.py`（66KB / 31 端点 → 按域拆 4 个）

| 新文件 | 承载端点 | 估行数 |
|---|---|---|
| `harness_register.py` | pre-register / probe-register / register / launch / launch-log / heartbeat / delete | ~400 |
| `harness_messaging.py` | message / task-result / pending-tasks / pending-activations / activation-result / api-probe / api-message / **auto-connect** | ~450 |
| `harness_bridge.py` | bridge-generate / bridge-path / bridge-test(×2) / bridge-templates / bridge-verify / prefill / peer-route / activate | ~450 |
| `harness_ws.py` | `/ws/harness/{id}` WebSocket | ~60 |

### 3.3 拆分纪律（防翻车）

- **每次只拆一个文件**，拆完跑全量回归，绿了再拆下一个
- **路由路径、参数、响应体一个字不改**（纯搬迁）
- 每次提交前留 `.bak_<日期>`（铁律 #2）
- 用 `git mv` 语义保留历史

### 3.4 风险

| 风险 | 缓解 |
|---|---|
| 跨文件的私有辅助函数 | 拆分时把 `_xxx()` 辅助函数跟着主调用方走，避免反向依赖 |
| `from ..server import` 惰性导入断裂 | 保持原样（这些是函数体内延迟导入，与文件位置无关） |
| 端点重复注册 | 每拆一个文件立即跑 `GET /api/status` + 全量回归 |

---

## 四、方案 B：状态访问收口（`.state` 依赖注入）

### 4.1 核心思路：**包装，不搬运**

`state.py` 的双副本语义必须保留（它修过 `--token` 双副本分裂的 bug）。所以**不移动状态对象**，只改访问方式：

```python
# 现在（4 个文件各拖 9 个符号）：
from ..state import tasks, agents, discussion_rooms, workshops, pending_tasks, ...

# 改为（一个入口，依赖可见）：
from ..state import S          # S 是命名空间对象
S.tasks['x'] = y               # 语义完全相同，底层仍是同一份 dict
```

**实现**：`state.py` 末尾追加一个 `class _Namespace: ...` 挂载实例 `S`，其属性**指向同一批全局对象**（`S.tasks is tasks` 恒为 `True`）。零迁移风险。

### 4.2 为什么这样最稳

| 方案 | 风险 |
|---|---|
| 改成真依赖注入（构造函数传入） | ❌ 要动全部 4 个 router 的函数签名 + FastAPI 依赖注入链，改动面巨大 |
| 移动状态对象 | ❌ 破坏双副本语义，重新引入已修 bug |
| **包装成命名空间（本方案）** | ✅ 属性引用同一批对象，**行为零变化**，只让依赖从"9 个符号"变成"1 个入口" |

### 4.3 这一步解决了什么

- ✅ 调用方依赖从 9 个符号 → 1 个入口（可读性）
- ✅ 未来可把 `S` 换成可注入实例（为多 Worker/测试隔离**铺路**，但本稿不实现）
- ✅ 零行为变更，139 项回归可验证

### 4.4 不解决什么（诚实声明）

- **不**实现多 Worker（那是部署层问题）
- **不**让测试自动隔离（需要额外的 fixture 改造，另立设计）
- 只做**减法**：降低耦合面、打开后续改造的门

---

## 五、实施顺序（分 5 步，每步独立可回退）

| 步 | 内容 | 估时 | 风险 |
|---|---|---|---|
| 1 | `harness.py` → 4 文件（纯搬迁） | 中 | 中 |
| 2 | `workshops.py` → 4 文件（纯搬迁） | 中 | 中 |
| 3 | `state.py` 加 `S` 命名空间，4 处调用改用 `S.` | 小 | 低 |
| 4 | 清理各 router 内部的死导入/重复导入 | 小 | 低 |
| 5 | 文档同步：`AGENTS.md` / 技能手册的代码地图 | 小 | 无 |

**每步纪律**：备份 → 改 → `py_compile` → 重启 18920 → 全量回归 139/139 → 提交。

---

## 六、验证矩阵

| # | 用例 | 期望 |
|---|---|---|
| 1 | `py_compile` 全部改动文件 | 通过 |
| 2 | 重启后 `GET /api/status` | 200，数据完整 |
| 3 | **端点总数不变**（拆分前后对比 132 处） | 相同 |
| 4 | `agent_community/tests` | 73 passed |
| 5 | 根 `tests/`（含 security_regression 打活服务） | 66 passed |
| 6 | 顺序反转跑根 tests | 66 passed（不引入顺序脆弱） |
| 7 | 协议冒烟 MCP/A2A | 11/11 |
| 8 | V-10 HTTP 矩阵（P1/P2 审计） | 18/18 |
| 9 | `S.tasks is tasks` 恒等断言 | True |
| 10 | 拆分后单文件行数 | ≤ 500 行 / ≤ 15 端点 |

---

## 七、对审阅意见的回应（备忘）

| 审阅建议 | 本稿处理 |
|---|---|
| 拆 `harness.py` | ✅ 方案 A，但**补上 `workshops.py`（更大）** |
| `.state` 依赖注入 | ✅ 方案 B，但**修正归因**（问题在状态本体，不在 import 数量），且**保留双副本语义** |
| 测试覆盖不足（并发/故障注入） | ⚠️ **同意，但不在本稿范围**——另立设计（并发压测 + 故障注入） |
| 错误处理一致性 | ⚠️ 同意，需单独代码审计，不在本稿 |

---

## 八、待拍板

1. **是否按 §五 的 5 步顺序执行**？（纯重构，零行为变更）
2. **方案 B 用"包装命名空间"还是你要真正的构造函数注入**？——我选前者（零风险、可分步），后者改动面大但隔离更彻底
3. **并发/故障注入测试**要不要另立项？（审阅指出的真缺口）

---

*本稿为设计文件，未写实现代码。待拍板后按 §五 分步实施，每步全量回归。*
