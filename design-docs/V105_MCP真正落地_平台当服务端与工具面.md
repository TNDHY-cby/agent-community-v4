# V105 · MCP 真正落地（主线）：平台当服务端，14 个工具逐个真调通

> ⚠️ **定位（老板 2026-10-28 更正）**：MCP **只是「工具面」** —— 它让外端 agent **调用平台的工具与技能**，
> 它**不表达**身份、任务归属、验收责任，也不是「协作协议里最好的那个」。
> 协作语义（派活 / 回执 / 验收 / 证据）在平台自研的 `V110` 回执 + 任务账本里；
> 分层口径见 `V116` §1.1（身份 → 契约 → 传输 → 触达）。


> 老板 2026-10-25 定：
> 「**我已经停止 MCP 协议的会话内回答的开发了，所以现在要做的是 MCP 真正落地**」。
> ⇒ 本文件是**现行主线**；`V102`（真相核对）与 `V103`（它回的话）里那条"让对方在自己会话里回话/我们核它回的话"
> 的做法**停产**，代码保留、**不作验收依据**（详见 §1）。
> **状态**：已落地（2026-10-25；判据 `tools/mcp_tools_check.py` **15 格全过**）。
> **谁写**：DSH（后端）

## 1. 停产的那条线（写清楚，别再回头做）

| 停产的东西 | 为什么 | 留着的部分 |
|---|---|---|
| 让外端 agent「在自己的会话里回答一声」，平台去核它答没答 | 实测（`V101`/`V103`）：那条路的"回答"全是**插件替它写库**，agent 本人没参与；应用关掉也能"通过" | `session_truth` 的读库判据、`agent_reply` 等字段保留，**只在诊断时用**；`test-now` 的返回里明确标注「已停止开发，不作验收依据」 |
| 平台"点一下就让它回话" | MCP 协议方向不允许（应用来调工具，不是工具叫应用） | —— |

## 2. 落地的主线：**平台当 MCP 服务端**，外端 agent 来调

```
外端 agent（任意 MCP 客户端）
    │  HTTP  POST /mcp   （桌面类：填一个地址）
    │  或 stdio          （命令行类：填 command+args）
    ▼
平台（服务端）── 14 个工具 ── 平台自己的 REST（工作间/任务/回执）
    │
    └─ 每次真调用都留痕：GET /api/mcp/calls（客户端名 / 工具 / 成败 / 时间）
```

### 2.1 工具面（14 个，一处真相在 `agent_community/mcp_server.py`）

`status` / `list_harnesses` / `list_agents` / `list_tasks` / `get_task` / `create_workshop` /
`workshop_discuss` / `workshop_review` / `workshop_continue` / `workshop_interject` / `submit_task_result`

- HTTP 版（`platform/routers/mcp_http.py`）与 stdio 版**共用同一份工具实现**，不重写；
- 工具是同步的、还会回头调平台 REST ⇒ 必须丢线程跑（`asyncio.to_thread`），
  否则事件循环被占死（实测 `status` 报 `TimeoutError`）。

### 2.2 怎么挂（一份配置，两种传输）

`GET /api/harness/mcp-config`（`platform/mcp_client_config.py` 是唯一真相）：

| 客户端类型 | 给什么 |
|---|---|
| 桌面类（只能填地址） | `{"mcpServers": {"external-agent-community": {"url": "<平台地址>/mcp"}}}` |
| 命令行类（Claude Code / Cursor） | `command` = 当前解释器、`args` = `["-m","agent_community.mcp_server"]`、`env.PYTHONPATH` = 本仓库根 |

解释器 / 仓库根 / 平台地址**都是运行时算出来的**（换机器自动对）；**令牌不进配置**。

### 2.3 鉴权口径（与平台全局一致，不另造一套）

| 场景 | 口径 |
|---|---|
| 本机（回环） | 免令牌（与其它端点一致） |
| 外部 | 需要平台令牌（`Authorization: Bearer …`）；没配令牌时**拒绝非本机** |
| 开了外部只读档（`AC_EXTERNAL_READONLY=1`） | `POST /mcp`（非 GET）**一律挡住** —— MCP 工具会写数据，只读档下不该放行 |
| `GET /mcp` | 明确回 **405**：只实现 Streamable HTTP 的 POST 一半（平台不主动向 agent 推流），并回一句"工具调用请用 POST /mcp" |

