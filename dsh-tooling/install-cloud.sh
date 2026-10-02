#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# DSH 功能移植包 —— 云端 Linux 一键安装
#
# 用法（在项目根目录下）：
#     cd <项目目录>
#     bash dsh-tooling/install-cloud.sh [profile名]
#
# 不带 profile 名就直接跑，它会列出可选的 profile。
#
# 做四件事：
#   1. 装技能（ac-project-dev / dsh-self-upgrade），并把路径占位符换成实际路径
#   2. 往 profile 里补一行：启用 skill-filesystem（不补则技能全线加载不了）
#   3. 装插件（安全网 / 技能中心 / 上下文洞察 / 架构图 / 官方定时提醒）
#   4. 装「合作社开发模式」agent preset
# ═══════════════════════════════════════════════════════════════════
set -uo pipefail

TOOLING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$TOOLING_DIR/.." && pwd)"
DSH_HOME="${DSH_HOME:-$HOME/.dsh}"
PROFILE="${1:-}"
PLUGIN_SOURCE_DIR="$DSH_HOME/.local-presets/ac-dev-cloud"

say()  { printf '\n==> %s\n' "$*"; }
ok()   { printf '  [OK] %s\n' "$*"; }
warn() { printf '  [!] %s\n' "$*"; }
die()  { printf '\n[FAIL] %s\n' "$*" >&2; exit 1; }

# 把占位符换成实际路径（用 | 作分隔符，避免路径里的 / 冲突；转义 &）
subst() {
  local f="$1"
  local esc="${PROJECT_DIR//&/\\&}"
  sed -i.tmpbak "s|__PROJECT_DIR__|${esc}|g" "$f" && rm -f "$f.tmpbak"
}

printf '════════════════════════════════════════════════════\n'
printf ' DSH 功能移植包 · 云端安装\n'
printf '════════════════════════════════════════════════════\n'
printf '  工具包目录 : %s\n' "$TOOLING_DIR"
printf '  项目目录   : %s\n' "$PROJECT_DIR"
printf '  DSH_HOME   : %s\n' "$DSH_HOME"

# ── 0. 定位 DSH ────────────────────────────────────────────────────
say "0/5 定位 DSH"
[ -d "$DSH_HOME" ] || die "$DSH_HOME 不存在 —— 这台机器上装了 DSH 吗？先确认 DSH_HOME。"

if [ -z "$PROFILE" ]; then
  if [ -d "$DSH_HOME/profiles" ]; then
    printf '  可用 profile（按最近修改排序，最上面那个通常是正在跑的）:\n'
    ls -lt "$DSH_HOME/profiles" | tail -n +2 | awk '{printf "    %s\n", $NF}'
  fi
  printf '\n  重新执行:  bash %s <profile名>\n' "${BASH_SOURCE[0]}"
  exit 1
fi

PATCH="$DSH_HOME/profiles/$PROFILE/cordis.patch.yml"
[ -f "$PATCH" ] || die "找不到 $PATCH —— profile 名对不对？"
ok "profile = $PROFILE"

# ── 1. 装技能 ──────────────────────────────────────────────────────
say "1/5 安装技能"
mkdir -p "$DSH_HOME/skills"
for s in ac-project-dev dsh-self-upgrade; do
  src="$TOOLING_DIR/skills/$s"
  dst="$DSH_HOME/skills/$s"
  if [ -d "$src" ]; then
    rm -rf "$dst"
    cp -r "$src" "$dst"
    find "$dst" -type f -name '*.md' -exec bash -c 'f="$1"; sed -i.tmpbak "s|__PROJECT_DIR__|$2|g" "$f" && rm -f "$f.tmpbak"' _ {} "$PROJECT_DIR" \;
    ok "$s  ($(find "$dst" -type f | wc -l) 个文件，路径已替换)"
  else
    warn "$s 不在工具包里，跳过"
  fi
done
printf '  技能目录是热扫描的，放进去即生效，不用重启。\n'

# ── 2. 补 patch：启用 skill-filesystem ────────────────────────────────
say "2/5 写入 profile 补丁（关键一步）"
if grep -qE '^[[:space:]]*-[[:space:]]*id:[[:space:]]*skill-filesystem[[:space:]]*$' "$PATCH"; then
  ok "skill-filesystem 条目已存在，跳过（请人工确认它的 disabled 不是 true）"
else
  cp "$PATCH" "$PATCH.bak-$(date +%Y%m%d-%H%M%S)"
  cat >> "$PATCH" <<'YAML'

