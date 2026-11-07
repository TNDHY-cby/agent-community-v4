# -*- coding: utf-8 -*-
"""脱敏（**一处真相**）：把可能带真实路径/凭据的文本或对象洗干净。

## 为什么单独一个模块

两个地方都要用，而且它们**分属两个世界**：
- `mcp_server.py`（MCP 服务端，**零第三方依赖 + stdout 必须纯净**）：资源面不能把 `/api/status` 里的
  `pipe_dir` 这类真实路径发出去；
- `platform/progress.py`（工作过程上报）：**对方写的自由文本**可能带真实路径/密钥，落盘前要洗。

`mcp_server` **不能** import `platform.*`（`platform/__init__.py` 会拽入 FastAPI 并污染 stdout，
见它的文件头注释）⇒ 所以这个模块放在**顶层包**，两边都从它取，别各写一份正则。

## 洗什么

| 目标 | 规则 | 例子 |
|---|---|---|
| 敏感**键名** | 键里带 `token/secret/password/api_key/credential` ⇒ 值换成 `<hidden>` | `{"agent_token": "…"}` → `"<hidden>"` |
| Windows 盘符路径 | `X:\a\b` | `D:\some\where\pipes` → `<path>` |
| POSIX 深路径 | `/a/b/…`（≥2 段）；**URI 不算**（`ac://x/y`、`https://h/a/b` 都保留） | `/home/someone/x` → `<path>` |
| 密钥串 | `sk-` 开头的长串 | `sk-abcdef…` → `<hidden>` |

⚠️ URI 豁免的实现细节：POSIX 正则的 lookbehind 排除 `:` `/` 与词字符 ——
`ac://workshop/x/y` 里 `/x` 前面是 `/`、`/y` 前面是字母 ⇒ 都不匹配（不然把资源 URI 洗成 `<path>` 就废了）。
"""
from __future__ import annotations

import re

#: 键名里带这些的一律不出（令牌、密钥、凭据）
SENSITIVE_KEY_RE = re.compile(r"(token|secret|password|passwd|api[_-]?key|credential)", re.I)
#: Windows 盘符路径
WIN_PATH_RE = re.compile(r"[A-Za-z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)*[^\\/:*?\"<>|\r\n]*")
#: POSIX 深路径（**排除 URI**：前面是 `:` `/` 或词字符就不算）
POSIX_PATH_RE = re.compile(r"(?<![:\w/])/(?:[^\s/\"']+/){2,}[^\s\"',;)}\]]*")
#: 常见密钥前缀
SECRET_TOKEN_RE = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}")

HIDDEN = "<hidden>"
PATH_TAG = "<path>"


def scrub_text(s: str) -> str:
    """洗一段文本（不清空、不改其它内容）。非字符串原样返回。"""
    if not isinstance(s, str):
        return s
    out = WIN_PATH_RE.sub(PATH_TAG, s)
    out = POSIX_PATH_RE.sub(PATH_TAG, out)
    out = SECRET_TOKEN_RE.sub(HIDDEN, out)
    return out


def scrub(obj):
    """递归洗：dict 的敏感**键名**换值、list 逐项、str 洗路径与密钥。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if SENSITIVE_KEY_RE.search(str(k)):
                out[k] = HIDDEN
            else:
                out[k] = scrub(v)
        return out
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    if isinstance(obj, str):
        return scrub_text(obj)
    return obj
