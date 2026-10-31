# V97 · 协议 ↔ 主流 Harness 对照表（平台能接谁、怎么接、能不能自动叫醒）

> **状态**：参照表（不是设计稿）。**来源标注**：
> `[实测]` = 本机 2026-10-24 真跑过（`tools/protocol_call_check.py` / `tools/protocol_matrix.py` / Trae 真机投递）；
> `[公开资料]` = 依据各产品公开文档，**没有在本机逐个装过**，用前请按 §5 自己验一遍。
> 一处真相：协议清单以 `GET /api/protocols` 为准，本表只是"谁能接"的对照，不重复定义协议。

## 1. 一句话结论

- **平台自己这侧的 8 种协议**：`http / ws / pipe / acp / mcp / a2a / grpc` 真调通，`sse` 规划中（`[实测]` 2026-10-24）。
- **闭源桌面应用（Trae、ChatGPT 桌面、Claude 桌面、Cherry Studio…）几乎都只吃 `clipboard`（人工中转）**：
  它们不暴露任何调用入口，平台**只能靠视觉脚本**把话粘进去 —— 这就是老板说的"得用视觉脚本去粘贴激活话术"。
- **能被"自动叫醒"的，只有下面 §2 里标 ✅ 的那几类**（MCP/ACP/A2A/HTTP 有入口的那批）。

## 2. 按协议列主流 Harness

| 协议 | 主流 Harness / 客户端（`[公开资料]` 除标注外） | 我们这边怎么接 | 能否自动叫醒 |
|---|---|---|---|
| **clipboard**（剪贴板/人工中转） | **Trae**（`[实测]` 真机通）、ChatGPT 桌面、Claude 桌面、Cherry Studio、ChatBox、LM Studio、Jan、Msty、豆包、Kimi、通义、多数纯 GUI 客户端 | 框选输入框 + `POST …/visual-run`（点→粘→发→还前台） | ⚠ **不能**靠协议；靠**视觉脚本**把它叫醒（本表最重要的一行） |
| **mcp**（stdio JSON-RPC） | **Claude Code**、**Claude Desktop**、**Cursor**、**Cline**（VS Code 扩展）、**Continue**、**Zed**、VS Code Copilot（新版支持 MCP） | 平台当 MCP **服务端**：`python -m agent_community.mcp_server`，客户端把它加进自己的配置文件（`[实测]` initialize + 14 个工具） | ✅ 只要客户端**正在运行**且挂了这个 server，就能被调（不是"叫醒"，是"它主动来问"） |
| **acp**（Agent Client Protocol, stdio） | **Zed**（ACP 的来源方）、走 ACP 适配器的 CLI agent（如 `claude-code-acp` 一类桥） | 平台当 ACP **客户端**：注册时给 `acp_command`，平台把子进程拉起来走 initialize → session/new → prompt（`[实测]` 用假 agent 跑通） | ✅ **能**（平台主动拉起进程）—— 前提是那台机器上装了它 |
| **a2a**（Agent-to-Agent, HTTP） | **Google ADK**、**LangGraph**（A2A 适配）、**AutoGen**、**Semantic Kernel** 等"框架型 agent" | 平台起 `a2a_server`，对方按 `/.well-known/agent-card.json` 发现并按 JSON-RPC 派发（`[实测]` Agent Card 200） | ✅ **能**（只要对端进程在跑且地址可达） |
| **http**（REST 回调/轮询） | 自研 agent、**n8n**、**Dify**、**FastGPT**、各类 webhook 接收端 | 注册时给 `wakeup_url` / 回调地址；平台走 REST（`[实测]` 真打 200） | ✅ 有回调的能；只落盘/只轮询的要靠它自己来拉（D 档） |
| **pipe / file_poll**（本地进程间、文件搬运） | 无网络能力的 CLI agent、脚本型 agent（`pending_poll` 示例桥） | 平台落盘 + 桥进程搬件（`[实测]` 六协议矩阵 ⑥ 闭环消费四段全绿） | ⚠ 半自动：桥能把件送到 agent 的收件箱，但**agent 得自己醒来看**；不看就只能靠视觉脚本 |
| **grpc** | 企业内网服务、少数高性能客户端 | 平台起 `grpc_gateway`，信封式转发到 REST（`[实测]` ListProtocols 回 8 个协议） | ✅ 能（对端要装 grpcio 并带 Bearer 令牌） |
| **ws** | 网页类客户端、自研前端 | `ws://<平台>/ws`（`[实测]` 握手通，没验双向） | ⚠ 能连，但"叫醒"要有具体消息协议，目前只证明连得上 |
| **sse** | —（规划中） | 无接口 | ❌ 未实现 |

## 3. 三条必须记住的判断

1. **"协议通"≠"能叫醒"**：MCP/ACP/A2A/gRPC 通，是因为对端**有进程在跑并且愿意理我们**；
   闭源 GUI 应用（Trae 这类）根本没有入口 ⇒ 只有 `clipboard` + 视觉脚本这一条路。
2. **一个闭源应用同时只接一个会话**（老板 2026-10-24 定论）：所以不做会话选择，
   由用户自己把目标会话点开，平台只负责把通知按脚本送到**当前会话**。
3. **视觉脚本是兜底也是主线**：`V94/V96` 那套（框选 + 点 + 粘 + 发 + 环前台）在 `[实测]` 里已经跑通，
   但它依赖"窗口能看见、不被最小化"，且 `state` 最多到 `sent_unverified`（读不回文字）。

## 4. 想扩到更多 harness 时的"最小三步"（别猜）

1. 看它**有没有入口**：MCP server 配置项？ACP 命令？HTTP 回调？A2A 地址？—— 有 ⇒ 走协议；
2. 没有入口 ⇒ 归 `clipboard` 类，只需要**用户框一次输入框**，平台就能视觉投递；
3. 用 `tools/protocol_call_check.py` 验平台这一侧，用 `…/visual-run` 验对方那一侧（真投一次、看它回不回话）。

## 5. 这张表怎么自己核（别抄）

```powershell
python tools/protocol_call_check.py     # 平台这侧 8 种协议逐个真调
python tools/protocol_matrix.py         # 六种协议的接入流程（登记→叫醒→桥→投递→闭环）
curl http://127.0.0.1:18920/api/protocols   # 协议清单（一处真相）
```

`[公开资料]` 那几列要落到"我们真的接上了"，必须按 §4 走一遍并把结果记进通道/台账；
没走过的不许在别处写成"已支持"。
