# 更新日志（CHANGELOG）

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [4.8.15] - 2026-10-15

**对接中心 P4：激活操作台**（成员对接卡 + 批量 + 逐项如实回报）。

### 新增（界面，**只在单间范围**）
- 激活类端点本来就是 `/{ws}/…` 的语义 ⇒ **只有"这个间"的入口才出现这些操作**
  （总控要先选一个间再进来，避免"在一个页面里同时对多个间动手"，V34 §1）。
- **成员对接卡**（数据来自 `GET …/members-status`）：角色/名字、**通道状态**（已接入/未接入/已停用）、
  第几条通道、**会话名**、成员状态；孤儿成员（agent 已不在平台上）**单独告警并给恢复建议**；
  每张卡上五个动作：**建会话 / 送桥 / 抢救 / 停用通道 / 新建通道**。
- **批量**：`全员对接（建会话+命名）`、`掉线扫描`。
- **逐项如实回报**（V34 §7.3，本版最要紧的一条）：
  - 每步状态用**徽章**分三态：`✓ 已自证` / `⚠ 未核实`（自动但没自证）/ `⚠ 人工模式`；
  - 动作结果**只有 `all_verified === true` 才给 ✓ 的成功样式**；否则显示
    「⚠ 部分步骤为**人工模式**（未核实）—— 按指引做，平台不会把它当成已完成」；
  - 头部汇总直接说「**N 名有"人工模式"的步骤**（那些步骤没被核实，不算完成）」。
- **不做**"一键点完所有成员的所有步骤"这种会掩盖失败的按钮 —— 每个写动作逐个点击。

### 写请求白名单（V34 §4.3）扩到 **10 条**
`/api/harness/{pre-register,probe-register,register}` +
`/api/workshop/*/onboard-members`、`/recover-scan`、
`/member/*/{establish-session,deliver-bridge,recover,channel/retire,channel/establish}`。
页面里仍然**只有一条写通道** `dockPost()`（带白名单闸），路径全部来自 `DOCK_PATH` 模板表（**唯一来源**）。

### 守卫与验证
- `tests/test_docking_ui_static.py` 增至 **15 条**，本版新增：
  **白名单必须正好是那 10 条**（多一条=偷偷扩大权限面，少一条=按钮被自己的闸拦住）/
  **每个动作路径都要能被白名单匹配** / **激活面板只在单间挂载** /
  ★**未核实的步骤绝不许渲染成"完成"**（徽章必须给 ⚠，动作结果必须按 `all_verified` 分档）。
- 突变检验 ⇒ **11/11 KILLED**（含"总控也挂激活面板"、"未核实显示成✓已完成"、"白名单少一条"）。
- 真跑走查新增两条：`members-status` 真给成员；`establish-session` 在**没配动作脚本**的实例上
  返回 **`all_verified=False` + 2 条人工步骤指引** —— 也就是**真跑里守住了"不许假成功"**。
- ⚠️ 本轮又抓出一个**守卫助手的真 bug**：我那个"去注释"函数用 `re.sub(r"/\*.*?\*/", ...)`，
  而白名单里的 `'/api/workshop/*/onboard-members'` **字符串里就含 `/*`** ⇒ 它把字符串后半段
  当注释删了，造成"动作路径不在白名单里"的**假红**。现在只剥**行锚定**的块注释与行注释。

### 已知限制（如实标注）
- **P5 未做**：架桥辅助与教程/模板迁入、`register.html` 两步下线。
- 成员侧**还不会自动**产出结构化陈述（辩论模式的全员参与还差回报模板这一步）。
- 窗口激活、桥目录进回收站：**Windows 专属**，其它平台如实返回「不支持」。
- **非 Windows 上 AI 密钥是明文保存**（会明确告警并给出路：`chmod 600` 或环境变量 `AC_AI_API_KEY`）。
- CI 覆盖 Python **3.11**（Linux + Windows）；其它版本未逐一验证。

反馈问题请带上**版本号**（本版为 `4.8.15`）与 `<数据目录>/logs/server_stdout.log` 尾部。

## [4.8.14] - 2026-10-15

**对接中心 P3：登记（接入）＋ 写请求白名单纪律**。

### 新增（界面）
- `docking.html` 多了一张**登记卡**（**只在总控出现** —— 注册是全局能力，V34 §1 把单间限定为"本间的对接操作"）：
  - 字段只问**你手头有的东西**：一句话命令 / 一个目录 / 一个端口 / 唤醒方式（**默认让平台探**）；
  - **① 探测并登记**（`POST /api/harness/probe-register`，平台**自己先探一次**再补齐）、
    **② 只存草稿**（`POST /api/harness/pre-register`，稍后再探）；
  - 结果**如实画出来**：成功逐字段列出（含唤醒方式/档位事实），失败**原样显示后端的中文原因**，
    并明确写一句"这一页不会把失败显示成没内容"；平台探测的原始输出放 `<details>` 里可展开。

### 写请求白名单（V34 §4.3）—— 本版才真正需要它
- 页面里只有**一条写通道** `dockPost()`，它带**白名单闸**（不在白名单里的路径当场拒绝，不是偷偷发出去）；
- 白名单：`/api/harness/pre-register`、`/api/harness/probe-register`、`/api/harness/register`；
- **`/discuss` 出现即 FAIL**（对接中心不做讨论 UI、不转发讨论）。

### 走查当场抓出并修掉一个真缺陷
`POST /api/harness/pre-register` 与 `probe-register` 的错误信息是**英文**
（`harness_id required`），而 `register` 是中文 —— 违反 V34 §9.2「注册错误路径 → **后端中文 error**」
与准则④（第一次接入的人看不懂英文报错就卡在第一步）。
现已全部改为中文（heartbeat 那条同样处理），并让走查**断言"错误里必须有中文"**：
以后这类退化会被当场抓住，而不是靠人眼看。

### 守卫与验证
- `tests/test_docking_ui_static.py` 增至 **11 条**，本版新增：
  **写请求必须在白名单内** / **代码里（去注释后）不许出现 `/discuss`** /
  ★**写请求必须只有 `dockPost` 一条通道**（否则白名单闸可被绕过）/ 注册区**只在总控挂载** /
  注册失败**必须原样渲染 `esc(d.error`**。
- 突变检验 `temp/docking_ui_mutation_check.py` ⇒ **8/8 KILLED**（改坏三个前端文件，跑完按字节还原并核对）。
  ⚠️ 其中**两次是守卫自己有问题**、被突变体逼出来的：
  ① "注册失败要显示原因"第一版只查函数里有没有 `esc(`，而同一函数别处也有 ⇒
     把错误那行换成一句"操作未完成"依旧通过（**因为错误的原因通过**）；
  ② 我写了句注释"`/discuss` 出现即 FAIL"，结果**那句注释本身**含 `/discuss` ⇒ 守卫判自己红 ——
     现在**判代码前先剥掉注释**。
- 真跑走查（隔离实例 + 真 HTTP）：总览/单间收窄/404/悬空引用/**只读指纹不变**/
  **三条注册错误路径都返回中文 error**（本版新断言）/ **`docking.html`、`docking.js` 由平台真的提供**。

### 已知限制（如实标注）
- **P4~P5 未做**：激活操作台（成员对接卡：建会话/送桥/批量/抢救、通道停用新建）、
  架桥辅助与教程迁入、`register.html` 两步下线 —— 页面上**还没有**这些操作入口。
- 成员侧**还不会自动**产出结构化陈述（辩论模式的全员参与还差回报模板这一步）。
- 窗口激活、桥目录进回收站：**Windows 专属**，其它平台如实返回「不支持」。
- **非 Windows 上 AI 密钥是明文保存**（会明确告警并给出路：`chmod 600` 或环境变量 `AC_AI_API_KEY`）。
- CI 覆盖 Python **3.11**（Linux + Windows）；其它版本未逐一验证。

反馈问题请带上**版本号**（本版为 `4.8.14`）与 `<数据目录>/logs/server_stdout.log` 尾部。

## [4.8.13] - 2026-10-15

**对接中心 P2：`docking.html`**（`V34` §6 的界面第一步）—— 两个入口、同一页面、只读总览。

### 新增（界面）
- **`docking.html` + `docking.js`**（复用 `hub.css`，零新增样式）：
  - **两个入口、同一份实现**（V34 §1）：`/docking.html` 是**总控**（全局）；
    `/docking.html?ws=<工作间id>` 是**单间**（锁死这一个间）。
  - **总控**：工作间选择器（带每个间的"接入/待接/停用/掉线"四个数）＋ 每个 harness 的**绑定总览**
    （接在哪个工作间、哪个岗位、通道状态、会话名）＋ 唤醒事实（`interface_class` 中文标签、
    "平台能不能自动叫醒它"）。
  - **单间**：**不渲染工作间选择器**（V34 §1/§9.6 明令：工作间的入口只能进对应的那个间，不许越权）。
  - **悬空引用显式列出来**：成员引用了未登记的 harness 时，页面上单独一块说明"界面不能显示成接上了却谁也找不到"。
  - **空白引导**：一个 harness 都没登记时给三步上手（V34 §10 第 5 条"装好就能用"）。
- **入口接线**（V34 §5 第 1 步"能力追平、入口改向"）：
  - **大厅**主入口按钮 `🔌 注册 Harness` → **`🔌 对接中心`**（指向 `/docking.html`）；输入框下方的
    "接入员工"提示链接同样改向。`register.html` **保持可达**（旧链接不 404），只是不再从大厅主入口导向。
  - **工作间**侧边栏新增 `🔌 对接中心`，加载工作间时把 href 绑成 **`?ws=<本间id>`**
    （不带就退化成总控 = 能看别的间，**串范围**）。

