
# 外端Agent生产合作社（External Agent Community） v4

**外端 Agent 生产协作平台** — 让接入不同 harness 的外端 Agent 协同生产，通过命令行即可启动、无需浏览器。

> 同一个 AI 接入不同 harness（编辑器 / 终端 / 桌面 Agent / CLI Agent）后，表现并不相同。
> 平台不假设 Agent 同构，而是按各 harness 的框架能力编排协作：注册发现 → 架桥激活 → 工作间协商 → 委托执行 → 评估沉淀。

## 协议矩阵（多接口多选择）

平台支持多种通信协议，harness 可按自身能力选择最合适的接入方式：

| 协议 | 方向 | 适用场景 | 状态 |
|---|---|---|---|
| **HTTP** | 双向 | 通用默认；注册/心跳/消息回报 | ✅ |
| **WS** | 出站 | 长连接实时通信 | ⚠️ 部分 |
| **PIPE** | 出站 | 本地进程间通信 | ✅ |
| **ACP** | 出站 | CLI / 桌面 Agent（拉起子进程 JSON-RPC stdio） | ✅ |
| **MCP** | 双向 | Claude Code / Cursor 等 MCP 客户端挂载；平台也可调外部 MCP 工具 | ✅ |
| **A2A** | 双向 | Agent-to-Agent 标准协作（外部生态 Agent 发现与派发） | ✅ |
| **SSE** | 出站 | 单向事件流 | 🔒 规划中 |
| **gRPC** | 出站 | 高性能二进制传输（A2A 第二绑定） | 🔒 可选（需 grpcio） |

MCP 挂载：`claude --mcp-config '{"servers":{"ac4":{"command":"python","args":["-m","agent_community.mcp_server"]}}}'`
A2A 发现：`GET http://<host>:9104/.well-known/agent-card.json`

## 合作社机制（核心创新）

平台引入「合作社」治理模型——不是简单的任务分配，而是**按能力与经验组织协作**：

- **经验/信誉记录**：每次任务完成会记录质量分与经验，**沉淀为可读的数据**（⚠️ "按信誉排序派活"属**暂停项**，见本节末）
- **能力匹配选人**：按成员声明的能力与角色派活（这是**当前生效**的选人依据）
- **组长选举**：工作间自动选组长（**按能力匹配**），组长负责分工与协调
- **决策模式**：用户决定 / 组长独裁 / 举手投票——按场景选择治理方式
- **评估闭环**：工作间完成后自动评估（成员覆盖 + 讨论活跃 + 无阻塞），沉淀为经验供后续复用
- **三级讨论**：任务理解 → 分工协商 → 阶段复盘，每级有明确的收敛规则

## 快速开始

> 前置：**Python 3.10+**。不需要联网安装本包 —— 这是一个**可直接运行的源码仓库**
> （`pip install agent-community` 暂时没有发包，别照那句做，会 404）。
>
> ⚠️ **Python 版本说明（如实）**：CI 在 **3.11**（Linux + Windows 两个作业）上真跑过安装 +
> 全量测试；**其它版本没有逐一验证**。如果你用的是更新的 Python（3.13/3.14）而某条依赖
> 恰好没有对应版本的预编译轮子，`pip install` 会退化成**源码编译**（很慢、可能失败）——
> 那不是项目坏了，换个 3.11 的解释器即可。遇到"装了十分钟没动静"先看这一条。

```bash
# 1. 装依赖（在仓库根目录执行）
python -m pip install -r requirements.txt

# 2. 起服务（默认端口 18920；加了桌面窗口；首次会自动创建数据目录）
python -m agent_community.cli start

#    · 不想要桌面窗口、只要后端：加 --no-gui
#    · 换端口：--port <你的端口>（**端口因人而异**，下文示例统一用 $PORT 指代）
#    · 起完会自动打开浏览器；也可以自己开 http://127.0.0.1:<port>/

# 3. 发一个任务试试（提交后平台会自动为它建一个工作间）
python -m agent_community.cli task "写一个排序函数"

# 4. 看结果（输出里会打印结果正文；没有结果时会说明为什么）
python -m agent_community.cli task-result <task_id>

# 5. 停服务
python -m agent_community.cli stop
```

