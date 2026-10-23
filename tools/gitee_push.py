# -*- coding: utf-8 -*-
"""Gitee 推送 + 校验 —— **令牌绝不进命令行、绝不打印**（老板 2026-10-16 换源后的一处真相）。

## 为什么单独有这个工具

老板 2026-10-16：「GitHub 网络连接不稳定，换成 Gitee（码云）的」
⇒ 主发布远端改成 `https://gitee.com/TNDHY-cby/external-agents-community.git`。

四条纪律（都是本项目踩过的坑换来的）：
1. **令牌只从文件读**（默认 `<仓库上一级>/temp/.giteetoken`，可用 `AC_GITEE_TOKEN_FILE` 覆盖）；
   文件**绝不进仓库、绝不打印**；输出里出现令牌一律替换成 `***`；
2. **禁掉凭据助手**（`-c credential.helper=`）：GitHub 时代就是被 GCM 弹窗卡住的；
   带凭据的 URL + 空助手 = 不弹窗、不落盘到 `.git/config`；
3. **唯一可信判据是"问远端"**：推送后 `git ls-remote` 取远端 sha，与本地 HEAD 比对；
   `git push` 自己报成功不算数（它把进度写 stderr，脚本里常被误判）；
4. **★推之前先问远端**（2026-10-23 补）：如果远端有你没有的提交，**先停下来说清楚**，
   不要等 `git push` 回一句 `non-fast-forward` 才让人去猜。

## 第 4 条为什么重要（真实场景）

发布是「dev → oss → Gitee」**单向**推。如果这段时间里有人（比如前端）**直接往 Gitee master 推过东西**，
本地 oss 就落后于远端。这时候：
- 硬推 = 抹掉对方的工作（**绝对不许**）；
- 不硬推 = `git push` 直接失败，只回一句 `! [rejected] master -> master (non-fast-forward)`，
  而发布脚本只会说"推送失败"，**没人知道是"有人推过 master"**。

⇒ 本工具现在会：**先 `ls-remote` → 判断远端是不是本地 HEAD 的祖先**：
祖先 ⇒ 正常推；不是 ⇒ 打印「远端有你没有的 N 个提交」+ 是谁的 + 三条处置办法，然后**退出码 2**（明确区分"没推"和"推了但没成功"）。

用法：
    python tools/gitee_push.py --repo <oss 仓库根> [--branch master] [--tags]
    python tools/gitee_push.py --repo <oss> --verify-only           # 只校验远端 == 本地
    python tools/gitee_push.py --repo <oss> --allow-remote-ahead    # 明知远端领先仍要推（危险，要显式给）
    python tools/gitee_push.py --repo <oss> --force-with-lease <远端 sha>  # 首次覆盖远端自动初始化的提交
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

GITEE = "https://gitee.com/TNDHY-cby/external-agents-community.git"
GITEE_USER = "TNDHY-cby"


def token_file(repo: Path, explicit: str = "") -> Path:
    """令牌文件位置：`AC_GITEE_TOKEN_FILE` > `<仓库上一级>/temp/.giteetoken`。

    ★不写死本机绝对路径（可移植性）：默认位置就是"发布副本的同级 temp/"，换机器只改环境变量。
    """
    if (explicit or "").strip():
        return Path(explicit.strip())
    env = (os.environ.get("AC_GITEE_TOKEN_FILE") or "").strip()
    if env:
        return Path(env)
    return repo.resolve().parent / "temp" / ".giteetoken"


def _token(repo: Path, explicit: str = "") -> str:
    f = token_file(repo, explicit)
    if not f.is_file():
        raise SystemExit("找不到令牌文件（应放在 %s，且不要提交进仓库；可用 AC_GITEE_TOKEN_FILE 指定）" % f)
    return f.read_text(encoding="utf-8").strip()


def _mask(text: str, token: str) -> str:
    return (text or "").replace(token, "***") if token else (text or "")


def _auth_url(token: str, url: str = "") -> str:
    """把令牌塞进 https URL；**本地路径/裸库原样返回**（别乱拼前缀）。

    `AC_GITEE_URL` 可覆盖远端（测试/镜像用：指到本地裸库就不会碰真远端）。
    """
    u = url or (os.environ.get("AC_GITEE_URL") or "").strip() or GITEE
    if not u.startswith(("http://", "https://")):
        return u
    return u.replace("https://", "https://%s:%s@" % (GITEE_USER, token))


def _git(repo: Path, args: list, token: str, timeout: int = 900):
    cmd = ["git", "-C", str(repo), "-c", "credential.helper=",
           "-c", "http.lowSpeedLimit=1024", "-c", "http.lowSpeedTime=60"] + args
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    return r.returncode, _mask(r.stdout or "", token), _mask(r.stderr or "", token)


def _local_head(repo: Path) -> str:
    rc, out, _ = _git(repo, ["rev-parse", "HEAD"], "", timeout=60)
    return out.strip() if rc == 0 else ""


def _is_ancestor(repo: Path, maybe_ancestor: str) -> bool:
    """`maybe_ancestor` 是不是 `HEAD` 的祖先（远端落后＝可以快进推；不是＝远端领先/分叉）。"""
    rc, _, _ = _git(repo, ["merge-base", "--is-ancestor", maybe_ancestor, "HEAD"], "", timeout=60)
    return rc == 0


def _explain_rejected(repo: Path, remote: str, local: str,
                      url: str = "", branch: str = "master") -> None:
    """把"推送被拒"翻译成人话 + 给出三条处置办法（这才是"失败要说话"）。"""
    print("")
    print("=" * 78)
    print("★ 推送被拒：**远端有你没有的提交**（本地 master 落后于 Gitee）")
    print("=" * 78)
    print("  本地 HEAD      = %s" % local)
    print("  gitee/master   = %s" % remote)
    # ⚠️ 本地**通常没有**远端那些对象（"落后"本来就是这个意思）⇒ 光 `git log HEAD..sha` 查不到。
    #    所以先 fetch 一次（只更新 FETCH_HEAD，**不动本地分支/工作树**），再列提交；失败就如实说。
    shown = False
    rc, out, _ = _git(repo, ["log", "--oneline", "--max-count=8", "HEAD..%s" % remote], "", timeout=60)
    if rc == 0 and out.strip():
        print("  远端比本地多出来的提交（= 别人直接推过 master）：")
        for line in out.strip().splitlines()[:8]:
            print("    %s" % line[:120])
        shown = True
    elif url:
        rc2, _o2, e2 = _git(repo, ["fetch", "--quiet", url, branch], "", timeout=300)
        rc3, out3, _ = (_git(repo, ["log", "--oneline", "--max-count=8", "HEAD..FETCH_HEAD"], "", timeout=60)
                        if rc2 == 0 else (1, "", ""))
        if rc3 == 0 and out3.strip():
            print("  远端比本地多出来的提交（= 别人直接推过 master）：")
            for line in out3.strip().splitlines()[:8]:
                print("    %s" % line[:120])
            shown = True
        elif not shown:
            print("  （列出远端那些提交时没成功：%s）" % (e2.strip()[:160] or "未知原因"))
    print("")
    print("  为什么会这样：发布是 **dev → oss → Gitee 单向**推；这期间有人（多半是前端）")
    print("                直接往 Gitee 的 master 推了东西，本地 oss 就落后了。")
    print("")
    print("  怎么处置（**不要 force push** —— 那会抹掉对方的工作）：")
    print("    1) 先把对方的东西取回来并对齐（推荐）：")
    print("         git -C \"<oss>\" fetch gitee master")
    print("         git -C \"<oss>\" log --oneline HEAD..FETCH_HEAD     # 看是谁的、几笔")
    print("         git -C \"<oss>\" merge --ff-only FETCH_HEAD        # 快进（没有冲突时）")
    print("       然后重跑发布（此时远端已是本地祖先，推送会正常）")
    print("    2) 只要前端那几笔里的**部分文件**：`git -C <oss> cherry-pick <sha>`（保留作者）")
    print("    3) 让对方改走分支：`refs/heads/frontend/<日期>`（见通道 259 号）")
    print("")
    print("  确认要覆盖远端（**只有你明确知道会丢掉什么**时才用）：")
    print("         python tools/gitee_push.py --repo <oss> --force-with-lease %s" % remote[:12])
    print("=" * 78)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="仓库根目录（发布副本）")
    ap.add_argument("--branch", default="master")
    ap.add_argument("--tags", action="store_true", help="同时推所有本地 tag")
    ap.add_argument("--force-with-lease", default="", metavar="EXPECTED_REMOTE_SHA",
                    help="覆盖远端时用；必须给出**你去查过的**远端当前 sha（防误伤）")
    ap.add_argument("--allow-remote-ahead", action="store_true",
                    help="明知远端领先仍然要推（危险；配合 --force-with-lease 才真正生效）")
    ap.add_argument("--token-file", default="", help="令牌文件路径（默认读 AC_GITEE_TOKEN_FILE，或发布副本同级 temp/.giteetoken）")
    ap.add_argument("--verify-only", action="store_true")
    a = ap.parse_args()

    repo = Path(a.repo).resolve()
    if not (repo / ".git").exists():
        raise SystemExit("不是 git 仓库：%s" % repo)
    token = _token(repo, a.token_file)
    url = _auth_url(token)
    local = _local_head(repo)

    # ── 推之前先问远端（第 4 条纪律）：远端领先就**先说话**，别等 push 报 non-fast-forward ──
    rc, out, err = _git(repo, ["ls-remote", url, a.branch], token, timeout=300)
    if rc != 0:
        print("[pre] ls-remote 失败：%s" % err.strip()[:300])
        return 1
    remote_before = (out.strip().split()[0] if out.strip() else "")

    if not a.verify_only and remote_before and not a.allow_remote_ahead and not a.force_with_lease:
        if not _is_ancestor(repo, remote_before):
            print("[pre] 远端 = %s，本地 HEAD = %s" % (remote_before, local))
            _explain_rejected(repo, remote_before, local, url=url, branch=a.branch)
            print("[pre] **没有推送**（退出码 2＝「没推」，区别于「推了但失败」）")
            return 2

    if not a.verify_only:
        args = ["push"]
        if a.force_with_lease:
            args.append("--force-with-lease=%s:%s" % (a.branch, a.force_with_lease))
        args += [url, a.branch]
        if a.tags:
            args.append("--tags")
        rc, out, err = _git(repo, args, token)
        print("[push] exit=%s" % rc)
        tail = (out.strip().splitlines() + err.strip().splitlines())[-4:]
        for line in tail:
            print("   " + line[:200])
        if rc != 0:
            low = (out + err).lower()
            if "non-fast-forward" in low or "rejected" in low or "fetch first" in low:
                _explain_rejected(repo, remote_before or "(未知)", local, url=url, branch=a.branch)
            else:
                print("推送失败（令牌已打码）—— 上面是 git 的原话，别再猜别的原因")
            return 1

    rc, out, err = _git(repo, ["ls-remote", url, a.branch], token, timeout=300)
    if rc != 0:
        print("[verify] ls-remote 失败：%s" % err.strip()[:300])
        return 1
    remote = (out.strip().split()[0] if out.strip() else "")
    print("[verify] local  HEAD = %s" % local)
    print("[verify] gitee %-6s = %s" % (a.branch, remote))
    if not remote:
        print("[verify] 远端这个分支还不存在")
        return 1
    if remote != local:
        print("[verify] ★不一致：远端 sha != 本地 HEAD（别报「已同步」）")
        return 1
    print("[verify] OK：远端与本地一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
