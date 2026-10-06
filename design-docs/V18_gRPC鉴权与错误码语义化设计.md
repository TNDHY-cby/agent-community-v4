# V18 · gRPC 层鉴权与错误码语义化设计

> 状态：**待拍板**（2026-10-06 出稿；按协作记录 22 号指令「只出设计稿、不写码」，负责人拍板后方可实施）
> 触发事由：2026-10-06 P2 gRPC 落地后的**真机端到端实测**当场暴露两个缺陷（证据见 §1）
> 关联：`design-docs/P2_gRPC传输实施方案.md`（2026-10-02 暂缓稿，本次已完成其 §5.1 的服务端与 §6 步骤 1-3、6 的部分）
> 本次范围：**只动 `agent_community/grpc_gateway.py` 与其测试**；不改 proto、不改 REST 层、不改 P1 实现
> 落地目标：gRPC 客户端拿到的**错误码有语义、身份凭证能生效**，且与 REST 的 P1 校验**单一来源不重复实现**

---

## 1. 问题（有实测证据，不是推测）

gRPC 网关 2026-10-06 落地后，用真 gRPC 客户端打真平台（`网关:9105 → 平台:18920`，只读 RPC）实测：

| # | 缺陷 | 证据 | 后果 |
|---|---|---|---|
| **D1** | **上游错误原样冒成 `UNKNOWN`** | `GetTask` 查询不存在的任务 → 上游 404 → 客户端收到 `StatusCode.UNKNOWN - "Exception calling application: HTTP Error 404: Not Found"` | 客户端**无法按语义分支**：404（不存在，别重试）、401（凭证问题）、403（被拒）、500（平台侧故障）在 gRPC 侧**全部长得一样**，重试策略与告警都无从谈起 |
| **D2** | **`agent_token` 从未被转发** | 网关 `SendMessage` 只把 `payload_json` POST 出去（`grpc_gateway.py` 的 `_post` 只带 Bearer 访问 token），proto 里的 `agent_token` 字段**被丢弃**；而平台侧 `POST /api/harness/message` 与 `/api/harness/task-result` 都挂着 P1 校验（`routers/harness_messaging.py:30` 与 `:69` → `_harness_identity_check` → `identity.check_request_token`） | **已签发 token 的 harness 走 gRPC 会直接被 401 拒绝**（静态确认：网关未产出 `x-agent-token` 头；`identity.py:77` 规定缺头即 401）。也就是说 gRPC 这条路对"启用身份凭证的 harness"**事实上不可用** |
| **D3** | 连接类失败同样塌成 `UNKNOWN` | 网关 `_call` 用 `urllib.request.urlopen`，`URLError`/超时**无捕获** → 冒泡成 `UNKNOWN` | 平台没起/端口不通，与"业务错误"不可区分（应为 `UNAVAILABLE`） |

> **D2 的端到端行为待基线测量**（§9 步骤 0）：静态代码已证明网关不产 `x-agent-token` 头，
> 但"真平台 + 真已签发 token 的 harness"这条链上是否确实 401，实施前需实测一次（安全做法见 §9）。

---

## 2. 现状勘察（真实代码坐标）

### 2.1 平台侧：两层身份，互不相同（必须分清）

| 层 | 凭证 | 提取 | 校验点 | 拒绝码 |
|---|---|---|---|---|
| **服务访问 token**（进程级） | `Authorization: Bearer <token>` 或 `X-API-Key` | `server.py:224 _extract_token` | `server.py:249/252` | **403**（服务未配 token 却非本地访问）/ **401**（token 无效） |
| **P1 Agent-Token**（每 harness） | `x-agent-token` 头（`identity.AGENT_TOKEN_HEADER`） | `identity.extract_token_from_headers` | `identity.check_request_token` → `routers/harness_common.py:69 _harness_identity_check` | **401**（该 harness 已签发 token 但请求缺头，`identity.py:77`）/ **403**（token 不匹配，`identity.py:79`） |

