# 可移植性：把"换台机器能跑"变成可执行的规则

> 本文是**可移植性的唯一规则源**。`AGENTS.md`、`CLAUDE.md`、`.cursorrules` 都指向它。
> 起因（老板 2026-10-14 原话）：「**本地网址不应该引入**」「**本地端口因人而异**」。
> 判据一句话：**当前电脑能跑不算合格；陌生环境只改配置就能跑才算合格。**

> ⚠️ **本文引用的这些是「开发副本独有」、公开仓库不含**：`temp/*.py`（各项突变检验/诊断脚本）、
> `deploy/`（部署脚本，含 `watchdog_all.py`）、`publish_acv4.ps1`（发布工具）、`HANDOVER.md`。
> 它们是**开发侧证据与工具**；公开读者要复核结论，请看仓库里的 `tests/` 与 `tools/`。

---

## 1. 硬规则（违反即不合格，扫描器会拦）

**禁止硬编码**
- 端口、IP、域名、`localhost:数字`
- 绝对路径、用户名、本地目录（`C:\Users\xxx`、`/Users/xxx`、本机盘符目录）
- 密钥、数据库 URL、API 地址、回调地址

**必须做**
1. 所有环境差异**从环境变量读**，集中登记在 `agent_community/platform/env_spec.py` 并做校验。
2. 端口/平台地址**只经** `agent_community/platform_address.py` 解析（别处不许自己拼）。
3. 提供 `.env.example` 与**环境变量表**（README 的「环境变量」节）——**由脚本生成，别手抄**。
4. 默认值只用于开发，且**必须能被环境变量覆盖**；生产缺必需变量时**启动失败并提示**
   （`AC_ENV=production` 下由 `env_spec.assert_ready()` 抛错并列出怎么修）。
5. 本地适配只改 `.env`（已在 `.gitignore`），**不得改源码**。
6. 完成后**自查硬编码**，并输出：环境变量清单 + 默认值 + 是否必需 + 自查结果。

## 2. 派任务时的标准话术（复制给任何 agent）

> 实现 X。**按 `PORTABILITY.md` 的可移植性规则**。先列出涉及的环境变量和默认值，再写代码。
> **禁止针对我的电脑。** 验收：在陌生 Linux 容器里只改环境变量即可运行。

被纠正时（项目负责人对我们说的原话）——不要只改那一处：

> 你写死了端口，这不可接受。请重构为配置驱动，并同步更新 `AGENTS.md`、`.env.example`、
> README 和测试。以后任何环境相关值都不得硬编码。完成后给我 grep 自查结果和环境变量表。

## 3. 工程层：配置驱动是默认

```python
# ✅ 端口/地址：一处真相
from agent_community.platform_address import resolve_port, base_url
port = resolve_port(explicit=args.port)      # 命令行 > AC_PORT > 默认
print(base_url())                            # http://127.0.0.1:<真实端口>

# ✅ 其它环境差异：登记 + 读取
from agent_community.platform import env_spec
data_dir = env_spec.get("AC_DATA_DIR")
strict = env_spec.is_production()
```

```python
# ❌ 不要
app.listen(18920)                     # 写死端口
BASE = "http://127.0.0.1:18920"       # 写死本机地址
DEFAULT_URL = "http://localhost:8000" # 写死本机地址
CONFIG = Path(__file__).parent / "data"   # 不认 AC_DATA_DIR
```

**必须提供**：`.env.example`｜README 环境变量表｜`env_spec` 集中读取与校验｜
`Dockerfile` + `docker-compose.yml`｜CI 在**干净 Linux 环境**跑测试｜
**测试优先随机端口**（`AC_PORT=0` 让系统分配），不要写死测试端口。

**端口冲突时**：改 `.env` 里的 `AC_PORT`，**不要改代码**。

## 4. 验收层：Definition of Done（每次任务完成必须满足）

