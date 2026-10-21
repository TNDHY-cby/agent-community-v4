# 外端Agent生产合作社（External Agent Community）— 工作指引

> 无论你用哪个 AI / 哪个模式进来，先读这一页。更详细的项目手册在项目技能
> `ac-project-dev`（若你的环境支持技能，直接加载它；不支持就读
> `docs/` 与 `design-docs/平台设计总纲.md`）。
>
> 🔖 **接力/新会话先读 [`HANDOVER.md`](./HANDOVER.md)** ——
> 当前进度（V-15 九步全部完成且**步骤8/9 已补测**）、未提交改动清单、
> 待拍板事项、关键文件地图、高频坑与汇报约定，全在那里。

> ⚠️ **2026-10-05 实测警告**：本机 fastapi 0.141.1 / starlette 1.7.0，
> `app.include_router()` 往 `app.routes` 放的是 **`_IncludedRouter`（`.path is None`）**，
> 真实路由在 **`.original_router.routes`**。**只遍历 `app.routes` 取 `.path` 会漏掉
> 全部 include_router 端点**（`/api/sessions` `/api/policy` … 都看不到），
> 但服务其实是好的。写"端点已挂载"断言请用
> `agent_community/tests/test_sessions_v15.py::_mounted_paths()` 的展开写法。

## 可移植性硬规则（2026-10-14 新增，违反即不合格）

> **判据**：**当前电脑能跑不算合格；陌生环境只改配置就能跑才算合格。**
> 完整规则、DoD 清单与自查命令见 **[`PORTABILITY.md`](./PORTABILITY.md)**（唯一规则源；
> `CLAUDE.md` / `.cursorrules` 都是指向它的入口，不重复内容）。

**禁止硬编码**：端口、IP、域名、`localhost:数字`、绝对路径、用户名、本地目录、
密钥、数据库 URL、API 地址、回调地址。

**必须**：
1. 环境差异**从环境变量读**，登记在 `agent_community/platform/env_spec.py` 并校验；
   端口与平台地址**只经** `agent_community/platform_address.py` 解析（别处不许自己拼）；
2. 数据落点认 `AC_DATA_DIR`（不许把运行数据写进仓库）；
3. 默认值只用于开发且**可被环境变量覆盖**；`AC_ENV=production` 下缺必需项**启动失败并提示**；
4. 本地适配只改 `.env`（已在 `.gitignore`），**不得改源码**；
5. 改完跑三条自查：`python tools/check_portability.py`（必须 fail=0）→
   `python tools/gen_env_docs.py --check` → `python -m pytest -q`；**改了跨平台相关的东西再加**
   `python tools/dual_platform_check.py`（双平台真跑；2026-10-17 起这是 CI 的替代判据），
   并输出**环境变量清单 + 默认值 + 是否必需 + 自查结果**。

**派任务时的标准话术**（复制给任何 agent）：

> 实现 X。按 `PORTABILITY.md` 的可移植性规则。先列出涉及的环境变量和默认值，再写代码。
> **禁止针对我的电脑。** 验收：在陌生 Linux 容器里只改环境变量即可运行。

**被纠正时的正确反应**（不要只改那一处）：

> 你写死了端口，这不可接受。请重构为配置驱动，并同步更新 `AGENTS.md`、`.env.example`、
> README 和测试。以后任何环境相关值都不得硬编码。完成后给我 grep 自查结果和环境变量表。

**例外**：确有理由的硬编码，必须在该行写 `# portability: allow(<一句话理由>)`（显式申请，扫描器才放行）。

## 产品化硬规则（2026-10-15 新增，违反即不合格）

> **判据**：**换一个目录 + 换一个端口 + 换一个数据目录，不改一行代码就能走通**；
> 做不到就**如实说没做到**。
> 完整规则、六种「私人定制」形态、每个形态对应的检查器与**欠账表**见
> **[`PRODUCTIZATION.md`](./PRODUCTIZATION.md)**（唯一规则源）。

