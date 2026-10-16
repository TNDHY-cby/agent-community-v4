#!/usr/bin/env bash
# 在**这个仓库里**跑全量回归，并把计数打出来（Linux 侧判据的落点）。
#
# 为什么单独有这个脚本（2026-10-16/17 老板拍板）：
#   「GitHub CI 停更」⇒ 双平台判据改由**本机真跑**承担：Windows 侧 `pytest`，
#   Linux 侧就是本脚本（由 `tools/dual_platform_check.py` 在 WSL 里调用）。
#
# 用法（POSIX）：
#   bash tools/run_suite_in_repo.sh [python 解释器] [junit 输出路径]
# python 解释器解析顺序：命令行参数 > $AC_LINUX_PYTHON > python3 > python
# ⚠️ 不写死任何本机路径：本机适配请放 `.env`（见 tools/dual_platform_check.py 的说明）。
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PY="${1:-${AC_LINUX_PYTHON:-}}"
if [ -z "$PY" ]; then
  if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
fi
JUNIT="${2:-${TMPDIR:-/tmp}/ac_junit_linux.xml}"
# ★★2026-10-17：**必须把这个解释器所在的 bin 放进 PATH**。
#   实测（双平台工具第一次真跑）：不放的话，用例里 `spawn("python", …)` 的子进程找不到解释器
#   ⇒ `test_workshops_session` **4 条假失败**（本机 Windows 绿、Linux 红，看着像真 bug）。
export PATH="$(dirname "$(command -v "$PY" 2>/dev/null || echo "$PY")"):$PATH"
cd "$ROOT" || { echo "找不到仓库根：$ROOT"; exit 9; }

echo "PY      = $PY"
echo "ROOT    = $ROOT"
if ! "$PY" -c "import pytest, fastapi" >/dev/null 2>&1; then
  echo "★这个解释器缺依赖（pytest / fastapi）—— 如实报错，不假装跑过。"
  echo "  本机适配（放仓库根的 .env 里，或者直接设环境变量）：AC_LINUX_PYTHON=/path/to/venv/bin/python"
  echo "  新建 venv 的办法（示例）：python3 -m venv ~/acvenv && ~/acvenv/bin/pip install -r requirements.txt pytest"
  exit 3
fi

"$PY" -m pytest agent_community/tests tests -q -p no:cacheprovider --junit-xml="$JUNIT" 2>&1 | tail -5
rc=${PIPESTATUS[0]}
"$PY" - "$JUNIT" <<'PY'
import sys, xml.etree.ElementTree as ET
r = ET.parse(sys.argv[1]).getroot()
s = r if r.tag == 'testsuite' else r[0]
a = s.attrib
print('LINUX tests=%s failures=%s errors=%s skipped=%s time=%ss'
      % (a.get('tests'), a.get('failures'), a.get('errors'), a.get('skipped'), a.get('time')))
PY
# 仓库脏目录检查（同 2026-10-16 的事故：写死盘符的测试会在仓库根建出 `D:\...` 目录）
n=$(ls -d D:* 2>/dev/null | wc -l)
echo "仓库根 'D:*' 目录数（应为 0）= $n"
[ "$n" != "0" ] && ls -d D:* 2>/dev/null | head -5
exit "$rc"
