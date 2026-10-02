---
name: dsh-self-upgrade
description: 升级 DeepSeek Harness 自身时加载——新建/修改 agent preset（工作模式）、拉取与排查 skill、安装插件、看清实际运行的是哪个 profile、以及改坏了怎么回退。当任务是"升级自己""装插件""加模式""skill 不生效""插件装了没反应"时使用。
---

# 升级 DSH 自身

> 全部结论来自 2026-10-02 的一次实测（本机 dsh 0.2.0-rc.2 / Electron 版）。
> 版本升级后请复验，尤其是 app.asar 打包形式下的路径问题。

## 0. 第一原则：确认哪个 profile 在跑

**"装了"≠"生效"。** 本机有多个 profile，插件只对**正在运行的那个**生效。

```powershell
# 判断办法 1：看 profile 目录的最近修改时间 —— 只有活着的那个会被写
Get-ChildItem "C:\Users\1\.dsh\profiles" -Directory | ForEach-Object {
  "{0,-12} {1}" -f $_.Name, (Get-Item $_.FullName).LastWriteTime }
# → 改一次配置后，时间变的就是当前 profile

# 判断办法 2：profile_manager 的写入落在哪
# 任何 set_plugin / install_bundle 之后，看哪个 cordis.patch.yml 的 mtime 变了

# 判断办法 3（最权威）：看后端插件的 fiberPhase
# cordis_inspect_query / plugin_manager list_plugins → fiberPhase: "active" 才算真的活了
```

**本机有两套并行安装，各配一个 profile**（这是最容易搞错的地方）：