```bash
python tools/check_portability.py       # 硬编码坐标（端口/路径/凭据）
python tools/check_productization.py    # 平台专属命令冒充通用能力 / 开箱假数据
python tools/first_run_check.py --src <发布副本>   # 陌生人首次运行五步走查
python tools/dual_platform_check.py     # ★双平台真跑（Windows + WSL Linux）——2026-10-17 起的 CI 替代判据
python tools/pre_release_check.py       # ★发布前一条命令跑全五类判据（--fast 跳过双平台）
python tools/core_check.py              # ★核心体检一条命令（**24 项**，约 20 分钟）：六协议矩阵+闭环、建立会话、多轮派活、任务全链路、acp 真拉起、看门狗救活、一键更新、掉线恢复链、多成员并发派活、证据链 V55、辩论模式 V52、隔间继承 V53、通道生命周期 V28、受控重启 V58、只读日志尾 V59、任务树+资源库、巡逻员人工点派、视觉拉起、公司间合作 P1、**外部访问（只读档/令牌/cookie）**、**中转 M1/M2/M3**、桥产物落点
python tools/cross_company_check.py     # 公司间合作 P1：两个独立实例真投 / 真收 / 回查 / 鉴权 / 数据隔离 / 产物清单（**20 格**，不需要 frp）
python tools/external_access_check.py   # 外部访问 V65：只读档 / 三种令牌携带 / 401与403区分 / ?token= 种 cookie（九格）
python tools/relay_check.py             # 中转 V66 M1：一 Hub + 两终端互达 / 终端零入站 / 鉴权 / 断线重连 / 队列有界（九格）
python tools/relay_crossnet_check.py    # 中转跨网络：Non-loopback 地址 + WSL 另一网络栈；TLS 缺件**如实 SKIP**（不假绿）
python tools/relay_dispatch_check.py    # 中转 V67 M2：派活真建间 / ack / 回执自动更新 / 失败可见 / 零对外监听（八格）
python tools/relay_isolation_check.py   # 中转 V68 M3：私有形态不退化 / 共享形态真隔离 / 跨公司两道许可（六格）
python tools/gen_frpc.py --check        # 公司间合作 P2（**可选**）：生成 frpc 配置（缺项清晰报错；token 不打印明文）
python tools/visual_launch_check.py     # 视觉拉起：窗口列表不乱码 / 只读选项 / 真置顶 / 方法兜底 / 不许假成功（非 Windows 如实跳过）
python tools/patrol_chain_check.py      # 巡逻员人工点派：拒得合理 / 三类动作 / 三态 / 平台复核 / 留痕
python tools/tree_resources_check.py    # 任务树+资源库：真嵌套 / 双向关联 / 删登记不删文件 / 落盘留痕
python tools/logs_tail_check.py         # 只读日志尾 V59：白名单 / 上限 200 / 穿越被拒 / 去敏 / 只读 / 追尾
python tools/offline_recover_check.py   # 掉线恢复链单跑：不打扰空闲 / 掉线可见 / 派巡逻员 / 假修好拦住 / 真修好确认
python tools/task_parallel_check.py     # 多成员并发：各自落盘 / 各自回报 / 不串件 / 冒名被拦
python tools/evidence_chain_check.py    # 证据链 V55：校验 / 压分 / 归并 / 冲突互链 / 限量分发 / 不泄漏摘录
python tools/debate_chain_check.py      # 辩论模式 V52：默认关 / 只追加裁决 / 第二轮不重复烧钱
python tools/inherit_chain_check.py     # 隔间继承 V53：只搬产物 / 邮箱桥不搬 / 逐项核对 / 不接管
python tools/channel_chain_check.py     # 通道生命周期 V28：停用后不投递 / 换对话序号与留痕
python tools/watchdog.py --check        # 服务活没活（exit 0=活 / 1=挂）；--daemon 常驻守护（零第三方依赖）
python tools/autostart.py enable        # 让平台随登录自启（Windows 启动文件夹 / Linux systemd user unit）
python tools/check_watchdog.py          # 判据：看门狗真能救活服务（能起 + 崩了能再起 + 有日志）
python tools/launcher_check.py          # 判据（V72）：双击到大厅——先出过场页 / 并行拉起 / 就绪进大厅 / 端口被占或缺解释器都要说人话
python tools/gen_entry.py               # 由模板生成双击入口（启动合作社.vbs / .cmd，GBK+CRLF）；--check 防漂移
python tools/make_shortcut.py --check    # 桌面快捷方式必须**指向本仓库**（指到别的副本 = 起错数据）
python -m pytest -q                     # 全部回归（含双平台守卫）
python tools/gen_env_docs.py --check    # 环境变量文档不许漂移
```