P1 的**向后兼容开关**（`identity.py:73`）：目标 harness **未签发 token** 时一律放行 —— 这条必须原样继承，网关不得自行加严。

**P1 的挂接点实测只有两处**（决定哪几条 RPC 受影响）：

| gRPC RPC | 背后 REST 端点 | 是否过 P1 | 主要失败语义 |
|---|---|---|---|
| `SendMessage` | `POST /api/harness/message`（`harness_messaging.py:19`，校验在 `:30`） | **是** | 401 / 403 / 404（harness 未注册） |
| `ReportResult` | `POST /api/harness/task-result`（`harness_messaging.py:60`，校验在 `:69`） | **是** | 401 / 403 / 404 |
| `RegisterHarness` | `POST /api/harness/register`（`harness_register.py:195`） | 否（签发动作本身） | 400 / 500 |
| `SubmitTask` | `POST /api/workshop` | 否 | 400（`mode` 空 / 并行度<1）、404（工作间不存在）、**429（工作间达上限，`workshops_lifecycle.py:97`）** |
| `GetTask` | `GET /api/task/{id}` | 否 | **404**（`server.py:750/1087`「任务不存在」） |
| `ListProtocols` | `GET /api/protocols` | 否 | 恒 200 |

### 2.2 网关侧现状

- `_call` / `_post`：`urlopen` 非 2xx 直接抛 `HTTPError`，**无人捕获** → gRPC 层 `UNKNOWN`（D1/D3）。
- `HarnessMessage.agent_token` / `SubmitTaskRequest.agent_token` / `TaskResult.agent_token` **三个字段已存在于 proto 中但未被使用**（D2）。
- 只有 `Authorization: Bearer <AC4_TOKEN>`（服务访问层），由 `_headers()` 统一注入。

---

## 3. 目标与非目标

**目标**

1. 六条 RPC 的失败全部映射到**有语义的 gRPC 状态码**，`details` 携带可诊断但不泄漏内部实现的信息；
2. `agent_token` 从 gRPC 请求**传递**到 REST `x-agent-token` 头，使已启用 P1 的 harness 走 gRPC 可用；
3. 校验**仍只发生在平台一处**（`identity.check_request_token`），网关不做第二次 HMAC；
4. 截止时间（deadline）语义贯通：客户端设了 deadline，上游慢时给 `DEADLINE_EXCEEDED` 而不是干等。

**非目标（本次明确不做）**

- ❌ 不改 `.proto`（三个 `agent_token` 字段够用；无需重新生成桩）
- ❌ 不改 `server.py` / REST 层 / `identity.py`（P1 逻辑与向后兼容开关原样保留）
- ❌ 不做 TLS（v1 仍 `insecure_channel`，本地/内网部署；风险见 §10）
- ❌ 不做流式 RPC（`StreamRoom` 仍是 P2 旧稿的预留项）
- ❌ 不做 A2A 9104 的鉴权（那是候选 C，另轮设计）
- ❌ 不在网关侧新增配置键（沿用现有 `AC4_BASE_URL` / `AC4_TOKEN`）

---

## 4. 设计 A：`agent_token` 的传递与校验位置

### 4.1 传递（网关侧，新增）

```
gRPC 请求                     网关                              平台 REST
──────────────────────────────────────────────────────────────────────────
SendMessage(payload_json,      1) 取 request.agent_token
  agent_token="abc...")   ───► 2) 非空 → 加头 x-agent-token: abc...
                               3) 空 → **不加头**（关键）
                               4) POST /api/harness/message
                                          ─────────────────────► identity.check_request_token
                                                                      │ 缺头(已签发) → 401
                                                                      │ 不匹配        → 403
                              5) 网关只翻译状态码，不判断对错 ────────► 放行 → 200
```

**规则（四条，逐条可测）**：

