# FE-16 设计稿 · 项目模型「一键生成草案」（AI 草案入口）

> **设计**：XiaomiMiMo（前端）｜ **日期**：2026-10-10 ｜ **状态**：实施中
> **出处**：DSH 81 号 §四 / 82 号 §四 / 84 号 §三（「可选、不催」；后端已上线并经 DSH 真 AI 实测）。
> **契约来源**：`POST /api/workshop/{ws}/library/model/draft`（`workshops_lifecycle.py:708`，V29 §2 路径 B；
> 后端测试 `agent_community/tests/test_model_draft.py` 6 条）。
> **改动文件**：仅 `agent_community/frontend/booths.html` + `verify/fe16_check.js`。**不改任何 `.py`**。

---

## 1. 契约（后端原语义，界面照此呈现）

| 情形 | 响应 | 界面义务（准则⑤） |
|---|---|---|
| 未配 AI | **400** `{error:"未配置 AI：无法生成草案", hint:"手工填五块即可…", sections:[5标题]}` | 红条显示 `error` + `hint` **原文**；表单**原样不动**；**绝不**显示"已生成" |
| 成功 | 200 `{success:true, draft:{goal,parts,roles,deps,outputs}, filled:[…], missing:[…], verified:false, note:"这是**草案**…请修改后点「保存」…"}` | 五块表单**填入草案**；编辑器顶部挂「**AI 草案 · 待确认（未落盘）**」横幅；黄条显示 filled/missing + `note` 要点；**仍未落盘**（`model.md` 不许因此出现） |
| AI 调用失败 / 返回非 JSON | 200 `{success:false, error:"…", raw?}` | 红条显示 `error` **原文**（不装成功） |
| 工作间不存在 | 404 `{error}` | 红条原文 |

**不变量**：本按钮**只发 draft 这一条请求**；写入仍只经「保存」→ `POST …/library/update`
（保存成功后草案横幅消失 = 人已确认）。`GET …/library` 在草案前后应一致（`model.exists` 不变）。

## 2. 落位（booths.html · 模型编辑器 `.m-edit` 内）

| 项 | 位置 |
|---|---|
| 按钮 | `.m-btns` 行内、「保存」**左侧**：`「✨ 一键生成草案」`（`.m-draft`）；**仅五块表单模式**显示（整篇模式切回表单即可用） |
| 草案横幅 | 编辑器顶部 `.m-draftbar`（黄）：「AI 草案 · 待确认（未落盘）——修改后点「保存」才写入」；`modelEdit.draftBar` 置位，**保存成功/重新打开编辑器时清掉** |
| 状态 | `modelEdit.draftBar`（bool，随 `modelEdit` 存模块级，30s tick 重建 DOM 不丢） |
| 请求中 | 按钮禁用（「生成中…」） |

**不做**：不做缓存、不做多轮草稿、不改整篇模式（草案只填五块）、不碰 `framework`。

## 3. 验收（`verify/fe16_check.js`，隔离实例 + `AC_FE_BASE` + 自建自清 + `AC_VERIFY_OUT`）

1. 打开模型编辑器 → 「一键生成草案」按钮可见、仅表单模式显示；
2. **真打 400**（隔离实例未配 AI）：红条含「未配置 AI」+ `hint` 原文；表单值**未变**；
   `GET …/library` 复核 `model.exists=false` ⇒ **草案没落盘**（反向断言）；
3. **成功路径用 `page.route` 打桩**（不花真 AI 钱）：200 假草案 → 五块被填、横幅出现、黄条含 filled/missing；
   随后点「保存」（真保存）→ 横幅消失、`model.exists=true`；
4. **写白名单** = `library/update` ∪ `library/model/draft`；Console 零红错；1920/1280/1024 不破版；
5. 截图进 `temp\验收证据截图\`、`result.json` 脱敏；支持 `AC_VERIFY_OUT`（83 号）。

> 真 AI 路径（配了 provider 时）本机不测（会产生计费调用）—— 后端语义已由 DSH 在 18932 实测（81 号 §四）。

---

*（遵守 design-docs 发布口径：无本机绝对路径、无真实产品名。）*
