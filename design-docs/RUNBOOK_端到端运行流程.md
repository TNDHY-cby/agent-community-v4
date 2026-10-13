# 端到端运行流程（RUNBOOK，2026-10-08 更新）

> 这份文档回答一个问题：**这个项目现在怎么跑起来、按什么顺序走一遍**。
> 面向：接手者（人）、协作者（前端）、以及**外端 harness 的接入方**。
> 所有端点与形状均为**仓库实测口径**（2026-10-08，全量回归 998 passed）。
> 发布口径：本文**无本机绝对路径、无真实 harness 名**（示例一律用「示例Harness-A」）。

---

## 0. 一分钟版

```
① 起服务 → ② 注册 harness（可带"动作脚本"） → ③ 建工作间 → ④ 一级讨论 → ⑤ 选定成员
→ ⑥ 开工：平台**自动**建会话并命名（步骤 4/7/8/9，会抢桌面的那类自动跳过）
→ ⑦ 送具体桥（步骤 5） → ⑧ 桥与隔间对接（步骤 6）
→ ⑨ 二三级讨论 → ⑩ 阶段整合 → （随时）掉线抢救
```

**贯穿全程的一条规则**：脚本做了 ≠ 做成了。凡是"可能做了但没自证"的，一律标
**未核实（`verified=false`）**，界面**不许**显示成"完成/成功"。人工做的那一步同样如此。

---

## 1. 起服务

```bash
# 在仓库根目录跑（agent_community 包的父目录）
python -m agent_community.platform.server --port <端口>
```

- **演练/验证务必用隔离实例**（别动在用实例）：`AC_DATA_DIR=<临时目录>`
- 前端验收脚本指向某实例：`AC_FE_BASE=http://127.0.0.1:<端口>`

---

## 2. 步骤 0：注册 harness（**决定后面哪些步骤能自动**）

```http
POST /api/harness/register
{
  "harness_id": "示例Harness-A", "harness_name": "示例Harness-A",
  "harness_type": "desktop-agent", "wakeup_method": "file_poll",
  "acp_command": "<可执行文件> --noop",
  "ai": {"model_name": "示例模型"},
  "scripts": {
    "new_session":    {"NEW_SESSION_CMD": "<你的建会话命令>",  "foreground": "never"},
    "rename_session": {"RENAME_CMD": "<你的重命名命令> {new_name}", "foreground": "never"},
    "deliver":        {"DELIVER_CMD": "<你把文本送进会话的命令>", "foreground": "never"}
  }
}
```

- **必填只有** `harness_id` 与 `ai.model_name`（其余有默认值）。
  ❗**报错是可照做的**：缺字段时返回 `{error（中文，字段级）, 必填, 最小可用示例, 提示}`
  —— 直接把 `error` 显示给用户、把 `最小可用示例` 做成"一键填入"即可，**不要显示框架英文**。
- **`scripts` 全部可选**：没配的动作 ⇒ 该步骤走**人工模式**（平台给指引、返回"未核实"），**不是报错**。
- 响应里 **`action_scripts`** 逐动作告诉你：`configured` / `mode` / `foreground`；
  也可单独查 `GET /api/harness/{harness_id}/action-scripts`。
- **`foreground`**（`never`/`optional`/`required`）＝这一步**会不会占用用户桌面**；
  `required` 的步骤，界面**必须提前告知用户**（见 §11）。

> 三份骨架在 `agent_community/bridge_templates/script_framework/`；
> 一致性自检：`python conformance.py <你的脚本.py> --action new_session`（5 项检查）。

---

## 3. 步骤 1~3：建间 → 一级讨论 → 选定成员

```http
POST /api/workshop           {"name": "<项目名>", "hall_content": "...", "members": [...]}
POST /api/workshop/{ws}/discuss            {"message": "..."}   # ❗字段名是 message（不是 content）
POST /api/workshop/{ws}/select-members     # AI 按能力初选（**需要已配 AI**）
POST /api/workshop/{ws}/members            {"members": [{role, display_name, harness_ids}, ...]}
POST /api/workshop/{ws}/confirm-members    {"members": [...]}   # 确认名单 → 进 division（二级讨论）
```

- **没配 AI 时**：`select-members` 返回 400 且说明"AI Provider 未配置" —— 这**不挡路**，
  直接用 `members` **手工给名单**即可（这条路不需要 AI）。
- 大厅广播任务也会**自动建间**：`GET /api/tasks/{task_id}/workshop` 可反查它属于哪个间。

---

