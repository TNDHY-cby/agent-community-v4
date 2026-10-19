# -*- coding: utf-8 -*-
"""判据：**任务树 + 资源库端到端**（建树 → 挂资源 → 双向关联 → 删登记不删文件 → 留痕）。

```bash
python tools/tree_resources_check.py     # 约 8 秒
```

## 为什么补这一条

`task_tree` 与 `resources` 是"协作层的账本"：**谁在做哪块、产物挂在哪个节点**。
此前只有单元与接口级检查，**没有一条端到端判据**把"树形结构 / 双向关联 / 删登记不删文件 / 落盘"串起来。
它最容易假成功的形态：资源挂上了但**只写了一边**（节点有 rid、资源却没回填 node_id），
或者"移除资源"顺手把用户的文件删了。

## 十一格判据（真打 HTTP + 读真实落盘文件）

| 格 | 判据 |
|---|---|
| ⓪ | 隔离实例 + 建间 + 开工 |
| ① | 建根节点 + 子节点 ⇒ `GET /tree` 是**真嵌套**（子节点在父的 children 里） |
| ② | 空 `label` ⇒ 400；不存在的 `parent_id` ⇒ 400（都不静默成根节点） |
| ③ | 节点状态：`active→done` 生效；**非法状态 ⇒ 400 且状态不变** |
| ④ | 登记资源 ⇒ `GET /resources` 里有它；空 name / 空 path ⇒ 400 |
| ⑤ | **双向关联**：`attach` 后节点里有 `rid`、资源里回填 `task_node` |
| ⑥ | 另一条路径：登记时就带 `node_id` ⇒ 同样双向关联 |
| ⑦ | 挂**不存在**的资源 ⇒ 400（不假装挂上） |
| ⑧ | **删登记不删文件**：移除资源后资源库没了，但磁盘上的实体文件仍在 |
| ⑨ | `RESOURCES.md` 落盘：登记后有该资源；移除后不再列它 |
| ⑩ | 审计有 `tree.create` / `tree.status` / `resource.add` / `tree.attach` / `resource.remove` |
| ⑪ | 持久化：`workshops.json` 里 `task_tree` 真有节点（不是只在内存） |
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _clean_env(**over) -> dict:
    keep = ("PATH", "PYTHON", "SYSTEM", "WINDIR", "TEMP", "TMP", "HOME", "USER",
            "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT", "LANG", "LC_", "LD_LIBRARY")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AC_") and k.upper().startswith(keep)}
    env.update({k: str(v) for k, v in over.items()})
    return env


def req(base: str, path: str, body=None, method=None, timeout=30):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    m = method or ("POST" if body is not None else "GET")
    r = urllib.request.Request(base + path, data=data, method=m,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:                                    # noqa: BLE001
            return e.code, {"raw": raw[:300]}
    except Exception as e:                                   # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(e).__name__, str(e)[:120])}


def _find_node(tree: list, node_id: str):
    """在 `_tree_build` 的嵌套结构里找节点（递归）。"""
    for n in tree or []:
        if not isinstance(n, dict):
            continue
        if n.get("node_id") == node_id:
            return n
        hit = _find_node(n.get("children") or [], node_id)
        if hit is not None:
            return hit
    return None


def _rids_of(node: dict) -> set:
    """节点上挂的资源 rid 集合。

    ⚠️ `_tree_build()` 把 `resources` **展开成资源对象**（不是 rid 字符串列表）——
    第一版判据直接写 `rid in node["resources"]`，于是永远 False（假红）。
    """
    out = set()
    for r in (node or {}).get("resources") or []:
        if isinstance(r, dict):
            if r.get("rid"):
                out.add(str(r["rid"]))
        elif str(r or "").strip():
            out.add(str(r))
    return out


def main() -> int:
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    data_dir = Path(tempfile.mkdtemp(prefix="ac_tree_"))
    env = _clean_env(AC_PORT=port, AC_DATA_DIR=data_dir, PYTHONPATH=str(ROOT),
                     PYTHONIOENCODING="utf-8", AC_PYTHON=str(sys.executable))
    proc = subprocess.Popen([sys.executable, "-m", "agent_community.platform.server"],
                            cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fails = 0

    def cell(name: str, ok: bool, detail: str = "") -> None:
        nonlocal fails
        print("   %-50s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        fails += 0 if ok else 1

    print("=" * 92)
    print("任务树 + 资源库端到端判据（实例 %s，数据目录 %s）" % (base, data_dir))
    print("=" * 92)
    try:
        code = 0
        for _ in range(120):
            code, _b = req(base, "/api/status", timeout=3)
            if code == 200:
                break
            time.sleep(0.5)
        if code != 200:
            print("★实例没起来")
            return 2
        st, ws = req(base, "/api/workshop", {
            "name": "树与资源走查", "hall_content": "分三块做，产物挂到节点上",
            "members": [{"role": "码农", "harness_ids": []}],
        })
        ws_id = str((ws or {}).get("workshop_id") or "")
        if st != 200 or not ws_id:
            print("★建间失败：%s" % str(ws)[:300])
            return 1
        st_ws, wbody = req(base, "/api/workshop/%s" % ws_id)
        ws_dir = Path(str((wbody or {}).get("workspace_dir") or ""))
        cell("⓪ 隔离实例 + 建间", True, "ws=%s" % ws_id)

        # ① 建树（根 + 子）
        st_r, root = req(base, "/api/workshop/%s/tree/node" % ws_id,
                         {"label": "第一阶段", "kind": "phase"})
        root_id = str(((root or {}).get("node") or {}).get("node_id") or "")
        st_c, child = req(base, "/api/workshop/%s/tree/node" % ws_id,
                          {"label": "写排序模块", "parent_id": root_id, "kind": "task"})
        child_id = str(((child or {}).get("node") or {}).get("node_id") or "")
        st_t, tree = req(base, "/api/workshop/%s/tree" % ws_id)
        t = (tree or {}).get("tree") or []
        nested = _find_node(t, root_id)
        has_child = bool(nested) and any((c or {}).get("node_id") == child_id
                                         for c in (nested.get("children") or []))
        cell("① 建根+子，GET /tree 是真嵌套",
             st_r == 200 and st_c == 200 and st_t == 200 and has_child,
             "root=%s child=%s 嵌套=%s" % (root_id or "?", child_id or "?", has_child))

        # ② 校验
        st_a, ba = req(base, "/api/workshop/%s/tree/node" % ws_id, {"label": "  "})
        st_b, bb = req(base, "/api/workshop/%s/tree/node" % ws_id,
                       {"label": "挂到不存在的父", "parent_id": "t_nope"})
        cell("② 空 label 400 / 父不存在 400",
             st_a == 400 and st_b == 400,
             "空label=%s ｜ 坏父=%s" % (st_a, st_b))

        # ③ 状态流转 + 非法状态不改
        st_s, sb = req(base, "/api/workshop/%s/tree/node/%s/status" % (ws_id, child_id),
                       {"status": "done"})
        st_bad, bbad = req(base, "/api/workshop/%s/tree/node/%s/status" % (ws_id, child_id),
                           {"status": "whatever"})
        st_t2, tree2 = req(base, "/api/workshop/%s/tree" % ws_id)
        node_now = _find_node((tree2 or {}).get("tree") or [], child_id) or {}
        cell("③ done 生效；非法状态 400 且状态不变",
             st_s == 200 and st_bad == 400 and node_now.get("status") == "done",
             "done=%s ｜ 非法=%s ｜ 现值=%s" % (st_s, st_bad, node_now.get("status")))

        # ④ 登记资源（造一个真文件）
        product = ws_dir / "library" / "成果-v1.md"
        product.parent.mkdir(parents=True, exist_ok=True)
        product.write_text("# 成果 v1\n排序模块已自测\n", encoding="utf-8")
        st_p, pb = req(base, "/api/workshop/%s/resources" % ws_id, {
            "name": "成果 v1", "path": str(product), "kind": "file",
            "note": "第一版", "uploader": "组长"})
        rid = str(((pb or {}).get("resource") or {}).get("rid") or "")
        st_l, lb = req(base, "/api/workshop/%s/resources" % ws_id)
        names = [r.get("name") for r in ((lb or {}).get("resources") or [])]
        st_n1, _ = req(base, "/api/workshop/%s/resources" % ws_id, {"name": "", "path": "x"})
        st_n2, _ = req(base, "/api/workshop/%s/resources" % ws_id, {"name": "x", "path": ""})
        cell("④ 登记资源可查；空 name/path 各 400",
             st_p == 200 and rid and "成果 v1" in names and st_n1 == 400 and st_n2 == 400,
             "rid=%s ｜ 列表=%s" % (rid or "?", names))

        # ⑤ 双向关联（attach）
        st_at, atb = req(base, "/api/workshop/%s/tree/node/%s/attach" % (ws_id, child_id),
                         {"rid": rid})
        node_after = (atb or {}).get("node") or {}
        res_after = (atb or {}).get("resource") or {}
        st_t3, tree3 = req(base, "/api/workshop/%s/tree" % ws_id)
        tree_node = _find_node((tree3 or {}).get("tree") or [], child_id) or {}
        cell("⑤ attach 后双向关联（节点含 rid + 资源回填 node_id）",
             st_at == 200 and rid in _rids_of(node_after)
             and res_after.get("task_node") == child_id
             and rid in _rids_of(tree_node),
             "节点含 rid=%s ｜ 资源.node=%s ｜ /tree 里含=%s" % (
                 rid in _rids_of(node_after), res_after.get("task_node"),
                 rid in _rids_of(tree_node)))

        # ⑥ 另一条路径：登记时直接带 node_id
        st_p2, pb2 = req(base, "/api/workshop/%s/resources" % ws_id, {
            "name": "成果 v2", "path": str(ws_dir / "library" / "成果-v2.md"),
            "node_id": root_id})
        rid2 = str(((pb2 or {}).get("resource") or {}).get("rid") or "")
        st_t4, tree4 = req(base, "/api/workshop/%s/tree" % ws_id)
        root_node = _find_node((tree4 or {}).get("tree") or [], root_id) or {}
        cell("⑥ 登记即带 node_id ⇒ 同样挂上",
             st_p2 == 200 and rid2 and rid2 in _rids_of(root_node),
             "rid2=%s ｜ 根节点含=%s" % (rid2 or "?", rid2 in _rids_of(root_node)))

        # ⑦ 挂不存在的资源
        st_x, xb = req(base, "/api/workshop/%s/tree/node/%s/attach" % (ws_id, root_id),
                       {"rid": "r_nope"})
        cell("⑦ 挂不存在的资源 ⇒ 400", st_x == 400, "HTTP %s" % st_x)

        # ⑧ 删登记不删文件
        st_d, _db = req(base, "/api/workshop/%s/resources/%s" % (ws_id, rid), method="DELETE")
        st_l2, lb2 = req(base, "/api/workshop/%s/resources" % ws_id)
        still_listed = any(r.get("rid") == rid for r in ((lb2 or {}).get("resources") or []))
        cell("⑧ 删登记：资源库没了，但磁盘文件仍在",
             st_d == 200 and not still_listed and product.is_file(),
             "HTTP %s ｜ 仍列=%s ｜ 文件仍在=%s" % (st_d, still_listed, product.is_file()))

        # ⑨ RESOURCES.md 落盘
        manifest = ws_dir / "RESOURCES.md"
        mtext = manifest.read_text(encoding="utf-8", errors="replace") if manifest.is_file() else ""
        cell("⑨ RESOURCES.md：登记后有、移除后不再列",
             manifest.is_file() and "成果 v2" in mtext and "成果 v1" not in mtext,
             "含v2=%s ｜ 仍含v1=%s" % ("成果 v2" in mtext, "成果 v1" in mtext))

        # ⑩ 审计五类
        st_au, au = req(base, "/api/audit")
        evs = {str(r.get("event") or "") for r in ((au or {}).get("events") or [])}
        need = {"workshop.tree.create", "workshop.tree.status", "workshop.resource.add",
                "workshop.tree.attach", "workshop.resource.remove"}
        cell("⑩ 审计五类齐全", need.issubset(evs), "缺=%s" % (sorted(need - evs) or "无"))

        # ⑪ 持久化
        wj = data_dir / "workshops.json"
        blob = wj.read_text(encoding="utf-8", errors="replace") if wj.is_file() else ""
        cell("⑪ 落盘：workshops.json 里 task_tree 真有节点",
             wj.is_file() and root_id in blob and child_id in blob,
             "文件 %d 字节" % len(blob))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:                                    # noqa: BLE001
            proc.kill()
        import shutil
        shutil.rmtree(data_dir, ignore_errors=True)

    print("-" * 92)
    print("结论：%s" % ("**任务树+资源库端到端全绿（嵌套 / 校验 / 双向关联 / 删登记不删文件 / 落盘留痕）**"
                      if not fails else "**有 %d 格失败**" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