- [ ] 没有 `listen(18920)`、`localhost:数字`、`127.0.0.1:数字` 这类**在生产代码里**的硬编码
- [ ] 有 `.env.example` 和 README 环境变量表，且与 `env_spec` **一致**（生成器 `--check` 通过）
- [ ] 换 `AC_PORT=8080`、换 `AC_DATA_DIR`、换 `AC_AI_BASE_URL` 后**无需改代码**
- [ ] 在**干净环境**（CI 的 Linux job / Docker）能跑
- [ ] 密钥不进仓库（`tests/scan_sensitive.py` + `.gitignore`）
- [ ] 输出了**环境变量清单**和**自查结果**

**一键自查**（三条命令，CI 与发布前都跑）：

```bash
python tools/check_portability.py      # 硬编码扫描（fail=0 才合格）
python tools/gen_env_docs.py --check   # 环境变量文档是否漂移
python -m pytest -q                    # 回归
```

### 4.1 判据怎么跑（★2026-10-17 起：**本机双平台真跑**替代 CI）

> 老板 2026-10-16/17 拍板：「**GitHub CI 停更**」「更新 Gitee」「**本机双平台真跑替代**」。
> ⇒ **现行判据＝一条命令**（Gitee 侧没有免费 CI；GitHub Actions 不再跟随新提交）：

```bash
python tools/dual_platform_check.py            # 两侧都真跑 + 一张表 + 结论
python tools/dual_platform_check.py --plan     # 只打印"它会怎么做"（不跑）
python tools/dual_platform_check.py --local-only   # 只跑本机这一侧（诚实标"部分"）

# ★发布前**一条命令**跑全五类判据（预检 / 交接守卫 / 双平台 / 首跑 / 发布面三件套）：
python tools/pre_release_check.py            # 退出码 0=全过、1=有红、2=有项没跑成（＝部分，不算通过）

# ★**核心体检**一条命令（协议 / 桥 / 唤醒脚本 / 看门狗，约 2 分钟）：
python tools/core_check.py                   # 六协议矩阵+闭环、acp 真拉起、看门狗救活、桥产物落点（仓库不许新增）

# ★**活着 / 自愈**（发布面自带，零第三方依赖）：
python tools/watchdog.py --check             # 服务活没活（exit 0=活 / 1=挂，并说清为什么）
python tools/watchdog.py --daemon            # 常驻守护：挂了就拉起来（窗口内重启次数超限会停下并说明）
python tools/check_watchdog.py               # 判据：看门狗**真能**救活服务（能起 + 崩了能再起 + 有日志）
python tools/autostart.py enable            # 随登录自启（Windows 启动文件夹 / Linux systemd user unit）
```

**退出码本身就是判据**：`0`=两侧都绿；`1`=有侧红；**`2`=只跑成了一侧（＝部分，不算通过）**。
Windows 侧＝本机 `pytest`；Linux 侧＝**WSL 里跑 `tools/run_suite_in_repo.sh`**（同一个仓库、同一套用例）。

**本机适配只改 `.env`（不改源码）**：`AC_LINUX_WSL_DISTRO`（默认 `Ubuntu-24.04`）与
`AC_LINUX_PYTHON`（WSL 里**装好依赖的那个解释器**）。这两个键是**工具本地配置**，
**不进 `env_spec`**（它们不是平台运行期变量）；跑不了另一侧时工具**如实报缺什么、返回 2**，绝不假装通过。

> ⚠️ **两个实测坑（2026-10-17 都踩过，别再花时间）**：
> 1. **`/mnt/<盘>` 可能是空的**：本机 `/etc/wsl.conf` 里 `[automount] enabled=false` ⇒ WSL 重启后
>    `/mnt/d` 是**空目录**，Linux 侧会报 `bash: /mnt/d/…: No such file or directory`
>    —— **看着像脚本没了，其实是盘没挂**。工具会**按需 `mount -t drvfs`**（不改你的系统配置）；
>    挂不上就如实给两条补救路径（临时挂载 / 改 `wsl.conf` 后 `wsl --shutdown`）。
> 2. **Linux 侧必须把解释器所在 bin 放进 PATH**：否则用例里 `spawn("python", …)` 的子进程找不到解释器
>    ⇒ `test_workshops_session` **4 条假失败**（Windows 绿、Linux 红，看着像真 bug）。
>    这条已固化在 `tools/run_suite_in_repo.sh` 里，**别删那行**。

#### 4.1.1 `.github/workflows/ci.yml`（**已停更，仅归档**）

