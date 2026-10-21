# -*- coding: utf-8 -*-
"""守卫：**不许把「项目数据」写到用户主目录**（反私人定制）。

## 为什么有它

老板 2026-10-21 反复强调「**禁止私人定制**」。落到代码上最典型的一种形态是：
把**运行数据**写到 `~/.agent_community/...`（或 `Path.home()`、`~/Desktop`）。
后果不是"报错"，而是**换机器/换数据目录后行为悄悄不对**：

- `PID` 记录留在主目录 ⇒ 两个不同 `AC_DATA_DIR` 的实例**互相看见对方的 PID**，
  "这个端口是不是我自己占的"就判错（实测过）；
- AI 缓存/用量留在主目录 ⇒ 清缓存清不掉、用量对不上，而且异常当初被 `pass` 吞了（已于 4.16.x 修）。

## 判据（两条，都是真的）

① **静态**：`agent_community/**` 与 `tools/*.py` 里凡是 `expanduser("~")` / `Path.home()`，
   必须在**允许表**里（且写明理由）。允许的只有两类：
   - 用户级**偏好/凭据**（`~/.agent_community/config.json` 这种本来就该跟着人走）；
   - **操作系统约定的目录**（Windows 桌面/漫游、Linux XDG），且必须**先问系统**再兜底。
② **行为**：`AC_DATA_DIR=<临时目录>` 时，端口 PID 记录必须落在那个目录下（不是主目录）。
"""
from __future__ import annotations

import importlib
import io
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

#: ⚠️ 用 AST 判「真实的调用」，不用文本匹配 —— 否则本文件自己的文档（里面写着那两个名字）
#:    会被自己判红（这条坑本项目踩过不止一次：判据别查"这串字在不在"）。
def _hits_in_source(src: str) -> list:
    """返回该源码里"把主目录当目录用"的调用行号列表。"""
    import ast as _ast

    out = []
    try:
        tree = _ast.parse(src)
    except SyntaxError:
        return out
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.Call):
            continue
        f = node.func
        # expanduser("~")  /  os.path.expanduser("~")
        if isinstance(f, _ast.Attribute) and f.attr == "expanduser":
            if node.args and isinstance(node.args[0], _ast.Constant) and node.args[0].value == "~":
                out.append(node.lineno)
        # Path.home()  /  os.path.home()
        elif isinstance(f, _ast.Attribute) and f.attr == "home":
            out.append(node.lineno)
    return out

#: 允许出现"主目录"的地方：文件 → 理由（**新增必须写理由**，否则本守卫会红）
ALLOWED = {
    "agent_community/config.py": "用户级配置文件位置（~/.agent_community/config.json）——偏好本来就跟着人走",
    "agent_community/tests/conftest.py": "测试要能识别「真实用户配置」并隔离它",
    "agent_community/tests/test_config_isolation.py": "断言隔离生效，必须知道真实配置在哪",
    "tools/autostart.py": "自启位置是**操作系统约定**（Windows 启动文件夹 / Linux XDG），先问系统再兜底",
    "tools/make_shortcut.py": "桌面是**操作系统约定**目录：先问系统（GetFolderPath / XDG），兜底才用 ~/Desktop",
}


def _scan():
    bad = []
    for base in ("agent_community", "tools"):
        for p in (_ROOT / base).rglob("*.py"):
            if "__pycache__" in p.parts or ".bak" in p.name:
                continue
            rel = str(p.relative_to(_ROOT)).replace("\\", "/")
            if rel in ALLOWED:
                continue
            try:
                src = io.open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            lines = src.splitlines()
            for i in _hits_in_source(src):
                line = lines[i - 1] if 0 < i <= len(lines) else ""
                bad.append("%s:%d  %s" % (rel, i, line.strip()[:100]))
    return bad


def test_no_undeclared_home_dir_usage():
    bad = _scan()
    assert not bad, (
        "这些地方把「数据」写到用户主目录了（换机器/换数据目录会悄悄不对）：\n  "
        + "\n  ".join(bad)
        + "\n→ 要么改成走 env_spec.data_dir()（AC_DATA_DIR），"
          "要么把它加进本文件的 ALLOWED 并**写清理由**（只有用户级偏好/操作系统约定目录才允许）。"
    )


def test_pid_dir_follows_data_dir(monkeypatch, tmp_path):
    """行为判据：`AC_DATA_DIR` 一变，PID 记录目录必须跟着变。"""
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    from agent_community import port_resolve as pr

    importlib.reload(pr)
    assert str(tmp_path) in str(pr.PID_DIR), "PID 目录没跟着 AC_DATA_DIR 走：%r" % (pr.PID_DIR,)
    assert str(tmp_path) in str(pr.PID_FILE), pr.PID_FILE
    assert ".agent_community" not in str(pr.PID_DIR).replace(str(tmp_path), ""), \
        "PID 目录又回到了用户主目录（反私人定制要拦的就是这个）：%r" % (pr.PID_DIR,)