### 本版是 **P2（只读）**，因此
- 整页**零写请求**（P2 不需要白名单；对接动作在 P3/P4，届时必须落在 V34 §4.3 白名单内）。
- 失败**原样显示后端的中文原因**，不许显示成"没有内容"（准则⑤）。

### 守卫与验证
- `tests/test_docking_ui_static.py` **9 条**（按结构、不看行号）：页面/脚本存在且读到那个只读端点 /
  **整页零写请求** / **单间不许出现工作间选择器**（选择器必须在"只在总控"的分支里、单间要清空）/
  范围从 URL 读且总控给出带 `?ws=` 的收窄链接 / **后端中文 error 要被取出来并渲染** /
  悬空引用要显式说明 / 空白引导要有可执行三步 / **大厅主入口按钮**指向对接中心 /
  **工作间入口必须带 `?ws=` 且在加载时绑定**。
- 突变检验 `temp/docking_ui_mutation_check.py` ⇒ **5/5 KILLED**（改坏三个前端文件，跑完按字节还原并核对）。
  ⚠️ 其中一次是**我自己写错**被抓出来：第一版"大厅入口"守卫只断言"文件里有 `/docking.html`"，
  而页面里**另一处提示链接**也有它 ⇒ 把主入口改回注册页依旧通过（**因为错误的原因通过**）；
  现在改成**盯住那个按钮本身**。
