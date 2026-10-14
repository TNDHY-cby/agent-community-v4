# -*- coding: utf-8 -*-
"""tools/gen_env_docs：由**一处真相**生成环境变量文档（别手抄）。

生成两样（幂等，可反复跑）：
  1. `.env.example`（仓库根）
  2. `README.md` 里的「环境变量」表（夹在 `<!-- ENV_TABLE:BEGIN/END -->` 之间）

判据（CI 会跑 `--check`）：生成结果与仓库里的文件**一致** ⇒ 说明没人手改过、没漂移。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_community.platform import env_spec as es  # noqa: E402

ENV_EXAMPLE = ROOT / ".env.example"
README = ROOT / "README.md"
BEGIN = "<!-- ENV_TABLE:BEGIN（由 tools/gen_env_docs.py 生成，勿手改）-->"
END = "<!-- ENV_TABLE:END -->"

SECTION = f"""{BEGIN}
以下**全部都有默认值** —— 一个都不设也能起（AI 不配时走规则兜底）。
换端口 / 换数据目录 / 换服务商**都只改环境变量，不改代码**；本表由
`agent_community/platform/env_spec.py` 自动生成（`python tools/gen_env_docs.py`）。

{es.table_markdown()}

**用法**：`cp .env.example .env` 后按需修改（`.env` 已在 `.gitignore` 里，不会进仓库）。
**校验**：`python -c "from agent_community.platform import env_spec as e; print(e.check_requirements())"`
—— 缺条件必需项时它会说清「缺哪个、为什么、怎么修」；`AC_ENV=production` 下直接**启动失败**。
{END}"""


def render_readme(text: str) -> str:
    if BEGIN in text and END in text:
        head = text.split(BEGIN)[0]
        tail = text.split(END, 1)[1]
        return head + SECTION + tail
    # 首次：插在「## AI Provider 配置」之前（没有就插在「## 快速开始」之后）
    for anchor in ("## AI Provider 配置", "## CLI 命令", "## 快速开始"):
        if anchor in text:
            i = text.index(anchor)
            return text[:i] + "## 环境变量\n\n" + SECTION + "\n\n" + text[i:]
    return text + "\n\n## 环境变量\n\n" + SECTION + "\n"


def main() -> int:
    check = "--check" in sys.argv
    want_env = es.dotenv_example()
    want_readme = render_readme(README.read_text(encoding="utf-8"))

    problems = []
    if check:
        if not ENV_EXAMPLE.is_file() or ENV_EXAMPLE.read_text(encoding="utf-8") != want_env:
            problems.append(".env.example 与 env_spec 不一致（跑 python tools/gen_env_docs.py 重新生成）")
        if want_readme != README.read_text(encoding="utf-8"):
            problems.append("README 的环境变量表与 env_spec 不一致（同上）")
        if problems:
            print("[FAIL] 环境变量文档漂移：")
            for p in problems:
                print("  -", p)
            return 1
        print("[OK] 环境变量文档与 env_spec 一致")
        return 0

    ENV_EXAMPLE.write_text(want_env, encoding="utf-8")
    README.write_text(want_readme, encoding="utf-8")
    print(f"[OK] 已生成 {ENV_EXAMPLE.name}（{len(want_env.splitlines())} 行）"
          f" 与 README 环境变量表（{len(es.SPEC)} 个变量）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