## 4. 步骤 4/7/8/9：建会话 + 命名（**开工时自动做**，也可手动）

**开工即自动**：`POST /api/workshop/{ws}/start` 会在后台任务里**先跑步骤 4/7/8/9、再派激活消息**
（顺序不能反：激活消息里要带"平台给你的会话名"）。全程不卡 `/start`（实测返回 0.008s）。

★**只在安全时自动做**（两条同时满足）：
1. 该 harness **配了 `new_session` 脚本**；
2. 该动作 **`foreground != "required"`**（**不会抢用户桌面**）。

**会弹窗的那类自动跳过**（点一下"开工"就把用户窗口弹出来，违反"不要弹出来影响用户"），
留给界面按钮 + 人确认。已有会话名的成员也会跳过（不白跑子进程）。

手动入口（界面按钮用）：

```http
POST /api/workshop/{ws}/onboard-members           {"member_ids": ["m0","m1"]}   # 批量，不给=全体
→ { auto_ok: [...], need_human: [...], results: {m0: {name, steps, verified, all_verified, manual}}, summary }
POST /api/workshop/{ws}/member/{mid}/establish-session      {"new_name": "可选"}
GET  /api/workshop/{ws}/member/{mid}/session-name           # 只取名字（给"复制粘贴重命名"用）
```

- **名字由平台给**：`<工作间id>-<项目>-<职位>-<成员id>`，写进成员 `session_aliases`（**首位＝身份凭据**）；
- 每个成员的动作脚本渲染到**他自己的隔间** `<隔间>/scripts/*.py`（一员工一份）。

---

## 5. 步骤 5/6：送具体桥 → 桥与隔间对接

```http
POST /api/workshop/{ws}/member/{mid}/deliver-bridge
→ { bridge:{ok,path,error}, text_path, verified, manual }
```

- 桥产物落在该成员隔间；配了 `deliver` 就自动送进去；没配 ⇒ 人工模式：
  文本落成 `<隔间>/deliver_text.md`（界面给"复制"按钮即可，别让用户去 JSON 里抠长文本）。
- **桥与隔间对接**：平台只往**该成员自己的邮箱** `<隔间>/inbox` 写，他的桥只从自己的邮箱取
  ⇒ **同一 harness 开多个会话也不会串**。
  ```http
  GET /api/workshop/{ws}/compartments     # 磁盘实况：隔间/邮箱积压数/接入产物
  ```

---

## 6. **激活消息里带什么**（老板 2026-10-08 定：通用模板，只换路径）

平台下发的激活消息＝**通用模板 + 路径填充**，末尾追加三段（**不把分工内容贴进消息**）：

- 【这是什么平台】本地自部署的多 agent 工作间协作平台；
- 【本工作间】+【共享资料库路径】（读 `README.md`/`plan.md`/`framework.md`）；
- 【你的分工文件夹】`library/<agent名>-<职位>/`（**先看这里**）；
- 【平台给你的会话名】（未建立会话时**不显示**这一段，不编造）。

> 外端 agent **拿着路径自己去看**项目与分工——这是老板的原话要求。

---

## 7. 步骤 10：二三级讨论 → 阶段整合

```http
POST /api/workshop/{ws}/review                   # 三级讨论/评审
POST /api/workshop/{ws}/integrate                # 阶段整合（⑧后半）
POST /api/workshop/{ws}/complete                 # 收尾
GET  /api/workshop/{ws}/library                  # 共享资料库视图
```

- **阶段整合**把各成员隔间 `output/` 的**产物**列成 `<工作间>/library/integration.md`，
  交给整合者（默认组长）取；**平台不搬动**成员文件。
  ⚠️ 全员 `output/` 都空时 ⇒ `has_output=false` + 清单明写"无产物可整合"，**不得**当成"整合成功"。
- **共享资料库视图** `GET .../library` 返回：
  - `files`: `[{name,size}]`（**是列表**，注意形状）；
  - `directories`: `[{name, files:[…], count, assignment_preview}]` ← **每个员工的分工文件夹**
    （`<agent名>-<职位>/`，`assignment.md` 里写他该做什么）；
  - `preview`: `{plan.md/framework.md/README.md: 前 1200 字}`。

---

## 8. 随时可用：掉线抢救（老板口述②）

```http
GET  /api/workshop/{ws}/offline-report                 # 三态：idle / healthy / rescue
POST /api/workshop/{ws}/member/{mid}/recover           # 对单人跑一轮
POST /api/workshop/{ws}/recover-scan                   # 全间跑一轮
```

