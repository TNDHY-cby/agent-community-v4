# V44 五 harness 真机验收复盘与 7 个缺陷（设计稿 · 待老板拍板）

> **作者**：示例Harness-E ｜ **日期**：2026-10-13 ｜ **状态**：**待拍板**（铁律 1：未拍板不写码）
> **来源**：老板 2026-10-13 晚提供的 **5 份真机接入回执**
> （原件已归档到知识库《项目流程详情》的 `真机接入回执_20261013/`；
> 为便于对照本文，回执里的 harness 在本稿中按 **E1 / C1 / A1 / B1 / G1** 指代）。
> **与既有稿的关系**：本稿是 `V43`（接入自检清单）的**续篇**——V43 是"我自己按清单自查"，
> 本稿是"**五个真外部 agent 各走一遍**"的实测复盘；缺陷编号接 V43 的 #9 之后。

---

## 1. 五份回执的交叉核对（先看事实）

| harness | 声明的 `wakeup_method` | 实测自测档位 | 第 5 步用的桥型 | 第 4 步自检 | 第 5 步 |
|---|---|---|---|---|---|
| `E1` | `acp` | A 档 | `cli_acp` | 🟢（**第 3 次才绿**：先要手动把常驻桥跑起来） | 🟢 桥领件并回报 3 次 |
| `C1` | `clipboard`（人工中转） | D 档 | `pending_poll` | 🟢 | 🟢 桥常驻（pid 40452） |
| `A1` | `clipboard` | D 档（自测写成 E→http，平台按最安全落 clipboard） | `pending_poll` | 🟢 | 🟢 桥常驻、回报 `ok=True` |
| `B1` | `http_api` | C 档 | **`pending_poll`（验证时）→ `file_poll`（实际在跑）** | 🟢（验的是 `pending_poll` 桥） | 🟢 桥 PID 5524 |
| `G1` | `clipboard` | D 档 | `pending_poll` | 🟢 | 🟢 pid 38088/43300（父子链=1 只） |

**三处口径要钉死（否则下一步又会误读）**：
1. **四档 / 桥型 / `wakeup_method` 是三个不同的词**（总图 §8 已钉过）——五份回执里"档位"都写对了，但
   `wakeup_method=http_api` 的 harness **不需要桥**（平台直推 `/message`），B1 却仍走完了桥的 3/4/5 步；
2. **第 4 步绿 ≠ 全链路通**——五份回执**全部**自己写明了 `verified_scope=仅桥配置`，
   这一条是**产品已经做对的地方**（诚实口径生效），要保留；
3. **"桥是平台在验证间里启动的"**——所以第 4 步验的是"平台↔那只桥"，**不是** "harness 侧会不会跑桥、agent 会不会回话"。

## 2. 七个缺陷（按"是否阻断接入"排序）

### #10 【阻断 · A 档 harness 永远过不了第 4 步】验证间桥脚本键 `acp` ≠ 模板 kind `cli_acp`
〔源码〕`verify_sandbox.py:208-212`
```python
BRIDGE_SCRIPTS = {"file_poll": "filepoll_harness_bridge.py",
                  "pending_poll": "pending_poll_bridge.py",
                  "acp": "acp_harness_bridge.py"}       # ← 键是 "acp"
```
而模板 kind 的合法取值是 `KINDS`（`bridge_templates.py:99`）里的 **`cli_acp`**；
`verify_sandbox.py:483` 的判据是 `elif kind in BRIDGE_SCRIPTS and BRIDGE_SCRIPTS.get(kind)`，
拿到的 `kind` 是 `cli_acp` ⇒ **永远不命中** ⇒ 第 4 步必然报
「桥型「cli_acp」在验证间里不支持自动起桥（可在真实工作间里验）」+ `bridge_started=false`。
**E1 那份回执就是这个现象的实证**（第 2 次自检 `not_picked`，靠"先把第 5 步的桥手动跑起来"才变绿）。
**修法（小）**：键改成 `cli_acp`（并把 `bridge_argv` 里 `elif kind == "acp"` 一并对齐）；
或两处统一成同一常量表，禁止再各写各的（规则 20：一处真相）。

### #11 【误导 · 可能挑到没验证的草稿】`default_one` 取"列表第一条"，不分草稿/已验证
〔源码〕`bridge_templates.py:202-210`：`return items[0] if items else None`——**只取第一条**。
文档说"① 唯一的模板 → 用它"，但实现没有"唯一"这个前提，也没有"优先已验证"。
⇒ 一个 harness 同时有 `source=draft, verified=False` 的草稿和 `verified=True` 的具体模板时，
**只要草稿排在前面，省略 `template` 的 `bridge/generate` 就会用草稿**，而回执只说 `template_source`，
用户多半不会去核。**修法**：选序改为"① `verified=True` 的（多条则最近 `verified_at` 优先）→
② 唯一模板 → ③ 第一条"，并在响应里明写"这条是草稿/未验证"。

