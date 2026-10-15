# -*- coding: utf-8 -*-
"""**旧注册页不许再当接入入口**（`V34` §5 + 规则 20 整体同步）。

背景（2026-10-15 老板一句「记得同步更新」逼出来的）：我把 `register.html` 转成**只读镜像**之后，
**全仓还有 6 处用户可见的地方在指向它**，其中一处是**我自己刚写的**空状态引导 ——
用户照着点会到一个**写操作已被闸住**的页面（"看起来能登记，点了没反应"）。

这条守卫把它钉死：`agent_community/` 下的**代码与活前端**里出现的 `/register.html`
必须在下面的**白名单**里（每条都要说明为什么合法）；否则判红。
`design-docs/**` 与历史文档不在扫描面（那里写的是历史，不是现行入口）。

⚠️ 刻意**不扫** `frontend/verify/**`：那是 dev 侧的历史验收脚本（它们当年就是验旧页的），
在报告里如实列为"已失效的旧页验收"，而不是硬改它们 —— 改它们等于伪造历史证据。
"""
from __future__ import annotations

import os
import re
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

PKG = os.path.join(_ROOT, "agent_community")
SKIP_DIRS = {"__pycache__", "data", ".pytest_cache"}
#: 允许出现 `/register.html` 的地方（**每条都要有理由**）
ALLOW = {
    # 镜像页自己：它就是那个页面
    os.path.join("frontend", "register.html"): "旧页本体（只读镜像）",
    # 对接中心里那个**明确标为只读镜像**的回查入口
    os.path.join("frontend", "docking.html"): "对接中心里标着「接入模板（只读镜像）」的回查链接",
    # dev 侧历史验收脚本：它们是当年验旧页的，**不改**（改了等于伪造历史证据）
    os.path.join("frontend", "verify"): "dev 侧历史验收脚本（详见 CHANGELOG 说明）",
    # 测试自己：模板契约守卫必须读那一页（内容契约是给模板用的）
    os.path.join("tests", "test_template_contract.py"): "模板契约守卫（读旧页内容）",
    os.path.join("tests", "test_v44_batch1_fixes.py"): "V44 守卫（读旧页内容）",
    os.path.join("tests", "test_onboarding_truthfulness.py"): "接入诚实性守卫（读旧页内容）",
    os.path.join("tests", "test_bridge_catalog.py"): "桥型目录守卫（读旧页内容）",
    os.path.join("tests", "test_no_stale_register_entry.py"): "本守卫自己",
    # 平台代码里只是**注释**提到历史（不是入口）
    os.path.join("platform", "server.py"): "注释：记录 V-14 那次 content/message 别名事故",
    os.path.join("platform", "bridge_catalog.py"): "注释：记录旧话术写的是旧模型",
    os.path.join("platform", "tier_guidance.py"): "注释：记录自测题 A~E 的历史",
    os.path.join("platform", "routers", "company_admin.py"): "文档字符串：说明远程接入弹窗的来源",
    os.path.join("platform", "routers", "protocols.py"): "注释：说明协议面板曾被谁渲染",
}

_HIT = re.compile(r"/register\.html")


def _allowed(rel: str) -> bool:
    """`rel` 是否在白名单里。

    ⚠️ 白名单的键**既可能是文件也可能是目录**（如 `frontend/verify`）——
    第一版只做 `rel in ALLOW` 精确匹配，目录键永远匹配不上，
    于是 9 个 dev 验收脚本全部被误报。目录键按**前缀**匹配。
    """
    rel = rel.replace("\\", "/")
    for key in ALLOW:
        k = key.replace("\\", "/")
        if rel == k or rel.startswith(k + "/"):
            return True
    return False


def _scan():
    hits = []
    for root, dirs, files in os.walk(PKG):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if not f.endswith((".py", ".html", ".js")):
                continue
            p = os.path.join(root, f)
            rel = os.path.relpath(p, PKG)
            if _allowed(rel):
                continue
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh, 1):
                        if _HIT.search(line):
                            hits.append((rel.replace("\\", "/"), i, line.strip()[:110]))
            except OSError:
                continue
    return hits


def test_no_stale_register_entry_pointers():
    """活代码/活前端里不许再有指向旧注册页的**入口**（白名单里那些除外）。"""
    hits = _scan()
    assert not hits, (
        "这些地方还在把用户导向已下线的 /register.html（它现在是只读镜像，写操作被闸住）：\n"
        + "\n".join("  %s:%d  %s" % h for h in hits)
        + "\n⇒ 改成 /docking.html（对接中心），或把理由加进本守卫的 ALLOW 白名单。"
    )


def test_allowlist_entries_still_exist():
    """白名单不许留死条目（文件删了/改名了要一起清）—— 否则白名单会越长越像摆设。"""
    missing = []
    for rel, _why in ALLOW.items():
        p = os.path.join(PKG, rel)
        if not (os.path.exists(p) or os.path.exists(p + ".py")):
            missing.append(rel)
    assert not missing, "白名单里有已不存在的条目，请同步清理：%s" % missing


def test_docking_is_the_entry_everywhere_users_look():
    """反面对照：**该指向对接中心的地方**确实指了（否则守卫只防了"不该指"，没保证"该指"）。"""
    import pathlib
    need = {
        "cli.py": "/docking.html",
        os.path.join("frontend", "index.html"): "/docking.html",
        os.path.join("frontend", "workshop.html"): "/docking.html",
        os.path.join("frontend", "hub.js"): "/docking.html",
        os.path.join("platform", "routers", "workshops_assign.py"): "/docking.html",
    }
    missing = []
    for rel, needle in need.items():
        p = pathlib.Path(PKG, rel)
        if not p.exists() or needle not in p.read_text(encoding="utf-8", errors="replace"):
            missing.append(rel)
    assert not missing, "这些用户会看到的地方还没指向对接中心：%s" % missing
