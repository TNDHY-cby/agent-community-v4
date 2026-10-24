# -*- coding: utf-8 -*-
"""**给前端/走查用的示例员工**：在任意一个跑起来的实例上登记一位虚拟员工，用完可一键删掉。

为什么有它（2026-10-23 前端如实反馈）：
    前端要跑「送话面板」的真请求判据（`verify/fe30_check.js`），但**手上所有实例都没登记过员工** ——
    面板正确地显示"还没有登记任何员工"，于是 `#dkDeliver-target` 等不到，判据跑不成（不是代码坏）。
    与其让每个人自己手工点一遍接入向导，不如给一条**可复现、可清理**的命令。

它不是"开箱假数据"：
    · 只通过**公开接口**登记（`POST /api/harness/register`），走的是和真人一样的那条路；
    · 默认名字用虚拟名（`示例Harness-A`），不写死任何真实 harness；
    · `--delete` 走公开的注销接口（`DELETE /api/harness/{id}`）；
    · 默认 `wakeup_method=pending`（最不打扰的一种：平台只排队，不会去敲别人的窗口）。

用法：
    python tools/seed_example_harness.py                       # 在默认平台地址上登记示例员工
    python tools/seed_example_harness.py --base-url http://127.0.0.1:18946
    python tools/seed_example_harness.py --name 示例Harness-B --id demo-b
    python tools/seed_example_harness.py --delete              # 删掉（同 --id/--name）
    python tools/seed_example_harness.py --list                # 列出当前登记了谁
退出码：0=成功 / 1=失败（错误原样打印） / 2=平台连不上（并给怎么起）
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_NAME = "示例Harness-A"
DEFAULT_ID = "example-harness-a"


def _call(base: str, method: str, path: str, body: dict | None = None, timeout: float = 20.0):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:400]


def main() -> int:
    ap = argparse.ArgumentParser(description="登记/删除一位示例员工（走公开接口，可清理）")
    ap.add_argument("--base-url", default="", help="平台地址；默认取 AC_BASE_URL，再退回 127.0.0.1:AC_PORT")
    ap.add_argument("--id", default=DEFAULT_ID, help="harness_id（默认 %s）" % DEFAULT_ID)
    ap.add_argument("--name", default=DEFAULT_NAME, help="harness_name（默认 %s）" % DEFAULT_NAME)
    ap.add_argument("--wakeup", default="clipboard",
                help="唤醒方式（默认 clipboard：不需要外部依赖，也不会自己去敲窗口）；可选 http/file_poll/clipboard/acp/http_api/mcp")
    ap.add_argument("--delete", action="store_true", help="删掉这位员工（同 --id）")
    ap.add_argument("--list", action="store_true", help="列出当前登记了谁")
    args = ap.parse_args()

    base = args.base_url.strip().rstrip("/")
    if not base:
        import os
        base = (os.environ.get("AC_BASE_URL") or "").rstrip("/")
        if not base:
            port = os.environ.get("AC_PORT") or "18920"
            base = "http://127.0.0.1:%s" % port

    st, out = _call(base, "GET", "/api/status")
    if st != 200:
        print("连不上平台 %s（HTTP %s）。先起服务：python -m agent_community.platform.server" % (base, st))
        return 2
    ver = out.get("version") if isinstance(out, dict) else "?"
    print("平台：%s  版本：%s" % (base, ver))

    if args.list:
        st, out = _call(base, "GET", "/api/docking/overview")
        if st != 200:
            print("列不出来（HTTP %s）：%s" % (st, out))
            return 1
        rows = []
        if isinstance(out, dict):
            for k in ("harnesses", "employees", "members"):
                if isinstance(out.get(k), list):
                    rows = out[k]
                    break
        print("已登记 %d 位：" % len(rows))
        for r in rows[:20]:
            if isinstance(r, dict):
                print("  - %s / %s" % (r.get("harness_id") or r.get("id") or "?",
                                       r.get("harness_name") or r.get("name") or "?"))
        return 0

    if args.delete:
        st, out = _call(base, "DELETE", "/api/harness/%s" % args.id)
        ok = st in (200, 204)
        print(("已删除 %s" if ok else "删除失败（HTTP %s）：%s") % ((args.id,) if ok else (st, out)))
        return 0 if ok else 1

    body = {
        "harness_id": args.id,
        "harness_name": args.name,
        "wakeup_method": args.wakeup,
        "ai": {"model_name": "示例模型", "provider": "example"},
        "capabilities": ["示例能力"],
        "metadata": {"seeded_by": "tools/seed_example_harness.py",
                     "purpose": "给前端/走查用的可清理示例"},
    }
    st, out = _call(base, "POST", "/api/harness/register", body)
    if st == 200 and isinstance(out, dict) and (out.get("success") or out.get("ok")):
        print("已登记：%s（%s），唤醒方式=%s" % (args.name, args.id, args.wakeup))
        print("下一步（可选）：POST /api/docking/harness/%s/bind {\"hwnd\": <窗口句柄>} 把目标窗口绑给它；"
              "之后 POST /api/docking/harness/%s/deliver {\"text\": \"…\"} 就能真投。" % (args.id, args.id))
        print("用完删掉：python tools/seed_example_harness.py --id %s --delete" % args.id)
        return 0
    print("登记失败（HTTP %s）：%s" % (st, out))
    return 1


if __name__ == "__main__":
    sys.exit(main())
