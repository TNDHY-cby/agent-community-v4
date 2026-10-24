# 设计稿 V91 · 浏览器级投递（CDP）：不用真鼠标，把文字塞进 Electron 的聊天框

> 状态：**已实施（2026-10-23）**。老板原话：「**不能是真鼠标**」＋参考了 CDP 三档方案的取舍。
> 上游：`V89`（监控室连续流）、`V90`（状态持久化抽层）；本次替代"模拟鼠标键盘"那条路。

## 0. 一句话

往 Electron/Chromium 应用的输入框里塞文本，**走 CDP（调试协议）而不是物理鼠标键盘**：
不抢焦点、不点错位置、能读回核对，多行文本也不会触发误发送。

## 1. 为什么放弃"模拟鼠标键盘"（我自己刚做过、又被否掉的那条）

| | 模拟鼠标键盘（`SendInput`） | **CDP 浏览器级** |
|---|---|---|
| 占用用户的鼠标/键盘 | **要**（会抢前台、点错会点到别处） | **不要**（进程内派发事件） |
| 窗口不在前台 | 落空，或打到前台那个程序里 | **照样能投** |
| 能否核实 | 只有原生控件能读回；Electron 一律"未核实" | **能**：直接读 `innerText`/`value` |
| 多行文本 | `Enter` 会触发发送 | 一次性 insertText/paste，**不触发发送** |
| 换显示器/缩放 | 坐标要 DPI 换算，易偏 | 无关 |

实测证据（2026-10-23，Trae/Electron）：窗口置顶=True、剪贴板写入经核对、Ctrl+V 已按，
**输入框仍然空** —— 因为内部焦点不在输入框，而平台拿不到它的内部元素。模拟鼠标只能"猜位置"，不可靠。

## 2. 三档（由稳到"最像人"，自动降级 + 每档读回核对）

| 档 | 做法 | 碰剪贴板 | 换行 | 适用 |
|---|---|---|---|---|
| `insert` | JS `el.focus()` + `Input.insertText`（分块 2000 字） | 否 | 可能被组件吞 | 默认首选 |
| `paste-event` | JS 构造 `ClipboardEvent('paste')` + `DataTransfer` | 否 | **保留** | 监听 paste 的编辑器（ProseMirror/CodeMirror/Slate/VS Code 系） |
| `clipboard` | 写系统剪贴板（**用完还原**）+ `Input.dispatchKeyEvent` Ctrl+V | 是（约 0.5s） | 完整保留 | 最像真人；前两档不通时用 |

`isTrusted=false` 是第二档唯一的风险：编辑器若校验事件来源就无效 ⇒ 自动落到第三档。

## 3. 契约

```
GET  /api/docking/web/probe                       → {ok, ports:[{port, targets:[{id,title,url,ws}]}], why}
POST /api/docking/web/editors   {ws}              → {ok, editors:[{idx,tag,editable,placeholder,maxlength,cls,w,h}]}
POST /api/docking/web-send      {ws, text, idx, mode, enter, harness_id?}
                                                  → {ok, mode, verified, tiers:[…], readback, detail, next_step}
```

- 端口：`AC_CDP_PORT`（可逗号分隔，默认 `9222,9223,9224,9225`）；**目标应用必须自己开**
  `--remote-debugging-port=…`，没开就**如实说怎么开**（不假装能投）；
- `enter=false` 默认**不发**回车：有些聊天框 Enter 就是发送，发不发由用户决定；
- 记住选择：`input_anchor.set_web()`（与"表层交互的位置"**分开存**，两条路各记各的）。

## 4. 判据（真机，`tools/browser_input_check.py`）

自己拉起一个 **headless Chromium**（Edge/Chrome），页面里放 `textarea` + 一个**像真实聊天框那样监听
`paste`** 的 `contenteditable`，逐档投递并读回：

| # | 判据 | 实测 |
|---|---|---|
| ① | 探测到端口 + 页面 target | OK |
| ② | 扫到 textarea 与 contenteditable | OK（2 个） |
| ③ | `insert` 档：单行投进去 + 读回一致 | OK |
| ④ | `paste-event` 档：多行**保留换行** + 读回一致 | OK（`第一行\n第二行\n第三行…`） |
| ⑤ | `clipboard` 档：投递 + 读回一致 | OK |
| ⑥ | 投不进去时**如实失败**（idx 越界 ⇒ 明确指出） | OK |

