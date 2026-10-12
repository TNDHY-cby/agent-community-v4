# -*- coding: utf-8 -*-
"""★守卫：**健康检查监控的端口 = 看门狗负责恢复的端口**（不许有"被监控却没人管"的服务）。

**为什么单独立一条**（2026-10-12 实测缺口）：
  `deploy/healthcheck.ps1` **监控 3 项**（平台 18920 / A2A 9104 / **gRPC 9105**），
  而 `deploy/watchdog_all.py` 只恢复前 **2** 项 ⇒ **gRPC 掉了没人拉**。
  实测确实发生过：9105 **静默 DOWN**（健康检查一直报 DOWN，而平台侧毫无动作），
  我是顺手看健康检查才发现的 —— 属于"失败没人说话 + 装好并不能自愈"。

**原则**：**监控集 = 恢复集**。凡是被健康检查盯着的服务，就必须有人负责把它恢复；
否则那条检查只是"报丧"，不是"保障"。

**判据**：解析 `healthcheck.ps1` 里用到的 `AC4_*_PORT`，与 `watchdog_all.build_commands()`
返回的目标端口集合**逐一相等**（顺序无关）。
"""
from __future__ import annotations

import importlib.util
import os
import re

# 本文件在 `agent_community/tests/` 下 ⇒ 要退**三层**才到仓库根（`tests` → `agent_community` → 根）
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HEALTHCHECK = os.path.join(REPO, "deploy", "healthcheck.ps1")
WATCHDOG = os.path.join(REPO, "deploy", "watchdog_all.py")


def _monitored_ports() -> set:
    """healthcheck.ps1 里 `Get-Cfg "AC4_*_PORT"` 用到的配置键 → 端口名（如 SERVER/A2A/GRPC）。

    ⚠️键名里**可能带数字**（`AC4_A2A_PORT` 的 `2`）—— 我第一版用 `[A-Z]+` 就漏掉了 A2A，
    于是误报"看门狗恢复了没被监控的端口"。**又是"判据太窄"**（本项目今天第 N 次）。
    """
    src = open(HEALTHCHECK, encoding="utf-8", errors="replace").read()
    return set(re.findall(r"AC4_([A-Za-z0-9]+)_PORT", src))


def _recovered_ports() -> set:
    """watchdog_all.build_commands() 覆盖的端口 —— 用它**真实代码**算，不靠肉眼看源码。"""
    spec = importlib.util.spec_from_file_location("wd_all_probe", WATCHDOG)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    # AC4_A2A_HOST 显式给回环：默认 tailscale 会去查 Tailscale IP（测试里既慢又可能没有）
    cfg = {"AC4_A2A_HOST": "127.0.0.1"}
    cmds = m.build_commands(cfg, "python", os.path.dirname(WATCHDOG))
    return set(cmds.keys())


def test_every_monitored_service_is_also_recovered():
    """★被健康检查监控的每个服务，看门狗**都必须**负责恢复它。"""
    monitored = _monitored_ports()
    # 端口名 → 实际端口号（按 watchdog 的默认值约定）
    name2port = {"SERVER": 18920, "A2A": 9104, "GRPC": 9105}
    want = {name2port[n] for n in monitored if n in name2port}
    got = _recovered_ports()
    missing = sorted(want - got)
    assert not missing, (
        "★这些端口**被健康检查监控、却不在看门狗的恢复目标里** ⇒ 掉线没人拉、"
        "健康检查只会一直报 DOWN（实测 9105 gRPC 就这么静默挂过）：%s\n"
        "监控集=%s / 恢复集=%s" % (missing, sorted(want), sorted(got))
    )


def test_the_recovered_set_has_no_orphan_targets():
    """反向：看门狗也别去恢复一个**没人监控**的端口（否则它挂了也没人知道）。"""
    monitored = _monitored_ports()
    name2port = {"SERVER": 18920, "A2A": 9104, "GRPC": 9105}
    want = {name2port[n] for n in monitored if n in name2port}
    got = _recovered_ports()
    extra = sorted(got - want)
    assert not extra, "★看门狗恢复了没被监控的端口（挂掉无从发现）：%s" % extra