**判据分两级**（老板原话："有时候对方并没有工作需要进行"）：
```
第一级 有在进行的活吗？ 没有 ⇒ verdict=idle ⇒ 不救、不通知任何人（空闲是正常态）
第二级 有活 + 有异常信号 ⇒ verdict=rescue ⇒ 通知组长 → 尝试调起 → 3 次仍不成 ⇒ 通知用户
```
- **自动扫描默认关**（`AC_OFFLINE_AUTO=1` 才开）：它可能把用户窗口弹到前台，且老板定过
  「自动兜底默认关」；**手动按钮即主路径**。
- 信号分**硬/软**：`hard_reasons`（桥进程确认退出＝**确实收不到活**，`available=false`）与
  `soft_reasons`（心跳失联等——**本项目心跳可选，不能据此说"联系不上"**）。
- `verdict=idle` 的成员**物理上没有抢救按钮**（结构上做不到打扰空闲）。

---

## 9. 前端一眼看全：成员卡片数据

```http
GET /api/workshop/{ws}/members-status
→ 每成员：entry_state/entry_reason、session_name/session_aliases/session_ref、
  compartment{dir,exists,mailbox_pending,root_files,output_files}、bridge_files、
  steps{4/5/8 各 {automatic,mode,foreground}}；顶层 summary{count,all_steps_automatic,manual_members}
```

**一张卡要看的东西都在这里**（避免 N+1 请求、避免多源口径不一致）。

---

## 10. 默认值与开关

| 开关 | 默认 | 作用 |
|---|---|---|
| `AC_DATA_DIR` | 仓库内 `data/` | 数据目录（**演练/验证必改**） |
| `AC_FE_BASE` | 18920 | 前端验收脚本指向的实例 |
| `AC_OFFLINE_AUTO` | **关** | 掉线**自动**扫描（关=人点一下才救） |
| `scripts.*.foreground` | `optional` | 该动作会不会占用户桌面；`required` 的界面**要提前告知** |

---

## 11. 流程 ↔ 界面 一一对应（前端照这张表做就不会偏）

| 步 | 用户此刻在做什么 | 界面上该出现 | 点它调什么 |
|---|---|---|---|
| 0 | 接入 harness | 注册表单 + 档位提示；**报错可直接展示**（中文/字段级/最小示例） | `register` |
| 1 | 提需求 | 大厅输入 + 广播 | `POST /api/workshop` |
| 2 | 看讨论 | 一级讨论区 | `discuss`（body 用 `message`） |
| 3 | 选人 | 候选名单 + AI 初选理由 + **可手改**（AI 未配也能手工给名单） | `select-members` / `members` / `confirm-members` |
| 4/7/8/9 | 开工前把会话建好并命名 | 每成员：**会话名 + 复制**；三步「自动/需人工」徽章；一键全部 | `onboard-members`（或开工自动做） |
| 5 | 把桥送进各人会话 | 「送出具体桥」按钮；人工模式给**投递文本**（可复制） | `deliver-bridge` |
| 6 | 桥与隔间对接 | 隔间路径 + **邮箱积压数** | `compartments` |
| 7-8 | 看分工 | **资料库**入口：`plan.md` 全员分工 + `directories[]` 各人分工文件夹 | `library` |
| 9-10 | 看二级/三级讨论 | 讨论区 + 成员状态 | 既有 |
| 收尾 | 阶段成果 | 「阶段整合」→ 清单 + `has_output` | `integrate` |
| 随时 | 有人没动静 | 「成员状态」面板：idle / healthy / 需要抢救 / 需要你处理 | `offline-report` + `recover(-scan)` |

**每个成员卡上，用户的疑问永远只有三句**：
1. **「它该干什么？」** → 分工文件夹（`library/<agent名>-<职位>/assignment.md`）+ 职位 + 会话名
2. **「这步能不能自动？」** → `steps.*.automatic`（false ⇒ 显示「需人工」+ `manual[].hint` 原话）
3. **「做到哪了？」** → `entry_state`（派发进度，不是 `status`）＋ `verified`（做没做成）

---

> ★**改名（老板 2026-10-10）**：「**不要叫做对接中心，直接叫流程说明**」。
> ⇒ 全站用户可见名称一律用「**流程说明**」（页面标题、logo、大厅入口、工作间入口）。
> 该页**只讲不做**（零写请求）；**接入**操作在「Harness 接入」页，**激活类**操作在「工作间」。
> 下文若出现「对接中心」字样，均为**改名前的历史表述**（按原样保留，便于对照旧版）。

