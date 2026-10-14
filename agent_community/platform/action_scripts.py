"""platform/action_scripts：通用脚本框架的**平台侧**（V26）。

做什么：
  · 步骤 7 的**命名生成**：`session_name_for()` → `<工作间id>-<项目>-<职位>-<成员id>`；
  · 把 L1 骨架渲染成**自包含**的动作脚本（注入契约运行时 `_common.py`）；
  · 调用动作脚本并把退出码映射成平台语义（**0=成功且可自证 / 10=成功但未核实 / 其它=失败**）。

为什么强调"未核实"（准则⑤）：脚本"做了"不等于"做成了"。平台**不采信声明**——
`verified=False` 的调用**不得**写进"已完成"，界面要如实显示。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

ACTIONS = ("new_session", "rename_session", "deliver")
EXIT_OK = 0
EXIT_UNVERIFIED = 10

_FRAMEWORK_DIRNAME = "script_framework"
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
# 会话名的总长上限：留足空间给"职位/成员id"，超长时**先截项目名**（它是最可读、最不关键身份的段）
NAME_MAX = 64
_PROJECT_MAX = 24


def framework_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "bridge_templates" / _FRAMEWORK_DIRNAME


def safe_segment(raw: Any, fallback: str = "x", limit: int = 32) -> str:
    s = _ILLEGAL.sub("-", str(raw or "")).strip().strip(".")
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    return (s[:limit] or fallback)


def session_name_for(workshop: Any, member: Any) -> str:
    """步骤 7：平台给出新命名 `<工作间id>-<项目>-<职位>-<成员id>`。

    · **必须含"项目"**（可读、给用户看，老板原话"最好要有项目名给用户看"）；
    · 段内非法字符替换为 `-`；总长超限时**先截项目名**（身份段优先保留）；
    · 纯函数，不碰磁盘 —— 便于测试与"同一成员幂等"。
    """
    ws_id = safe_segment(getattr(workshop, "workshop_id", ""), "ws", 16)
    role = safe_segment(getattr(member, "role", ""), "成员", 12)
    mid = safe_segment(getattr(member, "member_id", ""), "m", 16)
    project = safe_segment(getattr(workshop, "name", ""), "未命名任务", _PROJECT_MAX)

    def compose(p: str) -> str:
        return f"{ws_id}-{p}-{role}-{mid}"

    name = compose(project)
    while len(name) > NAME_MAX and len(project) > 4:
        project = project[: len(project) - 2]
        name = compose(project)
    return name[:NAME_MAX]


# ── 渲染 ────────────────────────────────────────────────────────
_LIT_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)_LIT\}\}")
_PH_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")


def render(action: str, params: dict, framework: Optional[Path] = None) -> str:
    """把 L1 骨架渲染成**自包含**脚本：注入契约运行时源码，替换占位符。

    占位符约定**沿用既有 `bridge_factory` 的那一套**（一致性 = 可复制沿用）：
      · `{{KEY_LIT}}` —— 值经 `repr()` 转义后**连引号一起**注入（模板里**不要**自己写引号）；
        这是安全注入"含引号/反斜杠"的值（如带空格路径的命令）的**正确做法**；
      · `{{KEY}}`     —— 原样注入（仅用于非字面量位置）。
    与桥模板的一处**有意差异**：桥模板要求所有参数必填，动作脚本的参数多为**可选**
    （没配就退化为人工模式），故未提供的 `_LIT` 注入空字符串，而不是报错。
    """
    if action not in ACTIONS:
        raise ValueError(f"未知动作: {action}（可选 {ACTIONS}）")
    d = Path(framework) if framework else framework_dir()
    tpl = (d / f"{action}.tmpl").read_text(encoding="utf-8")
    common = (d / "_common.py").read_text(encoding="utf-8")
    # ⚠️ 实测坑一：`_common.py` 顶部的 `from __future__ import annotations` 注入后落在模板 docstring
    # 之后，而它必须是"仅随模块 docstring 之后"的第一个语句 ⇒ SyntaxError。生成物跑在 3.11+，
    # 不需要该 future import，注入前去掉。
    common = common.replace("from __future__ import annotations\n", "", 1)
    text = tpl.replace("{{AC_COMMON}}", common)  # productization: allow(渲染器自己在替换占位符，不是发给用户的数据)
    p = params or {}
    text = _LIT_RE.sub(lambda m: repr(str(p.get(m.group(1), "") or "")), text)
    text = _PH_RE.sub(lambda m: str(p.get(m.group(1), "") or ""), text)
    leftover = sorted(set(_LIT_RE.findall(text)) | set(_PH_RE.findall(text)))
    if leftover:
        # ⚠️ 实测坑二：不许把未替换的 {{XXX}} 当字面量发出去（既有渲染器也是这么严的）
        raise ValueError(f"渲染后仍残留占位符: {leftover}（模板: {action}）")
    return text


def write_action_script(action: str, params: dict, target_path: Any) -> dict:
    """渲染并落盘到指定路径（通常落在该成员的隔间里）。"""
    text = render(action, params)
    p = Path(str(target_path))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return {"ok": True, "action": action, "path": str(p), "bytes": len(text.encode("utf-8"))}


# ── 配置读写（挂在 harness 上，最小侵入：不改 HarnessInfo 的字段）──
def _meta(sess: Any) -> dict:
    md = getattr(sess, "metadata", None)
    return md if isinstance(md, dict) else {}


def get_config(sess: Any) -> dict:
    cfg = _meta(sess).get("action_scripts")
    return cfg if isinstance(cfg, dict) else {}


def set_config(sess: Any, action: str, params: dict, foreground: str = "") -> dict:
    """登记某 harness 的某动作配置（幂等覆盖）。"""
    if action not in ACTIONS:
        raise ValueError(f"未知动作: {action}")
    md = getattr(sess, "metadata", None)
    if not isinstance(md, dict):
        raise TypeError("该 harness 的 metadata 不可写")
    cfg = get_config(sess)
    entry = dict(cfg.get(action) or {})
    entry["params"] = {k: v for k, v in (params or {}).items()}
    if foreground:
        entry["foreground"] = foreground
    cfg[action] = entry
    md["action_scripts"] = cfg
    return cfg


def available_actions(sess: Any) -> dict:
    """哪些动作配了命令（`configured`）、哪些只能人工（`manual`）。

    供界面**如实说明**"该 harness 不支持第 X 步"（V26 §5 第 3 条），
    而不是默默失败。
    """
    out = {}
    cfg = get_config(sess)
    for a in ACTIONS:
        e = cfg.get(a) or {}
        prm = e.get("params") or {}
        cmd_key = {"new_session": "NEW_SESSION_CMD",
                   "rename_session": "RENAME_CMD",
                   "deliver": "DELIVER_CMD"}[a]
        has_cmd = bool(str(prm.get(cmd_key) or "").strip())
        out[a] = {"configured": has_cmd,
                  "mode": "cmd" if has_cmd else "manual",
                  "foreground": e.get("foreground") or ("optional" if has_cmd else "required")}
    return out


# ── 调用 ────────────────────────────────────────────────────────
def run_action(script_path: Any, action: str, payload: dict,
               env_extra: Optional[dict] = None, timeout: float = 45.0,
               python_exe: str = "") -> dict:
    """跑一个动作脚本，返回平台语义的结果。

    返回：`{ok, verified, action, evidence, error, exit_code, mode}`
      · `ok=True, verified=True`  → 成功且脚本给了可复核的 evidence；
      · `ok=True, verified=False` → 执行了但**无法自证**（退出码 10）⇒ 界面必须标「未核实」；
      · `ok=False`                → 失败（非零退出 / 超时 / JSON 不合法）⇒ 走降级链。
    """
    p = Path(str(script_path))
    res = {"ok": False, "verified": False, "action": action, "evidence": {},
           "error": "", "exit_code": None, "mode": ""}
    if not p.is_file():
        res["error"] = f"动作脚本不存在: {p}"
        return res
    exe = python_exe or os.environ.get("AC_PYTHON") or "python"
    fd, tmp = tempfile.mkstemp(prefix="ac_action_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload or {}, f, ensure_ascii=False)
    env = dict(os.environ)
    env.update({k: str(v) for k, v in (env_extra or {}).items() if v is not None})
    env["AC_ACTION"] = action
    # ★★2026-10-12 **实测 bug 修复**：下面 `subprocess.run(..., encoding="utf-8")` 是按 UTF-8
    #   解码子进程输出的，但**子进程自己用什么编码打印，取决于它的区域设置** ——
    #   中文 Windows 上动作脚本默认用 **GBK** 输出 ⇒ 平台按 UTF-8 解 ⇒ **中文全变成
    #   替换字符（\ufffd）**，于是"未核实/不一致"这类**判据文本被毁**，测试与真实功能一起错
    #   （实测：test_action_scripts / test_workshops_session 共 2 条因此失败）。
    #   平台自己的文档与模板里早就写着「`PYTHONIOENCODING=utf-8` 必加：不加会报
    #   `'gbk' codec can't encode`（中文 Windows 实测会弄死桥）」——
    #   那就在这里**由平台强制给出**，不要再指望调用者记得设。
    #   （不覆盖调用方显式给的值：env_extra 可覆盖。）
    env.setdefault("PYTHONIOENCODING", "utf-8")
    cmd = [exe, str(p), "--input", tmp]
    try:
        # ★2026-10-11 实测缺陷修复：**必须显式给编码**。
        #   `text=True` 不给 encoding 时，Python 用**区域默认**（中文 Windows = GBK）解码子进程输出；
        #   而动作脚本（L1 骨架）本身会打印中文 ⇒ 解码在读取线程里抛
        #   `UnicodeDecodeError: 'gbk' codec can't decode byte …` ⇒ **stdout 变空** ⇒
        #   上层误报"不是合法 JSON" ⇒ **人工模式（退出码 10）在中文机器上整体被判失败**。
        #   （实测复现：`write_action_script('new_session', ...)` 跑出来 exit_code=10、stdout='' ）
        pr = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env,
                            encoding="utf-8", errors="replace")
        res["exit_code"] = pr.returncode
        out = (pr.stdout or "").strip()
        err = (pr.stderr or "").strip()
        blob = {}
        if out:
            try:
                blob = json.loads(out[out.find("{"): out.rfind("}") + 1]) if "{" in out else {}
            except Exception:  # noqa: BLE001
                blob = {}
        if not isinstance(blob, dict):
            blob = {}
        # ★2026-10-11 顺序修正：**退出码 10 = 成功但无法自证**（人工模式的正常结果），
        #   它**本来就不需要 stdout 给 JSON** —— 骨架会打印给人看的操作指引。
        #   原实现先判"stdout 不是合法 JSON"并直接 return ⇒ **人工模式永远被判失败**
        #   （实测：exit_code=10、stdout 为空 ⇒ error="stdout 不是合法 JSON：(空)"，
        #    与 EXIT_UNVERIFIED 的语义正好相反）。⇒ 先认退出码，再要求 JSON。
        if pr.returncode == EXIT_UNVERIFIED:
            res["ok"] = True
            res["verified"] = False
            res["mode"] = str(blob.get("action") or "manual")
            res["evidence"] = blob.get("evidence") or {}
            res["error"] = str(blob.get("error") or err or "未核实：无法自证（人工模式）")
            return res
        if not blob:
            # 退出码 0 却没给可解析的 JSON ⇒ 无法确认它真的做了事，如实报失败（准则⑤）
            res["error"] = f"stdout 不是合法 JSON：{out[:200] or '(空)'}"
            return res
        res["mode"] = str(blob.get("action") or action)
        res["evidence"] = blob.get("evidence") or {}
        if pr.returncode == EXIT_OK:
            res["ok"] = True
            res["verified"] = bool(res["evidence"])
            if not res["verified"]:
                res["error"] = "退出码 0 但 evidence 为空 ⇒ 视为未核实"
            return res
        # 退出码 10 已在上面统一处理（不重复分支：留着会让两条路径语义漂移）
        res["error"] = str(blob.get("error") or err or f"退出码 {pr.returncode}")
        return res
    except subprocess.TimeoutExpired:
        res["error"] = f"动作脚本超时（{timeout}s）"
        return res
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {e}"
        return res
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
