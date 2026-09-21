#!/usr/bin/env python
"""核心冒烟测试 — 完全离线，不依赖运行中的服务，不污染正式 data 目录。

覆盖：
  A. 配置模块：默认配置字段、端口默认值、密钥脱敏
  B. 桥模板库：模板齐全、必填字段齐备、模板文件存在
  C. 仓库结构：开源所需文件齐全
  D. 服务模块：可导入、路由存在、数据目录可用 AC_DATA_DIR 覆盖并自动创建
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # 只加入项目根，绝不能加入 agent_community 目录（platform 同名遮蔽）

GREEN, RED, RESET = "\033[92m", "\033[91m", "\033[0m"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))
    print(f"{GREEN}[PASS]{RESET} {name}" if cond else f"{RED}[FAIL]{RESET} {name} {detail}", flush=True)


def main() -> int:
    # ── A. 配置模块 ──
    from agent_community import config as cfg

    need = {"ai_provider", "ai_base_url", "ai_api_key", "ai_model", "ai_temperature", "ai_thinking", "wakeup_enabled", "port"}
    check("A1 默认配置字段齐备", need <= set(cfg.DEFAULT_CONFIG), f"缺: {need - set(cfg.DEFAULT_CONFIG)}")
    check("A2 默认端口为 18920", cfg.DEFAULT_CONFIG.get("port") == 18920, str(cfg.DEFAULT_CONFIG.get("port")))
    check("A3 默认 api_key 为空（不含真实密钥）", cfg.DEFAULT_CONFIG.get("ai_api_key") == "")
    masked = cfg.mask_api_key("sk-abcdefghijklmnop")
    check("A4 密钥脱敏保留前后各 4 位", masked.startswith("sk-a") and masked.endswith("mnop") and "*" in masked, masked)

    # ── B. 桥模板库 ──
    from agent_community.platform.bridge_factory import list_templates, render, BridgeTemplateError

    tpls = list_templates()
    names = {t["name"] for t in tpls}
    check("B1 桥模板数量 >= 3", len(tpls) >= 3, str(names))
    check("B2 三类标准模板齐全", {"file_poll", "pending_poll", "cli_acp"} <= names, str(names))
    check("B3 各模板必填字段非空", all(t.get("required_fields") for t in tpls), str([t.get("required_fields") for t in tpls]))
    ok_file = all(Path(t["template_file"]).exists() for t in tpls)
    check("B4 各模板文件存在", ok_file)

    # ── C. 仓库结构 ──
    for f in ["README.md", "LICENSE", "CONTRIBUTING.md", "CHANGELOG.md", ".gitignore",
              "requirements.txt", "config.example.json", "harnesses.example.json"]:
        check(f"C 仓库文件存在: {f}", (ROOT / f).exists())

    # ── D. 服务模块（临时数据目录，避免污染）──
    tmp_data = Path(tempfile.mkdtemp(prefix="ac_smoke_data_"))
    os.environ["AC_DATA_DIR"] = str(tmp_data)
    os.environ.pop("AC_PORT", None)
    from agent_community.platform import server

    check("D1 服务默认端口 18920", server.DEFAULT_PORT == 18920, str(server.DEFAULT_PORT))
    check("D2 数据目录遵循 AC_DATA_DIR", str(server.DATA_DIR) == str(tmp_data), str(server.DATA_DIR))
    routes = {getattr(r, "path", "") for r in server.app.routes}
    for path in ["/api/status", "/api/agents", "/api/harness/register", "/api/harness/task-result", "/api/workshop"]:
        check(f"D3 路由存在: {path}", path in routes)

    server.save_state()
    check("D4 save_state 自动创建数据目录", tmp_data.exists() and tmp_data.is_dir())
    check("D5 落盘 harnesses.json", (tmp_data / "harnesses.json").exists())
    loaded = json.loads((tmp_data / "harnesses.json").read_text(encoding="utf-8"))
    check("D6 空状态落盘为合法 JSON 对象", isinstance(loaded, dict))
    server.load_state()
    check("D7 load_state 可正常回读", True)

    # ── 汇总 ──
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n冒烟测试: {passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
