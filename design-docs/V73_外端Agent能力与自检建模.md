# V73 · 外端 Agent 的能力与自检建模（**以本机 8 条真实记录为据**）

> 状态：**建模稿（第二版），待老板拍板**。2026-10-21。
>
> **为什么要写第二版**：第一版（`.bak_20261021_臆想版`）是**我推出来的**，不是量出来的。
> 老板当场指出「**不能臆想，不能主观臆断，现实和想象不太一样**」——
> 我把 `agent_community/data/` 里的真实记录摊开一对，**第一版有四处是错的**（清单见 §7）。
> 本版每条事实都给**出处**，凡是我推断的，一律标〔推断〕并写清"为什么这么推"。
>
> 出处标记：〔记录〕`agent_community/data/*.json` 的真实内容 ｜〔源码〕仓库代码 ｜
> 〔实测〕我当场跑/看过 ｜〔推断〕我的判断，**未实证**

---

## 1. 抽样与方法（先说清"我看了什么"）

- 数据：`agent_community/data/harnesses.json`（**8 条注册记录**）、`workshops.json`（**7 个工作间**）、
  `audit.json`（**500 条留痕**）〔实测·2026-10-21 09:5x 读取〕
- 代码：`platform/harness_probe.py`、`verify_sandbox.py`、`protocol.py`、`routers/docking.py`、`bridge_template.py`〔源码〕
- **真实名单不写进本稿**（规则 6：对外材料只用形态描述）。本稿按形态称呼，数量给真值。

---

## 2. 事实一：本机 8 条真实记录长什么样

〔记录〕8 条全部带这些字段：`harness_id`、`harness_name`、`harness_type`、`transports`、
`preferred_transport`、`callback_url`、`wakeup_method`、`wakeup_url`、`wakeup_dir`、`acp_command`、
`acp_cwd`、`api_base_url`、`api_message_path`、`api_outbox_dir`、`can_wake`、`waking_models`、
`waking_protocols`、`bridge_dir`、`bridge_status`、`activation_script`、`ai`、`tools`、`description`、`metadata`

真实取值分布〔记录〕：

| 字段 | 真实分布 | 说明 |
|---|---|---|
| `harness_type` | `desktop-agent` **6** / `ide-agent` **1** / `cli-agent` **1** | 桌面型是绝对主流 |
| `wakeup_method` | `clipboard` **6** / `http_api` **1** / `acp` **1** | **"剪贴板"是主流形态** |
| `transports` | `["http"]` **8/8** | 全都声明支持 http |
| `preferred_transport` | `null` **8/8** | 从没被填过 |
| `bridge_status` | `tested` **6** / 空 **2** | 6 条有"测过"的痕迹 |
| `can_wake` | **False 8/8** | 见 §5（这条我第一版理解错了） |
| `waking_models` / `waking_protocols` | **空 8/8** | 从没被填过 |
| `activation_script` | 全部空串 | 从没用过 |

### 2.1 最重要的一条：`clipboard` 的真实含义是「**人工中转**」

〔记录〕6 条 clipboard 记录里，`metadata.wake_wrapper` 原文一致：

> `{"kind": "manual", "mode": "manual", "command": "", "path": "", "note": "人工中转型：没有叫醒命令；任务由用户手动粘贴给该 agent"}`

⇒ **不是"平台自动往它的输入框里粘贴"，而是"没有叫醒命令，靠人手动粘贴"。**
第一版我把它想成了"自动注入"（L2 会话内一问）——**现实里这条通道压根不存在**。

---

## 3. 事实二：它的"能力"是怎么存下来的

**能力不是一个字段，是三个地方**〔记录〕：

1. `ai.capabilities`（列表，**声明的能力标签**）——8 条里 6 条非空，真实出现过的标签：
   `coding` / `file_ops` / `web_search` / `shell` / `bash` / `exec_command` / `system_ops` /
   `app_ops` / `memory`（**同一个意思被写成不同名字**：`shell`/`bash`/`exec_command` 三份）；
2. `ai` 里的四个布尔：`can_see` **8/8 False**、`can_code` 2 条 True、`can_browse` 1 条 True、
   `can_file_ops` 2 条 True —— 与第 1 项**互相矛盾**（有 6 条声明 `coding`/`file_ops`，但 `can_code/can_file_ops` 只有 2 条 True）；
