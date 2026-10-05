# V-16 设计稿：API 接入完善（远端模型清单 + 连通性自检）

> 状态：**§三 已实施（2026-10-04，随负责人"API 接口的插件不完善"反馈推进）；§四 待拍板**
> 日期：2026-10-04
> 触发：负责人反馈「接入 mimo 就不行了」；已先做漏洞审查（见 §一），**缺陷部分已修**，
> 本稿只覆盖**需要新增能力**的部分。
> 关联：`V14_安全治理策略引擎设计.md`（AI 调用本就在策略闸门内）
>
> **实施记录（2026-10-04）**：
> - 新端点 `GET /api/ai/models?base_url=` —— **实测 mimo 返回 9 个模型**
>   （`mimo-v2.5` / `mimo-v2.6-pro` / `mimo-v2.6-flash` …），两种 base_url 写法结果一致（归一化生效），
>   **响应不含密钥**（用服务端解密 key，前端只拿掩码）
> - 前端：打开设置自动拉一次 + base_url 失焦再拉；失败回落内置清单 + 黄字原因；
>   **「手动输入…」恒在**（§3.2 的死路出口）
> - 顺带修：**设置页保存会把掩码当真 key 覆盖**（`GET /api/config` 返回掩码 → 回填 → 保存）
>   后端加「含 `*` 即视为掩码、忽略并保留原 key」，前端改状态提示不回填值
> - **「无法调用」根因确诊**：`Unsupported model deepseek-v4-flash` ——
>   路径与 key 都对（归一化已生效），**只是模型名 mimo 不认**；
>   实测 `mimo-v2.6-pro` → **HTTP 200 ✅**
> - ⚠️ 修正原稿一处错误：**不是所有服务都有 `/models`**（我先打的 `{base}/models` 是 404），
>   正确路径是 **`{归一后 base}/v1/models`**；故 §3.2 的"失败回落 + 手动输入"是**必需品而非兜底**

---

## 一、审查结论（2026-10-04，已修部分）

| 级别 | 缺陷 | 处置 |
|---|---|---|
| P0-1 | `base_url` 未归一 —— 填 `…/v1` 会拼成 `/v1/v1/chat/completions` → **静默 404** | ✅ 已修（`_normalize_base_url`） |
| P0-3 | 已保存模型不在 `presets` 硬编码清单里 → `<select>.value` **静默失效**，下拉停在"请选择模型" | ✅ 已修（清单外补选项） |
| P1-4 | 保存失败仍返回 `success:true` 且无错误字段 → 用户收不到任何提示 | ✅ 已修（`provider_loaded`/`provider_error`） |
| P1-5 | `configured` 未算 model | ✅ 已修（`model_missing`/`model_effective`） |
| P1-6 | `GateProvider(CacheProvider(UsageProvider(...)))` 包装链不代理属性 → 界面看不到生效的 `base_url` | ✅ 已修（基类 `__getattr__` 透传） |
| **P0-2** | **模型清单只有前端硬编码 `presets`，且 OpenAI 兼容类型从不拉远端 `/models`** | 🔜 **本稿** |

**审查中自我纠正（须记）**：初判「保存会把模型配置冲掉（数据破坏）」**不成立** ——
`load_config()` 会合并 `DEFAULT_CONFIG`（`ai_model=deepseek-v4-flash`），
空提交回退到已保存值。P0-3 的**真实影响是「状态不一致 + 想换模型换不了」，不是数据丢失**。
已加用例 `test_blank_selection_does_not_wipe_saved_model` 锁住这个结论。

---

## 二、问题本质

前端 `index.html`：

```js
const presets = {openai:{url:'',models:['gpt-4o',...]}, deepseek:{...}};
const pre = presets[p] || {url:'', models:[]};   // ← 不认识的 provider 直接空清单
sel.innerHTML = '-- 请选择模型 --' + pre.models.map(...)
```

三个后果：
1. **新接入的服务必然空清单**（mimo 就是）—— 用户没有可选项
2. **清单里的模型名过期**（硬编码不会随服务更新）
3. **用户想输入清单外的模型**（自部署、微调名）**做不到**

