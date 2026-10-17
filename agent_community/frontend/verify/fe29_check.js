/* FE-29 验收：瓦片顺序/等块 + 弹窗开合 + 机甲点缀存在性（**常驻判据**，2026-10-17 从 MiMo 的 temp 脚本纳进发布面）

## 为什么有这个文件

MiMo 在通道 135 号说："请把 FE-29 那两支纳进发布面作**常驻判据**"——它原来放在自己 clone 的 `temp/_fe29_check.js`
（= 只在他那台机器上）。按「不许只在我这台上成立」，纳进 `agent_community/frontend/verify/`。

## 用法（需要 node + playwright-core；**不属 CI**，属"发布前/改动后人工跑"）

```bash
export AC_FE_BASE="http://127.0.0.1:18920"   # 惯例：指向你要验的实例（隔离实例更好）
node agent_community/frontend/verify/fe29_check.js
# 退出码 0 = 全过；1 = 有断言失败（result.json 里有明细）
```

## 比原脚本收严的三处（原脚本"只在我的机器上成立"）

1. **不写死端口**：`AC_FE_BASE` 优先；否则按 `AC_PORT`（默认 18920）拼 —— 换端口跑不用改代码；
2. **浏览器可降级**：先试 `msedge` 通道，没有就退回 playwright 自带 chromium，并**打印用的是哪个**；
3. **证据目录可覆盖**：`AC_VERIFY_OUT` 优先，默认 `temp/验收证据截图/FE29_弹窗`（`temp/` 已被 .gitignore 排除，不入库）。
*/
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');

const PORT = process.env.AC_PORT || '18920';
const BASE = (process.env.AC_FE_BASE || `http://127.0.0.1:${PORT}`).replace(/\/+$/, '');
const OUT = process.env.AC_VERIFY_OUT
  || path.join(__dirname, '..', '..', '..', 'temp', '验收证据截图', 'FE29_弹窗');

async function launch() {
  for (const opt of [{ channel: 'msedge' }, {}]) {
    try {
      const b = await chromium.launch({ headless: true, ...opt });
      console.log(`[browser] ${opt.channel || 'bundled-chromium'}`);
      return b;
    } catch (e) {
      console.log(`[browser] ${opt.channel || 'bundled-chromium'} 不可用：${String(e.message).split('\n')[0]}`);
    }
  }
  throw new Error('没有可用的浏览器（装 playwright-core 的浏览器，或用 msedge）');
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  console.log(`[base] ${BASE}\n[out ] ${OUT}`);
  const b = await launch();
  const p = await b.newPage({ viewport: { width: 1600, height: 1000 } });
  const errors = [];
  p.on('pageerror', e => errors.push(e.message));
  await p.goto(BASE + '/docking.html', { waitUntil: 'domcontentloaded' });
  await p.waitForSelector('.dock-tile');
  await p.waitForTimeout(1000);
  const order = await p.evaluate(() => [...document.querySelectorAll('.dock-tile')].map(t => t.dataset.dockService));
  const homeHidden = await p.evaluate(() => {
    const h = document.getElementById('dockModalHome');
    return h && h.hidden && !h.contains(document.querySelector('.dock-modal-overlay'));
  });
  /* 页面不应再有可见的长文功能区 */
  const sectionsVisible = await p.evaluate(() => {
    return ['dockRegisterWrap', 'dockScriptWrap', 'dockArchiveWrap', 'dockActivateWrap', 'dockAskWrap']
      .map(id => { const el = document.getElementById(id); return { id, inHome: !!(el && el.closest('#dockModalHome')) }; });
  });
  await p.screenshot({ path: path.join(OUT, '01_首屏.png') });
  /* 点登记 → 弹窗 */
  await p.click('.dock-tile[data-dock-service="register"]');
  await p.waitForTimeout(1200);
  const modalOpen = await p.evaluate(() => ({
    show: document.getElementById('dockModalOverlay').classList.contains('show'),
    title: document.getElementById('dockModalTitle').textContent,
    hasPrompt: !!document.getElementById('regPromptPre'),
    bodyScrollLocked: document.body.style.overflow === 'hidden'
  }));
  await p.screenshot({ path: path.join(OUT, '02_登记弹窗.png') });
  /* Esc 关闭 */
  await p.keyboard.press('Escape');
  await p.waitForTimeout(400);
  const closed = await p.evaluate(() => ({
    show: document.getElementById('dockModalOverlay').classList.contains('show'),
    backHome: !!document.getElementById('dockRegisterWrap').closest('#dockModalHome'),
    overflow: document.body.style.overflow
  }));
  /* 机甲点缀：hot 铭牌 + UNIT 编码 + 角标 + 危险条纹 */
  await p.click('.dock-tile[data-dock-service="script_activate"]');
  await p.waitForTimeout(800);
  const mecha = await p.evaluate(() => {
    const hot = document.querySelector('.dock-tile.hot');
    const tile = document.querySelector('.dock-tile');
    const core = hot ? getComputedStyle(hot, ':before').content : '';
    const unit = tile ? getComputedStyle(tile, ':after').content : '';
    const head = document.querySelector('.dock-modal-head');
    const hazard = head ? getComputedStyle(head, ':before').backgroundImage : '';
    return {
      core, unit, hazardHas45: /45deg/.test(hazard),
      hotBorder: hot ? getComputedStyle(hot).borderLeftColor : ''
    };
  });
  await p.screenshot({ path: path.join(OUT, '03_脚本激活弹窗.png') });
  const expectOrder = ['register', 'script_activate', 'archive', 'session_bridge',
    'onboard_recover', 'channel', 'bridge_helper', 'ask_frontdesk'];
  const out = {
    base: BASE,
    orderOk: JSON.stringify(order) === JSON.stringify(expectOrder), order,
    homeHidden, sectionsVisible, modalOpen, closed, mecha, errors,
    pass: JSON.stringify(order) === JSON.stringify(expectOrder) && homeHidden &&
      sectionsVisible.every(s => s.inHome) && modalOpen.show && modalOpen.hasPrompt &&
      !closed.show && closed.backHome && mecha.hazardHas45 && !errors.length
  };
  console.log(JSON.stringify(out, null, 2));
  fs.writeFileSync(path.join(OUT, 'result.json'), JSON.stringify(out, null, 2));
  await b.close();
  process.exit(out.pass ? 0 : 1);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
