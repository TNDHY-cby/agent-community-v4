# 更新日志（CHANGELOG）

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [4.2.1] — 2026-09-24

卡死工作间回收与孤儿残留清理版本。

### 新增
- **卡死工作间识别**：新增 `GET /api/workshops/stale`，识别心跳超时 / 长时间无进展的卡死工作间（pinned 永不列入）。
- **批量回收**：新增 `POST /api/workshops/recycle-stale`，一键回收全部卡死工作间（跳过 pinned，不终止共享 harness 桥进程）。
- **前端回收按钮**：工作间卡片新增回收按钮（带确认弹窗）。

### 修复
- **DELETE 遗留孤儿**：修复旧版 `DELETE /api/workshop/{ws_id}` 仅删除字典、不清理状态机记录与工作区目录的缺陷——删除时同步清除状态机记录（`task_state_machine` 新增 `remove(key)`）并将工作区目录移入回收站，不再产生孤儿残留。
- **启动自愈**：服务启动时自动清理历史遗留的孤儿状态机记录（工作间已删除但状态机残留）。

## [4.2.0] — 2026-09-24

工作间任务树结构化与记忆擦除纠偏版本。

### 新增
- **任务树结构化**：工作间任务树由扁平字符串升级为嵌套节点（parent/children），节点支持 kind（phase / task / correction）与 status（active / done / dropped）；新增 `/api/workshop/{ws_id}/tree` 端点族（建节点 / 更新状态 / 资源挂载 / 查询嵌套树）。
- **资源库联任务树**：工作间资源（`/api/workshop/{ws_id}/resources`）支持 `task_node` 挂载点，登记资源可挂到任意任务节点，`RESOURCES.md` 清单同步标注「关联任务」，前端资源卡片显示挂载节点名。
- **记忆擦除纠偏（确认走弯路）**：`POST /api/workshop/{ws_id}/halt-and-reset` 一键闭环——停止任务并重置状态机、在任务树落「错误路径（dropped）／正确路径（active）」纠偏节点、反思总结自动归档 `DETOUR_SUMMARY.md` 并挂正路节点、擦除讨论上下文、按成员/组长擦除平台任务记忆与 harness 经验索引、通知成员记忆已重置、工作间重置为 draft 可重新开始。
- **前端工作间升级**：任务树改为真实树形渲染（缩进 + 纠偏标签），新增「确认走弯路·停止任务并擦除记忆重来」按钮与决策模式展示。
### 文档
- 新增 `design-docs/工作间模式设计V5.md`：决策模式（用户决定 / 组长独裁 / 举手投票）完整设计，平台端仅保留设计，待 harness 端开发。
- 新增 `docs/silent-mode-orchestration-design.md`：静默模式编排设计。
- 新增 `design-docs/工作间模式-parallel并行落地设计.md`：并行工作模式落地设计。

## [4.1.0] — 2026-09-20

API 优先策略与工作间增强版本。
### 新增
- **AI Provider 策略**：默认 API 优先（OpenAI 兼容），本地模型仅作可选降级；`config.json` 支持 `ai_provider` / `ai_mode` 显式声明。
- **任务状态机**：9 态事件驱动（含 executing / waiting_reply / stuck_paused / blocked_retrying / timeout），心跳超时自动 L0 重试、失败升级 L1 提醒，终态防二次变更。
- **插话机制**：用户 / 系统插话（一般 / 紧急 / 灵感，系统级 L1/L2），优先级队列 + 超期回收，支持 JSON 落盘持久化。
- **工作间 UI（回字形车间）**：任务树 + 讨论区 + 插话抽屉 + 全屏放大视图，支持徽标化状态指示。
### 变更
- `data/` 运行时数据（workshops / tasks / harnesses / interjects / state_machine）JSON 落盘，重启自动恢复。
### 修复
- 大厅「发布」改走 `/api/workshop` 并跳转工作间，移除乐观插入导致的消息重复。
- harness 注册持久化（`harnesses.json`），重启不再丢失。

## [4.0.0] — 2026-09-11

首个开源准备版本（Open Source Ready）。

### 新增

- **平台核心**：FastAPI Web 服务 + Click 命令行，支持命令行一键启动、无需浏览器。
- **多 Agent 协作**：Agent 注册与发现、任务广播、举手、讨论室协商、委托执行、结果汇总。
- **Harness 标准化接入**：统一的注册 / 唤醒 / 派发 / 回报协议，外部 Agent（CLI / IDE / 桌面程序 / HTTP 服务）可作为成员参与协作。
- **桥模板库**：`file_poll`（文件邮箱轮询）、`pending_poll`（通用队列轮询）、`cli_acp`（ACP 协议子进程）三类模板，支持按 harness 信息机械渲染生成可运行桥脚本。
- **工作间模式（Workshop）**：大厅（hall）内容分发、成员激活、角色协作与工作区隔离。
- **AI Provider**：支持 `openai` 兼容接口、`ollama` 本地模型、`http_callback` 回调三类后端。
- **经验沉淀 v2**：能力账本（capability ledger）加权选人、任务完成奖励回写、harness 信誉加成。
- **自愈与守护**：自主恢复循环（周期性状态自检与恢复）、进程守护脚本。
- **持久化**：任务 / 讨论室 / harness / 工作间以 JSON 落盘，重启自动恢复。

### 开源准备

- 全仓脱敏：本地绝对路径改为相对路径或占位符；真实 harness 名改为示例名（`harness-a` / `harness-b`）；凭据字段改为环境变量占位。
- 依赖声明：新增 `requirements.txt`，明确运行所需第三方包与版本区间。
- 数据示例化：`data/` 改为首次运行自动生成；新增 `config.example.json`、`harnesses.example.json`。
- 文档：新增 `LICENSE`（MIT 占位）、`README.md`（重写）、`CONTRIBUTING.md`、本 `CHANGELOG.md`、`.gitignore`。
- 测试入仓：桥模板渲染断言、核心冒烟、端到端流程测试收敛至 `tests/`，提供一键入口且不依赖运行中服务。
- 配置：数据目录支持 `AC_DATA_DIR` 环境变量覆盖；端口默认 `18920`，可用 `--port` / `AC_PORT` 覆盖。

### 已知待办

- CI（GitHub Actions）与跨平台（Linux / macOS）验证尚未接入。
- 公开发布所需的 API 文档、示例 demo 数据尚未补充。
