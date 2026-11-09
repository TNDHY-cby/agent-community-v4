#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**团队资产体检**（`V118` §4 / 队列 49-8）。

真跑：临时实例 + 真造一个心智根（风格指南 + memories + 死胡同 + 案例）。

| # | 格 | 断言 |
|---|---|---|
| ① | 五维指纹齐全 | `代码审美/协作节奏/风险偏好/验证严格度/沟通密度` 五项都在；**没登记就写「未登记」**（不编） |
| ② | 四信号齐全且带事实+建议 | `僵化/默契退化/案例陈旧/教训遗忘` 四条都有 `fact` 与 `suggest` |
| ③ | **不评分** | 响应里没有 `score`/`rank`/百分比"健康度"；有 `no_score: true` |
| ④ | 僵化能真触发 | 有 `reusable` 记忆但全都超过阈值天数 ⇒ `rigid.triggered=true` |
| ⑤ | 教训遗忘能真触发 | `06_死胡同/` 有内容、`asset/失败教训.md` 不存在 ⇒ `forgotten_lessons.triggered=true` |
| ⑥ | 只读快照**不改盘** | `asset-snapshot` 返回 `readonly: true`；调用前后心智根文件列表逐字相同 |
| ⑦ | 开关真生效 | 子进程带 `AC_ASSET_HEALTH=0` ⇒ `enabled()` False |

退出码：0＝全过；1＝有格没过；2＝起不来。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
PY = sys.executable

ROWS: list = []
KEY = "asset-judge-01"


def check(label: str, ok: bool, detail: str = "") -> None:
    ROWS.append((label, bool(ok), detail))
    print("  %s %s%s" % ("PASS" if ok else "FAIL", label,
                        ("　（" + detail + "）") if detail and not ok else ""))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _http(base: str, path: str, body=None, timeout: float = 30):
    path = urllib.parse.quote(path, safe="/?=&:,.~@!$'()*+;")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    h = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(base + path, data=data, headers=h,
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.status), json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return int(e.code), json.loads(raw or "{}")
        except ValueError:
            return int(e.code), {"raw": raw[:300]}


def _py_env(expr: str, extra: dict) -> str:
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from agent_community.platform import asset_health as A\n"
            "print(%s)" % (str(ROOT), expr))
    env = dict(os.environ)
    env.update(extra)
    r = subprocess.run([PY, "-X", "utf8", "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120, env=env)
    return (r.stdout or "").strip()


def _make_mind(home: Path) -> None:
    """造一个"有腐化迹象"的心智根：风格指南只登记一维 + 老 reusable 记忆 + 有死胡同没教训汇总。"""
    (home / "memories").mkdir(parents=True, exist_ok=True)
    (home / "06_死胡同").mkdir(parents=True, exist_ok=True)
    (home / "asset" / "经典案例").mkdir(parents=True, exist_ok=True)
    (home / "asset" / "风格指南.md").write_text(
        "# 风格指南\n\n- 代码审美: 简洁优先，函数短\n- 成员数: 3\n", encoding="utf-8")
    old = time.time() - 60 * 86400
    mem = home / "memories" / "old-reusable.md"
    mem.write_text("# 记忆 old\n\n- 来源: agent\n- 标签: x\n- 时间: %s\n- 档: reusable（复用级）\n\n老经验\n"
                   % time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(old)), encoding="utf-8")
    os.utime(mem, (old, old))
    dead = home / "06_死胡同" / "2026-09-01.md"
    dead.write_text("# 死胡同\n\n- 试过用隧道，没用\n", encoding="utf-8")
    os.utime(dead, (old, old))
    case = home / "asset" / "经典案例" / "旧案例.md"
    case.write_text("# 旧案例\n", encoding="utf-8")
    os.utime(case, (old, old))


def _snapshot_files(root: Path) -> list:
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file())


