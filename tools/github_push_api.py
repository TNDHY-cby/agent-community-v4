# -*- coding: utf-8 -*-
"""GitHub 推送（**走 REST API**，不是 git 协议）—— `V-new`，2026-10-31 老板要求"更新 GitHub"后重建。

## 为什么不能用 `git push`

实测（2026-10-31，本机）：`github.com` 的 443 **TCP 通**，但 TLS/HTTP 一律被重置
（6 个候选 IP × 2 次全失败：`Operation too slow` / `Recv failure` / `Failed to connect`）；
而 **`api.github.com` 通**（返回 rate_limit JSON）。⇒ git 协议这条路走不了，只有 REST。

这正是当年 `temp/push_via_api.py` 存在的原因；那份工具后来丢了，本文件是它的替代品
（按同样思路重写，但**不再把载荷塞进命令行**——当年因此撞过 `WinError 206 文件名或扩展名太长`）。

## 两种模式

- **`--snapshot`（默认）**：把本地 `HEAD` 的**整棵树**作为一个新提交推上去，parent 用远端当前 head。
  · 快（只传**真正变化**的 blob：先拿远端父树递归清单，与本地 `ls-tree -r` 对比）；
  · 代价：**远端 sha ≠ 本地 HEAD**（新提交是"快照"），且 GitHub 历史少了中间提交 ——
    所以**判据不是比 sha，而是比 tree**：`远端 tree sha == 本地 HEAD 的 tree sha`（内容一致才算成功）。
- **`--replay`**：逐提交重放并照抄 author/committer/时间/message，使远端 sha == 本地 HEAD。
  ⚠️ 本文件**暂不实现**（明确抛错，不假装支持）；真要做，先把 --snapshot 这条路跑稳。

## 纪律（都是本项目踩过的坑）

1. **令牌只从文件读**（`<仓库上一级>/temp/.ghtoken`，或 `AC_GH_TOKEN_FILE`），**绝不进命令行、绝不打印**；
   输出里出现一律打码 `***`。
2. **不用 `http.client` 的默认解析**：本机 `api.github.com` 解析经常不对
   ⇒ 自建连接：**SNI 用域名、TCP 连 IP**（`--ip` 可指定；不给就按候选表试到通）。
3. **唯一可信判据是"问远端"**（tree 相等），`PATCH ref` 自己报成功不算。
4. **不 force**：远端 head 不是本地 HEAD 的祖先就说清楚并停下（除非显式 `--allow-remote-ahead`）。

用法：
    python tools/github_push_api.py --repo <oss 目录>                 # 快照推送 + 校验
    python tools/github_push_api.py --repo <oss> --dry-run            # 只算差异，不动远端
    python tools/github_push_api.py --repo <oss> --verify-only        # 只校验 tree 是否一致
    python tools/github_push_api.py --repo <oss> --ip 140.82.114.5    # 指定 API IP
"""
from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
import socket
import ssl
import subprocess
import sys
import urllib.parse
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

API_HOST = "api.github.com"
OWNER_REPO = "TNDHY-cby/agent-community-v4"
#: 候选 API IP（实测均可通；第一个通就用）
API_IPS = ("140.82.114.5", "140.82.114.6", "140.82.112.5", "140.82.113.5")
#: 一次 `POST /git/trees` 最多带多少条目（保守值；请求体小一点更稳）
MAX_TREE_ENTRIES = 400


# ── 令牌 ────────────────────────────────────────────────────────────
def token_file(repo: Path, explicit: str = "") -> Path:
    if (explicit or "").strip():
        return Path(explicit.strip())
    env = (os.environ.get("AC_GH_TOKEN_FILE") or "").strip()
    if env:
        return Path(env)
    return repo.resolve().parent / "temp" / ".ghtoken"


def _token(repo: Path, explicit: str = "") -> str:
    f = token_file(repo, explicit)
    if not f.is_file():
        raise SystemExit("找不到 GitHub 令牌文件（应放在 %s；可用 AC_GH_TOKEN_FILE 指定）。令牌绝不进仓库。"
                         % f)
    return f.read_text(encoding="utf-8").strip()


def _mask(text: str, token: str) -> str:
    return (text or "").replace(token, "***") if token else (text or "")


