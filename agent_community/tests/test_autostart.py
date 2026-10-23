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


def _can_create_shortcut():
    """这台机器能不能建 .lnk（**能力探测**，不是猜）。

    ★2026-10-21（恢复 GitHub 后 CI 真跑才发现）：GitHub 的 Windows runner **没有桌面/COM 环境**，
      `WScript.Shell.Save()` 直接抛 `FileNotFoundException` ⇒ 与产品无关，
      但会把 CI 弄红、还会让人误以为"开机自启坏了"。⇒ 先探一下：探不到就**带原因跳过**。
    """
    if os.name != "nt":
        return False, "非 Windows，没有 .lnk"
    tmp = tempfile.mkdtemp(prefix="ac_lnk_probe_")
    probe = Path(tmp) / "probe.lnk"
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
        "$s.TargetPath='%%COMSPEC%%';$s.Save()" % str(probe).replace("'", "''")
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
    except Exception as e:                                   # noqa: BLE001
        return False, "探测失败：%s" % e
    if probe.exists():
        return True, ""
    return False, ((r.stdout or "") + (r.stderr or "")).strip()[:200] or "建不出来（无原因输出）"


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
    r = _run("enable", "--mode", "startup", "--dry-run", "--startup", startup,
             env_extra={"AC_DATA_DIR": data})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "什么都没写" in r.stdout
    assert list(Path(startup).iterdir()) == [], "dry-run 竟然往启动目录写了东西"
    assert list(Path(data).iterdir()) == [], "dry-run 竟然往数据目录写了东西"


@pytest.mark.skipif(os.name != "nt", reason="计划任务（schtasks）只有 Windows 有")
def test_task_mode_dry_run_only_prints_the_scheduled_task_command():
    """`--mode task --dry-run` 只该**打印** schtasks 命令，绝不许真去建任务（建任务要管理员权限）。"""
    data = tempfile.mkdtemp(prefix="ac_as_data3_")
    r = _run("enable", "--mode", "task", "--dry-run", env_extra={"AC_DATA_DIR": data})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "schtasks" in r.stdout, "没打印计划任务命令：%s" % r.stdout[-300:]
    assert "什么都没写" in r.stdout
    assert list(Path(data).iterdir()) == [], "dry-run 竟然往数据目录写了东西"


@pytest.mark.skipif(os.name != "nt" and not sys.platform.startswith("linux"), reason="只支持 Windows/Linux")
def test_enable_then_disable_is_clean():
    ok, why = _can_create_shortcut()
    if not ok:
        pytest.skip("这台机器建不了快捷方式（CI runner 无桌面 COM）：%s" % why)
    data = tempfile.mkdtemp(prefix="ac_as_data2_")
    startup = tempfile.mkdtemp(prefix="ac_as_start2_")
    # ★2026-10-23：入口脚本已搬出运行数据目录（放 `deploy/autostart`，可被 `AC_AUTOSTART_DIR` 覆盖）。
    #   测试**必须**把它指到临时目录，否则会往真仓库的 deploy/ 里写东西（把工作树弄脏）。
    entry = tempfile.mkdtemp(prefix="ac_as_entry2_")
    # ⚠️ 必须显式 `--mode startup`：默认 `both` 会去建**计划任务**，而建"登录时运行"的计划任务
    #    需要管理员权限（本会话没提升 ⇒ Access is denied）。**测试不许去动真机器的计划任务。**
    r = _run("enable", "--mode", "startup", "--startup", startup, "--unit-dir", startup,
             env_extra={"AC_DATA_DIR": data, "AC_AUTOSTART_DIR": entry})
    assert r.returncode == 0, "enable 失败：%s%s" % (r.stdout, r.stderr)

    # 入口脚本在 entry 目录；数据目录里**不许**再有入口（那就是这次搬家的目的）
    made = [p for p in Path(entry).rglob("*") if p.is_file()]
    assert any(p.name.startswith("ac_watchdog") for p in made), "没生成启动器：%s" % made
    assert not [p for p in Path(data).rglob("ac_watchdog*")], \
        "入口脚本又被写进运行数据目录了（换 AC_DATA_DIR 就会丢）：%s" % list(Path(data).rglob("*"))
    entries = [p for p in Path(startup).iterdir()]
    assert len(entries) == 1, "启动入口应只有一个（实际 %s）" % [p.name for p in entries]

    # 装了之后 status 必须能看出来
    r2 = _run("status", "--json", "--startup", startup, "--unit-dir", startup,
              env_extra={"AC_DATA_DIR": data, "AC_AUTOSTART_DIR": entry})
    info = json.loads(r2.stdout.strip().splitlines()[-1])
    assert info["installed"] is True, "装完了 status 还说没装：%s" % info

    # 卸两次都要干净（幂等）
    for _ in range(2):
        r3 = _run("disable", "--startup", startup, "--unit-dir", startup,
                  env_extra={"AC_DATA_DIR": data, "AC_AUTOSTART_DIR": entry})
        assert r3.returncode == 0, r3.stdout + r3.stderr
    assert list(Path(startup).iterdir()) == [], "没卸干净：%s" % [p.name for p in Path(startup).iterdir()]
    info2 = json.loads(_run("status", "--json", "--startup", startup, "--unit-dir", startup,
                            env_extra={"AC_DATA_DIR": data,
                                       "AC_AUTOSTART_DIR": entry}).stdout.strip().splitlines()[-1])
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