| # | 规则 | 依据 |
|---|---|---|
| R1 | 三个带 `agent_token` 字段的 RPC（`SendMessage` / `SubmitTask` / `ReportResult`）一律：字段非空 → 注入 `x-agent-token` 头；为空 → **不注入** | 向后兼容（`identity.py:73`：未签发 token 的 harness 走 gRPC 也必须照常可用） |
| R2 | `RegisterHarness` / `GetTask` / `ListProtocols` 不涉及该字段，行为不变 | 消息里没有该字段 |
| R3 | **网关不做 HMAC 校验**：不在网关侧 import `identity`、不算 `hmac.compare_digest`，只**透传** | 单一校验来源 = 平台；避免"两处实现必然漂移"（项目已有 `_harness_identity_check` 统一入口的先例） |
| R4 | 平台返回 401/403 时，网关**原样翻译为 gRPC 对应码**，不改写成别的码 | 语义不失真；R3 的自然结果 |

> **R1 的决策点**（§11 拍板项 ①）：`SubmitTask → POST /api/workshop` 当前**不挂 P1 校验**，
> 但字段存在。本稿建议**照 R1 一并转发**（一致性 + 未来该端点加 P1 时网关无需再改），转发本身无副作用。

### 4.2 为什么不是"网关先校验一遍"

| 方案 | 判定 |
|---|---|
| 网关本地再算一次 HMAC | ❌ 两处真源必然漂移；还要把 `agent_token_secret` 分发到网关进程（扩大密钥暴露面） |
| 网关只透传，平台唯一校验 | ✅ **选定**。与"REST 的 P1 天然生效"的设计初衷一致，且向后兼容开关（未签发放行）自动继承 |

---

## 5. 设计 B：六条 RPC 的错误码映射表

### 5.1 上游 HTTP 状态 → gRPC 状态码（主表）

网关新增一个 **`_grpc_status_from_upstream(status:int, body:dict|str)`** 纯函数（可单测、可变异验证）：

| 上游 HTTP | gRPC `StatusCode` | 真实触发场景（已核对坐标） |
|---|---|---|
| 400 | `INVALID_ARGUMENT` | `POST /api/workshop` 的 `mode` 为空 / 并行度<1；`PATCH capabilities` 非字符串数组 |
| 401 | `UNAUTHENTICATED` | 服务访问 token 缺失或无效（`server.py:252`）；P1 缺 `x-agent-token` 头（`identity.py:77`） |
| 403 | `PERMISSION_DENIED` | 服务未配 token 却非本地访问（`server.py:249`）；P1 token 不匹配（`identity.py:79`）；V-14 策略闸门拒绝；重复提交/过期（`harness_bridge.py:101/105`） |
| 404 | `NOT_FOUND` | 任务不存在（`server.py:750/1087`）；harness not found；工作间不存在 |
| 409 | `ALREADY_EXISTS` | 资源冲突类（预留；当前未见直连端点返回） |
| 429 | `RESOURCE_EXHAUSTED` | 工作间达上限（`workshops_lifecycle.py:97`）；AI 预算熔断若以 429 呈现 |
| 500 | `INTERNAL` | 桥脚本不存在、inbox 不可写等平台内部错误 |
| 501/505 | `UNIMPLEMENTED` | 平台返回未实现 |
| 502/503/504 | `UNAVAILABLE` | 上游网关/不可用 |
| 其他未列 | `INTERNAL`（兜底，details 记 `upstream_status=<n>`） | 防御性 |

### 5.2 非 HTTP 状态的失败（网关自身触发）

| 失败 | gRPC `StatusCode` | 说明 |
|---|---|---|
| `URLError` / `ConnectionRefused` / DNS 失败 | `UNAVAILABLE` | 网关→平台不可达（D3 修复）；**可重试**语义 |
| 客户端 deadline 到期 | `DEADLINE_EXCEEDED` | `context.time()` 拿到剩余时间并作为 `urlopen(timeout=...)` 传下去；到点即断 |
| 网关自身超时（客户端未设 deadline，网关用默认上限） | `UNAVAILABLE` | 建议值：与现有默认一致（写请求 60s / 读请求 30s，沿用 `_post`/`_call` 的现值）；语义"稍后重试" |
| `payload_json` 不是合法 JSON | `INVALID_ARGUMENT` | 网关侧解析失败，不发往上游；details = `payload_json 解析失败：<定位信息>` |
| 上游返回非 JSON 体 | 按 HTTP 状态码映射；details 只带前 N 字符的清洗文本 | `_post` 现有 `{"raw": raw}` 分支保留 |

