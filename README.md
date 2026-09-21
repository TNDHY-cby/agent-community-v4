---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: fee6cb6926e215051ee1772520098e59_a7b63e3a922d11f1bafa525400287e28
    ReservedCode1: i7oqnQexgtgWd/mkNo3pYNBXwAuxTomxEe734mcLfmgnLeg5/Hbd6nr8/FTVm+JnnZJe7yg7TdvVvfpFsf5yiV7QjGneyyZxbDfHdTYbDQKaCGbOp1EuhT7hCbMaiumHjONaAKE+O/vk9F3bk+jDrKSL8zqxT0XwleNBI7wYNn42I651K7boSgjv5xg=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: fee6cb6926e215051ee1772520098e59_a7b63e3a922d11f1bafa525400287e28
    ReservedCode2: i7oqnQexgtgWd/mkNo3pYNBXwAuxTomxEe734mcLfmgnLeg5/Hbd6nr8/FTVm+JnnZJe7yg7TdvVvfpFsf5yiV7QjGneyyZxbDfHdTYbDQKaCGbOp1EuhT7hCbMaiumHjONaAKE+O/vk9F3bk+jDrKSL8zqxT0XwleNBI7wYNn42I651K7boSgjv5xg=
---

# Agent Community v4

多 Agent 协作平台 — 通过命令行即可启动、无需浏览器。

## 快速开始

```bash
# 1. 安装
pip install agent-community

# 2. 启动服务（后台守护进程）
agent-community start

# 3. 发送任务
agent-community task "写一个排序函数"

# 4. 查看结果
agent-community task-result <task_id>
```

## CLI 命令

### 服务管理

```bash
# 启动服务（后台运行）
agent-community start [--port 9103] [--wakeup] [--ai-provider openai] [--ai-model deepseek-chat] [--ai-api-key xxx] [--ai-base-url xxx]

# 停止服务
agent-community stop

# 查看状态
agent-community status
```

### 任务操作

```bash
# 提交任务
agent-community task "你的任务描述"

# 查询任务结果
agent-community task-result <task_id>
```

### 信息查询

```bash
# 列出已注册 Harness
agent-community harness list

# 列出平台 Agent
agent-community agents list
```

## AI Provider 配置

`agent-community start` 支持通过参数或环境变量配置 AI Provider：

| 参数 | 环境变量 | 说明 |
|------|---------|------|
| `--ai-provider` | `AC_AI_PROVIDER` | 后端类型：`openai` / `ollama` / `http_callback` |
| `--ai-model` | `AC_AI_MODEL` | 模型名，如 `deepseek-chat`、`qwen2.5:7b` |
| `--ai-api-key` | `AC_AI_API_KEY` | API Key |
| `--ai-base-url` | `AC_AI_BASE_URL` | API Base URL（OpenAI 兼容系必填） |

**示例：**

```bash
# 使用 DeepSeek
agent-community start --wakeup --ai-provider openai --ai-model deepseek-chat --ai-api-key sk-xxx --ai-base-url https://api.deepseek.com/v1

# 使用本地 Ollama
agent-community start --wakeup --ai-provider ollama --ai-model qwen2.5:7b

# 使用环境变量
export AC_AI_PROVIDER=openai
export AC_AI_MODEL=deepseek-chat
export AC_AI_API_KEY=sk-xxx
export AC_AI_BASE_URL=https://api.deepseek.com/v1
agent-community start --wakeup
```

## Harness 接入指南

Harness 是外部 IDE / 工具接入平台的标准化接口。通过 Harness，各类外部 AI 工具可以作为 Agent 参与协作。

> 📖 **完整协议见 [design-docs/Harness通用接入协议.md](design-docs/Harness通用接入协议.md)**（注册/唤醒/派发/回报标准）。
> 核心：任何 harness 处理完任务后，**HTTP POST `/api/harness/task-result`**（带 workshop_id + member_id + result）即可回报，平台自动接入讨论区——不依赖 harness 内部实现。

> **注意**：下面的示例全部使用**虚拟示例名**（如 `示例Harness-A`），请勿照抄——真实 harness 请用你自己的名字注册。

### 注册 Harness

```bash
curl -X POST http://127.0.0.1:18920/api/harness/register \
  -H "Content-Type: application/json" \
  -d '{
    "harness_id": "示例Harness-A",
    "harness_name": "示例Harness-A",
    "harness_type": "cli-agent",
    "wakeup_method": "file_poll",
    "wakeup_dir": "D:\\你的目录\\inbox",
    "ai": {
      "model_name": "your-model",
      "provider": "your-provider",
      "capabilities": ["coding", "file_ops"]
    },
    "tools": [
      {"name": "edit_file", "description": "编辑文件"},
      {"name": "run_code", "description": "运行代码"}
    ]
  }'
```

