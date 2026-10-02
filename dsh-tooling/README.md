# dsh-tooling — 把 DSH 能力复刻到云端（Linux）

> 用途：让**华为云上的那台 DSH** 拥有与 Windows 开发机同样的技能、插件与工作模式。
> 同时，因为云端也要开发本项目，这里带了一份**云端专属**的项目手册与人设。
> 建立日期：2026-10-02

---

## 一条命令

在云端机器的**项目根目录**下：

```bash
cd <项目目录>
bash dsh-tooling/install-cloud.sh          # 先跑一次，它会列出可用的 profile
bash dsh-tooling/install-cloud.sh <profile>  # 再带上 profile 名跑
```

---

## 它做五件事

| # | 做什么 | 为什么 |
|---|---|---|
| 1 | 装技能 `ac-project-dev` / `dsh-self-upgrade`，并把 `__PROJECT_DIR__` 换成实际路径 | 技能是热扫描的，放进去即生效 |
| 2 | 往 profile 补丁追加 `- id: skill-filesystem / disabled: false` | **不补这行，技能一个都加载不了**（见下） |
| 3 | 装插件：安全网 / 技能中心 / 上下文洞察 / 架构图 / 官方定时提醒 | 见 `plugins.txt`（在 Windows 侧的包里） |
| 4 | 装「合作社开发模式」preset（路径已按云端替换） | 新建会话时可选 |
| 5 | 打印验证清单 | —— |

### 第 2 步为什么是硬需求

DSH 内置的 `cordis` 预设把 `skill-filesystem` 的 `customSkillDirs` 指向了
**安装目录内部**（打包版是 `app.asar` 里的路径）—— 那个路径在真实文件系统上不存在，
于是**整个技能提供方失效**，连放在 `~/.dsh/skills/` 里的技能都加载不了。

- 症状：`skill <任意名字>` 一律返回 `unknown or no longer available`
- 已在 Windows 打包版用「5 个扫描根各埋一个探针技能」的实验定位并验证修复

---

## 验证（必做，重启后开新会话）

1. **技能** —— 让 agent 执行 `skill ac-project-dev`，应返回项目手册
2. **模式** —— 新建会话，模式选择器里应有「合作社开发模式」
3. **插件** —— 让 agent 调 `plugin_manager list_plugins`，各插件 `fiberPhase` 必须是 `active`
   （`enabled: true` 但 `fiberPhase` 不是 `active` 的，等于没生效）

---

## 如果脚本失败，分步手敲

```bash
DSH_HOME="${DSH_HOME:-$HOME/.dsh}"
PROFILE=<你的profile>

# 1. 技能
mkdir -p "$DSH_HOME/skills"
cp -r dsh-tooling/skills/ac-project-dev    "$DSH_HOME/skills/"
cp -r dsh-tooling/skills/dsh-self-upgrade  "$DSH_HOME/skills/"
sed -i "s|__PROJECT_DIR__|$PWD|g" "$DSH_HOME/skills/ac-project-dev/SKILL.md"

# 2. profile 补丁（追加这两行）
cat >> "$DSH_HOME/profiles/$PROFILE/cordis.patch.yml" <<'YAML'
- id: skill-filesystem
  disabled: false
YAML

# 3. 插件
dsh plugin --profile "$PROFILE" add dsh-undo-savepoint
dsh plugin --profile "$PROFILE" add @linxin666/dsh-client-ui-skill-explorer
dsh plugin --profile "$PROFILE" add dsh-context
dsh plugin --profile "$PROFILE" add @tt-a1i/archify-dsh
dsh plugin --profile "$PROFILE" add @deepseek-ai/dsh-experimental-schedule-bundle

# 4. 工作模式（先替换占位符）
cp -r dsh-tooling/presets/ac-dev-cloud "$DSH_HOME/ac-dev-cloud"
sed -i "s|__PROJECT_DIR__|$PWD|g" "$DSH_HOME/ac-dev-cloud/cordis.patch.yml"
dsh plugin --profile "$PROFILE" add "$DSH_HOME/ac-dev-cloud"

# 5. 重启 DSH
```

---

## 与 Windows 开发机版的差异

| | Windows 开发机 | 云端 Linux |
|---|---|---|
| 副本结构 | 开发副本 + 发布副本（-oss）双副本 | **单个 git clone**（就是 `origin/master`） |
| 改动前 | 留 `.bak`（开发副本无 git） | `git commit`（有 git） |
| 密钥加密 | DPAPI（用户绑定） | 无 DPAPI，**降级明文并告警** → 用 `AC_AI_API_KEY` 环境变量 |
| agent-instructions | 项目根 `AGENTS.md` | 同左（仓库里已带） |

人设里已写明「这一份就是唯一的副本；不要去找发布副本」，避免云端 agent 按 Windows 手册乱找。

---

## 未验证的部分（如实说明）

- 本脚本**未在云端实机跑过**：没有通往那台机器的通路（只能从华为云控制台操作）
- Windows 侧做了：`bash -n` 语法检查（**0 错误**）、行尾确认（纯 LF）、占位符替换逻辑核对
- `.gitattributes` 已强制 `*.sh text eol=lf`，防止 Windows 的 autocrlf 把行尾改成 CRLF
  导致 Linux 上执行报错
- 云端 DSH 的**发行方式未知**：若是源码版，`dsh plugin` 的可用性需实测
