# -*- coding: utf-8 -*-
"""把 AI 调用失败翻译成**人话 + 下一步**（一处真相）。

## 为什么单独一层

真机上陌生人第一次跑，第 4 步看到的是：

```
✗ 这次没有产生有效结果（AI 调用失败）:
[Error: [Errno 11001] getaddrinfo fail
```

这句话对用户等于没说：**他不知道要干什么**。而 `getaddrinfo fail` 其实只意味着
"模型地址解析不到"——多半是没配，或地址填错。

所以：**机器错误在显示前先过这里**，输出 `{kind, human, fix}`。
`human` 是一句人话（说清发生了什么），`fix` 是一步到位的下一步（说清去哪填什么）。

## 纪律

- **只认网络层 / HTTP 层的通性**，**不绑定任何模型服务商**（不认品牌名、不认专属错误码）；
- 认不出来就**如实说认不出**（返回 `kind="unknown"` + 原始文本），**不许编一句像模像样的解释**；
- 这层只做**翻译**，不改任何判定逻辑（判定仍在 `ai_provider.ai_is_error()` 那边）。
"""
from __future__ import annotations

import re
from typing import Optional

#: 每个类别：`(kind, 正则, 人话, 下一步)`
_RULES = (
    # ── 网络层 ────────────────────────────────────────────
    ("dns",
     r"getaddrinfo fail|name or service not known|nodename nor servname|"
     r"errno 11001|temporary failure in name resolution|no such host",
     "模型地址解析不到（域名找不到）",
     "先看 AI 地址有没有拼错（常见：多打了路径、少了端口）；本机能上网吗？"
     "地址在哪改：对接中心 → AI 设置（或环境变量 AC_AI_BASE_URL / 配置文件里的 ai.base_url）"),
    ("conn_refused",
     r"connection refused|errno 111|actively refused|connectex|connecterror|"
     r"connection error|newconnectionerror|failed to establish",
     "连不上模型服务（对方没有接受连接）",
     "多半是地址/端口写错，或那个服务没起。先确认 AI 地址能打开（浏览器或 curl 试一下），"
     "再确认为模型服务写的端口；如果填的是本机地址，看那个服务在不在跑"),
    ("timeout",
     r"timed out|timeout|errno 60|errno 10060|readtimeout|connecttimeout|pooltimeout",
     "连模型时超时了",
     "网络慢或地址不通。先确认地址能打开（浏览器/curl 试一下），再看是否需要代理"),
    ("network_other",
     r"errno (10051|10054|10065|104|110)|network is unreachable|ssl|certificate|"
     r"connection reset|eof occurred",
     "连模型时网络断了（或被证书拦下）",
     "检查网络与证书；如果用的是自签/内网地址，确认平台信任它"),

    # ── HTTP 层（云服务通性，不针对某一家）──────────────
    ("auth",
     r"http 401|unauthorized|invalid[_ ]?api[_ ]?key|invalid token|authentication",
     "模型服务说：密钥不对（401）",
     "把 API 密钥重新粘一遍（注意不要带空格/换行）；确认密钥没过期。"
     "在哪改：对接中心 → AI 设置"),
    ("forbidden",
     r"http 403|forbidden|permission denied|not allowed",
     "模型服务说：没权限（403）",
     "这个密钥不能调这个模型。换一个模型名，或换一个有权限的密钥"),
    ("quota",
     r"http 402|insufficient|balance|quota|billing|arrears",
     "模型服务说：余额/额度不够（402）",
     "去模型服务那边充值或换一个密钥；这一条不是平台的问题"),
    ("rate_limit",
     r"http 429|rate limit|too many requests|throttle",
     "模型服务说：请求太频繁（429）",
     "等一会儿再试；如果经常这样，说明额度档位太低"),
    ("not_found",
     r"http 404|not found|no such model|model_not_found|unknown model",
     "模型服务说：地址或模型名找不到（404）",
     "常见原因：地址少了 `/v1` 之类的路径，或模型名拼错。检查 AI 地址与模型名"),
    ("server_error",
     r"http 5\d\d|internal server error|bad gateway|service unavailable|gateway timeout",
     "模型服务那边出错了（5xx）",
     "不是你的配置问题，稍后再试；持续出现就换一个模型服务地址"),

    # ── 未配置（最常见）──────────────────────────────────
    ("not_configured",
     r"api\.example\.com|未配置|没配置|not configured|placeholder|占位",
     "平台还没有配置可用的 AI 模型",
     "去「对接中心 → AI 设置」填上模型地址、模型名、密钥（三样）；"
     "填完点验证，看到通过再提交任务"),
)

#: 编译一次（正则不重复编译）
_COMPILED = tuple((k, re.compile(p, re.I), h, f) for (k, p, h, f) in _RULES)


def explain(text: str) -> dict:
    """把一段 AI 失败文本翻译成 `{kind, human, fix, raw}`。

    认不出时 `kind="unknown"`，`human` 说明"没认出来"，`fix` 让用户把原文带给管理员。
    """
    raw = (text or "").strip()
    if not raw:
        return {"kind": "empty", "human": "AI 没有返回任何内容",
                "fix": "先确认 AI 设置已通过验证；如果验证通过仍为空，可能是模型名不对", "raw": ""}
    for kind, rx, human, fix in _COMPILED:
        if rx.search(raw):
            return {"kind": kind, "human": human, "fix": fix, "raw": raw[:400]}
    return {"kind": "unknown", "human": "AI 调用失败（具体原因没认出来）",
            "fix": "把下面这段原文发给平台管理员；同时先检查 AI 设置里的地址/模型名/密钥三样是否都填了",
            "raw": raw[:400]}


def humanize(text: str) -> str:
    """一行式：`人话 —— 下一步`（给 CLI/界面直接显示）。"""
    e = explain(text)
    return "%s —— %s" % (e["human"], e["fix"])


def with_explanation(text: str, indent: str = "  ") -> str:
    """多行式：人话 + 下一步 + 原始文本（原始文本缩进到后面，便于排查但不吓人）。"""
    e = explain(text)
    lines = ["%s原因：%s" % (indent, e["human"]), "%s怎么办：%s" % (indent, e["fix"])]
    if e["raw"]:
        lines.append("%s原始信息（排查用）：%s" % (indent, e["raw"]))
    return "\n".join(lines)


def has_bare_tech_error(text: str) -> Optional[str]:
    """检查一段**显示给用户**的文本里有没有裸的技术串（判据用）。

    返回命中的那个串（没命中返回 None）。目标是：**显示层不许只有技术串**。
    """
    if not text:
        return None
    for kind, rx, _h, _f in _COMPILED:
        m = rx.search(text)
        if m:
            return m.group(0)
    return None