3. `tools[]`（工具清单，带 `capability_tag`）：**只有 3 条记录有工具**，且 **`parameters` 全是 `{}`**〔记录〕

⇒ **结论（与现实相符的版本）**：平台存的是**声明**，不是**实测**；
而且声明自身**不一致**（标签命名不统一、`capabilities` 与四个布尔打架、工具参数从未抓过）。
**这才是真实的建模漏洞**——不是"没有验证字段"，而是"验证字段存在，但验证的是别的东西"（见 §4）。

---

## 4. 事实三：平台已经验证到哪一步（原文比我第一版说的细）

〔记录〕每条 `metadata.bridge_template_verified` 里都带一句 `verified_scope`，**原文**：

> **仅桥配置**（桥能连平台/领件/回报；且这只桥是**平台在验证间里启动的**）——
> **harness 侧是否运行该桥、agent 能否回话，本次未验证**

〔记录〕`metadata.bridge_test` 原文（`ok=true` 的那次）：

> `echo = "pending-poll-bridge alive, work-dir=…\agent_community\bridges\<harness_id>"`
> `at = 2026-10-13T15:24~15:35`（6 条集中在同一时段）

〔源码〕`verify_sandbox.py` 里确实是**平台自己 `Popen` 起桥**、建一个角色为 **`验证员`** 的成员（`SANDBOX_ROLE`）。
⇒ 验证间里"对面"是**平台自己**，**没碰过真实 agent**。这与 `verified_scope` 的自述完全一致。

**所以"三段"是真实存在的，而且平台自己写清楚了**：

| 段 | 谁证明了 | 证据字段 | 本机真值 |
|---|---|---|---|
| ① 桥能连平台/领件/回报 | 平台（验证间，自己起桥） | `bridge_test` / `bridge_template_verified` | **6 条 ok** |
| ② harness 侧真的在运行这只桥 | **没人证明** | 无 | — |
| ③ agent 真的能回话 | **没人证明** | 无 | — |

---

## 5. 事实四：`can_wake` 不是"它能不能被叫醒"

〔源码〕`platform/protocol.py:471`：

```python
can_wake: bool = False   # 声明"我能当喊人专员"
```

〔源码〕`platform/server.py:2331-2348`：任务创建后**扫描在线 harness 里 `can_wake=true` 的"喊人专员"**，
按协议分派举手判断；没有就打印「无在线喊人专员（can_wake=true），跳过唤醒」。

⇒ `can_wake` 描述的是**"这只 agent 能不能替我喊别的 agent"**，**不是"它自己能不能被叫醒"**。
本机 8/8 为 False，只说明**没有一只 agent 被登记成喊人专员**——这是"没配"，**不是"不能"**。
第一版我把它当成"能不能被叫醒"，是**望文生义**。

---

## 6. 回答老板的三个问题（**只用上面的事实**）

### 6.1 外端 Agent 通常有什么能力？

- **声明的认知/执行能力**：真实出现过 9 个标签（§3.1），但**标签不统一**、与布尔字段打架；
- **工具清单**：3/8 条有，参数全是空；
- **通信能力**：8/8 声明 `http`；**真实可用的叫醒方式 6/8 是"人工手动粘贴"**，1 条 `http_api`、1 条 `acp`；
- **被验证过的能力**：**只有"桥能连平台"这一段**（6 条 ok），**"它会不会干活"从未被验证**（§4）。

### 6.2 能否自检？

- **平台这一侧能自检**：桥脚本能不能跑（`bridge_test`，零侵入）、进程/端口/路径能不能探到
  （`harness_probe.py` 的 `probe_hints`，本机真存着 `process_hint` / `port_hint` / `path_hint` 三种线索）；
- **agent 自己自检**：本机数据里**没有任何一条**证据来自"agent 自述检测结果"；
  〔推断〕且沙箱里它看不见宿主，它自述"我有什么接口"不能当事实——**但这一条我目前没有本机实测反例样本**，
  第一版写的"实测：它回『我什么接口都没有』"是**引用旧设计稿的说法，不是我这次量的**（〔引用〕V42）；
- ⇒ 能说的实话：**"能不能自检"目前只能回答平台侧；agent 侧没有证据，既不证实也不证伪。**

### 6.3 自检会不会影响上下文？

**按真实链路回答（而不是按我想象的链路）**：