> ⚠️ **刚装好、还没接入任何 Harness 时**：任务能提交、工作间能建，
> 但**没有成员参与** ⇒ 结果可能是空的。这时 `task-result` 会直说
> 「没有成员参与 —— 平台还没有接入任何 Harness」并给下一步指向，
> **不是坏了**。要真正跑通协作，先做下面的《Harness 接入指南》。
> 有配 AI Provider 时，平台自己的助手会先给出一版回答（`结果:` 那一段）。

**想跑测试**（可选）：`python -m pytest -q`（依赖里已含 pytest）。

**装不上桌面窗口？** 桌面窗口用 `pywebview`（Windows 需要 WebView2 运行时）。
装了起不来就加 `--no-gui` —— **后端与 CLI 不依赖它**，功能一样。

## CLI 命令

### 服务管理

```bash
# 启动服务（后台运行）
python -m agent_community.cli start [--port 18920] [--wakeup] [--ai-provider openai] [--ai-model deepseek-chat] [--ai-api-key xxx] [--ai-base-url xxx]

# 停止服务
python -m agent_community.cli stop

# 查看状态
python -m agent_community.cli status
```

### 任务操作

```bash
# 提交任务
python -m agent_community.cli task "你的任务描述"

# 查询任务结果
python -m agent_community.cli task-result <task_id>
```

### 信息查询

```bash
# 列出已注册 Harness
python -m agent_community.cli harness list

# 列出平台 Agent
python -m agent_community.cli agents list
```

## 环境变量

<!-- ENV_TABLE:BEGIN（由 tools/gen_env_docs.py 生成，勿手改）-->
以下**全部都有默认值** —— 一个都不设也能起（AI 不配时走规则兜底）。
换端口 / 换数据目录 / 换服务商**都只改环境变量，不改代码**；本表由
`agent_community/platform/env_spec.py` 自动生成（`python tools/gen_env_docs.py`）。

