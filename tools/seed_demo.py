#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""播一份演示数据（clone 完马上有东西可看）：3 个 agent + 1 个工作间 + 流程 + 1 个待批节点。

用法（服务要先起来）：
    python tools/seed_demo.py                 # 真播
    python tools/seed_demo.py --dry-run       # 只说会做什么
    python tools/seed_demo.py --base-url http://127.0.0.1:18920

设计口径：
- 只走**公开 HTTP 接口**（不直接改数据文件），因此播出来的东西与会话/权限一致；
- 用**示例名**（示例Harness-A…），不写任何真实机器/真实 agent 名；
- 播完打印"下一步点哪儿"，并**如实**说明哪些是真数据、哪些只是演示。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEMO_AGENTS = [("示例Harness-A", "工程师"), ("示例Harness-B", "评审"), ("示例Harness-C", "测试")]
DEMO_WORKSHOP = "演示：本地记账工具"
DEMO_FLOW = [
    {"node_id": "demo-需求", "label": "需求确认", "kind": "task", "status": "planned"},
    {"node_id": "demo-实现", "label": "实现", "kind": "task", "status": "planned", "parent_id": "demo-需求"},
    {"node_id": "demo-评审", "label": "人工评审", "kind": "approval", "status": "planned", "parent_id": "demo-实现"},
]


def _post(base, path, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "{}")


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "{}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:18920")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    base = a.base_url.rstrip("/")

    steps = [
        ("登记 %s（%s）" % (n, r), "/api/harness/register",
         {"harness_name": n, "harness_type": "custom", "wakeup_method": "clipboard", "role": r})
        for n, r in DEMO_AGENTS
    ]
    steps.append(("建演示工作间", "/api/workshop/create",
                  {"hall_content": "做一个本地记账工具，先出需求再实现"}))
    steps.append(("导入演示流程", "/api/workshops/demo/workflow", {"nodes": DEMO_FLOW}))

    print("=" * 62)
    print("演示数据播种 %s" % ("（dry-run，不真做）" if a.dry_run else ""))
    print("=" * 62)
    for title, path, payload in steps:
        print("  · %-16s POST %s" % (title, path))
        if a.dry_run:
            continue
        try:
            out = _post(base, path, payload)
            ok = out.get("success", True)
            print("      -> %s %s" % ("OK" if ok else "拒", str(out)[:110]))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:160]
            print("      -> HTTP %s %s" % (e.code, body))
        except Exception as e:                                # noqa: BLE001
            print("      -> 失败：%s: %s" % (type(e).__name__, str(e)[:120]))
            print("     ⚠️ 服务没起来？先跑 python -m agent_community.cli start")
            return 1

    print("\n下一步：打开 %s/workshop.html → 选「%s」→ 顶部「画布」" % (base, DEMO_WORKSHOP))
    print("说明：这些是**演示数据**（示例名、clipboard 人工中转型），删掉不影响真实接入。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