### 5.3 逐 RPC 的主要映射（应用层视角）

| RPC | 典型成功 | 典型失败 → 期望码 |
|---|---|---|
| `RegisterHarness` | `RegisterReply(payload_json=含 agent_token)` | 400→`INVALID_ARGUMENT`；500→`INTERNAL` |
| `SendMessage` | `MessageAck(ok=true)` | **401→`UNAUTHENTICATED`**；**403→`PERMISSION_DENIED`**；404（未注册）→`NOT_FOUND`；500→`INTERNAL` |
| `SubmitTask` | `TaskAck(ok=true, task_id=ws_xxx)` | 400→`INVALID_ARGUMENT`；404→`NOT_FOUND`；**429→`RESOURCE_EXHAUSTED`** |
| `GetTask` | `TaskStatus(status=...)` | **404→`NOT_FOUND`**（D1 的直接修复目标） |
| `ReportResult` | `ResultAck(ok=true)` | 401→`UNAUTHENTICATED`；403→`PERMISSION_DENIED`；404→`NOT_FOUND` |
| `ListProtocols` | `ProtocolsReply(payload_json=协议目录)` | 恒 200；连接失败→`UNAVAILABLE` |

### 5.4 `details` 的卫生规则

1. **优先取上游 JSON 体的 `error` / `detail` 字段**（平台各端点高度一致地返回 `{"error": ...}`）；
2. 超长截断（建议 256 字符），超出部分以 `…` 结束；
3. **不得把本地绝对路径 / Traceback / token 透出**（与"对外文档不泄内部信息"的既有纪律一致）：
   实现为对 `details` 做一次后处理（剔除盘符路径形如 `[A-Za-z]:\\` 与 `Traceback` 行），并有专测钉住；
4. `details` 中带上游状态码（如 `upstream=404`），便于排查时定位是哪一层拒的。

### 5.5 实现形态（对现有代码的最小改动）

- 新增私有 helper：`_request(method, path, payload, timeout) -> (status:int, body:dict|str)`——
  把 `urlopen` 的成功与 `HTTPError` **统一收成状态码+体**（不再抛异常外泄）；
- 每条 servicer 方法用 `try/except` 包一层，失败时 `context.abort(code, details)`（gRPC Python 的标准写法，
  比"返回空 message 再 set_code"更不易漏）；
- 纯函数 `_grpc_status_from_upstream` **独立可测**（变异验证的靶点）。

---

## 6. 配置与运行要求

| 项 | 值 | 说明 |
|---|---|---|
| 网关启动 | `python -m agent_community.grpc_gateway --port 9105 --api http://127.0.0.1:18920` | 不变 |
| `AC4_TOKEN` | **必须**与平台服务访问 token 一致 | 否则网关自身先被 401（该 token 已由 `start_18920_v26.ps1` 单一来源管理，网关从**同一处读取**，不另存副本） |
| `AC4_BASE_URL` | 默认 `http://127.0.0.1:18920` | 不变 |
| 新增配置键 | **无** | 本稿不引入新键，降低面 |

> 与候选 A（网关纳入常驻/自启）的衔接：届时自启脚本读 token 的方式**沿用看门狗的既有做法**
> （从启动脚本读，不重复造），本稿不展开。

---

## 7. 测试扩展方案（对现有 10 条冒烟的扩展）

现有 `agent_community/tests/test_grpc_gateway.py`（10 条，2026-10-06）的**假 REST 单元测试结构原样保留**，
在其上做三类扩展；**现有 10 条必须全程保持全绿**（作为零回归门槛）。

