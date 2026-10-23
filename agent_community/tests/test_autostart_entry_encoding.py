# -*- coding: utf-8 -*-
"""守卫：**开机自启入口脚本的编码**（2026-10-23 补，起因是发现"声称有守卫、实际没有"）。

`tools/autostart.py` 的注释里一直写着"守卫 `agent_community/tests/test_ps1_bom.py` 会扫
data/autostart 下所有 .ps1"——**但这个文件从来不存在**（典型"指向不存在的守卫"，等于没守）。
而它防的是真事故（2026-10-19 本机 pytest 抓到）：

- 入口 `.ps1` 含中文路径；**没有 UTF-8 BOM** 时，Windows PowerShell 5.1 按 ANSI(cp936) 读
  ⇒ 直接 `ParserError` ⇒ **别人的机器上"登录自启"静默失败**（只在我这台碰巧带 BOM 的机器上成立）；
- 老版本还生成过 `.cmd`：cmd.exe 按 ANSI 读脚本，中文路径的**重定向**会静默不写
  ⇒ 看门狗"一声不响什么都没起"。

## 判据（真生成、真解析，不查源码文本）

| 格 | 判据 |
|---|---|
| ① | 生成的 Windows 入口 `.ps1` **带 UTF-8 BOM**（前 3 字节 `EF BB BF`） |
| ② | 它**能被 PowerShell 真解析**（`[ScriptBlock]::Create`；没有 PowerShell 就如实 skip） |
| ③ | Linux 入口 `.sh` 存在且 `bash -n` 语法通过（没有 bash 就如实 skip） |
| ④ | 生成过程**不往真仓库的 `deploy/` 写东西**（测试必须指 `AC_AUTOSTART_DIR` 到 tmp） |
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

TOOL = _ROOT / "tools" / "autostart.py"


def _generate(tmp: Path) -> Path:
    """真跑一次 `enable --dry-run`？不行——dry-run 不写文件。这里真写，但落点在 tmp。"""
    startup = tmp / "startup"
    startup.mkdir(parents=True, exist_ok=True)
    data = tmp / "data"
    data.mkdir(parents=True, exist_ok=True)
    entry = tmp / "entry"
    entry.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "AC_DATA_DIR": str(data), "AC_AUTOSTART_DIR": str(entry)}
    r = subprocess.run([sys.executable, str(TOOL), "enable", "--mode", "startup",
                        "--startup", str(startup), "--unit-dir", str(startup)],
                       cwd=str(_ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180, env=env)
    assert r.returncode == 0, "enable 失败：%s%s" % (r.stdout, r.stderr)
    made = [p for p in entry.iterdir() if p.is_file()]
    assert len(made) == 1, "入口脚本应恰好一个：%s" % [p.name for p in made]
    return made[0]


def test_windows_entry_has_utf8_bom_and_parses():
    """① BOM + ② 真解析（PowerShell 5.1 没 BOM 会按 ANSI 读 ⇒ 中文路径直接 ParserError）。"""
    if os.name != "nt":
        pytest.skip("PowerShell 入口只在 Windows 侧生成")
    with tempfile.TemporaryDirectory(prefix="ac_bom_") as tmp:
        entry = _generate(Path(tmp))
        raw = entry.read_bytes()
        assert raw[:3] == b"\xef\xbb\xbf", (
            "自启入口 .ps1 **没有 UTF-8 BOM** —— PowerShell 5.1 会按 ANSI 读，中文路径必炸：%s" % entry)
        assert entry.suffix.lower() == ".ps1", "Windows 入口应是 .ps1（.cmd 的中文重定向不可靠）：%s" % entry

        pwsh = shutil.which("powershell") or shutil.which("pwsh")
        if not pwsh:
            pytest.skip("这台机器没有 PowerShell，没法真解析")
        # 真解析：把脚本读成文本再交给 PowerShell 的解析器（不执行里面的看门狗）
        ps = ("$t = Get-Content -Raw -LiteralPath '%s'; "
              "$e = $null; [void][System.Management.Automation.Language.Parser]::ParseInput($t, [ref]$null, [ref]$e); "
              "if ($e.Count -gt 0) { $e[0].Message; exit 1 } else { 'PARSE-OK'; exit 0 }"
              % str(entry).replace("'", "''"))
        r = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        assert r.returncode == 0, "入口 .ps1 解析不过（登录时一定失败）：%s%s" % (r.stdout, r.stderr)


def test_linux_entry_passes_bash_syntax_check():
    """③ Linux 入口 `.sh`：`bash -n` 语法必须过（否则 systemd 起来就死）。"""
    if os.name == "nt":
        pytest.skip("非 Windows 才生成 .sh（这里用 Windows 跑测试）")
    with tempfile.TemporaryDirectory(prefix="ac_bom_sh_") as tmp:
        entry = _generate(Path(tmp))
        assert entry.suffix == ".sh", entry
        bash = shutil.which("bash")
        if not bash:
            pytest.skip("没有 bash，没法做语法检查")
        r = subprocess.run([bash, "-n", str(entry)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, "入口 .sh 语法不过：%s" % (r.stderr or r.stdout)


def test_generation_does_not_touch_repo_deploy_dir():
    """④ 测试生成入口时**不许**在真仓库 `deploy/autostart/` 里留下东西（否则工作树就脏了）。"""
    real = _ROOT / "deploy" / "autostart"
    before = sorted(p.name for p in real.iterdir()) if real.is_dir() else None
    with tempfile.TemporaryDirectory(prefix="ac_iso_") as tmp:
        _generate(Path(tmp))
    after = sorted(p.name for p in real.iterdir()) if real.is_dir() else None
    assert before == after, "测试往真仓库 deploy/autostart 写了东西：%s → %s" % (before, after)
