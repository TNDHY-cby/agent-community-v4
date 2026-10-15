# V50 · 看门狗 stdlib 跨平台化 + 纯标准库进程探测（procprobe）

> **性质**：**欠账清偿稿**（不是新功能）。欠账出处＝[`PRODUCTIZATION.md`](../PRODUCTIZATION.md) 欠账表第 1 行、
> [`PORTABILITY.md`](../PORTABILITY.md) §5.2「仍是 Windows 专属」表第 3 行、
> 以及继承书 `上下文继承书_20261015.md` §4 欠账第 1 条。
> 依据（上一场实证）：[`V49_可移植性的CI实证与跨平台进程探测.md`](./V49_可移植性的CI实证与跨平台进程探测.md) §7/§8。

## 0. 一句话

看门狗（`tools/watchdog.py`、`deploy/watchdog_all.py`）**承诺"全程标准库、无第三方依赖"**
（它是"救一个坏掉的环境"的那只手，那时连依赖都可能装不上）⇒ 它**不能引 psutil**，
于是它的进程发现至今是**Windows 专属**：在 Linux 上"谁在监听这个端口 / 这个 pid 还在不在"
恒返回空 ⇒ **看门狗在 Linux 上等于没有**（服务挂了不会重生，端口被占着也查不到、杀不掉）。

本稿的做法：把"**零第三方依赖的本机事实探测**"抽成**一个**纯标准库模块
`agent_community/platform/procprobe.py`，看门狗与 `sysprobe` 都走它 —— 一处真相，两个约束都满足。

## 1. 判据（可执行，不认"我这台能跑"）

1. **行为级**：在**真 Linux**（本机 WSL Ubuntu-24.04 + CI 的 `ubuntu-latest`）上，
   `python agent_community/tools/watchdog.py --check --port <一个真在听的端口>` 必须报 **OK**
   （exit 0），并且 `--check` 对**一个真起的僵死监听**能认出 pid；
2. **零依赖**：`procprobe.py` 的 import 集合 ⊆ 标准库（用 `sys.stdlib_module_names` 真判，
   不靠肉眼看源码）；
3. **一处真相**：平台专属命令（`powershell`/`netstat`/`tasklist`/`taskkill`/`Get-*`）
   在 `tools/`、`deploy/` 里**一条都不剩**；`check_productization.py` 的例外申请从 6 条降到 0 条；
4. **没 psutil 也不瞎**：把 `sysprobe._psutil()` 封成 `None`（模块加载器仍可用），
   `iter_processes()` / `listening_pids()` / `process_cmdline()` / `is_pid_alive()` / `kill_pid()`
   **必须照常答对**（真拿自己的进程、真开一个端口、真起一个子进程来杀）；
5. **Windows 行为零变化**：Windows 上的回落命令仍是原来那套（PowerShell/netstat/tasklist/taskkill），
   端口 18920 / 9105 现有守护路径不变。

## 2. 为什么是"新模块 + 让 sysprobe 也转向它"，而不是"在看门狗里就地写一份"

现成的 `platform/sysprobe.py` 已经是"本机事实的**唯一**入口"（`AGENTS.md` / `PORTABILITY.md` 都这么写）。
但它的**主路径是 psutil**，没有 psutil 时**非 Windows 平台如实返回空** ——
所以看门狗**不能**用它（一用就要求用户装 psutil，违背"救坏掉的环境"这个定位）。

若在看门狗里就地写一份 POSIX 实现 ⇒ 同一个事实（谁在监听某端口）**两处实现**，
违反规则 20（一处真相），且 `sysprobe` 在"Linux + 没 psutil"下**仍然瞎**
（那条缝隙不是看门狗特有的，平台自己也踩：`listening_pids()` 恒空 ⇒ 界面说"没查到"）。

⇒ 正确的切法是**按约束分层**，不是按调用方分层：

| 层 | 模块 | 依赖 | 谁用 |
|---|---|---|---|
| ① 首选 | `platform/sysprobe.py` | **psutil 优先**，允许第三方 | 平台业务（桥监管、退出并关闭、系统信息） |
| ② 兜底 | **`platform/procprobe.py`（本稿新增）** | **纯标准库，零第三方** | 看门狗、部署脚本、`sysprobe` 的回落层 |

`sysprobe` 在**没有 psutil** 时**整个转向 procprobe**（Windows 与 POSIX 都是），
自己不再留第二份平台实现 ⇒ 平台侧与看门狗侧从此**同一份事实代码**。

## 3. `procprobe.py` 的能力面与平台矩阵

**API**（与 `sysprobe` 同名同义，便于"`sysprobe` 回落层 = procprobe"这句话可以直接在代码里成立）：

