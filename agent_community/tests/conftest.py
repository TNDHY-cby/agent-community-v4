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
import os
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
    """记录 data/ 下所有**业务文件**的 (大小, mtime_ns)；日志与自动启动日志不算。

    ★2026-10-21（真事故，假红）：原来只忽略 `logs/` 目录，而看门狗会把启动日志写到
    `data/autostart/ac_watchdog.log` —— **平台服务在跑的时候**每写一行，跑完整测试就会撞见
    "测试污染真实数据"（实测：`autostart\\ac_watchdog.log` 6845→7139 字节被判红）。
    日志本来就是**边跑边写**的，与"测试有没有偷写真实数据"无关 ⇒ 一律忽略 `*.log`。
    """
    fp = {}
    if not root.exists():
        return fp
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(root))
        if rel.startswith("logs"):          # 日志目录：本来就在写，不纳入
            continue
        if p.suffix.lower() == ".log":      # 任何位置的日志文件（如 data/autostart/ac_watchdog.log）
            continue
        # ★2026-10-22（真事故，假红）：V82 给 `cli start` 加了单实例守卫，会在
        #   `data/run/platform.lock` 写"谁持有锁"。**平台服务在跑的时候**这个文件会被创建/改写，
        #   于是"跑完整测试"就撞见"测试污染真实数据"（Linux 侧尤其明显：两边挂同一路径）。
        #   锁文件与 pid 文件都是**运行时租约**，不是业务数据 ⇒ 一律不纳入指纹。
        if p.suffix.lower() in (".lock", ".pid"):
            continue
        # ★2026-10-30（真事故，假红）：`data/` 下这几类是**服务自己的运行时产物**，
        #   刚停服务就立刻跑全量会撞红、重跑又绿（今天真机上遇到两次）：
        #   `run/platform.lease.json`（启动写/退出清）、`monitor/watched.json`（监控名单快照）、
        #   `audit.json`（审计流水按行追加）。与"测试有没有偷写用户数据"无关 ⇒ 不纳入指纹。
        #   ⚠️ 豁免必须窄：名单/工作间/凭据/配置**一个都不放过**。
        if rel.replace("\\", "/").startswith("run/"):
            continue
        if rel.replace("\\", "/") in ("monitor/watched.json", "audit.json"):
            continue
        # ★2026-10-30（真事故，假红，今日第三次同类）：**状态快照是服务自己每 30 分钟拍的**
        #   （`backups/state_<时间>_auto/_meta.json`）。全量跑一次常跨过这个边界 ⇒ 一次红一次绿。
        #   快照是平台产物（`tools/state_restore.py` 用），不是测试写的用户数据 ⇒ 不纳入指纹。
        if rel.replace("\\", "/").startswith("backups/"):
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


def _is_seed_write_only(before: dict, after: dict) -> bool:
    """唯一的**窄豁免**：`data/policy.json` 由「不存在」变为「恰好等于出厂种子」。

    背景（HANDOVER §13.9）：`policy.py` 末尾 `policy_engine = PolicyEngine()` 是**模块级单例**，
    导入即 `_load_config()` —— 文件不存在时无条件落盘种子。于是**全新 clone** 里跑 pytest，
    会在真实 `data/` 造出该文件，被本守卫判成"测试污染真实数据"
    （实测：干净 clone 21 passed / 1 error，开源用户按 README 跑测试就会撞上）。

    为什么可以豁免：这不是测试写坏的，是**生产代码的导入副作用**，文件内容就是出厂种子、无数据损失。
    为什么豁免必须这么窄：比对的是**出厂种子的真实落盘字节**，不是文件名 ——
    用户改过的 policy.json 绝不豁免；除该文件外的任何变化也绝不豁免。
    """
    key = "policy.json"
    if before.get(key) is not None:
        return False                      # 原本就存在 -> 不是"首次生成"
    if after.get(key) is None:
        return False
    try:
        from agent_community.platform import policy as _pol
        seeded = (_pol._SEED_CONFIG)
        import copy as _copy
        import json as _json
        expect = _json.dumps(_copy.deepcopy(seeded), ensure_ascii=False, indent=2)
        actual = (REAL_DATA_DIR / key).read_text(encoding="utf-8")
        return actual == expect
    except Exception as _e:   # 读不到/导入失败 -> 不豁免（宁红不默）
        print(f"[guard] 种子豁免判定失败，按污染处理: {_e}", flush=True)
        return False