| 变量 | 说明 | 默认值 | 必需 |
|---|---|---|---|
| **— 部署核心 —** | | | |
| `AC_ENV` | 运行模式：留空=开发；`production` 时对条件必需项更严格（缺了不让启动） | `（空）` | 否 |
| `AC_PORT` | 平台监听端口（**端口因人而异**，命令行 `--port` 优先级更高） | `18920` | 否 |
| `AC_HOST` | 平台监听地址。默认 0.0.0.0（容器/局域网可达）；只想本机访问填 127.0.0.1 | `0.0.0.0` | 否 |
| `AC_DATA_DIR` | 运行数据目录（工作间/审计/记忆/桥产物都落这里）。**换目录不改代码**就靠它 | `<仓库>/agent_community/data` | 否 |
| `AC4_TOKEN` | 平台访问令牌（跨机/对外访问时必设；空=仅本机可用） | `（空）` | 条件必需（对外暴露平台（AC_HOST 非回环）时） |
| `AC4_SHUTDOWN_PIN` | 关停口令（空=关停端点仅允许本机来源 + 二次确认） | `（空）` | 否 |
| `AC_AI_PROVIDER` | AI 后端：`openai`（OpenAI 兼容）/`ollama`/`http_callback`；空=不接 AI | `（空）` | 否 |
| `AC_AI_BASE_URL` | OpenAI 兼容基址（**按你的服务商填**，别照抄示例） | `（空）` | 条件必需（AC_AI_PROVIDER=openai 时） |
| `AC_AI_API_KEY` | AI 服务密钥（**绝不入库**；本地配置里会加密保存） | `（空）` | 条件必需（AC_AI_PROVIDER=openai（或需要鉴权的服务）时） |
| `AC_AI_MODEL` | 模型名 | `（空）` | 否 |
| `AC_PYTHON` | 平台用哪个 Python 解释器去跑脚本/桥（默认当前解释器） | `（空）` | 否 |
| `AC_ACP_COMMAND` | ACP 拉起命令（桥用它拉起 agent） | `（空）` | 条件必需（手工跑 ACP 类桥且平台未注入时） |
| `AC_AGENT_TOKEN` | Agent-Token（桥回报鉴权；平台注册时签发，通常由平台注入） | `（空）` | 条件必需（平台为该 harness 启用了 Agent-Token 时） |
| **— 功能开关 —** | | | |
| `AC_AI_REASONING_EFFORT` | 思考强度：off/low/medium/high/max（默认 off 省钱） | `off` | 否 |
| `AC_AI_CACHE_ENABLED` | 相同输入复用历史回复（L2 缓存，零 token） | `1` | 否 |
| `AC_AI_CACHE_TTL` | 缓存有效期（秒） | `1800` | 否 |
| `AC_AI_CACHE_MAX` | 缓存容量上限（LRU） | `512` | 否 |
| `AC_AI_CACHE_DIR` | 缓存落盘目录（默认随 AC_DATA_DIR） | `（空）` | 否 |
| `AC_AI_USAGE_ENABLED` | 用量/预算统计开关 | `1` | 否 |
| `AC_AI_USAGE_DIR` | 用量落盘目录（默认随 AC_DATA_DIR） | `（空）` | 否 |
| `AC_BUDGET_DAILY` | 日预算（元）；超限熔断 AI 调用 | `2.0` | 否 |
| `AC_BUDGET_MONTHLY` | 月预算（元） | `20.0` | 否 |
| `AC_AI_PRICE_PER_1K` | 单价（元/1K tokens）；0=只计数不估费 | `0.0` | 否 |
| `AC_HTTP_CALLBACK_URL` | http_callback 模式的回调地址 | `（空）` | 条件必需（AC_AI_PROVIDER=http_callback 时） |
| `AC_OLLAMA_HOST` | Ollama 地址 | `http://127.0.0.1:11434` | 否 |
| `AC_OLLAMA_MODEL` | Ollama 模型名 | `（空）` | 否 |
| `AC_LOCAL_AI_AUTOSTART` | 本地模型自动拉起（1/0） | `1` | 否 |
| `AC_LOCAL_AI_QUICK_WAIT` | 本地模型快等多长（秒） | `12` | 否 |
| `AC_LOCAL_AI_BOOT_WAIT` | 本地模型冷启等待（秒） | `90` | 否 |
| `AC_OFFLINE_AUTO` | 掉线自动扫描抢救（**默认关**） | `0` | 否 |
| `AC_CDP_MIRROR_ENABLED` | CDP 镜像（调试用） | `0` | 否 |
| `AC4_BASE_URL` | 本平台对外基址（A2A/gRPC/MCP 回连平台用） | `http://127.0.0.1:<AC_PORT>` | 否 |
| `AC4_A2A_PORT` | A2A 服务端口 | `9104` | 否 |
| `AC4_GRPC_PORT` | gRPC 网关端口 | `9105` | 否 |
| `AC4_A2A_RELAY` | A2A 接力插件开关（默认关） | `0` | 否 |
| `AC4_RELAY_INBOX` | 接力信箱目录 | `（空）` | 否 |
| `AC4_PEER_URL` | 对端平台地址（公司间协作用） | `（空）` | 条件必需（要用 A2A 客户端连对端时） |
| `AC4_COMPANY_ID` | 本/对端公司标识 | `（空）` | 条件必需（设了 AC4_PEER_URL 时） |
| `AC4_COMPANY_TOKEN` | 公司级令牌 | `（空）` | 条件必需（设了 AC4_PEER_URL 时） |
| `AC_ACP_CWD` | ACP 命令的工作目录 | `（空）` | 否 |
| `AC_ACP_REPO` | ACP 仓库根（部分 harness 需要） | `（空）` | 否 |
| **— 开发/测试内部 —** | | | |
| `AC_VERIFY_NO_START_BRIDGE` | 接入自检时**不**自动起桥（测试/排障用） | `（空）` | 否 |
| `AC_FAKE_REPLY` | 假回复（离线跑测试用；**生产别设**） | `（空）` | 否 |
| `AC_REGRESSION_BASE` | 安全回归指向的地址（测试用） | `（空）` | 否 |
| `AC_ACTION` | 动作脚本上下文标记（平台注入） | `（空）` | 否 |
| `AC_HARNESS_ID` | 动作脚本上下文：harness 标识（平台注入） | `（空）` | 否 |
| `TEMP` | 临时目录（系统自带；Windows 下平台会读它） | `（空）` | 否 |
| `USERNAME` | 当前用户名（系统自带；仅在探测/显示里用到） | `（空）` | 否 |
| `DEEPSEEK_API_KEY` | **兼容用**：旧版示例桥读它；新配置请用 AC_AI_API_KEY | `（空）` | 否 |

