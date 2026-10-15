# 更新日志（CHANGELOG）

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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
