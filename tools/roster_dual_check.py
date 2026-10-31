# -*- coding: utf-8 -*-
"""体检：**两套名单**（旧 `Workshop.members` vs v5 名单）是否对得上（老板 2026-10-31 的担心）。

老板原话：「旧成员表和新的那份还并存、迁移没做完，这种地方最容易出事，两套名单对不上，
验收就会卡住」。所以给一条**随时能跑的判据**，而不是等出事。

规则（`V116 §0-11`）：**v5 名单是真源**，旧 `members` 只是投影。
- 旧里有、v5 没有 ⇒ **危险**（验收/派活按 v5 算，会"人明明加了却派不出去"）⇒ 退出码 1；
- v5 里有、旧没有 ⇒ 正常（投影没刷新，界面读 `members_v5` 就不受影响）⇒ 仅提示；
- 两边都有但状态不同 ⇒ 提示（以 v5 为准）。
"""
import json
import os
import sys
import urllib.request

BASE = "http://127.0.0.1:%s" % os.environ.get("AC_PORT", "18920")


def _get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return json.loads(r.read().decode())


def _post_members(wid: str, members: list) -> dict:
    """用原样名单调保存端点（harness 不变 ⇒ 后端不会触发新增激活）。"""
    body = json.dumps({"members": members}).encode("utf-8")
    req = urllib.request.Request(BASE + "/api/workshop/%s/members" % wid, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def main() -> int:
    fix = "--fix" in sys.argv
    try:
        ws_list = _get("/api/workshops")["workshops"]
    except Exception as e:                                   # noqa: BLE001 — 连不上要说清
        print("[FAIL] 连不上平台 %s：%s（先起服务）" % (BASE, e))
        return 2
    danger, notes, ok, fixable = [], [], 0, []
    for w in ws_list:
        wid = w["workshop_id"]
        try:
            detail = _get("/api/workshop/%s" % wid)
            live = _get("/api/workshop/%s/roster" % wid)
        except Exception as e:                               # noqa: BLE001
            notes.append("%s：读不到（%s）" % (wid, e))
            continue
        old_roles = [str(m.get("role") or "") for m in (detail.get("members") or [])]
        v5_slots = [str(m.get("slot_dir") or "") for m in (live.get("members") or [])]
        old_only = [r for r in old_roles if r and r not in v5_slots]
        v5_only = [s for s in v5_slots if s not in old_roles]
        if not old_roles and not v5_slots:
            ok += 1
            continue
        if old_only:
            danger.append("%s：旧名单有、v5 没有 → %s（**派活/验收按 v5 算，会派不出去**）"
                          % (wid, "、".join(old_only)))
            fixable.append((wid, [{"role": str(m.get("role") or ""),
                                   "display_name": str(m.get("display_name") or ""),
                                   "harness_ids": list(m.get("harness_ids") or [])}
                                  for m in (detail.get("members") or [])]))
        if v5_only:
            notes.append("%s：v5 有、旧投影没有 → %s（正常，界面请读 `members_v5`）"
                         % (wid, "、".join(v5_only)))
        if not old_only:
            ok += 1
    print("体检工作间：%d 个（对得上 %d）" % (len(ws_list), ok))
    for d in danger:
        print("  [危险] " + d)
    for n in notes:
        print("  [提示] " + n)
    if danger:
        if not fix:
            print("\n结论：**%d 个工作间两套名单对不上**（旧有 v5 无）。"
                  "跑 `python tools/roster_dual_check.py --fix` 同步进 v5（标 manual，不删任何东西）"
                  % len(danger))
            return 1
        print("\n开始同步（--fix）：")
        bad = 0
        for wid, members in fixable:
            try:
                out = _post_members(wid, members)
                sync = out.get("v5_sync") or {}
                print("  %s → %s" % (wid, "已同步" if sync.get("ok") else ("失败：%s" % sync.get("why"))))
                if not sync.get("ok"):
                    bad += 1
            except Exception as e:                           # noqa: BLE001
                print("  %s → 请求失败：%s" % (wid, e))
                bad += 1
        print("同步完成：成功 %d，失败 %d" % (len(fixable) - bad, bad))
        return 0 if bad == 0 else 1
    print("\n结论：两套名单**没有「旧有新无」的情况**（v5 仍是真源）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