而后端 `ai_provider` 也不拉远端 `/models` —— 全库唯一一处是
`local_ai.py:87` 拉 Ollama 的 `/models`，OpenAI 兼容路径**完全没有**。

---

## 三、方案

### 3.1 新端点：`GET /api/ai/models?base_url=&api_key=`

| 项 | 设计 |
|---|---|
| 行为 | 对 `{base_url}/models`（**先做归一化**，同 P0-1）发起 GET，带 `Authorization: Bearer` |
| 超时 | 5s（清单拉不到不该卡住设置页） |
| 成功 | `{"ok":true,"models":["…"],"source":"remote"}` |
| 失败 | `{"ok":false,"models":[],"source":"builtin","error":"…","fallback":true}` —— **永远返回 200 + 清单**，让前端可以无条件消费 |
| 安全 | 走 V-14 闸门 `network.egress`（出网）；**api_key 只在服务端用，不回传** |

**为何失败也返回 200**：设置页的唯一目标是"让用户能选到模型"。
拉取失败时回落硬编码清单 + 给出可读原因，比抛错让下拉空着更符合用途。

### 3.2 前端改动

```
用户填完 base_url（失焦/点"获取模型列表"）
  → GET /api/ai/models
  → 成功：用远端清单重建下拉，**保留已选值**（若在清单内）
  → 失败：保留 presets 硬编码清单 + 一行提示「清单获取失败：<原因>（可用下方手输）」
  → 无论成败：下拉末尾常驻一个「手动输入…」项，选中后变成可编辑 input
```

**「手动输入」是本稿的核心** —— 它让清单永远不是死路。

### 3.3 `presets` 降级为兜底

硬编码清单保留，但**角色从"唯一来源"变成"远端拉不到时的兜底"**。
并在远端成功后按 provider 缓存（`ai_model_cache`，TTL 10 分钟），避免每次开设置页都出网。

---

## 四、连通性自检（P1-4 的延伸）

保存成功 ≠ 能用。建议（可独立拍板）：

```
POST /api/config 时（ai_mode=remote 且 base_url+key+model 齐全）
  → 发一条极小 chat（max_tokens=1，不实际生成）
  → 成功：provider_verified=true
  → 失败：provider_verified=false + 可读原因（401 无效 key / 404 路径错 / 403 权限 / 超时）
```

前端据此显示 **✅ 已连通 / ⚠️ 保存了但连不上：<原因>** —— 用户不必"保存后自己去试"。

---

## 五、验证矩阵（草案）

| # | 用例 | 期望 |
|---|---|---|
| 1 | base_url 含 `/v1` | 请求 `/models` 与 `/chat/completions` 均**不出现 `/v1/v1/`** |
| 2 | 远端 `/models` 正常 | 下拉重建且**保留已选值** |
| 3 | 远端超时/404 | 返回 200 + `fallback:true` + 可读 error，**下拉不空** |
| 4 | 未配置 api_key 仍请求 | 仍尝试（有的服务允许匿名列模型），失败走兜底 |
| 5 | 清单外模型 | 「手动输入…」可提交并被保存 |
| 6 | 连通性自检 401 | `provider_verified=false` + 原因含"密钥" |
| 7 | 连通性自检成功 | `provider_verified=true`，前端显示 ✅ |
| 8 | 出网请求 | 经 V-14 `network.egress` 闸门，审计留痕 |
| 9 | 回归 | 全量测试通过，真实 `~/.agent_community/config.json` **指纹不变** |

---

## 六、非目标

- **不**做多 provider 并存/热切换（当前是单 provider 模型）
- **不**在前端缓存明文 api_key（沿用 DPAPI 落盘）
- **不**自动猜模型名（拿不到就让人手输，不猜）

---

## 七、待拍板

1. **3.1 拉取时机**：自动（base_url 失焦即拉）还是手动（按钮触发）？—— 自动更省事但会频繁出网
2. **四、连通性自检**是否一并做？（它会真的发一次 API 调用，可能计 1 次费用）
3. **3.3 缓存 TTL**（草案 10 分钟）是否合适

> 未拍板前，**已修的 P0-1/P0-3/P1-4/P1-5/P1-6 可先合入**（缺陷修复，不属新能力）。
