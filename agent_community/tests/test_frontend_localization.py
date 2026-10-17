# -*- coding: utf-8 -*-
"""守卫：前端「本机坐标」体检器本身要可信（按真结果判，且**自动收严**）。

背景（老板 2026-10-17 判据：「项目必须在拉取陌生人电脑中也能跑，不能私人定制」）：
  · 产品侧（`frontend/*.html|js|css`，不含 `verify/`）：**必须零本机路径**；
  · 验收脚本侧（`verify/**`）：现在是开发工具、先只报不拦；**归零后必须把 `STRICT_VERIFY` 打开**
    （否则"修好了却没人管"＝判据失效）。
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)


def _load():
    p = Path(_ROOT) / "tools" / "check_frontend_localization.py"
    spec = importlib.util.spec_from_file_location("cf_loc", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_drive_regex_does_not_match_http_scheme():
    """★我第一版就栽在这：`p://127.0.0.1:{PORT}` 被当成 `p:` 盘符 ⇒ 产品"6 处不合格"是假警报。"""
    m = _load()
    for line in ('"http://127.0.0.1:18920/api"', "https://example.com/report", '"ws://127.0.0.1:9"'):
        hits = [x.group(0) for x in m.DRIVE.finditer(line)]
        assert hits == [], "把 URL 当成盘符了：%s → %s" % (line, hits)


def test_drive_regex_matches_escaped_windows_paths():
    """JS 源码里反斜杠是转义的（`C:\\\\Users\\\\`）—— 不认这个就会漏报（我第二版栽在这）。"""
    m = _load()
    line = 'const p = "C:\\\\Users\\\\me\\\\bridge.py";'
    hits = [x.group(0) for x in m.DRIVE.finditer(line)]
    assert hits, "漏报了转义反斜杠的盘符路径：%s" % line


def test_placeholders_are_not_counted_as_local_paths():
    """通用占位（`D:\\你的目录\\inbox`、`D:\\虚拟目录\\…`）不算本机路径。"""
    m = _load()
    for s in ("D:\\你的目录\\inbox", "D:\\虚拟目录\\wake_示例Harness-A.cmd", "C:\\path\\to\\your\\harness"):
        assert m.is_real_path(s) is False, "占位符被当成本机路径：%s" % s
    for s in ("D:\\DSH工作区1\\x", "C:\\Users\\me", "D:\\Temp\\ac_fe26_inbox"):
        assert m.is_real_path(s) is True, "真本机路径被放过：%s" % s


def test_product_side_has_no_local_paths():
    """产品侧必须零本机路径（这条是**真跑**体检器，不是查源码文本）。"""
    r = subprocess.run([sys.executable, str(Path(_ROOT) / "tools" / "check_frontend_localization.py")],
                       cwd=_ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, "体检器判红：\n%s" % out[-800:]
    head = next((ln for ln in out.splitlines() if ln.startswith("产品文件：")), "")
    assert "真本机路径 0 处" in head, "产品侧出现本机路径了：%s" % head


def test_strict_flag_must_be_on_once_verify_is_clean():
    """**自动收严**：验收脚本归零后还不打开严格档 ⇒ 红（防"修好了却没人管"）。"""
    m = _load()
    rows = m.scan()
    ver_real = [r for r in rows if r["kind"] == "verify" and r["type"] == "path" and r["real"]]
    if not ver_real:
        assert m.STRICT_VERIFY is True, \
            "verify 侧已归零，但 STRICT_VERIFY 还是 False ⇒ 请把严格档打开（不然这条判据等于没做）"
