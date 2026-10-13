#!/usr/bin/env python3
"""外端Agent生产合作社（External Agent Community）— 一键启动（**薄包装**）。

用法：
    python start.py                     # 起服务（带桌面窗口，端口 9103）
    python start.py --no-gui            # 只要后端
    python start.py --port 18920        # 换端口
    python start.py --no-gui --port 18920

⚠️ 本文件曾是**老架构的一键启动脚本**（直接去找 `platform/server.py` 再 Popen 它）——
   那个目录在 V-9 拆包后就不存在了，所以 `python start.py` 会直接报「找不到 server.py」。
   现在它只是 **CLI 的薄包装**，真正逻辑只在 `agent_community/cli.py` 一处（规则 20：不留两份口径）。
   等价命令：`python -m agent_community.cli start [--port N] [--no-gui]`
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    try:
        # 走 CLI 的同一个命令函数（`standalone_mode=False` 让我们能把 click 的结果翻译成退出码）
        from agent_community.cli import start as cli_start
    except Exception as e:  # noqa: BLE001
        print("[ERROR] 无法导入 agent_community.cli —— 请先在仓库根目录执行：\n"
              "        python -m pip install -r requirements.txt\n"
              f"        原因：{e}", file=sys.stderr)
        return 1
    try:
        cli_start.main(args=sys.argv[1:], standalone_mode=False)
    except SystemExit as e:                      # click 的正常退出
        return int(e.code or 0)
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] 启动失败：{type(e).__name__}: {e}", file=sys.stderr)
        print("        提示：桌面窗口需要 pywebview（Windows 还需 WebView2）。"
              "起不来就加 --no-gui，后端与 CLI 不依赖它。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
