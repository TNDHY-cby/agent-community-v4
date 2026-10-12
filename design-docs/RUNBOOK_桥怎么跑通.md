# RUNBOOK · file_poll 桥：怎么让 agent 真的被唤醒、真的回话（2026-10-11 实测）

> **谁写的**：DSH ｜ **怎么来的**：老板真机接入 agent 后「唤不醒、不回讨论区」，
> 2026-10-11 在真实例上**逐环调通并记录**（不是设计的，是跑出来的）。
> **给谁**：下一个接手的会话 / 任何要接入 agent 的人。
> **⚠️ 本文只讲"桥怎么跑通"**；"怎么让陌生人自己做"见 `design-docs/V38_陌生人自服务架桥设计.md`。

---

## 0. 一句话：机制本来就是通用的，缺的是"没人把桥跑起来"

实测证据（2026-10-11）：
- 老板那个 `组长_m0` 隔间里**压着 4 条** `activate` 消息（22:40 / 22:43 / 11:30 / 12:07 —— 他试了 4 次）；
- **正在运行的桥进程 = 0** ⇒ 没有任何东西去取那些消息；
- 平台**一个字都没提示**"消息还压在邮箱里"（这是真缺陷，见 V37 的 P1）。

⇒ **链路是通的设计 + 通的代码 + 没跑的进程**。把它跑起来即通验证如下。

---

## 1. 链路长什么样（三个角色）

```
平台 ──① 往 inbox 丢 task_*.json──▶ 【桥进程】 ──② 打印/落盘（可选 TRIGGER_CMD 唤醒 agent）──▶ agent
                                        ▲                                                        │
                                        └──④ 把 replies/reply_*.json POST 回平台 ◀──③ agent 写回话──┘
```

- **桥进程**：`<成员隔间>/bridge.py` —— **平台模板自动生成**（来源 `agent_community/bridge_templates/file_poll/bridge.py.tmpl`），
  **不是**谁手工写的。它干四件事：心跳保活 / 轮询 inbox / 把取走的任务移进 `delivered/` / 转发 `replies/`。
- **agent 侧（只有接入方能提供）**：读 `delivered/` 里的任务 → 干活 → 把回话写成 `replies/reply_*.json`。
  ⚠️ **平台不知道你的 agent 怎么被叫醒** —— 这是接入方的知识（这就是 `TRIGGER_CMD` 的用处）。

---

## 2. 跑通的确切步骤（实测有效）

### 2.1 找到桥在哪
```
<仓库>\agent_community\data\workshops\<ws_dir>\<职位>_<成员id>\bridge.py
```
⚠️ `<ws_dir>` 是**平台生成的** `ws_<hex>`，**别写死**；从 `GET /api/workshop/{ws}/members-status`
的 `compartment.dir` 取（那是权威路径来源）。

### 2.2 起桥（两种方式，都实测过）

**方式 A（推荐，能长期存活）**：用 `Start-Process` 带重定向
```powershell
Start-Process -FilePath <python.exe> -ArgumentList "-u","bridge.py" `
  -WorkingDirectory <隔间绝对路径> `
  -RedirectStandardOutput <日志> -RedirectStandardError <错误日志> -NoNewWindow -PassThru
```

**方式 B**：WMI 脱离父进程（**命令行要拼对**，我拼错过一次，见 §4 坑）
```powershell
Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
  CommandLine = "cmd /c cd /d `"<隔间绝对路径>`" && `"<python.exe>`" -u bridge.py > `"<日志>`" 2>&1"
  CurrentDirectory = "<隔间绝对路径>"
}
```

### 2.3 桥起来后应当看到（自检输出）
```
[桥] file_poll harness 桥，平台模板生成，回连 http://127.0.0.1:18920
[桥] harness: <hid> | inbox: <…>\inbox | trigger: (空)
[自检] 平台连通: http://127.0.0.1:18920 -> HTTP 200
[自检] inbox 可写: <…>\inbox，delivered/ 已经创建
[投递] task_xxx.json          ← 开始取积压
```
**判据**：`inbox/` 里的任务数变成 0、`inbox/delivered/` 里出现同等数量的文件。

### 2.4 验证"回程"（agent 回话 → 平台）
把回话写成 `inbox/replies/reply_<任意>.json`，内容是**直接 POST 给平台的载荷**：
```json
{ "type": "activation_result",
  "harness_id": "<hid>", "workshop_id": "<ws>", "member_id": "<mid>",
  "status": "entered", "session_id": "<会话id>", "context_turns": 1,
  "source": "bridge" }
```
桥会把它 POST 到 `/api/harness/activation-result`，成功后把文件移进 `replies/sent/`。
**实测输出**：`[回报] POST …/api/harness/activation-result -> ok=True {"success":true,...}`