- 真跑走查（隔离实例 + 真 HTTP）：总览/单间收窄/404/悬空引用/**调用前后 `workshops.json` 指纹一字不变**，
  并新增一条 —— **`GET /docking.html` 与 `/docking.js` 由平台真的提供出来**（HTTP 200 + 内容标记），
  而不是"只躺在仓库里"。

### 已知限制（如实标注）
- **P2 只读**：注册、激活（建会话/送桥/批量/抢救）、架桥辅助迁入、`register.html` 下线（P3~P5）**都没做**；
  本版页面上还没有那些操作入口。
- 成员侧**还不会自动**产出结构化陈述（辩论模式的全员参与还差回报模板这一步）。
- 窗口激活、桥目录进回收站：**Windows 专属**，其它平台如实返回「不支持」。
- **非 Windows 上 AI 密钥是明文保存**（会明确告警并给出路：`chmod 600` 或环境变量 `AC_AI_API_KEY`）。
- CI 覆盖 Python **3.11**（Linux + Windows）；其它版本未逐一验证。

反馈问题请带上**版本号**（本版为 `4.8.13`）与 `<数据目录>/logs/server_stdout.log` 尾部。

## [4.8.12] - 2026-10-15

**辩论模式上界面**（`V52` 的最后一环）：工作间三级讨论区里的开关、陈述入口、跑一轮。

### 新增（界面）
- `workshop.html` 的**三级讨论区上方**多了一条辩论面板：
  **开关**（开/关，默认关，界面上**明写"默认关"**）、**＋陈述**（内联表单：谁说的 / 一句话结论 /
  证据（一行一条 `kind|ref|verified`）/ 推理）、**⚖ 跑一轮**。
- **裁决不需要单独渲染**：它本来就是**追加**的一条 `orchestrator` 消息（zone=3），刷新即可看到 ——
  也就不存在"界面上的结论"和"讨论区里的结论"两份口径。
- **没有证据的陈述当场提示**："这条没有证据，裁决里权重为 0"（V52 §5.1：不许显示成"参与裁决"）。
- 三道闸门的拒绝原因**原样弹给用户**（后端给的都是中文原因）。

### 一处纪律：前端**不许**重算裁决逻辑
面板只做"读状态 / 开关 / 提交 / 触发"四件事；**权重、同源度、分数、阈值全在后端**
`platform/debate.py`。否则界面上显示的与裁决书就是**两套口径**（规则 20）。
守卫里有一条专门盯这个：任何 `jaccard` / `threshold` / `score_cluster` 出现在该段 JS 里即判红。

### 守卫与验证
- `tests/test_debate_ui_static.py` **6 条**（按结构、不看行号）：部件齐不齐 /
  **`loadDebate()` 必须在 `loadWorkshop()` 里被调用**（只加面板不接线 ⇒ 用户永远看到"关"）/
  面板位置在三级讨论区之后 / 该段 JS 不许重算裁决 / **用户可见的两处都要写"默认关"** /
  **用户可见的两处都要提示"权重为 0"**。
- 突变检验 `temp/debate_ui_mutation_check.py` ⇒ **4/4 KILLED**（临时改坏 `workshop.html`，
  跑完**按字节还原并核对**）。
  ⚠️ 其中两次是**我自己写错**被抓出来的：① 第一版 M2 只改了属性、**根本没挪位置**（突变体无效）；
  ② 第一版"默认关"守卫只查**全文件**有没有那三个字 —— 而注释里也有 ⇒ 把界面文案删干净它照样绿
  （**因为错误的原因通过**）。现在只查**用户真正看得见的两处**。
- `temp/check_workshop_ui.js`（node）：内联脚本**语法**检查（`new Function` 只解析不执行）+
  16 条静态断言 —— 实测 **0 语法错误**；`hub.js` 过 `node --check`。

### 规则 20 整体同步（这件事的对外表达一次全改）
- **界面**：`workshop.html` 面板（上面）；
- **说明书**：`hub.js` 的三级讨论卡补上"辩论模式（可开可关，默认关）"整段口径（讲清"不是投票"、
  同源降权、无证据权重 0、最多 3 轮、入口在哪）；
- **设计稿**：`V52` 增补 §8 **词汇对齐** —— 平台**已有** `platform/handover.py` 的
  `claim + evidence{commit,file,test}`（完成声明），与辩论的 `evidence{kind,ref,verified}`（来源身份）
  **不是同一件事、不该合并**；桥接方式是 `kind` 留自由文本
  （如 `{"kind":"file","ref":"<路径>","verified":true}`），**不新增第二套字段名**；
- **文档**：`项目完整模型.md` §11 现状表同步为"后端已接线 + 界面已做"。

### 已知限制（如实标注）
- 成员侧**还不会自动**产出结构化陈述（需要回报模板/话术也带 `claim/evidence/reasoning` 三段）——
  现在由用户/组长/平台 AI 通过面板或 API 提交。这一步做完，"辩论"才真正是全员参与的。
- 对接中心 P2~P6、`register.html` 两步下线未开始。
- 窗口激活、桥目录进回收站：**Windows 专属**，其它平台如实返回「不支持」。
- **非 Windows 上 AI 密钥是明文保存**（会明确告警并给出路：`chmod 600` 或环境变量 `AC_AI_API_KEY`）。
- CI 覆盖 Python **3.11**（Linux + Windows）；其它版本未逐一验证。

反馈问题请带上**版本号**（本版为 `4.8.12`）与 `<数据目录>/logs/server_stdout.log` 尾部。

## [4.8.11] - 2026-10-15

**辩论模式接线**（`V52` 的第二半）：开关、收陈述、跑裁决、**裁决落到三级讨论区**。

### 新增
- **工作间开关 `debate_enabled`（默认关）** 与 **`debate_state`**（陈述 / 轮次 / 上轮证据 / 上次裁决，
  **落盘**：重启后开关与轮次不会丢）。
- **四个端点**（挂在 `discuss`/`review` 同组）：
  | 端点 | 作用 |
  |---|---|
  | `GET  /api/workshop/{ws}/debate` | **只读**：开关 / 轮次 / 已收陈述 / 上次裁决 |
  | `POST /api/workshop/{ws}/debate` | 开/关（写、落审计）|
  | `POST /api/workshop/{ws}/debate/statements` | 提交**结构化陈述**（观点 → 证据 → 推理；写、落审计）|
  | `POST /api/workshop/{ws}/debate/run` | 跑一轮并把裁决**追加**到三级讨论区（写、落审计）|
- **三道闸门，拒绝时都如实说明原因**（不静默、不假装）：
  ① 不在三级讨论（`review`）⇒ 400「辩论只在三级讨论里跑（当前状态：…）」；
  ② 模式没开 ⇒ 400「辩论模式未开启（默认关）」；
  ③ 还没有任何陈述 ⇒ 400「还没有任何结构化陈述」。
- **裁决只追加、不篡改**：作为一条 `orchestrator` 消息写进 **zone=3**（带结构化 `meta`），
  **不改任何人的原话、不删任何发言**；没证据的发言**照旧留在讨论区**，只是裁决里权重为 0。
- 陈述**不要求带证据**（那是常态），但结构上如实标成"无证据"；陈述有上限（防状态文件无限增长）。

### 真跑走查抓出的一个真 bug（已修）
第一版在路由层**自己手写**了一段证据归一（`x.get("kind")`），而 `Statement.evidence` 里装的是
`Evidence` 对象（没有 `.get`）⇒ **AttributeError ⇒ HTTP 500**，而且崩在**落盘之前**：
表现为「裁决消息已经落到讨论区了，但轮次没保存」→ 重启后 `round` 仍是 0。
修法：改用内核的 `evidence_set()`（**一处真相**）—— 接线层不要自己再算一遍。
（这正是"接线必须真跑一遍"的价值：单元测试没覆盖到这条路径，走查当场抓住。）

### 守卫与验证
- `tests/test_debate_endpoints.py` **6 条**：缺 claim/缺 speaker 要拒（裁决要指名道姓，不许匿名）/
  **没有证据也要收下**（权重 0 而已）/ 证据条目归一与"没写 verified 就当没核对过" /
  坏数据不许让端点炸 / 陈述有上限。
- 真跑走查 `temp/walkthrough_debate.py`（隔离实例 + 真 HTTP + **重启实测**）：
  三道闸门都按预期拒绝 / 真裁决**守住了"不做投票"**（两个同源的人投甲 vs 一个证据独立的人投乙 ⇒ 出乙）/
  讨论区**只多一条**且原有发言逐字未变 / **重启后** 开关、轮次、陈述都还在。

### 已知限制（如实标注）
- **界面入口还没做**：目前只有 API（`可开可关` 在接口层面成立），工作间页面上还没有按钮
  —— 本轮刻意先让后端与守卫立住，界面下一轮补（准则③"用户视角"还差这一步）。
- 成员侧还不会**自动**产出结构化陈述（需要回报里带 `claim/evidence/reasoning`，
  即"对外表达"的模板/话术同步）—— 现在由用户/组长/平台 AI 通过 API 提交。
- 对接中心 P2~P6、`register.html` 两步下线未开始。
- 窗口激活、桥目录进回收站：**Windows 专属**，其它平台如实返回「不支持」。
- **非 Windows 上 AI 密钥是明文保存**（会明确告警并给出路：`chmod 600` 或环境变量 `AC_AI_API_KEY`）。
- CI 覆盖 Python **3.11**（Linux + Windows）；其它版本未逐一验证。

反馈问题请带上**版本号**（本版为 `4.8.11`）与 `<数据目录>/logs/server_stdout.log` 尾部。

## [4.8.10] - 2026-10-15

**三级讨论区的「辩论模式」内核**（`V52`）：**证据驱动纠错，不做投票定对错**。

### 背景（为什么不能靠"多数"）
多数投票有效的经典前提是 **Condorcet 陪审团定理**：投票者**相互独立**、各自正确率 > 50%。
我们的 agent 常常**共享同一批数据源 / 同构模型 / 相似检索路径** ⇒ 误差**高度相关** ⇒ 前提不成立，
此时"多数"不是纠错，是**互相强化**；无证据锚定时，轮次越多只会放大初始错误。

### 新增：`platform/debate.py`（**纯逻辑内核**，无 IO、无第三方依赖）
把判定对象从「答案」换成「**证据**」——这样每一步都可计算、可审计，且**不需要预先知道正确答案**
（用结论算权重、再用权重定结论是逻辑循环）。照《多Agent辩论_修订版》落成可执行的东西：

- **证据同源度**：证据归一成身份串（小写、去 `#fragment`、去尾部 `/`）后算 **Jaccard**；
  `≥ 0.5` 判同源 ⇒ **合并成一个簇，总权重 1**（两个同源 agent 同错，合并后仍是 1，**不是 2**）；
  证据互相独立的簇各保留权重 1。
- **权重 0 的两类**：**无任何证据**的断言（`unsupported`）、**与外部校验器冲突**的结论
  （工具/计算返回与它互斥）—— 都不进结论候选；但**仍然留痕在排名里，不从讨论区删原话**。
- **轨迹评分（没有"文采分"）**：`簇分数 = 可验证证据项数 − 与已有证据冲突项数 − 无证据断言数`。
  之所以**只由可核对的东西构成**：修订稿明确警告过，若评分只看"逻辑连贯/说服力"，
  **自洽的错误答案会套利**（把"票多者胜"换成"说得漂亮者胜"）。
- **非共识**：不要求一致；**同分如实并列**，不硬选（无 ground truth 时我们没资格裁判"谁对"）。
- **触发阈值（成本闸门）**：模式**默认关**；开了也只在这些信号下才跑 ——
  结论互斥且证据不相交 / 与校验器冲突 / 错误代价高 / 用户显式要求；
  简单事实查询与纯主观偏好**不触发**。
- **终止条件**：证据链闭环 / 无新信息进入 / 边际信息增益不足 / 轮次上限 **3**。
- **输出四件套**：结论 + 置信度 + 关键证据 + 分歧点；置信度**明确标注为启发式示意值（不是概率）**，
  成本口径（`N×R` 倍 token）也随裁决一起如实回报。

### 守卫与验证
- `tests/test_debate.py` **19 条**，其中两条是"不做投票"的**试金石**：
  ① 多数但**全都没证据** 必须输给 少数但有独立证据；
  ② **人多但证据同源（分数 1）** 必须输给 **人少但证据独立（分数 2）**。
- 突变检验 ⇒ **5/5 KILLED**（含"结论按人多选"这个把不做投票偷偷改回投票的突变体）。
  ⚠️ 值得一提：第 ② 条试金石是**因为突变体逃掉才补的** —— 第一版试金石里"多数"那簇
  **权重是 0**、排在候选之外，于是"按人数选"的突变体照样活（守卫**因为错误的原因**通过）。

### 已知限制（如实标注）
- 本轮只落地**纯逻辑内核**（可单测、无 IO）。
  **工作间开关 `debate_enabled` 与 `discuss` 的 review 分支接线尚未做**（下一轮），
  因此本版**对用户还没有可见入口**；这是刻意的分步：内核先被守卫钉死，再接界面。
- 对接中心 P2~P6、`register.html` 两步下线未开始。
- 窗口激活、桥目录进回收站：**Windows 专属**，其它平台如实返回「不支持」。
- **非 Windows 上 AI 密钥是明文保存**（会明确告警并给出路：`chmod 600` 或环境变量 `AC_AI_API_KEY`）。
- 视觉拉起的**消息式注入只在 Windows 有**；其它平台只有剪贴板半自动兜底。
- CI 覆盖 Python **3.11**（Linux + Windows）；其它版本未逐一验证。

反馈问题请带上**版本号**（本版为 `4.8.10`）与 `<数据目录>/logs/server_stdout.log` 尾部。

## [4.8.9] - 2026-10-15

**修发布流程本身**：让「版本号」与「CHANGELOG 条目」**永远在同一笔提交里**。

### 为什么（真实事故）
CI 有一条守卫 `test_changelog_mentions_current_version`：**CHANGELOG 必须含当前版本的条目**。
而发布流程原本是「**先撤回 CHANGELOG**（为了让预检『oss 工作树必须干净』通过）→ 同步 + 提交 → 事后补条目」。
于是 **v4.8.8 提交那一刻**：版本已经升到 `4.8.8`，CHANGELOG 里最新还是 `4.8.7`
⇒ **CI 两个测试作业全红**（`portability` 反而是绿的）。
根因不是「忘了补」，是**流程把两件必须同时存在的事拆开了**。

### 变更
- **发布脚本新增 `-ChangelogFile <条目.md>`**：在同步之后、`git add -A` **之前**，
  由 `tools/insert_changelog_entry.py` 把条目插到 oss 的 CHANGELOG 顶部 —— 版本与条目**同一笔提交**。
- **新工具 `tools/insert_changelog_entry.py`**：
  - `--file <条目.md>` 插入；`--check` 只校验「当前版本有没有条目」（CI 同款判据）；
  - **幂等**（该版本已有条目就跳过，发布脚本可重复跑）；
  - **保住换行风格**（按现有文件的 CRLF/LF 写回，不制造巨大的假 diff）；UTF-8、不加 BOM。
- **发布面闸门补强**（上一次加的闸门其实是个**纸老虎**，本次一并修掉）：
  1. 原来用 `$oss` 拼路径，而它在脚本里是 **`…-oss\agent_community` 子目录** ⇒
     三个检查 **全部落进"跳过"分支**，闸门等于没跑；现在一律用**仓库根** `$ossRoot` 并
     `Push-Location` 到根（否则 `git ls-files` 只看到子树，**扫描面是错的**）；
  2. 新增第 4 条检查：**版本必须有 CHANGELOG 条目**；
  3. **跳过不再等于通过**：只要有检查被跳过就**中止发布**（不许闸门变摆设还打印 OK），
     通过时明说"几条都真跑过"。

### 守卫与验证
- `tests/test_changelog_entry_tool.py` **6 条**：插到最上面 / 文件头不挪 / 旧条目不丢 / **幂等** /
  **保住 CRLF**（不许混入裸 LF）/ 没有版本标题行要拒绝 / 空 CHANGELOG 也能插 /
  判据与 CI 同口径。
- 真演练：拿 oss 的 CHANGELOG 当靶子 —— 删掉最新条目后 `--check` 返回 **1**，
  插入后与备份**字节完全一致**（542 行）。

### 已知限制（如实标注，未变）
- 对接中心 **P2~P6（界面 `docking.html`、`register.html` 两步下线）未开始**。
- 窗口激活、桥目录进回收站：**Windows 专属**，其它平台如实返回「不支持」。
- **非 Windows 上 AI 密钥是明文保存**（会明确告警并给出路：`chmod 600` 或环境变量 `AC_AI_API_KEY`）。
- 视觉拉起的**消息式注入只在 Windows 有**；其它平台只有剪贴板半自动兜底。
- CI 覆盖 Python **3.11**（Linux + Windows）；其它版本未逐一验证。

反馈问题请带上**版本号**（本版为 `4.8.9`）与 `<数据目录>/logs/server_stdout.log` 尾部。

## [4.8.8] - 2026-10-15

**对接中心 P1：只读跨间总览** `GET /api/docking/overview` —— 补上一个此前**没有任何端点能回答**的问题。

### 新增
- **`GET /api/docking/overview`**（`platform/routers/docking.py`）：回答「**这个 harness 现在接在哪个工作间、
  哪个岗位**」。此前 `GET /api/harness/list` 只给"已注册的 harness"，工作间侧 `members-status` 只给一个间
  ⇒ 总控（大厅）视角根本立不住。
- **两种范围、同一份实现**：不带参数＝全局（总控）；`?ws=<工作间id>`＝只回该间（工作间入口）。
  不存在的 ws 返回 **404 + 中文 error**（**不许**悄悄退化成全局）。
- **绑定关系由工作间成员表反查**（成员里记着 `harness_ids` + 通道状态），**不新建存储、不留影子状态**。
- **`dangling_bindings`（新增的诚实字段）**：成员引用了**未注册**的 harness（注销/改名/手改数据都会造成）
  时必须报出来 —— 否则界面会显示"接上了"却谁也找不到（准则⑤ 失败要说话）。

### 与设计稿的一处偏差（实现期以现行裁定为准）
- 设计稿形状提案里有 `access_tier`，那已被裁定为「**只写不读、界面话术一律不引用**」
  （同一个"档位"概念历史上曾有三份定义）⇒ 改为输出 `tier_guidance` 的**事实判断**：
  `interface_class`（有命令 / 盯目录 / 常驻 HTTP / 只能人工）+ 中文标签 +
  `can_be_woken_by_platform` + `needs_protocol`。设计稿里的 `has_template` 实测**不存在**于
  `list_sessions()`，**不凭空造**。

### 守卫与验证
- `tests/test_docking_overview.py` **10 条**：跨间绑定正确 / 四个计数分开给 / 单间真收窄 /
  不存在的 ws 不退化成全局 / 悬空引用报出 / **只读无副作用**（同一输入两次一致 + 成员数据不被改）/
  接入事实取自 `tier_guidance` / **载荷里不许出现 `access_tier`** / 端点**真的挂进 app** /
  只读路径里不许出现写调用（**AST 结构判据**）。
- 突变检验 ⇒ **5/5 KILLED**。
- 真跑走查（隔离实例 + 真 HTTP）：注册两个 harness → 建间绑成员 → 总览里两个 harness 各带 1 条绑定、
  `interface_class` 正确（`has_http_api` / `manual_only`）、未注册那个进 `dangling_bindings`；
  `?ws=` 真收窄；不存在的 ws → 404；★**全部 GET 调用前后 `workshops.json` 的 sha256 一字不变**。

### 已知限制（如实标注，未变）
- 对接中心 **P2~P6（界面 `docking.html`、`register.html` 两步下线）未开始**。
- 窗口激活、桥目录进回收站：**Windows 专属**，其它平台如实返回"不支持"。
- **非 Windows 上 AI 密钥是明文保存**（会明确告警并给出路：`chmod 600` 或环境变量 `AC_AI_API_KEY`）。
- 视觉拉起的**消息式注入只在 Windows 有**；其它平台只有剪贴板半自动兜底。
- CI 覆盖 Python **3.11**（Linux + Windows）；其它版本未逐一验证。

反馈问题请带上**版本号**（本版为 `4.8.8`）与 `<数据目录>/logs/server_stdout.log` 尾部。

## [4.8.7] - 2026-10-15

**视觉拉起（把"名字进数据"接成"真的送进会话"）＋ 一条静默送垃圾的老缺陷（剪贴板把中文写坏）**

### 新增：视觉拉起（`V51`）
- **`platform/visual_launch.py`**：按成员的**会话名**（`session_aliases[0]`，脚本不写死名字）
  把一段文本送进那个会话窗口。档位**照抄 `V25` §5、不新增**：
  `wmpaste`（Windows 消息式注入，**不抢前台**）＋ `clipboard`（**跨平台半自动兜底**：放剪贴板 + 人点一下再粘贴）；
  OCR/CV 仍**不进核心依赖**。
- 端点（归对接中心的"激活"边界）：`GET …/member/{id}/visual-options`（**只读**，给界面按钮可用性）、
  `POST …/member/{id}/visual-launch`（写动作，**落审计**）；步骤 9 的响应里**附上"下一步能怎么拉起"**
  （此前写完名字就没有然后了：`tools/inject_window.ps1` 早就在仓库里，**平台从没调用过它**）。

### 修复
- **★剪贴板把中文写坏（可移植性缺陷条 E）**：Windows 分支原来把文本走 **stdin**，
  而 PS 5.1 按控制台码页解码 stdin ⇒ **UTF-8 被当 GBK**，实测 `中文标记` → `涓\ue15f枃鏍囪\ue187`，
  **而函数照常返回成功** ⇒ 中文 Windows 上「剪贴板型唤醒」一直在送乱码。
  改为把文本 base64 嵌进命令（命令全 ASCII），PowerShell 里解回 UTF-8 再 `Set-Clipboard`。
- **`-List` 的解析**：那几列是按宽度对齐打印的，而 WinForms/Electron 的窗口类名**会超过对齐宽度**
  ⇒ 列间只剩一个空格；按"两个空格"切分会把 `title` 吃掉、`options()` 永远报"0 个候选窗口"
  （界面显示没有可注入的窗口，**实际有**）。改用正则解析。

### "不假装成功"（本版最要紧的一条）
- 实测：脚本打印 `[inject] WM_PASTE … (15 chars)`（看着成功），**而受控靶子窗口自报文本框 `len=0`** ——
  **文本根本没落进去**。⇒ `launch()` **默认带 `-Verify` 读回核对**，并把结论如实分三态：
  `True` 读回一致 / `False` 读回不一致（`ok=False`）/ `None` 没能自证（目标无标准文本框，或调用方明确不核对）——
  `None` 时 `detail` **明说"这只是我们送出了输入，没有读回核对"**。

### 守卫与验证
- `tests/test_visual_launch.py` **17 条**（没命名不乱来 / 非 Windows 如实不支持且不偷动剪贴板 /
  剪贴板真写中文并读回比对 / 找不到窗口绝不算成功 / `auto` 退兜底要说清为什么 /
  `-Verify` 真的被传下去 / 读回不匹配必须报失败 / 读不回来要如实说"没自证" / `-List` 长类名解析 …）。
- 突变检验 `temp/visual_launch_mutation_check.py` ⇒ **5/5 KILLED**。
- 端到端诚实性判据 `temp/visual_launch_e2e.py`（**报成功但内容没到 = FAIL**；如实失败 = PASS）；
  分工链路端到端 `temp/walkthrough_fengong.py`（真起实例看盘上产出）。

### 已知限制（如实标注，未变）
- 窗口激活、桥目录进回收站：**Windows 专属**，其它平台如实返回"不支持"。
- **非 Windows 上 AI 密钥是明文保存**（会明确告警并给出路：`chmod 600` 或环境变量 `AC_AI_API_KEY`）。
- 视觉拉起的**消息式注入只在 Windows 有**；其它平台只有剪贴板半自动兜底。
- 容器路线已在真机走查；`network_mode: host` 那条路**仍未验证**。
- CI 覆盖 Python **3.11**（Linux + Windows）；其它版本未逐一验证。

反馈问题请带上**版本号**（本版为 `4.8.7`）与 `<数据目录>/logs/server_stdout.log` 尾部。


## [4.8.6] - 2026-10-15

**可移植性扫描器：docstring 与「代码里的字符串」分开判（A 项）** —— 继续修**判据本身的可信度**。

### 变更
- **扫描器改用 AST 区分 docstring 与代码**（原来靠**数三引号**猜）。两处真收益：
  1. **不再误降级**：代码里一个三引号字符串**不是** docstring，旧实现会把它当文档降成 warn
     ⇒ 产品逻辑里真写死的地址被**漏判**；现在它按代码处理（该 fail 就 fail）。
  2. **文档不再刷屏**：docstring 里解释「默认值长什么样」（如 `AC_OLLAMA_HOST` 默认
     `http://127.0.0.1:11434`）**不再计入命中** —— warn 从 **96 条降到 71 条**。
- **但 `一律` 级规则在 docstring 里照抓**（本机绝对路径 / 真实凭据样式）：文档里写出别人真实的
  私有路径或密钥样式本身就是问题（HANDOVER 记过：一次全仓扫描抓出 16 处私有根路径，
  其中 5 处是当天新引入的，就落在 docstring 与测试夹具里）⇒ **"过滤 docstring"不放过这一类**。
- **解析失败时保守回落**：语法错的 `.py` / 非 Python 文件退回旧启发式，**宁可多报，不静默放过**。

### 守卫
- `tests/test_portability_report.py` 增至 **10 条**，新增 `TestDocstringVsCode`（5 条）：
  docstring 里的本机地址不算命中 / 代码里写死仍然 fail / **三引号字符串不被误当 docstring** /
  docstring 里的私有路径仍被报出 / 解析失败仍保守报出。
- 突变检验 `temp/portability_report_mutation_check.py` ⇒ **7/7 KILLED**
  （新增 M5 退回数三引号 / M6 连 `一律` 级也放过 / M7 文档里的地址又算命中）。
- ⚠️ 其中一条**守卫自己踩过坑**（留作教训）：合成文本最初把「Windows 用户目录样式」的路径写成
  **单反斜杠**，而它在**被 ast 解析的源码里是非法转义** ⇒ 走了回落分支 ⇒ AST 路径根本没被考到、
  突变体 M6 因此存活。现在用例里**先自证合成文本是合法 Python**，否则测的是回落分支而不是判据。
  （补记：本行初版把那个路径**原样写进了 CHANGELOG**，结果被本扫描器的「本机绝对路径」规则
    判成 **fail**、CI 三个作业全红 —— 文档里举例要写成"样式描述"，别抄真实路径的写法。）


## [4.8.5] - 2026-10-15

**让"扫描器会解释自己"** —— 修的是**判据本身的可信度**，不是产品行为。

### 变更
- **`tools/check_portability.py` 的警告档会解释自己了**。它长期只印一个数字加一屏行号；
  实测**逐条看过 97 条之后，没有一条是产品面硬编码** —— 它们是：
  **测试夹具的"陷阱串"**（如 harness 名 + 用户目录那类反面样例）、
  **说明历史修复的 docstring**（"这里原来写死 …"）、
  **可被环境变量覆盖的 dev 验收脚本兜底**（`process.env.AC_FE_BASE || 'http://127.0.0.1:…'`）、
  以及**默认值表**（`AC_OLLAMA_HOST` 默认 `http://127.0.0.1:11434` 这种出厂默认，本就是设计）。
  现在的警告区按**规则**与**位置**分组汇总，并明说"这些**不阻断**、不是产品逻辑里的写死"。
  - **为什么这不是小事**：一个不解释的大数字会让人以为产品千疮百孔 ⇒
    下一个人就去加 `--exclude` ⇒ **判据失效**（本项目在 `check_productization` 第一版
    52 条噪音上吃过一次同样的教训）。
  - 新增 `--warn-detail`：要看全部时用（默认只列 5 条 + 汇总）。
- **`.txt` 产物不再参与扫描**：`tests/offline_assert_report.txt` 是某次断网自证的**输出报告**，
  拿"源码硬编码"的尺子去量它属于**判据错位**。白名单里的 `.txt`（`requirements.txt`）照扫。
- **git 不可用时扫描器会"悄悄换一副尺子"**：`git ls-files` 拿不到（不是 git 仓库，或被 git 拒绝，
  如 WSL 里的 `dubious ownership`）时会退化成**整树遍历** ⇒ **扫描面变了**。
  实测踩到：同一份仓库，Windows `warn=96`、WSL `warn=121`，**差别全部来自这个退化分支** ——
  两个环境数字对不上、却**都不说自己扫了什么**。现在走退化分支时报告里会**明确警告**
  （并给出修法：`git config --global --add safe.directory <仓库路径>`）。

### 修复（工具）
- **`publish_acv4.ps1` 的 push 段会静默卡死**：实测在 GitHub 直连被限速到 <1 B/s 时，
  裸 `git push` **既不报错也不退出**（4 个 git 进程零 CPU 静挂 **24 分钟**，发布会一直卡着）。
  现在 push 与 `ls-remote` 都带 `-c http.lowSpeedLimit=1 -c http.lowSpeedTime=30`，
  太慢就判失败、换 IP；**唯一可信判据仍是直接问远端**（`ls-remote` 的 sha == 本地 HEAD）。

### 守卫
- 新增 `agent_community/tests/test_portability_report.py`（5 条）：
  ① **报告印出来的 `fail=/warn=` 必须等于 `scan()` 的真实条数**（不虚报、不漏报）；
  ② 警告区必须**说清"不阻断"**并按规则/位置分组（每条命中都必须带 `where`，否则分组会退化）；
  ③ `.txt` 产物不扫、白名单 `.txt` 照扫；④ **反向证明判据没被"降噪"降没了**
  （产品逻辑里写死 `http://127.0.0.1:18999` 仍然必须命中 fail 规则）；
  ⑤ git 不可用时走退化遍历**必须留标记**（两个环境的数字才有解释）。
- 突变检验 `temp/portability_report_mutation_check.py` ⇒ **4/4 KILLED**
  （又去扫 `.txt` / 报告数虚报 +1 / 命中不再带 `where` / 退化遍历不留标记）。



## [4.8.4] - 2026-10-15

**容器路线真机验证 + 两条"说话不算数"的缺陷修复** —— `PRODUCTIZATION.md` 欠账表第 2 条还清、
**平台专属 `skipif` 由 1 条降到 0 条**，并在走查/复现中抓出并修掉**可移植性缺陷条 C 与条 D**
（见 `PORTABILITY.md` §5.1）。

### 修复
- **`AC4_TOKEN` 是"设了也没用"的令牌**（条 C，与 2026-10-14 的 `AC_HOST` 同族）：
  README 环境变量表 / `env_spec` / **`docker-compose.yml` 头部**三处都承诺
  「在 `.env` 里设 `AC4_TOKEN=<你的令牌>`，请求带 `Authorization: Bearer <令牌>`」，
  而实现**只读 `--token` 命令行** ⇒ 容器里（`Dockerfile` 的 `CMD` 没有 `--token`）
  与 `python -m agent_community.cli start`（`Popen` 也没传）**设了令牌完全无效**。
  真机实测打出来的是 `403 {"error":"服务未配置访问 Token"}` ——
  **看起来像"你没配"，其实配了**：这是最坏的一类失败（失败不说话）。
  - 修法：新增 `server.collect_allowed_tokens()`，把 `--token` **＋ 环境变量 `AC4_TOKEN`** 合并
    （`__main__` 与 `platform.server` 双副本**一起**更新）；
    启动时如实打印「外部访问令牌已配置 N 个（非本机请求需带 Bearer）」或
    「未配置 ⇒ **只有本机能访问**（怎么开）」。
- **AI 密钥"已加密保存"是假话（非 Windows）**（条 D，真 Linux 上复现出来的）：
  `config.save_config()` **无条件**打印「AI API Key 已加密保存（DPAPI 当前用户绑定）」，
  而 `_encrypt_secret` 在非 Windows 上是**明文回退** ⇒ 真 Linux 实测：屏幕说"已加密"、
  磁盘上是 `sk-repro-…` **原文**。一句话同时违反 README「本地配置里会加密保存」与
  《安全披露_CDP镜像与密钥存储》「非 Windows 降级为明文保存**并在保存时打印告警**」两条承诺
  —— **对安全属性撒谎**是最坏的一类"失败不说话"。
  - 修法：按**实际落盘形态**分档说 —— 带 `dpapi:` 前缀 → 「已用 DPAPI 加密保存（当前用户绑定）」；
    否则 → 「**警告**：当前系统没有 DPAPI（非 Windows），AI API Key 以**明文**保存在 …，
    请限制该文件权限（如 `chmod 600`），或改用环境变量 `AC_AI_API_KEY`（不落盘）」。
  - README 环境变量表同步改成「Windows 走 DPAPI 加密 / 其它系统明文并打告警」。

### 文档（规则 20：一次全改）
- `docker-compose.yml` 头部把安全边界说得**更准**：端口映射下**没配置** `AC4_TOKEN` 是
  **403「服务未配置访问 Token」**；**配置了**但请求没带令牌是 **401**；带对的是 **200 running**。
  这两档的差别本身就是"令牌有没有被读到"的可观测证据。
- `PORTABILITY.md` §5.1 从「两个真缺陷」变「**四个**」，条 C / 条 D 各自记录症状 / 修法 / 守卫 / 突变结论；
- `PRODUCTIZATION.md` 欠账表：**看门狗跨平台**、**容器路线**、**平台专属 skipif** 全部移入「已还」表。

### 守卫（按真结果写，且验过有牙）
- `agent_community/tests/test_ac4_token_env.py`（**9 条**）：真读环境变量、
  真调 `_auth_check`（用真的 `Request`，`client.host` 给成**非本机地址**）、真调中间件、
  **真起一个只设 `AC4_TOKEN`、不带 `--token` 的服务进程**。
- `test_security.py::test_save_config_message_matches_disk`：**平台无关的不变量** ——
  存密钥时说的话必须与磁盘上的字节一致；
  `test_secret_storage_roundtrip_on_every_platform`：**撤掉原来那条整条 skip**，
  两个平台都验往返、平台差异如实断言。
- 突变检验：`temp/ac4token_mutation_check.py` **2/2 KILLED**；
  `temp/dpapi_mutation_check.py` **两个平台各跑一遍**、各自的关键突变 KILLED
  （"无条件说已加密"只在 Linux 咬得住，"无条件说明文"只在 Windows 咬得住 —— 合起来才是完整的不变量）。

### 实测（真机 · 两阶段 · 都真起容器）
- `docker compose build` → 阶段 A（**配了**令牌）：不带令牌 **401 有原因**、带对令牌 **200 running**；
  阶段 B（**完全不配**）：**403 且文案就是「服务未配置访问 Token」**、带任何令牌仍 403。
- 容器内 `python -m agent_community.platform.procprobe` ⇒ **`backend=procfs`**
  （**V50 的纯标准库模块在干净 Linux 镜像里能跑**）；容器内 `test_procprobe.py` +
  `test_ac4_token_env.py` **21 条全绿**。
- 真 Linux（WSL）：条 D 修前/修后**同一条命令**对照（修前说"已加密"、修后如实说"明文"），
  `test_security.py` **42 条全绿**，`check_productization.py` **平台专属 skip=0**。

## [4.8.3] - 2026-10-15

**看门狗跨平台化（欠账清偿）** —— 看门狗从此不再只是"Windows 上能用的那只手"。
对应 `PRODUCTIZATION.md` 欠账表第 1 条；设计稿 `design-docs/V50_看门狗stdlib跨平台化与纯标准库进程探测.md`。

### 修复
- **看门狗在 Linux 上等于没有**（`tools/watchdog.py`、`deploy/watchdog_all.py`）：
  这两个文件承诺「全程标准库实现、无第三方依赖」（它是"救一个坏掉的环境"的那只手，
  那时连依赖都可能装不上）⇒ **不能引 psutil**，于是它们的进程发现一直是 **Windows 专属**实现
  （`powershell` / `netstat` / `tasklist` / `taskkill`）。后果不是报错，而是
  **在 Linux 上静默降级**：端口被谁占着查不到、僵死进程杀不掉 ⇒ 服务挂了不会重生。
- **`is_pid_alive()` 把僵尸当活着的**（真 Linux 上实测出来的）：进程被 `SIGKILL` 后先进入僵尸态，
  `os.kill(pid, 0)` 对它**照样成功** ⇒ `kill_pid()` **永远返回 False**。
  Windows 没有僵尸态，所以这个问题在本机怎么测都是绿的。现在认 `/proc/<pid>/stat` 的状态字符，
  `Z` 不算活着。

### 新增
- **`agent_community/platform/procprobe.py`**：**零第三方依赖**的本机事实探测
  （进程表 / 命令行 / pid 存活 / 端口归属 / 结束进程）。Windows 走本机命令、Linux 走 `/proc`
  （`/proc/net/tcp{,6}` 的 LISTEN inode → 扫 `/proc/*/fd` 反查 pid，取不到再问 `ss -ltnp`）、
  其它 POSIX 走 `ps` / `lsof`。看门狗**按文件路径**加载它（保住"包不在 `sys.path` 上也能单独跑"）。
- `tools/watchdog.py --probe-info`：一条命令看清"本机事实现在走哪条路、探不到时怎么办"。

### 变更
- **`platform/sysprobe.py` 没有 psutil 时整层转向 `procprobe`**（原先在非 Windows 上
  "如实返回空"）⇒ 安装平台**不再以"装上 psutil"为前提**；`backend()` 新增 `procfs` / `ps` 取值。
- `tools/check_productization.py` 的扫描面加上「**未跟踪但未被 `.gitignore` 排除**的 `.py`」——
  此前新写出来的文件对扫描器是**隐形**的，它会照样打印"OK"，那句话在那一刻是假的。

### 守卫（都按真结果写，并做过突变检验）
- `agent_community/tests/test_procprobe.py`：真进程 / 真端口 / 真子进程（含"父→孙"整树） /
  **按生产调用方式真跑一次看门狗**（`python agent_community/tools/watchdog.py --check …`）；
  "纯标准库"这条承诺用 `sys.stdlib_module_names` **真判**，不靠肉眼看 import 行。
- `agent_community/tests/test_sysprobe.py::TestStdlibTierWithoutPsutil`：把 psutil 封成 `None` 之后，
  上面这些事实**仍须答对**（把"没 psutil 就瞎"钉死）。
- `test_watchdog_health.py` 里两条**源码文本**断言升级为行为判据（喂合成进程行）。
- 突变检验 `temp/procprobe_mutation_check.py`：**4/4 KILLED**（Windows 与真 Linux 各跑一遍）。
- 实测：Windows 全量 `agent_community/tests` 全绿；真 Linux（WSL Ubuntu-24.04）22 passed；
  换端口 + 换数据目录起真服务打真请求全通。

## [4.8.2] - 2026-10-15

**把「产品不是私人定制的」从口号变成可执行判据**（老板 2026-10-15：「完善开发框架，以后的开发
也要注意产品不是私人订制的」）。

### 新增
- **`PRODUCTIZATION.md`（唯一规则源）**：六种「私人定制」形态 → 各自的检查器与级别、
  每次任务的 Definition of Done、以及**欠账表**（明确记录欠什么、为什么先留着、怎么还）。
- **`tools/check_productization.py`**：**AST 级**扫描（只看真正的 `subprocess` 调用点，
  不扫注释/文档串 —— 第一版正则扫全文报了 52 条噪音，那种扫描器第一天就会被绕过）：
  - 规则 A｜**平台专属命令伪装成通用能力**（`powershell`/`netstat`/`tasklist`/`taskkill`/`Get-CimInstance` …）
    只允许出现在 `platform/sysprobe.py`、有平台分支的函数里，或显式申请例外
    （`# productization: allow(<理由>)`）；
  - 规则 B｜**开箱假数据**：字符串字面量里出现未渲染的 `{{占位}}`（用户会看到，像项目没做完）；
  - 附带**平台专属 skip 清单**（不判死，但每条都意味着「这块在别的平台没守」）。