```
backend() -> "windows" | "procfs" | "ps" | "none"
probe_note() -> str                      # 如实说明现在走哪条路、探不到时怎么办
iter_processes() -> [(pid, ppid, name, cmdline)]
process_cmdline(pid) -> str
is_pid_alive(pid) -> bool
listening_pids(port) -> [pid]
listening_ports(*, local_only=False) -> [port]
kill_pid(pid, *, tree=False) -> bool     # 返回"确实已不存活"，不是"命令返回 0"
_run(argv, timeout) -> str               # 唯一的子进程出口（守卫封它来证明"主路径不 shell"）
```

| 能力 | Windows | Linux（`/proc` 可用） | 其它 POSIX（macOS 等） |
|---|---|---|---|
| 进程表 | `Get-CimInstance Win32_Process` → JSON | 扫 `/proc/<pid>/stat`（pid/ppid/comm）+ `/proc/<pid>/cmdline` | `ps -eo pid=,ppid=,comm=,args=` |
| 命令行 | 同左（按 pid 过滤） | `/proc/<pid>/cmdline`（NUL 分隔） | `ps -o args= -p <pid>` |
| pid 存活 | `tasklist /FI` | `os.kill(pid, 0)`（`PermissionError` ＝ 活着但不属于我） | 同 Linux |
| 端口归属 | `netstat -ano` | `/proc/net/tcp{,6}` 取 LISTEN inode → 扫 `/proc/*/fd` 找 `socket:[inode]`；**取不到再问 `ss -ltnp`** | `lsof -nP -iTCP:<port> -sTCP:LISTEN -t`，没有 lsof 就 `ss -ltnp` |
| 监听端口表 | `netstat -ano` | `/proc/net/tcp{,6}`（`local_only` 判 回环/通配） | `lsof -nP -iTCP -sTCP:LISTEN` |
| 结束进程 | `taskkill /F [/T]` | `SIGTERM` → 等 → `SIGKILL`；`tree=True` 先用 ppid 图收子进程 | 同 Linux |

**诚实边界（要说话，不许假装）**：

- Linux **非 root** 只能读**自己**进程的 `/proc/<pid>/fd` ⇒ `listening_pids()` 的语义是
  **"可查范围内"**，`probe_note()` 必须说清「探不到 ≠ 真没有」（沿用 `sysprobe` 的原话）；
- **macOS 没有 `/proc`**，走 `ps`/`lsof`；这两条命令**系统自带**，但若被裁掉就如实返回空 + 说明；
- 平台专属**概念**（激活软件窗口、系统回收站）**不在本稿范围**，继续如实返回不支持。

## 4. 改动清单（含"对外表达一次全改"，规则 20）

| # | 文件 | 改动 |
|---|---|---|
| 1 | `agent_community/platform/procprobe.py` | **新增**：纯标准库实现（本稿主体） |
| 2 | `agent_community/platform/sysprobe.py` | 去掉自己那份 PowerShell/netstat/tasklist 回落代码；没有 psutil 时**整层转 procprobe**；`backend()` 新增 `procfs`/`ps` 取值；`probe_note()` 跟着改 |
| 3 | `agent_community/tools/watchdog.py` | 删掉 `_run_powershell`/`netstat`/`tasklist`/`taskkill`/WMI 全部就地实现；**按文件路径**加载 procprobe（不依赖包导入，保住"能单独跑"）；`_gui_shell_exists` 改为跨平台的进程表匹配（判据抽成纯函数 `_looks_like_gui_shell`）；新增 `--probe-info` 排障开关 |
| 4 | `deploy/watchdog_all.py` | `kill_listener()` 走 procprobe（新增 `_listener_pids()`）；同样按文件路径加载 |
| 5 | `agent_community/tests/test_procprobe.py` | **新增**行为级守卫（真进程 / 真端口 / 真子进程 / 真杀 / **按生产调用方式真跑看门狗**；纯标准库断言） |
| 6 | `agent_community/tests/test_sysprobe.py` | 更新两处**已过时的真话**（`backend()` 取值域、"封死回落通道"要连 procprobe 一起封）；新增 `TestStdlibTierWithoutPsutil` |
| 7 | `agent_community/tests/test_watchdog_health.py` | `TestGuiShellRegexNotHardcoded` 的**源码文本**断言 → 升级成 `TestGuiShellDetection`（**行为判据**，喂合成行） |
| 8 | `tools/check_productization.py` | 扫描面补上"未跟踪但未被 `.gitignore` 排除"的 .py（**新文件不该对扫描器隐形**）。⚠️ 最终**没给 procprobe 加例外**：它的平台命令都落在带平台分支的函数里，`fail=0` |
| 9 | `PORTABILITY.md` §5.2 | 看门狗从"仍是 Windows 专属"**移入**"已在 Linux 上真的能用"；规则段补 procprobe 层 |
| 10 | `PRODUCTIZATION.md` | 规则 A 补 procprobe；欠账表那行**移入"已还"** |
| 11 | `AGENTS.md` / `项目完整模型.md` / 技能 `ac-project-dev` | 口径同步（"看门狗仍是 Windows 专属"这句话作废） |
| 12 | `CHANGELOG.md` + `__version__` | 发布时**同一笔提交**（守卫会抓） |

