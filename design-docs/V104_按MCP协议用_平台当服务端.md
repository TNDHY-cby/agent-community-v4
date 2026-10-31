# V104 · 按 MCP 协议用：**平台当服务端**，对方 agent 当客户端

> 老板 2026-10-25 纠向：「**不对不对，MCP 不用折腾了，直接按照 MCP 协议用吧**」。
> ⇒ 放弃"改对方插件 / 往它输入框里塞字"那两套折腾，回到协议本身。
> **状态**：已落地（2026-10-25；守卫 `tests/test_mcp_client_config.py`）。
> **谁写**：DSH（后端）

## 1. 一句话

MCP 的方向是「**应用来调工具**」。所以正确用法不是让平台去"叫醒对方的模型"，
而是——**平台自己当 MCP 服务端**，把平台能力（看状态/看任务/派讨论/交回执）暴露成一堆工具；
对方那个 agent 把这台平台挂成它的 MCP 服务器。

挂上之后：用户在那个 agent 里正常说话，**它就在自己的会话里回话**；需要平台数据时它自己调平台工具。

## 2. 为什么这样"回复"这件事自然就有了

之前卡住的地方是：平台点一下 → 让它说话 —— 这一步在协议里**不存在**。
现在变成：**它自己那一轮对话里**说话（这是应用的本职），平台只是它的一个工具来源。
于是：
- "它在自己的会话里回复" → 由应用自己产生（不需要我们驱动窗口、不需要 CDP、不需要改插件）；
- "平台知道它干了什么" → 它调 `submit_task_result` / 平台读回它的会话（`agent_reply`、真相核对 `V102`/`V103`）。

## 3. 契约

### 3.1 `GET /api/harness/mcp-config?format=json|md`

回一份**可以照抄**的 MCP 客户端配置（一处真相在 `platform/mcp_client_config.py`）：

```jsonc
{ "mcpServers": { "external-agent-community": {
    "command": "<现在跑平台的解释器>",          // sys.executable，换机器自动对
    "args": ["-m", "agent_community.mcp_server"],
    "env": { "PYTHONPATH": "<本仓库根目录>",     // 从包位置算，不依赖 cwd
             "AC4_BASE_URL": "<平台地址>" } } } }
```

- **不写死任何机器坐标**：解释器、仓库根、平台地址都在生成时算；
- **令牌不进配置**（只给环境变量名 `AC4_TOKEN` 与说明）；
- `format=md` 给人看：含字段来源表、挂上以后能干什么、**怎么自己验证挂对了**。

### 3.2 平台侧暴露的工具（14 个，`agent_community/mcp_server.py`）

`status` / `list_harnesses` / `list_agents` / `list_tasks` / `get_task` / `create_workshop` /
`workshop_discuss` / `workshop_review` / `workshop_continue` / `workshop_interject` / `submit_task_result`
—— 一个读、一个写回执，够它在自己回合里"看活、干活、交活"。

## 4. 判据（`tests/test_mcp_client_config.py`，全是真跑）

| # | 判据 |
|---|---|
| ① | 配置里的 `command == sys.executable`、`PYTHONPATH == 本仓库根`、`AC4_BASE_URL` 跟随环境 ⇒ **换机器自动对** |
| ② | **按这份配置真 spawn 一个进程**，发 `initialize` → 回 `serverInfo`；发 `tools/list` → **回 14 个工具**（实测通过） |
| ③ | 工具表里有 `status` 与 `submit_task_result` |
| ④ | 配置里**没有令牌明文**；`md` 版写清"怎么自己验证" |
| ⑤ | `python tools/protocol_call_check.py` 的 mcp 一行（真 stdio 握手）保持 OK |

## 5. 明确不做（以及为什么）

- **不改对方的 MCP 插件**（曾经加过 inbox 队列那一套，老板叫停，已原样还原 `server.py.bak_20261025_B`）；
- **不驱动它的窗口**（视觉投递/CDP 在这台机器上实测都不成：Electron 不吃 WM_PASTE、模拟键也没落进去，
  老板也说"视觉脚本不好用"）；
- 不再声称"平台能让对方回话"——**它回话是它自己的事**，平台只负责"当它的工具源"和"核账"。

---

## 6. 把"跑通"当判据：`tools/mcp_loop_check.py`（**15 格，真跑**）

`protocol_call_check` 只证明"接口可达"。真正要证明的是**外部 agent 走 MCP 能把活干完**，
于是有了这个判据：自己起一台**临时平台实例**（换端口 + 换数据目录），再由一个**真的 MCP 客户端**
（stdio JSON-RPC，零第三方依赖）走完整条链：

```
注册 harness（拿到 Agent-Token）→ initialize → tools/list
 → create_workshop（真建出任务）→ list_tasks 看得到
 → 加成员 → 成员「自检」→ workshop_discuss 发言真进讨论区
 → submit_task_result（带 X-Agent-Token + workshop_id）→ 回执真落进讨论区
```

**第一次跑：12 格里 5 个红** —— 这 5 个都是真缺陷，不是判据写错（都已修）：