GitHub 那边的工作流文件**保留**（谁要重启用照着跑即可），但**不再跟随新提交、不再是判据**。
（历史：曾短暂放在 `ci/ci.yml` 绕过令牌限制，老板 2026-10-14 在网页端建好标准位置那份之后 `ci/` 已删除 ——
两份定义并存必然漂移，规则 20 不允许。）若将来要改它，注意那个老坑：
推送用的令牌**没有 `workflow` 权限**时，GitHub 会整次拒收：

```
! [remote rejected] master -> master
  (refusing to allow a Personal Access Token to create or update workflow
   `.github/workflows/ci.yml` without `workflow` scope)
```

⇒ 要么在网页端改，要么给令牌补 `workflow` 权限（且本地那份必须与远端逐字节一致）。

#### 4.1.2 依赖必须全部声明（2026-10-14 CI 第一次真跑的收获；结论对今天仍然有效）

CI 首跑**两个 job 同一步挂掉**，`portability` job 是过的：

```
agent_community/proto/agent_community_pb2.py:7: in <module>
    from google.protobuf import descriptor as _descriptor
E   ModuleNotFoundError: No module named 'google'
!!!!!! Interrupted: 1 error during collection !!!!!!
```

真因不是网络、也不是工作流写错，而是**依赖漏声明**：`requirements.txt` 只写了 `grpcio`，
而 **grpcio 不拉 protobuf**（`pip show grpcio` 的 Requires 只有 typing-extensions），
仓库根也没有 `pyproject.toml` 兜底 ⇒ 干净 runner 装不出 `google.protobuf`。
**本机一路 1391 passed，只是因为恰好装着 protobuf 7.36.2** —— 又一次"我这台能跑不算数"。

处置（不止补这一个包，而是把**这一类**堵死）：

- `requirements.txt` 补 `protobuf>=7.35.1`（下限＝pb2 的 gencode：`ValidateProtobufRuntimeVersion`
  会校验运行时 minor ≥ gencode，**不许调低**）；
- 新增守卫 `TestThirdPartyImportsAreDeclared`（`agent_community/tests/test_foundation_base.py`）：
  用 AST 扫 `agent_community/**` 的第三方 import，凡未在 `requirements.txt` 声明的一律判红；
- 守卫用 `temp/deps_mutation_check.py` 做过突变检验（删 protobuf / 调低下限 / 删 fastapi ⇒ 三次全 KILLED）。

> ⚠️ **CI 里那句兜底 `|| pip install fastapi uvicorn click httpx pydantic pytest` 是个陷阱**：
> 它装出来的环境和 `requirements.txt` **不是同一个**，绿了也不能说明任何事
> （本次事故它甚至没触发，因为主装路径是成功的）。**建议删掉 `||` 那半句**，
> 改成 `pip install --retries 5 --timeout 60 -r requirements.txt` —— 要的是抗网络抖动，
> 不是"装个残缺环境然后假装通过"。

#### 4.1.3 路径里的非 ASCII（中文用户名/中文目录）是同一类假绿

干净环境验收顺带抓到的第二个缺陷：`agent_community/tests/test_rule19_fresh_identity.py`
造"假 agent"的 `.cmd` 启动器时写死 `encoding="ascii"`，注释理由是"纯 ASCII、无 BOM —— cmd 的坑"。

- **"无 BOM"才是真正的不变量；"纯 ASCII"只是个偷懒的代理条件**。
- 只要**解释器路径含非 ASCII**（把 venv 建在 `D:\DSH工作区1\temp\cleanvenv\` 下就行），
  插 `sys.executable` 时直接 `UnicodeEncodeError: 'ascii' codec can't encode characters in position 7-9`；
  后续症状伪装成 `state='no_reply'`（"桥取走了测试消息，但没等到 agent 回话"）——
  **报错信息完全指不到真因**。
- 为什么开发机与 CI 都绿：开发机解释器在 `D:\Program Files\...`、GitHub runner 也在纯 ASCII
  路径下。而真实用户 `C:\Users\张三\AppData\Local\Programs\Python\...` **极其常见**。

