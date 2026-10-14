# V49 · 可移植性的 CI 实证，与"跨平台进程探测"的交接

> **日期**：2026-10-14 ｜ **状态**：教训部分**已落地**；psutil 部分**待实施**（本轮做了一半，主动回退，理由见 §5）
> **前置**：`PORTABILITY.md`（三层制度）、`V48`（端口/数据落点一处真相）

## 1. 这份稿的由来

可移植性制度（三层）落地时，我在**开发机**上跑通了一切，写了守卫、做了突变检验，然后说"好了"。
老板随后打开 GitHub 看 CI —— **从 run #1 到 #10，没有一次全绿**。

真正把它修绿的唯一有效动作，是**拿到 GitHub 上的真日志**。这篇稿记录的就是"真日志告诉了我什么"，
以及为什么前两轮"读代码 + 推理"的修法必然失败。

## 2. 证据链：一次 CI 真跑，9 个失败，3 类真因

| 类别 | 失败 | 真因 | 本质 |
|---|---|---|---|
| **打印炸了**（Win ×2） | `test_portability_gate_passes`、`test_env_docs_are_not_drifted` | GitHub windows runner 控制台是 **cp1252**，`tools/*.py` print 中文 → `UnicodeEncodeError` → 脚本非 0 退出 ⇒ 守卫跟着红，**看起来像"扫出硬编码/文档漂移"** | 判据被伪装；开发机 cp936 编得出中文 ⇒ 永远绿 |
| **真实数据污染**（两平台） | `guard_real_data_dir` 报 `memory/task_memory.json: None -> (476,…)` | 我 2026-10-14 早先加的 `test_data_roots_respect_ac_data_dir` 用 `importlib.reload(memory)` 验环境变量 —— **reload 会重建模块级单例**，`workshops_discuss` 绑住的旧对象从此没人隔离 ⇒ 它的 complete 流程写进**真实** `data/` | **我自己造的**；且只在**全新 clone** 上暴露（dev 里那文件本来就有、尺寸没变就放过） |
| **平台专属代码伪装成通用代码**（Linux ×4、Win ×1） | `test_dpapi_roundtrip`、`test_bridge_watch_probe` 整文件、`bridge_instances` 事件循环那例、`test_allow_executes`、`test_operator_can_launch` | ① DPAPI 是 Windows 专属；② 进程探测只有 PowerShell（`Get-CimInstance Win32_Process`）；③ `ShellExecTool` 无条件 `powershell.exe`；④ **`subprocess.CREATE_NEW_PROCESS_GROUP` 被无条件使用**（Linux 上根本没这个属性 ⇒ 桥启动直接 500） | 同一件事散在 5 处、3 种写法 |

### 2.1 已落地的修法

- `tools/check_portability.py`、`tools/gen_env_docs.py`：顶部 `reconfigure(encoding="utf-8", errors="replace")`；
- `test_data_roots_respect_ac_data_dir` **改成开子进程验**（不碰本进程模块状态）；
- `agent_community/platform/spawn.py::spawn_kwargs()`：**子进程平台差异的唯一真相**
  （Windows→`creationflags`；POSIX→`start_new_session`；一律 `getattr` 兜底）；
- `ShellExecTool` 补 POSIX 分支（非 Windows 走 `/bin/sh -c`）；
- 受 PowerShell 专属影响的用例：**显式 `skipif` + 写明理由**（不是静默跳过），
  真因未明的那条（`test_operator_can_launch`）**不加 skip**，只把端点真实返回值写进断言。

## 3. 三处"只有我这台能跑"的典型（都值得记住）

1. **cp1252 打印**：报错是 `UnicodeEncodeError`，但**现象**是"可移植性扫描没通过" —— 判据伪装成结论。
2. **`CREATE_NEW_PROCESS_GROUP`**：报错文本是
   `module 'subprocess' has no attribute 'CREATE_NEW_PROCESS_GROUP'`，
   而**现象**是"一键注册失败" —— 用户看到的是业务错误，不是平台错误。
