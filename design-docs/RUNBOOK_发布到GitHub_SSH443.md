# RUNBOOK · 发布到 GitHub（走 SSH over 443）

> 本文是 2026-11-01 实测后写的**唯一口径**。凡"怎么把代码推到 GitHub"一律看这里，别再各显神通。
> 适用：Windows 开发机 + 中国大陆网络（`github.com` 的 git/https 协议不通，但 `ssh.github.com:443` 通）。

---

## 1. 三条路都试过，只有第三条能走（实测数据，不是听说）

| 路 | 实测结果 | 结论 |
|---|---|---|
| **`git push https://github.com/...`**（git 协议 / https） | 6 个候选 IP × 2 次全失败：`Operation too slow`、`Recv failure: Connection was reset`、`Failed to connect`。TCP 443 通，但 TLS/HTTP 一律被重置 | **不通** |
| **REST API 逐对象上传**（blob→tree→commit→PATCH ref） | `api.github.com` 通（返回 rate_limit）。但每次请求都新建 TCP+TLS：新建连接 990/976/1496 ms，复用 453/890/242/244 ms，其中某 IP TLS 握手 3989 ms。413 个 blob 串行 × ~1.2 s ≈ 8 分钟，抖动就中断（实测跑 12 分钟未提交） | **能通但太慢、太脆** |
| **SSH over 443**（`ssh.github.com:443`） | TCP 通、SSH banner `SSH-2.0-a09ebb4f` **647 ms**；配好后 `git push` **7 秒**推完 43 个提交（`fa06365..5d02461`） | **✅ 采用** |

**为什么 SSH 反而快**：它把整个历史打成一个 pack **一次传完**（不是每个对象一次 HTTP 往返），
而且**保留真实提交历史**——远端 sha 会**等于**本地 HEAD，比"快照压成一个提交"强一个档。

---

## 2. 一次性配置（换机器照抄）

### 2.1 生成/选一把专用密钥

```bash
ssh-keygen -t ed25519 -C "dsh-publish" -f ~/.ssh/id_cloud_dsh
```
- **专用**：别复用你登录别的服务器的密钥；
- 建议设 passphrase（不设的话，任何能读该文件的进程都能以你的身份写仓库）。

### 2.2 把**公钥**加到 GitHub 账号

网页：Settings → SSH and GPG keys → New SSH key，粘贴 `~/.ssh/id_cloud_dsh.pub` 全文。
命令行（需要 PAT 带 `admin:public_key`）：

```bash
# 载荷写文件，别塞进命令行（公钥含特殊字符 + 命令行长度的坑）
python - <<'PY'
import json, os, pathlib
key = (pathlib.Path(os.path.expanduser("~")) / ".ssh" / "id_cloud_dsh.pub").read_text().strip()
pathlib.Path("/tmp/_ghkey.json").write_text(json.dumps({"title": "dsh-publish", "key": key}))
PY
curl -sS -X POST -H "Authorization: token $GH_TOKEN" -H "Accept: application/vnd.github+json" \
     -H "Content-Type: application/json" --data @/tmp/_ghkey.json https://api.github.com/user/keys
rm -f /tmp/_ghkey.json        # 用完删
```

> 挂之前先 `GET /user/keys` 看一眼，别把已有的顶掉。
> **权限说明（要让人知道）**：这把钥匙能给**该账号能写的所有仓库**推送。挂之前确认账号下有哪些仓库
> （`GET /user/repos?affiliation=owner,collaborator,organization_member`）。

### 2.3 `~/.ssh/config` 加一个 Host 块

```
Host github-443
  HostName ssh.github.com
  Port 443
  User git
  IdentityFile ~/.ssh/id_cloud_dsh
  IdentitiesOnly yes
```

⚠️ **这个文件不能有 BOM**。实测踩过：BOM 会让 OpenSSH 报
`no argument after keyword "\357\273\277"` 然后直接失败（看着像密钥问题，其实是文件头）。
检查：`python -c "print(open(p,'rb').read()[:3])"` → 不应是 `b'\xef\xbb\xbf'`。

### 2.4 仓库的 remote 指过去

```bash
git -C <发布副本> remote set-url origin git@github-443:TNDHY-cby/agent-community-v4.git
```

### 2.5 Windows 特有：`core.sshCommand` 必须指向**能用的那个 ssh**

