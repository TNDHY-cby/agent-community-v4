#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**大厅「未读回报」的后端水位**（`FE-33` §5 第 1 条缺口的后端实现）。

前端第一版把水位放 `localStorage`（换浏览器/换设备不同步、清缓存重置）；这里验后端那份能不能用。

**为什么分两层**（这两层各测各的，都真跑）：

- **口径层**（①⑥）：`read_marks.unread_of()` 是**纯函数**，直接喂合成 discussion ——
  "未读怎么算"的真相就在它里面，用函数测比造真消息更准（也更快）；
- **契约层**（②③④⑤⑦⑧）：真起临时实例（换端口 + 换数据目录）+ 真工作间，验 HTTP 端点
  （推进 / 读回 / 只前进 / reset / 404 / GET 只读）。

⚠️ 不通过"父进程往子进程内存塞消息"来造数据 —— 那是两个进程，塞不进去（第一版就是这么错的）。

退出码：0 = 全过；1 = 有格没过；2 = 起不来（如实说没跑成）。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
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
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
PY = sys.executable

#: 合成夹具：与真实落盘结构一致（role / content / seq / timestamp / zone）
DISC = [
    {"role": "user", "content": "开工", "seq": 0, "timestamp": "2026-11-06T00:00:00", "zone": 1},
    {"role": "member", "content": "干完了第一步", "seq": 1,
     "timestamp": "2026-11-06T00:01:00", "zone": 1},
    {"role": "orchestrator", "content": "汇总一下", "seq": 2,
     "timestamp": "2026-11-06T00:02:00", "zone": 1},
]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _http(base, path, body=None, timeout=20):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    h = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(base + path, data=data, headers=h,
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw or "{}")
        except ValueError:
            return e.code, {"raw": raw[:200]}