**修法**：`.cmd` 一律 **UTF-8 无 BOM**（见 `temp/diag_cmd_encoding.py` 的实测数据：
本机 `chcp`=936，ascii 编码就失败、`mbcs` 被 cmd 判成"找不到路径"、**UTF-8 正常**；
且 UTF-8 对纯 ASCII 内容产生的字节与原实现完全一致 ⇒ **常见情形行为不变，非 ASCII 情形从报错变能跑**）。
守卫：`test_fake_agent_launcher_survives_non_ascii_interpreter_path`，
突变检验 `temp/cmd_launcher_mutation_check.py`（把 UTF-8 退回 ascii ⇒ KILLED）。

`check_portability.py` 的语义：
- 扫描范围＝**发布面**（`agent_community/`、`design-docs/`、`tests/`、根文档），
  与 `publish_acv4.ps1` 的同步面**一一对应**；dev-only 运维脚本不进范围（避免 alert fatigue）。
- 命中即 **fail**；测试/文档/docstring 内部命中只 **warn**。
- 确有理由的例外，必须在该行写 `# portability: allow(<一句话理由>)` —— **例外要显式申请**。

## 5. 现状（2026-10-14 自查）

- 环境变量：**49 个**登记在 `env_spec.SPEC`，其中"条件必需"按功能启用与否判定；
- 端口/平台地址/监听地址：**一处真相** `platform_address`
  （原先端口散在 6 处、默认值还不一致：9103 / 18920）；
- 数据落点：全部认 `AC_DATA_DIR`（原先 5 处写死包内 `data/`）；
- 扫描器：发布面 **fail=0**（warn≈95，全部是测试/文档/docstring 内部的说明性命中）；
- 监听地址：`--host` > `AC_HOST` > `0.0.0.0`；
- CI：新增干净 `ubuntu-latest` 作业，把上面三条自查 + "换端口/换数据目录后能起"跑成**失败即拦**。

### 5.2 跨平台现状：**"在 Linux 上真的能用"到底涵盖哪些**（2026-10-14 实测口径）

**规则（新增，与"一处真相"同源）**：凡要拿**本机事实**——进程表 / 某进程的命令行 /
pid 是否存活 / 谁在监听某端口 / 结束进程——**一律走 `agent_community/platform/sysprobe.py`**，
**不许在别处再直接 shell 出去**（`Get-CimInstance` / `Get-NetTCPConnection` / `tasklist` /
`netstat -ano` / `taskkill`）。理由不是"整洁"，而是**那些命令只在 Windows 存在**：
写死它们 ⇒ 在 Linux 上**静默降级**（恒返回空/False），
外部看到的是"桥明明在跑却认不出来""端口被占了却查不到""点了退出却关不掉"，
而**测试里"认不出来"类断言会恒真 = 假绿**（本项目为此付过好几轮 CI）。

**两层实现，一处真相**（2026-10-15 补齐第二层）：

| 层 | 模块 | 依赖 | 谁用 |
|---|---|---|---|
| ① 首选 | `platform/sysprobe.py` | **psutil 优先**，允许第三方 | 平台业务（桥监管 / 退出并关闭 / 系统信息） |
| ② 兜底 | **`platform/procprobe.py`** | **纯标准库，零第三方** | **看门狗那一族**（它们**不能**引 psutil：承诺"救一个坏掉的环境"，那时连依赖都装不上），以及 `sysprobe` 没有 psutil 时的**整层回落** |

⇒ "**Linux 上真的能用**"这件事，**不再以"装了 psutil"为前提**。
守卫：`agent_community/tests/test_procprobe.py`（真进程 / 真端口 / 真子进程 / **按生产调用方式
`python agent_community/tools/watchdog.py --check` 真跑一次** / "纯标准库"用 `sys.stdlib_module_names` 真判）、
`agent_community/tests/test_sysprobe.py`（**行为判据**：真开一个监听端口问它、
真起一个子进程杀掉它、"**把回落通道整个封死，主路径仍须工作**"，以及
`TestStdlibTierWithoutPsutil`：**把 psutil 封成 None 之后这些事实仍须答对**）。

**已经在 Linux 上真的能用**（都经真 Linux 环境实跑，见 `design-docs/V49…md` §8 与 `V50`）：

