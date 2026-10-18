# -*- coding: utf-8 -*-
"""守卫：**开机自启**工具（`tools/autostart.py`）——可查、可关、**测试绝不动真机器**。

起因（2026-10-18 真事故）：老板那台机器突然关机，重启后 18920 打不开 ——
项目里有看门狗，但**没有随机器启动的机制**（启动文件夹空、无计划任务）。这违反「装好就能用」。

判据（都能机器判）：
  ① `status --json` 可解析，且**如实**给出 supported/installed/where/detail；
  ② `enable --dry-run` **一个文件都不写**（防"预览一下却真装了"）；
  ③ 用 `--startup <临时目录>` 真装一次 ⇒ 只写**两个**文件：启动器（数据目录）+ 快捷方式/unit（临时目录），
     仓库里不许多出任何东西；再 `disable` 干净卸掉（幂等：卸两次不报错）；
  ④ 本工具**源码里没有本机坐标**（跑出来的路径全是运行时发现的）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

TOOL = Path(_ROOT) / "tools" / "autostart.py"


def _run(*args, env_extra=None):
    env = {**os.environ}
    env.pop("PYTHONPATH", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, str(TOOL), *args], cwd=_ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=180, env=env)


def test_status_json_is_parseable_and_honest():
    r = _run("status", "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    info = json.loads(r.stdout.strip().splitlines()[-1])
    for k in ("supported", "installed", "where", "detail"):
        assert k in info, "status --json 少了 %s：%s" % (k, info)
    assert isinstance(info["installed"], bool)
    assert info["detail"], "detail 不许空（失败要说话）"


def test_dry_run_writes_nothing():
    data = tempfile.mkdtemp(prefix="ac_as_data_")
    startup = tempfile.mkdtemp(prefix="ac_as_start_")
    r = _run("enable", "--dry-run", "--startup", startup, env_extra={"AC_DATA_DIR": data})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "什么都没写" in r.stdout
    assert list(Path(startup).iterdir()) == [], "dry-run 竟然往启动目录写了东西"
    assert list(Path(data).iterdir()) == [], "dry-run 竟然往数据目录写了东西"


@pytest.mark.skipif(os.name != "nt" and not sys.platform.startswith("linux"), reason="只支持 Windows/Linux")
def test_enable_then_disable_is_clean():
    data = tempfile.mkdtemp(prefix="ac_as_data2_")
    startup = tempfile.mkdtemp(prefix="ac_as_start2_")
    r = _run("enable", "--startup", startup, "--unit-dir", startup,
             env_extra={"AC_DATA_DIR": data})
    assert r.returncode == 0, "enable 失败：%s%s" % (r.stdout, r.stderr)

    # 数据目录里应有启动器；启动/unit 目录里应有那一个入口
    made = [p for p in Path(data).rglob("*") if p.is_file()]
    assert any(p.name.startswith("ac_watchdog") for p in made), "没生成启动器：%s" % made
    entries = [p for p in Path(startup).iterdir()]
    assert len(entries) == 1, "启动入口应只有一个（实际 %s）" % [p.name for p in entries]

    # 装了之后 status 必须能看出来
    r2 = _run("status", "--json", "--startup", startup, "--unit-dir", startup,
              env_extra={"AC_DATA_DIR": data})
    info = json.loads(r2.stdout.strip().splitlines()[-1])
    assert info["installed"] is True, "装完了 status 还说没装：%s" % info

    # 卸两次都要干净（幂等）
    for _ in range(2):
        r3 = _run("disable", "--startup", startup, "--unit-dir", startup,
                  env_extra={"AC_DATA_DIR": data})
        assert r3.returncode == 0, r3.stdout + r3.stderr
    assert list(Path(startup).iterdir()) == [], "没卸干净：%s" % [p.name for p in Path(startup).iterdir()]
    info2 = json.loads(_run("status", "--json", "--startup", startup, "--unit-dir", startup,
                            env_extra={"AC_DATA_DIR": data}).stdout.strip().splitlines()[-1])
    assert info2["installed"] is False


def test_tool_has_no_machine_coordinates_in_code():
    """**代码里**不许有本机坐标；文档字符串里提事故经过（如"重启后 18920 打不开"）不算坐标。

    ⚠️ 我第一版直接 grep 全文 ⇒ 把 docstring 里的 `18920` 判成硬编码（我自己写死的判据太钝）。
    现在用 AST 只扫**字符串常量里去掉 docstring 之后**剩下的那些。
    """
    import ast
    src = TOOL.read_text(encoding="utf-8")
    tree = ast.parse(src)
    doc_strings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                doc_strings.add(d)
    bad_hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in doc_strings:
                continue
            for bad in ("D:\\", "C:\\Users", "18920"):
                if bad in node.value:
                    bad_hits.append((bad, node.value[:60]))
    assert not bad_hits, "autostart.py 的**代码**里出现了本机坐标：%s" % bad_hits[:3]
