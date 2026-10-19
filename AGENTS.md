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
python tools/core_check.py              # ★核心体检一条命令：六协议矩阵+闭环、建立会话、多轮派活、任务全链路、acp 真拉起、看门狗救活、一键更新、掉线恢复链、多成员并发派活、证据链 V55、辩论模式 V52、隔间继承 V53、通道生命周期 V28、受控重启 V58、只读日志尾 V59、任务树+资源库、巡逻员人工点派、桥产物落点（18 项，约 8 分钟）
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

`agent_community/platform/server.py` 是 **269 KB / 5498 行**（2026-10-03 实测），**仍挂着 46 个 `@app.*` 端点**（44 HTTP + 2 WS：`/api/assistant/chat`、`/api/command`、`/ws`、`/api/room/*`、`/api/wakeup/*` 等）。
> ⚠️ 本文件旧版曾写「已不含任何路由（只剩 `include_router` 与编排）」—— **实测为误**，`server.py` 自己的路由一直都在。
> **「新端点加到 `routers/` 对应端点组」这条规矩不变**，只是别再当作"server 已经没有路由"来理解。
> HTTP 业务端点在 `platform/routers/`：

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
