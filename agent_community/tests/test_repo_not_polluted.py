# -*- coding: utf-8 -*-
"""守卫：**"测试往仓库里写运行数据"必须被抓出来**（2026-10-17 真事故：`library/` 又被写进仓库根）。

病灶（两处）：
  ① `agent_community/tests/test_roster_keeps_bindings.py` 造工作间时 `workspace_dir=""`
     ⇒ 平台把共享资料库写到 **仓库根 `library/`**（`.gitignore` 里 2026-10-12 就记过一次）；
  ② `.gitignore` 只是不让它进库，**判据上"有没有写"仍然没人看** ⇒ 谁也不会发现。

修法：① 测试改用 `tmp_path` 当真工作区；② `tools/dual_platform_check.py` 在跑用例**前后**
给"运行数据目录"拍快照，跑完比差异 ⇒ 有新增/改写就**判红**（下面是它的自证）。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

import importlib.util                                          # noqa: E402
from pathlib import Path                                       # noqa: E402


def _load_tool():
    p = Path(_ROOT) / "tools" / "dual_platform_check.py"
    spec = importlib.util.spec_from_file_location("dpc", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pollution_detector_catches_new_and_rewritten_files():
    """检测器必须同时抓：**新增**文件 与**大小变了**的文件（只抓新增会漏掉"每轮重写"）。

    ★2026-10-20 口径校正（判据自己假红）：签名从 `(mtime_ns, size)` 改成**只按 size**。
    原因：`dual_platform_check` 跑用例时，**同机的生产实例/看门狗**会周期性用**相同内容**
    重写 `data/audit.json` 等文件 —— mtime 一变就被判"测试污染"，跑一次红一次绿。
    `conftest.py` 早在 2026-10-12 就把这条判清了（只变 mtime 不算污染）；本工具现在与它同一口径。
    """
    dpc = _load_tool()
    before = {"library/plan.md": 100, "library/README.md": 50, "data/audit.json": 80}
    after = {"library/plan.md": 120, "library/README.md": 50, "data/audit.json": 80,
             "library/新人-测试/assignment.md": 10}
    diff = dpc._pollution_diff(before, after)
    assert any("plan.md" in d and ("被改写" in d or "大小变了" in d) for d in diff), diff
    assert any("assignment.md" in d and "新增" in d for d in diff), diff
    assert not any("README" in d for d in diff), "没动的文件不该报"
    # ★这条是本次修复的**契约**：只变 mtime（同内容重写）**不许报** —— 那是别的进程在写。
    assert not any("audit.json" in d for d in diff), \
        "只变 mtime 被报成了污染（会把同机服务的正常写入误判成测试污染）：%s" % diff


def test_snapshot_covers_runtime_dirs_and_ignores_missing_ones():
    dpc = _load_tool()
    snap = dpc._snapshot_repo_runtime()
    assert isinstance(snap, dict)
    # 快照里只该出现"运行数据"这几类路径（不许把整个仓库抓进来）
    # ⚠️ 2026-10-18：`agent_community/bridges/` 也纳入了快照（**生成的桥脚本**是运行期产物），
    #   所以这里要放行它 —— 这不是"抓多了"，是那条运行数据目录清单又多了正确的一项。
    bad = [k for k in snap
           if Path(k).parts[0] not in ("library", "data", "runtime_logs")
           and Path(k).parts[:2] not in (("agent_community", "data"),
                                         ("agent_community", "bridges"))]
    assert not bad, "快照抓到了不该看的路径：%s" % bad[:5]


def test_roster_tests_do_not_leave_workspace_dir_empty():
    """补充性静态检查（主判据是上面的快照检测；这条防"有人又把它改回空值"）。"""
    src = (Path(_ROOT) / "agent_community" / "tests" / "test_roster_keeps_bindings.py").read_text(
        encoding="utf-8")
    assert 'workspace_dir=""' not in src, "这个测试又用空 workspace_dir 了 ⇒ 会把资料库写进仓库根"
    assert "tmp_path" in src, "它应该用 tmp_path 当工作区"

def _load_conftest():
    """把 conftest 当模块加载（只为拿 `_fingerprint`，不跑 fixture）。"""
    import importlib.util

    p = Path(_ROOT) / "agent_community" / "tests" / "conftest.py"
    spec = importlib.util.spec_from_file_location("ac_conftest", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_conftest_fingerprint_exemption_is_narrow(tmp_path):
    """运行时产物被豁免，业务文件**必须仍然纳入**（豁免放宽 = 守卫失效）。"""
    cf = _load_conftest()
    root = tmp_path / "data"
    files = {
        # 该豁免（服务自己边跑边写）
        "run/platform.lease.json": "{}",
        "run/platform.lock": "pid",
        "monitor/watched.json": "{}",
        "audit.json": '{"a":1}',
        "logs/a.log": "x",
        "backups/state_20261030-000000_auto/_meta.json": "{}",
        # 不该豁免（用户数据）
        "workshops/ws_1/_meta/roster.json": "{}",
        "v5_roster.json": "{}",
        "policy.json": "{}",
        "credentials.json": "{}",
        "workshops/ws_1/hall.md": "hi",
    }
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    fp = cf._fingerprint(root)
    # 指纹键用平台分隔符（Windows 是 `\\`）⇒ 归一后再比，别让分隔符把判据搞红
    sep = chr(92)                      # Windows 路径分隔符；不写字面量，免得转义打架
    keys = {k.replace(sep, "/") for k in fp}
    for rel in ("workshops/ws_1/_meta/roster.json", "v5_roster.json", "policy.json",
                "credentials.json", "workshops/ws_1/hall.md"):
        assert rel in keys, "业务文件被误豁免了：%s（豁免放宽＝守卫失效）" % rel
    for rel in ("run/platform.lease.json", "run/platform.lock", "monitor/watched.json",
                "audit.json", "logs/a.log", "backups/state_20261030-000000_auto/_meta.json"):
        assert rel not in keys, "运行时产物应豁免：%s" % rel
