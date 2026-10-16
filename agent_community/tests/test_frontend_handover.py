# -*- coding: utf-8 -*-
"""前端外包**交接区**守卫（`外包交接_前端界面/`，2026-10-16 老板要求建）。

## 为什么要有这个守卫

老板 2026-10-16：「前端外包，**外包者是看不到本地文件的**，得上传到仓库的库，**单独划分一个区域**出来，
内容要有**清晰指向**，**不要遗漏**」。

⇒ 那个目录是外包方**唯一能看到的东西**。它有三种最容易坏的方式，本守卫各盯一条：
  ① **指向漂移**：README 里写的路径在仓库里不存在（或改了名没跟着改）⇒ 外包方读到一个死链接；
  ② **仪器坏了**：`check_frontend_spec.py` 的判据本身失效（放过违规页面 / 冤枉合格页面）⇒ 拿它验收等于没验；
  ③ **同步漏了**：目录建得再好，**发布脚本没同步它** ⇒ 外包方在仓库里根本看不到
     （同 `requirements.txt` / `start.py` 那两次"开发机能跑、别人拿不到"的教训）。
     第 ③ 条只能查发布脚本本身：**脚本就是要被验证的那个物件**，所以这里对它的文本检查是"按真对象判"。

判据都是真结果：路径用 `Path.exists()` 判；仪器直接**真跑**它的 `--selftest`；报告结构按真输出解析。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
AREA = _ROOT / "外包交接_前端界面"
CHECKER = AREA / "check_frontend_spec.py"
PUBLISH = _ROOT / "publish_acv4.ps1"

EXPECTED_FILES = ("README.md", "硬约束.md", "验收清单.md", "交付说明模板.md", "check_frontend_spec.py")

#: 文档里以 `反引号` 写出来的仓库内路径（用来判"指向有没有漂移"）
_PATH_RE = re.compile(r"`([^`\n]+)`")
#: 明显不是"具体文件路径"的写法：通配、占位、省略号、目录、URL、命令
_NOT_A_PATH = re.compile(r"[*<>{}…\s]|^https?://|^\.\.\.|/$|^python |^git |^\$|^POST|^GET")


def _md_files() -> list:
    return sorted(p for p in AREA.glob("*.md"))


def test_area_exists_with_every_piece():
    assert AREA.is_dir(), "交接区不见了：%s" % AREA
    missing = [f for f in EXPECTED_FILES if not (AREA / f).exists()]
    assert not missing, "交接区缺件（外包方就看不到）：%s" % missing
    assert _md_files(), "交接区一篇文档都没有"


def test_every_pointer_resolves():
    """★文档里写出来的仓库内路径**必须真的存在** —— 否则外包方读到的是死链接。"""
    dead = []
    checked = 0
    for md in _md_files():
        for raw in _PATH_RE.findall(md.read_text(encoding="utf-8")):
            p = raw.strip()
            if not p or _NOT_A_PATH.search(p):
                continue
            if "/" not in p and not p.endswith(".md"):
                continue                      # 纯文件名（如 `dockPost`）不是路径
            if not p.startswith(("agent_community/", "design-docs/", "外包交接_前端界面/",
                                 "tools/", "tests/")):
                continue                      # 只判"仓库内路径"，跳过命令片段/端点
            checked += 1
            if not (_ROOT / p).exists():
                dead.append("%s → %s" % (md.name, p))
    assert checked >= 8, "只认出 %d 条路径引用 —— 解析坏了还是文档没写清？" % checked
    assert not dead, "这些指向的文件不存在（外包方点不开）：\n  " + "\n  ".join(dead)


def test_checker_selftest_passes():
    """★仪器自检：**真跑**它 —— 合格夹具必须全过、违规夹具必须被抓。"""
    r = subprocess.run([sys.executable, str(CHECKER), "--selftest"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180, cwd=str(_ROOT))
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, "check_frontend_spec.py --selftest 没通过（判据坏了）：\n%s" % out[-1200:]
    tail = out.split("违规夹具 → 被抓条目：")[-1][:24]
    assert "违规夹具 → 被抓条目" in out and "（无）" not in tail, \
        "自检说「违规夹具一条都没抓到」—— 那套判据是摆设：\n%s" % out[-600:]


def test_checker_reports_every_item_with_a_reason():
    """真跑一次对**当前页面**的检查：每个条目都要有结论 + 理由（不许静默跳过）。"""
    r = subprocess.run([sys.executable, str(CHECKER)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180, cwd=str(_ROOT))
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode in (0, 1), "预检脚本异常退出（%s）：\n%s" % (r.returncode, out[-800:])
    ids = re.findall(r"^(X\d+|W\d+|D\d+|S\d+|A\d+|E\d+|G\d+|P\d+|M\d+)\s+(PASS|FAIL|待人工)", out, re.M)
    got = {i for i, _v in ids}
    need = {"W1", "W2", "W3", "W4", "D1", "D2", "D3", "S1", "S2", "S3", "S4", "S5",
            "A1", "E1", "E2", "G1", "G2", "P1", "M1", "M2"}
    assert need.issubset(got), "预检报告缺条目：%s" % sorted(need - got)
    assert "PASS" in out and ("FAIL" in out or "待人工" in out), "报告没有分档结论"


def test_area_is_actually_published():
    """③ 同步漏检：这个目录**必须真的进得了公开仓库**，否则外包方看不到它。

    ⚠️ 这里查的是**发布脚本自己**（它就是要被验证的物件）—— 本项目已有两次同类事故：
    `requirements.txt` 与 `start.py` 都是"dev 有、公开仓库没有"，而脚本照旧打印"publish done"。

    ★**两个面都要能判**（本守卫第一版踩了这个坑，被 CI run#45 当场抓红）：
      · **开发副本**：`publish_acv4.ps1` 在（它是 dev-only 工具）⇒ 查它的同步段里有没有这个目录；
      · **发布副本 / CI**：那个脚本**本来就不存在**（它从不随仓库发布）⇒ 改判**真结果**：
        `git ls-files` 能列出交接区的文件 = 它确实进了这个仓库。
      （第一版无条件要求脚本存在 ⇒ 在 CI 上"AssertionError: 发布脚本不见了" ——
      守卫自己依赖了 dev-only 的东西，属于"只在我这台机器绿"。）
    """
    if PUBLISH.exists():
        src = PUBLISH.read_text(encoding="utf-8", errors="replace")
        assert "外包交接_前端界面" in src, \
            "发布脚本没有同步「外包交接_前端界面/」—— 外包方在仓库里看不到交接包"
        assert "robocopy" in src and "ossOffload" in src, \
            "交接区的同步段看起来没写全（robocopy 目标变量不在）"
        return

    tracked = _git_tracked()
    if tracked is None:
        import pytest
        pytest.skip("既没有发布脚本（发布副本），也拿不到 git —— 这一面无法判定同步")
    missing = [f for f in EXPECTED_FILES if ("外包交接_前端界面/" + f) not in tracked]
    assert not missing, "交接区在公开仓库里**没有被跟踪**（＝没发布出去）：%s" % missing


def _git_tracked():
    """本仓库 `git ls-files` 的结果集合；拿不到 git / 不是仓库时返回 None。

    ⚠️ **必须 `-c core.quotepath=false`**：git 默认把非 ASCII 路径按八进制转义输出
    （`"\\345\\244\\226..."`）⇒ 拿它跟中文目录名比会**假红**（实测踩到，见下）。
    """
    try:
        r = subprocess.run(["git", "-c", "core.quotepath=false", "ls-files"],
                           cwd=str(_ROOT), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120)
    except Exception:  # noqa: BLE001
        return None
    if r.returncode != 0:
        return None
    return {ln.strip().replace("\\", "/") for ln in (r.stdout or "").splitlines() if ln.strip()}


def test_handover_area_avoids_local_and_real_names():
    """对外件纪律：交接区里的**代码文件**不许出现本机坐标与真实 harness 名。

    ⚠️ 只判 `.py/.js/.html`：`.md` 里出现 `127.0.0.1:<示例端口>` 是**教人怎么本地跑**，
    属于有意为之（真正的"写死本机坐标"判据在 `check_frontend_spec.py` 的 P1 上，判的是页面文件）。
    """
    bad = []
    for f in sorted(AREA.rglob("*")):
        if not f.is_file() or "__pycache__" in f.parts or f.suffix not in (".py", ".js", ".html"):
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for pat, why in ((r"127\.0\.0\.1:\d+", "写死的本机端口"),
                         (r"localhost:\d+", "写死的本机端口"),
                         (r"[A-Za-z]:\\\\", "绝对路径")):
            if re.search(pat, text):
                bad.append("%s：%s" % (f.name, why))
    assert not bad, "交接区的代码文件里出现了本机私有坐标：%s" % bad
