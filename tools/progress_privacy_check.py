#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**工作过程上报的三处补强**（`V131` §6.3 脱敏 / §5 按天保留 / §7 落盘失败要说人话）。

## 为什么要有它

`progress` 是**对方写的自由文本**落进我们的库：可能带真实路径、可能带密钥；
而"只按条数滚动"会让一天灌满就把历史挤光；落盘失败时原来会把裸异常冒成 500（用户看到 500 不知道为什么）。

## 判据（8 格，全部真跑函数/真文件）

| # | 格 | 断言 |
|---|---|---|
| ① | 盘符路径脱敏 | `D:\\some\\where\\x.txt` → `<path>`，库里**不含** `D:\\some` |
| ② | POSIX 深路径脱敏 | `/home/someone/secret/x` → `<path>` |
| ③ | 密钥串脱敏 | `sk-` + 一段长串 → `<hidden>` |
| ④ | **不误伤** | `ac://workshop/x/y`、`https://example.com/a/b`、中文、普通句子**原样保留** |
| ⑤ | 按天上限 | 造一条 40 天前的 ⇒ 被裁掉；造一条 1 天前的 ⇒ 留着 |
| ⑥ | 条数上限 | 灌 510 条 ⇒ 只留 500 |
| ⑦ | 落盘失败要说人话 | 让写盘抛异常 ⇒ `ok=false` + `code=PROGRESS_WRITE_FAILED` + `error` 里有人话与真实原因 |
| ⑧ | 返回值如实 | 脱敏发生时 `redacted=true`，`text` 是**实际存下来的**那份 |

退出码：0 = 全过；1 = 有格没过。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

ROWS: list = []


def check(label, ok, detail=""):
    ROWS.append((label, bool(ok), detail))
    print("  %s %s%s" % ("PASS" if ok else "FAIL", label,
                        ("　（" + detail + "）") if detail and not ok else ""))


def main() -> int:
    print("判据：工作过程上报的三处补强（脱敏 / 按天保留 / 失败要说）")
    print("=" * 74)

    tmp = tempfile.mkdtemp(prefix="ac_progress_")
    old_env = os.environ.get("AC_DATA_DIR")
    os.environ["AC_DATA_DIR"] = tmp
    try:
        from agent_community.platform import progress as PG
        from agent_community.redact import scrub_text

        f = Path(tmp) / "progress.jsonl"

        # ①②③④ 纯函数层（一处真相在 redact.scrub_text）
        s1 = scrub_text(r"产物在 D:\some\where\x.txt 里")
        check("① 盘符路径 → <path>", "<path>" in s1 and "D:\\some" not in s1, s1)
        s2 = scrub_text("库在 /home/someone/secret/x.db")
        check("② POSIX 深路径 → <path>", "<path>" in s2 and "/home/someone" not in s2, s2)
        # ⚠️ 别在这里写完整的 sk- 字面量：发布面的密钥扫描器会把它当成"真凭据"（实测踩过）
        _fake_key = "sk-" + "abcdef0123456789"
        s3 = scrub_text("key=%s 别外传" % _fake_key)
        check("③ 密钥串 → <hidden>", "<hidden>" in s3 and _fake_key not in s3, s3)
        keep = ["ac://workshop/ws-1/tasks/2", "https://example.com/a/b", "我改完了 a.py，产物已写入"]
        bad = [k for k in keep if scrub_text(k) != k]
        check("④ 不误伤（URI / URL / 中文 / 普通句子原样）", not bad, "被误改：%s" % bad)

        # ⑧ 返回值如实 + 库里真的洗过
        r = PG.add(r"我把文件放到 D:\build\out\x.zip 了", harness_id="h1", kind="step")
        raw = f.read_text(encoding="utf-8") if f.is_file() else ""
        check("⑧ 入库的是洗过的，且返回 redacted=true",
              r.get("ok") and r.get("redacted") is True
              and "<path>" in str(r.get("text")) and "D:\\build" not in raw,
              json.dumps(r, ensure_ascii=False)[:200])

        # ⑤ 按天保留（造一条 40 天前的 + 一条 1 天前的）
        now_ms = int(time.time() * 1000)
        old_item = {"id": "pg_old", "text": "很久以前", "kind": "step", "harness_id": "h1",
                    "task_id": "", "at_ms": now_ms - 40 * 86400 * 1000, "at": "2026-09-27T00:00:00"}
        fresh_item = {"id": "pg_new", "text": "昨天", "kind": "step", "harness_id": "h1",
                      "task_id": "", "at_ms": now_ms - 86400 * 1000, "at": "2026-11-05T00:00:00"}
        f.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in (old_item, fresh_item)) + "\n",
                     encoding="utf-8")
        PG.add("触发一次重写", harness_id="h1")
        after = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
        ids = {x.get("id") for x in after}
        check("⑤ 按天保留：40 天前的被裁、1 天前的留着",
              "pg_old" not in ids and "pg_new" in ids, "剩余 id=%s" % sorted(ids)[:6])

        # ⑥ 条数上限
        f.write_text("", encoding="utf-8")
        os.environ["AC_PROGRESS_KEEP_DAYS"] = "0"        # 关掉按天，单验条数
        for i in range(510):
            PG.add("第 %d 条" % i, harness_id="h2")
        n = len([x for x in f.read_text(encoding="utf-8").splitlines() if x.strip()])
        check("⑥ 条数上限：留 500 条", n == PG.MAX_ITEMS, "实际 %d" % n)

        # ⑦ 落盘失败要说人话
        def _boom(_items):
            raise OSError(28, "No space left on device")

        orig = PG._rewrite
        PG._rewrite = _boom
        try:
            bad_r = PG.add("这条应该写不进去", harness_id="h3")
        finally:
            PG._rewrite = orig
        check("⑦ 落盘失败：ok=false + code + 人话（带真实原因）",
              bad_r.get("ok") is False and bad_r.get("code") == "PROGRESS_WRITE_FAILED"
              and "磁盘满" in str(bad_r.get("error")) and "No space left" in str(bad_r.get("error")),
              json.dumps(bad_r, ensure_ascii=False)[:220])
    finally:
        os.environ.pop("AC_PROGRESS_KEEP_DAYS", None)
        if old_env is None:
            os.environ.pop("AC_DATA_DIR", None)
        else:
            os.environ["AC_DATA_DIR"] = old_env
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 74)
    passed = sum(1 for _l, ok, _d in ROWS if ok)
    print("合计 %d 格：PASS %d / FAIL %d" % (len(ROWS), passed, len(ROWS) - passed))
    for lb, ok, d in ROWS:
        if not ok:
            print("  FAIL:", lb, "|", str(d)[:200])
    return 0 if passed == len(ROWS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
