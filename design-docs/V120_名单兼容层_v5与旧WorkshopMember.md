# V120 · v5 名单与旧 `WorkshopMember` 的**兼容层**（只读部分已实施，替换部分待拍板）

- **日期**：2026-10-30
- **状态**：**只读兼容已实施**（下面 §3）；**对象替换/数据迁移未实施**（§4，等老板拍板）
- **上位口径**：`V116` §2（对象与权威副本）、§21.2（旧模型仍在用）、§0（不变量清单）

## §1 问题（真机实测，不是推测）

```
GET /api/workshop/1bd6e140        → members: []                gate.state = "no_members"
GET /api/workshop/1bd6e140/roster → 1 人，state = "active"
```

⇒ 界面上写着「还不能开工：这个工作间还没有成员」，而它**有一个已经接上的成员**。
这不是显示瑕疵，是**假状态**（违反 `V116` §0-10「不许留看起来成功的状态」，反过来也一样：不许留看起来坏的状态）。

根因：两套对象并存 —— v4 的 `Workshop.members`（内存/DB）与 v5 的 `v5_roster.json`（影子库）。
读取方（闸门、详情页、旧界面）只读前者。

## §2 设计原则

1. **一处真相**：人数与"是否接上"的口径只有一个 —— `v5_roster.effective_members()`；
2. **只加不改**：旧 `members` 字段形状**不动**（老逻辑还在读它），新增字段并存；
3. **失败要说话**：名单模块取不到 ⇒ 降级为 `legacy`/`empty` 并**每进程喊一次**，不许 500、不许静默；
4. **不做对象替换**（那是破坏性迁移，必须老板拍板 + 迁移判据）。

## §3 已实施（只读兼容）

| 位置 | 变化 |
|---|---|
| `platform/v5_roster.py` | 新增 `effective_members(workshop_id)` → `{source: v5/legacy/empty, total, active, members[]}`（**不含凭据**） |
| `platform/workshop_gate.py` | 成员数与"是否接上"改用该口径；`info()` 增 `members_source`；名单模块异常**降级不 500**（`_warn_once` 喊一次） |
| `GET /api/workshop/{id}` | 增 `members_v5` / `roster_counts` / `members_source`（**只加字段**，旧 `members` 形状不变） |

**判据**（`tests/test_v5_roster.py`，4 条）：有 v5 工位 ⇒ 闸门**不再说没成员**（`no_verified_member`，人数 1、口径 v5）；
接上后 `ready`；详情带 `members_v5` 且旧 `members` 仍是 list、摘要无凭据；名单模块坏掉 ⇒ 闸门不 500、如实降级。

## §4 待拍板（破坏性，别自己动）

1. **是否把 v5 名单**提升为唯一真源并**废弃** `Workshop.members`？
   - 要动 DB 列、旧界面读取、`/api/workshop` 列表、`members` 相关端点（一整套）；
   - 必须先立判据：迁移前后**同一份数据逐条可核**（`v5_roster` vs 旧 members 的对应表），迁移失败可回滚；
2. **旧 `members` 写入口**（点「加成员」）要不要**直接写 v5 名单**？
   - 现状：仍写旧模型 ⇒ 界面上加了成员，v5 侧看不见（与上面那个假状态互为镜像的坑）；
3. 迁移期间**双写还是只读兼容**？建议只读兼容 + 旧写入**同步**到 v5（单向），不做双写。

## §5 给界面的口径（现在就能用）

- 显示人数/接上状态：读 `GET /api/workshop/{id}` 的 `members_v5` + `roster_counts`，
  **同时**保留旧 `members`（老字段），并在人数旁标注来源（`members_source`），例如
  「名单 1 人（接上 1）· 来源 v5」；
- 不要自己按旧 `members` 判断"能不能开工" —— 闸门已按 v5 口径算，读 `gate` 就行。
