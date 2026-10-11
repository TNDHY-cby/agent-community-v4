# -*- coding: utf-8 -*-
"""★"平台按 UTF-8 解码子进程输出、却不给子进程设输出编码"这一类 bug（2026-10-12 实测）。

**现象（真机）**：`test_action_scripts` 与 `test_workshops_session` 里
`assert "不一致" in error` / `assert "未核实" in ...` **失败**，而实际内容是：
    '\\ufffd\\u0631\\ufffd…'   ← **全是替换字符**
即动作脚本打印的中文被**毁掉了**，于是"未核实/不一致"这类**判据文本**失去可读性，
测试与真实功能一起错。

**根因**：`action_scripts.run_action` 用 `subprocess.run(..., encoding="utf-8")` 解码，
但**子进程自己用什么编码打印取决于它的区域设置** —— 中文 Windows 默认 **GBK**
⇒ 一边 GBK 写、一边 UTF-8 读 ⇒ 乱码。
而平台自己的文档与模板早已写明「`PYTHONIOENCODING=utf-8` 必加（不加会弄死桥）」，
**却漏在平台自己拉起子进程的地方**。

**为什么它"时红时绿"**（这是最危险的地方）：只要父进程环境里恰好有
`PYTHONIOENCODING=utf-8`（比如我某次在同一个 shell 里 export 过），子进程就跟着 UTF-8，
测试立刻全绿 —— 于是这个真 bug 会被当成"偶发"糊过去。
**测试必须能不依赖父进程环境地抓住它。**

本文件锁：
1. `run_action` 交给子进程的环境里**必须有 `PYTHONIOENCODING=utf-8`**；
2. **调用方显式给的值不被覆盖**（env_extra 优先）；
3. 端到端：真跑一个**打印中文**的动作脚本，断言平台拿到的中文**是好的**（不是替换字符）；
   且**在把父进程的 PYTHONIOENCODING 拿掉之后仍然成立**（这才是关键那条）。
"""
from __future__ import annotations

import os
import sys
import textwrap

import pytest


def test_run_action_sets_pythonioencoding(monkeypatch, tmp_path):
    """① 平台必须**自己**给出这个变量，不指望调用者记得设。"""
    from agent_community.platform import action_scripts as a

    seen = {}

    class _Done:
        returncode = 0
        stdout = "{}"
        stderr = ""

    def _fake_run(cmd, **kw):
        seen["env"] = kw.get("env") or {}
        return _Done()

    import subprocess as _sp_mod
    monkeypatch.setattr(_sp_mod, "run", _fake_run)
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    sc = tmp_path / "fake.py"
    sc.write_text("print(1)", encoding="utf-8")   # 必须真实存在：run_action 开头 is_file() 会提前返回
    a.run_action(sc, "deliver", {"x": 1}, python_exe=sys.executable)
    env = seen.get("env") or {}
    assert env.get("PYTHONIOENCODING") == "utf-8", \
        "没有给子进程设输出编码 ⇒ 中文 Windows 上它用 GBK 打印、平台按 UTF-8 读 ⇒ 中文变乱码"


def test_run_action_does_not_override_caller_value(monkeypatch, tmp_path):
    """② 调用方显式指定时要尊重（别的脚本可能故意用别 out encoding）。"""
    from agent_community.platform import action_scripts as a

    seen = {}

    class _Done:
        returncode = 0
        stdout = "{}"
        stderr = ""

    import subprocess as _sp_mod2
    monkeypatch.setattr(_sp_mod2, "run",
                        lambda cmd, **kw: (seen.update(env=kw.get("env") or {}), _Done())[1])
    sc = tmp_path / "f.py"
    sc.write_text("print(1)", encoding="utf-8")
    a.run_action(sc, "deliver", {}, python_exe=sys.executable,
                 env_extra={"PYTHONIOENCODING": "cp1252"})
    assert (seen.get("env") or {}).get("PYTHONIOENCODING") == "cp1252", \
        "env_extra 应优先于平台默认值"


def test_chinese_from_action_script_survives(monkeypatch, tmp_path):
    """③★端到端：**去掉父进程的 PYTHONIOENCODING** 后，中文仍必须完好。

    这条是关键 —— 它模拟"换一台没设过这个变量的机器"，也就把
    "时红时绿"的那层环境依赖剥掉了。
    """
    from agent_community.platform import action_scripts as a

    script = tmp_path / "cn.py"
    script.write_text(
        textwrap.dedent(
            '''
            import json, sys
            # 故意**不**自己 reconfigure：模拟"平台没设编码时子进程的默认行为"
            print(json.dumps({"error": "名字与要求不一致，未核实"}, ensure_ascii=False))
            sys.exit(10)
            '''
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)   # ← 关键：剥掉环境依赖
    r = a.run_action(script, "rename_session", {}, python_exe=sys.executable)
    err = str(r.get("error") or "")
    assert "\ufffd" not in err, f"中文被解码成替换字符（乱码）：{err!r}"
    assert "不一致" in err, f"判据文本不可读，测试与功能都会错：{err!r}"
    assert "未核实" in err, err


def test_chinese_survives_even_with_gbk_parent_env(monkeypatch, tmp_path):
    """④ 反向：即使父进程把输出编码**设成 GBK**，平台也仍按 UTF-8 正确读回。

    （平台强制 utf-8 的意义就在这里：不随调用者/机器的区域设置漂移。）
    """
    from agent_community.platform import action_scripts as a

    script = tmp_path / "cn2.py"
    script.write_text(
        'import json\nprint(json.dumps({"error": "未核实：命名不一致"}, ensure_ascii=False))\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONIOENCODING", "gbk")
    r = a.run_action(script, "rename_session", {}, python_exe=sys.executable)
    err = str(r.get("error") or "")
    assert "未核实" in err and "\ufffd" not in err, err