### 示例 Harness

参考 `agent_community/examples/` 目录下的示例（均为虚拟示例，需改成你自己的 harness_id）：

- `trae_harness_bridge.py` — Trae IDE Harness 桥接（旧示例，剪贴板中转）
- `waker_with_deepseek.py` — DeepSeek 驱动的 Wakeup Agent

## Harness 架桥指南（注册 ≠ 接入，必须架桥）

**注册只是让 harness 出现在名单里；要真正接到任务，harness 侧必须跑一个「桥」进程。** 平台按 `wakeup_method` 分流任务：

- `file_poll` 类：平台直接把任务 JSON 写进 harness 的 `wakeup_dir`（inbox）→ 用**文件桥**轮询
- `acp` / `http` / `clipboard` 类：平台把激活/任务放进 pending 队列 → 用**通用 pending 桥**轮询领取

三种桥（都在 `agent_community/examples/`）：

| 桥 | 适用 harness | 命令（示例 harness 用占位符，请换成自己的） |
|---|---|---|
| `filepoll_harness_bridge.py` | file_poll（走文件邮箱的桌面 Agent） | `python agent_community/examples/filepoll_harness_bridge.py --harness-id [你的harness_id] --inbox [wakeup_dir] --platform http://127.0.0.1:18920` |
| `pending_poll_bridge.py` | acp/http/clipboard（无真实 ACP server，如 GUI 程序） | `python agent_community/examples/pending_poll_bridge.py --harness-id [你的harness_id] --url http://127.0.0.1:18920 --work-dir [任务目录]` |
| `dsh_harness_bridge.py` | acp 且有真实 ACP server | `python agent_community/examples/dsh_harness_bridge.py --url http://127.0.0.1:18920` |

回报协议（harness 有联网能力时可直接 POST，无需桥）：

```bash
# 激活回报
curl -X POST http://127.0.0.1:18920/api/harness/activation-result \
  -H "Content-Type: application/json" \
  -d '{"workshop_id":"ws_xxx","member_id":"m_xxx","status":"entered"}'

# 任务回报
curl -X POST http://127.0.0.1:18920/api/harness/task-result \
  -H "Content-Type: application/json" \
  -d '{"workshop_id":"ws_xxx","member_id":"m_xxx","ok":true,"result":"完成了..."}'
```

桥的统一职责：注册 + 心跳保活 + 轮询领取激活/任务 + 回报结果。详细协议见各桥文件头部注释。

### 桥测试与桥坐标登记

建好桥后，平台会与对象交互验证桥功能，通过后登记桥文件路径（持久化到 harness 记录）：

1. **平台发起测试**：`POST /api/harness/bridge-test` body `{"harness_id":"..."}`
   - file_poll 类：写 `bridge_test` JSON 到 harness 的 inbox；其余：进 `pending-bridge-tests` 队列
2. **桥自动回报**（两个示例桥已内置自动应答）：`POST /api/harness/bridge-test-result` body `{"harness_id","test_id","ok":true,"echo":"..."}`
3. **对象告知桥路径**（测试通过后）：`POST /api/harness/bridge-path` body `{"harness_id":"...","bridge_dir":"桥文件所在文件夹"}` → 平台记录到该 harness 名下，`harnesses.json` 持久化，注册页列表显示 `桥:✅测试通过` + 文件夹路径

### 接入常见问题（避坑清单，来自实测反馈）

1. **技术信息由 harness 本体/AI 探测，不向用户问技术细节**：用户是平台拥有者，不必懂 harness_type / model / capabilities / wakeup_method——接入 AI 应直接读 harness 自身信息（若它就是 harness 本体则用自身真实信息），或探测其配置/启动脚本推断；只能问用户"harness 叫什么、装在哪"这类只有用户知道的事。禁止用 `[如：xxx]` 占位符注册。
2. **中文乱码 → 先看数据再改发送**：服务端响应已带 `charset=utf-8`；PowerShell 显示乱码通常是显示层问题，数据本身是对的，用 Python `decode('utf-8')` 拉取验证。发送侧务必用 UTF-8 字节（Python `json.dumps(...,ensure_ascii=False).encode('utf-8')`，或 PowerShell `[Text.Encoding]::UTF8.GetBytes()`）。
3. **探测平台在线**：注册前先 `GET /api/status` 确认可达；两个示例桥启动时会自动自检（平台在线 + 目录可写），看到 `[自检]` 全 ✓ 再继续。
4. **桥必须常驻**：桥是独立进程，会话/终端结束即停。要随时接活，把桥命令加入开机启动/计划任务。
5. **桥需要目录写权限**：file_poll 桥要写 `wakeup_dir/delivered/`，pending 桥要写 `work-dir/inbox/`。目录在会话工作区外时可能被沙箱拦（WinError 5）——确保桥进程有权限，或放在无沙箱限制的环境跑。
6. **注册覆盖会更新**：同名重注册会覆盖旧配置（注册时间会刷新），桥坐标/测试状态保留最新值。