## 5. 守卫怎么"有牙"（按真结果写）

- `test_procprobe.py::TestRealFacts::test_finds_the_socket_we_open` —— **真开**一个 `127.0.0.1:0` 监听，
  问 `listening_pids()`：自己的 pid 必须在里面；关掉之后必须不再报在听。
  （旧实现在 Linux 上恒返回空 ⇒ 这条会**当场变红**，这就是"有牙"。）
- `test_procprobe.py::TestRealFacts::test_kill_pid_ends_a_real_child` / `test_kill_pid_tree_takes_the_children_too`
  —— 真起子进程（乃至"父→孙"）→ 真杀 → 必须**真的没了**（只看父进程死不等于孙子也死）。
- `test_procprobe.py::TestWatchdogUsesIt::test_watchdog_check_ok_for_a_real_listener`
  —— **按生产调用方式**起子进程 `python agent_community/tools/watchdog.py --check --port N --health tcp`，
  对一个真在听的端口必须 `exit 0` 且输出里含**监听者的真实 pid**（这条同时验了
  "包不在 `sys.path` 上时看门狗仍能单独跑"）。
- `test_procprobe.py::test_pure_stdlib_only` —— AST 取 import，逐个查 `sys.stdlib_module_names`。
- `test_sysprobe.py::TestStdlibTierWithoutPsutil` —— 把 `sysprobe._psutil` 打成 `None`，
  上面几件事实**仍须答对**（这一条把"没 psutil 就瞎"钉死）。
- `test_watchdog_health.py::TestGuiShellDetection` —— 喂合成进程行，验"按**本守护自己的 port** 判 GUI 壳"
  （V19 缺陷②的行为版）。

**突变检验**（`python temp/procprobe_mutation_check.py`，在文件末尾追加覆盖、跑完即还原）：
**4/4 KILLED**（Windows 与真 Linux 各跑一遍）——

| 突变体 | 它模拟的错误 | 结果 |
|---|---|---|
| M1 `listening_pids` 恒空 | 当年那份 Windows 专属实现（Linux 上谁也查不到） | KILLED |
| M2 `is_pid_alive` 恒 True | 不把僵尸当"已死"（Linux 上 `kill` 永远返回 False） | KILLED |
| M3 `kill_pid` 直接 `return True` | "命令返回 0 就算成功" | KILLED |
| M4 `import psutil` | 破坏"全程标准库"这条承诺 | KILLED |

## 6. ★真 Linux 跑出来的一个真缺陷（**只有真跑才会露**）

第一版在 Windows 上 12/12 全绿，搬到 **WSL 真 Linux** 上 `test_kill_pid_*` **三条全红**
（`procprobe` 两条 + `sysprobe::TestStdlibTierWithoutPsutil` 一条）：

> 进程被 `SIGKILL` 之后先进入**僵尸态**（等父进程收尸），而 `os.kill(pid, 0)` 对僵尸**照样成功**
> ⇒ `is_pid_alive()` 报"还活着" ⇒ `kill_pid()` **永远返回 False**。

**Windows 上没有僵尸态**，所以这台机器怎么测都是绿的 —— 这正是本项目
"本机绿不算绿、判据只认真平台实跑"那条纪律的一次实证。
修法：`is_pid_alive()` 认 `/proc/<pid>/stat` 的**状态字符**，`Z` **不算活着**
（非 Linux 的 POSIX 用 `ps -o state=`；Linux 上 `/proc` 里查无此 pid 也判"已死"）。
修完真 Linux 上 `22 passed`。

## 7. 明确不做（避免"顺手改大"）

- **不引 psutil 进看门狗**（那是它存在的反面）；
- **不动** Windows 现有守护路径的行为（18920 与 9105 的 `--spawn-cmd` / 重启语义 / 日志格式）；
- **不**把 `sysprobe` 的 psutil 主路径改成别的；
- 窗口激活 / 回收站**保持**"非 Windows 如实不支持"。

## 8. 实跑记录（判据只认这些）

| 环境 | 做了什么 | 结果 |
|---|---|---|
| Windows（本机，uv 3.11） | 看门狗 `--probe-info` / `--check`、`procprobe` 自检 | `backend=windows`；260 条进程；`--check` 认出监听 pid |
| Windows | `agent_community/tests` 全量 | **全绿**（1390 collected） |
| Windows | `temp/procprobe_mutation_check.py` | **4/4 KILLED** |
| Windows | `check_productization.py` / `check_portability.py` | `fail=0` / `[OK]` |
| 真 Linux（WSL Ubuntu-24.04，3.12 + psutil 7.2.2） | `temp/wsl_procprobe_check.sh` | `backend=procfs`；`--check` 对真监听 `exit 0` 且报出 pid；**22 passed**；突变 **4/4 KILLED** |

---
*示例Harness-E · 2026-10-15 · 本稿是欠账清偿的实现依据；判据以 `PRODUCTIZATION.md`（唯一规则源）为准*