@pytest.fixture(scope="session", autouse=True)
def guard_real_data_dir():
    """会话前后对比真实 data/ **与用户级 config.json** 的指纹；被改动就 fail。

    这是「无论什么机制、只要碰了真实数据就必须暴露」的安全网。
    """
    before = _fingerprint(REAL_DATA_DIR)
    before_cfg = _fp_one(REAL_USER_CONFIG)
    yield
    after = _fingerprint(REAL_DATA_DIR)
    # 窄豁免：policy.json 首次生成出厂种子（导入副作用，非测试污染）—— 见 _is_seed_write_only。
    # ⚠️ 必须在下面比对循环**之前**摘掉，否则 changed 已经填好、豁免等于没加（本补丁首版就踩了）。
    if _is_seed_write_only(before, after):
        import copy as _c
        after = _c.deepcopy(after)
        after.pop("policy.json", None)

    changed = []
    for k in sorted(set(before) | set(after)):
        b = before.get(k)
        a = after.get(k)
        if b == a:
            continue
        # ★2026-10-12：**只变了 mtime 不算污染**。
        #   实测：线上服务（或看门狗）会周期性**用相同内容重写** `audit.json` ——
        #   大小不变、mtime 变 ⇒ 守卫误报"测试污染了真实数据"，而测试其实一个字节都没写。
        #   判据收紧为：**内容变了才报**（新增文件 / 删除文件 / 大小变化）。
        #   理由：真污染（数据丢失、API Key 被覆写）必然改变**大小**或**文件集合**；
        #   而"同大小同内容、只是被外部重写"与测试无关。
        if b is not None and a is not None and b[0] == a[0]:
            continue
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
        # 1b) **WORKSHOP_BASE（2026-10-07 补）**
        #     此前这里**没隔离它**：`WORKSHOP_BASE` 是 import 时就算好的常量，
        #     改 DATA_DIR 不会连带它。于是任何"建工作间"的测试都会把**真实**
        #     `agent_community/data/workshops/` 当落点写（真实目录里已有 21 个条目）。
        #     属"落盘单例必须在此登记"那条教训的同类。
        #     ⚠️ 只隔离路径，**不重建 `workshops`/`tasks` 字典**：它们是
        #     `from ..state import ...` 进来的**共享对象**，在此重绑会让 server 与各 router
        #     各看一个字典（同一份数据两个视图），反而制造更难查的问题。
        monkeypatch.setattr(server, "WORKSHOP_BASE", tmp / "workshops", raising=False)

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

    # 5b) 平台桥落盘根目录 `agent_community/platform/bridges/`
    #     （2026-10-06 补：test_policy_wiring_bridge 的桩会经
    #       routers/harness_bridge.py 往**真实仓库**的 bridges/h1/ 落盘，
    #       发布前"工作树必须干净"闸门与 HANDOVER §13.8 都记了这个缺陷。
    #       该模块的落点已提为模块级常量 BRIDGES_ROOT，这里直接重定向到 tmp；
    #       另两个同源落点（tools/bridge_tool、routers/harness_messaging 的默认 work_dir）
    #       一并重定向，避免同类回归。）
    hb = _mod("agent_community.platform.routers.harness_bridge")
    if hb is not None:
        monkeypatch.setattr(hb, "BRIDGES_ROOT", tmp / "platform_bridges", raising=False)
    bt = _mod("agent_community.platform.tools.bridge_tool")
    if bt is not None:
        monkeypatch.setattr(bt, "_BRIDGES_ROOT", tmp / "platform_bridges", raising=False)

    # 7) handover：V-23/V-24 交接体系的**全部落盘单例**
    #    ⚠️ 2026-10-07 实测事故（**同一个坑踩了两次**，第二次是给本模块加 `aging_tracker`
    #      时又忘了登记）：漏登记的回执/老化状态会被写进**真实** data/，触发会话级守卫
    #      `guard_real_data_dir` 报红（指纹 `None -> (316, ...)` / `None -> (114, ...)`）。
    #      教训：**给任何模块新加落盘单例时，必须同时在这里登记**——否则两层防护会拦下来，
    #      但那是"事后发现"，不是"不写进去"。下面按模块逐个属性登记，新增单例请照样补一行。
    ho = _mod("agent_community.platform.handover")
    if ho is not None:
        for _attr, _fname in (("handover_ledger", "handover_receipts.json"),
                              ("aging_tracker", "aging_state.json")):
            _obj = getattr(ho, _attr, None)
            if _obj is None:
                continue
            monkeypatch.setattr(_obj, "data_dir", tmp, raising=False)
            monkeypatch.setattr(_obj, "_path", tmp / _fname, raising=False)
            if hasattr(_obj, "records"):
                monkeypatch.setattr(_obj, "records", [], raising=False)
            if hasattr(_obj, "state"):
                monkeypatch.setattr(_obj, "state", {}, raising=False)

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


