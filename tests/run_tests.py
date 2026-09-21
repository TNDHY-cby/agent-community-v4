#!/usr/bin/env python
"""一键测试入口（不依赖任何预先启动的服务、不污染正式数据目录）。

用法：
    python tests/run_tests.py            # 运行全部测试
    python tests/run_tests.py smoke      # 只跑指定子集（按名称过滤）

兼容 pytest：
    pytest tests/ -v
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
TEST_FILES = [
    ("smoke", "test_smoke.py", "核心冒烟：模块 / 配置 / 路由 / 数据目录"),
    ("bridge-render", "test_bridge_render.py", "桥模板渲染"),
    ("workshop-http", "test_workshop_http.py", "工作间 HTTP 接线（临时服务 + 临时数据目录）"),
    ("e2e", "test_e2e.py", "端到端协作流程（临时服务 + 临时数据目录）"),
]


def run_one(name: str, filename: str, desc: str, keyword: str | None) -> bool:
    if keyword and keyword not in name:
        return True
    path = TESTS_DIR / filename
    print("\n" + "=" * 66)
    print(f"▶ {name} — {desc}")
    print("=" * 66, flush=True)
    t0 = time.time()
    proc = subprocess.run([sys.executable, str(path)], cwd=str(TESTS_DIR))
    cost = time.time() - t0
    ok = proc.returncode == 0
    print(f"{'✔ PASS' if ok else '✘ FAIL'}  {name}  ({cost:.1f}s)", flush=True)
    return ok


def main() -> int:
    keyword = sys.argv[1] if len(sys.argv) > 1 else None
    results = {}
    for name, filename, desc in TEST_FILES:
        if keyword and keyword not in name:
            continue
        results[name] = run_one(name, filename, desc, None)

    if not results:
        print(f"没有匹配的测试：{keyword}")
        return 1

    print("\n" + "=" * 66)
    print("测试汇总")
    print("=" * 66)
    for name, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    passed = sum(1 for v in results.values() if v)
    print(f"\n合计: {passed}/{len(results)} 通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