## 5. 边界（写在返回里，不粉饰）

- 目标没开调试端口 ⇒ 不可能投；这是**用户侧一次性设置**，平台只负责说清怎么做；
- 页面里若有多个输入框（搜索框/编辑器/聊天框），**要用户指认 idx**：平台按 `placeholder/cls/尺寸` 列出来，
  不替用户猜（猜错会把话打进搜索框）；
- 读回不一致一律判失败，绝不"报成功"。二期：`Runtime.addBinding` 做回执、按 elementHandle 精确定位。

## 6. 实测边界：有的发行版**明确拒绝**调试端口开关（2026-10-23）

平台侧链路已用真 Chromium 验过（probe → editors → web-send，`state=delivered_verified`，读回与原文一致）。
但**不是所有 Electron 发行版都允许开端口**：实测某个中文发行版把开关列进了白名单，四种写法全部被拒：

```
--remote-debugging-port=9222      → bad option: --remote-debugging-port=9222
--remote-debugging-port 9222      → bad option: --remote-debugging-port
--remote-debugging-port=0         → bad option: --remote-debugging-port=0
--no-sandbox --remote-debugging-port=9222 → bad option: --no-sandbox
（连 --force-renderer-accessibility 也是 bad option）
-- --remote-debugging-port=9222   → 被当成脚本路径，node 加载失败
```

⇒ 结论（写进产品边界，不粉饰）：

- **CDP 是否可用，取决于目标应用允不允许**；平台只能在"允许"时全自动 + 可核实；
- 被拒时**不要**变着法子绕（改配置文件/注入 DLL 之类 = 私人定制 + 越界），直接落到两条正路：
  1. **手动绑定目标 + 剪贴板半自动**：`state=queued_for_human`，界面明说"内容已在剪贴板，点一下输入框按 Ctrl+V"；
  2. **走它自己的桥**（文件轮询 / HTTP 回调 / pending 队列）＋ 让 agent **回执**证明它醒了 —— 这才是平台的正常唤醒闭环；
- `probe` 的 `why` 文案要能直接告诉用户"去加参数重启"，并在被拒时**不假装支持**（`ok=false`）。

判据：`tests/test_cdp_input.py`（端口来自 `AC_CDP_PORT`；无端口时 `probe` 如实失败并给做法）；
真机 `tools/browser_input_check.py`（自拉 headless Chromium 三档全绿）。

## 7. 界面改版后还能找到输入框吗（2026-10-23 老板问）

先给结论，再说依据：

| 变化 | 教过的相对位置还准吗 | 为什么 |
|---|---|---|
| 窗口拖动 / 换显示器 | **准** | 存的是窗口内**相对比例**（0~1），不是屏幕坐标 |
| 窗口缩放 / 最大化 | **准** | 比例不变（内容区缩放后比例仍然一致） |
| 改系统缩放（125%→150%） | **准** | 脚本自己声明 DPI 感知，坐标帧与鼠标一致 |
| **应用界面改版**（侧栏变宽、聊天面板挪位、面板折叠） | **会失准** | 相对比例是按旧版界面教的 |
| 换了一台不同分辨率的机器 | 大体准 | 比例仍对齐内容区 |

⇒ 所以平台给了**两条**兜底，各管一段：

1. **CDP 可用时：自动校准（自愈）**
   投递成功后，平台直接问页面"那个输入框现在在哪"（`element_box`），把相对位置刷新掉，
   并记 `source=auto-from-cdp` —— 界面怎么改，只要还能投一次，位置就自动跟上。
   校准失败**不影响**投递结果，但要留一句日志（`[delivery] 位置自动校准跳过：…`）。
2. **CDP 不可用时（例如应用拒绝开端口）：只能人再看一眼**
   - 脚本点之前会**自证**落点是不是目标窗口（`WindowFromPoint`），不是就不点、如实退出 5；
   - 投完只能报"未核实"（读不回），这是 Electron 的客观限制，平台不假装成功；
   - 教位置时**存窗口尺寸 + 最近 3 次历史**，改版后可以换回上一条或重新教一次。

判据：`tests/test_anchor_calibration.py`（3 条：历史/自动校准/校准失败不影响投递）、
`tools/surface_input_check.py`（真机六格）、`tools/visual_script_check.py`（真机四格）。