> ★**2026-10-17 老板拍板：GitHub CI 停更、只用 Gitee**（Gitee 侧没有免费 CI）⇒ **双平台判据＝
> `python tools/dual_platform_check.py`**：Windows 侧 `pytest` ＋ WSL Linux 侧 `tools/run_suite_in_repo.sh`。
> **退出码 2 ＝只跑成了一侧 ＝不算通过**（如实报"部分"）；本机适配只改 `.env`
> （`AC_LINUX_WSL_DISTRO` / `AC_LINUX_PYTHON`），**不改源码**。详见 `PORTABILITY.md` §4.1。

**两条最容易犯的**：
1. **平台专属实现不许冒充通用能力**：要拿本机事实（进程/命令行/pid/端口/结束进程）一律走
   `platform/sysprobe.py`（psutil 优先）；**用它的地方装不上 psutil 时**（看门狗那一族承诺
   "全程标准库、救一个坏掉的环境"）走同一件事的**纯标准库层 `platform/procprobe.py`** ——
   两层是**一处真相**（`sysprobe` 没有 psutil 时整层转 `procprobe`），别在别处再写第三份。
   只有 Windows 才有的概念（窗口激活、系统回收站）必须**平台分支 +
   如实返回不支持**，不假装成功。
2. **开箱默认不许是「看起来配好、其实必然失败」的假数据**（占位模型/示例域名/未渲染的 `{{占位}}`）。
   要么真能用，要么**明说没配**（启动日志与结果正文都说清"为什么"和"怎么改"）。

**派任务的补充话术**（接在原话术后面）：

> 做完跑 `check_portability` → `check_productization` → `pytest`；涉及用户入口的改动再跑
> `first_run_check`。**验收＝换目录 + 换端口 + 换数据目录、不改一行代码能走通。**

## 当前架构方向（2026-10-20 老板拍板，**压过一切细节**）

> 老板原话：「**隧道镜像没啥用**，最好还是像大厂做 APP 一样**每个终端都有平台，需要远程内容通过服务器**」，
> 紧接着又补两条：「**不能只是局域网**」「**没有怎么办**」（他手上没有公网服务器）。

```
终端 A（完整平台，本机直接用）──只出站──▶ 中转 Hub ◀──只出站── 终端 B（完整平台）
```

| 角色 | 干什么 | **不干什么** |
|---|---|---|
| **终端** | 跑完整平台（工作间/员工/任务/隔间/审计全在本地）；主动连 Hub；收件箱落本地 | **不监听公网端口、不要公网 IP、不要隧道** |
| **Hub** | 终端登记与在线状态、按 ID 转发、**有限暂存**（条数上限 + 过期） | **不存业务数据**、不主动连终端、不代理管理面 |

- 代码：`tools/relay_hub.py`（标准库零依赖，能跑在任何人的机器上）、`platform/relay_client.py`（只出站，长轮询）、
  `platform/relay_handler.py`（收到就办：`task.assign` 建工作间、`task.result` 更新外部任务）、`routers/relay.py`；
- 设计稿：`V66`（架构）/ `V67`（派活走中转）/ `V68`（公司隔离）/ `RUNBOOK_没有公网服务器怎么当中转.md`；
- **两条路别混**：`/api/relay/*` = 经中转（主路径）；`/api/peers` = 点对点 a2a（双方都有可达地址时才用）；
  外部任务表里有 **`via`** 字段（`hub`/`a2a`），界面上要显示"这条活儿怎么走的"；
- **隧道/frp 降级为可选**：`tools/gen_frpc.py` 保留但不主推；`RUNBOOK_公司间合作_隧道怎么用.md` 头部已标方向提示；
- **没有公网服务器的三条路**（私有组网推荐 / 暴露成 https（必须 TLS）/ 点对点），平台只认 `AC_HUB_URL` 一个地址，
  **不绑定任何服务商**。

