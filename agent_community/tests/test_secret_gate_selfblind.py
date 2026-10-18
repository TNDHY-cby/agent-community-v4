# -*- coding: utf-8 -*-
"""守卫：**凭据闸门必须能抓到"写在注释里的真凭据"**（它曾经抓不到，导致真令牌发布出去）。

## 真事故（2026-10-18，老板问「我的 API Key 有没有一起上传」时查出来的）

`tests/scan_sensitive.py` 里原先有一句 `if p.resolve() == SELF: continue` —— **整个跳过自己**，
理由是"本文件源码里写着这些正则字面量，否则必然自我命中"。
代价：**写在它自己注释里的真凭据永远扫不到**。而确实发生过：一行注释里贴了平台令牌真值
（`--token "<32 位>"`），发布到 Gitee 了，而 `--secrets-only` 一路返回 0。

修法：改成"扫自己，但只跳过**定义规则**的那几行"。

判据（都能机器判）：
  ① 临时目录里放一行 `# --token "<32 位>"`（注释形式）⇒ 闸门必须**退出码 1** 并指出该文件；
  ② 扫本仓库根 ⇒ **退出码 0**，且**不因自身正则字面量自我命中**（否则修复就退化成误报）；
  ③ 源码里不许再出现"整文件跳过自己"的写法。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCANNER = _ROOT / "tests" / "scan_sensitive.py"


def _run(target: Path, scanner: Path = _SCANNER):
    env = {**os.environ}
    env.pop("PYTHONPATH", None)
    return subprocess.run([sys.executable, str(scanner), str(target), "--secrets-only"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=300, env=env)


def _probe_line() -> str:
    """拼出一行"注释里的真凭据" —— **不许在源码里出现完整字面量**，否则闸门会抓我们自己（实测抓到过）。"""
    flag = "--tok" + "en"
    fake = "ABCDEFGHIJKLMNOP" + "QRSTUVWXYZ012345"
    return '# 当年踩过的坑：`%s "%s"`\n' % (flag, fake)


def test_gate_catches_credential_in_comment(tmp_path):
    (tmp_path / "leak_sample.py").write_text(_probe_line(), encoding="utf-8")
    r = _run(tmp_path)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 1, "注释形式的真凭据没被抓到（守卫又看不见自己了）：\n%s" % out[-600:]
    assert "leak_sample.py" in out, "抓到了但没指出文件：\n%s" % out[-600:]


def test_gate_passes_on_a_published_subtree_and_does_not_self_hit():
    """扫**发布面的一小块**必须 OK，且不因自身规则定义行误报。

    ⚠️ 早先这条是扫**整个仓库根**：在 Windows 上没问题，但在 WSL 里 `/mnt/d` 走 9p，
    1600+ 文件扫不完 ⇒ `subprocess` 超时 ⇒ Linux 侧假红。
    **判据要真，但也要扫得完**：这里只扫 `tools/`（发布面的一小块，几十个文件）。
    全仓扫描是**发布闸门**的活（它在 oss 副本里跑，速度快得多）。
    """
    target = _ROOT / "tools"
    assert target.is_dir(), "缺 tools/"
    r = _run(target)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, "发布面脚本目录出现真实凭据（必须处理）：\n%s" % out[-800:]
    assert "scan_sensitive.py:1" not in out, "闸门在自己身上误报了：\n%s" % out[-600:]


def test_scanner_does_not_self_hit_when_scanning_itself(tmp_path):
    """把扫描器**单独**放进空目录再扫它自己 ⇒ 必须 OK（规则定义行放行，且不再"整文件跳过"）。"""
    import shutil
    shutil.copy(_SCANNER, tmp_path / "scan_sensitive.py")
    r = _run(tmp_path, scanner=tmp_path / "scan_sensitive.py")
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, "扫描器扫自己时误报（规则定义行没放行）：\n%s" % out[-800:]
    assert "扫描 1 个文件" in out or "扫描 2 个文件" in out, "它没真的扫自己（又整文件跳过了？）：\n%s" % out[-400:]


def test_scanner_no_longer_skips_itself_wholesale():
    src = _SCANNER.read_text(encoding="utf-8", errors="replace")
    code_lines = [ln for ln in src.splitlines() if not ln.strip().startswith("#")]
    joined = "\n".join(code_lines)
    assert "if p.resolve() == SELF:" not in joined, \
        "又出现了「整文件跳过自己」的代码（注释里提到不算）—— 那会让注释里的真凭据永远扫不到"
    assert "_is_rule_definition" in src, "缺少「只放行规则定义行」的辅助函数"
