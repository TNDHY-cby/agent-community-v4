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
*示例Harness-E · 2026-10-14 · 本篇是 CI 实证记录 + 交接；制度本身见 `PORTABILITY.md`*
