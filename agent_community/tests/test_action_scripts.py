"""通用脚本框架（V26）测试：命名生成 / 渲染 / 配置 / 调用语义 / 端到端两种模式。

这一套的核心断言只有一句：**"做了"不等于"做成了"** —— 退出码 10 与"0 但无 evidence"
都必须落到 `verified=False`，绝不能被当成已完成（老板准则⑤）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

FW = Path(__file__).resolve().parent.parent / "bridge_templates" / "script_framework"
sys.path.insert(0, str(FW.parent.parent.parent))          # 仓库根：import agent_community.*


def _mod():
    from agent_community.platform import action_scripts as m
    return m


class _WS:
    def __init__(self, wid="w7f2a1b3", name="做一个数据看板"):
        self.workshop_id = wid
        self.name = name


class _M:
    def __init__(self, mid="m1", role="码农"):
        self.member_id = mid
        self.role = role


class TestSessionName:
    """步骤 7：`<工作间id>-<项目>-<职位>-<成员id>`，且**必须含项目名**（给用户看）。"""

    def test_basic_shape(self):
        n = _mod().session_name_for(_WS(), _M())
        assert n == "w7f2a1b3-做一个数据看板-码农-m1"

    def test_contains_project(self):
        assert "做一个数据看板" in _mod().session_name_for(_WS(), _M())

    def test_illegal_chars_replaced(self):
        n = _mod().session_name_for(_WS(name='a/b:c*d?"<>|e'), _M(role="码/农"))
        for bad in '/:*?"<>|':
            assert bad not in n
        assert "-" in n

    def test_long_project_truncated_identity_kept(self):
        n = _mod().session_name_for(_WS(name="很长的项目名" * 20), _M())
        assert len(n) <= _mod().NAME_MAX
        assert n.startswith("w7f2a1b3-"), "身份段必须保留在最前"
        assert n.endswith("-码农-m1"), "职位/成员段不许被截掉"

    def test_empty_inputs_do_not_crash(self):
        class E:
            pass
        n = _mod().session_name_for(E(), E())
        assert n.count("-") == 3 and n

    def test_deterministic(self):
        m = _mod()
        assert m.session_name_for(_WS(), _M()) == m.session_name_for(_WS(), _M()), "同成员必须幂等"


class TestRender:
    def test_common_runtime_injected_and_placeholders_gone(self):
        m = _mod()
        txt = m.render("new_session", {"HARNESS_ID": "h1", "HARNESS_NAME": "示例Harness-A"})
        assert "class Action:" in txt, "契约运行时必须被注入（生成物自包含）"
        assert "{{" not in txt, "不许把未替换的占位符发出去"
        assert "h1" in txt and "示例Harness-A" in txt

    def test_unknown_action_rejected(self):
        with pytest.raises(ValueError):
            _mod().render("nope", {})

    def test_quoted_command_does_not_break_generated_python(self, tmp_path):
        """钉住实测坑：参数含双引号/反斜杠时，生成的 .py 必须**仍能编译**。

        真实 harness 命令几乎必然含引号（可执行路径带空格），朴素替换会提前闭合字符串字面量。
        """
        m = _mod()
        cmd = '"C:\\Program Files\\示例 目录\\harness.exe" --new --name "{name}"'
        txt = m.render("new_session", {"HARNESS_ID": "h1", "HARNESS_NAME": "示例Harness-A",
                                       "NEW_SESSION_CMD": cmd})
        p = tmp_path / "quoted.py"
        p.write_text(txt, encoding="utf-8")
        compile(txt, str(p), "exec")          # 不能抛 SyntaxError
        assert "harness.exe" in txt
        # 渲染后的值必须与原文等价（转义后由 Python 解析回原串）
        ns: dict = {}
        exec(compile(txt, str(p), "exec"), ns)  # noqa: S102 —— 自造脚本，测的是可执行性
        assert ns["NEW_SESSION_CMD"] == cmd

    def test_write_lands_on_disk(self, tmp_path):
        m = _mod()
        out = m.write_action_script("rename_session", {"HARNESS_ID": "h1"},
                                    tmp_path / "成员_m1" / "rename_session.py")
        assert out["ok"] and Path(out["path"]).is_file() and out["bytes"] > 500


class TestConfigOnHarness:
    class _S:
        def __init__(self):
            self.metadata = {}

    def test_set_then_available_cmd_mode(self):
        m = _mod()
        s = self._S()
        m.set_config(s, "new_session", {"NEW_SESSION_CMD": "harness --new"}, foreground="never")
        av = m.available_actions(s)
        assert av["new_session"]["configured"] is True and av["new_session"]["mode"] == "cmd"
        assert av["new_session"]["foreground"] == "never"
        assert av["rename_session"]["mode"] == "manual", "没配的动作必须如实标成人工"

    def test_manual_mode_reports_required_foreground(self):
        av = _mod().available_actions(self._S())
        assert av["deliver"]["configured"] is False
        assert av["deliver"]["foreground"] == "required", "人工模式必然打扰用户"

    def test_set_config_unknown_action(self):
        with pytest.raises(ValueError):
            _mod().set_config(self._S(), "nope", {})


def _mk_script(tmp_path: Path, body: str, name="act.py") -> Path:
    p = tmp_path / name
    p.write_text("import json,sys,time\n" + body, encoding="utf-8")
    return p


class TestRunActionSemantics:
    """退出码 → 平台语义的映射，是准则⑤ 落到代码的地方。"""

    def test_exit0_with_evidence_is_verified(self, tmp_path):
        s = _mk_script(tmp_path, 'print(json.dumps({"ok":True,"action":"x",'
                                 '"evidence":{"session":{"id":"s1"}},"error":""}))')
        r = _mod().run_action(s, "new_session", {}, python_exe=sys.executable)
        assert r["ok"] and r["verified"] and r["evidence"]["session"]["id"] == "s1"

    def test_exit0_without_evidence_is_unverified(self, tmp_path):
        s = _mk_script(tmp_path, 'print(json.dumps({"ok":True,"action":"x","evidence":{},"error":""}))')
        r = _mod().run_action(s, "new_session", {}, python_exe=sys.executable)
        assert r["ok"] is True and r["verified"] is False
        assert "未核实" in r["error"] or "evidence" in r["error"]

    def test_exit10_is_unverified_not_failure(self, tmp_path):
        s = _mk_script(tmp_path, 'print(json.dumps({"ok":True,"action":"x","evidence":{},'
                                 '"error":"人工模式"}));sys.exit(10)')
        r = _mod().run_action(s, "rename_session", {}, python_exe=sys.executable)
        assert r["ok"] is True and r["verified"] is False and r["exit_code"] == 10
        assert "人工" in r["error"]

    def test_nonzero_is_failure(self, tmp_path):
        s = _mk_script(tmp_path, 'print(json.dumps({"ok":False,"action":"x","evidence":{},'
                                 '"error":"炸了"}));sys.exit(1)')
        r = _mod().run_action(s, "deliver", {}, python_exe=sys.executable)
        assert r["ok"] is False and r["verified"] is False and "炸了" in r["error"]

    def test_garbage_stdout_is_failure(self, tmp_path):
        s = _mk_script(tmp_path, 'print("这不是 JSON")')
        r = _mod().run_action(s, "deliver", {}, python_exe=sys.executable)
        assert r["ok"] is False and "JSON" in r["error"]

    def test_timeout_is_failure(self, tmp_path):
        s = _mk_script(tmp_path, 'time.sleep(5)')
        r = _mod().run_action(s, "deliver", {}, timeout=1.0, python_exe=sys.executable)
        assert r["ok"] is False and "超时" in r["error"]

    def test_missing_script_is_failure(self, tmp_path):
        r = _mod().run_action(tmp_path / "nope.py", "deliver", {}, python_exe=sys.executable)
        assert r["ok"] is False and "不存在" in r["error"]

    def test_env_is_passed_and_input_settings_win(self, tmp_path):
        s = _mk_script(tmp_path, 'import os\n'
                                 'print(json.dumps({"ok":True,"action":"x",'
                                 '"evidence":{"hid":os.environ.get("AC_HARNESS_ID",""),'
                                 '"step":os.environ.get("AC_ACTION","")},"error":""}))')
        r = _mod().run_action(s, "new_session", {}, env_extra={"AC_HARNESS_ID": "h9"},
                              python_exe=sys.executable)
        assert r["evidence"]["hid"] == "h9" and r["evidence"]["step"] == "new_session"


class TestEndToEndManualMode:
    """**开箱即用**：不填任何命令，L1 骨架也必须能跑、并且如实说"未核实"。"""

    def test_new_session_manual_renders_and_reports_unverified(self, tmp_path):
        m = _mod()
        p = tmp_path / "new_session.py"
        m.write_action_script("new_session", {"HARNESS_ID": "h1", "HARNESS_NAME": "示例Harness-A"}, p)
        r = m.run_action(p, "new_session", {"name_hint": "w1-项目-码农-m1"},
                         python_exe=sys.executable)
        assert r["ok"] is True, f"人工模式不该算失败：{r}"
        assert r["verified"] is False, "人工模式**必须**未核实"
        assert r["exit_code"] == 10

    def test_new_session_without_name_hint_fails_loudly(self, tmp_path):
        m = _mod()
        p = tmp_path / "new_session.py"
        m.write_action_script("new_session", {"HARNESS_ID": "h1"}, p)
        r = m.run_action(p, "new_session", {}, python_exe=sys.executable)
        assert r["ok"] is False and "name_hint" in r["error"], "平台不给名字就不许自己编"

    def test_command_mode_is_verified(self, tmp_path):
        m = _mod()
        helper = tmp_path / "fake_harness.py"
        helper.write_text('import json;print(json.dumps({"session":{"id":"abc123","name":"x"}}))',
                          encoding="utf-8")
        p = tmp_path / "new_session.py"
        m.write_action_script("new_session", {"HARNESS_ID": "h1", "HARNESS_NAME": "示例Harness-A",
                                             "NEW_SESSION_CMD": f'"{sys.executable}" "{helper}"'}, p)
        r = m.run_action(p, "new_session", {"name_hint": "w1-项目-码农-m1"},
                         python_exe=sys.executable)
        assert r["ok"] and r["verified"], f"配了命令且回报 JSON 时应可自证：{r}"
        assert r["evidence"]["session"]["id"] == "abc123"

    def test_rename_command_reports_mismatch_as_unverified(self, tmp_path):
        m = _mod()
        helper = tmp_path / "fake_rename.py"
        helper.write_text('import json;print(json.dumps({"applied_name":"完全不一样的名字"}))',
                          encoding="utf-8")
        p = tmp_path / "rename_session.py"
        m.write_action_script("rename_session", {"HARNESS_ID": "h1", "HARNESS_NAME": "示例Harness-A",
                                                "RENAME_CMD": f'"{sys.executable}" "{helper}"'}, p)
        r = m.run_action(p, "rename_session", {"new_name": "期望的名字", "session": {"id": "s1"}},
                         python_exe=sys.executable)
        assert r["ok"] is True and r["verified"] is False
        assert "不一致" in r["error"], "改名结果与要求不符时**不许**算核实通过"