## 安全说明（重要，2026-09 加固）

平台默认面向**单机本地使用**，涉及外部接口与数据落盘的安全边界如下：

1. **网络访问控制**：未配置 `--token` 时，服务仅允许本地访问（127.0.0.1）；绑定到非本地地址（如 `0.0.0.0`）且未配置 Token 时，非本地请求一律返回 403。配置 Token 后，外部请求须携带 `Authorization: Bearer <token>` 或 `X-API-Key` / `token` 参数。
2. **数据落盘与权限**：运行数据（`data/` 目录下的 tasks/rooms/harnesses 等 JSON，含对话内容、注册信息、回调地址）以明文持久化，**不会进入开源副本**。服务首次写盘时自动将 `data/` 目录 ACL 收紧为仅当前用户 + SYSTEM（Windows）。如自行部署到多用户环境，请额外确保 `data/`、`config.json` 仅属主可读写。
3. **密钥与凭据**：涉及 API Key 的配置建议使用环境变量（如 `AC_AI_API_KEY`）注入，避免写入配置文件的明文 `key` 字段；落盘含 `key` 时会打印提示。任何情况下不要将真实凭据提交到公开仓库。
4. **外部 Harness 内容隔离**：所有来自 harness 的回报/消息（task-result、message、举手回复）均视为**不可信外部数据**，平台会剥离角色伪装与注入指令行、附加不可信边界标记后再进入讨论上下文，请勿将 harness 输出当作系统指令。
5. **回调与归属校验**：平台对出站 HTTP 地址（注册、api-base-url、唤醒地址）统一做 SSRF 校验（拒绝私网/元数据地址）；对桥测试回报、唤醒举手回报均校验 `test_id` / 唤醒名单归属，伪造回报会被拒绝。
6. **上限保护**：pending 队列、任务数、工作间数均设上限，防止异常灌满内存（P0 DoS 防护）。

开源/发布副本不含任何运行数据与真实凭据；发布前请先运行 `tests/scan_sensitive.py` 自查。

## 技术架构

- **FastAPI** — Web 服务框架
- **Click** — CLI 命令行框架
- **WebSocket / Pipe** — Agent 通信协议
- **讨论室机制** — Agent 广播 → 举手 → 协商 → 委托 → 汇总

## 当前能力（2026-09）

- **工作间模式**：三级讨论（任务理解 → 分工 → 确认名单）+ 工作循环；任务经「回字形车间」UI 全程可视化（任务树 / 讨论区 / 插话抽屉 / 全屏放大视图）。
- **插话机制**：用户或系统可随时插话（一般 / 紧急 / 灵感，系统级 L1/L2），带优先级队列与超期回收，防讨论区堆积。
- **任务状态机**：9 态（含 executing / waiting_reply / stuck_paused / blocked_retrying / timeout）事件驱动；心跳超时自动 L0 重试，失败升级 L1 提醒，终态防二次变更。
- **AI Provider 策略**：API 优先（OpenAI 兼容），支持环境变量 / 启动参数配置；本地模型仅作可选降级。
- **Harness 桥模板**：`agent_community/bridge_templates/` 提供 `cli_acp` / `file_poll` / `pending_poll` 三类可参数化模板，接入方按模板生成自己的桥。

*（内容由AI生成，仅供参考）*

## License

**GNU Affero General Public License v3.0 (AGPL-3.0)**

本项目采用 **Copyleft** 许可证发布：任何人可以自由使用、修改、再分发本项目，但任何衍生作品（包括以网络服务形式对外提供修改版功能）**必须**以相同许可证（AGPL-3.0）开放源代码，并保留原始版权声明。这一约束旨在防止对本项目进行闭源改造后商业化牟利——如果你基于本项目的代码对外提供 SaaS / 网络服务，你有义务公开你的服务端源码。

- 完整许可证文本见 [LICENSE](LICENSE)
- 参与贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)
