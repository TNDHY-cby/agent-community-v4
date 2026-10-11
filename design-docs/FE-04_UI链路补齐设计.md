# FE-04 设计稿 · UI 链路补齐（入口互达）

> **设计**：XiaomiMiMo（前端）｜ **日期**：2026-10-07 ｜ **状态**：老板当面拍板（"从哪里进入你之前做的前端里，补齐UI链路"），本稿为落档
> **背景**：FE-01 任务书规定"在现有页面加入口链接须写进设计稿、拍板后动手"——本稿即该拍板的落档。

## 目标

五个页面（大厅 `index` / 工作间 `workshop` / 接入 `register` + 新增 `ops` / `handover` / `booths`）**任意一页可达其余页**，链路闭环、无孤岛。

## 改动清单（全部为加链接，不改任何功能逻辑）

| 文件（归属） | 位置 | 增加 |
|---|---|---|
| `index.html`（现页） | topbar「注册 Harness」按钮后 | 三个 `.btn`：📡 看板 / 🧾 回执 / 🏗 隔间（`title` 带全称）；`<title>` 后补一行 favicon 声明 |
| `workshop.html`（现页） | 左侧栏列表下方新增页脚行 | 三个小链接：看板 / 回执 / 隔间；同补 favicon 一行 |
| `register.html`（现页） | 「← 返回大厅」后 | 同排三个小链接（复用 `back` 样式缩距）；同补 favicon 一行 |
| `ops.html`（我方） | topbar | ➕ 🧾 回执台 / 🏗 隔间 / ◈ 大厅（补 `.navlink` 样式） |
| `handover.html`（我方） | topbar | ➕ 🏗 隔间 / ◈ 大厅（已有 →看板） |
| `booths.html`（我方） | topbar | ➕ ◈ 大厅（已有 →看板、→回执） |

> **favicon 说明**：三个现页原本无 `<link rel="icon">`，浏览器自动请求 `/favicon.ico` 得 404（Console 红字）。
> 统一补 `<link rel="icon" href="data:,">`（与新页一致），属零功能改动；改前备份在 `frontend/temp/*.bak_20261007_nav`。

```
        ┌──────── 大厅 index ────────┐
        │  ↕        ↕         ↕      │
   workshop    ops看板    register
        │  ↕        ↕
   回执台 handover ↔ 隔间 booths（互达）
```

## 约束

- 只加 `href`/`onclick=location.href`，不改现有 JS 逻辑、不动样式体系（现页用其既有 class，新页用 `.navlink`）。
- 窄窗口：index 顶栏按钮用短文案（看板/回执/隔间）+ `title` 全称；1024 下估算不溢出，验收实测。
- 验收：五页互链点击全通（HTTP 200、Console 无红字）、三档视口不破版、`git diff` 仅这些行。

## 不做

- 不改导航信息架构（不加汉堡菜单/侧边抽屉）；不动 workshop 主流程区。

## 验收结果（2026-10-07 实测，脚本 `verify/ui_links_check.js`）

| 项 | 结果 |
|---|---|
| 六页互链全通（`a[href]` 与 `location.href` 目标逐个 GET 200） | ✅ `ALL_LINKS_OK: true`（含 ops 时间线 10 条 `workshop.html?ws_id=` 行内跳转） |
| Console 无红字（六页） | ✅ `NO_CONSOLE_ERRORS: true`（补 favicon 后） |
| index 顶栏 1024 不溢出 | ✅ `scrollWidth == clientWidth == 1024` |
| 只加链接不改逻辑 | ✅ diff 仅上述行 + favicon 一行；三现页改前备份在 `frontend/temp/*.bak_20261007_nav` |

---

*（遵守 design-docs 发布口径：无绝对路径、无真实产品名。）*
