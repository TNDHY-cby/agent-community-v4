# V-16 设计稿：API 接入完善（远端模型清单 + 连通性自检）

> 状态：**§三已实施**（2026-10-04，随负责人"API 接口的插件不完善"反馈推进）；**§四待拍板**（2026-10-05 补定稿复核，见 §八）
> 日期：2026-10-04
> 触发：负责人反馈「接入某新服务就不行了」；已先做漏洞审查（见 §一），**缺陷部分已修**，
> 本稿只覆盖**需要新增能力**的部分。
> 关联：`V14_安全治理策略引擎设计.md`（AI 调用本就在策略闸门内）
>
> **实施记录（2026-10-04）**：
> - 新端点 `GET /api/ai/models?base_url=` —— **实测返回 9 个模型**
>   （`demo-model-2.5` / `demo-model-2.6-pro` / `demo-model-2.6-flash` …），两种 base_url 写法结果一致（归一化生效），
>   **响应不含密钥**（用服务端解密 key，前端只拿掩码）
> - 前端：打开设置自动拉一次 + base_url 失焦再拉；失败回落内置清单 + 黄字原因；
>   **「手动输入…」恒在**（§3.2 的死路出口）
> - 顺带修：**设置页保存会把掩码当真 key 覆盖**（`GET /api/config` 返回掩码 → 回填 → 保存）
>   后端加「含 `*` 即视为掩码、忽略并保留原 key」，前端改状态提示不回填值
> - **「无法调用」根因确诊**：`Unsupported model example-model-flash` ——
>   路径与 key 都对（归一化已生效），**只是模型名该服务不认**；
>   实测换成该服务自己的模型名 → **HTTP 200 ✅**
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
`load_config()` 会合并 `DEFAULT_CONFIG`（`ai_model=example-model-flash`），
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
1. **新接入的服务必然空清单**（示例服务-A 就是）—— 用户没有可选项
2. **清单里的模型名过期**（硬编码不会随服务更新）
3. **用户想输入清单外的模型**（自部署、微调名）**做不到**

而后端 `ai_provider` 也不拉远端 `/models` —— 全库唯一一处是
`local_ai.py:87` 拉 Ollama 的 `/models`，OpenAI 兼容路径**完全没有**。

---

## 三、方案

### 3.1 新端点：`GET /api/ai/models?base_url=&api_key=`

| 项 | 原设计 | 实测（2026-10-05） |
|---|---|---|
| 路径 | 对 `{base_url}/models`（**先做归一化**）发起 GET | ⚠️ **更正**：真实路径是 **`{归一化后 base}/v1/models`**（`config.py:151`）；`{base}/models` 实测 404 |
| 入参 | `?base_url=&api_key=` | 只有 `base_url`（`config.py:100`）；**密钥不接受前端传入**，只用服务端已存值 |
| 超时 | 5s | **8.0s**（`config.py:150`） |
| 成功 | `{"ok":true,"models":["…"],"source":"remote"}` | 一致（`config.py:167`），另含 `fallback:false` |
| 失败 | `{"ok":false,…,"fallback":true}` —— **永远返回 200** | ⚠️ **更正**：普通失败（无 base_url / 非 200 / 空清单 / 异常）返回 **200**；**策略闸门例外**：DENY→**403**，ASK→**202**（`config.py:138,143`） |
| 安全 | 走 V-14 闸门 `network.egress`（出网） | 一致（`config.py:133`）；**响应体不含密钥字段**（只回 `ok/models/source/fallback/error`）；请求头带 `Authorization: Bearer`（`config.py:152`） |
| 审计 | （原稿未写） | `policy.decision`（闸门，`policy.py:446`）+ `ai.models_fetch`，detail=`models=<n>`（`config.py:163`） |

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

## 四、连通性自检（P1-4 的延伸）—— **已拍板并实施（2026-10-05）**

保存成功 ≠ 能用。原设计（可独立拍板）：

```
POST /api/config 时（ai_mode=remote 且 base_url+key+model 齐全）
  → 发一条极小 chat（max_tokens=1，不实际生成）
  → 成功：provider_verified=true
  → 失败：provider_verified=false + 可读原因（401 无效 key / 404 路径错 / 403 权限 / 超时）
```

前端据此显示 **✅ 已连通 / ⚠️ 保存了但连不上：<原因>** —— 用户不必"保存后自己去试"。

