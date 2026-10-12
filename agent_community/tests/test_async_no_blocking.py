# -*- coding: utf-8 -*-
r"""★守卫：**async 函数里不许有同步阻塞调用**（会等的子进程 / sleep / 删目录 …）。

**为什么单独立一条**（2026-10-12 实测）：这类缺陷的**外部症状完全不像它本身** ——
  平台表现为「**卡住**」：watchdog 报 `TimeoutError: timed out`、
  「**进程还在、却不再收连接**」（`TCP not listening`）、然后自动重启。
  我从外面查了一大圈才找到根因；**外端 agent 当天报的「连接被远程主机关闭」也是它**
  （`probe-register` 的进程/端口扫描在事件循环里同步跑）。

**已确认并修掉的五例**（同一族）：
  ① `channel_selfcheck` 用 `time.sleep` 轮询（我写的）；
  ② `bridge_instances` 在 async 端点里同步跑 PowerShell（我写的，端点 0.02s → 1.28s）；
  ③④ `ProbeHarnessTool.execute` 两处 `subprocess.check_output`（进程扫描 timeout=25、端口扫描 timeout=20）；
  ⑤ `verify_sandbox.teardown` 里同步 `shutil.rmtree`；
  ⑥ `routers/plugins.py` 的 `Popen(...) + proc.communicate(timeout=5)`（本守卫扫出来的）。

**判据（两道，都只报"会等"的调用）**：
  · **模块级 API**：`subprocess.run/check_output/check_call/call`、`os.system/popen`、
    `time.sleep`、`shutil.rmtree/copytree` —— **按导入别名解析**
    （`import subprocess as _sp` 也算，否则 alias 一写就漏 —— 我第一版就这么漏了）；
  · **方法名**：任何对象上的 `.communicate(...)`（无歧义；`wait` 故意不收，
    因为异步 API 也常叫 `wait`，收了会误报）。
  · `Popen` **不算** —— 它只起进程、不等；真正阻塞的是它之后的 `communicate()/wait()`。
  · 正确写法 `await asyncio.to_thread(<函数>, ...)` 里，函数是**属性引用不是调用** ⇒ 不报。
  · 确需同步的，在该行注明 `# async-blocking-ok: <原因>` 显式豁免。
"""
from __future__ import annotations

import ast
import os

BLOCKING = {
    ("subprocess", "run"), ("subprocess", "check_output"), ("subprocess", "check_call"),
    ("subprocess", "call"),
    ("os", "system"), ("os", "popen"),
    ("time", "sleep"),
    ("shutil", "rmtree"), ("shutil", "copytree"),
    # ★**同步网络 I/O**（2026-10-12 补的盲区）：我第一版只算"子进程/sleep/删目录"，
    #   于是漏掉了 `ProbeHarnessTool` 里逐个端口的 `urlopen(timeout=2)` ——
    #   实测那是**最长的一处阻塞**（修完子进程扫描后，平台仍被卡 8.9 秒）。
    ("urllib.request", "urlopen"), ("urllib.request", "urlretrieve"),
    ("urllib.request", "urlopen_full"),
}
BLOCKING_METHODS = {"communicate"}
ROOTS = (os.path.join("agent_community", "platform"),
         os.path.join("agent_community", "examples"))


def _aliases(tree: ast.AST) -> dict:
    """`{本地名: 真实限定名}`，如 `import subprocess as _sp` → `{'_sp': 'subprocess'}`；
    `from subprocess import run as _r` → `{'_r': 'subprocess.run'}`。"""
    m = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                m[a.asname or top] = a.name if a.asname else top
        elif isinstance(node, ast.ImportFrom):
            if not node.module:
                continue
            mod = node.module.split(".")[0]
            for a in node.names:
                m[a.asname or a.name] = "%s.%s" % (mod, a.name)
    return m


