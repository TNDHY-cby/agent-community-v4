# -*- coding: utf-8 -*-
"""对接中心前端（`docking.html` / `docking.js`）的**可执行预检** —— 对照 `design-docs/FE-26` 逐条给结论。

## 这是给谁的

前端界面已**外包**（老板 2026-10-15）。外包方**看不到开发机的文件**，所以这套东西随仓库一起发布
（目录 `外包交接_前端界面/`）。`FE-26` 是设计稿（人读），**本脚本是它的可执行那一半**（机器判）。

用法（在**仓库根目录**跑）：

```bash
python 外包交接_前端界面/check_frontend_spec.py            # 检查当前的 docking.html / docking.js
python 外包交接_前端界面/check_frontend_spec.py --selftest # 自检：用内部夹具证明"它能抓错"
```

退出码：全 PASS = 0；有 FAIL = 1。（`待人工` 的条目不参与退出码 —— 那些是"看得见的手感"，机器判不了。）

## 纪律（它自己也要守）

- **不判"好不好看"**：只判 `FE-26` 写死了的那些**结构/口径**（白名单、三态、只做不讲、四屏能力、两个入口）。
  手感类条目在 `验收清单.md` 里列成"人工走查"，**不假装机器能判**。
- **锚点尽量少**：只要求保留 `DOCK_WRITE_WHITELIST`、`dockPost`、`?ws=` 与 `data-dock-service` 这几处
  （它们同时是"权限面"与"功能模块"的判据），**其余长相完全由实现方定**。
- **探不到就说探不到**：文件缺了/读不了，报 FAIL 并说清路径，不静默跳过。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FE = ROOT / "agent_community" / "frontend"

#: `FE-26` §5 ＋ §12 的白名单（页面里**非 GET** 的请求只能落在这些路径上；带 `*` 的是路径段）
WHITELIST_EXPECT = {
    "/api/harness/pre-register", "/api/harness/probe-register", "/api/harness/register",
    "/api/workshop/*/onboard-members", "/api/workshop/*/recover-scan",
    "/api/workshop/*/member/*/establish-session",
    "/api/workshop/*/member/*/deliver-bridge",
    "/api/workshop/*/member/*/recover",
    "/api/workshop/*/member/*/channel/retire",
    "/api/workshop/*/member/*/channel/establish",
    "/api/harness/bridge-path", "/api/harness/bridge-verify", "/api/harness/auto-connect",
    "/api/assistant/chat",
    "/api/docking/locate", "/api/docking/try-activate",
    "/api/docking/harness/*",
    # ★2026-10-16 §12 补的两条：正文生成（屏 4）与一键脚本激活
    "/api/docking/compose-script", "/api/docking/run-script",
}

#: `FE-26` §2a：第一屏的功能模块（每块＝一件能当场办的事）。`data-dock-service` 用这些 id。
SERVICE_IDS = ("register", "script_activate", "archive", "session_bridge",
               "onboard_recover", "channel", "bridge_helper", "ask_frontdesk")

#: 说明书特征词（`FE-26` §1/§8 判据 5）：正文里出现即 FAIL
MANUAL_WORDS = ("三步", "自测", "怎么验证", "骨架", "教程", "使用说明")

#: 本机私有坐标（反私人定制）：出现即 FAIL。
#: 判据＝"页面里不许出现写死的地址"—— 相对路径之外，任何**带端口的绝对 URL**都算
#: （文档地址 `example.com` / `api.example.com` 除外，那是项目统一的虚拟名）。
LOCAL_PATTERNS = (r"localhost:\d+", r"127\.0\.0\.1:\d+", r"[A-Za-z]:\\\\", r"[A-Za-z]:\\",
                  r"https?://(?!api\.example\.com|example\.com)[^\s'\"`/]+:\d+")


def _read(p: Path) -> tuple:
    if not p.exists():
        return "", "读不到文件：%s" % p
    try:
        return p.read_text(encoding="utf-8"), ""
    except Exception as e:  # noqa: BLE001
        return "", "读不了 %s：%s" % (p, e)


def _js_code(src: str) -> str:
    """去掉注释后的 JS（判"代码里有没有"必须去注释，否则注释里提一句就误报）。

    只剥**行锚定**的块注释与行注释 —— 白名单字符串里含 `/*`，天真剥法会吃掉它（旧守卫踩过两次）。
    """
    src = re.sub(r"(?m)^\s*/\*.*?\*/\s*$", "", src, flags=re.S)
    src = re.sub(r"(?m)^\s*\*.*$", "", src)
    src = re.sub(r"(?m)//.*$", "", src)
    return src


def _whitelist_of(js_code: str) -> set:
    m = re.search(r"DOCK_WRITE_WHITELIST\s*=\s*\[(.*?)\]", js_code, re.S)
    if not m:
        return set()
    return set(re.findall(r"['\"]([^'\"]+)['\"]", m.group(1)))


def _write_fetches(code: str) -> list:
    """代码里**非 GET** 的 fetch 路径（GET 不限，所以必须先判方法再谈白名单）。"""
    out = []
    for m in re.finditer(r"""fetch\(\s*[`'"]([^`'"]+)[`'"]""", code):
        tail = code[m.end():m.end() + 400]
        mm = re.search(r"""method\s*:\s*[`'"](\w+)[`'"]""", tail)
        method = (mm.group(1).upper() if mm else "GET")
        if method != "GET":
            out.append((m.group(1), method))
    return out


def checks(html: str, js: str) -> list:
    """返回 `[{id, title, verdict, reason}]`；verdict ∈ PASS / FAIL / 待人工。"""
    code = _js_code(js)
    out = []

    def add(cid, title, ok, bad_reason, good_reason, manual=False):
        out.append({"id": cid, "title": title,
                    "verdict": "待人工" if manual else ("PASS" if ok else "FAIL"),
                    "reason": good_reason if ok else bad_reason})

    if not html or not js:
        add("X0", "页面文件可读", False,
            "docking.html / docking.js 读不到 —— 后面所有判据都没意义",
            "两个文件都读到了")
        return out

    # ── 权限面（白名单）────────────────────────────────────────────────
    wl = _whitelist_of(code)
    add("W1", "写请求白名单存在且与 FE-26 §5 一致", wl == WHITELIST_EXPECT,
        "多=%s 少=%s" % (sorted(wl - WHITELIST_EXPECT), sorted(WHITELIST_EXPECT - wl)),
        "共 %d 条，与设计稿一致" % len(wl))

    writes = _write_fetches(code)
    outside = [p for p, _m in writes
               if re.sub(r"\$\{[^}]+\}", "*", p) not in WHITELIST_EXPECT and p not in WHITELIST_EXPECT]
    add("W2", "每个**写**请求都落在白名单里", not outside,
        "白名单外的写请求：%s" % outside[:6],
        "共 %d 个写请求，全部在白名单内（GET 不限）" % len(writes))

    add("W3", "没有讨论/工作类入口（`/discuss` 出现即 FAIL）", "/discuss" not in code,
        "代码里出现了 /discuss（对接中心只做接入与激活）", "未出现")

    i = code.find("async function dockPost(")
    add("W4", "写请求只有一条通道（`dockPost`，含白名单闸）", i != -1,
        "找不到 dockPost —— 写请求必须收成一条通道，否则白名单闸形同虚设",
        "找得到 dockPost（唯一写通道）")

    # ── 职责：只做不讲 ──────────────────────────────────────────────────
    hits = [w for w in MANUAL_WORDS if w in html or w in js]
    add("D1", "正文没有说明书特征（只做不讲）", not hits,
        "出现说明书特征词：%s（讲解归 hub.html）" % hits,
        "未出现教程式段落特征词")
    n_hub = html.count("hub.html")
    add("D2", "讲解只留一条去「流程说明」的链接", n_hub <= 1,
        "hub.html 出现 %d 次（最多留一条）" % n_hub, "hub.html 引用 %d 次" % n_hub)

    # ── 第一屏＝功能模块（老板裁定）────────────────────────────────────
    svc = set(re.findall(r"""data-dock-service\s*=\s*["']([^"']+)["']""", html))
    missing = [s for s in SERVICE_IDS if s not in svc]
    add("D3", "第一屏是功能模块（≥6 块，锚点 data-dock-service）",
        len(svc) >= 6 and not missing,
        "缺：%s（现有 %d 块）" % (missing, len(svc)),
        "共 %d 块功能模块" % len(svc))

    # ── 脚本激活工作台（四屏）──────────────────────────────────────────
    for cid, path, desc in (
            ("S1", "/api/docking/keyword-suggestions", "屏1 候选关键词"),
            ("S2", "/api/docking/locate", "屏2 让平台去找"),
            ("S3", "/api/docking/try-activate", "屏3 确认 + 试投一次"),
            ("S4", "/api/docking/harness/", "屏4 保存进档案（脚本正文）"),
            ("S5", "export.ps1", "脚本可导出 .ps1 副本"),
            ("S6", "/api/docking/compose-script", "屏4 生成脚本正文（平台给，别自己编）"),
            ("S7", "/api/docking/run-script", "屏4 之后的一键脚本激活")):
        add(cid, "脚本激活工作台 · %s" % desc, path in code,
            "代码里没有用到 %s" % path, "用到 %s" % path)

    # ── 员工档案详情四块 ───────────────────────────────────────────────
    blocks = [b for b in ("框架性能", "适配模型", "激活方式") if b in js or b in html]
    add("A1", "员工档案「详情」四块可看可改", len(blocks) >= 3,
        "只找到 %s（老板裁定的四块：框架性能/适配模型/具体桥模板/激活方式）" % blocks,
        "找到 %s" % blocks)

    # ── 三态如实 ───────────────────────────────────────────────────────
    honest = ("未核实" in js or "待人工" in js) and ("已自证" in js or "verified" in code)
    add("E1", "三态如实（未自证不许显示完成）", honest,
        "缺少「未核实/待人工」文案，或没有按 verified 分档", "有未核实文案 + verified 分档")
    shown = ("d.error" in js) or ("error" in js and "原因" in js)
    add("E2", "失败要说清（渲染后端 error / 原因）", shown,
        "没有把后端 error 原样显示出来（会出现「点了没反应」的死状态）", "会显示后端 error")

    # ── 两个入口、同一页面 ─────────────────────────────────────────────
    scope = "URLSearchParams" in js and ("'ws'" in js or '"ws"' in js)
    add("G1", "两个入口同一页面（从 URL 读 `?ws=`）", scope,
        "没从 URL 读范围参数（两个入口会退化成同一份总控）", "会从 URL 读 `?ws=`")
    gw = ("DOCK_GLOBAL" in js) and ("innerHTML = ''" in js)
    add("G2", "单间模式不出现工作间选择器", gw,
        "没有「总控才渲染选择器、单间清空」的分支（工作间入口能越权看别的间）",
        "有「总控才渲染、单间清空」的分支")

    # ── 反私人定制 ─────────────────────────────────────────────────────
    bad = []
    for pat in LOCAL_PATTERNS:
        bad += re.findall(pat, html) + re.findall(pat, js)
    add("P1", "没有写死本机坐标（端口/绝对路径）", not bad,
        "出现本机坐标：%s" % bad[:5], "未发现写死的本机坐标")

    # ── 手感类（机器判不了，交给人工走查）─────────────────────────────
    add("M1", "「一眼就会」：第一屏不点任何东西就能看懂能办哪些事", True,
        "", "人工走查：打开页面，5 秒内能否说出「这里能办哪几件事」", manual=True)
    add("M2", "办不成时当场说清「为什么 + 下一步去哪」", True,
        "", "人工走查：故意给错关键词 / 投给不存在的窗口，看它是否说清原因与下一步", manual=True)
    return out


def report(rows: list) -> int:
    width = max(len(r["id"]) for r in rows)
    fails = [r for r in rows if r["verdict"] == "FAIL"]
    for r in rows:
        mark = {"PASS": "PASS", "FAIL": "FAIL", "待人工": "待人工"}[r["verdict"]]
        print("%-*s  %-4s  %s" % (width, r["id"], mark, r["title"]))
        print("%s        %s" % (" " * width, r["reason"]))
    print("-" * 72)
    print("PASS %d / FAIL %d / 待人工 %d"
          % (len([r for r in rows if r["verdict"] == "PASS"]), len(fails),
             len([r for r in rows if r["verdict"] == "待人工"])))
    if fails:
        print("\n必须先修：")
        for r in fails:
            print("  - [%s] %s —— %s" % (r["id"], r["title"], r["reason"]))
    return 1 if fails else 0


# ── 自检：用内部夹具证明"它真的能抓错"（守卫会跑这一段）──────────────────
_GOOD_HTML = "".join(
    ['<a href="hub.html">去流程说明 ↗</a>'] +
    ['<button data-dock-service="%s">服务%s</button>' % (s, i) for i, s in enumerate(SERVICE_IDS)])
_GOOD_JS = """
const DOCK_WRITE_WHITELIST = [%s];
async function dockPost(path, body){ /* 白名单闸 */ return fetch(path, {method:'POST', body}); }
const usp = new URLSearchParams(location.search); const ws = usp.get('ws');
const DOCK_GLOBAL = !ws;
function dockMount(){ if (DOCK_GLOBAL){ pick.innerHTML = ''; } }
const blocks = ['框架性能','适配模型','桥模板','激活方式'];
const tag = '未核实 / 待人工'; const done = '已自证'; const ok = all_verified === true;
const s1 = '/api/docking/keyword-suggestions', s2 = '/api/docking/locate', s3 = '/api/docking/try-activate';
const s4 = '/api/docking/harness/H-1', s5 = 'export.ps1';
const s6 = '/api/docking/compose-script', s7 = '/api/docking/run-script';
const err = d.error;   // 失败要说清
""" % ",".join("'%s'" % p for p in sorted(WHITELIST_EXPECT))

_BAD_HTML = "<h2>对接中心使用说明</h2><p>第一步：三步骨架；自测题见下</p>"
# ⚠️ 违规夹具里**故意**放一个"带端口的绝对地址"（用 TEST-NET-1 文档地址，不是任何人的真机地址）——
#    它既能让 P1 判据真的被抓到，又不会让"交接区不许出现本机坐标"那条守卫误报。
_BAD_JS = "fetch('/api/docking/locate'); fetch('http://192.0.2.10:8080/api/workshop/x');"


def selftest() -> int:
    print("== 自检：合格夹具应当全 PASS，违规夹具应当被抓 ==")
    good = checks(_GOOD_HTML, _GOOD_JS)
    bad = checks(_BAD_HTML, _BAD_JS)
    g_fail = [r["id"] for r in good if r["verdict"] == "FAIL"]
    b_fail = [r["id"] for r in bad if r["verdict"] == "FAIL"]
    print("合格夹具 → FAIL 条目：%s" % (g_fail or "（无）"))
    print("违规夹具 → 被抓条目：%s" % (b_fail or "（无）"))
    ok = True
    if g_fail:
        print("★自检失败：合格夹具被判 FAIL（判据写错了）：%s" % g_fail)
        ok = False
    if not b_fail:
        print("★自检失败：违规夹具一条都没抓到（判据是摆设）")
        ok = False
    print("OK：判据既能放过合格的、也能抓住违规的" if ok else "自检未通过")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true", help="用内部夹具自检本脚本的判据")
    ap.add_argument("--frontend", default="", help="前端目录（默认 agent_community/frontend）")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    fe = Path(a.frontend) if a.frontend else FE
    html, e1 = _read(fe / "docking.html")
    js, e2 = _read(fe / "docking.js")
    if e1 or e2:
        print(e1 or e2)
    print("检查对象：%s" % fe)
    print("-" * 72)
    rows = checks(html, js)
    rc = report(rows)
    print("\n（判据来源：design-docs/FE-26_对接中心前台与脚本激活设计.md §2/§3/§5/§8；"
          "人工走查项见 外包交接_前端界面/验收清单.md）")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