### 4.1 负责人拍板（2026-10-05）

| 项 | 拍板 |
|---|---|
| 做不做 | **做**，但**默认关**：加开关默认 `off`，避免默认产生计费流量 |
| 触发时机 | 手动/诊断为主；**保存时**与**启动时**各一个开关 |
| 模型清单拉取时机 | **启动时** |
| 缓存 TTL | **10 分钟**（600s） |

> ⚠️ **实施时的一处口径取舍（需负责人知悉）**：拍板里「启动时拉取」与「默认关避免计费」
> 存在张力 —— 启动自检若要默认开，则**每次重启都会真发一次调用**。
> 本实现按**"不产生默认计费"优先**：`ai_verify_on_save` 与 `ai_verify_on_startup`
> **默认均为 False**，启动自检也要显式打开。要改默认开只需动 `DEFAULT_CONFIG` 一行。

### 4.2 实施要点（`platform/ai_verify.py` + `routers/config.py` + `server.py` lifespan）

| 项 | 实现 |
|---|---|
| 探针 | `POST {归一后 base}/v1/chat/completions`，`max_tokens=1`，单次超时 **45s**（可配 `ai_verify_timeout`），**只发一次** |
| ⚠️ 超时口径更正 | 原设计写"超时 ≤8s"（抄 `/api/ai/models` 的 8.0s）—— **实测证明这是错的**：列清单只要 **0.37s**，而自检是一次**真实 chat 调用**，同一服务 `max_tokens=1` 实测要 **37.8s**（不带 max_tokens 54.2s）。按 8s 判会把"慢但能用"报成"连不上"，诊断结论正好反了。故默认 45s |
| 结论字段 | `provider_verified` / `provider_verify_reason` / `provider_verified_at` / `provider_verify_elapsed_ms`（并入 `POST /api/config` 响应） |
| 缓存 | 指纹 = `provider+base_url+model+sha256(key)[:16]`，TTL 600s；**保存换过 key/base/model 会先清缓存**，避免拿旧结论冒充 |
| 可读原因 | 401 密钥无效 / 403 无权 / 404 路径或模型不存在 / 429 限流额度 / 5xx 服务端 / 超时 / 连不上 / "服务不认这个模型名" |
| 出网闸门 | 走 V-14 `network.egress`（被拦/待批时如实回报，不硬发） |
| 审计 | `ai.verify`（含 ok / status / cached / elapsed / reason） |
| 失败不阻断 | 自检**绝不抛异常**，任何异常都转成可读结论，保存与启动照常完成 |
| 不做 | 默认关；不在 GET 里发请求（GET 只回缓存摘要）；manual/off/local 不发云端探针 |

### 4.3 验证矩阵对应关系

本节的 §五 #6/#7 即连通性自检的断言项，状态见 §八.4。

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

---

## 八、实施记录 / 实测证据（2026-10-05 补定稿复核）

> 复核口径：**逐条读代码**（不采信旧文档转述）。全部为 `文件:行号` 级证据。

### 8.1 §三 已实施部分

