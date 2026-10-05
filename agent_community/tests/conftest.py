"""pytest 共享夹具。

两块内容：
1. sys.path 处理（原有）
2. **真实数据目录隔离**（2026-10-03 事故后新增，见下）

注意必须 append 到末尾而非 insert(0)：
- dev 根存在 platform\\ 包目录，若置于 sys.path 前部会遮蔽标准库 platform 模块，
  导致 pytest 自身 import platform 即崩溃；
- 标准库 types 同名冲突已通过 types.py -> shared_types.py 重命名解决。

────────────────────────────────────────────────────────────────────
数据隔离（事故记录，务必保留）
────────────────────────────────────────────────────────────────────
2026-10-03 事故：V-14 步骤5 的单测 `test_operator_generate_allowed`
直接调用真实端点 `harness_bridge_generate`（只打桩了 bridge_factory.generate），
而该端点内部会调 `save_state()` —— 后者把 `server.DATA_DIR` 下的
tasks.json / rooms.json / task_to_room.json / workshops.json 全量重写。
测试进程里的 state 是**空的**，于是把真实 data/ 的 60 个任务、46 个讨论室、
1 个工作室**整个写空**（harnesses.json 因序列化假 session 抛异常被 try/except 吞掉而幸存）。

根因不是「某个测试写错了」，而是**测试与真实数据目录之间没有隔离**——
任何调用真实端点的测试都可能触发落盘，靠人工记得打桩不可靠。

因此这里做两层防护：
- `isolate_data_dirs`（autouse）：把每个模块级「数据目录/文件」重定向到临时目录；
- `guard_real_data_dir`（session autouse）：记录真实 data/ 的文件指纹，
  会话结束时若有变化**直接 fail** —— 这样即使将来有新的泄漏路径，也会立刻暴露。
"""
import sys
from pathlib import Path

import pytest

_PKG_ROOT = Path(__file__).resolve().parent.parent  # agent_community 包根
_PARENT = _PKG_ROOT.parent                          # import agent_community 的父目录

if str(_PARENT) not in sys.path:
    sys.path.append(str(_PARENT))

REAL_DATA_DIR = _PKG_ROOT / "data"
# 用户级配置（DPAPI 加密的 AI API Key 等）——**在 data/ 之外，原先不在守卫范围内**
REAL_USER_CONFIG = Path.home() / ".agent_community" / "config.json"


# ── 第二层：真实数据目录指纹守卫（会话级）──────────────────────────
def _fingerprint(root: Path) -> dict:
    """记录 data/ 下所有文件的 (大小, mtime_ns)，忽略日志子目录。"""
    fp = {}
    if not root.exists():
        return fp
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(root))
        if rel.startswith("logs"):          # 日志本来就在写，不纳入
            continue
        try:
            st = p.stat()
            fp[rel] = (st.st_size, st.st_mtime_ns)
        except OSError:
            continue
    return fp


def _fp_one(p: Path) -> tuple:
    """单文件指纹 (存在, 大小, mtime_ns)。"""
    try:
        st = p.stat()
        return (True, st.st_size, st.st_mtime_ns)
    except OSError:
        return (False, 0, 0)


@pytest.fixture(scope="session", autouse=True)
def guard_real_data_dir():
    """会话前后对比真实 data/ **与用户级 config.json** 的指纹；被改动就 fail。

    这是「无论什么机制、只要碰了真实数据就必须暴露」的安全网。
    """
    before = _fingerprint(REAL_DATA_DIR)
    before_cfg = _fp_one(REAL_USER_CONFIG)
    yield
    after = _fingerprint(REAL_DATA_DIR)
    changed = []
    for k in sorted(set(before) | set(after)):
        if before.get(k) != after.get(k):
            b = before.get(k)
            a = after.get(k)
            changed.append(f"    {k}: {b} -> {a}")

    after_cfg = _fp_one(REAL_USER_CONFIG)
    if before_cfg != after_cfg and before_cfg[0]:
        changed.append(
            f"    [用户配置] {REAL_USER_CONFIG}: {before_cfg} -> {after_cfg}"
        )

    if changed:
        pytest.fail(
            "测试污染了真实数据 "
            f"{REAL_DATA_DIR} 或 {REAL_USER_CONFIG}"
            "（2026-10-03 数据丢失 / 2026-10-04 API Key 被覆写，均为同类风险）：\n"
            + "\n".join(changed)
            + "\n\n修法：让被测代码走隔离后的路径（见 conftest.isolate_data_dirs），"
              "或在测试里显式 monkeypatch 掉落盘函数。"
        )


