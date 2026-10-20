# -*- coding: utf-8 -*-
"""platform/tunnel：**"对端能不能连到我、连不上该怎么办"的建议层**（V62 §5）。

设计边界（很重要，别越界）：

- 本模块**只做判断与说法**，**不发起任何网络请求、不启动任何隧道客户端**；
- **不认任何穿透服务商**（frp / ngrok / 自建反代在它眼里都一样：最后只要给对端一个 `http://host:port`）；
- 判断只依据**本机事实**（监听地址是不是回环、端口是不是本机端口）。

为什么这么做：老板 2026-10-20 明确要"**通用框架**"。把某家服务商写进平台，
就等于给产品绑一个外部依赖（也是"私人定制"的一种）。平台该给的是
「你这台现在能不能被外面连到 → 不能的话有哪几类办法 → 最后你要交给我什么」。
"""
from __future__ import annotations

import os
from typing import Optional

#: 哪些东西**绝对不能**映射到公网（V61 §4 三件套之一）
#: ⚠️ 这些字符串是**直接显示在界面上**的 ⇒ 一律纯文本，不带 markdown 记号（`**`/反引号会原样露出）。
MUST_NOT_EXPOSE = [
    "平台管理端口上的 /api/config、/api/admin/*、/api/logs/*（能改配置、能重启、能读日志）",
    "平台静态管理页（能点按钮就是能操作）",
    "任何带 AI 密钥的响应（/api/config 会回打码值，但不要给它出公网的机会）",
]


def _is_loopback(host: str) -> bool:
    h = (host or "").strip().lower()
    return h in ("127.0.0.1", "localhost", "::1") or h.startswith("127.")


def local_bind_host() -> str:
    """本机当前配置的监听地址（只读环境变量，**不探测**）。"""
    return (os.environ.get("AC_HOST") or "0.0.0.0").strip()


def a2a_port(explicit: Optional[int] = None) -> int:
    """要暴露出去的端口：**默认是 a2a 端口**（不是平台管理端口）。"""
    if explicit:
        return int(explicit)
    for key in ("AC4_A2A_PORT", "AC_A2A_PORT"):
        v = (os.environ.get(key) or "").strip()
        if v.isdigit():
            return int(v)
    return 9104


def advise(local_port: Optional[int] = None, peer_url: str = "") -> dict:
    """给出"对端怎么连到我"的判断与建议。

    返回：`{local_port, bind_host, reachable_from_outside, why, suggest[], must_not_expose[]}`
    `reachable_from_outside` 只有三档：`likely` / `unlikely` / `unknown`（**不猜"已通"**）。
    """
    port = a2a_port(local_port)
    host = local_bind_host()
    loop = _is_loopback(host)
    if loop:
        state, why = "unlikely", "本机只监听回环地址（%s）——只有这台机器自己能连到 %d" % (host, port)
    elif host in ("0.0.0.0", "::", ""):
        state, why = ("unknown",
                      "监听在 %s:%d（同局域网可达），但是否公网可达取决于路由器/防火墙/NAT，"
                      "平台无法从本机判断" % (host or "0.0.0.0", port))
    else:
        state, why = ("unknown", "监听在 %s:%d（一个具体地址）——是否能从对端网络到达需实测" % (host, port))

    suggest = [
        "同机 / 同局域网：对端直接填 http://<这台机器的内网IP>:%d（先确认防火墙放行）" % port,
        "跨网络（对端连不到你）：任选一种隧道把 %d 映射出去，拿到一个 http 地址：\n"
        "   · frp（自建 frps，或任何提供 frp 的服务）——本仓库 python tools/gen_frpc.py 可生成客户端配置\n"
        "   · ngrok / cloudflared 之类的反向隧道\n"
        "   · 自有服务器上做反向代理\n"
        "   平台不内置、也不推荐某一家：只要最后能给对端一个 http 地址就行。" % port,
        "映射好之后：在我方 /api/peers 里把 base_url 填成那个地址——只改这一行，其余不动",
    ]
    if peer_url:
        suggest.append("你当前登记的对端地址是 %s（隧道换个端口/域名时，改的就是它）" % peer_url)
    return {
        "local_port": port,
        "bind_host": host,
        "reachable_from_outside": state,
        "why": why,
        "suggest": suggest,
        "must_not_expose": list(MUST_NOT_EXPOSE),
    }


def check_inbound_ready(token_ready: bool, exposed: bool = True) -> dict:
    """暴露前的自查：**鉴权先于暴露**（照搬 a2a 的 fail-closed 口径）。"""
    problems = []
    if exposed and not token_ready:
        problems.append("对端访问需要公司令牌，但本机还没签发过（先 POST /api/companies 登记并签发，"
                        "或让平台生成 secret）——没鉴权不许暴露")
    return {"ok": not problems, "problems": problems,
            "note": "暴露前请确认：只映射 a2a 端口；管理端点不要出公网（见 must_not_expose）"}