| 结论 | 证据 |
|---|---|
| 端点位置 | `agent_community/platform/routers/config.py:99-100`（`@router.get("/api/ai/models")`，签名只有 `base_url: str = ""`） |
| 归一化函数 | `platform/ai_provider.py:138` `_normalize_base_url()`；`config.py:118` 以 `_normalize_base_url as _norm` 引入 |
| 归一化行为 | `ai_provider.py:149-152`：`strip().rstrip("/")` 后 **`while` 循环剥掉全部尾部 `/v1`** → 例 `https://api.example.com/v1/` → `https://api.example.com`；两种写法结果一致 |
| 统一拼接 | 全模块固定拼 `{base}/v1/chat/completions`（`ai_provider.py:141`），故归一化后**永不出现 `/v1/v1/`** |
| base_url 兜底 | `config.py:124`：入参为空 → 取配置 `ai_base_url`；仍为空 → `config.py:126-127` 返回 `ok:false / fallback:true / error="未配置 base_url"`（**200**） |
| 拉取失败回落 | 非 200 → `config.py:153-155`（error 含 `GET <norm>/v1/models -> HTTP <code>`）；空清单 → `config.py:159-161`（`"服务返回空模型清单"`）；抛异常 → `config.py:168-170`（`<异常类名>: <消息>`）。**全部 200 + `fallback:true`** |
| 密钥不外泄 | `config.py:146-147` 服务端解密；`config.py:151-152` 仅作为请求头 `Authorization: Bearer` 发出；`config.py:167` 响应无 key 字段 |
| 前端「手动输入…」 | `frontend/index.html:646` 生成 `value='__manual__'` 的常驻选项；`:684-688` 选中后 `prompt` 手输并追加 `（手输）` 选项 → **清单永远不是死路** |
| 前端触发时机 | `index.html:470` base_url `onblur="loadRemoteModels()"`；`:765` 打开设置即拉一次；无独立「获取清单」按钮 |
| 远端成功后保住已选 | `index.html:663` 取 `sel.dataset.saved`（`:730` 在打开设置时写入）→ `:668-669` 清单内直接选、清单外补选项 |
| 失败回落前端 | `index.html:672-676`：回落 presets 内置清单 + 黄字「清单获取失败：<原因> —— 可手动输入模型名」 |
| 掩码防覆盖（后端） | `config.py:220-228`：提交值**含 `*` 即判为掩码** → 忽略，改用 `AC_AI_API_KEY` env 或已存 key；判定理由与代码注释一致（`:221`） |
| 掩码防覆盖（前端） | `index.html:734-742`：`GET /api/config` 返回含 `*` 时**不回填输入框值**（留空=不修改），改用 placeholder 显示 `已配置（sk-yo****…****-key）` 样式状态 |
| 写入路径确认 | `GET /api/config` 确实返回掩码（`config.py:194-195` `mask_api_key`）；`mask_api_key` 实现见 `platform/core/security.py:121-125` |

### 8.2 §一「已修」清单复核

| 编号 | 复核结论 | 证据 |
|---|---|---|
| P0-1 base_url 归一 | ✅ 成立 | `ai_provider.py:138-152` + `:175` 构造时统一归一 |
| P0-3 清单外模型补选项 | ✅ 成立 | `index.html:723-729`（打开设置）+ `:634-643` `_keepModel()`（远端重建后用） |
| P1-4 保存失败也 `success:true` | ✅ 已修 | `config.py:313-314` 增量字段 `provider_loaded` / `provider_error` |
| P1-5 `configured` 未算 model | ✅ 已修 | `config.py:304,316-317`：改按「**本次是否真选**」判 `model_missing`，并给 `model_effective` |
| P1-6 包装链不代理属性 | ✅ 已修 | `ai_provider.py:114-135` 基类 `__getattr__` 透传（禁自引用递归） |

### 8.3 与原稿不符 / 尚未实现（事实更正）

| # | 项 | 事实 |
|---|---|---|
| 1 | §3.3 **缓存 `ai_model_cache`（TTL 10 分钟）** | **未实现**（全库无此符号）。现状＝**每次 base_url 失焦/开设置都真出网** |
| 2 | §四 `provider_verified` | **未实现**（全库无此符号），保存响应里不存在该字段 |
| 3 | §3.1 `?api_key=` 入参 | **未实现**（`config.py:100` 只收 `base_url`），密钥只在服务端取用 |
| 4 | §3.1 超时 5s | **实为 8.0s**（`config.py:150`） |
| 5 | §3.1「**永远返回 200**」 | **有例外**：策略 DENY→403、ASK→202（`config.py:138,143`）。前端因此**必须同时判 `d.ok` 与 `d.models.length`**（`index.html:664`），不能只看 HTTP 码 |
| 6 | §3.2「无论成败下拉末尾常驻手动输入」 | 成立，但**成功分支才保证**：远端成功→`_appendManual()`（`:667`）；失败→`onProviderChange()` + `_appendManual()`（`:672-673`） |
| 7 | `config.py:147` 对 `load_config()` 返回值再调一次 `_decrypt_secret` | **冗余无效**：`load_config()` 已解密（`config.py:81-82`），再解密是空转（明文无 `dpapi:` 前缀 → 原样返回，`core/security.py:118`）。**当前不构成缺陷**，但注释暗示"这里才解密"，易误导后续维护者 |
| 8 | 原稿 §一附 的模型名示例 | 已统一为虚拟名（铁律⑥） |

### 8.4 §五 验证矩阵覆盖度（复核）