# ── 自建 HTTPS 连接：**SNI 用域名、TCP 连 IP** ──────────────────────
class _IPConn(http.client.HTTPSConnection):
    """`api.github.com` 在本机解析常不对 ⇒ 用这个：证书/SNI 用域名，socket 连指定 IP。"""

    def __init__(self, host: str, ip: str, timeout: float = 30.0):
        super().__init__(host, 443, timeout=timeout)
        self._ip = ip

    def connect(self):                                       # noqa: D401 — 覆盖父类
        sock = socket.create_connection((self._ip, 443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class Api:
    def __init__(self, token: str, ip: str = ""):
        self.token = token
        self.ip = ip or ""
        self.calls = 0

    def _conn(self, ip: str) -> _IPConn:
        ctx = ssl.create_default_context()
        return _IPConn(API_HOST, ip, timeout=60.0)

    def req(self, method: str, path: str, body=None, expect=(200, 201), raw=False):
        """发一个 API 请求；自动试候选 IP；令牌只进 header。

        ⚠️ `expect` 默认**含 201**（GitHub 的创建类 API——blobs/trees/commits——返回 201）。
        2026-10-31 实测踩过：默认只认 200 ⇒ 201 被当成"非预期"走错误分支返回 `{"_error"}`，
        而调用方只看 status 就以为成功了 ⇒ 建树拿到 `sha=None`、白传 413 个 blob。
        所以这里**只要不是 expect 里的状态码，就一定把 `_error` 带回去**，调用方必须检查。
        """
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": "token " + self.token,
            "Accept": "application/vnd.github+json",
            "User-Agent": "ac-github-push",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"

        ips = ([self.ip] if self.ip else []) + [x for x in API_IPS if x != self.ip]
        last_err = ""
        for ip in ips:
            try:
                conn = self._conn(ip)
                conn.request(method, path, body=payload, headers=headers)
                r = conn.getresponse()
                data = r.read()
                self.calls += 1
                if r.status in expect:
                    self.ip = ip
                    if raw:
                        return r.status, data
                    return r.status, (json.loads(data.decode("utf-8")) if data else {})
                # 非预期状态码：换 IP 没意义（是业务错误），直接返回
                self.ip = ip
                txt = _mask(data.decode("utf-8", "replace"), self.token)[:500]
                return r.status, {"_error": txt}
            except Exception as e:                           # noqa: BLE001 — 网络问题换下一个 IP
                last_err = "%s: %s" % (type(e).__name__, e)
                try:
                    conn.close()                             # noqa: F821 — 只在上一轮成功赋值时存在
                except Exception:                            # noqa: BLE001
                    pass
                continue
        raise SystemExit("连不上 %s（试过 %s）：%s" % (API_HOST, ", ".join(ips), last_err))


# ── git 侧（只用本地对象，不联网）──────────────────────────────────
def _git(repo: Path, args: list, timeout: int = 120) -> str:
    r = subprocess.run(["git", "-C", str(repo)] + args, capture_output=True,
                       timeout=timeout)
    if r.returncode != 0:
        raise SystemExit("git %s 失败：%s" % (" ".join(args),
                                             r.stderr.decode("utf-8", "replace")[:300]))
    return r.stdout.decode("utf-8", "replace")


def local_tree(repo: Path, rev: str = "HEAD") -> tuple:
    """返回 `(tree_sha, {path: (mode, type, sha)})`；路径用 `-z` 取，避免中文/空格被转义。"""
    head = _git(repo, ["rev-parse", rev]).strip()
    tree_sha = _git(repo, ["rev-parse", head + "^{tree}"]).strip()
    out = _git(repo, ["ls-tree", "-r", "-z", head])
    files = {}
    for rec in out.split("\0"):
        if not rec:
            continue
        meta, path = rec.split("\t", 1)
        mode, typ, sha = meta.split()
        files[path] = (mode, typ, sha)
    return head, tree_sha, files


def blob_content(repo: Path, sha: str) -> bytes:
    r = subprocess.run(["git", "-C", str(repo), "cat-file", "blob", sha],
                       capture_output=True, timeout=120)
    if r.returncode != 0:
        raise SystemExit("读 blob %s 失败" % sha)
    return r.stdout


# ── 主流程 ──────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="仓库根（发布副本 oss）")
    ap.add_argument("--branch", default="master")
    ap.add_argument("--repo-slug", default=OWNER_REPO, help="owner/repo")
    ap.add_argument("--ip", default="", help="指定 api.github.com 的 IP")
    ap.add_argument("--token-file", default="")
    ap.add_argument("--dry-run", action="store_true", help="只算差异与请求体规模，不动远端")
    ap.add_argument("--verify-only", action="store_true", help="只校验远端 tree 是否与本地一致")
    ap.add_argument("--allow-remote-ahead", action="store_true",
                    help="远端 head 不是本地祖先时仍要推（危险，默认拒绝）")
    ap.add_argument("--message", default="", help="快照提交的说明（默认自动生成）")
    a = ap.parse_args()

    repo = Path(a.repo).resolve()
    if not (repo / ".git").exists():
        raise SystemExit("不是 git 仓库：%s" % repo)
    token = _token(repo, a.token_file)
    api = Api(token, a.ip)

    head, tree_sha, files = local_tree(repo)
    print("[local] HEAD   = %s" % head)
    print("[local] tree   = %s" % tree_sha)
    print("[local] 文件数 = %d" % len(files))

    base = "/repos/%s" % a.repo_slug

    # 远端当前 head
    st, ref = api.req("GET", "%s/git/ref/heads/%s" % (base, a.branch), expect=(200, 404))
    if st == 404 or not (ref or {}).get("object"):
        raise SystemExit("远端没有 %s 分支（本工具只做增量推送，不做首次初始化）" % a.branch)
    remote_sha = (ref.get("object") or {}).get("sha") or ""
    print("[remote] %s = %s" % (a.branch, remote_sha))

    # 远端那个提交的树（递归清单）
    st, rc = api.req("GET", "%s/git/commits/%s" % (base, remote_sha))
    if st != 200:
        raise SystemExit("读远端提交失败（HTTP %s）：%s" % (st, rc.get("_error")))
    r_tree = (rc.get("tree") or {}).get("sha") or ""
    st, rt = api.req("GET", "%s/git/trees/%s?recursive=1" % (base, r_tree))
    if st != 200:
        raise SystemExit("读远端树失败（HTTP %s）：%s" % (st, rt.get("_error")))
    truncated = bool(rt.get("truncated"))
    remote_files = {}
    for it in rt.get("tree") or []:
        if it.get("type") == "blob":
            remote_files[it["path"]] = (it.get("mode") or "100644", "blob", it["sha"])
    print("[remote] 父树 = %s ｜ 文件数 = %d%s"
          % (r_tree, len(remote_files), "（⚠️ 被截断，改用全量建树）" if truncated else ""))

    # 差异
    to_add, to_del = [], []
    for path, (mode, typ, sha) in files.items():
        old = remote_files.get(path)
        if old is None or old[2] != sha:
            to_add.append((path, mode, typ, sha))
    for path in remote_files:
        if path not in files:
            to_del.append(path)
    print("[diff] 需上传 %d ｜ 需删除 %d ｜ 不变 %d"
          % (len(to_add), len(to_del), len(files) - len(to_add)))

    if a.verify_only:
        same = (r_tree == tree_sha)
        print("[verify] 远端 tree = %s" % r_tree)
        print("[verify] 本地 tree = %s" % tree_sha)
        print("[verify] %s" % ("OK：两侧内容一致" if same else "★不一致（远端还没这个版本的内容）"))
        return 0 if same else 1

    if a.dry_run:
        for path, mode, typ, sha in to_add[:15]:
            print("   + %s" % path)
        if len(to_add) > 15:
            print("   …另 %d 个" % (len(to_add) - 15))
        for path in to_del[:10]:
            print("   - %s" % path)
        if len(to_del) > 10:
            print("   …另 %d 个" % (len(to_del) - 10))
        print("[dry-run] 不发任何写请求；API 调用数到现在 = %d" % api.calls)
        return 0

    # 祖先检查（不 force）：远端 head 必须是本地 HEAD 的祖先，否则说不清楚就停
    if remote_sha and not a.allow_remote_ahead:
        r = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor",
                            remote_sha, "HEAD"], capture_output=True)
        if r.returncode != 0:
            print("★ 远端 head（%s）不是本地 HEAD 的祖先 —— 说明远端有本地没有的提交。" % remote_sha)
            print("  不要 force。先把它取回来对齐（或确认它就是这里的旧版本），再重跑；")
            print("  确实要覆盖（会丢远端那几笔）时加 --allow-remote-ahead。")
            return 2

    # 传 blob（**GitHub 建 blob 返回 201**，必须显式认它；只看 status 不看 body 曾踩过坑）
    uploaded = 0
    for path, mode, typ, sha in to_add:
        st, _b = api.req("POST", "%s/git/blobs" % base,
                         {"content": base64.b64encode(blob_content(repo, sha)).decode("ascii"),
                          "encoding": "base64"}, expect=(201,))
        if st != 201 or not (_b or {}).get("sha"):
            raise SystemExit("上传 blob 失败（%s，HTTP %s）：%s"
                             % (path, st, ((_b or {}).get("_error") or "")[:300]))
        uploaded += 1
        if uploaded % 50 == 0:
            print("   …已传 %d/%d" % (uploaded, len(to_add)))
    print("[blob] 上传 %d 个" % uploaded)

    # 建新树：以**远端父树**为底，只列变化条目（删除用 sha=None）
    entries = []
    for path, mode, typ, sha in to_add:
        entries.append({"path": path, "mode": mode, "type": typ, "sha": sha})
    for path in to_del:
        old = remote_files[path]
        entries.append({"path": path, "mode": old[0], "type": "blob", "sha": None})

    if truncated:
        # 递归清单不可信 ⇒ 不带 base_tree，用**本地全量**建树（请求会大些，但正确优先）
        print("[tree] 父树被截断 ⇒ 用本地全量建树（%d 条）" % len(files))
        chunks, cur = [], []
        for path, (mode, typ, sha) in files.items():
            cur.append({"path": path, "mode": mode, "type": typ, "sha": sha})
            if len(cur) >= MAX_TREE_ENTRIES:
                chunks.append(cur)
                cur = []
        if cur:
            chunks.append(cur)
        # 先建一棵空树，再逐块拼（每块 base_tree=上一块结果）
        base_tree = None
        for i, chunk in enumerate(chunks, 1):
            body = {"tree": chunk}
            if base_tree:
                body["base_tree"] = base_tree
            st, nb = api.req("POST", "%s/git/trees" % base, body, expect=(201,))
            if st != 201 or not (nb or {}).get("sha"):
                raise SystemExit("建树失败（第 %d 块，HTTP %s）：%s"
                                 % (i, st, ((nb or {}).get("_error") or "")[:300]))
            base_tree = nb.get("sha")
        new_tree = base_tree
    else:
        if entries:
            print("[tree] 变化条目 %d 个" % len(entries))
            base_tree = None
            pending = list(entries)
            new_tree = None
            while pending:
                chunk, pending = pending[:MAX_TREE_ENTRIES], pending[MAX_TREE_ENTRIES:]
                body = {"tree": chunk}
                prev = new_tree or r_tree
                if prev:
                    body["base_tree"] = prev
                st, nb = api.req("POST", "%s/git/trees" % base, body, expect=(201,))
                if st != 201 or not (nb or {}).get("sha"):
                    raise SystemExit("建树失败（HTTP %s）：%s"
                                     % (st, ((nb or {}).get("_error") or "")[:300]))
                new_tree = nb.get("sha")
        else:
            new_tree = r_tree
            print("[tree] 内容无变化 ⇒ 直接复用父树")

    print("[tree] 新树 = %s" % new_tree)
    if new_tree != tree_sha:
        # tree 对不上就地停手（不推一个内容不对的提交）
        print("★ 新树的 sha 与本地 HEAD 的 tree 不一致：")
        print("   本地 tree = %s" % tree_sha)
        print("   远端新树 = %s" % new_tree)
        print("  ⇒ 停手，不推。多半是路径模式（mode）或删除项没对齐，先查明再重跑。")
        return 1

    msg = a.message.strip() or ("快照同步：%s（%s）" % (head[:10], "内容与本地 HEAD 的树一致"))
    st, nc = api.req("POST", "%s/git/commits" % base,
                     {"message": msg, "tree": new_tree, "parents": [remote_sha]}, expect=(201,))
    if st != 201 or not (nc or {}).get("sha"):
        raise SystemExit("建提交失败（HTTP %s）：%s" % (st, ((nc or {}).get("_error") or "")[:300]))
    new_commit = nc.get("sha")
    print("[commit] %s" % new_commit)

    st, pb = api.req("PATCH", "%s/git/refs/heads/%s" % (base, a.branch),
                     {"sha": new_commit, "force": False})
    if st not in (200, 201):
        raise SystemExit("更新分支失败（HTTP %s）：%s" % (st, (pb.get("_error") or "")[:300]))

    # 校验：**问远端**（tree 相等才算成功；sha 在快照模式下本来就不等于本地 HEAD）
    st, ref2 = api.req("GET", "%s/git/ref/heads/%s" % (base, a.branch))
    got = (ref2.get("object") or {}).get("sha") or ""
    st, cc = api.req("GET", "%s/git/commits/%s" % (base, got))
    got_tree = (cc.get("tree") or {}).get("sha") or ""
    print("[verify] 远端 %s = %s" % (a.branch, got))
    print("[verify] 远端 tree = %s" % got_tree)
    print("[verify] 本地 tree = %s" % tree_sha)
    if got_tree != tree_sha:
        print("[verify] ★不一致 —— 别报成功")
        return 1
    print("[verify] OK：远端内容与本地 HEAD 一致（快照模式，sha 不同是预期的）")
    print("[stat] API 调用 %d 次" % api.calls)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