# ── 第一层：把模块级数据目录/文件重定向到临时目录 ──────────────────
@pytest.fixture(autouse=True)
def isolate_data_dirs(tmp_path, monkeypatch):
    """把 server / audit / policy / memory / harness_launcher 的数据落点改到 tmp_path。

    只重定向「已存在且 import 时就定下路径」的对象；找不到的模块静默跳过，
    避免测试因模块重构而整体崩掉。
    """
    import importlib

    tmp = tmp_path / "_isolated_data"
    tmp.mkdir(parents=True, exist_ok=True)

    def _mod(name):
        try:
            return importlib.import_module(name)
        except Exception:
            return None

    # 1) server：DATA_DIR 与由它派生的文件常量
    server = _mod("agent_community.platform.server")
    if server is not None:
        monkeypatch.setattr(server, "DATA_DIR", tmp, raising=False)
        monkeypatch.setattr(server, "PLUGINS_FILE", tmp / "plugins.json", raising=False)
        monkeypatch.setattr(server, "PLUGIN_MODES_FILE", tmp / "workshop_modes.json", raising=False)

    # 2) audit：模块级单例 audit_log 的目录与文件路径
    audit = _mod("agent_community.platform.audit")
    if audit is not None and getattr(audit, "audit_log", None) is not None:
        log = audit.audit_log
        monkeypatch.setattr(log, "data_dir", tmp, raising=False)
        monkeypatch.setattr(log, "_path", tmp / "audit.json", raising=False)
        monkeypatch.setattr(log, "events", [], raising=False)

    # 3) policy：模块级单例 policy_engine
    pol = _mod("agent_community.platform.policy")
    if pol is not None and getattr(pol, "policy_engine", None) is not None:
        eng = pol.policy_engine
        monkeypatch.setattr(eng, "data_dir", tmp, raising=False)
        monkeypatch.setattr(eng, "config_path", tmp / "policy.json", raising=False)
        monkeypatch.setattr(eng, "pending_path", tmp / "policy_pending.json", raising=False)
        monkeypatch.setattr(eng, "_pending", [], raising=False)

    # 4) memory：TaskMemory / CapabilityLedger 的记忆目录
    mem = _mod("agent_community.platform.memory")
    if mem is not None:
        for attr in ("task_memory", "capability_ledger"):
            obj = getattr(mem, attr, None)
            if obj is None:
                continue
            for fld in ("dir", "data_dir", "memory_dir", "_dir", "_path"):
                if hasattr(obj, fld):
                    try:
                        monkeypatch.setattr(obj, fld, tmp / "memory", raising=False)
                    except Exception:
                        pass

    # 5) harness_launcher：日志/状态目录
    hl = _mod("agent_community.platform.harness_launcher")
    if hl is not None:
        monkeypatch.setattr(hl, "DATA_DIR", tmp, raising=False)

    # 6) **用户级配置** ~/.agent_community/config.json
    #    （2026-10-04 事故：测试调 POST /api/config 直接把真实配置覆写，
    #      连 DPAPI 加密的 AI API Key 一起换成测试值 `"k"`，**不可恢复**。
    #      它不在 agent_community/data/ 下，所以原两层防护都拦不住。）
    ucfg = _mod("agent_community.config")
    if ucfg is not None:
        for _attr in ("CONFIG_FILE", "_CONFIG_FILE", "CONFIG_PATH", "_CONFIG_PATH"):
            if hasattr(ucfg, _attr):
                monkeypatch.setattr(ucfg, _attr, tmp / "user_config.json",
                                    raising=False)

    return tmp