⚠️ **本稿的矩阵是「草案」，仓库内没有独立的 V-16 测试文件**；下表是逐条对代码/既有测试的核对结果，缺口**不得当作已验**。

| # | 用例 | 覆盖 | 证据 / 缺口 |
|---|---|---|---|
| 1 | base_url 含 `/v1` → 不出现 `/v1/v1/` | ✅ | `ai_provider.py:150-151`（while 剥净）+ `:141`（统一拼接）；逻辑上不可达双重 `/v1` |
| 2 | 远端正常 → 下拉重建且保留已选值 | ⚠️ 仅代码级 | `index.html:664-670`；**无自动化测试** |
| 3 | 远端超时/404 → 200 + `fallback:true` + 下拉不空 | ⚠️ 部分 | 后端分支 `config.py:153-155,168-170` 明确；**无测试**；前端回落 `:672-676` **无测试** |
| 4 | 未配置 api_key 仍请求 | ✅ 代码级 | `config.py:152`：`_key` 空则**不带头**仍发请求 |
| 5 | 清单外模型可提交并保存 | ⚠️ 仅代码级 | 前端 `:684-688`、`:639`；后端 `config.py:229` 直接落库（不校验白名单） |
| 6 | 连通性自检 401 | ❌ **未实现** | §四 未实施，无 `provider_verified` |
| 7 | 连通性自检成功 → ✅ 显示 | ❌ **未实现** | 同上；`index.html` 无「已连通」展示 |
| 8 | 出网经 V-14 `network.egress` 闸门 + 审计 | ✅ | `config.py:133` 调 `check(NETWORK_EGRESS,…)`；`policy.py:445-450` 记 `policy.decision`；`config.py:163` 记 `ai.models_fetch` |
| 9 | 回归：全量测试通过 + 真实 config 指纹不变 | ✅ | `agent_community/tests/conftest.py` 两层隔离（`isolate_data_dirs` + `guard_real_data_dir`，已覆盖 `~/.agent_community/config.json`） |

**结论**：9 条里 **2 条（#6/#7）因 §四未实施必然为空白**，**4 条（#2/#3/#5）只有代码级依据、无自动化测试**。若 §四 拍板实施，建议同时补这三条的前端/端点级用例。

### 8.5 §四 实施状态 —— **2026-10-05 已实施**（原为唯一待拍板项）

| 项 | 拍板前现状（存档） | 实施后 |
|---|---|---|
| 触发时点 | `POST /api/config` 无任何连通性探测 | ✅ `_verify_after_save()` 内联；另加 `server.py` lifespan 启动自检（开关控制） |
| 计费风险 | 会真发一次 `chat/completions`（`max_tokens=1`） | ✅ 负责人拍板接受；**默认关**，且 TTL 缓存避免重复出网 |
| 落地边界 | 建议默认关 / 仅 remote / 超时 ≤8s / 失败不影响保存 | ✅ 全部照此实现（超时 8.0s，与 `/api/ai/models` 同口径）；另加指纹缓存与 `ai.verify` 审计 |

---

## 九、待拍板清单（汇总）—— **2026-10-05 全部拍板并落地**

| # | 事项 | 现状 |
|---|---|---|
| 1 | **§四 连通性自检是否做** | ✅ **做**（默认关）—— 见 §四；实现 `platform/ai_verify.py` |
| 2 | 拉取时机：自动还是加按钮 | ✅ 现状**自动**（`index.html` 打开设置即拉 + base_url 失焦再拉）**保留**；负责人拍板「启动时」指启动自检 |
| 3 | 模型清单缓存 TTL | ✅ 拍板 **10 分钟**（600s）；⚠️ 清单拉取本身仍每次真出网，`ai_model_cache` **未实现**（与自检缓存是两回事，如需清单级缓存另开） |
| 4 | §四 是否加开关 + 默认关 | ✅ **已加两个开关，均默认 False**（`ai_verify_on_save` / `ai_verify_on_startup`） |

> §一 的 P0-1/P0-3/P1-4/P1-5/P1-6 与 §三 早已落地；§四 已于 2026-10-05 实施。
> **对外口径同步**：本稿与仓库内真实模型名/服务域名已按负责人拍板换成中性占位名
> （`example-model-flash` / `api.example.com`），本地副本保留真实名。