### 7.1 新增：错误码映射（表驱动）

假 REST 改为"按路径返回可配置状态码"（fixture 加一个 `respond_with(path, status, body)` 钩子）：

| 用例组 | 断言 |
|---|---|
| `test_upstream_400_maps_invalid_argument` | `e.code() == INVALID_ARGUMENT` 且 details 含上游 `error` 文本 |
| `test_upstream_401_maps_unauthenticated` | `UNAUTHENTICATED` |
| `test_upstream_403_maps_permission_denied` | `PERMISSION_DENIED` |
| `test_upstream_404_maps_not_found`（核心，D1） | `NOT_FOUND`，details 含上游错误信息与 `upstream=404` |
| `test_upstream_429_maps_resource_exhausted` | `RESOURCE_EXHAUSTED` |
| `test_upstream_500_maps_internal` | `INTERNAL` |
| `test_upstream_503_maps_unavailable` | `UNAVAILABLE` |
| `test_unknown_status_falls_back_internal` | 未列状态（如 418）→ `INTERNAL` 且 details 记 `upstream_status=418` |
| `test_connection_refused_maps_unavailable`（D3） | `BASE_URL` 指向不通端口 → `UNAVAILABLE` |
| `test_invalid_payload_json_maps_invalid_argument` | 坏 JSON → `INVALID_ARGUMENT` 且**不发往上游**（假 REST 侧零调用记录） |

### 7.2 新增：token 转发（D2）

| 用例 | 断言 |
|---|---|
| `test_agent_token_forwarded_as_header` | 假 REST 收到的 `x-agent-token` **等于**请求里的 `agent_token` |
| `test_empty_token_omits_header` | 字段为空 → **头不出现**（R1 向后兼容分支） |
| `test_gateway_does_not_verify_locally` | 传一个**假 token** → 网关**不拒绝**，照常转发；由假 REST 返回 401 时 → 客户端收到 `UNAUTHENTICATED`（证明"网关不校验、平台校验"成立，R3 可验证） |
| `test_list_protocols_and_gettask_untouched` | R2：这两条不注入任何 `x-agent-token` |

### 7.3 新增：deadline 与卫生

| 用例 | 断言 |
|---|---|
| `test_client_deadline_exceeded` | 假 REST 故意 sleep 2s，客户端 `timeout=0.5` → `DEADLINE_EXCEEDED` |
| `test_details_hides_local_paths_and_tracebacks` | 上游 body 含 `D:\...` 与 `Traceback (most recent call last)` → details 里**都不存在** |

### 7.4 变异验证（项目纪律：断言必须有判别力）

对 `_grpc_status_from_upstream` 与 details 清洗函数做变异（临时副本上动刀，不碰原文件），期望**全部 KILLED**：

| 变异 | 预期 |
|---|---|
| 把 404 映射改成 `INTERNAL` | 404 组测试红 |
| 401/403 全部并成一个码 | 相应组红 |
| 关掉 details 清洗 | 卫生用例红 |
| `_request` 不捕获 `HTTPError`（退回抛异常） | 连接类与 404 组红 |

### 7.5 真机端到端（只读优先）

| 步骤 | 期望 |
|---|---|
| 起网关 → 真平台；`GetTask(不存在)` | **`NOT_FOUND`**（当前是 `UNKNOWN`，可见修复前后差异） |
| `ListProtocols` | 仍 200（零回归） |
| 401 基线测量（§9 步骤 0 的安全做法） | 若某 harness 已签发 token，用它调 `SendMessage` 且**故意不带 token** → 期望 `UNAUTHENTICATED`（平台拒绝，**不产生消息副作用**）；若不存在这类 harness，则该步跳过并在报告注明 |

---

## 8. 对现有行为的影响面

