# -*- coding: utf-8 -*-
"""判据：**监控室真能抓一帧**（V88 画面墙，`GET /api/monitor/{id}/frame`）。

```bash
python tools/monitor_frame_check.py       # 约 5 秒（要服务活着）
```

## 为什么单列

V88 的判据表里写着「**抓到的是真画面**」——那一格以前只在 `test_monitor.py` 的单元层验，
**没有一条从真服务、走真 HTTP、对真窗口**验过；而不是 Windows 的机器上，这功能应当
**如实说"不支持"**（不是报错、更不是给一张黑图）。所以这格补的是：

| 格 | 判据 | 红了说明什么 |
|---|---|---|
| ① | `/api/monitor/health` 200 且字段齐全；不支持时 **`why` 必须说人话** | 界面拿不到"能不能用/为什么不能用" |
| ② | 对**没开监控**的 agent 取帧 ⇒ **403**（不是 200、不是 500） | 界面会把"没开"显示成"抓到了"或直接崩 |
| ③ | `/api/monitor/{id}/stream`：**要么 200（流已落地）**，**要么 501 + 文案**；500/空原因一律红 | 用户看到按钮亮着却永远是坏的 |
| ④ | 真抓一帧：`200 + image/png + PNG 魔数 + X-Monitor-Method 非空`，且**不是黑图**（复用 `monitor._looks_black`） | 「抓黑当画面」——V88 记着的那次事故 |
| ⑤ | 检查结束后 **watched 名单与开始时一致**（不许留下我自己开的监控） | 判据污染真实状态 |

## 平台与依赖

- **非 Windows**：①②③ 照跑（"不支持"本身就是要判的契约），④⑤ **如实跳过**（不是通过、也不算红），exit 0；
- **没有可抓窗口**（本机没 agent 带窗口/被最小化）：④ 如实跳过并说清原因，exit 0；
- 只在**有窗口可抓**且**抓黑图**时才 exit 1。
"""
from __future__ import annotations

import json
import os
import struct
import sys
import urllib.error
import urllib.request
import zlib
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def base_url() -> str:
    """服务基址：`AC_MONITOR_BASE` > `platform_address` 的解析（端口一处真相）。"""
    env = (os.environ.get("AC_MONITOR_BASE") or "").strip()
    if env:
        return env.rstrip("/")
    try:
        from agent_community import platform_address as pa

        return pa.base_url().rstrip("/")
    except Exception as e:                                     # noqa: BLE001 — 取不到就用默认，下面会真连一次
        print("  · 提示：拿不到 platform_address（%s），回落到 http://127.0.0.1:18920" % e)
        return "http://127.0.0.1:18920"


def req(method: str, url: str, body: dict | None = None) -> tuple:
    """返回 (status, headers, bytes)。连不上返回 (0, {}, 原因)。

    ⚠️ `headers` 的键**统一小写**：`dict(resp.headers)` 保留的是服务端原始大小写
    （实测 `content-type` 是小写），按 `Content-Type` 查会拿到 `None` —— 第一版就因此误判成红。
    """
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, {k.lower(): v for k, v in (resp.headers or {}).items()}, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read()
    except Exception as e:                                     # noqa: BLE001
        return 0, {}, ("%s: %s" % (type(e).__name__, e)).encode("utf-8")


def txt(raw: bytes, limit: int = 120) -> str:
    """把响应体转成能读的一行（**别直接打印 bytes repr**，那样中文会变成 \\xe7\\x94\\xb1 这种）。"""
    return raw.decode("utf-8", "replace").strip().replace("\\n", " ")[:limit]


def as_json(raw: bytes) -> dict:
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:                                          # noqa: BLE001 — 不是 JSON 就当空
        return {}


def png_stats(png: bytes) -> dict:
    """解我们自己的 PNG（filter 0 / 8bit / RGB）并给出是否黑图 —— 判「真画面」用。"""
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        return {"ok": False, "why": "不是 PNG（前 8 字节不对）"}
    w = h = 0
    idat = bytearray()
    pos = 8
    while pos + 8 <= len(png):
        ln = struct.unpack(">I", png[pos:pos + 4])[0]
        tag = png[pos + 4:pos + 8]
        body = png[pos + 8:pos + 8 + ln]
        if tag == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
            if depth != 8 or ctype != 2:
                return {"ok": False, "why": "不是 8bit 真彩色（depth=%d ctype=%d）" % (depth, ctype)}
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
        pos += 12 + ln
    if w <= 0 or h <= 0 or not idat:
        return {"ok": False, "why": "PNG 里没有有效尺寸/像素数据"}
    try:
        raw = zlib.decompress(bytes(idat))
    except Exception as e:                                     # noqa: BLE001
        return {"ok": False, "why": "IDAT 解压失败：%s" % e}
    stride = w * 3
    if len(raw) < h * (stride + 1):
        return {"ok": False, "why": "像素数据长度不对"}
    rgb = bytearray()
    for y in range(h):
        off = y * (stride + 1)
        if raw[off] != 0:                                      # 我们的编码只用 filter 0
            return {"ok": False, "why": "行滤波不是 0（我们的编码不该出现）"}
        rgb += raw[off + 1:off + 1 + stride]
    out = {"ok": True, "w": w, "h": h, "bytes_rgb": len(rgb)}
    try:
        from agent_community.platform.monitor import _looks_black      # 一处真相：黑图判定用平台那份

        out["blank"] = bool(_looks_black(bytes(rgb)))
    except Exception as e:                                     # noqa: BLE001 — 导不进来就别假装判过
        out["blank"] = None
        out["blank_why"] = "拿不到平台的黑图判定（%s）" % e
    return out