3. **`monkeypatch.setattr(os, "name", "nt")`**（我自己写的守卫）：
   CPython 3.11 的 `pathlib.Path.__new__` 是**实例化时读 `os.name`** 的 ⇒ 那个窗口里造出的 `Path`
   是 `WindowsPath`；只要有一个对象逃出去（实测：pytest 自己的失败报告里就有一个），
   整场变成 `INTERNALERROR: cannot instantiate 'WindowsPath'` ⇒ **所有结果都不可信**。
   ⇒ 教训：**改全局状态（`os.name`/`sys.path`/模块级单例）的代价不是"这条用例对不对"，而是"整份结果还可不可信"。**
   已加守卫 `test_no_test_monkeypatches_global_os_name`。

## 4. 方法论（这一轮最值钱的部分）

1. **日志优先，别猜。** 前两轮我靠读代码改，改完还是红；拿到真日志后 9 个失败**一次全部定位**。
2. **拿不到信息时，先穷尽"怎么拿到"。** 我两次尝试后就下结论"网络被中间设备拦"，
   真因其实是本机 `hosts` 里一条手写记录（详见 §6）。**结论要可复现，不能是"试了下不行"。**
3. **给失败加现场信息，而不是加 skip。** 没找到真因时，把"被测函数真实返回值/trigger 原文/目录实况"
   写进断言信息 —— `test_operator_can_launch` 就是靠这一手在下一轮直接吐出那个 500。
   **跳过不确知原因的用例＝假装绿。**
4. **"我改的"还是"本来就红"要分清**：拉历史 run 的日志比对（本轮证明 rule19 那条在改动前就红）。
5. **诊断代码也是代码**：我上一版诊断用了没导入的 `Path`，"现场信息"只输出
   `name 'Path' is not defined` —— **白费一轮 CI**。诊断必须本地先跑一遍。

## 5. 待实施：把进程探测换成 psutil（本轮做了一半，主动回退）

**目标**：让"扫进程找桥"跨平台 —— 现在只有 PowerShell 一条路，Linux 上恒返回空。
后果有两层：真实的（**桥在跑却认不出来** ⇒ auto-connect 会再起一只）和更阴的
（测试里"认不出来"类断言在 Linux 上**恒真 = 假绿**）。

**外部查证**：拿别的进程的命令行**没有纯 stdlib 的跨平台办法**，要么第三方库要么各平台各写一套。
选 **psutil**（Win/Linux/macOS 都有 wheel）。

**实施要点**（已想清楚，写下来免得下次重走）：
- 目标函数 `agent_community/platform/routers/harness_messaging.py::_probe_pids_watching`；
- 新增 `_iter_processes()` 产出 `(pid, ppid, name, cmdline)`：psutil 主路径，
  **PowerShell 只作无 psutil 时的回落**（防老环境退化）；
- `requirements.txt` 补 `psutil>=5.9`（声明守卫会自动要求它）；
- 判据三条**原样保留**：cmdline 含 `bridge.py`、进程名 `python*`、cmdline 含线索
  （线索**原样用**，别丢给"把相对名解析成当前目录文件"的那类 API）；
- 改完**撤掉** `test_bridge_watch_probe.py` / `test_onboarding_truthfulness.py` 里
  因"探测只有 PowerShell"而加的 `skipif` —— 那才是这一改动的真正收益（Linux 恢复真覆盖）。

**我卡在哪一步（诚实记录）**：替换后有 4 条用例变红
（`test_real_bridge_watching_the_inbox_is_detected` 等）。
两个已修：① 该文件用的是 `import os as _os`，我写了 `os.` ⇒ NameError；
② 有条守卫扫源码禁止某 API 名，而我在 docstring 里提到它 ⇒ 守卫被散文骗。
**剩下的没查完**：探测里有一条自我排除 `ppid == me`（原 PowerShell 版也有），
与测试替身 `_probe_as_if_external` 之间的耦合 —— 需要先读那个替身再动，
否则会把"真桥认不出"和"自己起的子进程被排除"两件事混在一起。

**当时的选择**：上下文预算耗尽 ⇒ **回退，不留半坏的工作树**。这比"看起来做了"重要。

## 6. 环境事实（本机，值得记）

- **GitHub API 读不到的根因是 `hosts`**：`C:\Windows\System32\drivers\etc\hosts` 里有
  `# GitHub hosts (marvis add)` 段（**重复 3 次**），把 `api.github.com` 也指到了 `140.82.114.4`
  （那是 github.com **网页**的 IP）⇒ 请求落到网页前端，回 HTTP 410「this API would go away」。
  **不改系统 hosts 的读法**：`curl --resolve api.github.com:443:140.82.114.5 <url>`；
  工具：`temp/fetch_ci_logs.py`、`temp/ci_run_report.py`、`temp/job_concl.py`。