- **`tools/first_run_check.py`**：**陌生人首次运行走查** —— clone → `cli start --no-gui`
  （**换端口 + 换数据目录**）→ `cli task` → `cli task-result` → `cli stop`，
  外加「仓库必须保持干净」「错误不许是空原因」。
- **两个工具都进了 CI**（`portability` 作业跑扫描；`test-linux` 作业跑走查）**并且进了 `pytest`**
  （陌生人拿到代码第一件事是跑 pytest，闸门也要在那里）。
- `AGENTS.md` 新增《产品化硬规则》一节（判据 + 五条命令 + 两条最容易犯的 + 派任务补充话术）。

### 文档
- 欠账表首次登记 4 条：看门狗进程发现仍 Windows 专属（它承诺"纯标准库自足"⇒ 不能引 psutil）、
  窗口激活与回收站（Windows 独有概念，已如实返回不支持）、1 条平台专属 `skipif`、
  容器路线未在真机验证。
- 开发模式（DSH agent preset `ac-dev`）的 L1 人设与 L2 手册同步更新：
  「产品不是私人定制」写入铁律并提供两个工具的调用点；工作方式改为
  **长推进、要最优解、高效率**，并明确「持续更新项目模型」。

## [4.8.1] - 2026-10-15

**来自 v4.8.0 真机测试回执的修复**（一位朋友在隔离沙箱里照 README 走完了全流程，
`start --no-gui` / `task` / `task-result` / `stop` 全通；下面是他撞到的三处）。