# ── 2026-10-23：`check` 子命令（登录后到底会不会自己起）─────────────────────
def test_check_reports_not_installed_honestly(tmp_path):
    """没装 ⇒ exit 2 + 说清"重启/登录不会自己起" + 给修复命令（不许假装 OK）。"""
    startup = tmp_path / "startup_empty"
    startup.mkdir()
    data = tmp_path / "data_empty"
    data.mkdir()
    r = _run("check", "--startup", str(startup), "--unit-dir", str(startup),
             env_extra={"AC_DATA_DIR": str(data), "AC_AUTOSTART_DIR": str(tmp_path / "entry_empty")})
    assert r.returncode == 2, "没装自启应当是 2（没做成），实际 %s：%s" % (r.returncode, r.stdout + r.stderr)
    assert "不会自己起" in r.stdout, r.stdout
    assert "autostart.py enable" in r.stdout, "得告诉用户怎么修：%s" % r.stdout


def test_check_detects_broken_pointer(tmp_path):
    """★核心：快捷方式**还在**、但它指向的脚本没了 ⇒ 必须报断链。

    这正是"看起来成功"的死状态：仓库搬家 / data 被清 / 解释器被删之后，
    登录时什么都没起，而旧版 `status` 只看快捷方式在不在，照样说"已装"。
    """
    ok, why = _can_create_shortcut()
    if not ok:
        pytest.skip("这台机器建不了快捷方式（CI runner 无桌面 COM）：%s" % why)
    data = tmp_path / "data_inst"
    startup = tmp_path / "startup_inst"
    entry = tmp_path / "entry_inst"                       # 入口脚本落点（不许写进真仓库 deploy/）
    data.mkdir()
    startup.mkdir()
    r = _run("enable", "--mode", "startup", "--startup", str(startup), "--unit-dir", str(startup),
             env_extra={"AC_DATA_DIR": str(data), "AC_AUTOSTART_DIR": str(entry)})
    assert r.returncode == 0, r.stdout + r.stderr

    rc_ok = _run("check", "--startup", str(startup), "--unit-dir", str(startup),
                 env_extra={"AC_DATA_DIR": str(data), "AC_AUTOSTART_DIR": str(entry)})
    assert rc_ok.returncode == 0, "刚装好应当 OK：%s" % (rc_ok.stdout + rc_ok.stderr)

    entries = [p for p in entry.rglob("ac_watchdog*") if p.is_file()]
    assert entries, "没生成入口脚本：%s" % list(entry.rglob("*"))
    entries[0].unlink()                                   # 模拟"脚本没了"

    rc_bad = _run("check", "--startup", str(startup), "--unit-dir", str(startup),
                  env_extra={"AC_DATA_DIR": str(data), "AC_AUTOSTART_DIR": str(entry)})
    assert rc_bad.returncode == 1, "入口脚本没了却报 OK（假绿）：%s" % (rc_bad.stdout + rc_bad.stderr)
    assert "断链" in rc_bad.stdout or "不存在" in rc_bad.stdout, rc_bad.stdout


def test_check_detects_unparseable_entry(tmp_path):
    """入口脚本**在**、依赖也都在，但内容坏了（解析不过）⇒ 登录时一定失败，必须报红。

    ★这条防的是"BOM 丢了 / 文件被改坏"：PS 5.1 对无 BOM 的中文脚本按 ANSI 读，直接 ParserError，
    而"文件存在 + 依赖都在"照样会让人以为没事。
    """
    if os.name != "nt":
        pytest.skip("只有 PowerShell 入口需要解析校验")
    ok, why = _can_create_shortcut()
    if not ok:
        pytest.skip("这台机器建不了快捷方式：%s" % why)
    data = tmp_path / "d"
    startup = tmp_path / "s"
    entry = tmp_path / "e"
    for d in (data, startup, entry):
        d.mkdir()
    r = _run("enable", "--mode", "startup", "--startup", str(startup), "--unit-dir", str(startup),
             env_extra={"AC_DATA_DIR": str(data), "AC_AUTOSTART_DIR": str(entry)})
    assert r.returncode == 0, r.stdout + r.stderr

    ps1 = [p for p in entry.rglob("*.ps1")][0]
    ps1.write_text("if ($x -eq ) { broken\n", encoding="utf-8-sig")     # 故意写坏（语法都不成立）

    rc = _run("check", "--startup", str(startup), "--unit-dir", str(startup),
              env_extra={"AC_DATA_DIR": str(data), "AC_AUTOSTART_DIR": str(entry)})
    assert rc.returncode == 1, "入口解析不过却报 OK（假绿）：%s" % (rc.stdout + rc.stderr)
    assert "解析不过" in rc.stdout, rc.stdout
