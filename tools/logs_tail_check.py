# -*- coding: utf-8 -*-
"""判据：**只读日志尾端点** `GET /api/logs/tail`（V59）。

```bash
python tools/logs_tail_check.py     # 约 6 秒
```

## 为什么单列

这个端点把"平台内部文件"暴露给界面 —— 它的错法都是安全面或稳定性事故：
能读任意路径（穿越）、能改文件、把凭据原样吐出去、把几百 MB 日志全载入内存。
所以判据必须证明**该拒的都拒了、该遮的都遮了、该只读的真只读**。

## 八格判据

| 格 | 判据 |
|---|---|
| ① | 取尾部 N 行：顺序正确，最后一行 = 文件最后一行 |
| ② | 上限生效：请求 1000 行 ⇒ 实际 ≤200 且 `truncated=true` |
| ③ | **穿越被拒**：`../secret`、绝对路径、带扩展名的路径 ⇒ 400，且响应里**不含任何日志内容** |
| ④ | **去敏生效**：日志里的假凭据被遮成 `***`，响应正文**搜不到原值**，`redacted ≥ 1` |
| ⑤ | **只读**：读前读后 size/mtime 不变，且**不新建文件** |
| ⑥ | 非本机来源（`Origin: https://evil.example`）⇒ 403 |
| ⑦ | 追尾：带 `since` 只给新行；同一个 `since` 再来一次**不重复给** |
| ⑧ | 审计有 `system.logs_tail` |
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent

#: 假凭据（**故意写成不像真凭据的形状**，但命中去敏规则；只用于判据）
FAKE_TOKEN = "sk-" + ("test" * 6)
FAKE_SEED = "AC4_TOKEN=" + ("dummy" * 6)


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


def req(base: str, path: str, hdr=None, timeout=20):
    r = urllib.request.Request(base + path, headers=hdr or {})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:                                   # noqa: BLE001
        return 0, "%s: %s" % (type(e).__name__, str(e)[:120])


def main() -> int:
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    data_dir = Path(tempfile.mkdtemp(prefix="ac_logs_"))
    logs_dir = data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    logfile = logs_dir / "server_stdout.log"
    # 造 300 行，最后几行里放"假凭据"与可辨识标记
    lines = ["line-%03d 正常日志内容" % i for i in range(1, 298)]
    lines += ["line-298 请求头 X-Agent-Token: %s" % FAKE_TOKEN,
              "line-299 %s" % FAKE_SEED,
              "line-300 最后一行: 结束标记-END"]
    logfile.write_text("\n".join(lines) + "\n", encoding="utf-8")

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
    print("只读日志尾端点判据（实例 %s，数据目录 %s）" % (base, data_dir))
    print("=" * 90)
    try:
        code = 0
        for _ in range(120):
            c, _b = req(base, "/api/status", timeout=3)
            if c == 200:
                code = 200
                break
            time.sleep(0.5)
        if code != 200:
            print("★实例没起来")
            return 2

        # ① 尾部 N 行
        st, body = req(base, "/api/logs/tail?name=server&lines=30")
        try:
            d = json.loads(body)
        except Exception:                                    # noqa: BLE001
            print("★返回不是 JSON：%s" % body[:200])
            return 1
        got = [x["text"] for x in (d.get("lines") or [])]
        cell("① 取尾部 30 行（顺序对、末行对）",
             st == 200 and len(got) == 30 and "结束标记-END" in got[-1]
             and "line-300" in body,
             "HTTP %s 行数=%d 末行=%s" % (st, len(got), got[-1][:28] if got else "?"))

        # ② 上限 200
        st2, body2 = req(base, "/api/logs/tail?name=server&lines=1000")
        d2 = json.loads(body2)
        cell("② 上限 200 行生效（truncated=true）",
             st2 == 200 and len(d2.get("lines") or []) <= 200 and d2.get("truncated") is True,
             "行数=%d truncated=%s" % (len(d2.get("lines") or []), d2.get("truncated")))

        # ③ 穿越被拒
        bad_names = ["../secret", "..%2Fsecret", "C:\\x.log", "server_stdout.log", "etc/passwd"]
        results = []
        for nm in bad_names:
            s3, b3 = req(base, "/api/logs/tail?name=" + urllib.parse.quote(nm))
            results.append((nm, s3, ("line-300" in b3) or ("结束标记" in b3)))
        cell("③ 穿越/路径名一律 400 且不吐内容",
             all(s == 400 and not leaked for _n, s, leaked in results),
             " ｜ ".join("%s→%s%s" % (n[:12], s, "(泄漏!)" if lk else "") for n, s, lk in results))

        # ④ 去敏
        joined = json.dumps(d, ensure_ascii=False)
        cell("④ 凭据被遮成 *** 且原文搜不到",
             FAKE_TOKEN not in joined and "dummy" * 6 not in joined
             and int(d.get("redacted") or 0) >= 1 and "***" in joined,
             "redacted=%s ｜ 原文出现=%s" % (d.get("redacted"),
                                            (FAKE_TOKEN in joined) or ("dummy" * 6 in joined)))

        # ⑤ 只读
        before = (logfile.stat().st_size, logfile.stat().st_mtime_ns)
        files_before = sorted(p.name for p in logs_dir.iterdir())
        req(base, "/api/logs/tail?name=server&lines=10")
        time.sleep(0.05)
        after = (logfile.stat().st_size, logfile.stat().st_mtime_ns)
        files_after = sorted(p.name for p in logs_dir.iterdir())
        cell("⑤ 只读（size/mtime 不变、不新建文件）",
             before == after and files_before == files_after,
             "size/mtime 不变=%s ｜ 文件列表不变=%s" % (before == after, files_before == files_after))

        # ⑥ 非本机来源
        s6, _b6 = req(base, "/api/logs/tail?name=server", {"Origin": "https://evil.example"})
        cell("⑥ 非本机来源 ⇒ 403", s6 == 403, "HTTP %s" % s6)

        # ⑦ 追尾
        s7, b7 = json.loads(body), None
        n1 = int(d.get("next_since") or 0)
        s7, b7 = req(base, "/api/logs/tail?name=server&since=%d" % n1)
        d7 = json.loads(b7)
        s7b, b7b = req(base, "/api/logs/tail?name=server&since=%d" % n1)
        d7b = json.loads(b7b)
        cell("⑦ 追尾（since 只给新行、重复不重给）",
             s7 == 200 and len(d7.get("lines") or []) == 0 and len(d7b.get("lines") or []) == 0,
             "第一次=%d 行 ｜ 再来一次=%d 行" % (len(d7.get("lines") or []),
                                                len(d7b.get("lines") or [])))

        # ⑧ 审计
        s8, b8 = req(base, "/api/audit")
        au = json.loads(b8) if b8 else {}
        evs = {str(r.get("event") or "") for r in (au.get("events") or [])}
        cell("⑧ 审计有 system.logs_tail", "system.logs_tail" in evs,
             "事件数=%d" % len(evs))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:                                    # noqa: BLE001
            proc.kill()
        import shutil
        shutil.rmtree(data_dir, ignore_errors=True)

    print("-" * 90)
    print("结论：%s" % ("**只读日志尾端点全绿（尾部/上限/穿越被拒/去敏/只读/本机/追尾/审计）**"
                      if not fails else "**有 %d 格失败**" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