### 修复
- **结果正文只有一个 `[Error: ]`**（九个字符、**不含任何原因**）。
  真因两层：全新安装**没配任何 AI**，而 provider 的兜底是**占位配置**
  （`https://api.example.com` + `example-model-flash`），任务因此走 AI 路径并必然失败；
  失败时 `str(e)` 恰为空（连接类异常常见），而原实现直接拼成 `[Error: {e}]`。
  ⇒ ① 错误文本**一律说清原因**（空信息退到异常类型名；HTTP 无正文则明说"服务端没有给说明"）；
  ② 还停在占位配置时，附上「**你其实还没配 AI** + 怎么改（`AC_AI_BASE_URL`/`AC_AI_API_KEY`/`AC_AI_MODEL`，
  或把 `ai_mode` 设为 `off`）」；③ **启动日志明说「AI 未配置」**（原来只有一行 `AI Provider: openai`，
  读起来像配好了）；④ CLI 把 `[Error: …]` 当**失败**呈现，而不是当"结果"打印。
- **`start`（默认带桌面窗口）在无图形环境直接崩**（Linux 缺 GTK/Qt、Windows 缺 WebView2）——
  而 README《快速开始》第 2 步就是这条命令 ⇒ 现在**说清原因并自动退回后端守护模式**
  （功能一样），并提示下次可直接 `--no-gui`。