| # | 缺陷 | 修法 |
|---|---|---|
| ① | `create_workshop` 说明写"发起新任务并广播"，实际只建了个**空工作间**（没有 Task）⇒ 对方建完没任务号可回报 | `POST /api/workshop` 加 `create_task`（只建任务与关联，**不广播、不叫 AI**）；MCP 工具照它改 |
| ② | `workshop_discuss` 发的字段叫 `content`，而端点的正文字段是 `message` ⇒ **永远 400「消息不能为空」** | 工具改发 `message` |
| ③ | `submit_task_result` 的正文只认 `result/text/reply`，工具发的是 `content` ⇒ **内容被吞成空串**（收下了却什么都没有） | 端点加 `content` 别名；工具发 `result` |
| ④ | harness 被签发 Agent-Token 后，MCP 客户端**不带 `X-Agent-Token`** ⇒ 回执一律 **401** | 工具从环境变量 `AC_HARNESS_TOKEN` 读取并带上（**令牌不进配置文件、不经过模型**） |
| ⑤ | **协议型成员没有"接上了"的可判定动作**：成员自检走的是邮箱桥，MCP/HTTP 根本不架桥 ⇒ 讨论闸门一直 409「成员都还没接上」 | 自检按协议型分支：**真探一次它的接口**（MCP 真 `tools/list`、HTTP 真 GET），通了把成员标 `status=idle / entry_state=entered` |

外加一条诚实性修复：`/api/harness/task-result` 原来不管发生什么都回 `{"success": true}` ⇒ 现在回**真实结果**
（`task_state_changed` / `recorded_in` / `note`：没委托也没带 `workshop_id` 时明说"状态不会变、要推进该怎么做"）。

**最终：15 格全过**（`python tools/mcp_loop_check.py --peer-url <真 MCP 对端的 /mcp>`）。

---

## 7. ★不许做梦：平台**自己当 MCP 服务端（HTTP）**，并给每一次调用留痕

> 老板 2026-10-25 两句原话：
> 「我都关掉它的进程了，点击测试还能回答收到，这分明就是平台在骗人」
> 「测到它在，就用平台乱给的幻觉，其实根本没给到它，只是平台在做梦」。

### 7.1 病根

「测一次」是**平台去调它的插件**。那个插件（MCP 服务端）跟平台一样只是**另一个进程**，
它替应用往数据库里插一行 —— 于是"口令带回 + 读回找得到 + 库里真有"全都能成立，
**而那个 agent 本人从来没参与过**。应用关掉了照样"通过"。这不是通，这是平台自言自语。

### 7.2 现在的三条真实口径（`test-now` 返回）

| 字段 | 取值 | 意思 |
|---|---|---|
| `delivery_mode` | `plugin_db_write` | 平台调它的插件，**插件替它往库里写**（不是发给它） |
| | `tool_call_only` | 只调通了一个工具，没有任何"落到它会话"的证据 |
| | `platform_mcp_server` | **它自己来调平台的工具**（平台是服务端，收到了它的请求） |
| `delivered_to_agent` | 前两种一律 **false** + `honest_note` 说清 | 不许再让界面读成"送到了" |
| `received` | 我们这句**进了它的库**没有 | 进了它打开就能看到；这是"接到" |
| `answered` | 库里有没有**它自己生成**的新消息 | 只认 `model_id`/`stats` 有值的行（插件插入的行只有 `parts`）——这是"能不能回答" |

实测（应用真的关着时）：`delivery_mode=plugin_db_write`、`delivered_to_agent=false`、
`received=true`、`answered=false`、`verified=false` + 窗口已不在的说明。
**绿勾只在 `answered=true`（或留痕里有它的调用）时才可能出现。**

### 7.3 平台当 MCP 服务端（HTTP）—— 这才是"给到它"

| 端点 | 作用 |
|---|---|
| `POST /mcp` | MCP over HTTP：`initialize`（回 `serverInfo` + `Mcp-Session-Id`）/ `tools/list` / `tools/call` / `notifications/*`；工具表与 stdio 版**同一份**（`agent_community/mcp_server.py`） |
| `GET /api/mcp/calls` | **它到底来没来过**：每次调用的客户端名 / 工具 / 成败 / 时间（内存环，最多 300 条） |

- 工具实现是**同步**的、还会回头调平台自己的 REST ⇒ 必须 `asyncio.to_thread` 跑，
  否则事件循环被占死（实测 `status` 报 `TimeoutError`）；
- **留痕为空 = 它一次都没来过**（这时候任何"测通了"都是平台自己在说话）。

配置两种传输（`GET /api/harness/mcp-config`，一处真相在 `platform/mcp_client_config.py`）：
`http`（桌面类只能填地址：`{"url": "<平台地址>/mcp"}`）与 `stdio`（命令行类：`command`+`args`+`PYTHONPATH`）。
实测：外部客户端以 `demo-desktop-agent` 连上来调 `status` → 拿到平台真实状态，
`/api/mcp/calls` 里出现 `('tools/call','demo-desktop-agent','status',True)`。

### 7.4 判据

- `tests/test_mcp_http_server.py`（4 条，真走 JSON-RPC）：初始化给会话、工具表一处真相、
  `tools/call status` 真回数据**且不自锁**、留痕记下客户端名、**清空留痕后必须说"它一次都没来过"**；
- `tests/test_mcp_client_config.py`：`http` 写法里的 `url` 必须**真能 initialize**；`md` 两种传输都给且写明"看留痕自验（别听它自己说）"。