**留痕落盘**：`/api/mcp/calls` 的记录同时写 `AC_DATA_DIR/mcp_calls.json`（最多 300 条）——
平台**重启之后**"它来没来过"不会消失（否则用户会以为它一次都没来）。

## 3. 判据（都真跑，不看源码文本）

| 判据 | 判什么 |
|---|---|
| `python tools/mcp_tools_check.py` | **15 格**：临时实例（换端口+换数据目录）上，一个真 MCP 客户端逐个调 14 个工具，每条都要**拿到真数据/真落库**（建间带任务、发言进讨论区、回执说清结果），最后核对留痕 |
| `python tools/mcp_loop_check.py --peer-url …` | 15 格：从注册到交回执的**全链路**（含成员自检、协议型闸门） |
| `agent_community/tests/test_mcp_http_server.py` | 4 条：初始化给会话、工具表一处真相、`tools/call` 真回数据且不自锁、**留痕空＝它没来过** |
| `agent_community/tests/test_mcp_client_config.py` | 5 条：配置无机器坐标、`http` 写法的 `url` 真能 initialize、stdio 写法真能 spawn |

实测（2026-10-25）：`mcp_tools_check` **15/15 PASS**；外部客户端以 `demo-desktop-agent` 调 `status`
→ 拿到平台真实状态，`/api/mcp/calls` 里出现 `('tools/call','demo-desktop-agent','status',True)`。

## 4. 边界（如实说，不硬撑）

- **平台不代对方生成任何内容**，也不假装它的模型说过话；
- 它一次都没来调过 ⇒ `/api/mcp/calls` 是空的，界面就必须显示「0 次」；
- 只支持 MCP 的 `tools` 能力；`resources` / `prompts` / `sampling` **未实现**（客户端来问就如实说不支持）。

## 5. ★「发进它会话」的现行口径：**让它们自己发**（老板 2026-10-25）

> 老板原话：「**可以在外端 agent 各自会话里面发消息的，最好是让它们发**」。
> ⇒ 平台**不代写**它的会话、不改它的库；平台只做这几件事：

```
① 平台/界面放一句：「请你在自己的会话里说这句」        POST /api/mcp/session-notes {text}
② 它自己取：pending_session_notes                     （MCP 工具，它来调）
③ 它自己发（用它自己的应用/工具）
④ 它回报「我发了」：report_session_sent(note_id, text)  POST /api/mcp/session-notes/{id}/sent
⑤ 平台看得到：GET /api/mcp/session-notes               （含时间、状态、是谁来报的）
```

**口径写死三条**：

1. **发送动作是它的，平台一个字都不代写**（`V101`/`V103` 的教训：代写的"成功"是平台在做梦）；
2. 它的回报是**自报**（不是硬证据）⇒ 接口与界面都必须写成「**它说它发了**」，
   不许写成"平台发出去了"（`report` 的返回里带 `scope` 把这点写明）；
3. 落点是 `AC_DATA_DIR/session_notes.json`（换目录就跟着走，不进仓库），最多保留 200 条。

对应工具（`agent_community/mcp_server.py`，HTTP/stdio **同一份**、同一个启用口径 `active_tools()`）：
`pending_session_notes` / `report_session_sent` —— 现在工具面是 **14 个**。

## 6. 工作过程上报（`report_progress`）：用户最直观地看到它在干活

> 老板 2026-10-26：「能够实现外端 Agent 内部在工作时于会话中发消息的最好加上，
> 这样用户可以最直观地看到工作过程」。

```
它干活时 → report_progress(text="我已经把 X 改完，正在跑自检", task_id?, kind=step)
        → POST /api/progress（平台只记录它自己报的，不替它演过程）
        → 界面「工作过程」按时间一条条出现（时间 + 一句话 + kind）
```

- `kind`：`step`（一步）/ `note`（备注）/ `block`（卡住了，写清卡在哪）/ `done`（收尾）；
- **顺手也要写进它自己的会话**：引导里要求它「有 `session_post` 那类工具就同时写一份」，
  这样用户在自己的会话里也能看到过程 —— 那部分**由它发**，平台不代写；
- 引导（可直接复制给它）：`GET /api/harness/{id}/progress-guide?format=md`；
- 落点 `AC_DATA_DIR/progress.jsonl`（追加写、上限 500 条），换目录跟着走；
- 工具面因此是 **14 个**；判据 `tools/mcp_tools_check.py` **21 格**（含「报一步 → 平台看得到」）。