| 能力 | 载体 |
|---|---|
| 平台服务端本体（起服务、端点、工作间、会话） | `platform/server.py` + `routers/` |
| 命令执行工具 | `platform/tools/shell_tool.py`（POSIX 走 `/bin/sh -c`） |
| 桥的启动 / 结束 | `platform/spawn.py`、`platform/examples/*_bridge.py` |
| **进程/端口事实**（桥在不在跑、端口归属、pid 存活、结束进程） | `platform/sysprobe.py`（psutil）+ **`platform/procprobe.py`**（纯标准库兜底） |
| **看门狗**（健康判定 / 查出端口归属 / 杀掉僵死进程 / 按原命令行拉起） | `tools/watchdog.py`（公开）＋ `deploy/watchdog_all.py`（**dev-only，公开树不含**；**全程标准库**，走 `procprobe`） |
| 主动探测外端 agent（进程/端口/HTTP/文件） | `platform/harness_probe.py` |
| 「退出并关闭」的整条链路 | `platform/routers/system.py` |
| 剪贴板型唤醒 | `platform/wakeup_bridge.py`（非 Windows 走 `xclip`/`xsel`/`pbcopy`） |

**仍是 Windows 专属（明确欠账，不是"忘了"）**：

| 能力 | 位置 | 为什么 |
|---|---|---|
| 激活软件窗口（监控室点画面） | `server.py::_activate_windows_by_pids` | 靠 Win32 的前台窗口模型；非 Windows 上**如实返回不支持**（不假装成功） |
| 桥目录进回收站 | `server.py::_trash_bridge_dir` | 回收站是 Windows 机制；非 Windows **不代删**（承诺的是"可恢复"，换个删法就是数据丢失） |


### 5.1 按"陌生人的方式"实跑验收，抓出的五个真缺陷（都已修，且都上了守卫）

这四条**不是读代码发现的**，是**真起服务/真跑那个平台**发现的 ——
所以下面的验收判据只能是"起真服务 / 真平台实跑"，不许退化成读源码：