| 自检手段（真实存在的） | 碰没碰它的上下文 | 依据 |
|---|---|---|
| 桥脚本自测（`bridge_test`，平台自己起桥） | **不碰** | 〔记录〕`verified_scope` 明说"这只桥是平台在验证间里启动的"；〔源码〕`verify_sandbox` 自己 `Popen` |
| 进程/端口/路径探测（`harness_probe`） | **不碰** | 〔源码〕只看宿主，零 token、不调 AI |
| 心跳 | **不碰** | 〔源码〕`heartbeat.py` 读时间戳 |
| **人把任务粘给 agent**（当前 6/8 的真实工作方式） | **必进它的上下文** | 〔记录〕`wake_wrapper.note` 原文「任务由用户手动粘贴给该 agent」 |
| 让 agent 来取件（`pending_poll` 队列被它读到） | **进** | 〔推断〕它读到队列内容即进入会话；本机无"agent 真来取件后回话"的记录可证明 |

⇒ **结论**：**今天平台上所有自动化的自检都不进它的上下文**（因为对面根本没人，是平台自己当对端）。
会进上下文的只有**人手动粘贴**这一条真实通道。
所以第一版那套 L0~L3 分档**方向没错，但把"我们能让它自检"想得太强**——
**真要做"问它一句"的自检，得先有第二条真实通道**（agent 侧真跑桥 + 真回话），那是**未验证段**。

---

## 7. 第一版我错在哪（留着防重犯）

| # | 第一版写的 | 真实情况 | 依据 |
|---|---|---|---|
| 1 | 「`capabilities` 是自报的，**平台没有一处验证过**」 | 验证字段存在（`bridge_template_verified` / `bridge_test` / `bridge_status`），**但验证的是"桥配置"，且平台自己写了"agent 能否回话未验证"** | §4 |
| 2 | 「平台看不到自检痕迹」 | 有痕迹，还带时间与 echo 原文 | §4 |
| 3 | 「clipboard 型是自动注入，会进上下文」 | **6/8 是人工中转**（无叫醒命令，人手动粘贴） | §2.1 |
| 4 | 「`can_wake` 表示它能不能被叫醒」 | 表示**它能不能当喊人专员** | §5 |
| 5 | 「实测：桌面型 agent 回『我什么接口都没有』」 | 那是**引旧设计稿**的说法，不是我这次量的；本机无此样本 | §6.2 |

---

## 8. 真实缺口（这才值得动手）

1. **"agent 能否回话"从未被验证过**（8/8 记录都停在"桥配置"），而这正是老板最在意的"看起来成功"；
2. **声明自身不一致**：`capabilities` 标签 3 种写法指同一件事、与 `can_code/can_file_ops` 打架、`tools.parameters` 全空；
3. **`can_wake` 被 8/8 留空**：界面上"谁能喊人"这条能力实际上是空的（不影响主流程，但会让"自动派活"没人可用）；
4. **没有任何字段记录"这段证据证明了什么、没证明什么"以外的东西**——
   其实 `verified_scope` 这段自述是**好实践**，值得推广到所有"验证"类字段（能力/工具也照此写一句"本验证的范围"）。

---

## 9. 建议（**待拍板，先不动码**）

1. **先把"段"说清**：能力页/接入页把 §4 的三段（桥配置 / harness 在跑桥 / agent 能回话）**分开显示**，
   只有第三段绿了才叫"接上了"；前两段绿一律标"配置可行"（沿用 V43 §0' 的口径）；
2. **声明的能力标注来源**：`declared`（它说的）/ `probed`（宿主探到的）/ `verified`（真跑通的）；
   **只有 `verified` 能当派活硬条件**；
3. **统一标签词表**（`shell` / `bash` / `exec_command` → 一个词），并修 `capabilities` 与布尔字段的打架；
4. **把 `verified_scope` 那句话变成模板**：以后任何"验证"都必须回填"本次证明了什么/没证明什么"。

---

## 10. 判据（真跑）

1. 拿一个**只有桥配置**的 harness：界面上必须显示"配置可行，未验证 agent 能回话"，**不许显示"已接上"**；
2. 拿本机 8 条真实记录跑一遍能力页：**每条都显示来源**（declared / probed / verified），无一条凭空显示 verified；
3. L0 探测（进程/端口/路径）前后，该 harness 的收件/发件计数**不变**（可断言"零侵入"）；
4. 词表统一后：`shell`/`bash`/`exec_command` 不会同时出现在同一份能力清单里。
