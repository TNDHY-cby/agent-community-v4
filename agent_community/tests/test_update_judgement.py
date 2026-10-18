# -*- coding: utf-8 -*-
"""守卫：**一键更新真的能用**（跑 `tools/check_update.py` 的端到端判据）。

老板 2026-10-19 问「一键更新没办法进行开发？」——能，而且必须**可验证**：
造一对"旧装 / 新发布"的真 git 仓库，验四格（拒绝脏树 / 拒绝数据嵌套 / 真更新 / 能回滚）。
本文件把那次判据钉进回归，防止以后"改着改着就不能更新了"。

⚠️ 它会起若干个 git 子进程（约 10–20s），但**这条值得慢**：更新的每一步都不可逆。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
TOOL = _ROOT / "tools" / "check_update.py"


def _clean_env(**over) -> dict:
    """守卫起工具时用**白名单环境**。

    ★2026-10-18/19 真事故（两次）：这条判据**单跑全绿、进整套回归就红**，根因都是
    "子进程继承了 Pytest 里别的用例留下的 `AC_*`"（如 `AC_ENV=production` ⇒ 隔离实例起不来）。
    教训：**凡是守卫 spawn 工具的，环境都要白名单**（同 `protocol_matrix.child_env`）。
    """
    keep = ("PATH", "PYTHON", "SYSTEM", "WINDIR", "TEMP", "TMP", "HOME", "USER",
            "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT", "LANG", "LC_", "LD_LIBRARY")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AC_") and k.upper().startswith(keep)}
    env["AC_POSTCHECK_TIMEOUT"] = "120"          # 套件里机器更忙，给足时间
    env.update({k: str(v) for k, v in over.items()})
    return env


def test_update_judgement_is_green():
    assert TOOL.exists(), "缺 tools/check_update.py（一键更新的端到端判据）"
    r = subprocess.run([sys.executable, str(TOOL)], cwd=str(_ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=900,
                       env=_clean_env())
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, "一键更新判据有格失败：\n%s" % out[-1500:]
    for kw in ("① 拒绝脏工作树", "② 拒绝数据在安装目录内",
               "③ 坏版本被自检拦住并回滚", "④ 好版本更新到", "④b 写了备份点",
               "⑤ 回滚回", "一键更新可用"):
        assert kw in out, "判据输出缺 '%s'：\n%s" % (kw, out[-900:])


def test_update_tool_refuses_to_touch_data_dir_and_reads_version_from_disk():
    """两条**真事故**的回归：① 数据目录在安装目录内必须拒绝；② 更新后必须读**磁盘**版本号。

    第 ② 条曾经害得工具"刚更新成功却判失败并自动回滚"——因为进程里 `agent_community` 是旧模块。
    """
    src = (_ROOT / "tools" / "update.py").read_text(encoding="utf-8", errors="replace")
    assert "数据目录在**安装目录之内**" in src, "缺少「数据目录嵌套」的安全拒绝"
    assert "git status" in src or '"status"' in src, "缺少工作树干净检查"
    assert 'read_text(encoding="utf-8"' in src and "__version__" in src, \
        "更新后必须从**文件**读版本号（不能信进程内的模块缓存）"
    assert "--rollback" in src, "缺少回滚入口"
    assert "merge" in src and "--ff-only" in src, "更新应走 fast-forward（可回滚），不做覆盖式替换"


def test_update_json_mode_is_machine_readable():
    """`--json` 必须给**一行可解析 JSON**（插件/前端照它写，不要解析中文）。

    这条是给「宋的一键更新插件」的接口：三个动作各一个 `action` 值。
    """
    import json
    env = {**os.environ}
    env.pop("PYTHONPATH", None)
    r = subprocess.run([sys.executable, str(_ROOT / "tools" / "update.py"), "--check", "--json"],
                       cwd=str(_ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300, env=env)
    out = (r.stdout or "").strip()
    line = [ln for ln in out.splitlines() if ln.strip().startswith("{")][-1]
    d = json.loads(line)
    for k in ("action", "current", "latest", "update_available", "error", "safe", "problems"):
        assert k in d, "JSON 缺字段 %s：%s" % (k, sorted(d))
    assert d["action"] == "check"
    assert r.returncode in (0, 1, 2), "退出码语义：0=正常 1=不行 2=查不到"


def test_update_has_post_check_and_escape_hatch():
    """更新后**必须自检**（版本对得上 ≠ 新版本能用），且要有 `--no-verify` 逃生口。

    真事：v4.8.60 出过"版本号没写进去但代码照推"。更强的一类是"版本号对、但新版本起不来"——
    只有真起一次实例才发现，所以 `--apply` 默认起一个**隔离实例**验。
    """
    src = (_ROOT / "tools" / "update.py").read_text(encoding="utf-8", errors="replace")
    assert "def post_check(" in src, "缺少更新后自检函数"
    assert "compileall" in src, "自检至少要能编译过"
    assert "/api/status" in src, "自检必须真起一次实例并打 /api/status"
    assert "--no-verify" in src, "没有 --no-verify 逃生口（用户被迫跳过不了）"
    assert "post-check-failed" in src, "自检失败必须能自动回滚并说明原因"


def test_update_tools_are_in_published_face():
    for rel in ("tools/update.py", "tools/check_update.py"):
        assert (_ROOT / rel).exists(), "发布面缺 %s（陌生人就没法一键更新）" % rel
