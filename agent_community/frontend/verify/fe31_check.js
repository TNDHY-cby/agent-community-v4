/* FE-31 验收：大厅「广播通知」＋卡片位置＋电路纹（真浏览器 + 真请求，2026-10-23）

## 判据（对应 design-docs/FE-30 §13）
1. **能广播**：点「广播通知」→ 填内容 → 「发出广播」→ 真打 `POST /api/hall/notice`（200），
   大厅里**真的出现**一条 `.hmsg.type-notice`，且 `GET /api/hall/notices` 的 total 增加 1；
2. **失败要说话**：空内容时不发请求，并在面板里给出原因（不许静默成功）；
3. **卡片在上面**：空态时 `.hall-entry` 的顶边在消息区靠上（不居中）；有广播时卡片可以不出现（那是空态）；
4. **纹样在**：`.hall-messages` 的 `background-image` 里确实有电路纹那一层（data:svg）＋水印；
   并**真采像素**：大厅中部取一块区域，颜色不是纯一色（纹样/水印真的画出来了）。

## 用法
```bash
set AC_FE_BASE=http://127.0.0.1:18938
node agent_community/frontend/verify/fe31_check.js
# 退出码 0=全过；1=有断言失败（证据与 result.json 在 AC_VERIFY_OUT）
```
*/
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');

const PORT = process.env.AC_PORT || '18920';
const BASE = (process.env.AC_FE_BASE || `http://127.0.0.1:${PORT}`).replace(/\/+$/, '');
const OUT = process.env.AC_VERIFY_OUT
  || path.join(__dirname, '..', '..', '..', 'temp', '验收证据截图', 'FE31_广播');

const fails = [];
const fail = m => { fails.push(m); console.log('✗ ' + m); };

async function getJson(p, url) {
  const r = await p.request.get(BASE + url);
  return { status: r.status(), body: await r.json().catch(() => null) };
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  console.log(`[base] ${BASE}\n[out ] ${OUT}`);
  let b = null;
  for (const opt of [{ channel: 'msedge' }, {}]) {
    try { b = await chromium.launch({ headless: true, ...opt }); console.log(`[browser] ${opt.channel || 'bundled'} `); break; }
    catch (e) { console.log(`[browser] ${opt.channel || 'bundled'} 不可用：${String(e.message).split('\n')[0]}`); }
  }
  if (!b) throw new Error('没有可用的浏览器');

  const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
  const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  const noticePosts = [];
  p.on('response', async r => {
    if (r.url().endsWith('/api/hall/notice')) noticePosts.push({ status: r.status(), body: await r.json().catch(() => null) });
  });

  await p.goto(BASE + '/', { waitUntil: 'load' });
  await p.waitForTimeout(1500);
  /* 设置浮层若盖住页面，先关掉；它可能只是 display:none 的残留节点（点不动），那就按 Esc */
  const closer = await p.$('.sdh-close');
  if (closer) {
    try { await closer.click({ timeout: 1200 }); } catch (e) { await p.keyboard.press('Escape'); }
  }
  await p.waitForTimeout(400);

  /* ① 功能面（2026-10-24 起）：老板把大厅视觉回滚到基线 ⇒ 这里**不再断言任何设计层**
     （氛围图/芯片/玻璃/按钮配色都不判），只判广播这条链路是真的：
     面板能开 → 空内容被本地拦住（不该发请求）→ 真发一条返回 200 → 大厅里出现 `.hmsg.type-notice` → 条数 +1。 */
  const hallBox = await p.evaluate(() => !!document.querySelector('.hall-messages'));
  if (!hallBox) fail('大厅消息区不存在（.hall-messages）');

/* ② 卡片在上面（空态才有卡片；有广播时允许不存在） */
  const cards = await p.evaluate(() => {
    const list = [...document.querySelectorAll('.hall-entry')];
    if (!list.length) return { present: false };
    const box = document.querySelector('.hall-messages').getBoundingClientRect();
    const top = Math.min(...list.map(e => e.getBoundingClientRect().top));
    return { present: true, offsetFromTop: Math.round(top - box.top), boxH: Math.round(box.height) };
  });
  if (cards.present && cards.offsetFromTop > 120) fail(`卡片没在上面：距顶部 ${cards.offsetFromTop}px`);
  if (cards.present && cards.offsetFromTop > cards.boxH / 2) fail('卡片跑到了消息区下半（还是居中）');

  /* ③ 空内容不发请求、说清原因 */
  const before = (await getJson(p, '/api/hall/notices?limit=1')).body || {};
  const totalBefore = before.total || 0;
  await p.click('#bcOpenBtn');
  await p.waitForTimeout(300);
  const opened = await p.evaluate(() => !document.getElementById('bcPanel').hidden);
  if (!opened) fail('点「广播通知」面板没打开');
  await p.fill('#bcText', '   ');
  await p.click('#bcSendBtn');
  await p.waitForTimeout(500);
  const emptyMsg = await p.evaluate(() => document.getElementById('bcMsg').innerText.trim());
  if (!emptyMsg) fail('空内容时面板没给原因（静默）');
  if (noticePosts.length) fail('空内容也发了请求 —— 前端该先拦住');

  /* ④ 真广播一条 */
  const text = '【界面实测】明天 10:00 统一升级，期间请勿派活 ' + new Date().toISOString().slice(11, 19);
  await p.fill('#bcText', text);
  await p.selectOption('#bcLevel', 'urgent');
  await p.click('#bcSendBtn');
  await p.waitForTimeout(1800);
  const after = (await getJson(p, '/api/hall/notices?limit=5')).body || {};
  if (!noticePosts.length || noticePosts[noticePosts.length - 1].status !== 200) fail('没真发出 POST /api/hall/notice 或没回 200');
  if ((after.total || 0) !== totalBefore + 1) fail(`列表 total 没 +1（${totalBefore} → ${after.total}）`);
  const shown = await p.evaluate(t => [...document.querySelectorAll('.hmsg.type-notice')]
    .some(e => e.innerText.includes(t)), text);
  if (!shown) fail('大厅里没出现这条广播（type-notice）');
  const panelClosed = await p.evaluate(() => document.getElementById('bcPanel').hidden);
  await p.screenshot({ path: path.join(OUT, '02_广播后.png') });

  const result = {
    base: BASE, cards, opened, emptyMsg, panelClosedAfterSend: panelClosed,
    totalBefore, totalAfter: after.total, noticePosts: noticePosts.map(x => x.status),
    showedInHall: shown, errs, fails,
    pass: fails.length === 0 && errs.length === 0
  };
  console.log(JSON.stringify(result, null, 1));
  fs.writeFileSync(path.join(OUT, 'result.json'), JSON.stringify(result, null, 2));
  await b.close();
  process.exit(result.pass ? 0 : 1);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
