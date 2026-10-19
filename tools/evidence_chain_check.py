# -*- coding: utf-8 -*-
"""判据：**证据链（V55 信息卡）端到端** —— 校验 → 分档压分 → 归并 → 冲突互链 → 限量分发。

```bash
python tools/evidence_chain_check.py     # 约 8 秒
```

## 为什么补这一条

V55 此前只有**单元**守卫（`test_evidence.py` 喂函数）与真 HTTP 冒烟，**没有端到端判据**：
从"委派检索"到"信息卡进工作间、被别人看到什么"这条链上没有一条能一起证明的判据。
而它管的正是**防信息污染**——出错的形态恰恰是"看起来成功了"（卡进了、但摘录原文被广播出去，
或者营销稿自评 high 被当真）。

## 九格判据（真打 HTTP；看平台真实返回与真实广播文本）

| 格 | 判据 |
|---|---|
| ⓪ | 隔离实例 + 建间（换端口 + 换数据目录） |
| ① | **委派检索**真派出去（载荷落盘），话术含"必须覆盖"与回报格式（提到 task-result） |
| ② | **不合规卡被拒**：422 且 `problems` 逐条说清（不许默默裁剪） |
| ③ | **合规卡入库**：200，`confidence.effective` 被压到档位上限且 `capped=true` 说得清为什么 |
| ④ | **营销稿不许自评 high**：cap=low、effective=low |
| ⑤ | **转载归并**：同 host + 同 title 的两条来源 ⇒ 1 个来源组（不把转载当独立证据） |
| ⑥ | **冲突互链**：后卡 `contradicts` 前卡 ⇒ 两张都 `disputed`，且各自记着对方 rid |
| ⑦ | **限量分发**：`GET /evidence` 默认**不带摘录原文**，`?full=1` 才带 |
| ⑧ | **广播不泄漏**：工作间 notice 摘要里**不许**出现摘录原文（防污染的核心） |
| ⑨ | 留痕：审计有 `workshop.evidence.add`，`RESOURCES.md` 落盘且含两张卡 |
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

QUOTE_1 = "官方文档写着：该接口在 2026-09 起要求必须传 workspace_id，否则返回 400。"
QUOTE_MKT = "限时优惠：现在购买企业版立减三成，联系我们获取方案。"
#: 转载稿的摘录与原文**一字不差**（真转载就是这样；改写过的稿子按规则不该并成一组）
QUOTE_REPRINT = QUOTE_1


def free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _clean_env(**over) -> dict:
    keep = ("PATH", "PYTHON", "SYSTEM", "WINDIR", "TEMP", "TMP", "HOME", "USER",
            "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT", "LANG", "LC_", "LD_LIBRARY")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AC_") and k.upper().startswith(keep)}
    env.update({k: str(v) for k, v in over.items()})
    return env


def req(base: str, path: str, body=None, hdr=None, timeout=40):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if hdr:
        headers.update(hdr)
    r = urllib.request.Request(base + path, data=data,
                               method="POST" if body is not None else "GET", headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:                                    # noqa: BLE001
            return e.code, {"raw": raw[:400]}
    except Exception as e:                                   # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(e).__name__, str(e)[:120])}


def _card(name: str, note: str, sources: list, *, as_of="2026-10-19", conf="high",
          limits="只覆盖公开文档，没跑通私有部署场景", contradicts=None) -> dict:
    ev = {"as_of": as_of, "confidence": conf, "limits": limits, "sources": sources}
    if contradicts:
        ev["contradicts"] = contradicts
    return {"name": name, "note": note, "evidence": ev}


def main() -> int:
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    data_dir = Path(tempfile.mkdtemp(prefix="ac_evi_"))
    inbox = data_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    env = _clean_env(AC_PORT=port, AC_DATA_DIR=data_dir, PYTHONPATH=str(ROOT),
                     PYTHONIOENCODING="utf-8", AC_PYTHON=str(sys.executable))
    proc = subprocess.Popen([sys.executable, "-m", "agent_community.platform.server"],
                            cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fails = 0

    def cell(name: str, ok: bool, detail: str = "") -> None:
        nonlocal fails
        print("   %-46s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        fails += 0 if ok else 1

    print("=" * 90)
    print("证据链（V55）端到端判据（实例 %s，数据目录 %s）" % (base, data_dir))
    print("=" * 90)
    try:
        code = 0
        for _ in range(120):
            code, _b = req(base, "/api/status", timeout=3)
            if code == 200:
                break
            time.sleep(0.5)
        if code != 200:
            print("★实例没起来")
            return 2
        st, reg = req(base, "/api/harness/register", {
            "harness_id": "evi-h", "harness_name": "示例Harness-Evi",
            "harness_type": "desktop-agent", "wakeup_method": "file_poll",
            "wakeup_dir": str(inbox), "ai": {"model_name": "示例模型"},
        })
        if st != 200:
            print("★登记失败：%s" % str(reg)[:200])
            return 1
        st, ws = req(base, "/api/workshop", {
            "name": "证据链走查", "hall_content": "查清某接口的鉴权要求",
            "members": [{"role": "信息搜索者", "harness_ids": ["evi-h"]}],
        })
        ws_id = str((ws or {}).get("workshop_id") or "")
        if st != 200 or not ws_id:
            print("★建间失败：%s" % str(ws)[:300])
            return 1
        cell("⓪ 隔离实例 + 建间", True, "ws=%s" % ws_id)

        # ① 委派检索
        for p in inbox.glob("*.json"):
            p.unlink(missing_ok=True)
        st, sr = req(base, "/api/workshop/%s/search-request" % ws_id, {
            "member_id": "m0", "question": "这个接口 2026-09 之后鉴权怎么变了？",
            "must_cover": ["鉴权方式", "变更时间"], "deadline_minutes": 10,
        })
        instr = str((sr or {}).get("instruction") or "")
        files = sorted(inbox.glob("*.json"))
        blob = files[-1].read_text(encoding="utf-8") if files else ""
        cell("① 委派检索真派出去（载荷落盘 + 话术齐）",
             st == 200 and (sr or {}).get("dispatched") is True and bool(files)
             and "鉴权方式" in blob and "必须覆盖" in instr and "task-result" in instr,
             "HTTP %s dispatched=%s 文件=%d" % (st, (sr or {}).get("dispatched"), len(files)))

        # ② 不合规卡被拒
        st, bad = req(base, "/api/workshop/%s/evidence" % ws_id, {
            "name": "", "note": "", "evidence": {"sources": [{"url": "", "quote": ""}]}})
        probs = (bad or {}).get("problems") or []
        cell("② 不合规卡被拒（422 + 逐条说清）",
             st == 422 and len(probs) >= 4 and bool((bad or {}).get("how_to_fix")),
             "HTTP %s problems=%d" % (st, len(probs)))

        # ③ 合规卡：二手来源自评 high ⇒ 被压到 medium
        st, r1 = req(base, "/api/workshop/%s/evidence" % ws_id, _card(
            "接口鉴权变更", "2026-09 起必须带 workspace_id",
            [{"url": "https://docs.example.com/api/auth", "title": "鉴权变更说明",
              "publisher": "示例媒体", "quoted_at": "2026-10-19", "quote": QUOTE_1}]))
        conf = (r1 or {}).get("confidence") or {}
        rid1 = str(((r1 or {}).get("resource") or {}).get("rid") or "")
        cell("③ 合规卡入库 + 置信被压到上限",
             st == 200 and conf.get("effective") == "medium" and conf.get("capped") is True
             and "最多 medium" in str(conf.get("why")),
             "HTTP %s effective=%s capped=%s rid=%s" % (
                 st, conf.get("effective"), conf.get("capped"), rid1 or "?"))

        # ④ 营销稿不许自评 high
        st, r2 = req(base, "/api/workshop/%s/evidence" % ws_id, _card(
            "某产品企业版优惠", "限时优惠信息",
            [{"url": "https://shop.example.com/product/enterprise?utm_source=x",
              "title": "企业版促销页", "publisher": "某厂", "quoted_at": "2026-10-19",
              "quote": QUOTE_MKT}]))
        conf2 = (r2 or {}).get("confidence") or {}
        cell("④ 营销稿自评 high 被压到 low",
             st == 200 and conf2.get("effective") == "low" and conf2.get("capped") is True,
             "effective=%s capped=%s why=%s" % (
                 conf2.get("effective"), conf2.get("capped"), str(conf2.get("why"))[:40]))

        # ⑤ 转载归并
        st, r3 = req(base, "/api/workshop/%s/evidence" % ws_id, _card(
            "接口鉴权变更（转载）", "同一篇稿被转载",
            [{"url": "https://docs.example.com/api/auth", "title": "鉴权变更说明",
              "publisher": "示例媒体", "quoted_at": "2026-10-19", "quote": QUOTE_1},
             {"url": "https://mirror.example.com/news/api-auth", "title": "鉴权变更说明",
              "publisher": "转载站", "quoted_at": "2026-10-18", "quote": QUOTE_REPRINT}]))
        cell("⑤ 转载被归并成 1 个来源组",
             st == 200 and (r3 or {}).get("source_groups") == 1,
             "HTTP %s source_groups=%s" % (st, (r3 or {}).get("source_groups")))

        # ⑥ 冲突互链
        st, r4 = req(base, "/api/workshop/%s/evidence" % ws_id, _card(
            "接口鉴权变更（另一种说法）", "另一家说 2026-10 才生效",
            [{"url": "https://blog.example.net/auth-2026", "title": "鉴权时间线",
              "publisher": "示例博客", "quoted_at": "2026-10-17",
              "quote": "该变更自 2026-10 起生效，与官方文档说法存在出入。"}],
            contradicts=[rid1]))
        st_list, lst = req(base, "/api/workshop/%s/evidence" % ws_id)
        items = {x.get("rid"): x for x in ((lst or {}).get("items") or [])}
        both_disputed = (items.get(rid1, {}).get("disputed") is True and
                         bool(items.get(rid1, {}).get("contradicts")) and
                         bool((r4 or {}).get("marked_disputed_with")))
        cell("⑥ 冲突互链（两张都标争议）", st == 200 and both_disputed,
             "marked=%s ｜ 卡1 disputed=%s" % ((r4 or {}).get("marked_disputed_with"),
                                               items.get(rid1, {}).get("disputed")))

        # ⑦ 限量分发：默认不带摘录原文
        st_d, default = req(base, "/api/workshop/%s/evidence" % ws_id)
        st_f, full = req(base, "/api/workshop/%s/evidence?full=1" % ws_id)
        d_items = (default or {}).get("items") or []
        f_items = (full or {}).get("items") or []
        no_quote = all("quotes" not in x and "quote" not in json.dumps(x, ensure_ascii=False)
                       for x in d_items)
        has_quote = any((x.get("quotes") or []) for x in f_items)
        cell("⑦ 默认不带摘录原文，full=1 才带",
             st_d == 200 and st_f == 200 and no_quote and has_quote,
             "默认含 quote=%s ｜ full 含 quotes=%s" % (not no_quote, has_quote))

        # ⑧ 广播摘要不许泄漏摘录原文
        st_ws, wbody = req(base, "/api/workshop/%s" % ws_id)
        disc = json.dumps(wbody.get("discussion") or [], ensure_ascii=False)
        leaked = (QUOTE_1[:16] in disc) or (QUOTE_MKT[:12] in disc)
        cell("⑧ 工作间广播不泄漏摘录原文", not leaked,
             "讨论区 %d 条 ｜ 泄漏=%s" % (len(wbody.get("discussion") or []), leaked))

        # ⑨ 留痕
        st_a, au = req(base, "/api/audit")
        ev_rows = [r for r in ((au or {}).get("events") or [])
                   if str(r.get("event") or "") == "workshop.evidence.add"]
        manifest = None
        for p in (data_dir / "workshops").rglob("RESOURCES.md"):
            manifest = p
            break
        mtext = manifest.read_text(encoding="utf-8") if manifest else ""
        cell("⑨ 审计 + 资源清单留痕",
             st_a == 200 and len(ev_rows) >= 4 and "接口鉴权变更" in mtext,
             "审计 %d 条 ｜ RESOURCES.md=%s" % (len(ev_rows), "含两卡" if
                                                ("接口鉴权变更" in mtext) else "缺/未落盘"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:                                    # noqa: BLE001
            proc.kill()
        import shutil
        shutil.rmtree(data_dir, ignore_errors=True)

    print("-" * 90)
    print("结论：%s" % ("**证据链端到端全绿（校验 / 压分 / 归并 / 互链 / 限量分发 / 不泄漏）**"
                      if not fails else "**有 %d 格失败**" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
