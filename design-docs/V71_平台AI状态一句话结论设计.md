# V71 · 平台 AI 状态「一句话结论」（`ai.verdict`）设计

> 状态：**待老板拍板**（2026-10-21 起草）。
> 起因：`10.25 下一步` 第 1 条 —— 现在 `ai-check` 只在 CLI，界面上要靠前端自己拼
> `detail / reason / how_to_fix`；**同一条结论有两个拼法**，迟早打架。本稿把它收成一处真相。

---

## 1. 现在有什么（别再造第二份口径）

| 出口 | 给的字段 | 谁在用 |
|---|---|---|
| `GET /api/status` → `ai` | `configured / reachable / detail / source / checked_at / age_seconds / provider / model` | 界面状态条、`cli task` 提交前那行「AI 状态：」、看门狗 |
| `POST /api/ai/verify` | `ok / status / reason / reason_human / how_to_fix / elapsed_ms / cached / detail` | 设置页「验证连通」按钮 |
| `cli ai-check` | 真跑一次探针，打印「可用 X ms / 原因 / 怎么办 / 原始信息」 | 命令行用户 |
| `platform/ai_error_human.py` | `{kind, human, fix, raw}` —— **机器错误 → 人话**（一处真相） | CLI 结果、选人/讨论 reason、外端 AI |

**问题**：`/api/status.ai` 只有**零件**（`source` + `reachable` + `detail` 各一片），
界面要显示"到底能不能用、下一步干什么"就必须自己推一遍状态机 —— 前端每写一次就是**第二份口径**，
文案也各写一套（真机已出现"验证连通未通过：…"和状态条说法不一致）。

**本稿的判据**：**结论只由后端给一句；前端只显示，不判断、不拼。**

---

## 2. 契约（新增字段，不动旧字段）

### 2.1 `GET /api/status` → `ai.verdict`

```jsonc
"ai": {
  "configured": true, "reachable": true, "source": "cache",   // ← 旧字段原样保留
  "verdict": {                                                // ← 新增
    "state": "ok",            // 五态枚举，见 §3
    "tone":  "good",          // good | warn | bad | neutral（纯给样式，不给文案）
    "text":  "平台 AI 可用（3 分钟前验过）",
    "action": "",             // 用户下一步（空串 = 不用做任何事）
    "at":    "2026-10-21T08:12:00"   // 有结论的时间；没结论时为空串
  }
}
```

- **`text` 是一整句人话**，界面直接显示；**不许**在里面放 markdown 记号、emoji、技术串
  （违反时守卫直接红，见 §6）。
- **`action` 是「怎么办」**（去哪点什么），与 `text` 分开是为了让界面上"结论"和"按钮旁提示"能分开排。
- **只读**：`verdict` 与 `/api/status` 同源，**绝不发网络请求、绝不烧钱**（结论来自 `ai_verify` 缓存）。

### 2.2 `POST /api/ai/verify`（既有端点，只补一处）

响应里加 `verdict`（结构与 §2.1 相同，`state` ∈ `ok` / `unreachable`）。
其余字段一个不改 —— 现有前端照旧能跑。

### 2.3 命令行

`cli ai-check` 的结论行改成**直接打印 `verdict.text`**（+ `action`），
而不是自己拼「可用 X ms」；原始信息仍在最后一行（V69 的规矩不变）。

---

## 3. 状态机（五态，唯一判定处）

| state | 触发条件 | tone | `text`（口径，逐字实现） | `action` |
|---|---|---|---|---|
| `not_configured` | 没填 provider 或模型 | `neutral` | `平台 AI 还没配置：讨论与分工用不了（接入的 agent 不受影响）` | `去「对接中心 → AI 设置」填接口地址、模型、密钥，保存后点一次「验证连通」` |
| `unverified` | 配了，但从没验过 | `warn` | `平台 AI 已配置，但还没验证过能不能用` | `点一次「验证连通」，10 秒内出结论` |
| `stale` | 有结论但超过缓存 TTL | `warn` | `上次验证是 N 分钟前，结论已过期` | `再点一次「验证连通」` |
| `ok` | 新鲜结论且可用 | `good` | `平台 AI 可用（N 分钟前验过）` | `""` |
| `unreachable` | 新鲜结论且不可用 | `bad` | `平台 AI 连不上：<人话原因>` | `<翻译层给的 fix>`（逐字取 `ai_error_human.explain()["fix"]`） |