# ── 由 dsh-tooling/install-cloud.sh 添加（云端移植）───────────────
# 必须启用：内置 cordis 预设把 skill-filesystem 的 customSkillDirs 指向
# 安装目录（app.asar / 包内）里的一个路径，该路径在真实文件系统上不存在，
# 会导致【整个技能提供方失效 —— 连用户目录里的技能都加载不了】。
# 症状：skill <任意名字> 一律返回 "unknown or no longer available"。
- id: skill-filesystem
  disabled: false
YAML
  ok "已追加（原文件已备份为 .bak-<时间戳>）"
fi

# ── 3. 装置信插件 ──────────────────────────────────────────────────
say "3/5 安装插件"
if ! command -v dsh >/dev/null 2>&1; then
  warn "PATH 上没有 dsh 命令，跳过插件安装"
  printf '     手动装法：\n'
  for p in dsh-undo-savepoint @linxin666/dsh-client-ui-skill-explorer dsh-context @tt-a1i/archify-dsh @deepseek-ai/dsh-experimental-schedule-bundle; do
    printf '       dsh plugin --profile %s add %s\n' "$PROFILE" "$p"
  done
else
  add() {
    printf '  -> %s\n' "$1"
    if dsh plugin --profile "$PROFILE" add "$1" >/dev/null 2>&1; then
      ok "$1"
    else
      warn "$1 安装失败（可能已装 / peer 不兼容 / 网络不通），请单独重试看报错"
    fi
  }
  add dsh-undo-savepoint                                   # 安全网：配置可回退
  add @linxin666/dsh-client-ui-skill-explorer               # 技能中心（纯 UI）
  add dsh-context                                           # 上下文洞察
  add @tt-a1i/archify-dsh                                   # 架构图
  add @deepseek-ai/dsh-experimental-schedule-bundle          # 官方：定时提醒 + 时间上下文
fi

# ── 4. 装工作模式 ──────────────────────────────────────────────────
say "4/5 安装工作模式（合作社开发模式）"
rm -rf "$PLUGIN_SOURCE_DIR"
mkdir -p "$(dirname "$PLUGIN_SOURCE_DIR")"
cp -r "$TOOLING_DIR/presets/ac-dev-cloud" "$PLUGIN_SOURCE_DIR"
subst "$PLUGIN_SOURCE_DIR/cordis.patch.yml"
if grep -q '__PROJECT_DIR__' "$PLUGIN_SOURCE_DIR/cordis.patch.yml"; then
  warn "占位符替换似乎没成功，请手工检查 $PLUGIN_SOURCE_DIR/cordis.patch.yml"
else
  ok "路径已写入人设：$PROJECT_DIR"
fi

if command -v dsh >/dev/null 2>&1; then
  printf '  -> %s\n' "$PLUGIN_SOURCE_DIR"
  if dsh plugin --profile "$PROFILE" add "$PLUGIN_SOURCE_DIR" >/dev/null 2>&1; then
    ok "合作社开发模式 已安装"
  else
    warn "安装失败，请单独重试看报错：dsh plugin --profile $PROFILE add \"$PLUGIN_SOURCE_DIR\""
  fi
else
  warn "没有 dsh 命令，跳过。手动：dsh plugin --profile $PROFILE add \"$PLUGIN_SOURCE_DIR\""
fi

# ── 5. 收尾 ────────────────────────────────────────────────────────
say "5/5 完成"
cat <<EOF

  下一步（必做）：

    1) 重启 DSH —— 插件与 agent preset 不会热加载

    2) 开一个新会话，验证三件事：
         · 让 agent 执行 skill ac-project-dev  → 应能加载出项目手册
         · 模式选择器里应出现「合作社开发模式」
         · 让 agent 调 plugin_manager list_plugins
           → 各插件 fiberPhase 必须是 active（enabled 但非 active 等于没生效）

    3) 如果第 2 步里「技能加载不了」：
         说明 skill-filesystem 那行没生效，检查 $PATCH
         里的 `- id: skill-filesystem / disabled: false`

  项目位置（已写入人设与手册）: $PROJECT_DIR
  启动服务: cd "$PROJECT_DIR" && python3 -m agent_community.platform.server --port 18920

  说明：本脚本在 Windows 侧做过静态检查，但未在云端实机跑过。
        出问题可按 dsh-tooling/README.md 分步手敲；profile 补丁有 .bak 可还原。
EOF