> ⚠️ **反私人定制（老板 2026-10-20 第二次强调）**：不许为"某一台机器/某一个域名/某一家穿透服务"写特例。
> 验收判据＝**换机器 + 换端口 + 换域名 + 换穿透方式，不改一行代码**走通；
> 代码与设计稿里**连举例都不写**具体域名/服务商/机器路径。

## 铁律（违反即为错）

1. **设计先行**。功能先出设计稿进 `design-docs/`，等老板拍板再写码；已定稿的设计「必须遵守、不得自行增删」。
2. **改任何文件前先留备份**：`*.bak` / `*.bak_<日期>` / `*.old_` / `*.before_*` / `temp\*.bak_<时间戳>`。**用这些后缀，`publish_acv4.ps1` 与 oss 的 `.gitignore` 才会自动排除它们。**
   （本副本自 2026-10-02 起**有了本地 git**，但它是本地快照仓库、**没有 remote**，不能当异地备份用——备份纪律照旧。）
3. **改完必须重启服务再实测**，否则跑的是旧代码。
4. **判定编码/中文问题用 Python `decode('utf-8')`，不要信 PowerShell 的显示结果**。PowerShell 把正常 UTF-8 显示成乱码是常态。
5. **不向老板问技术细节**（harness_type / model / capabilities / 端口自己探），只问只有他知道的事。
6. **对外文档与示例一律用虚拟名**（`示例Harness-A` / `sk-your-key` / `api.example.com`），禁止出现真实 harness 名。

## 副本地图（2026-10-02 实测核实）

| 副本 | 路径 | git | 说明 |
|---|---|---|---|
| **开发副本（这里）** | `<开发副本根目录>`（真实路径只写在 dev-only 的 HANDOVER.md 与项目技能手册里，**不进公开仓库**） | 有（本地，**无 remote**） | 实际开发在这里 |
| **发布副本（开源）** | `<发布副本根目录>`（同上，真实路径 dev-only） | **只有 `gitee` 一个有效远端**（`https://gitee.com/TNDHY-cby/external-agents-community.git`，下载与协作看它）。★**GitHub 已于 2026-10-16 停止更新**（仅历史归档，别再从那儿拉/推）。**以 `git ls-remote` 实测为准**，别信本文档里的旧 sha | 只由 `publish_acv4.ps1` 单向推入（默认推 Gitee）；`-MirrorGithub` 保留只为**应急**，正式流程不用（老板 2026-10-16：「GitHub 不用更新了」） |
| ⚠️ 已退休副本 | `<退休副本根目录>`（同上，真实路径 dev-only） | 无 | 2026-10-02 退休，架构停在 V-9 拆包之前。**P0/P1/P2 成果已由 V-10 回收入主线**，此副本仅存档 |
| 另一条线 | `<另一条线根目录>`（同上，真实路径 dev-only） | — | 独立项目线（**代码零回收价值**，dev 是超集；仅 2 份全盘唯一文档可捞），勿混 |

- ⚠️ `publish_acv4.ps1` 同步面（2026-10-16 起）：`agent_community/` + 根文档（`README.md`/`AGENTS.md`/`.gitignore`/`pytest.ini`/`requirements.txt`/`start.py`/`PORTABILITY.md`/`PRODUCTIZATION.md`/… ）+ `design-docs/` + **`外包交接_前端界面/`（对外交接件，`/MIR`）** + 根 `tests/`（copy-only）+ `tools/` + `.github/`；`HANDOVER.md`、协作书、`dsh-tooling/` 等 dev-only 件**不同步**。
- ★**前端外包交接区**（2026-10-16 老板：「前端外包，外包者是看不到本地文件的，得上传到仓库」）：
  `外包交接_前端界面/` —— 入口 `README.md`、`硬约束.md`、`验收清单.md`、`交付说明模板.md`、
  可执行预检 `check_frontend_spec.py`（对着 `design-docs/FE-26…md` 逐条给 PASS/FAIL）。
  **改这个区域就是改对外契约**，守卫 `agent_community/tests/test_frontend_handover.py` 盯三件事：
  指向不许漂移、预检仪器自检必须过、**发布脚本必须同步它**（少一次同步＝外包方看不到）。
