# V68 · 一个 Hub 服务多家公司：公司维度隔离（M3）（2026-10-20）

> V66/V67 的中转只知道"终端"，不知道"公司"。**一台 Hub 给多家公司用**时，
> 必须保证 A 公司的活不会串到 B 公司（这是共享中转的最低要求）。

## 1. 两种形态（默认安全，不破坏已有用法）

| 形态 | 开关 | 行为 |
|---|---|---|
| **私有 Hub**（默认） | `AC_HUB_SHARED=0` | 只服务一个公司：**所有终端互通**（就是 M1/M2 的样子，公司字段不参与判断） |
| **共享 Hub** | `AC_HUB_SHARED=1` | **按公司隔离**：只能发给自己公司；跨公司需要显式许可 |

**为什么默认是私有**：绝大多数人是自己一台机器跑 Hub 给自己几台设备用；
把它默认设成"隔离"会让 M1/M2 的用法突然不通（那是产品退化）。

## 2. 隔离规则（共享形态）

```
终端注册：{terminal_id, company_id, name}
Hub 记录：terminals[tid] = {company, name, last_seen}

发消息：{from, to, kind, payload, allow_cross?}
  ├─ from/to 都登记过 ⇒ 比 company
  │    ├─ 同公司 ⇒ 放行
  │    ├─ 跨公司 且 AC_HUB_ALLOW_CROSS=1 且 allow_cross=true ⇒ 放行（并记审计）
  │    └─ 跨公司 其它情况 ⇒ **403 + 说清是哪个公司的**
  └─ 任一方未登记 ⇒ 403（不许给"看不见的公司"发；也可能是打错名字）
```

**跨公司**在"合作社"这个语境里是**正常业务**（公司间合作），所以不是永久禁止，而是：
**默认关、要开就显式开、开了还要每条显式带 `allow_cross`** —— 三道闸，避免"手滑发给别家"。

## 3. 端点变化

| 端点 | 变化 |
|---|---|
| `POST /hub/register` | 新增可选 `company_id`（默认 `default`） |
| `POST /hub/send` | 新增可选 `allow_cross`（默认 false）；跨公司被拒时返回 403 + `{"error","from_company","to_company","how_to_fix"}` |
| `GET /hub/terminals` | 每条多 `company` 字段 |
| `GET /hub/health` | 多 `shared` / `allow_cross` 两个字段（一眼看出这是哪种 Hub） |

终端侧：`AC_HUB_COMPANY`（默认空 ⇒ `default`）；发送时不带 `allow_cross`
（跨公司派活要走 `POST /api/relay/dispatch` 的 `allow_cross: true`，由人显式点）。

## 4. 判据（`tools/relay_isolation_check.py`，六格）

| 格 | 判据 |
|---|---|
| ① | 私有形态（`AC_HUB_SHARED=0`）：**跨公司名字也能通**（保证向后兼容，M1/M2 不退化） |
| ② | 共享形态（`AC_HUB_SHARED=1`）：**同公司通** |
| ③ | 共享形态：**跨公司被拒 403**，且响应里有 `from_company`/`to_company`/`how_to_fix` |
| ④ | 共享形态 + `AC_HUB_ALLOW_CROSS=1`：**带 `allow_cross=true` 才通**，不带仍被拒 |
| ⑤ | `GET /hub/terminals` 每题终端都带 `company`（运维能看清谁是谁） |
| ⑥ | 发给**未登记**的终端 ⇒ 403（不许给看不见的公司发） |

## 5. 明确不做

- 不做"公司级配额/计费"（不是这个阶段的事）；
- 不做"跨公司白名单文件"（三道闸已经够；真要细粒度等有人真用再说）；
- 不把公司当成安全边界之外的凭据（**`AC_HUB_TOKEN` 仍是唯一的密钥**；公司隔离是**防手滑**，
  不是"多租户强隔离"。要强隔离就一个公司一台 Hub —— 文档里写清这句，别让人误以为很安全）。