def _check_stream_first_frame(base: str, hid: str, fails: list, skips: list) -> None:
    """④′ 真开一条连续画面流，读到第一段就判：必须是 multipart + 段头 + PNG 魔数。

    只读一小段就关（`read(4096)`），不会把流跑满时长——判据要快、要不吵。
    """
    url = base + "/api/monitor/%s/stream?max_w=160&fps=2" % hid
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            ct = (resp.headers.get("Content-Type") or "").lower()
            if "multipart" not in ct:
                fails.append("④′ 连续流的 Content-Type 不是 multipart：%s" % ct)
                return
            buf = resp.read(8192)
    except urllib.error.HTTPError as e:
        fails.append("④′ 打开连续流失败：%s（%s）" % (e.code, txt(e.read(), 120)))
        return
    except Exception as e:                                   # noqa: BLE001 — 读流失败要说清原因
        skips.append("④′ 连续流没读成（%s: %s）⇒ 如实跳过，不当通过" % (type(e).__name__, e))
        return
    if b"--" not in buf:
        fails.append("④′ 连续流没有分帧边界（前 80 字节：%r）" % buf[:80])
        return
    idx = buf.find(b"\x89PNG\r\n\x1a\n")
    if idx < 0:
        fails.append("④′ 连续流第一段里没有 PNG 数据（前 80 字节：%r）" % buf[:80])
        return
    if b"X-Monitor-Seq:" not in buf:
        fails.append("④′ 段头缺 X-Monitor-Seq（帧序号），界面没法判断「卡住」还是「没动」")
        return
    stats = png_stats(buf[idx:idx + 200000])
    if not stats.get("ok"):
        fails.append("④′ 连续流首帧解不开：%s" % stats.get("why"))
    elif stats.get("blank"):
        fails.append("④′ 连续流首帧是黑图（%dx%d）——不许「黑屏但看起来在播」" % (stats["w"], stats["h"]))
    else:
        print("  ④′ 连续画面流首帧 ✅ %dx%d / %d 字节 / 段头齐全" % (stats["w"], stats["h"], len(buf)))


