# -*- coding: utf-8 -*-
"""tools/github_push.py —— 把发布副本推到 GitHub（**和 Gitee 并列的正式远端**）。

老板 2026-10-21：「**更新Github**」⇒ 发布 = 两边都推（此前 2026-10-16 曾只推 Gitee）。

## 为什么不是一句 `git push`

1. **令牌不进命令行**：`https://x-access-token:<token>@…` 只在**本进程内**拼，输出一律打码；
   令牌来源按顺序：`--token-file` > 环境变量 `AC_GH_TOKEN` > `GITHUB_TOKEN`；
2. **关凭据助手**（`-c credential.helper=`）：无人值守时 GCM 会**卡住不报错**（实测挂死 20 分钟）；
3. **指定可达 IP**（`-c http.curloptResolve=…`）：某些网络的 DNS/hosts 把 github.com 指错，
   实测 `140.82.114.4` 通、`140.82.112.4` 超时；候选表可用 `--ips` 覆盖；
4. **进程级硬超时**：卡住就杀，不让发布流程挂死；
5. **判据只认远端 sha**：`ls-remote` 的 sha == 本地 HEAD 才算成。

用法：
    python tools/github_push.py --repo <发布副本路径> [--branch master] [--tags]
    python tools/github_push.py --repo <…> --token-file <路径>
退出码：0 = 已同步（远端 sha == 本地 HEAD）；1 = 没成；2 = 没令牌。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SLUG = "TNDHY-cby/agent-community-v4"
DEFAULT_IPS = ("140.82.114.4", "140.82.114.5", "140.82.113.4", "140.82.112.3")


def _token(args) -> str:
    if args.token_file:
        p = Path(args.token_file)
        if p.exists():
            t = p.read_text(encoding="utf-8").strip()
            if t:
                return t
    for name in ("AC_GH_TOKEN", "GITHUB_TOKEN"):
        v = str(os.environ.get(name) or "").strip()
        if v:
            return v
    return ""


def _git(args_list, repo, timeout, ip=None):
    cmd = ["git", "-C", str(repo)]
    if ip:
        cmd += ["-c", "http.curloptResolve=github.com:443:%s" % ip]
    cmd += ["-c", "credential.helper="]
    cmd += args_list
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "超时（%ss）" % timeout


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="推发布副本到 GitHub（正式远端之一）")
    ap.add_argument("--repo", required=True, help="发布副本路径")
    ap.add_argument("--branch", default="master")
    ap.add_argument("--tags", action="store_true", help="连 tag 一起推（个别旧 tag 冲突时以 --tags-force 处理）")
    ap.add_argument("--tags-force", action="store_true", help="用 --force 推 tag（旧 tag 指向不同提交时）")
    ap.add_argument("--token-file", default="", help="令牌文件（不传则读环境变量 AC_GH_TOKEN / GITHUB_TOKEN）")
    ap.add_argument("--ips", default=",".join(DEFAULT_IPS), help="候选可达 IP，逗号分隔")
    ap.add_argument("--timeout", type=int, default=240, help="单次 git 操作硬超时（秒）")
    args = ap.parse_args(argv)

    repo = Path(args.repo)
    token = _token(args)
    if not token:
        print("[FAIL] 没有 GitHub 令牌：用 --token-file 或环境变量 AC_GH_TOKEN")
        return 2
    url = "https://x-access-token:%s@github.com/%s.git" % (token, SLUG)

    def mask(s: str) -> str:
        return (s or "").replace(token, "***")

    rc, out = _git(["rev-parse", "HEAD"], repo, 30)
    local = (out or "").strip()
    print("本地 HEAD =", local)

    for ip in [x.strip() for x in args.ips.split(",") if x.strip()]:
        print("尝试 %s …" % ip, flush=True)
        rc, out = _git(["push", url, "refs/heads/%s:refs/heads/%s" % (args.branch, args.branch)],
                       repo, args.timeout, ip=ip)
        print("  push rc=%s  %s" % (rc, mask("\n".join((out or "").strip().splitlines()[-3:]))))
        if rc != 0:
            continue
        if args.tags or args.tags_force:
            flag = "--force" if args.tags_force else None
            targs = ["push"] + ([flag] if flag else []) + ["--tags", url]
            rc2, out2 = _git(targs, repo, args.timeout, ip=ip)
            print("  tags rc=%s  %s" % (rc2, mask("\n".join((out2 or "").strip().splitlines()[-3:]))))
        rc3, out3 = _git(["ls-remote", "https://github.com/%s.git" % SLUG,
                          "refs/heads/%s" % args.branch], repo, 60, ip=ip)
        line = (out3 or "").strip().splitlines()
        got = line[-1].split()[0] if line else None
        print("  远端 sha =", got)
        if got == local:
            print("[OK] GitHub 已同步：%s == 本地 HEAD" % got)
            return 0
        print("  ! 远端 sha 与本地不一致，换 IP 重试")
    print("[FAIL] 候选 IP 都没成（未改动远端）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
