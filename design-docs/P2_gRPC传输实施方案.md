> **状态：暂缓（2026-10-02 决策）——本方案已归档，尚未实施。**
> 暂缓理由：需引入 grpcio / grpcio-tools 重依赖，与项目「零第三方依赖 / 轻量本地部署」基调冲突，
> 且当前**无真实外部 gRPC 客户端需求**。platform/protocol.py 的 TransportType.GRPC 枚举保持预留，
> 未来按需实施时本方案可直接复用，无需重写。
> 归档人：开发 Agent ｜ 决策依据：design-docs/取长补短成果回收设计.md §3.5、§10.4

---

# P2 gRPC 传输实施方案（agent_community-v4）

> 生成时间：2026-10-02
> 状态：待实施（方案定稿）
> 依据：取长补短任务档案 /《底层取长补短方案.md》P2 遗留项 / 源码勘察（protocol.py、adapters.py、harness_adapter.py）
> 原则：最小侵入、向后兼容、与既有 HTTP/WS/PIPE 传输并存

---

## 一、背景与目标

平台多型 Transport 已定义 `TransportType`（WS/HTTP/SSE/PIPE/**GRPC**，protocol.py:20-25），GRPC 枚举已预留但未实现。补齐 gRPC 传输，实现：

1. 外部 gRPC 客户端可向平台发起任务派发/查询（标准 gRPC 通道，替代或并存 HTTP）
2. harness 可通过 gRPC 长连接回报执行结果（适合流式/大批量场景）
3. 与 Avernet 多模式会话引擎（HTTP/WS/**gRPC**）对齐

**关键决策**：不改造 server.py 主线，沿用 P0 的"独立适配进程"模式——gRPC 作为**新传输前端**，内部仍转发 18920 既有 API，零改动核心协作逻辑。

---

## 二、现状勘察结论

| 位置 | 现状 | 与本方案关系 |
|------|------|--------------|
| `platform/protocol.py:20-25` | `TransportType.GRPC = "grpc"` 已定义 | 无需改，直接使用 |
| `platform/protocol.py` AgentEndpoint | `transport/url/priority/metadata` 字段齐备 | gRPC 端点可声明 `transport=grpc` + `url=host:port` |
| `platform/adapters.py:24` | `select_endpoint(card, preferred)` 按 transport 选端点 | 需补：preferred=GRPC 时解析 grpc 端点 |
| `platform/adapters.py:60/87/114` | 仅处理 `TransportType.HTTP` 分支 | 需补 GRPC 分支（发消息/查状态/取结果） |
| `platform/harness_adapter.py:217-225` | HTTP/WS/PIPE 三路连接分支 | 需补 GRPC 会话建立与消息传输 |
| `orchestrator.py:75` | 默认 `transport=TransportType.HTTP` | 保持默认，gRPC 按需指定 |

---

## 三、依赖引入

| 依赖 | 用途 | 安装 |
|------|------|------|
| `grpcio` | gRPC 运行时（服务端+客户端） | `pip install grpcio` |
| `grpcio-tools` | 从 .proto 生成 Python 桩代码 | `pip install grpcio-tools`（仅开发期需要） |

生成物（提交进仓库）：`*_pb2.py`、`*_pb2_grpc.py`（避免接力者再跑 protoc）。

---

## 四、协议设计（agent_community.proto）

```proto
syntax = "proto3";
package agent_community.v1;

service AgentCommunity {
  // 外部任务派发：等价 POST /api/harness/task-result 上游入口
  rpc SubmitTask(SubmitTaskRequest) returns (TaskAck);
  // 任务状态查询：等价 GET 任务详情
  rpc GetTask(GetTaskRequest) returns (TaskStatus);
  // 注册 harness：等价 POST /api/harness/register
  rpc RegisterHarness(RegisterRequest) returns (RegisterReply);
  // 消息入站（带 agent-token，走 P1 身份校验）
  rpc SendMessage(HarnessMessage) returns (MessageAck);
  // 结果回报：等价 POST /api/harness/task-result
  rpc ReportResult(TaskResult) returns (ResultAck);
  // 流式：讨论室消息订阅（预留，v1 可不实现）
  rpc StreamRoom(StreamRoomRequest) returns (stream RoomEvent);
}

message SubmitTaskRequest {
  string task_id = 1;
  string payload_json = 2;        // 与 HTTP 体同构，保证兼容
  string agent_token = 3;         // P1 身份凭证，无则放行（兼容旧 harness）
  map<string, string> meta = 4;
}
// …其余 message 与平台既有 Pydantic 模型字段一一对应，payload_json 承载复杂结构
```

**兼容策略**：gRPC message 只承载 `task_id + payload_json + agent_token + meta` 信封，复杂业务结构继续走 JSON 序列化——与 HTTP 接口共用同一数据模型，零协议漂移。

---

## 五、代码改动设计

### 5.1 新增 `agent_community/platform/grpc_gateway.py`（独立进程，端口 9105）
- 实现 `AgentCommunityServicer`（grpcio server，ThreadPoolExecutor）
- 内部复用现有 HTTP 客户端调用 18920：register / message / task-result / 任务查询
- 入站携带 agent-token 时按 P1 规则校验（401/403 语义映射为 gRPC 状态码：UNAUTHENTICATED / PERMISSION_DENIED）
- 启动方式：`python -m agent_community.platform.grpc_gateway --port 9105 --api http://127.0.0.1:18920`

### 5.2 改动 `platform/adapters.py`
- `select_endpoint`：`preferred=TransportType.GRPC` 时优先返回 `transport=grpc` 端点
- 消息发送/状态查询/取结果：新增 GRPC 分支，调用生成的桩 `AgentCommunityStub`

### 5.3 改动 `platform/harness_adapter.py`
- 连接分支 `elif t == TransportType.GRPC`：建立 grpc channel（支持 insecure_channel，TLS 留 metadata 字段扩展）
- 会话 transport 标记 `TransportType.GRPC`，心跳语义复用现有 pending-tasks 轮询

### 5.4 不改动
- `server.py` 主线（gRPC 网关转发既有 API，P1 校验/审计埋点天然生效）
- `orchestrator.py` 默认 transport（保持 HTTP，gRPC 仅按端点声明选用）
- 既有 HTTP/WS/PIPE 传输全部保留

---

## 六、实施步骤

1. `pip install grpcio grpcio-tools`，写 `agent_community/platform/proto/agent_community.proto`
2. `python -m grpc_tools.protoc -I proto --python_out=. --grpc_python_out=. proto/agent_community.proto`，提交生成桩
3. 实现 `grpc_gateway.py` 服务端（映射 18920 API + P1 身份校验）
4. 改 `adapters.py` / `harness_adapter.py` 补 GRPC 分支
5. `py_compile` 全部改动文件 + WMI 重启 18920 复测
6. 启动网关 9105，gRPC 冒烟：
   - RegisterHarness → 返回注册结果（与 HTTP 同语义）
   - SendMessage 无 token → UNAUTHENTICATED（已签发 harness）；旧 harness 放行
   - SubmitTask → TaskAck → ReportResult → 平台可见
   - StreamRoom（若实现）订阅讨论室事件流
7. `test_` 前缀数据清理

---

## 七、验证矩阵

| 用例 | 期望 |
|------|------|
| gRPC RegisterHarness | 返回 64 位 agent-token（幂等复用） |
| gRPC SendMessage（带错 token） | PERMISSION_DENIED（对应 403） |
| gRPC SendMessage（无 token，旧 harness） | 放行（对应兼容策略） |
| SubmitTask + ReportResult | 平台任务状态流转正常 |
| HTTP 并行回归 | 18920 既有 114 端点不受影响 |
| 审计 | gRPC 入口操作在 /api/audit 可见（经网关转发天然埋点） |

---

## 八、风险与回退

| 风险 | 缓解 |
|------|------|
| grpcio 引入运行时依赖，污染轻量部署 | 网关独立进程、可选启动（无 gRPC 需求可不拉起）；requirements 分 optional 组 |
| proto 与 Pydantic 模型漂移 | 信封式设计（payload_json），业务模型仅一处定义 |
| gRPC 网关故障影响主链路 | 网关独立端口，故障只影响 gRPC 客户端，HTTP 主链路零影响 |
| 回退方案 | 删除 grpc_gateway.py + 桩文件，还原 adapters/harness_adapter 分支（改动集中、可逆） |

---

## 九、与现有体系关系

- 与 P0 A2A 层（9104）：A2A 走 JSON-RPC over HTTP；gRPC 走 protobuf 二进制，两者并存，面向不同客户端生态
- 与 P1 身份凭证：gRPC 入站复用 agent-token 校验，天然继承 401/403 语义
- 与 P2 审计：经网关转发调用 18920 时自动落入既有审计埋点
- 端口规划：18920（平台主服务）/ 9104（A2A）/ 9105（gRPC 网关，新增）

---

## 十、待办清单

- [ ] 引入 grpcio / grpcio-tools 依赖
- [ ] 编写并编译 agent_community.proto（提交生成桩）
- [ ] 实现 grpc_gateway.py（9105）
- [ ] adapters.py / harness_adapter.py 补 GRPC 分支
- [ ] py_compile + 重启 18920 + gRPC 冒烟回归
- [ ] 更新《底层取长补短方案.md》标记 P2 gRPC 为"已实施"
- [ ] 更新交接说明并重新打包开发资料 zip
*（内容由AI生成，仅供参考）*