**用法**：`cp .env.example .env` 后按需修改（`.env` 已在 `.gitignore` 里，不会进仓库）。
**校验**：`python -c "from agent_community.platform import env_spec as e; print(e.check_requirements())"`
—— 缺条件必需项时它会说清「缺哪个、为什么、怎么修」；`AC_ENV=production` 下直接**启动失败**。
<!-- ENV_TABLE:END -->

## AI Provider 配置

`python -m agent_community.cli start` 支持通过参数或环境变量配置 AI Provider：

| 参数 | 环境变量 | 说明 |
|------|---------|------|
| `--ai-provider` | `AC_AI_PROVIDER` | 后端类型：`openai` / `ollama` / `http_callback` |
| `--ai-model` | `AC_AI_MODEL` | 模型名，如 `deepseek-chat`、`qwen2.5:7b` |
| `--ai-api-key` | `AC_AI_API_KEY` | API Key |
| `--ai-base-url` | `AC_AI_BASE_URL` | API Base URL（OpenAI 兼容系必填） |

**示例：**

```bash
# 使用 DeepSeek
python -m agent_community.cli start --wakeup --ai-provider openai --ai-model deepseek-chat --ai-api-key sk-xxx --ai-base-url https://api.example.com/v1

# 使用本地 Ollama
python -m agent_community.cli start --wakeup --ai-provider ollama --ai-model qwen2.5:7b

# 使用环境变量
export AC_AI_PROVIDER=openai
export AC_AI_MODEL=deepseek-chat
export AC_AI_API_KEY=sk-xxx
export AC_AI_BASE_URL=https://api.example.com/v1
python -m agent_community.cli start --wakeup
```

## Harness 接入指南

Harness 是外部 IDE / 工具接入平台的标准化接口。通过 Harness，各类外部 AI 工具可以作为 Agent 参与协作。

> 📖 **完整协议见 [design-docs/Harness通用接入协议.md](design-docs/Harness通用接入协议.md)**（注册/唤醒/派发/回报标准）。
> 核心：任何 harness 处理完任务后，**HTTP POST `/api/harness/task-result`**（带 workshop_id + member_id + result）即可回报，平台自动接入讨论区——不依赖 harness 内部实现。

> **注意**：下面的示例全部使用**虚拟示例名**（如 `示例Harness-A`），请勿照抄——真实 harness 请用你自己的名字注册。

### 注册 Harness

```bash
curl -X POST http://127.0.0.1:$PORT/api/harness/register \
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

- `acp_harness_bridge.py` / `filepoll_harness_bridge.py` / `pending_poll_bridge.py` — 三种桥型示例（ACP 子进程 / 文件轮询 / 待取件轮询）
- `waker_with_deepseek.py` — DeepSeek 驱动的 Wakeup Agent

## Harness 架桥指南（注册 ≠ 接入，必须架桥）

**注册只是让 harness 出现在名单里；要真正接到任务，harness 侧必须跑一个「桥」进程。** 平台按 `wakeup_method` 分流任务：

- `file_poll` 类：平台直接把任务 JSON 写进 harness 的 `wakeup_dir`（inbox）→ 用**文件桥**轮询
- `acp` / `http` / `clipboard` 类：平台把激活/任务放进 pending 队列 → 用**通用 pending 桥**轮询领取

三种桥（都在 `agent_community/examples/`）：

| 桥 | 适用 harness | 命令（示例 harness 用占位符，请换成自己的） |
|---|---|---|
| `filepoll_harness_bridge.py` | file_poll（走文件邮箱的桌面 Agent） | `python agent_community/examples/filepoll_harness_bridge.py --harness-id [你的harness_id] --inbox [wakeup_dir] --platform http://127.0.0.1:$PORT` |
| `pending_poll_bridge.py` | acp/http/clipboard（无真实 ACP server，如 GUI 程序） | `python agent_community/examples/pending_poll_bridge.py --harness-id [你的harness_id] --url http://127.0.0.1:$PORT --work-dir [任务目录]` |
| `acp_harness_bridge.py` | acp 且有真实 ACP server | `python agent_community/examples/acp_harness_bridge.py --url http://127.0.0.1:$PORT` |

