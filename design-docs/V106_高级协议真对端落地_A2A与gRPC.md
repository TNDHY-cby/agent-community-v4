# V106 · 高级协议真对端落地（A2A / gRPC / ACP / HTTP / WS）：真对端把活干完，平台查得到

> 老板 2026-10-25：「MCP 之外的高级协议同样收口」。
> **状态**：已落地（判据 `tools/protocol_peer_check.py` **14 格全过**）。
> **谁写**：DSH（后端）

## 1. 之前到哪一步，现在到哪一步

| | 之前 | 现在 |
|---|---|---|
| 判据 | `protocol_call_check`：能连、能列协议（**接口级**） | `protocol_peer_check`：**真对端派活 → 平台真建任务 → 同一条查得到 → 真回报** |
| A2A | `tasks/send` 回了 id，但那个 id 是**工作间**号；`tasks/get` 按它查不到 | 回**任务 id**；`tasks/get` 查得到同一条；`tasks/cancel` 有明确结果 |
| gRPC | `SubmitTask` 回的 `task_id` 是**工作间**号 ⇒ `GetTask` 永远 `task not found` | 回**任务 id**；`GetTask` / `ReportResult` 全通 |

## 2. 判据（14 格，真跑，一次性临时实例）

```
A2A ：Agent Card 可发现 → 不带令牌必被拒（fail-closed）→ tasks/send → 平台真建出任务
      → tasks/get 同一条 → tasks/cancel 有明确结果
gRPC：ListProtocols → RegisterHarness（真回令牌）→ SubmitTask（真建任务）
      → 平台侧查得到 → GetTask 同一条 → ReportResult 被接受
```

跑法：`python tools/protocol_peer_check.py`（自起临时平台 + 临时 a2a/grpc 进程，跑完删目录）。

## 3. 这一轮抓出来的三个真缺陷（都修了）

| # | 缺陷 | 后果 | 修法 |
|---|---|---|---|
| ① | A2A `tasks/send` 回的 `id` 是**工作间 id**（`_task_to_a2a` 第一参就是 ws_id） | 客户端拿自己得到的 id 回查**永远查不到**；A2A 的"任务"语义是假的 | `_task_to_a2a(..., a2a_id=任务号)`；`ws_id` 放 metadata（产物清单仍按它取） |
| ② | gRPC `SubmitTask` 回的 `task_id` 是**工作间 id** | `GetTask` 一律 `task not found`；回报 `ReportResult` 也被带歪 | 回 `resp["task_id"]` |
| ④ | **外部 WS 客户端收不到任何广播**（进程内 TestClient 能收到） | 前端实时更新形同虚设 | -m 起服务时模块是 __main__，routers 的 from ..server import ... 又导入一份 ⇒ **两份 ws_clients**：/ws 加进前者、bcast_to_clients 遍历后者（永远空）。修法：server.ws_registry() 收成**一处真相**（优先 __main__ 那份）；守卫 	ests/test_ws_broadcast_registry.py（含**真进程 + 手写 WS 客户端**的行为级判据） |
| ③ | 两个适配器"派活"都只建**空工作间**（`/api/workshop` 不认 `task_id`、也不建任务） | 与 MCP 那条同一个洞：看着派了，其实没任务 | `/api/workshop` 认 `task_id`（客户端指定就建/复用，幂等）+ `create_task`；两个适配器都改成带 `create_task` |

附带一条**口径**（两处入站鉴权一致）：A2A / gRPC 的**每个入站请求**都要求
`Authorization: Bearer <agent_token>`（`identity.check_edge_bearer`，fail-closed）。
真实流程是：**先在平台上登记 harness 拿到令牌**，再拿令牌按协议干活 —— 判据里就是这么走的，
并额外钉了一格"**不带令牌必须被拒**"。

## 4. 边界（如实说）

- ACP 不在本判据里：它是**平台去拉起对方进程**（出站），已有 `tools/acp_core_check.py` 真跑一轮；
- A2A 的 `message/send` / 流式推送、gRPC 的流式 RPC **未实现**（客户端来问就如实说不支持）；
- 判据跑在临时实例上，**不碰真实数据**；跑完删目录。
