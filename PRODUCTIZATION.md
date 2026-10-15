# 产品化与反私人定制（"这产品不是只给我一个人用的"）

> **唯一规则源**。老板原话（就是这个文件存在的理由）：
> 「**以后再这样搞私人定制我们就死了**」（2026-10-11）
> 「**完善开发框架，以后的开发也要注意产品不是私人订制的**（我说不清楚）」（2026-10-15）
>
> "说不清楚"的部分由本文件代他说清楚：**私人定制 = 产品预设了只有"我这台机器 / 我这个人 /
> 我这个场景"才成立的东西**。凡是这样的实现，换一台机器、换一个人、换一个 agent 名就会露馅 ——
> 而露馅通常在**用户第一次用**的时候。
>
> 相关：[`PORTABILITY.md`](./PORTABILITY.md)（环境差异：端口/路径/凭据）、
> [`AGENTS.md`](./AGENTS.md)（铁律与入口）、CI 定义 `.github/workflows/ci.yml`。

## 一、六种形态 → 各自的检查器（**能自动查的一律自动查**）

| # | 形态（人话） | 检查器 | 级别 |
|---|---|---|---|
| 1 | 写死我这台机器的坐标（端口 / IP / 绝对路径 / 用户名 / 凭据） | `python tools/check_portability.py` | **fail** |
| 2 | 代码/文档里出现真实 harness 名或私有产品名（用户会照抄） | `agent_community/tests/test_bridge_catalog.py::test_onboarding_assets_carry_no_private_names` | **fail** |
| 3 | **平台专属能力伪装成通用能力**（只有 Windows 能跑的实现，别的平台静默失效） | `python tools/check_productization.py` 规则 A | **fail** |
| 4 | **开箱默认里塞假数据**（看起来配好了其实必然失败；未渲染的模板占位） | `python tools/check_productization.py` 规则 B | **fail** |
| 5 | **只在我这台机器绿**（编码 / 换行 / 平台差异导致别人那儿红） | CI 双平台（`test-linux` + `test-windows`）+ `test_bridge_encoding_guard.py` 等 | **fail** |
| 6 | **用户第一次跑就撞到**（入口命令不可用、失败不说话、文档承诺了却没实现） | `python tools/first_run_check.py` + `tests/test_cli_contract.py` | **fail** |

**一次全跑**（发布前 / 大改后）：

```bash
python tools/check_portability.py        # 形态 1（fail=0 才合格）
python tools/check_productization.py     # 形态 3、4（fail=0 才合格）
python tools/first_run_check.py --src <发布副本>   # 形态 6（五步走查，见下）
python -m pytest -q                      # 形态 2、5 以及全部回归
```

> ⚠️ `first_run_check.py` **clone 已提交内容**：陌生人拿到的是**发布副本（oss）**，
> 而 dev 副本的 HEAD 常常是旧的 ⇒ 在 dev 里验收请务必 `--src <发布副本路径>`（或设 `AC_SRC`），
> 否则测的是旧提交，会给出**假红**（实测踩过：以为 `cli task` 坏了，其实是 clone 到了旧 CLI）。

## 二、形态 3/4 的具体判据（`check_productization.py` 在查什么）

- **规则 A｜平台专属命令**：`powershell` / `netstat` / `tasklist` / `taskkill` / `Get-CimInstance` …
  只允许出现在：① `platform/sysprobe.py`（跨平台取事实的**唯一入口**，psutil 优先）
  与 `platform/procprobe.py`（**同一件事的纯标准库层**，没有 psutil 时的回落实现，
  也是看门狗那一族唯一能用的那一份 —— 见 §五 已还欠账）；
  ② 有**平台分支**的函数里（`os.name` / `sys.platform` / `_IS_WINDOWS`，函数级 early-return 也算）；
  ③ 显式申请例外：该行写 `# productization: allow(<一句话理由>)`。
- **规则 B｜未渲染的模板占位**：产品代码的**字符串字面量**里出现 `{{名字}}`（AST 判，注释/docstring 不算）
  —— 用户会看到 `{{…}}`，像项目没做完（实测：桥监管表默认值就是一条假 harness + 未渲染占位，
  每次启动都打一行「桥脚本不存在」）。
- **报告项（不判死）**：平台专属 `skipif` 的**清单**。每一条都意味着「这块在别的平台没守」，
  它们应当随时间**减少**（本仓库当前仅剩 1 条，见欠账表）。

## 三、形态 6：判据是"陌生人第一次跑"

**判据原文（老板 2026-10-11 定，2026-10-15 复述）**：
> **换一个目录 + 换一个端口 + 换一个数据目录，不改一行代码就能走通。**

`tools/first_run_check.py` 把它做成可执行的五步：
clone → `cli start --no-gui`（**换端口 + 换数据目录**）→ `cli task` → `cli task-result` → `cli stop`，
外加两条：**仓库必须保持干净**（不许在仓库里长出 `agent_community/data/`）、
**错误不许是空原因**（`[Error: ]`）。

它**不测** `pip install`（那需要外网与独立解释器）—— 安装步骤由 CI 的两个作业真跑覆盖，
详见 [`docs/首次运行.md`](./docs/首次运行.md) 与 `README.md` 的《快速开始》。

## 四、每次任务完成的 Definition of Done（照抄进自检）

