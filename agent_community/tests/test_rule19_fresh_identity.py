# -*- coding: utf-8 -*-
"""★**规则 19 验收**（老板 2026-10-11 定的铁律，验收可查）：

    任何功能都不得预设"只有我这台机器才有的东西"（我的路径、真实 harness 名、
    固定端口/目录、某产品的专用分支）。
    **验收判据 = 换一个目录 + 换一个完全不同的 agent 名，不改一行代码就能走通；做不到即未完成。**

**为什么要做成测试**：2026-10-12 我用这个判据（一个临时脚本）跑了一次**真验收**，
第一次 **1/10 通过** —— 当场抓到两个真缺陷：
  ① `wakeup_method=file_poll` 注册被 400 拒（"acp_command 不能为空"）——**用不到也索要**；
  ② hint 线索只在 `probe-register` 存了，`register` 这条入口**仍被丢弃**（两个入口只改一个）。
两个都不是"我这条路通不通"，而是**新用户第一步就卡**。⇒ 这个判据值得每次回归都跑。

**本测试怎么保证"换名换目录"**：
  · harness 名/ID 用 `uuid` **每次随机**（不写死任何真实名字）；
  · 一切落在 pytest 的 `tmp_path`（**每次不同**），邮箱/包装脚本/假 agent 全在它下面；
  · 走的是**真实端点函数**（注册 → 桥模板 → 唤配置 → 验证间），不是替身。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid

import pytest

# 平台真实端点函数（与 HTTP 层同一批）
from agent_community.platform.routers import harness_bridge as hb
from agent_community.platform.routers import harness_register as hr


class _Req:
    """最小请求替身（这些端点只用 `.json()`）。"""

    def __init__(self, body=None):
        self._body = body or {}
        self.headers = {}
        self.query_params = {}

    async def json(self):
        return self._body


def _as_dict(out):
    """端点可能返回 dict，也可能返回 Utf8JSONResponse —— 统一取 body。"""
    if isinstance(out, dict):
        return out
    body = getattr(out, "body", None)
    if body is not None:
        try:
            return json.loads(body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"_raw": str(out)[:200]}


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    """★"换名换目录"：随机名 + 每次不同的 tmp_path；并隔离落盘与桥进程登记。"""
    import agent_community.platform.server as srv
    from agent_community.platform import bridge_procs as bp

    hid = "r19-" + uuid.uuid4().hex[:8]
    work = tmp_path / ("work_" + hid)
    work.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(srv, "save_state", lambda: None, raising=False)
    monkeypatch.setattr(bp, "_procs", {}, raising=False)
    # ⚠️叫醒包装脚本的落点是**写死的仓库相对路径**
    #   （`bridge_wake.RUNTIME_SUBDIR = "agent_community/data/runtime/wake"`）⇒
    #   测试里会写进**真仓库**（被 conftest 的"污染真实数据"守卫抓到）。
    #   这里是"在测试里显式 monkeypatch 掉落盘函数"的处置；**根治**要让该模块改从
    #   `server.DATA_DIR` 取根（与其它落盘点一致）——已记入待办，此处先隔离。
    from agent_community.platform import bridge_wake as bw
    monkeypatch.setattr(bw, "RUNTIME_SUBDIR", str(tmp_path / "runtime_wake"), raising=False)
    # 安全网：本文件会驱动**真实注册/架桥**路径，这些路径都写审计 ⇒ 再补一道审计落点隔离
    #   （conftest 已做，但实测 teardown 仍报 audit.json 被写；宁可多补一道，
    #    也绝不让测试污染真实数据 —— 这条纪律的代价项目已经付过一次。）
    try:
        from agent_community.platform.audit import audit_log as _al
        monkeypatch.setattr(_al, "_path", tmp_path / "audit.json", raising=False)
        monkeypatch.setattr(_al, "data_dir", tmp_path, raising=False)
        monkeypatch.setattr(_al, "events", [], raising=False)
    except Exception:  # noqa: BLE001
        pass
    yield {"hid": hid, "work": work, "srv": srv}
    try:
        srv.harness_manager.unregister(hid)
    except Exception:  # noqa: BLE001
        pass


def _make_fake_agent(work, inbox_abs: str, py_exe: str) -> str:
    """造一个"外端 agent"+平台对应的启动器；**返回可直接用的 TRIGGER_CMD**。

    按平台约定：**trigger 不带参数**；假 agent 从自己旁边的 `inbox.txt` 读邮箱路径，
    任务在 `inbox/delivered/`（桥是**先搬走再叫醒**，实测确认）。

    启动器按平台生成（Windows `.cmd` / POSIX `.sh`）—— 见 `_make_launcher` 的注释，
    原实现写死 `cmd.exe` 让 Linux CI 必红。
    """
    agent = work / "my_agent.py"
    # ★读**inbox 根里的 `task_*.json`** —— 平台自己的话术就是这么教的
    #   （实测取证：桥是**先执行 trigger、之后才搬文件**；我一开始以为"先搬走再叫醒"，
    #     结果假 agent 去 `delivered/` 找、永远 `handled 0`、自检只能得 no_reply）。
    #   回话写 `replies/reply_<probe_id>.json` —— 桥会转发回去。
    #   ★**两个地方都找**：`inbox/` 根 与 `inbox/delivered/`。
    #     实测取证（桥日志）：被叫醒时任务**已经在 `delivered/` 里**（桥是"先搬走、再执行
    #     trigger"）—— 我一度以为相反、只读 inbox 根，于是永远 `handled 0`、自检只能 no_reply。
    #     对 agent 来说"两处都找"本来也是更稳的写法（不依赖桥的搬运时机）。
    #     脚本还会把它**实际看到什么**打进桥日志 —— 出问题不用猜。
    agent.write_text(
        "import json,os,sys\n"
        "here=os.path.dirname(os.path.abspath(__file__))\n"
        "INBOX=open(os.path.join(here,'inbox.txt'),encoding='utf-8-sig').read().strip()\n"
        "r=os.path.join(INBOX,'replies')\n"
        "os.makedirs(r,exist_ok=True)\n"
        "spots=[INBOX, os.path.join(INBOX,'delivered')]\n"
        "print('AGENT-SEES %r' % [(s, os.path.isdir(s),\n"
        "      sorted(os.listdir(s)) if os.path.isdir(s) else None) for s in spots])\n"
        "n=0\n"
        "for s in spots:\n"
        "    for f in (sorted(os.listdir(s)) if os.path.isdir(s) else []):\n"
        "        p=os.path.join(s,f)\n"
        "        if not os.path.isfile(p) or not f.endswith('.json'):\n"
        "            continue\n"
        "        try:\n"
        "            b=json.load(open(p,encoding='utf-8-sig')); pid=str(b.get('probe_id') or '')\n"
        "            if pid:\n"
        "                json.dump({'type':'self_check_ack','probe_id':pid,'from':'r19-agent',\n"
        "                           'ok':True},\n"
        "                          open(os.path.join(r,'reply_%s.json'%pid),'w',encoding='utf-8'),\n"
        "                          ensure_ascii=False); n+=1\n"
        "        except Exception as e: print('AGENT-ERR',e)\n"
        "print('handled',n)\n",
        encoding="utf-8")
    (work / "inbox.txt").write_text(inbox_abs, encoding="utf-8")
    return _make_launcher(work, py_exe)


#: 写 `.cmd` 用的编码。实测结论（2026-10-14）：**UTF-8 无 BOM**。
#:
#: ⚠️ 原来的实现写死 `encoding="ascii"`，理由是"纯 ASCII、无 BOM —— cmd 的坑"。
#:   **"无 BOM"才是真正的不变量；"纯 ASCII"只是个偷懒的代理条件** ——
#:   只要**解释器路径含非 ASCII**，插 `sys.executable` 时就炸：
#:       UnicodeEncodeError: 'ascii' codec can't encode characters in position 7-9
#:   实测触发方式：把 venv 建在 `D:\DSH工作区1\temp\cleanvenv\` 下跑测试
#:   ⇒ `test_rule19_fresh_name_and_dir_end_to_end` 直接红。
#:   为什么平时看不见：开发机解释器在 `D:\Program Files\...`、GitHub runner 也在纯 ASCII 路径
#:   ⇒ **开发机与 CI 都是绿的**。而真实用户 `C:\Users\张三\AppData\Local\Programs\Python\...`
#:   极其常见 ⇒ 又一个"开发机全绿、别人那儿红"。
#:
#: 为什么选 UTF-8 而不是系统代码页（`mbcs`/cp936）—— **实测数据，不是推理**：
#:   `temp/diag_cmd_encoding.py` 在本机（`chcp`=936）逐个跑：
#:     · ascii  → 编码就失败（原缺陷）
#:     · mbcs   → cmd 报 "The system cannot find the path specified."（字节没被按 cp936 解释）
#:     · utf-8  → rc=0，路径正确，agent 正常起来
#:   且 UTF-8 对**纯 ASCII 内容**产生的字节与原实现**完全一致** ⇒ 常见情形行为不变、
#:   非 ASCII 情形从"直接报错"变成"能跑"，**严格更好，不会更坏**。
def _cmd_encoding() -> str:
    return "utf-8"


def _write_cmd(path, py_exe: str) -> None:
    """写一个**不带 BOM** 的 `.cmd`；含非 ASCII 的解释器路径也要能写进去。"""
    body = ('@echo off\r\n'
            'rem bare trigger (platform calls it with no args)\r\n'
            '"%s" "%%~dp0my_agent.py"\r\n' % py_exe)
    raw = body.encode(_cmd_encoding(), errors="replace")
    path.write_bytes(raw)                  # 用 bytes 写：绝不可能混进 BOM
    assert not raw.startswith(b"\xef\xbb\xbf"), "cmd 启动器不得带 UTF-8 BOM（cmd 会把它当命令）"


# ── 启动器：**按平台生成**（2026-10-14 修，Linux CI 的真拦路虎）────────────────
#
# ⚠️ 原实现只造 `wake.cmd`，并把 TRIGGER_CMD 写成 `cmd.exe /c "<wake.cmd>"`。
#   **Linux 上没有 cmd.exe** ⇒ 桥拉不起假 agent ⇒ 自检拿 `state='no_reply'`
#   ⇒ `test_rule19_fresh_name_and_dir_end_to_end`（一条**核心**用例）在 CI 的
#   `test-linux` 作业里必红。
#   为什么以前没暴露：这套 CI 是我 2026-10-14 才加的，此前**从来没在 Linux 上跑过**；
#   开发机与 windows runner 上 `cmd.exe` 都在，所以一直绿。
#   真实用户侧同样成立：Linux 用户不会用 .cmd 当唤醒命令 —— 他会用 .sh。
#   ⇒ 所以这里不是"为了过 CI 而跳过"，而是**按平台给用户真正会用的那种启动器**。
def _make_launcher(work, py_exe: str) -> str:
    """造平台对应的唤醒脚本，返回**可以直接当 TRIGGER_CMD 用**的命令串。

    两个平台都保持同一语义：**裸 trigger，不带参数**（平台就是这么调它的）。
    """
    if sys.platform == "win32":
        wrap = work / "wake.cmd"
        _write_cmd(wrap, py_exe)
        # 2026-10-14: 不要再套一层 cmd.exe /c。
        #   桥是用 subprocess.Popen(trigger, shell=True) 执行的，Windows 上
        #   Python 自己就会包一层 cmd.exe /c "..." => 原写法实际是
        #       cmd.exe /c "cmd.exe /c "D:\...\wake.cmd""
        #   嵌套引号，脆弱且难查（CI 的 windows runner 上就是 no_reply）。
        #   用户真实会填的就是那个 .cmd 的路径，交给 shell 去跑。
        return '"%s"' % wrap

    wrap = work / "wake.sh"
    # `$(dirname "$0")` —— 脚本自己所在目录（别写死绝对路径，目录要因人而异）
    body = ('#!/bin/sh\n'
            '# bare trigger (platform calls it with no args)\n'
            'exec "%s" "$(dirname "$0")/my_agent.py"\n' % py_exe)
    wrap.write_bytes(body.encode("utf-8"))     # 不带 BOM
    os.chmod(wrap, 0o755)
    return str(wrap)


def test_fake_agent_launcher_survives_non_ascii_interpreter_path(tmp_path):
    """★回归守卫：解释器路径含非 ASCII 时，启动器仍要能写出来。

    实证（2026-10-14）：干净 venv 建在 `D:\\DSH工作区1\\temp\\...` 下，
    `sys.executable` 带中文 ⇒ 原实现（`encoding="ascii"`）直接 `UnicodeEncodeError`。
    真实场景一样会撞：`C:\\Users\\张三\\...\\python.exe`。CI 与开发机都在纯 ASCII 路径下，
    **都不会发现**，所以必须由这条守卫钉住。
    """
    fake_non_ascii = r"C:\用户目录\Python 程序\python.exe"
    out = tmp_path / "wake.cmd"
    _write_cmd(out, fake_non_ascii)

    raw = out.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "不能带 BOM"
    assert b"\r\n" in raw, "cmd 需要 CRLF"
    text = raw.decode(_cmd_encoding(), errors="replace")
    assert fake_non_ascii in text, "解释器路径被编码破坏（含非 ASCII 时必须按代码页写）"
    assert "%~dp0my_agent.py" in text, "脚本路径必须用 %~dp0 相对写法（目录也要因人而异）"


def test_launcher_is_platform_appropriate(tmp_path):
    """★回归守卫：启动器必须**按平台**生成（Linux 上没有 cmd.exe）。

    实证（2026-10-14）：原实现只造 `wake.cmd` 并把 TRIGGER_CMD 写成 `cmd.exe /c ...`
    ⇒ Linux 上桥拉不起假 agent、自检拿 `no_reply`，`test-linux` 必红。
    本用例只断言"平台对应的那种"确实生成了、且能被当命令串用；
    Windows 上的 `.cmd` 规则由上面那条守卫继续钉住。
    """
    work = tmp_path / "w"
    work.mkdir()
    trigger = _make_launcher(work, sys.executable)

    if sys.platform == "win32":
        assert (work / "wake.cmd").is_file(), "Windows 上应当生成 wake.cmd"
        # ★2026-10-14 改：**不再**要求前缀 `cmd.exe /c`。
        #   桥用 `subprocess.Popen(trigger, shell=True)` 执行，Windows 上 Python 自己会包
        #   一层 `cmd.exe /c "…"`；再手动套一层就成了嵌套引号
        #   （`cmd.exe /c "cmd.exe /c "D:\…\wake.cmd""`）—— CI 的 windows runner 上就是
        #   卡在这里拿到 `no_reply`。用户真实会填的就是那个 .cmd 的路径。
        assert trigger.strip().startswith('"') and trigger.strip().endswith('.cmd"'), \
            f"Windows 的 TRIGGER_CMD 应当就是带引号的 .cmd 路径：{trigger}"
        assert "cmd.exe" not in trigger, \
            f"不要把 cmd.exe 写进 TRIGGER_CMD（shell=True 已经会包一层）：{trigger}"
    else:
        sh = work / "wake.sh"
        assert sh.is_file(), "POSIX 上应当生成 wake.sh（Linux 没有 cmd.exe）"
        assert os.access(sh, os.X_OK), "wake.sh 必须可执行，否则桥调它会被拒"
        assert "cmd.exe" not in trigger, f"POSIX 上不许出现 cmd.exe：{trigger}"
        body = sh.read_bytes()
        assert not body.startswith(b"\xef\xbb\xbf"), "脚本不得带 BOM（sh 会把 BOM 当命令）"
        assert b"$(dirname \"$0\")" in body, "脚本路径要用 $(dirname \"$0\") 相对写法"


def test_rule19_fresh_name_and_dir_end_to_end(fresh):
    """★核心：随机名 + 全新目录，走完 注册 → 桥模板 → 唤配置 → 验证间跑通 → 拆掉。"""
    hid, work, srv = fresh["hid"], fresh["work"], fresh["srv"]

    # ① 注册：**只带身份**（不提供 acp_command —— file_poll 用不到；
    #    邮箱路径也给个占位，稍后按平台真正用的那处改写模板）
    out = asyncio.run(hr.register_harness(_Req({
        "harness_id": hid, "harness_name": "示例Harness-" + hid[-4:],
        "harness_type": "desktop-agent", "wakeup_method": "file_poll",
        "wakeup_dir": str(work), "ai": {"model_name": "示例模型"},
        "process_hint": "r19-probe",
    })))
    d = _as_dict(out)
    assert d.get("success") is True, (
        "★file_poll 这种**不需要平台拉起**的方式，不该被索要 acp_command：%s" % d)

    sess = srv.harness_manager.sessions.get(hid)
    assert sess is not None
    hints = (sess.info.metadata or {}).get("probe_hints") or {}
    assert hints.get("process_hint") == "r19-probe", (
        "★hint 线索必须真的存进 metadata['probe_hints']（两个入口都要！）：%r" % hints)

    # ② 桥模板：**邮箱用平台自己的隔间路径**（它在运行时才算得出，别自己拼 ——
    #    我第一版自己拼，结果和平台实际用的不是同一个目录 ⇒ 桥在空目录里等文件、
    #    自检永远 no_reply。这正是"两个邮箱"那个坑，今天已踩第二次。）
    from agent_community.platform.verify_sandbox import draft_template_from, _bt_mailbox
    from agent_community.platform.verify_sandbox import ensure_sandbox

    ws = ensure_sandbox(srv.workshops, hid, data_dir=str(srv.DATA_DIR),
                        harness_name="示例Harness")
    member = ws.members[0]
    inbox = os.path.abspath(str(_bt_mailbox(ws, member)))
    os.makedirs(inbox, exist_ok=True)
    wrap = _make_fake_agent(work, inbox, sys.executable)

    out = asyncio.run(hb.harness_bridge_template_put(hid, _Req({
        "name": "r19桥", "kind": "file_poll",
        # ★2026-10-14：TRIGGER_CMD 由 `_make_launcher` **按平台**产出
        #   （Windows: `cmd.exe /c "wake.cmd"`；POSIX: `wake.sh`）。
        #   原先这里写死 `cmd.exe /c …` ⇒ Linux CI 必红（那边没有 cmd.exe）。
        "params": {"INBOX_DIR": inbox, "TRIGGER_CMD": wrap},
    })))
    d = _as_dict(out)
    assert d.get("success") is True, d
    assert (d.get("wake_synced") or {}).get("path"), \
        "模板里的 TRIGGER_CMD 必须同步成**桥真正使用的那一处**（P3 单一真相）"

    # ③ 验证间：建 → 架桥 → 真自检 → 跑通才回写 → 拆掉
    out = asyncio.run(hb.harness_verify_bridge(hid, _Req({"wait": 16, "teardown": True})))
    v = _as_dict(out)
    assert v.get("stage") == "selfcheck", "应跑到自检阶段：%s" % json.dumps(v, ensure_ascii=False)[:300]
    assert v.get("state") == "ok", (
        "★全新名字+全新目录下应能真跑通（平台→桥→agent→回报）；实得 state=%r reason=%r"
        % (v.get("state"), v.get("reason")))
    assert (v.get("committed") or {}).get("success") is True, \
        "★跑通了就必须把实测配置写进模板并标记已验证：%s" % (v.get("committed"),)
    td = v.get("torndown") or {}
    assert td.get("removed") is True, "★拆掉：连桥一起，只留模板：%s" % td
    from agent_community.platform.verify_sandbox import sandbox_dir_name as _sdn
    os_ws = os.path.join(str(srv.DATA_DIR), "verify_sandbox", _sdn(hid))
    assert not os.path.isdir(os_ws), "验证间目录应已清理"


def test_fresh_registration_does_not_require_a_launch_command(fresh):
    """★独立锁一条：**用不到拉起命令的接入方式，不该被索要它**。

    这是 2026-10-12 规则 19 验收抓到的第一个缺陷（新用户第一步就卡）。
    """
    hid, work, srv = fresh["hid"], fresh["work"], fresh["srv"]
    for wm in ("file_poll", "http_api", "clipboard"):
        out = asyncio.run(hr.register_harness(_Req({
            "harness_id": "%s-%s" % (hid, wm), "harness_name": "示例Harness-" + wm,
            "harness_type": "desktop-agent", "wakeup_method": wm,
            "wakeup_dir": str(work), "ai": {"model_name": "示例模型"},
            **({"api_base_url": "http://127.0.0.1:1"} if wm == "http_api" else {}),
        })))
        d = _as_dict(out)
        assert d.get("success") is True, (
            "wakeup_method=%s 不需要 acp_command，不该被拒：%s" % (wm, d))
        try:
            srv.harness_manager.unregister("%s-%s" % (hid, wm))
        except Exception:  # noqa: BLE001
            pass