- **CLI 不认 `AC_PORT`**：`_get_base_url` / `_is_running` / `start --port` 都回落到写死的常量 18920
  ⇒ 用 `AC_PORT` 换过端口的用户，CLI 会连错端口（实测：提示 URL 打的是 18920，而服务在 18963）。
  ⇒ 统一走端口的唯一解析点（命令行 > `AC_PORT` > 默认）。
- 启动日志里的**假配置噪音**：桥监管表的默认值原是一条假 harness + **未渲染的模板占位**，
  每次启动都报「桥脚本不存在: `{{…}}`，跳过」⇒ 默认改为空表（真实 harness 走注册 API，不靠静态表）。
- 删掉一处**没有任何读者**、且名为擦洗后私有名的环境变量（`示例Harness-E_SUPERVISOR_PID`）。

### 变更
- AI 失败前缀元组收成一处真相（`ai_provider.AI_FAILURE_PREFIXES`），两处内联元组改引用它。
- 新增守卫：`tests/test_ai_error_speaks.py`（错误文本**永远不许空原因**）、
  `tests/test_cli_contract.py`（CLI↔端点契约 + GUI 起不来不许崩）。

## [4.8.0] - 2026-10-14

**「陌生人照着 README 就能跑起来」这一版**：接入流程产品化、可移植性收口、CI 首次全绿，
并修掉一批"第一次用就会撞到、却看不出原因"的坑。

### 新增
- **接入产品化（V40–V48）**：桥的两段式模型（平台通用模板 + 工作间具体桥）、低耗验证间、
  `design-docs/V43_接入自检清单.md`、**按接口引导**、接口索引与适配等级契约、口径唯一化。
- **可移植性三层制度**：`PORTABILITY.md`（唯一规则源）；`platform/env_spec.py`（环境变量登记表 +
  条件必需校验 + `assert_ready()`）、`platform_address.py`（端口/监听地址/基址的**唯一解析点**）；
  三条自查 `tools/check_portability.py`、`tools/gen_env_docs.py --check`、`pytest`。