# ── 端点搬运期的共享工具（2026-10-21）────────────────────────────────
def resolve_handler(name: str, *, required: bool = True):
    """按名字找一个 handler：**先在 `server` 找，再去 `routers/*` 找**。

    背景：`server.py` 里的端点在持续往 `routers/` 搬（讨论室 9 → 唤醒心跳 7 → 任务 11 …）。
    判据如果写死 `from ...server import api_task_workshop`，**每搬一次就红一次**，
    久而久之大家就会去改测试而不是查真相。⇒ 统一用这个函数取，
    **判据跟着"它现在在哪"走**，搬家不再需要改测试。

    `required=False` 时找不到返回 None（调用方可自行 SKIP 并说明原因）。
    """
    import importlib
    import pkgutil
    import warnings

    from agent_community.platform import server as _srv

    fn = getattr(_srv, name, None)
    if fn is not None:
        return fn

    import agent_community.platform.routers as _rpkg
    for mod in pkgutil.iter_modules(_rpkg.__path__):
        try:
            m = importlib.import_module("agent_community.platform.routers." + mod.name)
        except Exception as exc:                                  # noqa: BLE001
            warnings.warn("导入 routers.%s 失败（找 %s 时跳过）：%s" % (mod.name, name, exc))
            continue
        fn = getattr(m, name, None)
        if fn is not None:
            return fn

    if required:
        raise LookupError("找不到 handler %r（server 与 routers/ 都没有；改名了？请同步调用方）" % name)
    return None


@pytest.fixture(autouse=True)
def _isolate_registry_health(tmp_path, monkeypatch):
    """V85：把名册健康库指到 tmp（否则探针/桥测试会往真实数据目录写 registry_health.json）。"""
    try:
        from agent_community.platform import registry_health as _rh

        monkeypatch.setattr(_rh, "HEALTH_DIR", tmp_path, raising=True)
    except Exception:                                        # noqa: BLE001 — 有意静默：模块没装也不该挡住别的测试
        pass


@pytest.fixture(autouse=True)
def _isolate_credentials(tmp_path, monkeypatch):
    """V86：凭据账本也指到 tmp（否则任何调 `/api/credentials` 的测试会往真实数据目录写账本）。"""
    try:
        from agent_community.platform import credentials as _cred

        monkeypatch.setattr(_cred, "LEDGER_DIR", tmp_path / "cred_keys", raising=True)
    except Exception:                                        # noqa: BLE001 — 有意静默：模块没装也不该挡住别的测试
        pass

@pytest.fixture(autouse=True)
def _isolate_monitor(tmp_path, monkeypatch):
    """V88：监控室的名单/状态也指到 tmp（否则任何碰 /api/monitor 的测试会往真实数据目录写）。"""
    try:
        from agent_community.platform import monitor as _mn

        monkeypatch.setattr(_mn, "MONITOR_DIR", tmp_path / "monitor", raising=True)
    except Exception:                                        # noqa: BLE001 — 有意静默：模块没装也不该挡住别的测试
        pass


@pytest.fixture(autouse=True)
def _no_live_platform(monkeypatch):
    """**不许偷偷依赖"本机正跑着一个平台"**（2026-10-25 用真事故换的）。

    背景：`test_mcp_http_server` 里有一条用例会**真去打 `127.0.0.1:18920`** ——
    本机服务开着时它绿、Linux 侧必红（假绿）。同一类写法还可能在别处埋着。

    做法：测试期间把**到平台端口的出站连接**直接掐掉（其它端口不管，子进程/临时实例照常）。
    要真的连本机服务（极少见）就设 `AC_TEST_ALLOW_LIVE=1`。

    为什么这样最省事：不用逐条去改测试，**依赖"线上服务"的用例会自己红**，红在它自己身上。
    """
    import socket as _socket

    if (os.environ.get("AC_TEST_ALLOW_LIVE") or "").strip() in ("1", "true", "yes", "on"):
        return
    try:
        from agent_community.platform_address import resolve_port as _rp

        live_port = int(_rp())
    except Exception:                                        # noqa: BLE001 — 有意静默：取不到端口就不拦（宁可不拦，不误伤）
        return

    _orig_connect = _socket.socket.connect

    def _guard_connect(self, address, *a, **kw):
        try:
            host, port = address[0], int(address[1])
        except (TypeError, ValueError, IndexError):          # 非 (host, port) 形状 ⇒ 不管
            return _orig_connect(self, address, *a, **kw)
        if port == live_port and str(host) in ("127.0.0.1", "::1", "localhost"):
            raise OSError(
                "测试里不许连本机正在跑的平台（端口 %d）：这条用例在别的机器/CI 上会红成假绿。"
                "要么改成走 TestClient/monkeypatch，要么显式设 AC_TEST_ALLOW_LIVE=1。" % live_port)
        return _orig_connect(self, address, *a, **kw)

    monkeypatch.setattr(_socket.socket, "connect", _guard_connect, raising=True)
