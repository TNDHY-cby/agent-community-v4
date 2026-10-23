# -*- coding: utf-8 -*-
"""守卫：**发布面**不许"开发机有、对外没有"（2026-10-23 加，起因是外部审查报告第 3 轮 P1）。

事故（外部审查抓到的，不是我发现的）：
- README:498 指向 `THIRD_PARTY_NOTICES.md`，Gitee 上没有这个文件；
- README:38 指向 `docs/FAQ.md`，而 `docs/` 从来没进同步面；
- 连带：公开仓库自带的 `tools/third_party_check.py` 实跑 **FAIL / exit 1** ——
  陌生人 clone 下来，自带的判据就是红的。

⇒ 两条判据：
1. `tools/publish_face_check.py`（dev 侧）必须全过：根文档的相对链接可达 + 许可登记一致；
2. 发布脚本的同步清单里**必须**写着这两个曾经漏掉的东西（`THIRD_PARTY_NOTICES.md` 在 `$rootDocs` 里、
   `docs/` 有单独的同步段）—— 这一条查的是**接线**（脚本有没有把它带出去），
   真正的判据是发布之后跑 `publish_face_check.py --oss`（那一步不在这里做，因为发布副本会滞后于 dev）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_publish_face_check_passes_on_dev():
    r = subprocess.run([sys.executable, str(_ROOT / "tools" / "publish_face_check.py")],
                       cwd=str(_ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, "发布面判据红了（README 的链接/许可登记）：\n%s" % out[-1500:]


def test_publish_script_ships_the_two_files_that_once_went_missing():
    """接线判据：同步清单里必须带上 `THIRD_PARTY_NOTICES.md` 与 `docs/`。"""
    script = _ROOT / "publish_acv4.ps1"
    assert script.is_file(), "找不到发布脚本 publish_acv4.ps1"
    text = script.read_text(encoding="utf-8", errors="replace")
    assert "THIRD_PARTY_NOTICES.md" in text, \
        "发布脚本的 rootDocs 清单里没有 THIRD_PARTY_NOTICES.md ⇒ 许可登记文件不会发布"
    assert "sync docs/" in text, \
        "发布脚本里没有 docs/ 的同步段 ⇒ README 引用的 docs/FAQ.md 在公开仓库里会是死链"
