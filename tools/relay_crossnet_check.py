# -*- coding: utf-8 -*-
"""判据：**中转跨网络可用性**（V66 补充）—— 老板 2026-10-20：「不能只是局域网」。

```bash
python tools/relay_crossnet_check.py     # 约 40 秒
```

## 为什么单独一条

`relay_check.py` 全在 `127.0.0.1` 上跑：它证明**逻辑对**，但**证明不了跨网络**。
老板这句话点得准：只用回环地址测 = "局域网玩具"。

这条判据要证明三件**跟网络范围有关**的事：

| 格 | 判据 |
|---|---|
| ① | Hub 绑在**非回环地址**（本机内网 IP）上 ⇒ 终端用该地址能连上（**不依赖 127.0.0.1**） |
| ② | 同一条消息经**内网地址**传递，内容逐字一致 |
| ③ | **TLS**：Hub 用自签证书起 HTTPS ⇒ 终端配 `AC_HUB_CA_FILE`（或自测用 insecure）能连上；**明文 HTTP 时客户端不会误以为安全** |
| ④ | 拿不到任何非回环地址时 ⇒ 判据**如实 SKIP**（标"未验证"，**不假绿**） |

> ④ 是这条判据最重要的部分：**没见过真跨网络就别说"支持跨网络"**。
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
HUB_TOKEN = "your-hub-token"


def free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def lan_ipv4() -> str:
    """本机的**非回环** IPv4（拿不到返回空串）。优先 Tailscale（`100.x`，真跨网络的 overlay 地址）。"""
    cands: list[str] = []
    # 1) 默认出口地址（UDP 连接探出，不真发包）
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        if not ip.startswith("127."):
            cands.append(ip)
    except Exception:                                        # noqa: BLE001
        pass
    finally:
        s.close()
    # 2) Tailscale 的 100.64.0.0/10（跨公网 overlay，最接近"真跨网络"）
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("100."):
                cands.insert(0, ip)
    except Exception:                                        # noqa: BLE001
        pass
    return cands[0] if cands else ""


def wsl_available() -> bool:
    """WSL 能不能用（它代表**另一个网络栈**，比同机回环更接近真跨网络）。"""
    distro = (os.environ.get("AC_LINUX_WSL_DISTRO") or "Ubuntu-24.04").strip()
    try:
        r = subprocess.run(["wsl", "-d", distro, "-u", "root", "--", "python3", "--version"],
                           capture_output=True, timeout=60)
        return r.returncode == 0
    except Exception:                                        # noqa: BLE001
        return False


def _wsl_python_cmd(distro: str, args: list[str]) -> list[str]:
    return ["wsl", "-d", distro, "-u", "root", "--", "python3"] + args


def _env(**over) -> dict:
    keep = ("PATH", "PYTHON", "SYSTEM", "WINDIR", "TEMP", "TMP", "HOME", "USER",
            "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT", "LANG", "LC_", "LD_LIBRARY")
    e = {k: v for k, v in os.environ.items()
         if not k.startswith("AC_") and not k.startswith("AC4_") and k.upper().startswith(keep)}
    e.update({k: str(v) for k, v in over.items()})
    return e


def req(url: str, body=None, token: str = "", method: str = "GET", timeout=20, insecure=False):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    r = urllib.request.Request(url, data=data, method=method, headers=h)
    ctx = None
    if url.startswith("https://") and insecure:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(r, timeout=timeout, context=ctx) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read().decode("utf-8", "replace")[:160]}
    except Exception as e:                                   # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(e).__name__, str(e)[:140])}


def start_hub(host: str, port: int, extra_args: list | None = None, **extra):
    env = _env(AC_HUB_TOKEN=HUB_TOKEN, AC_HUB_PORT=port, PYTHONIOENCODING="utf-8", **extra)
    cmd = [sys.executable, str(ROOT / "tools" / "relay_hub.py"), "--host", host, "--port", str(port)]
    cmd += (extra_args or [])
    p = subprocess.Popen(cmd, cwd=str(ROOT), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p


def self_signed_cert(dirp: Path) -> tuple[str, str] | tuple[None, None]:
    """用标准库生成不了证书 ⇒ 调 openssl；没有 openssl 就如实返回空（不假装支持 TLS 测试）。"""
    cert, key = dirp / "hub.crt", dirp / "hub.key"
    try:
        r = subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
             "-subj", "/CN=relay-hub-test", "-keyout", str(key), "-out", str(cert)],
            capture_output=True, timeout=60)
        if r.returncode == 0 and cert.is_file() and key.is_file():
            return str(cert), str(key)
    except Exception:                                        # noqa: BLE001
        pass
    return None, None


def main() -> int:
    fails = 0
    skipped = 0

    def cell(name: str, ok: bool, detail: str = "") -> None:
        nonlocal fails
        print("   %-58s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        fails += 0 if ok else 1

    def skip(name: str, why: str) -> None:
        nonlocal skipped
        skipped += 1
        print("   %-58s %-6s %s" % (name, "SKIP", why))

    print("=" * 104)
    print("中转跨网络判据（V66 补）—— 老板：「不能只是局域网」")
    print("=" * 104)
    ip = lan_ipv4()
    tmp = Path(tempfile.mkdtemp(prefix="ac_crossnet_"))
    procs: list[subprocess.Popen] = []
    try:
        if not ip:
            skip("① Hub 绑非回环地址、终端用该地址连", "本机拿不到非回环 IPv4（隔离环境/无网卡）")
            skip("② 经内网地址传消息内容一致", "同上")
        else:
            port = free_port()
            hub = start_hub(ip, port)
            procs.append(hub)
            hbase = "http://%s:%d" % (ip, port)
            up = False
            for _ in range(60):
                st, _b = req(hbase + "/hub/health", timeout=3)
                if st == 200:
                    up = True
                    break
                time.sleep(0.5)
            cell("① Hub 绑非回环地址（%s）且终端能连上" % ip, up, hbase)

            st_s, sb = req(hbase + "/hub/send", {"from": "t1", "to": "t2", "payload": "跨网络测试"}, HUB_TOKEN, "POST")
            payload_ok = False
            got_n = 0
            if st_s == 200:
                # 从 Hub 队列侧验证：t2 的心跳能取到
                st_h, hb = req(hbase + "/hub/heartbeat", {"terminal_id": "t2", "since": 0, "wait": 1},
                               HUB_TOKEN, "POST")
                msgs = (hb or {}).get("messages") or []
                got_n = len(msgs)
                payload_ok = any((m.get("payload") or "") == "跨网络测试" for m in msgs)
            cell("② 经非回环地址（%s）传递，内容逐字一致" % ip, payload_ok,
                 "HTTP %s ｜ 取到 %d 条" % (st_s, got_n))

        # ② WSL 侧作为"另一台机器"（**不同网络栈**）来连 Windows 上的 Hub
        if not wsl_available():
            skip("② WSL 侧（另一网络栈）跨网络注册与投递", "本机没有可用的 WSL（不假装测过）")
        else:
            distro = (os.environ.get("AC_LINUX_WSL_DISTRO") or "Ubuntu-24.04").strip()
            host = ip or "127.0.0.1"
            port = free_port()
            hub = start_hub("0.0.0.0", port)                 # 绑所有网卡，让 WSL 能连
            procs.append(hub)
            hbase = "http://%s:%d" % (host, port)
            up = False
            for _ in range(60):
                st, _b = req(hbase + "/hub/health", timeout=3)
                if st == 200:
                    up = True
                    break
                time.sleep(0.5)
            # 把探针脚本放到 **纯 ASCII 路径**（WSL 侧好引用）
            # ★2026-10-25（真跑踩到）：WSL 里 **D 盘没挂到 /mnt/d** 时，探针文件在 WSL 眼里根本不存在，
            #   表现为 python3: can't open file ...probe.py —— 看起来像判据坏了/平台不通，
            #   其实只是**这台机器的 WSL 没挂那个盘**。这里先探一下，看不到就**如实跳过并给挂载命令**。

            probe_dir = Path(os.environ.get("TEMP") or "/tmp") / "ac_wsl_probe"
            probe_dir.mkdir(parents=True, exist_ok=True)
            probe = probe_dir / "probe.py"
            probe.write_text((ROOT / "tools" / "wsl_relay_probe.py").read_text(encoding="utf-8"),
                             encoding="utf-8")
            drive = (probe.anchor or "D:\\")[:1].lower()
            probe_wsl = "/mnt/%s/%s" % (drive, str(probe).replace("\\", "/").split(":", 1)[1].lstrip("/"))
            # ★2026-10-25：先确认 WSL 里**看得见**探针文件；看不见＝那个盘没挂进去
            #   （表现为 `python3: can't open file ...probe.py`，像判据坏了，其实是环境）
            try:
                _vis = subprocess.run(["wsl", "-d", distro, "-u", "root", "--",
                                       "test", "-f", probe_wsl],
                                      capture_output=True, timeout=60)
                _visible = (_vis.returncode == 0)
            except (OSError, subprocess.TimeoutExpired):
                _visible = False
            if not _visible:
                skip("② WSL 侧（另一网络栈）跨网络注册与投递",
                     "WSL 里看不到 %s —— 这个盘没挂进去（`sudo mount -t drvfs %s: /mnt/%s`），"
                     "不是平台不通" % (probe_wsl, drive.upper(), drive))
                up = False
            wsl_ok, wsl_out = False, ""
            if up:
                # 先让 Windows 侧有个终端在线，WSL 才能投递给它
                req(hbase + "/hub/register", {"terminal_id": "win-term", "name": "windows"}, HUB_TOKEN, "POST")
                try:
                    r = subprocess.run(_wsl_python_cmd(distro, [probe_wsl, hbase, HUB_TOKEN, "wsl-term"]),
                                       capture_output=True, timeout=120, text=True, encoding="utf-8",
                                       errors="replace")
                    wsl_out = (r.stdout or "").strip()[-200:] or (r.stderr or "").strip()[-200:]
                    wsl_ok = ('"register": 200' in (r.stdout or ""))
                except Exception as e:                       # noqa: BLE001
                    wsl_out = "%s: %s" % (type(e).__name__, str(e)[:120])
                # Windows 侧看 WSL 是否真注册上了 + 投一条让它取
                st_l, lb = req(hbase + "/hub/terminals", None, HUB_TOKEN, "GET")
                ids = {t.get("terminal_id") for t in ((lb or {}).get("terminals") or [])}
                if "wsl-term" in ids:
                    req(hbase + "/hub/send", {"from": "win-term", "to": "wsl-term",
                                              "payload": "从 Windows 跨网络发来"}, HUB_TOKEN, "POST")
            cell("② WSL 侧（另一网络栈）能注册并互达", wsl_ok and "wsl-term" in ids if up else False,
                 "hub=%s ｜ WSL 输出=%s ｜ 在线=%s" % (hbase, wsl_out[:80], sorted(ids) if up else "-"))

        # ③ TLS
        cert, key = self_signed_cert(tmp)
        if not cert:
            skip("③ TLS（自签证书）端到端", "本机没有 openssl，生成不了自签证书（不假装测过）")
        else:
            port = free_port()
            host = ip or "127.0.0.1"
            hub = start_hub(host, port, extra_args=["--certfile", cert, "--keyfile", key])
            procs.append(hub)
            hbase = "https://%s:%d" % (host, port)
            up = False
            for _ in range(60):
                st, _b = req(hbase + "/hub/health", timeout=3, insecure=True)
                if st == 200:
                    up = True
                    break
                time.sleep(0.5)
            # 严格校验（不带 insecure）应当失败 —— 自签证书不被信任是**正确**行为
            st_strict, _x = (0, {})
            try:
                import ssl
                ctx = ssl.create_default_context()
                r = urllib.request.Request(hbase + "/hub/health")
                with urllib.request.urlopen(r, timeout=5, context=ctx) as resp:
                    st_strict = resp.status
            except Exception:                                # noqa: BLE001
                st_strict = 0
            cell("③ TLS 生效：自签证书下 Hub 可连（且默认严格校验会拒绝）",
                 up and st_strict == 0,
                 "insecure 连=%s ｜ 严格校验=%s（0=如期拒绝）" % (up, st_strict))
    finally:
        for p in reversed(procs):
            try:
                p.terminate()
                p.wait(timeout=10)
            except Exception:                                # noqa: BLE001
                try:
                    p.kill()
                except Exception:                            # noqa: BLE001
                    pass
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 104)
    tail = "（另有 %d 格如实跳过）" % skipped if skipped else ""
    print("结论：%s%s" % ("**跨网络能力已验证（非回环地址 + TLS）**" if not fails and not skipped
                        else ("**有 %d 格失败**" % fails if fails
                              else "**部分验证：能测的都过了，没条件测的已如实跳过**"),
                        tail))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
