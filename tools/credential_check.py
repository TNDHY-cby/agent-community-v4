# -*- coding: utf-8 -*-
"""凭据体检 CLI（V86）—— 与 `POST /api/credentials/scan` **同一份代码**。

用法（在项目根目录）：
    python tools/credential_check.py            # 体检并打印人话清单
    python tools/credential_check.py --json     # 机器可读（仍不含明文）
    python tools/credential_check.py --quiet    # 只给结论行（exit 0=没有弱值/明文，1=有问题）

**绝不打印明文**：只给位置、指纹（sha256 前 12 位）、强度与原因。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_community.platform import credentials as cred  # noqa: E402


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                        # noqa: BLE001 — 有意静默：老终端不支持就算了，不该挡住体检
        pass
    ap = argparse.ArgumentParser(description="凭据体检（只看问题，不打印明文）")
    ap.add_argument("--json", action="store_true", help="输出 JSON（仍不含明文）")
    ap.add_argument("--quiet", action="store_true", help="只输出结论行")
    ap.add_argument("--no-record", action="store_true", help="不更新账本（只体检）")
    a = ap.parse_args(argv)

    out = cred.scan() if a.no_record else cred.scan_and_record()
    found, skipped = out.get("found") or [], out.get("skipped") or []
    weak = [f for f in found if f.get("strength") == "weak"]
    bridge = [f for f in found if f.get("kind") == "bridge"]

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif not a.quiet:
        print("==" * 36)
        print("凭据体检（只显示位置与指纹，**不显示明文**）")
        print("==" * 36)
        if not found:
            print("没找到任何凭据 —— 要么你还没填，要么填在了平台不认的位置。")
        for f in found:
            mark = "弱 " if f.get("strength") == "weak" else "OK "
            print("[%s] %-28s %s" % (mark, f.get("purpose", "?"), f.get("source", "?")))
            print("     指纹 %s%s" % (f.get("fingerprint", ""),
                                    ("  ← " + f["why"]) if f.get("why") else ""))
        if skipped:
            print("\n没扫成的（已如实列出，不是「没问题」）：")
            for s in skipped:
                print("  - %s —— %s" % (s.get("source", "?"), s.get("why", "")))

    bad = len(weak) + len(bridge)
    print("结论：共 %d 处凭据，弱值 %d 处，桥产物明文 %d 处，未扫成 %d 处 %s"
          % (len(found), len(weak), len(bridge), len(skipped),
             "⇒ 建议按上面逐条改" if bad else "⇒ 没发现问题"))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