- [ ] 三条自查通过：`check_portability.py`（fail=0）、`gen_env_docs.py --check`、`pytest -q`
- [ ] `check_productization.py` 通过（fail=0；例外都写了理由）
- [ ] 用户可见的入口改了 ⇒ 跑过 `first_run_check.py`（或说明为什么本机跑不了）
- [ ] 平台/环境相关的改动 ⇒ 在**两个平台**上验过（CI 双作业 / WSL 真 Linux，见 `ac-project-dev` 手册）
- [ ] 改了对外表达（字段/话术/文档/模板/示例/验收脚本）⇒ **一次全改**（规则 20：一件事只有一处真相）
- [ ] 新增守卫**按真结果写**（真起进程 / 真打请求 / 喂合成数据），源码文本断言不算
- [ ] 遗留欠账**写进下面的表**（或代码里的 `# productization: allow(理由)`），不许悄悄留着

## 五、欠账表（**明确记录，不是"忘了"**）

| 欠账 | 位置 | 为什么先留着 | 怎么还 |
|---|---|---|---|
| 窗口激活、桥目录进回收站 | `platform/server.py` | **Windows 独有的系统概念**（Win32 前台窗口模型 / 系统回收站），别的平台没有等价物 | 已**如实返回不支持**（不假装成功）；保持 |

### 已还（**留记录，不留欠账**）

| 欠账 | 还法 | 守卫 |
|---|---|---|
| ~~看门狗的进程发现是 Windows 专属~~（2026-10-15 还清） | 把"零第三方依赖的本机事实探测"收成**一处真相** `platform/procprobe.py`：Windows 走本机命令、Linux 走 `/proc`、其它 POSIX 走 `ps`/`lsof`/`ss`；`tools/watchdog.py` 与 `deploy/watchdog_all.py` **按文件路径**加载它（保住"能单独跑"），`sysprobe` 没有 psutil 时**整层转向它**。设计稿 `V50` | `tests/test_procprobe.py`（真进程 / 真端口 / 真子进程 / **按生产调用方式真跑一次看门狗**；"纯标准库"用 `sys.stdlib_module_names` 真判）、`tests/test_sysprobe.py::TestStdlibTierWithoutPsutil`（把 psutil 打成 None 后仍须答对） |
| ~~容器（docker）路线未在真机验证~~（2026-10-15 还清） | **本机 Docker Desktop 起 daemon → `docker compose build` → 两阶段真走查**：阶段 A（配了 `AC4_TOKEN`）不带令牌 **401 有原因** / 带对令牌 **200 running**；阶段 B（完全不配）**403、文案就是「服务未配置访问 Token」**。容器内再跑一次 `procprobe` 自检（`backend=procfs`，证明 V50 新模块在**干净 Linux 镜像**里能跑）＋ 两条守卫文件 **21 条全绿**。脚本 `temp/verify_container.py`（两阶段都真起容器，不读源码下结论） | 这两档真结果就是 `test_procprobe.py` + `test_ac4_token_env.py` 的端到端证据 |
| ~~`AC4_TOKEN` 是"设了也没用"的令牌~~（2026-10-15，**走查期间抓出的新缺陷**） | 见 [`PORTABILITY.md`](./PORTABILITY.md) §5.1 **条 C**：README / `env_spec` / `docker-compose.yml` 三处承诺、实现只读 `--token` ⇒ 容器与 `cli start` 里设了令牌完全无效 | `tests/test_ac4_token_env.py`（9 条）+ `temp/ac4token_mutation_check.py` **2/2 KILLED** |
| ~~平台专属 `skipif` 1 条（DPAPI）~~（2026-10-15 还清，**当前 0 条**） | 那条 skip 背后其实藏着一个真缺陷：`save_config()` **无条件**说"已加密保存"，而非 Windows 是**明文回退** ⇒ 真 Linux 上「屏幕说加密、磁盘是原文」。见 [`PORTABILITY.md`](./PORTABILITY.md) §5.1 **条 D**。修法：按实际落盘形态分档说 + README 口径同步；并把那条 skip **撤掉**（往返两平台都验、差异如实断言） | `tests/test_security.py::test_save_config_message_matches_disk`、`test_secret_storage_roundtrip_on_every_platform` + `temp/dpapi_mutation_check.py`（两平台各跑）|

> ⚠️ **扫描器自己的一个坑（2026-10-15 一并修掉）**：`check_productization.py` 原先只扫
> `git ls-files`（**已跟踪**文件）⇒ **新写出来的文件对扫描器是隐形的**，它会照样打印
> 「OK：没有发现…」——**那句话在那一刻是假的**。现改为「已跟踪 + 未跟踪但未被 `.gitignore` 排除」，
> 发布面口径不变。

## 六、给未来的 agent / 协作者：开工话术

> 实现 X。**先列这件事的"对外表达"散落在哪几处**（字段 / 话术 / 文档 / 模板 / 示例 / 验收脚本），
> 然后一处一处对齐（规则 20）。
> **禁止针对我这台机器**：不许写死端口/路径/用户名/真实 harness 名/平台专属命令。
> 做完跑：`check_portability` → `check_productization` → `pytest` →（涉及入口时）`first_run_check`。
> 验收：**换目录 + 换端口 + 换数据目录、不改一行代码**能走通；做不到就**如实说没做到**。

---

*示例Harness-E · 2026-10-15 · 本文件是可执行判据的唯一来源；实现见 `tools/check_productization.py` 与 `tools/first_run_check.py`*
