# V60 · 平台 AI 健康字段（`/api/status` 增 `ai` 段）设计（2026-10-20）

> 需求来源：前端通道 **201**（`〔接口变更申请〕平台 AI 健康字段`）——
> 大厅左下角「状态：离线」那盏灯**写死了三个 agent id**（`ollama-qwen25` / `ollama-deepseek` / `example-bridge`）
> 去探 `/api/agents?online_only=true`：**只有"本机 Ollama + 示例桥"那台机器**才显示正确，
> AI 实际走云端 API 时**永远红灯误报**（老板机上就是如此）。老板已答「可以」。

## 1. 一句话

`GET /api/status` 增加一段 `ai`，让界面**不用猜 agent id** 就能如实显示"当前配置的平台 AI 通不通"。

## 2. 字段（新增，**不动既有字段**）

```json
"ai": {
  "provider": "openai",              // 配置里的 ai_provider（空串=没配）
  "model": "deepseek-v4-flash",      // 配置里的 ai_model
  "configured": true,                // provider 与 model 是否都非空
  "reachable": true,                 // true / false / null（null=没验过或结论已过期）
  "detail": "最近一次验证成功（3 分钟前）",
  "checked_at": "2026-10-20T09:31:00",  // 结论时间；没验过为空串
  "source": "cache",                 // cache / unverified / not-configured
  "age_seconds": 180                 // 结论距现在多久（没验过为 null）
}
```

## 3. **零成本铁律**（这条决定了实现方式）

`/api/status` 是**高频只读端点**（界面轮询、看门狗、CLI 自检都打它）。
⇒ **绝不在这个端点里主动探活**（那会每次轮询都花一次 token，还会把状态接口拖慢）。

所以 `reachable` 只来自 **V-16 已有的连通性自检缓存**（`ai_verify` 的 10 分钟 TTL 缓存）：

| 情形 | `reachable` | `source` | `detail` |
|---|---|---|---|
| provider 或 model 没配 | `null` | `not-configured` | 「还没配置平台 AI：填 provider 与模型；填完在「设置」里点一次"验证连通"」 |
| 配了但**从没验证过** | `null` | `unverified` | 「已配置（provider/model），但还没验证过连通——点一次"验证连通"」 |
| 有结论且在 TTL 内 | `true`/`false` | `cache` | 成功：「最近一次验证成功（N 分钟前）」；失败：「最近一次验证失败（N 分钟前）：<原因>」 |
| 有结论但**已过期** | `null` | `unverified` | 「上次验证是 N 分钟前，结论已过期——点一次"验证连通"」 |

> **为什么这样对产品更好**：界面显示的是"**我们确实知道的事**"，不是猜的。
> 没验过就显示"未知"，比红灯/绿灯都诚实。

## 4. 新增的一个小能力（供"验证连通"按钮与判据复用）

`platform/ai_verify.py` 增 `cached_verdict(provider_type, base_url, model, api_key, cfg=None)`：
按**同一套指纹逻辑**（模块内唯一实现）查缓存，返回
`{ok, status, detail, ts, age_s, fresh} ` 或 `None`。
⇒ 别的调用方不用自己拼 fingerprint（**一处真相**）。

## 5. 判据（`agent_community/tests/test_ai_health_field.py`，五点）

| # | 判据 |
|---|---|
| ① | `/api/status` 有 `ai` 段且七个字段齐全；**既有字段一个不少** |
| ② | **没配** ⇒ `configured=false`、`reachable=null`、`source=not-configured`、`detail` 说清"怎么配" |
| ③ | 配了但**没验证** ⇒ `reachable=null`、`source=unverified`（**不许**谎报 true） |
| ④ | 缓存里有**成功**结论 ⇒ `reachable=true`；有**失败**结论 ⇒ `reachable=false` 且 `detail` 带原因 |
| ⑤ | **不烧钱/不变慢**：两次 `/api/status` 的 `checked_at`/`age_seconds` 不因调用而刷新（证明没触发探活），且响应 < 500ms |

## 7. 主动验证端点 `POST /api/ai/verify`（2026-10-20 补，实施后追加）

> **为什么补**：§3 定了 `/api/status` **只读缓存、绝不探活**。可是"结论从哪来"必须有个入口 ——
> 原先只有"保存配置时（`ai_verify_on_save`）"和"启动时（`on_startup`）"两种自动触发，
> **没有一个"我现在就想验一次"的人工动作**。前端要做「验证连通」按钮，就缺这一块。
> （我在通道 202 里写过"调既有的 V-16 自检端点"——**那句是错的**，当时没核实；本端点补齐后才是事实。）

```
POST /api/ai/verify          （无 body；只接受本机来源）
```

| 情形 | HTTP | 返回要点 |
|---|---|---|
| 成功 | 200 | `ok=true, reachable=true, status, elapsed_ms, cached, checked_at, detail` |
| 失败（有原因） | 200 | `ok=false, reachable=false, reason, how_to_fix` |
| 没配 provider/model | 400 | `how_to_fix` 说清怎么配，**不发任何网络请求** |
| `ai_mode` 不是 remote | 400 | 说清"当前是本地/人工模式，没有联网接口可验" |
| 非本机来源 | 403 | 复用 `origin_allowed()`（一处真相） |

- **走 V-16 同一套缓存**（10 分钟 TTL）：重复点不重复烧钱，返回里的 `cached` 如实说明；
- 审计 `ai.verify`（谁、何时、结果）；
- **只读纪律**：用 `peek_config()`（延迟导入），不创建/改写配置文件。

### 判据（`agent_community/tests/test_ai_verify_endpoint.py`，6 条）

① 没配 ⇒ 400 且**零网络调用**；② 非 remote ⇒ 400；③ 成功/失败各如实（失败带 `reason` + `how_to_fix`）；
④ **验完之后 `/api/status` 的 `ai.reachable` 立刻有值**（整条链的契约：status 只读、verify 产生结论）；
⑤ 非本机 403；⑥ 验证过程**不创建配置文件**。

## 8. 交付（谁做什么）

- **后端（DSH，已完成）**：`/api/status` 的 `ai` 段、`POST /api/ai/verify`、`peek_config()`、判据与守卫。
- **前端（负责方）**：大厅左下角指示灯换数据源（`ai.reachable` 三态）+ 设置页「验证连通」按钮调
  `POST /api/ai/verify`，成功后无需刷新即可（下一次轮询 `/api/status` 就绿）。

