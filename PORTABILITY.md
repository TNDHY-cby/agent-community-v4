# 可移植性：把"换台机器能跑"变成可执行的规则

> 本文是**可移植性的唯一规则源**。`AGENTS.md`、`CLAUDE.md`、`.cursorrules` 都指向它。
> 起因（老板 2026-10-14 原话）：「**本地网址不应该引入**」「**本地端口因人而异**」。
> 判据一句话：**当前电脑能跑不算合格；陌生环境只改配置就能跑才算合格。**

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

### 4.1 CI 定义在 `ci/ci.yml`（**启用方式**）

CI 工作流**没有**放在 `.github/workflows/`，原因很实际：
本仓库的推送令牌是 Personal Access Token，**没有 `workflow` 权限**，GitHub 会直接拒收：

```
! [remote rejected] master -> master
  (refusing to allow a Personal Access Token to create or update workflow
   `.github/workflows/ci.yml` without `workflow` scope)
```

一旦把工作流放进那个目录，**整次发布都推不上去**（= 别人拿不到任何更新）。
所以仓库里常备一份可审阅的定义 `ci/ci.yml`，启用方式二选一（都是一次性动作）：

1. **网页端**（最简单）：GitHub → `Add file` → `Create new file` →
   路径填 `.github/workflows/ci.yml` → 把 `ci/ci.yml` 内容粘进去 → 提交；
2. **给令牌加权限**：GitHub → Settings → Developer settings → Tokens →
   勾上 `workflow` → 更新凭据后，把 `ci/ci.yml` 复制到 `.github/workflows/ci.yml` 提交。

工作流跑的就是上面那三条自查，外加一条「只改 `AC_PORT` / `AC_DATA_DIR` 也能起」的真启动验证。

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

### 5.1 按"陌生人的方式"实跑验收，抓出的两个真缺陷（都已修，且都上了守卫）

这两条**不是读代码发现的**，是**真起服务看真结果**发现的 ——
所以下面的验收判据只能是"起真服务"，不许退化成读源码：

| # | 缺陷 | 症状（实测） | 修法 | 守卫 |
|---|---|---|---|---|
| A | `AC_HOST` 是**改不动的开关** | `AC_HOST` 已登记进 `env_spec` / `.env.example` / README，但 `server.py` 末尾直接 `uvicorn.run(host="0.0.0.0")` ⇒ 设 `AC_HOST=127.0.0.1` 仍监听 `0.0.0.0` | 走 `platform_address.resolve_host()`（`--host` > `AC_HOST` > 默认） | `test_host_resolution_priority`、`test_server_really_binds_the_configured_host`（真起进程，从非回环地址连不上才算过） |
| B | 生产模式缺必需项**只警告不失败** | `AC_ENV=production` 且缺 `AC_DATA_DIR` / 关停口令 / 令牌，一路打警告**照样起服务** ⇒ "生产缺必需项就失败"等于没做 | `_conditional_issues()` 里生产模式的两条升格为 `error`；回环部署只把令牌/口令当提示 | `test_production_missing_required_fails_startup`（真调 `assert_ready()`，不看文本） |

> **教训（写进档案）**：文档里承诺一个"改不动的开关"，比不写这个开关**更坏** ——
> 用户会照文档改，改完以为生效了，然后拿错误的现象来问"为什么没用"。
> 这类缺陷**只有把服务真起起来、看真实监听/真实退出码**才能发现。

守卫的突变检验（有人把实现退回错误写法时，守卫**必须变红**）：
`python temp/acceptance_mutation_check.py` ⇒ 突变 A / B-1 / B-2 全部 `KILLED`。

---
*示例Harness-E · 2026-10-14 · 依据：老板三层方案 + `env_spec` / `platform_address` / `check_portability` 实现*