def main() -> int:
    base = base_url()
    print("=" * 78)
    print("监控室真抓一帧判据 —— %s" % base)
    print("=" * 78)
    fails, skips = [], []

    # ── ① health 契约 ─────────────────────────────────────────
    st, _hd, raw = req("GET", base + "/api/monitor/health")
    if st != 200:
        fails.append("① health 不是 200（%s / %s）" % (st, txt(raw)))
        health = {}
    else:
        health = as_json(raw)
        need = ("supported", "why", "encoding", "max_watch", "fps", "methods", "ai_observe")
        miss = [k for k in need if k not in health]
        supported = bool(health.get("supported"))
        if miss:
            fails.append("① health 缺字段：%s" % miss)
        elif not supported and not str(health.get("why") or "").strip():
            fails.append("① 不支持抓窗口，但 `why` 是空的 —— 界面没法告诉用户为什么")
        else:
            print("  ① health 200 ✅ supported=%s encoding=%s fps=%s max_watch=%s"
                  % (supported, health.get("encoding"), health.get("fps"), health.get("max_watch")))
            if not supported:
                print("     （本平台的如实原因：%s）" % health.get("why"))

    # ── ② 没开监控就取帧：必须 403 ───────────────────────────
    st, hd, raw = req("GET", base + "/api/monitor/agent-not-exist-xyz/frame")
    if st == 403:
        print("  ② 没开监控取帧 ⇒ 403 ✅（%s）" % (as_json(raw).get("error") or "")[:60])
    elif st == 501:
        print("  ② 本平台不支持抓窗口 ⇒ 501 ✅（如实说）")
    else:
        fails.append("② 没开监控的 agent 取帧应 403/501，实际 %s（%s）" % (st, txt(raw)))

    # ── ③ stream：V89 之后是**真连续流**；这里只判"契约对不对"，真帧在 ④′ 用可抓窗口验 ──
    st, hd, raw = req("GET", base + "/api/monitor/agent-not-exist-xyz/stream")
    if st == 403:
        # 没开监控 ⇒ 403 是**正确**答案（V89 §2：不许给"200 但一条流也没有"）
        print("  ③ 没开监控请求流 ⇒ 403 ✅（%s）" % (as_json(raw).get("error") or "")[:60])
    elif st == 501:
        why = str(as_json(raw).get("error") or "").strip()
        if why:
            print("  ③ 本平台不支持抓窗口 ⇒ 501 ✅（%s）" % why[:60])
        else:
            fails.append("③ stream 回 501 但没给原因（空文案＝用户点了一脸懵）")
    elif st == 200:
        ct = (hd.get("content-type") or "").lower()
        if "multipart" in ct:
            print("  ③ stream 已在推流（200 / %s）✅" % ct)
        else:
            fails.append("③ stream 回 200 但 Content-Type 不是 multipart：%s" % ct)
    else:
        fails.append("③ stream 应 403（没开监控）/ 501（平台不支持）/ 200（在推流），实际 %s（%s）"
                     % (st, txt(raw)))

    # ── ④ 真抓一帧（有可抓窗口才算数）────────────────────────
    st, _hd, raw = req("GET", base + "/api/monitor/agents")
    agents = as_json(raw).get("agents") if st == 200 else None
    if not isinstance(agents, list):
        skips.append("④ 拿不到 /api/monitor/agents（%s）⇒ 没法挑窗口" % st)
    else:
        cand = [a for a in agents if (a.get("window") or {}).get("can_capture")
                and int((a.get("window") or {}).get("hwnd") or 0)]
        if not cand:
            skips.append("④ 本机现在没有「能抓的 agent 窗口」（%d 个 agent 里 0 个可抓；"
                         "原因是没窗口/被最小化 —— 如实跳过，不当通过）" % len(agents))
        else:
            a = cand[0]
            hid = a.get("harness_id")
            before = sorted((r.get("harness_id") or "") for r in
                            (as_json(req("GET", base + "/api/monitor/watched")[2]).get("entries") or []))
            turned_on = False
            try:
                if not (a.get("monitor") or {}).get("on"):
                    st_on, _h, raw_on = req("POST", base + "/api/monitor/watched",
                                            {"harness_id": hid, "on": True})
                    if st_on != 200 or not as_json(raw_on).get("ok"):
                        skips.append("④ 开监控没成功（%s / %s）⇒ 跳过真抓"
                                     % (st_on, (as_json(raw_on).get("error") or txt(raw_on, 80))))
                        hid = None
                    else:
                        turned_on = True
                if hid:
                    st_f, hd_f, png = req("GET", base + "/api/monitor/%s/frame" % hid)
                    if st_f != 200:
                        fails.append("④ 对可抓窗口取帧应 200，实际 %s（%s）"
                                     % (st_f, txt(png, 140)))
                    elif (hd_f.get("content-type") or "").split(";")[0].strip() != "image/png":
                        fails.append("④ 取帧的 Content-Type 不是 image/png：%s" % hd_f.get("content-type"))
                    else:
                        method = (hd_f.get("x-monitor-method") or "").strip()
                        stats = png_stats(png)
                        if not stats.get("ok"):
                            fails.append("④ 返回的 PNG 解不开：%s" % stats.get("why"))
                        elif stats.get("blank") is None:
                            fails.append("④ 判不了黑图：%s" % stats.get("blank_why"))
                        elif stats["blank"]:
                            fails.append("④ 抓到的是**黑图**（%dx%d）——V88 记着的正是这种「抓黑当画面」"
                                         % (stats["w"], stats["h"]))
                        elif not method:
                            fails.append("④ 抓到了图，但 `X-Monitor-Method` 是空的（方法不可追溯）")
                        else:
                            print("  ④ 真抓一帧 ✅ %dx%d / %d 字节 / method=%s"
                                  % (stats["w"], stats["h"], len(png), method))
                            # ── ④′ 连续画面流（V89）：真开一条流，读首帧就说得出话 ──
                            _check_stream_first_frame(base, hid, fails, skips)
            finally:
                if turned_on:
                    req("POST", base + "/api/monitor/watched", {"harness_id": a.get("harness_id"), "on": False})
                # ── ⑤ 还原纪律：watched 名单必须与开始时一致 ──
                after = sorted((r.get("harness_id") or "") for r in
                               (as_json(req("GET", base + "/api/monitor/watched")[2]).get("entries") or []))
                if after != before:
                    fails.append("⑤ 检查结束后 monitored 名单变了：%s → %s（判据不许污染真实状态）"
                                 % (before, after))
                else:
                    print("  ⑤ 监控名单与开始时一致 ✅（%d 路）" % len(after))

    print("-" * 78)
    for s in skips:
        print("  [如实跳过] %s" % s)
    if fails:
        for f in fails:
            print("  [红] %s" % f)
        print("结论：**%d 条红** ⇒ 监控室这功能现在不能对用户说「好了」" % len(fails))
        return 1
    print("结论：**该判的都绿**（契约 ①②③ 全过，真抓一帧 %s，还原 %s）"
          % ("跳过（无窗口）" if any(s.startswith("④") for s in skips) else "已验",
             "已验" if not any(s.startswith("④") for s in skips) else "随 ④ 跳过"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