- `N`：`age_seconds // 60`，不足 1 分钟写「刚刚」。
- **判定顺序固定**：`not_configured` → `unverified` → `stale` → (`ok` | `unreachable`)。
- **认不出来就如实说认不出来**：`unreachable` 的 `<人话原因>` 直接取 `ai_error_human.explain()` 的 `human`
  （它认不出时给的是"具体原因没认出来"），**本层不许再编一句**。
- 读配置失败 / 查缓存失败 ⇒ `state="unknown"`（第六态，tone=`warn`，text 说明"判断不了，看服务日志"），
  **不许**显示成 ok。

---

## 4. 落点（代码）

| 位置 | 改动 |
|---|---|
| `platform/ai_status.py`（**新增，约 90 行**） | `verdict_from_snapshot(ai_snapshot) -> dict`：纯函数，输入就是 `/api/status.ai` 那几只字段，输出 §2.1 的 `verdict`。**唯一判定处**；不读环境、不发请求、不写日志。 |
| `platform/server.py::_ai_health_snapshot` | 末尾把 `snapshot["verdict"] = ai_status.verdict_from_snapshot(snapshot)`，其余不动 |
| `platform/routers/config.py::api_ai_verify` | 复用同一函数拼 `verdict`（`state=ok/unreachable`） |
| `cli.py` 的 `ai-check` | 打印 `verdict.text` + `verdict.action`，删掉自己拼的那句 |
| `frontend/index.html`、`docking`（**归前端负责方**） | 只显示 `text` + `action`；**不再**自己判 `reachable`、**不自己写**"可用/连不上"的文案 |

> 为什么新开一个模块：`server.py` 已经 6600 行；而且"结论怎么算"要被端点、CLI、判据**三处共用**，
> 放 `server.py` 里就只能靠 import 巨无霸（本项目已踩过双模块陷阱）。

---

## 5. 兼容与不做的事

- **只增不改**：`configured / reachable / source / detail` 保留（看门狗、CLI、旧前端仍在读）；
  `detail` 继续给"零件式"说明，`verdict` 给"结论式"一句 —— 两者不冲突，但**新界面只显示 verdict**。
- **不改默认行为**：`verdict` 只读缓存，`/api/status` 依旧**不发请求**（不烧钱），`ai_verify_on_save` 默认仍关。
- **不做**：不新增"自动定时探活"、不在前端做任何"猜测式"降级、不为某一家模型服务商写专属文案。

---

## 6. 判据（可验收）

1. `agent_community/tests/test_ai_status_verdict.py`（新，约 12 条）：
   - 六态各一条（含 `unknown`）；判定顺序（过期结论**不许**显示成可用）；
   - `ok` 时 `action` 必须为空串；`unreachable` 的 `action` 必须**逐字等于**翻译层的 `fix`；
   - `text` 里**不许**出现 `**`、emoji、`getaddrinfo`/`errno`/`http 4xx` 这类技术串（用正则判）；
   - 纯函数性：喂同一快照两次，输出完全相同（不含当前时间漂移）。
2. 端点真请求（重启后）：`GET /api/status` 的 `ai.verdict.text` 非空且与 `ai.source` 自洽；
   `POST /api/ai/verify`（未配置分支）也给 `verdict`。
3. `first_run_check.py` 增一格：五步走查里「AI 状态」那行**必须是 verdict 的 text**（不是技术串）。
4. 前端静态守卫（`test_docking_ui_static.py` / 前端预检）：界面**不许**出现自己拼的
   「验证连通未通过」「AI 可用」类文案，只允许显示 `verdict.text`。

---

## 7. 对前端（通道口径，拍板后发 `226`）

- 读 `GET /api/status` → `ai.verdict`；
- 显示：`text`（主句）＋ `action`（非空时作为下一步）；
- `tone` 只用来选样式（`good` 绿 / `warn` 黄 / `bad` 红 / `neutral` 灰），**不改文案**；
- 空/缺 `verdict`（老后端）时回落到今天的 `detail` —— 保证前端可以先行改、不必等后端同版本。
