#!/usr/bin/env python
"""桥模板渲染测试 — 完全离线，不依赖服务。

对 bridge_templates 下每个模板做真实渲染：断言无残留占位符、生成脚本可编译、
必填字段缺失时正确报错、generate 可落盘到指定目录。
"""
from __future__ import annotations

import py_compile
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_community.platform.bridge_factory import (  # noqa: E402
    BridgeTemplateError,
    generate,
    list_templates,
    render,
)

GREEN, RED, RESET = "\033[92m", "\033[91m", "\033[0m"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))
    print(f"{GREEN}[PASS]{RESET} {name}" if cond else f"{RED}[FAIL]{RESET} {name} {detail}", flush=True)


# 示例取值池（全部为示例值，不含任何真实环境信息）
SAMPLE_VALUES = {
    "HARNESS_ID": "harness-a",
    "HARNESS_NAME": "示例 Harness A",
    "INBOX_DIR": str(Path(tempfile.gettempdir()) / "harness-a-inbox"),
    "WORK_DIR": str(Path(tempfile.gettempdir()) / "harness-b-work"),
    "PLATFORM_URL": "http://127.0.0.1:18920",
    "TRIGGER_CMD": "",
    "ACP_COMMAND": "node /path/to/acp-server",
    "ACP_CWD": "",
    "MODEL_NAME": "example-model",
    "PROVIDER": "example-provider",
    "DESCRIPTION": "example",
}


def params_for(meta: dict) -> dict:
    p = {}
    for f in meta.get("required_fields", []):
        p[f] = SAMPLE_VALUES.get(f, f"example-{f.lower()}")
    return p


def main() -> int:
    tpls = list_templates()
    check("R1 模板列表非空", len(tpls) > 0, str([t["name"] for t in tpls]))

    tmp_out = Path(tempfile.mkdtemp(prefix="ac_bridge_out_"))
    for t in tpls:
        name = t["name"]
        meta = t
        try:
            params = params_for(meta)
            code = render(name, params)
        except Exception as e:  # noqa: BLE001
            check(f"R[{name}] 渲染成功", False, repr(e))
            continue
        check(f"R[{name}] 渲染成功且长度合理", len(code) > 200, f"len={len(code)}")
        check(f"R[{name}] 无残留占位符", "{{" not in code and "}}" not in code)
        check(f"R[{name}] 注入示例值生效", "harness-a" in code or "harness-b" in code)

        f = tmp_out / f"bridge_{name}.py"
        f.write_text(code, encoding="utf-8")
        try:
            py_compile.compile(str(f), doraise=True, cfile=str(tmp_out / f"{name}.pyc"))
            check(f"R[{name}] 生成脚本可编译", True)
        except Exception as e:  # noqa: BLE001
            check(f"R[{name}] 生成脚本可编译", False, repr(e))

        try:
            out = generate(name, params, tmp_out / f"gen_{name}")
            check(f"R[{name}] generate 落盘成功", Path(out).exists())
        except Exception as e:  # noqa: BLE001
            check(f"R[{name}] generate 落盘成功", False, repr(e))

    # 必填字段缺失 → 必须报错而不是静默产出坏脚本
    if tpls:
        first = tpls[0]
        required = first.get("required_fields") or []
        if required:
            try:
                render(first["name"], {})
                check("R9 缺少必填字段时报错", False, "未抛异常")
            except BridgeTemplateError:
                check("R9 缺少必填字段时报错", True)
            except Exception as e:  # noqa: BLE001
                check("R9 缺少必填字段时报错", False, f"异常类型非 BridgeTemplateError: {e!r}")

    # 不存在的模板 → 必须报错
    try:
        render("__not_exist__", {})
        check("R10 未知模板报错", False, "未抛异常")
    except BridgeTemplateError:
        check("R10 未知模板报错", True)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n桥模板渲染测试: {passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