- ⚠️ 发布脚本已带**反向分叉闸**（oss 独有代码文件 → 中止），防止 robocopy 静默摧毁 oss 上的独有成果。
- ⚠️ 开发副本**曾有缺 `requirements.txt`** 的问题，已于 `7c99828` 补上；
  **2026-10-05 又修了一次**：原文件末尾被粘了 AIGC 尾注（无 `#`）且含中文，
  本机 pip（cp936/GBK 首选编码）连**解码**都过不去 → `pip install -r` 完全不可用。
  现已改为**纯 ASCII**，并由 `agent_community/tests/test_foundation_base.py` 守卫。
  **以后往 requirements.txt 里加中文，pytest 会直接变红。**

## 最快上手

```powershell
cd "<开发副本根目录>"   # 真实路径见 HANDOVER.md / 项目技能手册（不进公开仓库）
python -m agent_community.cli start                   # 起服务（带桌面窗口；--no-gui 只要后端）
python -m agent_community.platform.server             # 纯后端（端口取 AC_PORT，默认 18920）
python -m pytest tests/security_regression.py -v       # 安全回归（需服务在跑）
.\run_all_tests.bat                                    # 一键回归

# 端口/数据目录**因人而异**：改环境变量，不改代码
#   $env:AC_PORT=8080 ; $env:AC_DATA_DIR=D:\acdata
# 可移植性自查（CI 与发布前同款）
python tools/check_portability.py    # 必须 fail=0
python tools/gen_env_docs.py --check # 环境变量文档不许漂移
```

必须**在项目根目录**（即 `agent_community` 包的父目录）跑 `python -m agent_community.*`，否则包解析失败。

`agent_community/platform/server.py` 是 **330 KB / 6597 行 / 51 个 `@app.*` 端点**（**2026-10-20 实测**），仍是巨无霸。
> 规模数字**以实测为准**，别照抄历史文档（旧版写"269 KB / 5498 行 / 46 端点"，早已失真）。
> ⚠️ 本文件旧版曾写「已不含任何路由（只剩 `include_router` 与编排）」—— **实测为误**，`server.py` 自己的路由一直都在。
> **「新端点加到 `routers/` 对应端点组」这条规矩不变**，只是别再当作"server 已经没有路由"来理解。
> HTTP 业务端点在 `platform/routers/`（**33 个文件**）：

| 端点组 | 结构（V-13 拆分后，每个 ≤500 行） |
|---|---|
| `workshops*` | `workshops.py`(25 行聚合器) + `_lifecycle` / `_discuss` / `_assign` / `_task` / `_common` |
| `harness*` | `harness.py`(25 行聚合器) + `_register` / `_messaging` / `_bridge` / `_common` |
| 其余 | `config.py` / `mirror.py` / `plugins.py` / `audit.py` / `protocols.py` / `protocol_brief.py` |

**新端点加到对应端点组**；聚合器只做 `router.routes.extend`（别用 `include_router`，会让 `router.routes` 自省不到真实路径）。协议适配器（`mcp_server.py` / `a2a_server.py` / `grpc_gateway.py`）放**顶层包**——`platform/__init__.py` 会拽入 FastAPI 并污染 stdout。

### 关键设计稿

`design-docs/取长补短成果回收设计.md`（V-10，已实施）、`design-docs/多协议接入与前端落地设计.md`（V-11）、`design-docs/V13_Router拆分与状态注入设计.md`（V-13，已实施）。

> 本文件由 AI 维护。发现哪条与事实不符，**当次任务就改**。

---

---

<!-- 2026-10-13 移除：下面原本是一整段「AOCI 仓库认知」使用指令（6,673 字符 ≈ 2.0k token）。
     移除原因：① 它**每轮都被注入系统提示**，是恒定开销；② 它指向的 rea/AOCI MCP 工具
     已在同一轮被关闭（profiles/desktop/cordis.patch.yml 里 - id: mcp-rea + disabled: true），
     留着就是**指向不存在工具的死指令**（既费钱又误导）。
     恢复办法：把那个 disabled 改回 false 之后，再从 AGENTS.md.bak-aoci-20261013 取回本段。 -->