def _sha(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except FileNotFoundError:
        return ""


def main() -> int:
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_readmarks_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rows: list = []

    def check(label, ok, detail=""):
        rows.append((label, bool(ok), detail))
        print("  %s %s%s" % ("PASS" if ok else "FAIL", label,
                            ("　（" + detail + "）") if detail and not ok else ""))

    print("判据：大厅「未读回报」后端水位（口径层纯函数 + 契约层真实例）")
    print("=" * 74)
    try:
        # ── 口径层：纯函数（不依赖任何服务）──
        os.environ["AC_DATA_DIR"] = tmpdir
        from agent_community.platform import read_marks as RM

        u1 = RM.unread_of(DISC, RM.UNREAD_INITIAL)
        check("① 没看过时：unread=2（非用户消息数）/ max_seq=2 / 首个未读 seq=1",
              u1["unread"] == 2 and u1["max_seq"] == 2 and u1["first_unread_seq"] == 1,
              json.dumps(u1, ensure_ascii=False))

        disc2 = DISC + [{"role": "user", "content": "我也说一句", "seq": 3,
                         "timestamp": "2026-11-06T00:03:00", "zone": 1}]
        u2 = RM.unread_of(disc2, RM.UNREAD_INITIAL)
        check("⑥ 最高 seq 是用户自己发的 ⇒ 未读仍为 2（不当成新回报）",
              u2["unread"] == 2 and u2["max_seq"] == 3,
              json.dumps(u2, ensure_ascii=False))

        u3 = RM.unread_of(DISC, 2)
        check("①' 水位到 2 ⇒ unread=0（口径与前端 FE-33 一致）", u3["unread"] == 0,
              json.dumps(u3, ensure_ascii=False))

        # ── 契约层：真实例 ──
        up = False
        for _ in range(80):
            try:
                st, _b = _http(base, "/api/status", timeout=3)
                up = st == 200
            except (urllib.error.URLError, OSError):
                up = False
            if up:
                break
            time.sleep(0.5)
        if not up:
            print("[FAIL] 平台没起来 —— 这次不算跑成")
            return 2

        st, made = _http(base, "/api/workshop", {"name": "未读水位巡检", "hall_content": "夹具",
                                                "create_task": True, "task_title": "巡检任务",
                                                "task_description": "由 tools/unread_marks_check.py 创建"})
        ws = str(made.get("workshop_id") or "")
        if not ws:
            print("[FAIL] 建工作间失败：%s" % str(made)[:200])
            return 2
        marks_file = Path(tmpdir) / "read_marks.json"

        st, b = _http(base, "/api/workshop/%s/read" % ws)
        check("② 新工作间没读过：initialized=false / seq=-1",
              st == 200 and b.get("initialized") is False and b.get("seq") == -1,
              json.dumps(b, ensure_ascii=False)[:200])

        st, b2 = _http(base, "/api/workshop/%s/read" % ws, {"seq": 2})
        check("③ POST {seq:2} ⇒ 水位=2、moved=true",
              st == 200 and b2.get("seq") == 2 and b2.get("moved") is True,
              json.dumps(b2, ensure_ascii=False)[:200])

        st, b3 = _http(base, "/api/workshop/%s/read" % ws)
        check("③' 再 GET 一致（seq=2 / initialized=true）",
              st == 200 and b3.get("seq") == 2 and b3.get("initialized") is True,
              json.dumps(b3, ensure_ascii=False)[:200])

        st, b4 = _http(base, "/api/workshop/%s/read" % ws, {"seq": 0})
        check("④ POST 更小的 seq ⇒ 不后退，moved=false 且说明原因",
              st == 200 and b4.get("moved") is False and b4.get("seq") == 2
              and bool(str(b4.get("why") or "")),
              json.dumps(b4, ensure_ascii=False)[:200])

        st, b5 = _http(base, "/api/workshop/%s/read" % ws, {"reset": True})
        check("⑤ reset ⇒ 回到 -1", st == 200 and b5.get("seq") == -1,
              json.dumps(b5, ensure_ascii=False)[:200])

        st7, b7 = _http(base, "/api/workshop/no-such-ws/read")
        st7b, b7b = _http(base, "/api/workshop/no-such-ws/read", {})
        check("⑦ 不存在的工作间 ⇒ GET/POST 都 404 + code + next",
              st7 == 404 and b7.get("code") == "NO_SUCH_WORKSHOP" and b7.get("next")
              and st7b == 404 and b7b.get("code") == "NO_SUCH_WORKSHOP",
              "GET=%s/%s POST=%s/%s" % (st7, b7.get("code"), st7b, b7b.get("code")))

        h1 = _sha(marks_file)
        for _ in range(3):
            _http(base, "/api/workshop/%s/read" % ws)
        h2 = _sha(marks_file)
        check("⑧ 连调 GET 不改水位文件（sha256 不变）", h1 == h2,
              "%s -> %s" % (h1[:10], h2[:10]))

        # 落盘跨进程：另起一个进程读同一个数据目录，水位还在（"重启不丢"）
        code = (
            "import json,sys;"
            "from agent_community.platform import read_marks as R;"
            "print(json.dumps(R.get(%r)))" % ws
        )
        _http(base, "/api/workshop/%s/read" % ws, {"seq": 2})
        r = subprocess.run([PY, "-c", code], cwd=str(ROOT),
                           env={**env, "AC_DATA_DIR": tmpdir},
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        ok = '"seq": 2' in (r.stdout or "")
        check("⑨ 水位落盘：另一个进程读同一数据目录仍是 2（重启不丢）", ok,
              (r.stdout or r.stderr)[:160])
    finally:
        try:
            srv.terminate()
            srv.wait(timeout=10)
        except Exception:                                      # noqa: BLE001
            srv.kill()
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("-" * 74)
    passed = sum(1 for _l, ok, _d in rows if ok)
    print("合计 %d 格：PASS %d / FAIL %d" % (len(rows), passed, len(rows) - passed))
    for lb, ok, d in rows:
        if not ok:
            print("  FAIL:", lb, "|", str(d)[:200])
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
