# -*- coding: utf-8 -*-
"""P2：生成 **frpc 配置**（V61 §6）—— 让跑在 NAT 后面的公司 B 被公司 A 访问到。

```bash
# 先看必填项齐不齐（不写文件、不打印 token 明文）
python tools/gen_frpc.py --check

# 真生成（写 <AC_DATA_DIR>/frpc/frpc.toml，并打印启动命令）
python tools/gen_frpc.py --write
```

## 它做什么 / 不做什么

- **做**：按环境变量生成 frp 客户端配置（TOML），字段全部来自配置，**不含任何写死地址**；
  写文件时**收紧权限**（Unix 600）；日志与输出里 token **只显示掩码**。
- **不做**：不内置 frps 服务端、不自动启动 frpc（避免"悄悄替用户开一个公网入口"）、不猜服务器地址。

## 需要的环境变量（登记在 `env_spec`）

| 变量 | 必填 | 说明 |
|---|---|---|
| `AC_FRP_SERVER_ADDR` | 是 | frps 地址（域名或 IP，**由部署方提供**） |
| `AC_FRP_SERVER_PORT` | 否 | frps 控制端口，默认 7000 |
| `AC_FRP_TOKEN` | 是 | frps 的 token（与服务器侧同一串） |
| `AC_FRP_REMOTE_PORT` | 是 | 在 frps 上对外开放的端口（公司 A 就访问它） |
| `AC_PORT` | 否 | 本实例端口（要映射出去的那个），默认按平台默认端口 |
| `AC_FRP_PROXY_NAME` | 否 | 代理名，默认 `ac-company-b` |
| `AC_DATA_DIR` | 否 | 落盘根（文件写到 `<它>/frpc/frpc.toml`） |
"""
from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _mask(token: str) -> str:
    if not token:
        return "(未设置)"
    return token[:4] + "…" + token[-2:] if len(token) > 8 else "***"


def collect() -> tuple[dict, list[str]]:
    """读配置并校验；返回 `(值, 缺什么)`。**不打印明文 token。**"""
    from agent_community.platform_address import resolve_port
    addr = (os.environ.get("AC_FRP_SERVER_ADDR") or "").strip()
    port = (os.environ.get("AC_FRP_SERVER_PORT") or "7000").strip()
    token = (os.environ.get("AC_FRP_TOKEN") or "").strip()
    remote = (os.environ.get("AC_FRP_REMOTE_PORT") or "").strip()
    name = (os.environ.get("AC_FRP_PROXY_NAME") or "ac-company-b").strip()
    local = int(resolve_port())
    missing = []
    if not addr:
        missing.append("AC_FRP_SERVER_ADDR（frps 地址，部署方给）")
    if not token:
        missing.append("AC_FRP_TOKEN（与 frps 一致的 token）")
    if not remote:
        missing.append("AC_FRP_REMOTE_PORT（frps 上对外开放的端口）")
    if not port.isdigit():
        missing.append("AC_FRP_SERVER_PORT 必须是数字")
    if remote and not remote.isdigit():
        missing.append("AC_FRP_REMOTE_PORT 必须是数字")
    return {"addr": addr, "port": port, "token": token, "remote": remote,
            "name": name, "local": local}, missing


def render(v: dict) -> str:
    """生成 frp 客户端 TOML（frp ≥0.52 的格式）。"""
    return (
        "# 由 `python tools/gen_frpc.py --write` 生成（V61 公司间合作 P2）。\n"
        "# ⚠️ 本文件含 token：别提交到仓库、别贴到聊天里。\n\n"
        'serverAddr = "%s"\n'
        "serverPort = %s\n\n"
        'auth.method = "token"\n'
        'auth.token = "%s"\n\n'
        "[[proxies]]\n"
        'name = "%s"\n'
        'type = "tcp"\n'
        'localIP = "127.0.0.1"\n'
        "localPort = %d\n"
        "remotePort = %s\n"
    ) % (v["addr"], v["port"], v["token"], v["name"], v["local"], v["remote"])


def default_target() -> Path:
    from agent_community.platform.identity import _default_data_root
    return Path(_default_data_root()) / "frpc" / "frpc.toml"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成 frpc 配置（公司间合作 P2）")
    ap.add_argument("--check", action="store_true", help="只校验必填项，不写文件")
    ap.add_argument("--write", action="store_true", help="写配置文件并打印启动命令")
    ap.add_argument("--out", default="", help="自定义输出路径（默认 <AC_DATA_DIR>/frpc/frpc.toml）")
    ap.add_argument("--print", dest="show", action="store_true",
                    help="把生成的内容打到屏幕（token 会以掩码替换）")
    args = ap.parse_args(argv)

    v, missing = collect()
    print("frpc 配置（公司 B 侧）")
    print("  frps 地址      : %s" % (v["addr"] or "(未设置)"))
    print("  frps 控制端口  : %s" % v["port"])
    print("  远端开放端口   : %s" % (v["remote"] or "(未设置)"))
    print("  本实例端口     : %d（要映射出去的就是它）" % v["local"])
    print("  代理名         : %s" % v["name"])
    print("  token          : %s（明文只写进配置文件，不打印）" % _mask(v["token"]))
    if missing:
        print("\n★还缺 %d 项，补齐后再来：" % len(missing))
        for m in missing:
            print("   - %s" % m)
        print("\n  这些只有部署方知道（frps 在哪儿、token、对外开放哪个端口）——"
              "平台的其余部分不需要它们也能跑（见 V61 §6：P1 不依赖 frp）。")
        return 1
    text = render(v)
    if args.show:
        import re
        print("\n--- 生成内容（token 已掩码）---")
        print(re.sub(r'(auth\.token = ")([^"]*)(")', lambda m: m.group(1) + _mask(m.group(2)) + m.group(3),
                     text))
        print("--- 结束 ---")
    if not args.write:
        print("\n（--check 模式：没写文件。要落地请加 --write）")
        return 0

    out = Path(args.out) if args.out else default_target()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    try:
        out.chmod(stat.S_IRUSR | stat.S_IWUSR)          # 600：只有本人能读（Windows 上尽力而为）
        perm = "已收紧为 600"
    except Exception as e:                              # noqa: BLE001
        perm = "权限没能收紧（%s）—— 请自己限制该文件访问" % e
    print("\n✔ 已写入：%s（%s）" % (out, perm))
    print("启动 frpc（在装了 frp 的机器上跑）：")
    print("  frpc -c \"%s\"" % out)
    print("\n安全提醒（V61 §4）：暴露出去后**只开 a2a 入站**；"
          "`/api/config`、`/api/admin/*`、`/api/logs/*` 不要映射到公网。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