> 端点选择由载荷里的 **`type`** 决定：`activation_result` → `/api/harness/activation-result`；
> 其余（任务类）→ `/api/harness/task-result`。

---

## 3. 这套东西已经"通用"在哪（对着规则 19 自查）

| 检查项 | 结论 |
|---|---|
桥脚本是谁写的？ | **平台模板自动生成**（`bridge_templates/file_poll/bridge.py.tmpl`）—— 不是私人定制 |
路径写死了吗？ | 桥里的 `INBOX_DIR` 由**平台渲染注入**（按工作间真实路径）；**调用方不应写死**，从 API 取 |
依赖某个特定 agent 吗？ | 不依赖。`TRIGGER_CMD` 为空 = 只落盘不叫醒；填上你 agent 的命令即可 |
示例用真名了吗？ | ⚠️ 桥注释与日志里**没有**真实 agent 名；但**本机存在的 `bridge.py` 里有**（那是平台按注册渲染的，属运行数据，不入库） |

⇒ **结论**：机制通用；**需要做成产品的是"让人知道要跑它、并且一键能跑"**（V37 P1/P3）。

---

## 4. 我踩过的坑（省你时间）

1. **WMI 拼命令行容易错**：我第一版 `cmd /c cd /d "…" && "…python.exe" -u bridge.py > "…log" 2>&1`
   没起来、**连日志文件都没建** ⇒ 改用 `Start-Process -RedirectStandardOutput` **一次就活**。
   排查提示：**日志文件不存在 = 命令根本没跑起来**（不是桥的问题）。
2. **别用 `powershell` 直接看中文日志**：全是乱码。判编码用 Python `decode('utf-8')`（铁律 4）。
3. **桥是常驻进程**：不要挂在"可被取消的后台作业"下（继承书 §6 第 22 条，我因此把服务全杀过一次）。
4. **`delivered/` 会一直涨**：桥只搬不删，久了要清（本次 4 条已清）。
5. **`inbox/replies/failed/` 里的东西 = 平台没收下的回报**：出问题先看这里。
6. ★**同一个邮箱 / 同一个 work-dir 只能有一个桥在跑**（我实测踩到过：调通过程里留下
   **两个** bridge 进程，`pid=3888` 与 `38292`）。两个桥抢同一个 `inbox` ⇒
   **同一批任务会被投递两次**。
   ★**2026-10-12 更新 —— 本条原写「平台不会阻止你起第二个（没有互斥）」，现已不成立**：
   - **桥自己会拦**：`pending_poll_bridge.py` / `filepoll_harness_bridge.py` 已加**单实例锁** ——
     同 work-dir 起第二只会**直接拒绝启动**（退出码 2），打印
     「另一只桥正在使用同一个 work-dir（拒绝重复启动）」。**这是正常保护，不是故障**；
     要重启**先停掉原来那只**再起。
   - **平台也会说**：`POST /api/harness/bridge-test` 回 `bridges_running` >1 时带 `bridge_warning`
     （有重复桥 ⇒ 停掉多余的只留一只）。判定依据是**扫活进程**，不是心跳。
   - 手工核对仍可用：`Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? CommandLine -match 'bridge.py'`，
     多个就只留一个。
   ⚠️坑（写给我自己）：加锁时我第一版**忘了 `f.seek(0)`** —— `open(lock,"a+")` 是追加模式、
   指针在 EOF，而 `msvcrt.locking` 锁的是"从当前指针起的 N 字节" ⇒ 两只桥**锁在不同字节上、毫无冲突**。
   **源码看着全对，实测完全不拦** ⇒ 这类互斥**必须真起两个进程验**，源码断言测不出来。

---

## 5. 诊断清单：唤醒不了时按顺序查

| 次序 | 查什么 | 命令/位置 | 说明什么 |
|---|---|---|---|
1 | 桥在跑吗 | `Get-CimInstance Win32_Process \| ? CommandLine -match 'bridge.py'` | 不在跑 ⇒ **一切都不会发生** |
2 | 邮箱有积压吗 | `members-status` 的 `compartment.mailbox_pending.inbox` | >0 且桥没跑 ⇒ 消息压着没人取 |
3 | 桥日志说了什么 | 你起的日志 / `delivered` 数量 | 有 `[投递]` 说明取走了；没有 ⇒ 桥没工作 |
4 | 回话有没有到平台 | `inbox/replies/failed/` 是否为空 | 有东西 ⇒ 平台拒收了，看内容 |
5 | 会话建了没 | `GET …/member/{mid}/bridge` 的 `session_ref` | 空 ⇒ 平台还没认到这个会话 |

---

*DSH · 2026-10-11 · 本文是可更新的：以后每次"调通"都往这里补一条实测步骤*
