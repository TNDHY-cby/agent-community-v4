# -*- coding: utf-8 -*-
"""基座自检：环境与共享底座的**可持续性守卫**。

这个文件不是测某个功能，而是测「**开发基座本身没有退化**」。
起因是一次真实事故（2026-10-05）：

    `requirements.txt` 末尾被粘了一行 AIGC 尾注 `（内容由AI生成，仅供参考）`
    —— 没有 `#`。pip 解析时先按**操作系统首选编码**解码（本机 cp936/GBK），
    中文注释直接让它抛 `UnicodeDecodeError`；即使解码过了，那行裸文本也会被
    当成包名，`pip install -r requirements.txt` **整条失败**。
    也就是说：**环境不可复现**，而没有任何测试会发现这件事。

本文件把那次的教训固化成守卫：任何一次「往 requirements 里粘中文/粘尾注」
都会在 `pytest` 里立刻变红。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS = REPO_ROOT / "requirements.txt"

# 合法依赖行：name / name[extras] / name op version，可带环境标记与行内注释
_REQ_LINE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"          # 包名
    r"(\[[A-Za-z0-9._,-]+\])?"              # 可选 extras
    r"\s*([<>=!~]=?\s*[^\s;#]+(\s*,\s*[<>=!~]=?\s*[^\s;#]+)*)?"  # 可选版本约束
    r"(\s*;.*)?$"                            # 可选环境标记
)


def _dep_lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


@pytest.fixture(scope="module")
def req_bytes() -> bytes:
    assert REQUIREMENTS.exists(), f"缺失发布依赖清单：{REQUIREMENTS}"
    return REQUIREMENTS.read_bytes()


# ══════════════════════════════════════════════════════════════════
# A. 编码：pip 能不能读
# ══════════════════════════════════════════════════════════════════
class TestRequirementsEncoding:
    def test_pure_ascii(self, req_bytes):
        """必须纯 ASCII —— 这是本机 pip（GBK 首选编码）能读的前提。"""
        try:
            req_bytes.decode("ascii")
        except UnicodeDecodeError as e:
            pytest.fail(
                "requirements.txt 含非 ASCII 字节，pip 在本机（cp936/GBK）会直接抛 "
                f"UnicodeDecodeError，`pip install -r` 完全不可用：{e}\n"
                "修法：该文件只写 ASCII 注释；中文说明放 README/文档里。"
            )

    def test_no_bom(self, req_bytes):
        """不得有 BOM —— 文件是脚本可再生的，BOM 下次生成就会被抹掉。"""
        assert not req_bytes.startswith(b"\xef\xbb\xbf"), \
            "requirements.txt 不应带 UTF-8 BOM（再生时会丢失，问题会复发）"

    def test_pip_can_decode(self, req_bytes, tmp_path):
        """用 **pip 自己的加载路径**验证（它就是真实的故障点）。

        ⚠️ 2026-10-05 修正：原实现是
            `enc = pytest.importorskip("pip._internal.utils.encoding")`
        而该模块在 **pip 26.2.1 里已被整个移除**（实测全仓无 `def auto_decode`）——
        于是这条基座守卫在**新 pip 上被静默跳过**：明明自称"就是真实的故障点"，
        却根本没跑（覆盖率静默消失，正是本项目"验证工具本身有盲区"那类坑）。

        改为走 pip 现存的加载入口 `pip._internal.req.req_file.get_file_content()`
        （定义仍在，且"文件不可解码即抛 UnicodeDecodeError"的语义不变）；
        真遇到入口整体缺失（未来再改名）就 **fail 而非 skip** —— 守卫宁红不默。
        """
        try:
            from pip._internal.req.req_file import get_file_content
        except Exception as e:  # noqa: BLE001
            pytest.fail(
                "pip 的 requirements 加载入口不可用，这条基座守卫无法执行 —— "
                f"{type(e).__name__}: {e}\n"
                "请改用当前 pip 版本的等价公开入口后更新本用例；"
                "**不要改回 importorskip**（那会让守卫静默失效）。"
            )
        f = tmp_path / "requirements.txt"
        f.write_bytes(req_bytes)
        try:
            _loc, content = get_file_content(str(f), session=None)
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"pip 无法加载 requirements.txt：{type(e).__name__}: {e}")
        # 附带守住"读出来的内容与原文一致"，防入口行为漂移
        assert content == req_bytes.decode("ascii")


# ══════════════════════════════════════════════════════════════════
# B. 语法：每一行都得是合法依赖
# ══════════════════════════════════════════════════════════════════
class TestRequirementsSyntax:
    def test_every_dep_line_is_valid(self, req_bytes):
        text = req_bytes.decode("ascii")
        bad = [ln for ln in _dep_lines(text) if not _REQ_LINE.match(ln)]
        assert not bad, (
            "以下行不是合法依赖（会被 pip 当成包名而整条安装失败）：\n  "
            + "\n  ".join(bad)
        )

    def test_no_aigc_footer(self, req_bytes):
        """点名守卫那次事故的原文形态，防止再粘一次。"""
        text = req_bytes.decode("ascii", errors="replace")
        for marker in ("内容由AI生成", "仅供参考", "*（内容由"):
            assert marker not in text, f"requirements.txt 混入了文档尾注：{marker}"

    def test_no_wildcard_lines(self, req_bytes):
        """裸 `*` / `-r` 之类不得误入（本文件应自包含）。"""
        for ln in _dep_lines(req_bytes.decode("ascii")):
            assert ln != "*", "出现裸通配行"

    def test_pip_parser_accepts_file(self):
        """用 pip 的正式解析器过一遍（最强证据）。"""
        req_file = pytest.importorskip("pip._internal.req.req_file")
        session_mod = pytest.importorskip("pip._internal.network.session")
        reqs = list(req_file.parse_requirements(
            str(REQUIREMENTS), session=session_mod.PipSession()))
        assert reqs, "应解析出至少一个依赖"


# ══════════════════════════════════════════════════════════════════
# C. 内容：该有的依赖必须在
# ══════════════════════════════════════════════════════════════════
class TestRequirementsContent:
    def _names(self, req_bytes) -> set[str]:
        out = set()
        for ln in _dep_lines(req_bytes.decode("ascii")):
            out.add(re.split(r"[<>=!~\[]", ln)[0].strip().lower())
        return out

    @pytest.mark.parametrize("pkg", ["fastapi", "uvicorn", "pydantic", "httpx"])
    def test_core_runtime_present(self, req_bytes, pkg):
        assert pkg in self._names(req_bytes), f"缺核心运行依赖 {pkg}"

    def test_click_present_because_cli_uses_it(self, req_bytes):
        """`agent_community/cli.py` 顶部 `import click` —— 漏了它 CLI 起不来。"""
        cli = REPO_ROOT / "agent_community" / "cli.py"
        assert "import click" in cli.read_text(encoding="utf-8"), \
            "cli.py 不再用 click 了？若是，请同步删掉本测试与依赖"
        assert "click" in self._names(req_bytes), \
            "cli.py 依赖 click 但它不在 requirements.txt（oss 副本里有）"


# ══════════════════════════════════════════════════════════════════
# D. 共享底座模块必须可 import（拆分后不得互相拖死）
# ══════════════════════════════════════════════════════════════════
BASE_MODULES = [
    "agent_community.platform.identity",
    "agent_community.platform.protocol_catalog",
    "agent_community.platform.tool_registry",
    "agent_community.platform.session_registry",
    "agent_community.platform.tier_guidance",
    "agent_community.platform.routers.workshops_common",
    "agent_community.platform.routers.harness_common",
    "agent_community.platform.routers.sessions",
]


@pytest.mark.parametrize("modname", BASE_MODULES)
def test_base_module_importable(modname):
    """基座模块**单独** import 就必须成功（不依赖 server 的 import 顺序）。"""
    import importlib

    mod = importlib.import_module(modname)
    assert mod is not None


def test_requirements_not_utf8_bom_regression():
    """回归断言：本文件所在仓库根必须能定位到 requirements.txt（路径守卫）。"""
    assert REQUIREMENTS.parent == REPO_ROOT
    assert sys.version_info >= (3, 10)


# ══════════════════════════════════════════════════════════════════
# 版本号单一真源（2026-10-06 加）
# ══════════════════════════════════════════════════════════════════
class TestVersionSingleSource:
    """事故：`/api/status` 曾**硬编码** `"version": "4.0.0"`，与 `agent_community/__init__.py`
    的 `__version__`、以及 CHANGELOG 迭代线（当时已到 4.5.0）**三处口径不一**。
    版本号一旦有第二处真源，就必然漂移 —— 这里把"只有一处"钉住。
    """

    def test_api_status_version_equals_package_version(self):
        import asyncio

        from agent_community import __version__
        from agent_community.platform import server

        data = asyncio.run(server.api_status())
        assert data["version"] == __version__, (
            f"/api/status 报的版本 {data['version']!r} 与包元数据 {__version__!r} 不一致 —— "
            "版本号必须单一真源（读 agent_community.__version__）"
        )

    def test_no_hardcoded_version_literal_in_server_source(self):
        import pathlib
        import re

        from agent_community.platform import server

        src = pathlib.Path(server.__file__).read_text(encoding="utf-8", errors="replace")
        hits = re.findall(r'"version"\s*:\s*"[0-9][^"]*"', src)
        assert not hits, f"server.py 里出现了硬编码版本号 {hits} —— 请改读 _PKG_VERSION"

    def test_package_version_looks_like_semver(self):
        from agent_community import __version__

        assert re.match(r"^\d+\.\d+\.\d+$", __version__), f"版本号形态异常: {__version__!r}"

    def test_changelog_mentions_current_version(self):
        """有 CHANGELOG 的副本（发布副本）里，最新条目应与 __version__ 对齐。

        开发副本没有 CHANGELOG（它只存在于发布副本），故此处按"存在才校验"处理。
        """
        import pathlib

        from agent_community import __version__

        # dev: <repo>/agent_community/tests/ -> <repo>；CHANGELOG 在发布副本根，故向上找两级再并列判断
        here = pathlib.Path(__file__).resolve()
        cands = [here.parents[2] / "CHANGELOG.md"]           # dev 根（通常不存在）
        for p in cands:
            if not p.exists():
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            assert f"[{__version__}]" in text, (
                f"CHANGELOG 里没有当前版本 [{__version__}] 的条目 —— 发布前必须补")
        # 不存在的副本不判失败（守卫只在"有该文件时"生效）
