#!/usr/bin/env python
"""工作间（Workshop）HTTP 接线测试 — 自带临时服务与临时数据目录，不依赖外部实例。

流程：拉起临时服务（随机高端口 + 临时 AC_DATA_DIR）→ 创建/查询/开启工作间 →
校验接线 → 立即停止进程。不等待成员真实"进入"（那需要外部 harness，不属于单元测试范围）。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER_SCRIPT = ROOT / "agent_community" / "platform" / "server.py"

GREEN, RED, RESET = "\033[92m", "\033[91m", "\033[0m"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))
    print(f"{GREEN}[PASS]{RESET} {name}" if cond else f"{RED}[FAIL]{RESET} {name} {detail}", flush=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    tmp = Path(tempfile.mkdtemp(prefix="ac_ws_http_"))

    env = os.environ.copy()
    env["AC_PORT"] = str(port)
    env["AC_DATA_DIR"] = str(tmp / "data")
    env["TEMP"] = str(tmp / "tmp")
    (tmp / "tmp").mkdir(parents=True, exist_ok=True)
    env.pop("AC_AI_API_KEY", None)

    def post(path: str, body: dict):
        req = urllib.request.Request(
            base + path, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))

    def get(path: str):
        with urllib.request.urlopen(base + path, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))

    proc = subprocess.Popen(
        [sys.executable, str(SERVER_SCRIPT)],
        env=env, cwd=str(ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace",
    )
    try:
        ready = False
        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                st = get("/api/status")
                if st:
                    ready = True
                    break
            except Exception:
                time.sleep(0.5)
        check("W1 临时服务启动就绪（/api/status）", ready)
        if not ready:
            raise RuntimeError("服务未就绪")

        check("W2 首次运行自动创建数据目录", (tmp / "data").is_dir(), str(tmp / "data"))

        w = post("/api/workshop", {
            "name": "HTTP 接线验证",
            "hall_content": "任务：示例任务。\n验证码：HALL-HTTP-99\n",
        })
        check("W3 创建工作室返回成功", bool(w.get("success", True)) and bool(w.get("workshop_id")), json.dumps(w, ensure_ascii=False)[:200])
        ws_id = w.get("workshop_id")
        check("W4 返回 workshop_id", bool(ws_id), str(ws_id))

        st = get(f"/api/workshop/{ws_id}")
        members = st.get("members") or []
        check("W5 查询工作室成功", bool(st), json.dumps(st, ensure_ascii=False)[:200])
        check("W6 工作室含成员列表", len(members) > 0, f"members={len(members)}")
        check("W7 大厅内容已写入", "HALL-HTTP-99" in json.dumps(st, ensure_ascii=False))

        s = post(f"/api/workshop/{ws_id}/start", {})
        check("W8 开启工作室返回成功", isinstance(s, dict), json.dumps(s, ensure_ascii=False)[:200])

        st2 = get(f"/api/workshop/{ws_id}")
        check("W9 开启后状态可查询", bool(st2))
        check("W10 工作室已落盘", any((tmp / "data").glob("workshops.json")), str(list((tmp / "data").glob("*"))))
    except Exception as e:  # noqa: BLE001
        check("W0 测试执行未抛异常", False, repr(e))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("[INFO] 临时服务已停止")

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n工作间 HTTP 测试: {passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
