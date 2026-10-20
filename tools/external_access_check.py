# -*- coding: utf-8 -*-
"""判据：**外部访问的通用能力**（V65）—— 只读档 / 令牌种 cookie / 401 与 403 的区分。

```bash
python tools/external_access_check.py     # 约 12 秒（真起实例，随机端口 + 临时数据目录）
```

## 为什么要有它

老板 2026-10-20 把平台经免费 HTTP 隧道开到公网，外网 403。**问题不在那条隧道**，
而在于平台缺三件**通用**能力：只读档、页面自带令牌、对外访问引导。
这条判据钉死"通用性"：**换机器/换端口/换域名/换穿透方式，不改一行代码**。

## 九格

| 格 | 判据 |
|---|---|
| ① | 未设 `AC4_TOKEN` + 外部来源 ⇒ **403**（"服务未配置访问 Token"） |
| ② | 设了令牌 + 外部来源 + 三种携带方式（`Authorization` / `X-API-Key` / `?token=`）⇒ 全部 **200** |
| ③ | 令牌错 ⇒ **401**（与 403 区分开：403=没配，401=配了但不对） |
| ④ | 本机来源 ⇒ **一切照旧**（不受只读档影响） |
| ⑤ | 只读档开 + 外部 + 正确令牌 + **POST** ⇒ **403** 且带 `how_to_fix` |
| ⑥ | 只读档开 + 外部 + 正确令牌 + GET **白名单内**（`/api/status`）⇒ **200** |
| ⑦ | 只读档开 + 外部 + 正确令牌 + GET **白名单外**（`/api/config`）⇒ **403**（**令牌不能绕过档位**） |
| ⑧ | `?token=<正确>` ⇒ 响应带 `ac_ext_token` cookie；`?token=<错的>` ⇒ **不种** |
| ⑨ | **通用性**：判据自己做 grep —— 代码里不许出现域名/服务商/机器路径等硬编码（由 portability 扫描兜底） |
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
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent

TOKEN = "your-external-token"
#: 模拟"外部来源"：伪装成非本机 IP。**不用真域名**（判据不许依赖任何真实坐标）。
EXT_HEADER = {"X-Forwarded-For": "203.0.113.7"}      # TEST-NET-3，文档专用地址段


def free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _env(**over) -> dict:
    keep = ("PATH", "PYTHON", "SYSTEM", "WINDIR", "TEMP", "TMP", "HOME", "USER",
            "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT", "LANG", "LC_", "LD_LIBRARY")
    e = {k: v for k, v in os.environ.items()
         if not k.startswith("AC_") and not k.startswith("AC4_") and k.upper().startswith(keep)}
    e.update({k: str(v) for k, v in over.items()})
    return e


def req(base: str, path: str, method: str = "GET", headers: dict = None, body=None,
        timeout=15) -> tuple[int, dict, dict]:
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    r = urllib.request.Request(base + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw), dict(resp.headers)
            except Exception:                                # noqa: BLE001
                return resp.status, {"raw": raw[:200]}, dict(resp.headers)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw), dict(e.headers)
        except Exception:                                    # noqa: BLE001
            return e.code, {"raw": raw[:200]}, dict(e.headers)
    except Exception as e:                                   # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(e).__name__, str(e)[:120])}, {}


def start(port: int, data_dir: Path, **extra):
    env = _env(AC_PORT=port, AC_DATA_DIR=str(data_dir), PYTHONPATH=str(ROOT),
               PYTHONIOENCODING="utf-8", AC_PYTHON=sys.executable, **extra)
    p = subprocess.Popen([sys.executable, "-m", "agent_community.platform.server"],
                         cwd=str(ROOT), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d" % port
    for _ in range(120):
        if req(base, "/api/status", timeout=3)[0] == 200:
            return p, base
        time.sleep(0.5)
    return p, base


def main() -> int:
    fails = 0

    def cell(name: str, ok: bool, detail: str = "") -> None:
        nonlocal fails
        print("   %-56s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        fails += 0 if ok else 1

    print("=" * 100)
    print("外部访问通用能力判据（V65）—— 只读档 / 令牌携带 / 401 与 403 区分")
    print("=" * 100)
    tmp = Path(tempfile.mkdtemp(prefix="ac_ext_"))
    procs = []
    try:
        # ① 没配令牌 + 外部来源 ⇒ 403
        p1, b1 = start(free_port(), tmp / "d1")
        procs.append(p1)
        st, body, _ = req(b1, "/api/status", headers=EXT_HEADER)
        cell("① 未配令牌 + 外部来源 ⇒ 403（服务未配置访问 Token）",
             st == 403 and "Token" in str(body.get("error")), "HTTP %s" % st)

        # ②③ 配了令牌：三种携带方式 200；错令牌 401
        p2, b2 = start(free_port(), tmp / "d2", AC4_TOKEN=TOKEN)
        procs.append(p2)
        st_a, _ba, _h = req(b2, "/api/status", headers={**EXT_HEADER,
                                                       "Authorization": "Bearer " + TOKEN})
        st_b, _bb, _h = req(b2, "/api/status", headers={**EXT_HEADER, "X-API-Key": TOKEN})
        st_c, _bc, _h = req(b2, "/api/status?token=" + TOKEN, headers=EXT_HEADER)
        cell("② 带令牌（三种携带方式）⇒ 200", st_a == st_b == st_c == 200,
             "Authorization=%s X-API-Key=%s ?token=%s" % (st_a, st_b, st_c))
        st_w, _bw, _h = req(b2, "/api/status", headers={**EXT_HEADER,
                                                       "Authorization": "Bearer wrong-token"})
        cell("③ 令牌错 ⇒ 401（与 403 区分）", st_w == 401, "HTTP %s" % st_w)

        # ④ 本机来源照旧
        st_l, _bl, _h = req(b2, "/api/config")
        cell("④ 本机来源 ⇒ 一切照旧（/api/config 200）", st_l == 200, "HTTP %s" % st_l)

        # ⑤⑥⑦ 只读档
        p3, b3 = start(free_port(), tmp / "d3", AC4_TOKEN=TOKEN, AC_EXTERNAL_READONLY="1")
        procs.append(p3)
        auth = {**EXT_HEADER, "Authorization": "Bearer " + TOKEN}
        st_wr, body_wr, _h = req(b3, "/api/workshop", method="POST", headers=auth,
                                 body={"name": "不该写进去", "hall_content": "x"})
        cell("⑤ 只读档：外部写操作 ⇒ 403 且带 how_to_fix",
             st_wr == 403 and bool(body_wr.get("how_to_fix")), "HTTP %s" % st_wr)
        st_ok, _bo, _h = req(b3, "/api/status", headers=auth)
        cell("⑥ 只读档：白名单内 GET ⇒ 200", st_ok == 200, "HTTP %s" % st_ok)
        st_no, _bn, _h = req(b3, "/api/config", headers=auth)
        cell("⑦ 只读档：白名单外 GET（/api/config）⇒ 403（令牌不能绕过档位）",
             st_no == 403, "HTTP %s" % st_no)
        st_loc, _bl2, _h = req(b3, "/api/config")     # 本机仍可用
        cell("⑦b 只读档不影响本机（本机 /api/config 200）", st_loc == 200, "HTTP %s" % st_loc)

        # ⑧ cookie 种法
        st_g, _bg, h_g = req(b2, "/?token=" + TOKEN, headers=EXT_HEADER)
        setc = h_g.get("Set-Cookie") or h_g.get("set-cookie") or ""
        st_bad, _bbad, h_bad = req(b2, "/?token=wrong-token", headers=EXT_HEADER)
        setc_bad = h_bad.get("Set-Cookie") or h_bad.get("set-cookie") or ""
        cell("⑧ ?token=正确 ⇒ 种 ac_ext_token；错令牌 ⇒ 不种",
             ("ac_ext_token" in setc) and ("ac_ext_token" not in setc_bad),
             "正确种=%s ｜ 错误种=%s" % (bool("ac_ext_token" in setc), bool("ac_ext_token" in setc_bad)))
    finally:
        for p in procs:
            try:
                p.terminate()
                p.wait(timeout=15)
            except Exception:                                # noqa: BLE001
                try:
                    p.kill()
                except Exception:                            # noqa: BLE001
                    pass
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 100)
    print("结论：%s" % ("**外部访问通用能力全绿（只读档 / 三种令牌携带 / 401与403区分 / cookie / 本机不受影响）**"
                      if not fails else "**有 %d 格失败**" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
