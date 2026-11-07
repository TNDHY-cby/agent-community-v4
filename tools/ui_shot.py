# -*- coding: utf-8 -*-
"""平台界面截图（一条命令，交付时附证据用）。

为什么不直接在命令行拼 playwright：
  · 本机 PATH 里的 node 版本与 playwright 不兼容（实测崩），要挑对 node；
  · chromium 在 playwright 缓存里，不在系统 Program Files（本机没有 Chrome/Edge）；
  · 两者都可能换位置 ⇒ 一律**探测 + 可被环境变量覆盖**，找不到就**说清怎么装**，不假装成功。

用法：
  python tools/ui_shot.py --url http://127.0.0.1:18920 --out temp/shots/hall.png
  python tools/ui_shot.py --url http://127.0.0.1:18920/docking.html --out temp/shots/dock.png --full
  python tools/ui_shot.py --check          # 只做环境自检（node / playwright / chromium 三件齐不齐）

环境变量（都可覆盖探测结果）：
  AC_NODE              node 可执行（默认：PATH 里的 node）
  AC_PLAYWRIGHT_PATH   含 playwright 的 node_modules 目录
  CHROME_PATH          chromium/chrome 可执行
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent


def _first(paths):
    for p in paths:
        if p and Path(p).exists():
            return str(p)
    return None


def find_node() -> str | None:
    env = os.environ.get("AC_NODE")
    if env and Path(env).exists():
        return env
    return shutil.which("node")


def node_major(node: str) -> int | None:
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
        return int(out.lstrip("v").split(".")[0])
    except Exception:  # noqa: BLE001 — 探不到版本不算致命，后续还有 playwright 加载这一步兜底
        return None


def _npm_cache_dirs() -> list[Path]:
    """npm 的 _npx 缓存可能被配置到任意盘（本机就在自定缓存盘）⇒ 问 npm 自己，不猜。"""
    dirs: list[Path] = []
    npm = shutil.which("npm")
    if npm:
        try:
            out = subprocess.run([npm, "config", "get", "cache"], capture_output=True, text=True, timeout=15)
            cache = out.stdout.strip()
            if cache and "Error" not in cache:
                dirs.append(Path(cache) / "_npx")
        except Exception:  # noqa: BLE001 — 问不到 npm 缓存不影响后面的常规位置探测
            pass
    return dirs


def find_playwright() -> str | None:
    env = os.environ.get("AC_PLAYWRIGHT_PATH")
    if env and (Path(env) / "playwright").is_dir():
        return env
    candidates = [REPO / "node_modules"]
    # 1) npm 自己报的缓存目录（最可靠：缓存可能被挪到别的盘）
    for root in _npm_cache_dirs():
        if root.is_dir():
            candidates += sorted(root.glob("*/node_modules"))
    # 2) 常规位置（不写死盘符与用户名）
    for base in filter(None, [os.environ.get("LOCALAPPDATA"), os.environ.get("APPDATA"),
                              str(Path.home() / "AppData" / "Local"), str(Path.home() / "AppData" / "Roaming")]):
        for root in (Path(base) / "npm-cache" / "_npx", Path(base) / "npm" / "_npx"):
            if root.is_dir():
                candidates += sorted(root.glob("*/node_modules"))
    for c in candidates:
        if (Path(c) / "playwright").is_dir():
            return str(c)
    # 3) 全局 npm 根（`npm i -g playwright` 的安装形态）
    if shutil.which("npm"):
        try:
            out = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=15)
            root = Path(out.stdout.strip())
            if (root / "playwright").is_dir():
                return str(root)
        except Exception:  # noqa: BLE001 — 全局根也探不到就到头了，上方 missing 会如实说怎么装
            pass
    return None


def find_chrome() -> str | None:
    env = os.environ.get("CHROME_PATH")
    if env and Path(env).exists():
        return env
    names = ["chrome.exe", "chrome", "msedge.exe", "msedge", "headless_shell.exe"]
    roots = []
    for base in filter(None, [os.environ.get("LOCALAPPDATA"), os.environ.get("PROGRAMFILES"),
                              os.environ.get("PROGRAMFILES(X86)"), str(Path.home() / "AppData" / "Local")]):
        roots += [Path(base) / "ms-playwright", Path(base) / "Google" / "Chrome" / "Application",
                  Path(base) / "Microsoft" / "Edge" / "Application"]
    for r in roots:
        if not r.is_dir():
            continue
        for n in names:
            hits = sorted(r.rglob(n))
            if hits:
                return str(hits[-1])
    return None


def diagnose(overrides: dict | None = None) -> dict:
    ov = overrides or {}
    node = ov.get("node") or find_node()
    info = {
        "node": node,
        "node_major": node_major(node) if node else None,
        "playwright": ov.get("playwright") or find_playwright(),
        "chrome": ov.get("chrome") or find_chrome(),
    }
    missing = []
    if not node:
        missing.append("node（装 Node.js 或设 AC_NODE）")
    if not info["playwright"]:
        missing.append("playwright（`npm i playwright`，或设 AC_PLAYWRIGHT_PATH / 传 --playwright 指到含 playwright 的 node_modules；"
                       "npx 缓存被挪到非默认盘时也要这样做）")
    if not info["chrome"]:
        missing.append("浏览器（装 Chrome/Edge，或 `npx playwright install chromium`，或设 CHROME_PATH / 传 --chrome）")
    info["ok"] = not missing
    info["missing"] = missing
    return info


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="平台界面截图（真跑真截，交付证据用）")
    ap.add_argument("--url", help="要截的地址，例如 http://127.0.0.1:18920")
    ap.add_argument("--out", help="输出 PNG 路径（默认 temp/shots/<时间>.png）")
    ap.add_argument("--size", default="1440x900", help="视口，默认 1440x900")
    ap.add_argument("--wait", type=int, default=2500, help="加载后等待毫秒，默认 2500")
    ap.add_argument("--full", action="store_true", help="整页截图（默认只截首屏）")
    ap.add_argument("--check", action="store_true", help="只做环境自检")
    ap.add_argument("--node", help="指定 node 可执行（覆盖探测）")
    ap.add_argument("--playwright", help="指定含 playwright 的 node_modules（覆盖探测）")
    ap.add_argument("--chrome", help="指定 chromium/chrome 可执行（覆盖探测）")
    args = ap.parse_args(argv)

    info = diagnose({"node": args.node, "playwright": args.playwright, "chrome": args.chrome})
    if args.check or not args.url:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        if not info["ok"]:
            print("\n[FAIL] 截图环境缺件：\n  - " + "\n  - ".join(info["missing"]), file=sys.stderr)
            return 1
        print("\n[OK] node / playwright / 浏览器 三件齐")
        return 0
    if not info["ok"]:
        print("[FAIL] 截图环境缺件：\n  - " + "\n  - ".join(info["missing"]), file=sys.stderr)
        return 1

    out = Path(args.out) if args.out else (REPO / "temp" / "shots" / "shot.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["NODE_PATH"] = info["playwright"]
    env["CHROME_PATH"] = info["chrome"]
    cmd = [info["node"], str(HERE / "ui_shot.js"), args.url, str(out),
           f"--wait={args.wait}", f"--size={args.size}"]
    if args.full:
        cmd.append("--full")
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=120)
    if res.returncode != 0:
        print(res.stdout.strip())
        print(res.stderr.strip(), file=sys.stderr)
        print(f"[FAIL] 截图失败（exit {res.returncode}）", file=sys.stderr)
        return res.returncode
    try:
        payload = json.loads(res.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001 — 输出不是 JSON 说明 node 侧异常，原文已在上面打印
        print(res.stdout)
        return 1
    payload["bytes"] = out.stat().st_size if out.exists() else 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\n✅ 截图：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