- **CI（GitHub Actions）**：`portability` / `test-linux` / `test-windows` 三作业，**首次连续全绿**；
  依赖漏声明由 AST 守卫 `TestThirdPartyImportsAreDeclared` 拦死。
- **`platform/sysprobe.py`（本机事实的跨平台入口）**：进程表 / 命令行 / pid 存活 / 端口归属 / 结束进程。
  **psutil 主路径**（Windows/Linux/macOS），PowerShell·netstat·tasklist 只作无 psutil 时的回落
  ⇒ 「桥在不在跑」「端口被谁占着」「退出并关闭」这些能力**在 Linux 上真的能用**了
  （此前是静默降级：恒返回空、还不报错）。
- `Dockerfile` + `docker-compose.yml`（容器路线；注意事项见下）。

### 修复
- **`python -m agent_community.cli task "…"` 一直失败**（README 第 3 步）：CLI 发的字段名与
  `/api/command` 不匹配 ⇒ 现在对齐，且**失败时打印服务端原话**并返回非 0。
- **`task-result` 从不打印结果**（README 第 4 步叫"看结果"却看不到）⇒ 现在打印结果正文；
  没有结果时说清原因（进行中 / 没有成员参与 = 还没接入 Harness）并给下一步。
- **`task-result` 的"参与数"永远是 0**：它读的字段在 Task 模型里不存在 ⇒ 改为读真字段（举手/派活/回报）。
- **换了 `AC_DATA_DIR` 之后仓库仍被写脏**：CLI 的日志目录写死成仓库布局
  ⇒ 收口到 `env_spec.data_dir()`（唯一解析点）。
- **中文路径/内容在非 UTF-8 控制台（英文 Windows 的 cp1252）下把子进程弄死**：
  桥模板、工具脚本、动作脚本、示例统一加输出编码自保（这是"只有 CI 才红、本机复现不出来"的那一类）。
- 端口默认值口径统一：README / `cli.py` / `start.py` / 设计稿里残留的旧默认值 **9103**
  一律对齐唯一真源（**18920**）。

### 变更
- `env_spec.data_dir()` 成为数据目录的唯一解析点；数据落点守卫的扫描范围从 `platform/` 扩到**整个包**。
- **容器路线提醒**：用 `ports:` 端口映射时，宿主浏览器来的请求在容器里**不是回环地址**
  ⇒ 未设 `AC4_TOKEN` 时一律 403（有意的安全边界）。`docker-compose.yml` 头部写明两条可行路。

### 已知限制（如实标注）
- 窗口激活、桥目录回收站：**Windows 专属**，其它平台如实返回不支持（不假装成功）。
- `tools/watchdog.py` 的进程发现仍是 Windows 专属（它承诺"全程标准库、无第三方依赖"，故不引 psutil）。
- CI 的 Python 为 **3.11**；其它版本未逐一验证。
- Docker 路线**未在真机验证**；主路径是源码运行（README《快速开始》）。

## [4.7.0] - 2026-10-06

gRPC 层鉴权与错误码语义化（V18）+ 公开仓库本地路径泄漏清理。

### 新增
- **gRPC 错误码语义化**：六条 RPC 的失败不再塌成 `UNKNOWN` —— 上游 400/401/403/404/429/500/503 分别映射
  `INVALID_ARGUMENT` / `UNAUTHENTICATED` / `PERMISSION_DENIED` / `NOT_FOUND` / `RESOURCE_EXHAUSTED` /
  `INTERNAL` / `UNAVAILABLE`；连接不可达 `UNAVAILABLE`；客户端 deadline 到期 `DEADLINE_EXCEEDED`；
  坏的 `payload_json` `INVALID_ARGUMENT`（且不发往上游）。
- **`agent_token` 透传**：三条带该字段的 RPC 把它注入 `x-agent-token` 头交由平台校验，
  网关**不做二次校验**（单一校验来源不变）；字段为空则不注入，保留向后兼容。
- **details 卫生**：对外错误文本剔除本地绝对路径与 Traceback、截断，并附 `upstream=<状态码>` 便于对账。
- **deadline 贯通**：上游超时按"客户端是否设过 deadline"区分 `DEADLINE_EXCEEDED` 与 `UNAVAILABLE`。

### 修复
- **公开仓库本地路径泄漏**（自查发现）：清理 5 处本机绝对路径（注释/测试夹具/副本地图），
  真实路径改为仅保留于不随仓库发布的本地文档。
- **泄漏检查器两处盲区**：路径正则补齐私有根模式；扫描后缀由固定白名单改为**按文本性判断**
  （`.bak_*` 等不再漏网）；新增"发布副本工作区无视忽略规则的私有路径残留"检查（堵住 gitignore 藏匿通道）。

### 测试
- gRPC 冒烟由 10 条扩至 **30 条**（错误码表驱动 / token 转发 / deadline / details 卫生），
  变异验证 4/4 KILLED；回归全绿。

## [4.6.0] - 2026-10-06

协议层补齐（gRPC 落地）+ 看门狗常驻守护 + 版本号单一真源 + 一批真实缺陷修复。

### 新增
- **gRPC 网关接入可用**：`agent_community/grpc_gateway.py` + `proto/agent_community.proto` 生成桩
  （信封式 `payload_json`，与 HTTP 接口零协议漂移），把 gRPC 调用转发到平台 REST；
  协议目录里 gRPC 端点按可用性给出（`主机:9105`）。
- **看门狗用户态自启**：`watchdog_autostart.ps1`（`install/uninstall/start/stop/status/evidence`），
  只在当前用户「启动」文件夹放一个 `.cmd`，不碰系统启动项/服务/注册表。
- **AI 连通性自检**：`POST /api/config` 可顺带真发一次极小探针（`max_tokens=1`）验证"保存成功 ≠ 能用"，
  带指纹缓存与可读原因；`GET /api/config` 回开关摘要（不发请求）。**两个开关默认关**，避免默认计费。
- **远端模型清单**：`GET /api/ai/models` 拉服务端 `/v1/models`，失败回落内置清单并给可读原因。
- **会话可见性**：`GET /api/sessions` 列出活跃会话与各状态计数。

### 修复
- **`--spawn-cmd` 含空格路径被切断**：命令按空白切分导致子进程拿到半个路径并秒退；
  改为引号感知切分（保反斜杠、剥成对引号、不配对时回退不抛异常）。
- **版本号三处口径不一**：`/api/status` 曾硬编码版本号，与包元数据、变更日志各行其是；
  现统一读 `agent_community.__version__`（单一真源）。
- **协议目录状态不再"导入时快照"**：装好依赖但未重启时也能如实反映（gRPC 可用性运行时判定）。
- **基座守卫在新版 pip 上被静默跳过**：改用当前 pip 的等价加载入口，入口缺失时判失败而非跳过。
- **测试污染真实目录**：桥落盘根提为可隔离常量，测试不再往真实 `platform/bridges/` 写入。
- **发布流程死路**：工作树干净时的空提交不再导致发布中断；推送结果以远端直查为准，避免误报失败。

### 变更
- 对外文档与示例统一为中性占位名（`example-model-flash` / `api.example.com`），不出现真实服务名。

### 工程
- 新增自动化测试：gRPC 网关冒烟（同进程假 REST + 真 gRPC 客户端）、协议目录运行时状态契约、
  版本号单一真源契约。

## [4.5.0] - 2026-09-29

组长转向/委派细化、能力分级路由、卡死任务批量回收与端到端实测版本。

### 新增
- **组长转向/委派细化**：新增 `POST /api/workshop/{ws_id}/leader/assign`（任务委派细化）、`POST /api/workshop/{ws_id}/leader/redirect`（任务转向）、`GET /api/workshop/{ws_id}/leader/assignments`（委派记录查询），assignments 字段持久化落盘。
- **能力分级路由**：新增 `GET /api/capabilities/ledger`（能力账本查询）、`POST /api/capabilities/route`（按能力路由任务）、`POST /api/task/{task_id}/route`（单任务能力路由），综合评分 = 匹配分×0.6 + 信誉分×0.4 加权。
- **卡死任务批量回收**：新增 `GET /api/tasks/stale`（卡死任务识别）、`POST /api/tasks/recycle-stale`（批量回收），卡死判定为任务处于中间态（broadcasting / in_discussion / delegating / executing）且 `updated_at` 距今超过 `timeout_sec*3`。

### 测试
- 并行端到端验证通过：创建工作间与并行子任务（parallel 模式、parallel_limit=3），多 harness 成员并行执行、进度更新、结果聚合、讨论区落记录全链路真实可用。
- 插话机制端到端实测通过：提交插话 → 写入插话池 → 紧急插话注入讨论区 → 目标成员拉取/消费（execute）→ 落讨论区完整链路真实可用（无代码改动）。

## [4.4.0] - 2026-09-29

V-18 全功能回归：193 项 HTTP 测试通过，4 项缺陷修复。

### 修复
- **AI Provider 配置校验（缺陷 A）**：`POST /api/config` body 显式传空 `ai_provider` 时即使配置存在默认值也拒绝，返回 400 友好提示，不再以 500 掩盖校验意图；provider 属性一律 `getattr` 兜底，避免缺失属性抛 AttributeError。
- **桥生成参数校验（缺陷 B）**：harness 桥脚本渲染参数校验失败（模板缺失 / 必填字段缺失 / 占位符残留等 `BridgeTemplateError`）时返回 400 明确错误，不再落入 500。
- **插话池持久化（缺陷 C）**：插话条目存储独立收敛，提交 / 过期回收 / 状态流转回归正常。
- **工作间删除清理插话池（缺陷 D）**：删除 / 回收工作间时同步清理该工作间全部插话条目（`interject_store.remove_workshop`），不再遗留 `interjects.json` 脏数据。

