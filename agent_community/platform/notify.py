# -*- coding: utf-8 -*-
"""通知渠道（V80 §B）：webhook（出站 POST）与 SMTP 邮件。

为什么只有这两种：飞书/钉钉/企微/Telegram 都要**申请应用或 Bot**（要账号、要审核）⇒ 本版不做；
而它们的"自定义机器人"本质就是 webhook —— 用户填一个 URL 就能用，不需要我们做申请流程。

凭据只从环境变量读，**任何端点都不回显明文**。
"""
from __future__ import annotations

import json
import os
import smtplib
import urllib.request
from email.message import EmailMessage

WEBHOOK_ENV = ("AC_WEBHOOK_URL",)
SMTP_ENV = ("AC_SMTP_HOST", "AC_SMTP_PORT", "AC_SMTP_FROM", "AC_SMTP_TO")


def webhook_cfg() -> dict:
    url = (os.environ.get("AC_WEBHOOK_URL") or "").strip()
    headers = {}
    raw = (os.environ.get("AC_WEBHOOK_HEADERS") or "").strip()
    if raw:
        try:
            headers = json.loads(raw)
        except Exception:                                    # noqa: BLE001 — 头坏了就当没配，测的时候会报
            headers = {}
    return {"url": url, "headers": headers if isinstance(headers, dict) else {}}


def smtp_cfg() -> dict:
    return {
        "host": (os.environ.get("AC_SMTP_HOST") or "").strip(),
        "port": (os.environ.get("AC_SMTP_PORT") or "587").strip(),
        "user": (os.environ.get("AC_SMTP_USER") or "").strip(),
        "password": (os.environ.get("AC_SMTP_PASSWORD") or ""),
        "from": (os.environ.get("AC_SMTP_FROM") or "").strip(),
        "to": (os.environ.get("AC_SMTP_TO") or "").strip(),
        "tls": (os.environ.get("AC_SMTP_TLS") or "1").strip() not in ("0", "false", "off"),
    }


def configured() -> dict:
    """**只说配没配，不回显任何凭据**。"""
    w = webhook_cfg()
    s = smtp_cfg()
    missing_smtp = [k for k in ("host", "from", "to") if not s[k]]
    return {
        "webhook": {"configured": bool(w["url"]),
                    "why": "" if w["url"] else "没配 AC_WEBHOOK_URL"},
        "smtp": {"configured": not missing_smtp,
                 "why": "" if not missing_smtp else "缺：" + "、".join("AC_SMTP_" + m.upper() for m in missing_smtp)},
    }


def send_webhook(text: str, timeout: float = 10.0) -> dict:
    cfg = webhook_cfg()
    if not cfg["url"]:
        return {"ok": False, "why": "没配 AC_WEBHOOK_URL（用户给自己的 URL 即可，不需要申请任何账号）"}
    body = json.dumps({"text": str(text)[:2000]}).encode("utf-8")
    req = urllib.request.Request(cfg["url"], data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          **{str(k): str(v) for k, v in cfg["headers"].items()}})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code = int(getattr(r, "status", 0) or r.getcode() or 0)
            tail = (r.read(500).decode("utf-8", "replace") or "")[:200]
        return {"ok": 200 <= code < 300, "status": code, "tail": tail}
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "why": "%s: %s" % (type(e).__name__, str(e)[:160])}


def send_mail(subject: str, text: str, timeout: float = 15.0) -> dict:
    cfg = smtp_cfg()
    miss = [k for k in ("host", "from", "to") if not cfg[k]]
    if miss:
        return {"ok": False, "why": "缺 SMTP 配置：" + "、".join("AC_SMTP_" + m.upper() for m in miss)}
    msg = EmailMessage()
    msg["Subject"] = str(subject)[:120] or "合作社通知"
    msg["From"] = cfg["from"]
    msg["To"] = cfg["to"]
    msg.set_content(str(text)[:4000])
    try:
        with smtplib.SMTP(cfg["host"], int(cfg["port"]), timeout=timeout) as s:
            if cfg["tls"]:
                s.starttls()
            if cfg["user"]:
                s.login(cfg["user"], cfg["password"])
            s.send_message(msg)
        return {"ok": True}
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "why": "%s: %s" % (type(e).__name__, str(e)[:160])}