| profile | 属于 | bundles | 说明 |
|---|---|---|---|
| `desktop` | **打包版 DSH**（`D:\DSH\` 的 Electron 应用） | `dsh-base` + `dsh-web-app` + `experimental-agent-team-profile` | **当前正在运行的就是它** |
| `web` | **源码版 DSH**（checkout 在 `D:\Programs\deepseek-harness`，git 仓库，`@deepseek-ai/dsh-root`） | 14 个第三方 bundle（`dsh-tier-router` / `dshmarket` / `dsh-mnemon` / `@nanmicoder/dsh-agent-teams` / `@liustack/modlens` / `@ychris12138/dsh-usage-stats` / `dsh-github-mcp` / `@anionex/dsh-turn-rewind` …） | 计划任务 `dsh_web_start` 负责起它：`cmd /c cd /d D:\Programs\deepseek-harness && pnpm dsh --profile web`。**该任务 2026-08-16 起就没成功过**，所以这些插件一直没在跑 |

**教训**：`profiles\web` 不是垃圾，是源码版的 profile——**别删**。
但"`dsh-tier-router` 已装"这类结论对**打包版**是错的：插件只对正在运行的那个 profile 生效。

> `D:\Programs\deepseek-harness` 才是**真正的 DSH 源码 checkout**。
> 系统提示里给的 `D:\DSH\resources\app.asar\dsh\` **不存在**（asar 是压缩包，只有 `app.asar.unpacked\dsh` 是真实目录且只含 `node_modules`）。
> 要改 DSH 自身（客户端插件 HMR 需要 `pnpm run dev:web`）就去 `D:\Programs\deepseek-harness`。

### 怎么知道我当前跑在哪个 agent preset（模式）上

对着 `list_plugins` / 自己的工具面反查内置预设声明（`app.asar` 内
`@deepseek-ai/dsh-web-app/presets/*.patch.yml`）：

| 判据 | 说明 |
|---|---|
| 有 `plugin_manager` 工具 | `tool-plugin-manager` 受 `!!js "!ctx.get('profileContext')"` 守卫 → 只在 `cordis` 预设里开 |
| 有 `cordis_inspect_*` | `tool-cordis` 只在 `cordis` 预设里 |
| 有 `run_code` 单工具 | 在 `ptc` 预设 |
| 只有 `bash` + `str_replace_editor` | 在 `minimal` 预设 |
| 完整工具面、无 `plugin-manager` | 在 `standard` 预设（默认） |

## 1. 工作模式 = agent preset

### 声明格式（现代做法，**不是** `~/.dsh/.agent-presets/`）

⚠️ `~/.dsh/.agent-presets/<id>/`（`preset.yml` + `agent.cordis.yml`）是**旧机制，已无人读取**。
本机那个 `liangshen`（梁神模式）就是死配置，不会出现在选择器里。

现在要写一个 **bundle**：

```
<workspace>\<name>\
  package.json          { "name": "...", "private": true, "type": "module",
                          "dsh": { "bundle": { "patch": "./cordis.patch.yml" } } }
  cordis.patch.yml      - insert:
                          - id: preset-<id>
                            name: '@deepseek-ai/dsh-agent-preset'
                            config:
                              id: <id>            # 会话保存的 preset 标识（小写字母数字连字符）
                              name: 显示名
                              description: 说明
                              order: 10           # 内置：standard=1 ptc=2 minimal=3 cordis=4
                              plugins: [...]      # 子插件行列表 = 这个模式的能力面
```

然后 `plugin_manager` → `install_bundle`，**target = bundle 的绝对路径**（本地目录会自动 `link:`）。

### 关键约束

- **`tool-plugin-manager` 要开的话必须带守卫** `disabled: !!js "!ctx.get('profileContext')"`，
  否则非 profile 场景（headless/sdk）会炸。
- **预设只影响之后创建的 Agent**。改完必须**开新会话**验证；老会话保持原 revision。
- **preset id 不可重复**；重复会让声明加载失败（但会带着诊断留在 roster 上，看得出）。
- **预设声明会被逐步激活并共享**：所有选了它的会话共用同一份配置，改一次全体生效。
- 预设里给模型看的**人设（persona）禁止放动态内容**（时间戳/计数器/随机 id）——
  它在每一步的前缀里，一变就毁 KV 前缀缓存。`{{cwd}}` / `{{model}}` 是会话级常量，安全。
- 需要**时间感**就用 `@deepseek-ai/dsh-time-context`：它注入的是**一条额外的 user 消息**
  （durable history），追加在缓存前缀之后，默认 `refreshIntervalMs: 600000`（10 分钟一条），
  **不毁前缀缓存**。

### 工具面怎么选

照抄内置 `standard` 或 `cordis` 的 `plugins` 列表再改，比从零写安全得多：
从 `app.asar` 里取——
```powershell
node <asar-scan.mjs> "D:\DSH\resources\app.asar" cat "dsh/node_modules/@deepseek-ai/dsh-web-app/presets/standard.patch.yml"
```
`_tools\asar-scan.mjs`（list / cat 两个子命令）能读 asar 里的任何文件。

## 2. skill 拉取与排查

### 扫描根（优先级 = rank 数字小的先命中）

| rank | 来源 | 路径 |
|---|---|---|
| 100 | project-dsh | `<项目根>/.dsh/skills` |
| 200 | project-agents | `<项目根>/.agents/skills` |
| 300 | custom | 预设里配的 `customSkillDirs` |
| 400 | user-dsh | `C:\Users\1\.dsh\skills` |
| 500 | user-agents | `C:\Users\1\.agents\skills` |
| 600 | bundled | 预设里配的 `bundledSkillDir` |

**"项目根" = 最近一个含 `.git` 的祖先目录；没有 `.git` 就用当前 cwd。**

### 技能格式

```
<root>/<name>/SKILL.md      # 目录 bundle 形式（推荐，可带 references/ examples/）
<root>/<name>.md            # 平铺形式
```
`SKILL.md` 必须有 YAML frontmatter 的 `name` + `description`。
`description` 写清**什么场景该用**，模型靠它决定要不要加载。
**`SKILL.md` 本体建议 < 8192 字符**（超过阈值会被工具结果修剪器裁），细节放 `references/`。

### ⚠️ 本机踩过的最大一个坑

内置 `cordis` 预设把 `skill-filesystem` 的 `customSkillDirs` 指向
`path.join(dirname(require.resolve('@deepseek-ai/dsh-agent-preset/package.json')), 'skills')`。
Electron 版里这个包在 **`app.asar` 内部**，该路径在真实文件系统上**不存在** →
**整个 skill 提供方失效，所有技能（含用户目录里的）全部加载不了。**

**症状**：`skill <任意名字>` 一律返回 `unknown or no longer available`，连已存在的技能也是。
**修法**（已验证）：启用宿主机平面那行，让默认根兜底——
```
plugin_manager set_plugin  target=include:skill-filesystem  enabled=true
```
**别**在预设里配指向安装目录（asar）内的 `customSkillDirs`/`bundledSkillDir`。

### 诊断手法

往**每个候选根**各放一个探针技能（`name: probe-root-N`），逐个调 `skill` 工具，
命中的 rank 就是生效的那些。一次实验就能定位，别猜。

## 3. 插件安装

### 来源

- **社区索引**：`https://awesome-dsh-plugin.com/plugins.json`（2026-10-01 快照 **4412 条**）
  每条带 `category` / `stars` / `downloads` / `capabilities` / `capabilityRedLines` / `install`。
  本地已留一份：`D:\DSH工作区1\dsh-modes\_registry\plugins.json`（+ `shortlist.txt` 分类短名单）。
- **官方可选 bundle**：`plugin_manager list_bundles` 里 `optional: true, enabled: false` 的那些
  （如 `@deepseek-ai/dsh-experimental-schedule-bundle`、`dsh-experimental-auto-review`、
  `dsh-experimental-voice-input-bundle`）。**零第三方供应链风险，优先用这个**。
- 装：`plugin_manager` → `install_bundle`，target = npm 包名 / GitHub spec / 本地目录绝对路径。

### 判断标准

1. `capabilityRedLines` 非空（尤其 `reads credentials/secrets AND has network access`）→ 慎重。
2. `capabilities` 里有 `shell` / `dynamic-code` / `host-runtime` → 它能在你机器上跑任意代码。
3. 先看它是否与**已有原生能力重叠**（见下表），重叠就别装。
4. 一次别装太多；**每装一个就查一次 `fiberPhase`**。

### 别重复造：DSH 原生已有的

| 想装的东西 | 原生等价物 |
|---|---|
| 上下文压缩 | `compaction-basic`（token-meter 驱动 + LLM 摘要） |
| 工具结果剪枝 | `compaction-tool-result-pruner`（head/middle/tail） |
| 可逆压缩 / 大输出按需取回 | `spill-policy` + `spill-local`（token 预算内保留 + 可恢复路径） |
| 图片超预算卸载 | `compaction-image-offload` |
| 用量计量 | `token-meter`（+ 会话投影） |
| 计划模式 | `plan-mode` |
| 目标跨轮续跑 | `goal` + `goal-round-driver` |
| 子代理 / 工作流 | `subagent`（spawn/fork）、`workflow-ptc` |
| 权限档 | `permission-presets`（只读 / 工作区写 / 完全访问） |

## 4. 安全网（动配置之前先做）

```powershell
# ① 手工备份 profile（零依赖，最可靠）
$bk = "D:\DSH工作区1\dsh-modes\_backup\profile-<name>-$(Get-Date -f yyyyMMdd-HHmmss)"
New-Item -ItemType Directory -Force -Path $bk | Out-Null
Copy-Item "C:\Users\1\.dsh\profiles\<name>\*" $bk -Force
```
② 装了 `dsh-undo-savepoint` 后，配置改动会**自动快照**；说一句「撤销上一步」即可回退，
   崩溃到起不来时用它的 `undo_safe_mode` / 离线 CLI。

### ⚠️ `undo_doctor` 的已知误报

它会报 `@deepseek-ai/dsh-base` / `dsh-web-app` / `dsh-experimental-agent-team-profile`
「no dsh.bundle.patch / cannot resolve」并声称 DSH 会中止启动。
**这些是内置 bundle，从安装目录（app.asar）解析，本就不在 profile 的 `node_modules` 里。**
只要 DSH 此刻能跑，就不是真问题。判断依据：这些 bundle 在你动手**之前**就在清单里。

## 5. 显示编码

**PowerShell 会把正常的 UTF-8 显示成乱码**——`package.json` 里的
`link:D:/DSH工作区1/dsh-modes/ac-dev` 曾显示成 `link:D:/DSH宸ヤ綔鍖?/dsh-modes/ac-dev`，
文件内容其实完全正确。**判定编码问题一律用 `read` 工具或 Python `decode('utf-8')`。**
