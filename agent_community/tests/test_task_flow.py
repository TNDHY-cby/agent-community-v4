# -*- coding: utf-8 -*-
"""守卫：**任务全链路**（`tools/task_flow_check.py`）——派活→回报→复核→退回重派→完成。

判据（都能机器判）：
  ① 判据工具真跑到全绿（六格：开工 / 派活落盘 / 回报不误判 / 复核 / 退回重派 / 完成）；
  ② 判据必须**看平台状态**（`/api/workshop/{ws}` 的 status），不能只认"接口返回 200"；
  ③ POST 端点必须带 body（`req()` 只有 body 非 None 才用 POST）——这条踩过：
     第一版把 `/review`、`/complete` 当成 GET 发出去，结果 404，白排查半天。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
TOOL = _ROOT / "tools" / "task_flow_check.py"


def _clean_env(**over) -> dict:
    keep = ("PATH", "PYTHON", "SYSTEM", "WINDIR", "TEMP", "TMP", "HOME", "USER",
            "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT", "LANG", "LC_", "LD_LIBRARY")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AC_") and k.upper().startswith(keep)}
    env.update({k: str(v) for k, v in over.items()})
    return env


def test_task_flow_judgement_is_green():
    assert TOOL.exists(), "缺 tools/task_flow_check.py"
    r = subprocess.run([sys.executable, str(TOOL)], cwd=str(_ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=600,
                       env=_clean_env())
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, "任务全链路判据有格失败：\n%s" % out[-1500:]
    for kw in ("⓪ 开工", "① 派活成功且真落盘", "② 回报被接受且未被误判完成",
               "③ 进入复核", "④ 退回重派", "⑤ 完成", "任务全链路全绿"):
        assert kw in out, "判据输出缺 '%s'：\n%s" % (kw, out[-900:])


def test_flow_check_asserts_real_status_and_posts_have_body():
    src = TOOL.read_text(encoding="utf-8", errors="replace")
    assert '"/api/workshop/%s"' in src, "判据必须读工作间真实状态，不能只看接口 200"
    for path in ("review", "continue", "complete"):
        assert '"/api/workshop/%%s/%s" %% ws_id, {})' % path in src, \
            "%s 必须带 body 发 POST（否则会被当成 GET ⇒ 404）" % path
    assert "start" in src, "判据必须先开工（discussing → running）"