## 12. 「对接中心」是什么（老板 2026-10-09 一句话定义，权威口径）

> 老板原话：「**只是负责接入和激活一类对接任务，讨论和工作依旧在工作间进行。**」

据此划边界（平台按它实现、界面按它分页）：

| 归 **对接中心** | 归 **工作间** |
|---|---|
**接入**：注册 harness（含档位 / `action-scripts` 提示） | **讨论**：一级/二级/三级、插话、评审（`discuss` / `review`） |
**激活类**：建会话并命名（`establish-session` / `onboard-members`）、送桥（`deliver-bridge`）、抢救（`recover` / `recover-scan`） | **工作本身**：阶段整合（`integrate`）、产出与收尾（`complete`） |
为上述服务**只读状态**：`members-status` / `offline-report` / `compartments` / `library`（只读视图） | **决策**：选人（`select-members` / `members` / `confirm-members`）、**开工**（`start`） |
| | 资料库**写**（`library/update`，讨论产物） |

**一句话自检**：**在把外部东西接进来 / 唤醒起来吗？→ 对接中心；在讨论或推进工作吗？→ 工作间。**

- 「开工」（`POST /api/workshop/{ws}/start`）**归工作间**：它虽会触发激活，但语义是"**开始工作**"的决策；
  对接中心只提供"**补做某个人的接入 / 激活**"这类动作，**不提供"开工"**。
- 这条口径与前端写请求白名单一致（`register / establish-session / deliver-bridge / onboard-members /
  recover / recover-scan`），且**不含** `library/update`（它归工作间）。

---

## 13. 两条"不许假装"的硬规矩（复核时先看它）

1. **`verified=false` 一律「⚠ 待人工 / 未核实」**，界面不得出现"完成/成功"（准则⑤）。
2. **平台完全不认识的 harness**（没注册、也没有它的桥进程）：
   成员标 `blocked` + `entry_reason` 明写「平台不认识…先注册它、或改绑」——
   **不再回"已派发"**（旧逻辑会假 dispatched，而没有任何人能收到那条消息）。
   该判定对**员工路径与组长路径都生效**（`_harness_unknown`，唯一判定点）。

---

*示例Harness-E · 2026-10-08 · 事实来源：本仓库代码 + 隔离实例实测*

## 14. 启动设施现状（2026-10-10 冷启动实测后更新）

**日常启动**：双击桌面「启动合作社」→ 起 平台 + A2A + **grpc**（只起没在跑的）→
保证**一套**看门狗（有就收编、没有才起）→ 逐项健康汇总 → 用**内置浏览器窗**打开平台。

**看门狗：只有一套**（此前记的"两套并存"已清）：
- 现在跑的是 `deploy/watchdog_all.py`（守 platform + A2A），由 `deploy/watchdog_ensure.ps1` 保证唯一；
- 旧的独立守护（platform/grpc 两套）**不再自动启动**：启动文件夹 `ac_v4_*` = 0 项；
  计划任务 `AC4_Platform_Startup` 虽在（删它需管理员），但被"中和"—— 它调用的脚本**默认只体检、不起服务**；
- ⚠️ **重启后服务不会自己回来**（老板明确要"不要开机自启"）⇒ 开机后**双击快捷方式**即可。

**冷启动实测抓出并已修的 6 个坑**（提交 `a8f7276`，都记在这里防再犯）：
1. 批处理**必须先 `cd /d "%~dp0"`**：否则从别的目录启动时子进程报 `No module named 'agent_community.platform'`（窗口一闪，错误只在日志里）；
2. **grpc 已纳入**启动脚本（命令：`python -m agent_community.grpc_gateway --port <grpc> --api http://127.0.0.1:<平台>`）；
3. `watchdog_all.py` **只认** `--daemon/--interval/--project`：多传 `--platform-port` 等会让它 argparse 报错后立刻退出（看门狗静默起不来）；
4. PowerShell `Start-Process -ArgumentList` 里的**路径必须自带引号**：工程路径含空格，不加引号会被拆成多个参数；
5. `watchdog_ensure.ps1` 的 `-Root` 改为**可选**（默认=仓库根），避免调用方漏传即报 missing mandatory；
6. **别用 `for /f` 读常驻进程的输出**：看门狗继承管道句柄 ⇒ cmd 的 `for /f` 永不返回 ⇒ 启动脚本卡死；
   改为「输出重定向到文件 + `set /p` 读第一行」。