def _iter_body_nodes(fn: ast.AsyncFunctionDef):
    """遍历 async 函数体，但**不进入嵌套的同步函数**（`def`/`lambda`）。

    ★为什么必须停：正确修法就是把阻塞活打包成一个**嵌套同步函数**再用
    `await asyncio.to_thread(那个函数)` 调 —— 那种写法里的 `communicate()` 是**该有**的，
    判据钻进去就会误报（我第一版就这么误报了自己刚修好的 `plugins.py`）。
    （嵌套 `async def` **要**继续扫：它同样跑在事件循环上。）
    """
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.Lambda)):
            continue                      # 嵌套同步函数 / lambda：整体跳过
        yield n
        stack.extend(ast.iter_child_nodes(n))


def _scan_file(path: str) -> list:
    """扫单个文件：返回 async 函数里的**直接**同步阻塞调用描述列表。"""
    try:
        src = open(path, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
    except Exception:  # noqa: BLE001
        return []
    al = _aliases(tree)
    lines = src.split("\n")
    # ★在 **`await` 的子树内**的调用都算"异步语境"，不算同步阻塞 ——
    #   例：`await proc.communicate()`，以及
    #       `await asyncio.wait_for(proc.communicate(), timeout=30)`（`communicate` 是**参数**）。
    #   只看"直接被 await 的那一个调用"会漏掉后者（我第一版就这么误报了 `shell_tool.py`）。
    #   代价：`await f(同步阻塞())` 这种罕见写法会被放过 —— 可接受（作者已表达异步意图）。
    awaited_nodes = set()
    for a in ast.walk(tree):
        if isinstance(a, ast.Await):
            for sub in ast.walk(a.value):
                awaited_nodes.add(id(sub))
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for sub in _iter_body_nodes(node):
            if not isinstance(sub, ast.Call):
                continue
            if id(sub) in awaited_nodes:
                continue
            fn_node = sub.func
            tag = None
            if isinstance(fn_node, ast.Attribute) and isinstance(fn_node.value, ast.Name):
                holder = fn_node.value.id
                real = al.get(holder, holder)          # ★按别名还原
                if (real, fn_node.attr) in BLOCKING:
                    tag = "%s.%s" % (real, fn_node.attr)
                elif fn_node.attr in BLOCKING_METHODS and holder not in al:
                    tag = "%s.%s" % (holder, fn_node.attr)   # 变量上的 .communicate()
            elif isinstance(fn_node, ast.Name):
                real = al.get(fn_node.id)
                if real and tuple(real.split(".")) in BLOCKING:
                    tag = real                          # `from subprocess import run`
                elif fn_node.id == "sleep":
                    tag = "sleep"
            if not tag:
                continue
            line = lines[sub.lineno - 1] if sub.lineno - 1 < len(lines) else ""
            if "async-blocking-ok" in line:             # 显式豁免
                continue
            bad.append("%s:%d async %s() 里的 %s(...)"
                       % (path.replace(os.sep, "/"), sub.lineno, node.name, tag))
    return bad


def _scan_tree(root: str) -> list:
    bad = []
    for dp, dn, fn in os.walk(root):
        if "__pycache__" in dp:
            continue
        for f in sorted(fn):
            if f.endswith(".py") and ".bak" not in f:
                bad += _scan_file(os.path.join(dp, f))
    return sorted(set(bad))


def test_no_sync_blocking_inside_async_functions():
    """★`async def` 里不许**直接调用**会等的同步 API（见文件头判据）。"""
    bad = []
    for r in ROOTS:
        bad += _scan_tree(r)
    bad = sorted(set(bad))
    assert not bad, (
        "async 函数里有同步阻塞调用（会卡死事件循环 ⇒ 平台「假死」、watchdog 重启、"
        "外端 agent 报「连接被关闭」）：\n  " + "\n  ".join(bad)
        + "\n\n修法：`await asyncio.to_thread(<函数>, ...)`；"
          "确需同步的，在该行注明 `# async-blocking-ok: <原因>`。"
    )


def test_the_guard_itself_has_teeth(tmp_path):
    """★守卫自身要有牙：用**同一份判据**抓反例，且不误报正确写法。

    （本项目反复踩"判据空转通过"的坑 —— 例如 `cmd /c` 起进程失败、断言 `== 0` 照样通过。
      所以每条守卫都要证明它抓得住反例；而且要用**真判据**，不是复制一份逻辑。）
    """
    sample = (
        "import subprocess, time, asyncio, shutil, os\n"
        "import subprocess as _sp\n"
        "from subprocess import check_output as _co\n"
        "async def bad_alias():\n"
        "    _sp.check_output(['x'])\n"
        "async def bad_plain():\n"
        "    time.sleep(1)\n"
        "    subprocess.run(['x'])\n"
        "    shutil.rmtree('y')\n"
        "    os.system('z')\n"
        "    _co(['w'])\n"
        "async def bad_network():\n"
        "    import urllib.request as _ur\n"
        "    _ur.urlopen('http://127.0.0.1:1/')\n"
        "async def bad_communicate():\n"
        "    p = subprocess.Popen(['x'])\n"
        "    p.communicate(timeout=5)\n"
        "async def good():\n"
        "    await asyncio.to_thread(time.sleep, 1)\n"
        "    await asyncio.to_thread(subprocess.run, ['x'])\n"
        "    await asyncio.to_thread(shutil.rmtree, 'y')\n"
        "async def good_nested():\n"
        "    def _work():\n"
        "        p = subprocess.Popen(['x'])\n"
        "        p.communicate(timeout=5)\n"
        "    await asyncio.to_thread(_work)\n"
        "async def good_async_api():\n"
        "    p = await asyncio.create_subprocess_exec('x')\n"
        "    await p.communicate()\n"
        "async def good_network():\n"
        "    import urllib.request as _ur\n"
        "    await asyncio.to_thread(_ur.urlopen, 'http://127.0.0.1:1/')\n"
        "async def good_waitfor():\n"
        "    p = await asyncio.create_subprocess_exec('x')\n"
        "    await asyncio.wait_for(p.communicate(), timeout=30)\n"
        "async def exempted():\n"
        "    time.sleep(0)  # async-blocking-ok: 测试里说明用途\n"
    )
    p = tmp_path / "sample.py"
    p.write_text(sample, encoding="utf-8")
    found = "\n".join(_scan_file(str(p)))
    for need in ("async bad_alias()", "async bad_plain()", "async bad_communicate()",
                 "async bad_network()"):
        assert need in found, "★判据漏报：%s（实得：\n%s）" % (need, found)
    # ⚠️扫描器会把**别名归一化**（`_sp.check_output` → `subprocess.check_output`）——
    #   这是刻意行为（否则 alias 一换就漏），所以这里断言的是**归一化后的名字**。
    for tag in ("subprocess.check_output", "time.sleep", "subprocess.run",
                "shutil.rmtree", "os.system", "p.communicate",
                "urllib.request.urlopen"):
        assert tag in found, "★判据漏报 %s（实得：\n%s）" % (tag, found)
    assert "async good()" not in found, \
        "★把 `to_thread(...)` 的正确写法误报了（函数是属性引用，不该算调用）：\n%s" % found
    assert "async good_nested()" not in found, \
        ("★把「嵌套同步函数 + to_thread」的正确写法误报了 —— 判据不能钻进嵌套的同步 def：\n%s"
         % found)
    assert "async good_async_api()" not in found, \
        ("★把 `await proc.communicate()`（协程调用，正确）误报了 —— 被 await 的调用不算同步阻塞：\n%s"
         % found)
    assert "async good_waitfor()" not in found, \
        ("★把 `await asyncio.wait_for(proc.communicate(), ...)` 误报了 —— "
         "`await` 子树内的调用都算异步语境：\n%s" % found)
    assert "async good_network()" not in found, \
        ("★把 `await asyncio.to_thread(urlopen, ...)` 误报了：\n%s" % found)
    assert "async exempted()" not in found, "显式豁免（async-blocking-ok）没生效"