| 对象 | 影响 |
|---|---|
| 现有 10 条冒烟 | **不变**（成功路径的断言不涉及错误码）；若有因错误码细化而变化的断言，逐条核对后更新并说明理由 |
| REST/P1/策略闸门 | **零改动**（网关只是翻译者） |
| proto 与生成桩 | **零改动** |
| 协议目录 | 零改动（gRPC 条目状态与端点逻辑已有 `_runtime_status` 管着） |
| 公开仓库 | 只新增本设计稿（拍板后）+ 网关改动（拍板后） |

---

## 9. 实施步骤（拍板后执行）

0. **基线测量（先于一切改动）**：查平台数据确认存在**已签发 token** 的 harness → 用真 gRPC 调 `SendMessage` 不带 token → 期望 401（拒绝即无副作用）；同时记录 `GetTask(不存在)` 的**当前**码（`UNKNOWN`）作为对照。
1. 实现 `_request`（统一收状态码+体）与 `_grpc_status_from_upstream`（纯函数）。
2. servicer 六条方法接入 `try/except → context.abort`；三条带 token 的方法加 `x-agent-token` 注入。
3. details 清洗（路径/Traceback 剔除 + 截断 + `upstream=` 标记）。
4. 跑 §7.1-7.3 新测试 + **现有 10 条全绿**；跑 §7.4 变异验证（目标 4/4 KILLED）。
5. 跑全量回归 `agent_community/tests`（现基线 632 passed / 10 xfailed）+ 根 `tests/`（66）。
6. 真机端到端复测 §7.5，对比基线记录差异。
7. 提交 → 发布（走既有闸门）→ 更新 CHANGELOG（下一版本条目，对齐 `__version__` 单一真源纪律）。

---

## 10. 风险与回退

| 风险 | 缓解 |
|---|---|
| 明文传输（`insecure_channel`，`agent_token` 与业务 payload 均明文） | v1 明确为**本地/内网**部署；已在 §11 拍板项 ③ 列为待决（是否要求 TLS） |
| 状态码映射把上游语义翻错 | 映射表是纯函数 + 表驱动测试 + 变异验证；`details` 恒带 `upstream=<n>` 便于事后对账 |
| `details` 清洗误删有效信息 | 只剔除"绝对盘符路径/Traceback 行"两类确定性模式，不做泛化正则；有专测钉住 |
| 回退 | 改动集中在 `grpc_gateway.py` 单文件：还原该文件 + 测试文件即回到 2026-10-06 现状（可逆、无数据、无配置键） |

---

## 11. 待拍板项（需负责人决策，故本稿不自行实施）

| # | 问题 | 建议 | 影响 |
|---|---|---|---|
| **①** | `SubmitTask` 是否也转发 `agent_token`（该端点当前不挂 P1 校验） | **转发**（一致性；未来端点加 P1 时网关无需再改） | 一行代码 + 一条测试 |
| **②** | `details` 是否透出上游原文（如「该 Harness 已启用 Agent-Token」） | **保留上游原文**（本地部署、便于排障）；已做路径/Traceback 清洗 | 若要求统一文案，映射时替换为固定文案即可 |
| **③** | v1 是否要求 TLS | **暂不做**（本地/内网 + 平台已有两层 token 机制） | 若要求：网关加 `--tls-cert/--tls-key`，proto 不变，属独立增量 |

---

## 12. 关联文件（拍板后的改动面）

| 文件 | 动作 |
|---|---|
| `agent_community/grpc_gateway.py` | 增改（`_request`、映射函数、token 注入、details 清洗、`context.abort` 接入） |
| `agent_community/tests/test_grpc_gateway.py` | 扩展（§7.1-7.3；现有 10 条保持全绿） |
| `agent_community/CHANGELOG.md`（发布副本） | 发布时补条目 |
| `HANDOVER.md` | 落地后记 §13.12 |
| **不改** | `.proto` / 生成桩 / `server.py` / `platform/identity.py` / `platform/routers/*` / 协议目录 |

*（内容由AI生成，仅供参考）*