| # | 缺陷 | 症状（实测） | 修法 | 守卫 |
|---|---|---|---|---|
| A | `AC_HOST` 是**改不动的开关** | `AC_HOST` 已登记进 `env_spec` / `.env.example` / README，但 `server.py` 末尾直接 `uvicorn.run(host="0.0.0.0")` ⇒ 设 `AC_HOST=127.0.0.1` 仍监听 `0.0.0.0` | 走 `platform_address.resolve_host()`（`--host` > `AC_HOST` > 默认） | `test_host_resolution_priority`、`test_server_really_binds_the_configured_host`（真起进程，从非回环地址连不上才算过） |
| B | 生产模式缺必需项**只警告不失败** | `AC_ENV=production` 且缺 `AC_DATA_DIR` / 关停口令 / 令牌，一路打警告**照样起服务** ⇒ "生产缺必需项就失败"等于没做 | `_conditional_issues()` 里生产模式的两条升格为 `error`；回环部署只把令牌/口令当提示 | `test_production_missing_required_fails_startup`（真调 `assert_ready()`，不看文本） |
| C | `AC4_TOKEN` 是**设了也没用的令牌**（2026-10-15 容器真机走查抓出） | README 环境变量表 / `env_spec` / **`docker-compose.yml` 头部**三处都写着「在 `.env` 里设 `AC4_TOKEN=<令牌>`，请求带 `Authorization: Bearer`」，而实现**只读 `--token` 命令行** ⇒ 容器里（`Dockerfile` 的 `CMD` 没有 `--token`）与 `cli start`（`Popen` 也没传）**设了令牌完全无效**。真机实测 `403 {"error":"服务未配置访问 Token"}` —— **看起来像"你没配"，其实配了** | 新增 `server.collect_allowed_tokens()`：`--token` **＋ 环境变量 `AC4_TOKEN`** 合并；启动时如实打印"已配置 N 个 / 未配置 ⇒ 只有本机能访问" | `tests/test_ac4_token_env.py`（9 条：真读环境变量、真调 `_auth_check`、真调中间件、**真起一个只设 env 不带 `--token` 的服务进程**）；突变 `temp/ac4token_mutation_check.py` ⇒ **2/2 KILLED** |
| D | AI 密钥"**已加密保存**"是**假话**（非 Windows，2026-10-15 真 Linux 复现） | `config.py::save_config()` **无条件**打印「AI API Key 已加密保存（DPAPI 当前用户绑定）」，而非 Windows 上 `_encrypt_secret` 是**明文回退** ⇒ 真 Linux 实测：屏幕说"已加密"，磁盘上是 `sk-repro-…` **原文**。一句话同时违反两条承诺：README「本地配置里会加密保存」、《安全披露_CDP镜像与密钥存储》「非 Windows 降级为明文保存**并在保存时打印告警**」 | 按**实际落盘形态**分档说：带 `dpapi:` 前缀 → "已用 DPAPI 加密保存（当前用户绑定）"；否则 → "**警告**：…以**明文**保存 … 请 `chmod 600`，或改用 `AC_AI_API_KEY`（不落盘）" | `tests/test_security.py::test_save_config_message_matches_disk`（**平台无关的不变量**：消息必须与磁盘字节一致）、`test_secret_storage_roundtrip_on_every_platform`（**撤掉整条 skip**：两平台都验，差异如实断言）；突变 `temp/dpapi_mutation_check.py` **两平台各跑一遍** ⇒ 关键突变 KILLED |
| E | 剪贴板把**中文写坏**（中文 Windows 上「剪贴板型唤醒」一直在送垃圾，2026-10-15 实测） | `wakeup_bridge._set_clipboard()` 的 Windows 分支用 `Set-Clipboard -Value $input` 把文本走 **stdin**，而 **PS 5.1 按控制台输入码页解码 stdin**（中文 Windows = cp936），Python 送进去的却是 **UTF-8 字节** ⇒ 实测 `中文标记` 进到剪贴板变成 `涓\ue15f枃鏍囪\ue187`（UTF-8 被当 GBK 解），**而函数照常返回成功**。中文 Windows 上 `V25` §5 那个"永远可用"的兜底档 f 送的全是乱码 | **不经 stdin**：把文本 base64 嵌进命令（命令全 ASCII，不受码页影响），在 PowerShell 里 `[Text.Encoding]::UTF8.GetString(...)` 解回再 `Set-Clipboard`；超长文本（>20KB，命令行长度上限）走临时文件、PowerShell 侧显式按 UTF-8 读 | `tests/test_visual_launch.py::test_clipboard_fallback_really_writes_or_says_what_to_install`（**写中文 → 真读回来比对**；读回时还必须逼 PowerShell 吐 UTF-8，否则分不清"剪贴板坏了"还是"我读坏了"） |

> **教训（写进档案）**：文档里承诺一个"改不动的开关"，比不写这个开关**更坏** ——
> 用户会照文档改，改完以为生效了，然后拿错误的现象来问"为什么没用"。
> 这类缺陷**只有把服务真起起来、看真实监听/真实退出码**才能发现。
> A 是"改不动"、C 是"设了没用"，**同族两条** ⇒ 判据不变：**文档承诺的开关必须真能用**。

守卫的突变检验（有人把实现退回错误写法时，守卫**必须变红**）：
`python temp/acceptance_mutation_check.py` ⇒ 突变 A / B-1 / B-2 全部 `KILLED`；
`python temp/ac4token_mutation_check.py` ⇒ 突变 C 全部 `KILLED`；
`python temp/dpapi_mutation_check.py` ⇒ 突变 D **两个平台各跑一遍**、各自的关键突变 `KILLED`
（"无条件说已加密"只在 Linux 上咬得住，"无条件说明文"只在 Windows 上咬得住 —— **合起来才是完整的不变量**）；
`python temp/visual_launch_mutation_check.py` ⇒ 视觉拉起那 5 个突变（含**条 E** 的剪贴板读回）全部 `KILLED`。

---
*示例Harness-E · 2026-10-14 起稿，**2026-10-15 增补条 C（容器真机走查）与条 D（真 Linux 复现的密钥落盘假话）**·
依据：老板三层方案 + `env_spec` / `platform_address` / `check_portability` 实现*