### #12 【有坑 · 覆盖真注册】`examples/acp_harness_bridge.py` 启动即 `register()` 写虚拟示例值
〔回执 · E1.txt 坑②①〕启动就用 `ai.model=your-model`、`acp_command=your-acp-command --flag value`
重新注册，**把用户刚注册好的真实值覆盖掉**（E1 的注册真的被覆盖过，靠重新 register 救回）。
**修法**：桥只**读**注册信息，不写；确需写时只补空字段，**绝不用示例值覆盖非空字段**。

### #13 【阻断 · 起不了 agent】`AcpHarnessBridge()` 无参构造只读 `AC_ACP_COMMAND`
〔回执 · E1.txt 坑②②〕全机没人设这个环境变量 ⇒ 必抛「未配置 ACP 启动命令」。
模板侧同一处已修（`bridge.py.tmpl` 改为把注入的 `ACP_COMMAND`/`ACP_CWD` 用 `shlex.split` 传进去，已随 2026-10-13 晚发布）；
**examples 侧这只没修**——而它正是 auto-connect / 验证间会用的那只。

### #14 【阻断 · 回报 401】同一只桥 `post_json` 不带 `X-Agent-Token`
〔回执 · E1.txt 坑②③〕今天起 `message` / `task-result` **强制校验**该头 ⇒ 桥能领件、回报被拒。
模板侧已注入 token（V43 那轮修的），**examples 侧没跟上**（同 #13 的同一只文件）。

> #12/#13/#14 是**同一只文件**的三个毛病，建议**一次改完**（规则 20：别只改一处）。

### #15 【产品缺口 · 验的不是要用的那条】桥型与 `wakeup_method` 不匹配且无人拦
〔回执 · B1.txt〕`wakeup_method=http_api`（**C 档，平台直推，根本不需要桥**）却走完 3/4/5 步：
第 4 步验的是 `pending_poll` 桥，第 5 步实际在跑的是 `file_poll` 桥 ⇒ **"已验证模板"和"在用的桥"不是同一条**。
**这是"verified 被读成整条链路通"的又一种形态**：这次连"验的桥型"都和"跑的桥型"不一致。
**修法（要设计）**：① `http_api` 型 harness 在界面上**跳过桥的 3/4/5 步**，只留"平台直推地址"自检；
② 省略 `template` 时，若选出的模板 `kind` 与注册声明的 `wakeup_method` 不匹配 ⇒ **响应里给警告**
（不禁止，但要说话，准则⑤）。

### #16 【流程 · 老进程 + 新模板】`AGENT_TOKEN` 那批修复提交后没重启就自检
〔回执 · E1.txt 坑①〕18920 是 09:07 起的、修复 09:20 才提交 ⇒ verify 一上来 attach 失败。
**这不是代码缺陷，是纪律缺陷**（铁律 3 已有，但**平台侧没有任何提醒**）。
**修法（可选）**：`/api/status` 里带上"进程启动时间 vs `agent_community` 下最新 .py mtime"，
界面在"启动时间早于代码"时显示一条 ⚠️（`verify_status.ps1` 的 5a 段已有同款判据，可复用）。

## 3. 建议的落地顺序（等拍板）

| 批次 | 内容 | 量级 | 风险 |
|---|---|---|---|
| **批 1（建议先做）** | #10 键名对齐、#11 选序改"优先已验证"、#12/#13/#14 **examples 那只桥一次改完** | 小（约 3 处函数 + 1 个文件） | 低；均有测试可加（#10 加"cli_acp 在验证间能起桥"断言） |
| **批 2（要设计）** | #15 `http_api` 跳过桥步骤 + 桥型/叫醒方式不匹配告警 | 中（端点 + 界面文案 + 契约） | 中；**规则 20**：界面话术、自检清单、文档要一起改 |
| **批 3（可选）** | #16 启动时间 vs 代码 mtime 的界面提醒 | 小 | 低 |

**验收判据（照规则 19）**：改完后，**再拉一个此前没接过的 agent**（换个目录、换个完全不同的名字、不改一行代码）
从第 1 步走到第 5 步全绿；其中 A 档 agent 必须能在**不手动预先跑桥**的情况下通过第 4 步。

## 4. 明确不动的东西（避免误伤）

- 第 4 步 `verified_scope=仅桥配置` 的**诚实口径**：**保留**（五份回执都证明它写对了）；
- `clipboard`（D 档）"没有叫醒命令、`mode:manual` 属正常"的语义：**保留**（C1/A1/CodeArts 三份都对）；
- `pending_poll` 作为"无任何证据时的最安全兜底桥型"：**保留**（但见 #15 的告警）。

---
*示例Harness-E · 2026-10-13 · 依据：5 份真机回执 + `verify_sandbox.py` / `bridge_templates.py` / `examples/acp_harness_bridge.py` 源码核对*