- **Git Credential Manager 会弹"选择账号"对话框把脚本卡死**（老板明确不删凭据）⇒
  自动化 push 一律 `git -c credential.helper= push <带令牌的URL>` 或设 `GCM_INTERACTIVE=never`。
- 令牌**缺 `workflow` 权限**时，提交只要碰了 `.github/workflows/`，GitHub **拒收整次 push**。

---
---

## 7. 续篇（同日晚场）：run #11 的三条真因，与 **run #12 的第一次全绿**

> 本节是上一节（run #7 那一轮）的续。**结论：`7c321b4` 上 run #12 三个 job 全绿**
> （`portability` / `test-linux (3.11)` / `test-windows (3.11)`）—— 这是 run #1 以来第一次。

### 7.1 run #11 的三条失败（逐条：现象 → 真因 → 修法）

| job | 失败 | 真因 | 修法 |
|---|---|---|---|
| windows | `test_rule19_fresh_name_and_dir_end_to_end` → `no_reply`（**从 run #5 红到 #11**） | 用例造的**假 agent** 第一件事就 `print('AGENT-SEES %r' …)`，而那行里含**中文成员目录**（验证间叫 `验证员_v0`）。它的 stdout 继承的是**桥的日志文件句柄** ⇒ 子进程按**系统 ANSI 代码页**定编码 —— runner 上是 **cp1252**，一个汉字都编不出来 ⇒ `UnicodeEncodeError` ⇒ **在写回报之前就崩**；而开发机是 cp936（编得出）⇒ 本机怎么跑都绿 | 假 agent 开头 `sys.stdout/stderr.reconfigure(encoding="utf-8", errors="replace")`（**与平台自己的桥同一纪律**） |
| linux | `test_policy_wiring.py::TestShellToolGating::test_allow_executes` → `stderr: /bin/sh: 0: Illegal option -N` | `ShellExecTool` 把 PowerShell 的 `-NoProfile/-NonInteractive/-Command` **硬套给 `/bin/sh`**（两平台同一份 argv）⇒ `sh` 把 `-N` 当自己的选项。**"命令执行"这条能力在 Linux 上等于没有** | 平台差异收进 `shell_tool._shell_argv()` 一处（Windows: PowerShell `-Command`；POSIX: `/bin/sh -c`）；**同时**把该用例里写死的 `New-Item …` 换成按平台给命令（它的兄弟用例上一轮已改，**这一条被漏了** ⇒ 规则 20） |
| linux | `test_v44_batch1_fixes.py::test_spawn_kwargs_is_platform_aware` → `Windows 应给 creationflags：{}` | **是守卫自己错了**：它无条件断言"`_IS_WINDOWS=True` ⇒ 必有非零 creationflags"，而 `CREATE_NEW_PROCESS_GROUP` 在 Linux 上**根本不存在**、实现用 `getattr(…, 0)` 兜底（故意的）⇒ **在 Linux 上永远不可能成立**。守"平台差异"的用例自己成了"只在我这台成立" | 改成：①真平台分支按本机实际断言；②跨分支只断言"**不许出现那个平台不该用的参数**"（两平台都成立、且仍有牙） |

### 7.2 这一轮最值钱的：**怎么在本机复现"只有 CI 才会红"的失败**

上一节写着"这一条本机复现不出来"。**这句话这次被推翻了** —— 办法是：
**先在真日志里找到"CI 与开发机的唯一差别"，再在本机只制造那个差别。**

1. 差别 = **子进程 stdout 的编码**（runner cp1252 / 本机 cp936）。证据不是猜的：
   真日志里明写 `File "C:\hostedtoolcache\windows\Python\3.11.9\x64\Lib\encodings\cp1252.py"`。
2. 复现姿势（`temp/repro_rule19_cp1252.py`、`temp/diag_agent_cp1252.py`）：
   **在本进程内**设 `PYTHONIOENCODING=cp1252` —— 本进程的 stdout 早已建好、不受影响，
   而之后被拉起的桥/agent 全部继承 cp1252 ⇒ 一条命令复现出与 CI **一字不差**的 `no_reply`。
   更小的夹点（`temp/diag_agent_cp1252.py`）把机理钉死在假 agent 一行上：
   `UnicodeEncodeError: 'charmap' codec can't encode characters in position 46-48`（正是 `验证员` 三字）。