def main() -> int:
    port = _free_port()
    tmpdir = Path(tempfile.mkdtemp(prefix="ac_asset_"))
    minds = tmpdir / "minds"
    _make_mind(minds / KEY)
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": str(tmpdir / "data"),
                "AC_MIND_ROOT": str(minds), "PYTHONIOENCODING": "utf-8",
                "AC_ARTIFACT_WATCH_SEC": "3600"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：团队资产体检（49-8 · 真跑）")
    print("=" * 78)
    try:
        up = False
        for _ in range(80):
            try:
                up = _http(base, "/api/status", timeout=3)[0] == 200
            except (urllib.error.URLError, OSError):           # noqa: BLE001 — 起服期间连不上是预期
                up = False
            if up:
                break
            time.sleep(0.5)
        if not up:
            print("[FAIL] 平台没起来 —— 这次不算跑成")
            return 2

        st1, b1 = _http(base, "/api/asset-health/%s?members_count=5" % KEY)
        dims = ((b1 or {}).get("fingerprint") or {}).get("dims") or []
        labels = [d.get("label") for d in dims]
        vals = {d.get("label"): d.get("value") for d in dims}
        check("① 五维指纹齐全（没登记就写「未登记」，不编）",
              st1 == 200 and labels == ["代码审美", "协作节奏", "风险偏好", "验证严格度", "沟通密度"]
              and vals.get("代码审美", "").startswith("简洁优先")
              and vals.get("协作节奏") == "(未登记)",
              "HTTP %s dims=%r" % (st1, vals))

        sig = {s.get("signal"): s for s in ((b1 or {}).get("signals") or [])}
        check("② 四信号齐全且都带事实+建议",
              {"rigid", "tacit_decay", "stale_cases", "forgotten_lessons"} <= set(sig)
              and all(bool((sig.get(k) or {}).get("fact")) and bool((sig.get(k) or {}).get("suggest"))
                      for k in sig),
              "signals=%r" % list(sig))

        # ⚠️ 不能扫整个 blob：我自己的 `no_score: true` 标记里就有 "score" 子串
        #   （上一轮 `no_ranking` 撞 "rank" 是同一个坑）。**只看数据字段，不看说明文字**。
        bad_keys = []
        for sec in ([b1.get("fingerprint") or {}] + list((b1 or {}).get("signals") or [])):
            for k in (sec or {}):
                kl = str(k).lower()
                if "score" in kl or "rank" in kl or "health_pct" in kl or "grade" in kl:
                    bad_keys.append(k)
        check("③ **不评分**（数据字段里没有 score/rank/健康度百分比；有 no_score 声明）",
              (b1 or {}).get("no_score") is True and not bad_keys,
              "no_score=%r 违规字段=%r" % ((b1 or {}).get("no_score"), bad_keys))

        check("④ 僵化能真触发（reusable 全是 60 天前）",
              bool((sig.get("rigid") or {}).get("triggered")),
              "rigid=%r" % (sig.get("rigid") or {}))

        check("⑤ 教训遗忘能真触发（有死胡同、没有失败教训.md）",
              bool((sig.get("forgotten_lessons") or {}).get("triggered")),
              "forgotten=%r" % (sig.get("forgotten_lessons") or {}))

        before = _snapshot_files(minds)
        st6, b6 = _http(base, "/api/asset-snapshot/%s?members_count=5" % KEY)
        after = _snapshot_files(minds)
        check("⑥ 只读快照 readonly=true 且**不改盘**（前后文件列表逐字相同）",
              st6 == 200 and (b6 or {}).get("readonly") is True and before == after,
              "readonly=%r 文件 %d→%d" % ((b6 or {}).get("readonly"), len(before), len(after)))
        # ⑧ 不存在的根 ⇒ **404**（真打抓到过 200 + code 的"看起来成功"）
        st8, b8 = _http(base, "/api/asset-health/no-such-key?members_count=1")
        st8b, b8b = _http(base, "/api/asset-snapshot/no-such-key?members_count=1")
        check("⑧ 不存在的根 ⇒ 404 NO_MIND_HOME（不许 200 + code）",
              st8 == 404 and (b8 or {}).get("code") == "NO_MIND_HOME"
              and st8b == 404,
              "health=%s %s / snapshot=%s" % (st8, (b8 or {}).get("code"), st8b))
    finally:
        try:
            srv.terminate()
            srv.wait(timeout=10)
        except Exception:                                      # noqa: BLE001 — 收尾
            srv.kill()
        shutil.rmtree(tmpdir, ignore_errors=True)

    off = _py_env("A.enabled()", {"AC_ASSET_HEALTH": "0"})
    check("⑦ 开关真生效（子进程 AC_ASSET_HEALTH=0 ⇒ enabled=False）", off == "False", "got=%r" % off)

    print("-" * 78)
    passed = sum(1 for _l, ok, _d in ROWS if ok)
    print("合计 %d 格：PASS %d / FAIL %d" % (len(ROWS), passed, len(ROWS) - passed))
    for lb, ok, d in ROWS:
        if not ok:
            print("  FAIL:", lb, "|", str(d)[:240])
    return 0 if passed == len(ROWS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