实测本机 `C:\Windows\System32\OpenSSH\ssh.exe` **是坏的**（缺 `libcrypto-3-x64.dll` / `libssl-3-x64.dll`，
`ssh -V` 都直接 exit 255 且无任何输出）。Git 自带的那个是好的（`OpenSSH_10.3p1`）。

但它的路径含空格，直接写进 `core.sshCommand` 会被 git 按空格切碎
（报 `C:/Program: No such file or directory`）⇒ **用 8.3 短名**：

```bash
git -C <发布副本> config core.sshCommand "C:/PROGRA~1/Git/usr/bin/ssh.exe"
```

（`C:\PROGRA~1` 就是 `C:\Program Files` 的短名，先 `cmd /c dir /x C:\` 确认它存在。）

---

## 3. 发布流程（每次发版）

> ★2026-11-04 老板：「**不更新 gitee 了，换成 Github**」⇒ 本文就是**主发布路径**：
> 每次发版推 GitHub（SSH over 443），Gitee 保留为**备用**（要发那边时加 `-Target gitee`）。
> 判据只有一条：**问远端**，`ls-remote` 的 sha == 本地 HEAD。

```powershell
# ① 同步 + 提交（publish_acv4.ps1 默认 -Target github，会一路推到 GitHub）
.\publish_acv4.ps1 -Message "<ascii 摘要>" -ChangelogFile <CHANGELOG 条目绝对路径> -Target gitee

# ② 如需手动补推（脚本已做过也安全，幂等）：SSH over 443
git -C <发布副本> push origin master

# ③ 判据：**问远端**，sha 必须等于本地 HEAD
$local  = git -C <发布副本> rev-parse HEAD
$remote = (git -C <发布副本> ls-remote origin master) -split '\s+' | Select-Object -First 1
if ($remote -eq $local) { "OK" } else { "★不一致，别说成功" }
```

**判据只认 ②③ 的结果**：`git push` 自己打印 `-> master` 不算数（它把进度写 stderr，脚本里常被误判）。

---

## 4. 已知的坑（都是真踩过的）

| 坑 | 症状 | 处理 |
|---|---|---|
| 系统 ssh 缺 DLL | `ssh -V` exit 255、零输出，看着像"命令不存在" | 用 Git 自带 ssh，`core.sshCommand` 指短名路径 |
| `~/.ssh/config` 带 BOM | `no argument after keyword "\357\273\277"` | 去 BOM（`utf-8-sig` 读、`utf-8` 写） |
| 路径含空格 | `C:/Program: No such file or directory` | 用 8.3 短名 `C:/PROGRA~1/...` |
| 令牌进命令行 | 进程表可见、日志泄漏 | 令牌只从文件读、只进 HTTP header；输出里出现一律打码 |
| 载荷塞命令行 | `WinError 206 文件名或扩展名太长` | 请求体走文件或走 Python 直发，不走 argv |
| 以为 `-MirrorGithub` 会推 | `publish_acv4.ps1` 里它指向的 `temp/github_mirror_push.py` **不存在**，会**静默跳过** | 别指望它；GitHub 按本文第 3 节单独推 |
| REST 快照模式 | `tools/github_push_api.py` 只把内容压成 1 个提交、丢中间历史 | 只在 SSH 也不通时应急用，且判据是 **tree 相等**（不是 sha） |

---

## 5. 要收回权限时（吊销）

- 网页：Settings → SSH and GPG keys → 找到 `dsh-publish*` → Delete；
- 命令行：`DELETE /user/keys/{id}`（挂的时候记下 id，本次是 `168313647`）。

删掉后本机 `ssh -T git@ssh.github.com` 会回到 `Permission denied (publickey)`，属正常。

---

## 6. 现状（2026-11-01）

- GitHub `TNDHY-cby/agent-community-v4`：master = `5d02461`（4.38.0），与发布副本 HEAD 一致；
- 断更期间（2026-10-16 ~ 2026-10-31）的 **43 个提交**已一次性补推，历史连续（远端含 `fa06365` 为祖先）；
- 挂上的公钥：`id=168313647`、`title=dsh-publish-2026-11-01`、有写权限；
  该账号下**只有这一个仓库**（`GET /user/repos` 实测 = 1）。