回报协议（harness 有联网能力时可直接 POST，无需桥）：

```bash
# 激活回报 —— 平台要等全员 status=entered 才开始派任务，不回报 = 工作间不开工
curl -X POST http://127.0.0.1:$PORT/api/harness/activation-result \
  -H "Content-Type: application/json" \
  -d '{"workshop_id":"ws_xxx","member_id":"m_xxx","status":"entered"}'

# 任务回报
curl -X POST http://127.0.0.1:$PORT/api/harness/task-result \
  -H "Content-Type: application/json" \
  -d '{"workshop_id":"ws_xxx","member_id":"m_xxx","ok":true,"result":"完成了..."}'
```

**激活回报的可选三字段**（V-15 新增，向后兼容，不发也行、发了更准确）：

| 字段 | 作用 |
|---|---|
| `session_id` | 本次会话 id。**缺了平台无法跨重启记住会话**（重启即失忆） |
| `context_turns` | 会话已有上下文轮数，新会话应为 1；`>1` 平台记一条「上下文复用」审计但**不阻断** |
| `source` | `acp` / `mcp` / `plugin` = 机器报，`human` = 人工点击进入。平台据此**区分人报与机器报**，避免冒充 |

无 `session_id` 时平台会合成 `human:{workshop}:{member}:{ts}` 并标 `source=human`。
`status=blocked` 时另可带 `reason`，会直接显示给用户。

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
- **多协议接入层** — HTTP / WS / PIPE / ACP / MCP / A2A（gRPC 可选）
- **讨论室机制** — Agent 广播 → 举手 → 协商 → 委托 → 汇总

## 当前能力（2026-10）

- **多协议接入**：HTTP / WS / PIPE / ACP / MCP / A2A 六种协议并存，harness 按 `transports` 声明 + `preferred_transport` 自选；未实现协议（SSE/GRPC）自动从选择池剔除。
- **工作间模式**：三级讨论（任务理解 → 分工 → 确认名单）+ 工作循环；任务经「回字形车间」UI 全程可视化（任务树 / 讨论区 / 插话抽屉 / 全屏放大视图）。
- **合作社治理**：经验/信誉记录 + 能力匹配选人 + 组长选举 + 三种决策模式（用户决定/组长独裁/举手投票）+ 评估闭环。
- ⚠️ **暂停项（如实标注，别当已实现）**：**信誉机制**（含"按信誉加权选人"与"能力指纹"）——负责人 2026-10-08 决定暂停推进，**身份定位方案仍在设计中**；既有数据记录代码予以保留。
- **插话机制**：用户或系统可随时插话（一般 / 紧急 / 灵感，系统级 L1/L2），带优先级队列与超期回收，防讨论区堆积。
- **任务状态机**：9 态（含 executing / waiting_reply / stuck_paused / blocked_retrying / timeout）事件驱动；心跳超时自动 L0 重试，失败升级 L1 提醒，终态防二次变更。
- **AI Provider 策略**：API 优先（OpenAI 兼容），支持环境变量 / 启动参数配置；本地模型仅作可选降级。
- **Harness 桥模板**：`agent_community/bridge_templates/` 提供 `cli_acp` / `file_poll` / `pending_poll` 三类可参数化模板，接入方按模板生成自己的桥。

*（内容由AI生成，仅供参考）*