3. ⚠️**预测器必须忠实，否则比没有更糟**：第一版直接设 `PYTHONIOENCODING=cp1252` 跑全量，
   报出 8 条失败 —— 全是**假失败**：`action_scripts.run_action` 里有
   `env.setdefault("PYTHONIOENCODING", "utf-8")`（平台**故意**给自己拉起的动作脚本上的保险），
   我先把变量设上了 ⇒ `setdefault` 失效 ⇒ 那 8 条在 CI 上本来是绿的。
   ⇒ 忠实版（`temp/like_ci/sitecustomize.py` + `temp/run_suite_like_ci.py`）：
   **由每个 python 子进程自己判断**要不要切成 cp1252，且**跳过已显式指定编码的进程**；
   预测器自身也先用 `temp/check_like_ci_shim.py` 验过（无变量的子进程会炸、带 `utf-8` 的不受影响）。
   用它跑全量 ⇒ 全绿，与 run #12 的真实结论一致。

### 7.3 顺带挖出的**产品**缺陷（不是测试的）

`bridge_templates/{file_poll,pending_poll,cli_acp}/bridge.py.tmpl` 渲染出来的桥**没有任何输出编码自保**，
而它们会 print 大量中文（cli_acp 21 处 / file_poll 26 处 / pending_poll 37 处）——
用户的 stdout 一旦被重定向（验证间、看门狗、nohup、计划任务），
在**英文 Windows** 上就是 cp1252 ⇒ **桥进程启动即死**，用户只看到"桥不取件"。
（上一轮只给 `examples/*.py` 加了自保，**模板漏了**。）
⇒ 三个模板补上同一段；`tests/test_bridge_encoding_guard.py` 从"只守 examples"扩到**模板全覆盖**，
并新增**行为级**判据：真渲染模板 + 真在 cp1252 下跑 `--help`（旧模板 rc=1 报 UnicodeEncodeError、
新模板 rc=0 打出中文，⇒ 有牙）。`temp/verify_tmpl_encoding.py` 留了新旧对比的实测脚本。

### 7.4 仍未完成（交接）

- **psutil 跨平台进程探测**：仍未做（方案见 §5，老板已同意上 psutil）。
- **产品进程/探针层仍大量 PowerShell 专属**：`harness_probe` / `server`(4 处) /
  `routers/system` / `wakeup_bridge` / `assistant_tools`。
  **"Linux 上全功能可用"目前仍只对：平台服务端 + shell 工具 + 桥启动。**
- `项目完整模型.md` 的 CI 章节仍待补（本 V49 是它的素材）。
- **WSL 这条 Linux 环境**：本机 Ubuntu-24.04 + 网络可用（`temp/wsl_setup_min.sh`），
  建好 venv 后**Linux 侧改动就不必烧 CI 轮次**了；本轮**没装成**（pip 下载极慢），
  所以本轮 Linux 结论**仍以 CI 真日志为准**（不在本机假装验过）。

### 7.5 这一轮的纪律（新增两条，可查）

1. **诊断要"看得见"，不能只"存在"**：上一版 rule19 诊断在 `teardown=True`（已拆验证间）**之后**
   去列 inbox/隔间目录 ⇒ 现场永远是 `[]`/`{}`（**白烧一轮**）。
   真正留证据的是**桥日志** `<DATA_DIR>/verify_sandbox/_bridge_logs/<hid>.log`（**不随拆除删除**）。
   ⇒ 写诊断先问：**它读的那份东西，在我要读的时刻还在不在？**
2. **一次只改一处 + 改完立刻用"同条件"验**：本轮四处改动（模板 / rule19 用例 / shell 工具 / spawn 守卫）
   每处都在改完当场用**能判死的老条件**验过（新旧对比、cp1252 复现、WSL 里 `sh` 的语义夹点），
   最后才推 —— 与上一轮"堆一批补丁再统一测"是两种节奏。

---

*示例Harness-E · 2026-10-14（含晚场续篇）· 本篇是 CI 实证记录 + 交接；制度本身见 `PORTABILITY.md`*
