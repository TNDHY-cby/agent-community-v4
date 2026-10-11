"""通用脚本框架 · 契约运行时（被注入到每个动作脚本里，见 V26 §3）。

**为什么要有这个文件**：每个 harness 的动作脚本实现不同，但**契约必须一致**——
调用方式、stdout 形状、退出码语义、evidence、超时、幂等。把这段集中一处，
由渲染器注入到生成的脚本里（生成物**自包含**，不依赖导入路径）。

契约（V26 §3）：
  · 调用：`script --input <json路径>` 或从 **stdin** 读同一份 JSON；
  · 环境变量同时提供 AC_ACTION / AC_HARNESS_ID / AC_WORKSHOP_ID / AC_MEMBER_ID /
    AC_ROLE / AC_PROJECT / AC_WORKSPACE_DIR / AC_PLATFORM_URL / AC_DATA_DIR；
  · **stdout 只允许一个 JSON**；日志/调试一律走 **stderr**；
  · 退出码：0 = 成功且能自证；10 = 成功但无法自证（平台标「未核实」）；其它 = 失败。
"""

from __future__ import annotations

import json
import os
import sys
import time

EXIT_OK = 0
EXIT_UNVERIFIED = 10
EXIT_FAILED = 1
EXIT_BAD_INPUT = 2


class Failed(Exception):
    """确定性失败：平台据此走降级链，**不得当成成功**。"""


class Unverified(Exception):
    """做了但无法自证：平台标「未核实」（准则⑤ 不许假装成功）。"""


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default) or default


def log(msg: str) -> None:
    """日志只走 stderr —— stdout 是契约通道，混进任何杂质平台就解析不了。"""
    sys.stderr.write(str(msg).rstrip() + "\n")
    sys.stderr.flush()


def read_input(argv: list[str] | None = None) -> dict:
    """`--input <path>` 优先；否则读 stdin；两者都空则返回 {}（动作可只用 env）。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    path = ""
    for i, a in enumerate(argv):
        if a == "--input" and i + 1 < len(argv):
            path = argv[i + 1]
        elif a.startswith("--input="):
            path = a.split("=", 1)[1]
    raw = ""
    if path:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    else:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        obj = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        raise Failed(f"输入不是合法 JSON: {e}") from e
    if not isinstance(obj, dict):
        raise Failed("输入必须是 JSON 对象")
    return obj


def emit(obj: dict) -> None:
    """把结果写到 stdout：**唯一**允许写 stdout 的地方。"""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.write("\n")
    sys.stdout.flush()


def build_payload(inp: dict, action: str) -> dict:
    """输入 + 环境变量合并成 payload（输入优先，env 兜底）。"""
    p = {
        "action": action,
        "harness_id": env("AC_HARNESS_ID"),
        "workshop_id": env("AC_WORKSHOP_ID"),
        "member_id": env("AC_MEMBER_ID"),
        "role": env("AC_ROLE"),
        "project": env("AC_PROJECT"),
        "workspace_dir": env("AC_WORKSPACE_DIR"),
        "platform_url": env("AC_PLATFORM_URL"),
        "data_dir": env("AC_DATA_DIR"),
    }
    p.update({k: v for k, v in (inp or {}).items() if v not in (None, "")})
    return p


class Action:
    """动作基类：子类只实现 `do(payload) -> evidence dict`。

    返回的 evidence 就是"凭什么说成功"（平台会独立复核）：
      · new_session    -> {"session": {"id","name","window":{...}}}  至少给一个定位信息
      · rename_session -> {"applied_name": "...", "readback": "..."}
      · deliver        -> {"method": "...", "readback": "..."}
    """

    action = ""

    def do(self, payload: dict) -> dict:  # pragma: no cover - 子类实现
        raise NotImplementedError

    def run(self) -> int:
        t0 = time.time()
        try:
            inp = read_input()
        except Failed as e:
            emit({"ok": False, "action": self.action, "evidence": {}, "error": str(e)})
            return EXIT_BAD_INPUT
        payload = build_payload(inp, self.action)
        log(f"[{self.action}] harness={payload.get('harness_id')} member={payload.get('member_id')}")
        try:
            ev = self.do(payload) or {}
            if not isinstance(ev, dict):
                raise Failed("do() 必须返回 evidence 字典")
            emit({"ok": True, "action": self.action, "evidence": ev, "error": ""})
            log(f"[{self.action}] OK 用时 {time.time() - t0:.2f}s")
            return EXIT_OK
        except Unverified as e:
            emit({"ok": True, "action": self.action, "evidence": {}, "error": str(e)})
            log(f"[{self.action}] 已执行但**无法自证**：{e}")
            return EXIT_UNVERIFIED
        except Failed as e:
            emit({"ok": False, "action": self.action, "evidence": {}, "error": str(e)})
            log(f"[{self.action}] 失败：{e}")
            return EXIT_FAILED
        except Exception as e:  # noqa: BLE001 —— 未预期异常也算失败，不许吞
            emit({"ok": False, "action": self.action, "evidence": {},
                  "error": f"{type(e).__name__}: {e}"})
            log(f"[{self.action}] 异常：{type(e).__name__}: {e}")
            return EXIT_FAILED


def run_action(cls_or_obj) -> int:
    """入口：`sys.exit(run_action(MyAction()))`。

    统一在这里处理 `--self-test` —— **一处实现，三个动作模板全部生效**
    （本来每个模板各写一遍会漏，实测就被一致性自检抓到了）。
    """
    obj = cls_or_obj() if isinstance(cls_or_obj, type) else cls_or_obj
    if "--self-test" in sys.argv[1:]:
        return self_test(obj.action)
    return obj.run()


def run_command(cmd: str, timeout: float = 25.0) -> tuple[bool, str]:
    """跑一条外部命令（shell）；返回 (是否成功, stdout 或错误文本)。

    **失败不抛**：由调用方按自己的语义决定是 Failed 还是 Unverified
    （例如"命令跑起来了但读不回结果"应当算 Unverified，而不是成功）。
    """
    import subprocess
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"命令超时（{timeout}s）"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    if p.returncode != 0:
        return False, (p.stderr or p.stdout or f"退出码 {p.returncode}").strip()[:400]
    return True, (p.stdout or "").strip()


def parse_json_blob(text: str) -> dict:
    """从命令输出里取 JSON：整体是 JSON 就整体用；否则取最后一个 {} 块。失败抛 Failed。"""
    t = (text or "").strip()
    if not t:
        raise Failed("命令没有输出")
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
    except Exception:  # noqa: BLE001
        pass
    start, end = t.find("{"), t.rfind("}")
    if 0 <= start < end:
        try:
            obj = json.loads(t[start:end + 1])
            if isinstance(obj, dict):
                return obj
        except Exception as e:  # noqa: BLE001
            raise Failed(f"输出含 JSON 但解析失败: {e}") from e
    raise Failed("命令输出里没有可用的 JSON")


def self_test(action: str) -> int:
    """`--self-test`：不发任何真实操作，只验证契约本身（一致性自检用）。"""
    emit({"ok": True, "action": action, "evidence": {"self_test": True},
          "error": "", "contract": "v26"})
    log(f"[{action}] self-test OK")
    return EXIT_OK