### 测试
- 新增 `tests/` 测试套件：`conftest.py`、`test_plugins.py`、`test_routers_smoke.py`、`test_security.py`、`test_state.py`。
- 全量 193 项 HTTP 测试回归通过（服务 18920 重启复测）。

## [4.3.0] - 2026-09-28

V-9 单体渐进式拆分与插件安全拦截修复。

### 重构
- **server.py 渐进式拆分**：按域拆出 `platform/core/security.py`（危险命令模式/DPAPI 密钥加密/mask/原子写）、`platform/state.py`（共享状态 Store）与 `platform/routers/`（mirror、plugins、config、harness、workshops 五个路由组），主文件由 7500+ 行降至 5100+ 行，119 个路由全部保留并回归通过。
- **延迟导入避循环**：routers 子模块通过延迟 import server 依赖，避免 package 初始化循环引用。

### 修复
- **插件 invoke 危险命令拦截失效（安全）**：`POST /api/plugins/{name}/invoke` 此前仅校验注册时 `target`，请求体 `command` 未参与执行与校验，`format` / `net stop` / `rm -rf` 等危险命令可被放行；现改为对实际执行的命令做 `_is_dangerous` 拦截，拦截返回 400。
- **`--token` 双模块副本失效（安全）**：`python -m agent_community.platform.server` 启动时存在 `__main__` 与 `agent_community.platform.server` 双模块副本，`--token` 仅更新 `__main__` 副本导致 routers 延迟导入读到空 `ALLOWED_TOKENS`（invoke 永远"未配置 Token"）；已在 `__main__` 块同步双副本 Token。
- **config 保存逻辑回归**：`POST /api/config` 幂等回写通过。

### 安全加固（延续）
- 密钥经 DPAPI（CryptProtectData）加密后落盘，`dpapi:` 前缀 base64 存储，无 pywin32 依赖。
- AI Key 读取优先级统一为 环境变量 > 配置文件 > 默认值，env 值永不落盘覆盖。
- CDP 镜像默认关闭，未启用时返回 `cdp_enabled=False` 并披露状态。

## [4.2.2] - 2026-09-24

全功能测试缺陷修复补丁。

### 修复
- **无 body 请求 500**：`POST /api/workshops/recycle-stale`、`POST /api/workshop/{ws_id}/pin` 在无 body / 空 body 时抛 JSONDecodeError 返回 500，已容错为空对象处理，返回正常语义（recycle-stale 需参数则 400，pin 默认取反）。
- **confirm-members 类型校验**：`POST /api/workshop/{ws_id}/confirm-members` 的 members 非对象数组（如字符串数组）时遍历抛 AttributeError 500，已在遍历前校验并返回 400 友好提示。
- **select-members 空名单无提示**：AI 未选出合适成员时返回空 members 无任何说明，已附加 `reason` 字段提示人工补充名单。
- **heartbeat 提示语**：`harness_id` 缺失时错误信息补充 URL query 传参示例。

---

## [4.2.1] — 2026-09-24

卡死工作间回收与孤儿残留清理版本。

### 新增
- **卡死工作间识别**：新增 `GET /api/workshops/stale`，识别心跳超时 / 长时间无进展的卡死工作间（pinned 永不列入）。
- **批量回收**：新增 `POST /api/workshops/recycle-stale`，一键回收全部卡死工作间（跳过 pinned，不终止共享 harness 桥进程）。
- **前端回收按钮**：工作间卡片新增回收按钮（带确认弹窗）。

### 修复
- **DELETE 遗留孤儿**：修复旧版 `DELETE /api/workshop/{ws_id}` 仅删除字典、不清理状态机记录与工作区目录的缺陷——删除时同步清除状态机记录（`task_state_machine` 新增 `remove(key)`）并将工作区目录移入回收站，不再产生孤儿残留。
- **启动自愈**：服务启动时自动清理历史遗留的孤儿状态机记录（工作间已删除但状态机残留）。

## [4.2.0] — 2026-09-24

工作间任务树结构化与记忆擦除纠偏版本。

### 新增
- **任务树结构化**：工作间任务树由扁平字符串升级为嵌套节点（parent/children），节点支持 kind（phase / task / correction）与 status（active / done / dropped）；新增 `/api/workshop/{ws_id}/tree` 端点族（建节点 / 更新状态 / 资源挂载 / 查询嵌套树）。
- **资源库联任务树**：工作间资源（`/api/workshop/{ws_id}/resources`）支持 `task_node` 挂载点，登记资源可挂到任意任务节点，`RESOURCES.md` 清单同步标注「关联任务」，前端资源卡片显示挂载节点名。
- **记忆擦除纠偏（确认走弯路）**：`POST /api/workshop/{ws_id}/halt-and-reset` 一键闭环——停止任务并重置状态机、在任务树落「错误路径（dropped）／正确路径（active）」纠偏节点、反思总结自动归档 `DETOUR_SUMMARY.md` 并挂正路节点、擦除讨论上下文、按成员/组长擦除平台任务记忆与 harness 经验索引、通知成员记忆已重置、工作间重置为 draft 可重新开始。
- **前端工作间升级**：任务树改为真实树形渲染（缩进 + 纠偏标签），新增「确认走弯路·停止任务并擦除记忆重来」按钮与决策模式展示。
### 文档
- 新增 `design-docs/工作间模式设计V5.md`：决策模式（用户决定 / 组长独裁 / 举手投票）完整设计，平台端仅保留设计，待 harness 端开发。
- 新增 `docs/silent-mode-orchestration-design.md`：静默模式编排设计。
- 新增 `design-docs/工作间模式-parallel并行落地设计.md`：并行工作模式落地设计。

## [4.1.0] — 2026-09-20

API 优先策略与工作间增强版本。
### 新增
- **AI Provider 策略**：默认 API 优先（OpenAI 兼容），本地模型仅作可选降级；`config.json` 支持 `ai_provider` / `ai_mode` 显式声明。
- **任务状态机**：9 态事件驱动（含 executing / waiting_reply / stuck_paused / blocked_retrying / timeout），心跳超时自动 L0 重试、失败升级 L1 提醒，终态防二次变更。
- **插话机制**：用户 / 系统插话（一般 / 紧急 / 灵感，系统级 L1/L2），优先级队列 + 超期回收，支持 JSON 落盘持久化。
- **工作间 UI（回字形车间）**：任务树 + 讨论区 + 插话抽屉 + 全屏放大视图，支持徽标化状态指示。
### 变更
- `data/` 运行时数据（workshops / tasks / harnesses / interjects / state_machine）JSON 落盘，重启自动恢复。
### 修复
- 大厅「发布」改走 `/api/workshop` 并跳转工作间，移除乐观插入导致的消息重复。
- harness 注册持久化（`harnesses.json`），重启不再丢失。

## [4.0.0] — 2026-09-11

首个开源准备版本（Open Source Ready）。

### 新增

- **平台核心**：FastAPI Web 服务 + Click 命令行，支持命令行一键启动、无需浏览器。
- **多 Agent 协作**：Agent 注册与发现、任务广播、举手、讨论室协商、委托执行、结果汇总。
- **Harness 标准化接入**：统一的注册 / 唤醒 / 派发 / 回报协议，外部 Agent（CLI / IDE / 桌面程序 / HTTP 服务）可作为成员参与协作。
- **桥模板库**：`file_poll`（文件邮箱轮询）、`pending_poll`（通用队列轮询）、`cli_acp`（ACP 协议子进程）三类模板，支持按 harness 信息机械渲染生成可运行桥脚本。
- **工作间模式（Workshop）**：大厅（hall）内容分发、成员激活、角色协作与工作区隔离。
- **AI Provider**：支持 `openai` 兼容接口、`ollama` 本地模型、`http_callback` 回调三类后端。
- **经验沉淀 v2**：能力账本（capability ledger）加权选人、任务完成奖励回写、harness 信誉加成。
- **自愈与守护**：自主恢复循环（周期性状态自检与恢复）、进程守护脚本。
- **持久化**：任务 / 讨论室 / harness / 工作间以 JSON 落盘，重启自动恢复。

### 开源准备

- 全仓脱敏：本地绝对路径改为相对路径或占位符；真实 harness 名改为示例名（`harness-a` / `harness-b`）；凭据字段改为环境变量占位。
- 依赖声明：新增 `requirements.txt`，明确运行所需第三方包与版本区间。
- 数据示例化：`data/` 改为首次运行自动生成；新增 `config.example.json`、`harnesses.example.json`。
- 文档：新增 `LICENSE`（MIT 占位）、`README.md`（重写）、`CONTRIBUTING.md`、本 `CHANGELOG.md`、`.gitignore`。
- 测试入仓：桥模板渲染断言、核心冒烟、端到端流程测试收敛至 `tests/`，提供一键入口且不依赖运行中服务。
- 配置：数据目录支持 `AC_DATA_DIR` 环境变量覆盖；端口默认 `18920`，可用 `--port` / `AC_PORT` 覆盖。

### 已知待办

- CI（GitHub Actions）与跨平台（Linux / macOS）验证尚未接入。
- 公开发布所需的 API 文档、示例 demo 数据尚未补充。
