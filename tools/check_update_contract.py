# -*- coding: utf-8 -*-
"""检查：`GET /api/version` —— **一键更新插件**要的唯一一处版本事实，必须真的可用、且不撒谎。

老板 2026-10-18：「我让宋去做一个一键更新的插件出来，到时候别人就可以一键更新了，不用跑到仓库来部署」。
契约见 `design-docs/V57_一键更新插件契约.md`。

判据（真起一个独立实例打真 HTTP）：
  ① 200 且字段齐全（current/channel/install_dir/data_dir/update_safety/notes）；
  ② `current` **等于** `agent_community.__version__`（不许多一份口径）；
  ③ `latest` 与 `update_available` 允许为 `null`，但**不许瞎编**（没有远端查询就必须 null）；
  ④ `channel` 指向**有效远端**（Gitee），且给得出 release 拉取地址的形状；
  ⑤ 数据目录 != 安装目录时，`update_safety.keep_data_dir` 必须为 true（更新不许动数据）。
"""
from __future__ import annotations

import json
import os
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
    import socket
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def get(url: str, timeout: float = 10.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read().decode("utf-8", "replace")[:300]}
    except Exception as e:                                   # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(e).__name__, e)}


def main() -> int:
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    data_dir = Path(tempfile.mkdtemp(prefix="ac_version_data_"))
    keep = ("PATH", "PYTHON", "SYSTEM", "WINDIR", "TEMP", "TMP", "HOME", "USER",
            "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT", "LANG", "LC_")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AC_") and (k.upper().startswith(keep) or k in ("LD_LIBRARY_PATH",))}
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": str(data_dir), "PYTHONPATH": str(ROOT)})

    proc = subprocess.Popen([sys.executable, "-m", "agent_community.platform.server"],
                            cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fails, notes = 0, []
    try:
        code = 0
        for _ in range(120):
            code, _body = get(base + "/api/status", timeout=3)
            if code == 200:
                break
            time.sleep(0.5)
        if code != 200:
            print("★实例没起来，无法检查")
            return 2

        st, body = get(base + "/api/version")
        print("=" * 84)
        print("GET /api/version（实例 %s，数据目录 %s）" % (base, data_dir))
        print("=" * 84)
        print(json.dumps(body, ensure_ascii=False, indent=2)[:1200])

        def cell(name: str, ok: bool, detail: str = "") -> None:
            nonlocal fails
            print("  %-34s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
            fails += 0 if ok else 1

        cell("① 200 且字段齐全", st == 200 and all(
            k in (body or {}) for k in ("current", "channel", "install_dir", "data_dir",
                                        "update_safety", "notes")), "HTTP %s" % st)

        sys.path.insert(0, str(ROOT))
        import agent_community
        cell("② current == __version__", (body or {}).get("current") == agent_community.__version__,
             "%r vs %r" % ((body or {}).get("current"), agent_community.__version__))

        ok_null = (body or {}).get("latest") is None and (body or {}).get("update_available") is None
        cell("③ latest/update_available 如实为 null", ok_null,
             "latest=%r" % ((body or {}).get("latest"),))

        chan = (body or {}).get("channel") or {}
        cell("④ channel 指向主仓库（GitHub）且给了 release 地址", "github.com" in str(chan.get("repo", ""))
             and "{tag}" in str(chan.get("release_tarball_tpl", "")), str(chan.get("repo", ""))[:60])

        safety = (body or {}).get("update_safety") or {}
        cell("⑤ 更新只动安装目录、要保数据", safety.get("replace_install_dir_only") is True
             and safety.get("keep_data_dir") is True, json.dumps(safety, ensure_ascii=False)[:90])

        same = Path(str((body or {}).get("data_dir") or "")).resolve() == ROOT.resolve()
        if same:
            notes.append("数据目录与安装目录相同（用 AC_DATA_DIR 另指一处更安全）")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:                                    # noqa: BLE001
            proc.kill()

    print("-" * 84)
    for n in notes:
        print("  · %s" % n)
    print("结论：%s" % ("**/api/version 契约可用**" if not fails else "**有 %d 格失败**" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
