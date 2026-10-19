# -*- coding: utf-8 -*-
"""判据：**隔间继承（V53）端到端** —— 显式继承 → 只搬该搬的 → 逐项核对 → 写继承文 → 不接管。

```bash
python tools/inherit_chain_check.py     # 约 8 秒
```

## 为什么补这一条

V53 内核有单元守卫，但**没有端到端判据**把「隔间里真实躺着的那些文件（含邮箱/桥/草稿/大文件）
会不会被搬过去」这条串起来。它最容易假成功的形态：接口回 `success` 而**邮箱被搬走了**
（等于伪造"继任者已收到过任务"）、或**旧隔间的文件被改/被删**（违反"追加不接管"）。

## 十一格判据（真打 HTTP + 真读磁盘 sha256）

| 格 | 判据 |
|---|---|
| ⓪ | 隔离实例 + 建间（三名成员）+ 开工；只在**磁盘上**造出旧隔间的各类文件 |
| ① | 不指定来源 ⇒ **400**，且**不写继承文**（平台不替你猜从谁继承） |
| ② | 指名自己 / 指名不存在成员 ⇒ 400 / 404（都不静默成功） |
| ③ | 来源隔间不存在 ⇒ **409** 并说清"可能还没真正开工" |
| ④ | 真继承：`verified=true`，**该搬的搬了**（产物/笔记），`moved` 逐项列出 |
| ⑤ | **不该搬的都不搬**：`inbox/` `replies/` `temp/` `bridge.py` `connect_contract.json` `activation_prompt.txt` 全在 `skipped` 且**每条有原因** |
| ⑥ | **逐项核对**：目标文件 sha256 与源一致（平台不采信"我搬过了"） |
| ⑦ | **不覆盖继任者自己的东西**：目标已有同名不同内容 ⇒ 跳过且原因是"不覆盖" |
| ⑧ | **上限生效**：超过 `max_file_mb` 的大文件被跳过且原因写明上限 |
| ⑨ | **追加不接管**：旧隔间每个文件仍在、内容逐字节未变；`legacy.md` 写在**继任者**隔间根 |
| ⑩ | 幂等：再继承一次 ⇒ `moved` 为空、无 `failed`（重复执行不会把工作区搞乱） |
| ⑪ | 只读端点：`GET` 给出 `inherited_from` + 继承文全文，且**不改动** `legacy.md`（mtime 不变） |
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _clean_env(**over) -> dict:
    keep = ("PATH", "PYTHON", "SYSTEM", "WINDIR", "TEMP", "TMP", "HOME", "USER",
            "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT", "LANG", "LC_", "LD_LIBRARY")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AC_") and k.upper().startswith(keep)}
    env.update({k: str(v) for k, v in over.items()})
    return env


def req(base: str, path: str, body=None, timeout=40):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    r = urllib.request.Request(base + path, data=data,
                               method="POST" if body is not None else "GET",
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:                                    # noqa: BLE001
            return e.code, {"raw": raw[:400]}
    except Exception as e:                                   # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(e).__name__, str(e)[:120])}


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    data_dir = Path(tempfile.mkdtemp(prefix="ac_inh_"))
    env = _clean_env(AC_PORT=port, AC_DATA_DIR=data_dir, PYTHONPATH=str(ROOT),
                     PYTHONIOENCODING="utf-8", AC_PYTHON=str(sys.executable))
    proc = subprocess.Popen([sys.executable, "-m", "agent_community.platform.server"],
                            cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fails = 0

    def cell(name: str, ok: bool, detail: str = "") -> None:
        nonlocal fails
        print("   %-48s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        fails += 0 if ok else 1

    print("=" * 90)
    print("隔间继承（V53）端到端判据（实例 %s，数据目录 %s）" % (base, data_dir))
    print("=" * 90)
    try:
        code = 0
        for _ in range(120):
            code, _b = req(base, "/api/status", timeout=3)
            if code == 200:
                break
            time.sleep(0.5)
        if code != 200:
            print("★实例没起来")
            return 2
        st, ws = req(base, "/api/workshop", {
            "name": "继承走查", "hall_content": "老员工离职，把成果交给继任者",
            "members": [{"role": "码农", "display_name": "老员工", "harness_ids": []},
                        {"role": "码农", "display_name": "继任者", "harness_ids": []},
                        {"role": "记录员", "display_name": "没有隔间的", "harness_ids": []}],
        })
        ws_id = str((ws or {}).get("workshop_id") or "")
        if st != 200 or not ws_id:
            print("★建间失败：%s" % str(ws)[:300])
            return 1
        st_ws, wbody = req(base, "/api/workshop/%s" % ws_id)
        ws_dir = Path(str((wbody or {}).get("workspace_dir") or ""))
        if not str(ws_dir) or not ws_dir.is_dir():
            print("★拿不到 workspace_dir：%r" % str(ws_dir))
            return 1

        src = ws_dir / "码农_m0"
        dst = ws_dir / "码农_m1"
        # ① 造旧隔间（真实形态：产物 + 笔记 + 邮箱 + 桥/身份产物 + 草稿 + 大文件）
        (src / "output").mkdir(parents=True, exist_ok=True)
        (src / "inbox").mkdir(parents=True, exist_ok=True)
        (src / "replies").mkdir(parents=True, exist_ok=True)
        (src / "temp").mkdir(parents=True, exist_ok=True)
        (src / "output" / "报告.md").write_text("# 旧员工的成果\n排序模块已自测通过\n", encoding="utf-8")
        (src / "output" / "数据.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        (src / "notes.md").write_text("交接笔记：坑在鉴权\n", encoding="utf-8")
        (src / "inbox" / "task_1.json").write_text('{"task":"旧任务"}', encoding="utf-8")
        (src / "replies" / "r1.json").write_text('{"ok":true}', encoding="utf-8")
        (src / "temp" / "draft.txt").write_text("草稿", encoding="utf-8")
        (src / "bridge.py").write_text("# 旧桥\n", encoding="utf-8")
        (src / "connect_contract.json").write_text("{}", encoding="utf-8")
        (src / "activation_prompt.txt").write_text("旧提示词", encoding="utf-8")
        (src / "big.bin").write_bytes(b"0" * (1_500 * 1024))
        # 继任者自己的东西：同名不同内容（平台**不许**覆盖）
        (dst / "output").mkdir(parents=True, exist_ok=True)
        (dst / "output" / "报告.md").write_text("# 继任者自己写的版本（不许被覆盖）\n", encoding="utf-8")
        src_shas = {p.relative_to(src).as_posix(): sha(p) for p in src.rglob("*") if p.is_file()}
        cell("⓪ 隔离实例 + 三名成员 + 磁盘造出旧隔间", True,
             "ws=%s ｜ 源文件 %d 个" % (ws_id, len(src_shas)))

        # ① 不指定来源
        st0, b0 = req(base, "/api/workshop/%s/member/m1/inherit" % ws_id, {})
        no_legacy = not (dst / "legacy.md").exists()
        cell("① 不指定来源 ⇒ 400 且不写继承文",
             st0 == 400 and "from_member_id" in str((b0 or {}).get("reason") or "") and no_legacy,
             "HTTP %s ｜ 继承文未写=%s" % (st0, no_legacy))

        # ② 指名自己 / 不存在
        st_a, ba = req(base, "/api/workshop/%s/member/m1/inherit" % ws_id,
                       {"from_member_id": "m1"})
        st_b, bb = req(base, "/api/workshop/%s/member/m1/inherit" % ws_id,
                       {"from_member_id": "mX"})
        cell("② 指名自己 400 / 指名不存在 404",
             st_a == 400 and st_b == 404,
             "自己=%s（%s）｜ 不存在=%s" % (st_a, str((ba or {}).get("reason"))[:18], st_b))

        # ③ 来源隔间不存在（m2 从没开过工）
        st_c, bc = req(base, "/api/workshop/%s/member/m1/inherit" % ws_id,
                       {"from_member_id": "m2"})
        cell("③ 来源隔间不存在 ⇒ 409 说清",
             st_c == 409 and "还没真正开工" in str((bc or {}).get("reason") or ""),
             "HTTP %s %s" % (st_c, str((bc or {}).get("reason"))[:40]))

        # ④ 真继承（max_file_mb=1 ⇒ big.bin 超限）
        st1, r1 = req(base, "/api/workshop/%s/member/m1/inherit" % ws_id,
                      {"from_member_id": "m0", "max_file_mb": 1})
        moved = [m.get("path") for m in ((r1 or {}).get("moved") or [])]
        skipped = {s.get("path"): s.get("reason") for s in ((r1 or {}).get("skipped") or [])}
        cell("④ 真继承：verified 且产物搬到了",
             st1 == 200 and (r1 or {}).get("verified") is True
             and "output/数据.csv" in moved and "notes.md" in moved,
             "verified=%s moved=%s" % ((r1 or {}).get("verified"), moved))

        # ⑤ 不该搬的都没搬，且每条有原因
        must_skip = ["inbox/task_1.json", "replies/r1.json", "temp/draft.txt", "bridge.py",
                     "connect_contract.json", "activation_prompt.txt", "big.bin"]
        missing = [p for p in must_skip if p not in skipped]
        no_reason = [p for p in must_skip if p in skipped and not str(skipped.get(p) or "").strip()]
        cell("⑤ 邮箱/桥/草稿/大文件都没搬且都给原因",
             not missing and not no_reason,
             "漏列=%s ｜ 无原因=%s" % (missing or "无", no_reason or "无"))

        # ⑥ 逐项核对：sha256 一致
        checks = []
        for rel in ("output/数据.csv", "notes.md"):
            d = dst / rel
            checks.append(d.is_file() and sha(d) == src_shas[rel])
        cell("⑥ 目标文件 sha256 与源一致（逐项核对）", all(checks),
             "核对 %d 项，通过 %d" % (len(checks), sum(checks)))

        # ⑦ 不覆盖继任者自己的同名文件
        own = (dst / "output" / "报告.md").read_text(encoding="utf-8")
        cell("⑦ 同名不同内容 ⇒ 不覆盖继任者的",
             "继任者自己写的版本" in own and "不覆盖" in str(skipped.get("output/报告.md") or ""),
             "原因=%s" % str(skipped.get("output/报告.md"))[:36])

        # ⑧ 上限生效（原因写明上限）
        cell("⑧ 超 max_file_mb 的大文件被跳过并写明上限",
             "单文件上限" in str(skipped.get("big.bin") or ""),
             str(skipped.get("big.bin"))[:44])

        # ⑨ 追加不接管：源隔间逐字节未变 + 继承文在继任者隔间
        changed = [rel for rel, h in src_shas.items()
                   if not (src / rel).is_file() or sha(src / rel) != h]
        legacy = dst / "legacy.md"
        ltext = legacy.read_text(encoding="utf-8", errors="replace") if legacy.is_file() else ""
        cell("⑨ 旧隔间一字未动 + 继承文写在继任者隔间",
             not changed and legacy.is_file() and "来源成员" in ltext
             and "已搬运" in ltext and "未搬运" in ltext,
             "源文件变化=%s ｜ legacy.md=%s" % (changed or "无",
                                                "有" if legacy.is_file() else "缺"))

        # ⑩ 幂等：再继承一次 ⇒ moved 为空、无失败
        st2, r2 = req(base, "/api/workshop/%s/member/m1/inherit" % ws_id,
                      {"from_member_id": "m0", "max_file_mb": 1})
        cell("⑩ 重复继承幂等（moved 空、无失败）",
             st2 == 200 and not ((r2 or {}).get("moved") or [])
             and not ((r2 or {}).get("failed") or []),
             "moved=%d failed=%d" % (len((r2 or {}).get("moved") or []),
                                     len((r2 or {}).get("failed") or [])))

        # ⑪ 只读端点：给出来源与全文，且不改盘
        mtime_before = legacy.stat().st_mtime_ns
        st3, r3 = req(base, "/api/workshop/%s/member/m1/inherit" % ws_id)
        time.sleep(0.05)
        mtime_after = legacy.stat().st_mtime_ns
        cell("⑪ 只读查询给出 source+全文且不改盘",
             st3 == 200 and (r3 or {}).get("inherited_from") == "m0"
             and "继承文" in str((r3 or {}).get("legacy_text") or "")
             and mtime_before == mtime_after,
             "inherited_from=%s ｜ legacy 未改=%s" % ((r3 or {}).get("inherited_from"),
                                                     mtime_before == mtime_after))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:                                    # noqa: BLE001
            proc.kill()
        import shutil
        shutil.rmtree(data_dir, ignore_errors=True)

    print("-" * 90)
    print("结论：%s" % ("**隔间继承端到端全绿（只搬产物 / 邮箱桥不搬 / 逐项核对 / 不覆盖 / 不接管 / 幂等）**"
                      if not fails else "**有 %d 格失败**" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
