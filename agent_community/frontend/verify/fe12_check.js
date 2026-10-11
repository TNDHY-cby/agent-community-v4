/* FE-12 通道界面验收（隔离实例 + AC_FE_BASE + 自建自清；三条反向断言） */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');
const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, 'FE12_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', 'FE12_验证');
      fs.mkdirSync(o, { recursive: true });
      return o;
    }
    const p = path.dirname(d);
    if (p === d) break;
    d = p;
  }
  return RESULT_DIR;
}
const OUT = process.env.AC_VERIFY_OUT || _resolveOut();
fs.mkdirSync(RESULT_DIR, { recursive: true });
function sanitize(o){
  const drive = /[A-Za-z]:[\\][^\n\r]*/g;
  const walk = v => {
    if (typeof v === 'string') return v.replace(drive, '<本机路径>')
      .replace(/harness-小白龙/g, '示例Harness-A').replace(/harness-测试甲/g, '示例Harness-B')
      .replace(/harness-测试乙/g, '示例Harness-C').replace(/harness-测试丙/g, '示例Harness-D')
      .replace(/harness-dsh-web-[A-Za-z0-9.\-]+/g, '示例Harness-E');
    if (Array.isArray(v)) return v.map(walk);
    if (v && typeof v === 'object') { const r = {}; for (const k in v) r[k] = walk(v[k]); return r; }
    return v;
  };
  return walk(o);
}
const BASE = process.env.AC_FE_BASE || 'http://127.0.0.1:18920';
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const errors = [];
  const nonGet = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('request', r => {
    if (r.url().includes('/api/') && r.method() !== 'GET') nonGet.push(r.method() + ' ' + r.url().replace(BASE, '').split('?')[0]);
  });
  const step = (n, d) => console.log('>> ' + n, JSON.stringify(d));

  /* 0. 自建 */
  const ws = await (async () => {
    const r = await fetch(BASE + '/api/workshop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'FE12验收-自建' }) });
    return (await r.json()).workshop_id;
  })();
  await fetch(BASE + `/api/workshop/${ws}/members`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ members: [{ role: '码农', harness_ids: ['harness-fe12-fake'], display_name: 'harness-fe12-fake' }] }) });
  step('自建', { ws });
  if (!ws) process.exit(1);

  const url = `${BASE}/workshop.html?ws_id=${ws}`;
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 25000 });
  await page.waitForSelector('.sf-chrow', { timeout: 45000 }).catch(() => {});
  await sleep(800);

  /* 1. 初始（pending 或 信息未取到 均可，记录真实） */
  const init = await page.evaluate(() => {
    const row = document.querySelector('.sf-chrow');
    return row ? {
      badge: (row.querySelector('.ch-badge') || {}).textContent,
      text: row.innerText.replace(/\n/g, ' | ').slice(0, 160),
      hasEstablishBtn: [...row.querySelectorAll('button')].some(b => /新建通道/.test(b.textContent)),
      hasRetireBtn: [...row.querySelectorAll('button')].some(b => /停用通道/.test(b.textContent))
    } : { missing: true };
  });
  step('初始通道行', init);
  await page.screenshot({ path: path.join(OUT, '01_初始.png') });

  /* 2. 真实新建通道（名字留空=平台生成） */
  await page.click('.sf-chrow .ch-acts .btn');
  await sleep(2200);
  const afterNew = await page.evaluate(() => {
    const row = document.querySelector('.sf-chrow');
    const note = row ? row.querySelector('.ch-note') : null;
    return {
      badge: (row.querySelector('.ch-badge') || {}).textContent,
      name: (row.querySelector('.ch-name') || {}).textContent,
      meta: (row.querySelector('.ch-meta') || {}).textContent,
      note: note ? note.innerText.replace(/\n/g, ' ') : null,
      noteCls: note ? note.className : null,
      hasRetireBtn: [...row.querySelectorAll('button')].some(b => /停用通道/.test(b.textContent)),
      driveInRow: row ? /[A-Za-z]:\\/.test(row.innerText) : false
    };
  });
  step('新建通道后', afterNew);
  await page.screenshot({ path: path.join(OUT, '02_运行中.png') });

  /* 3. 真实停用（acModal 确认） */
  await page.click('.sf-chrow .ch-acts .btn'); /* 停用通道 → 弹确认 */
  await sleep(500);
  await page.evaluate(() => {
    const foot = document.querySelector('.ac-modal .acm-foot');
    if (foot) { const b = foot.querySelector('.danger') || foot.querySelector('button'); if (b) b.click(); }
  });
  await sleep(2200);
  const afterRetire = await page.evaluate(() => {
    const row = document.querySelector('.sf-chrow');
    const note = row ? row.querySelector('.ch-note') : null;
    return {
      badge: (row.querySelector('.ch-badge') || {}).textContent,
      retiredClass: row ? row.classList.contains('retired') : null,
      meta: (row.querySelector('.ch-meta') || {}).textContent,
      note: note ? note.innerText.replace(/\n/g, ' ') : null,
      noteHas停用成功: note ? /已停用。/.test(note.innerText) : false,
      noteHasNone伪装: note ? /已停用/.test(note.innerText) && /没有可停用/.test(note.innerText) : false,
      hasEstablishBtn: [...row.querySelectorAll('button')].some(b => /新建通道/.test(b.textContent))
    };
  });
  step('停用后', afterRetire);
  /* 历史展开 */
  await page.click('.sf-chrow .ch-acts .btn'); /* 新建通道旁没有历史——停用后 history button? 我们把历史按钮放 active 行; retired 行无按钮 → skip */
  await sleep(300);
  await page.screenshot({ path: path.join(OUT, '03_已停用.png') });

  /* 4. 罐头断言：none / active拒绝 / 路径不外显已验 */
  await page.route(`**/api/workshop/${ws}/member/*/channel/retire`, route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ success: true, action: 'none', channel_state: 'retired', member_id: 'm0' })
  }));
  /* 先真建回 active 才能再停——用罐头 retire 即可（route 拦截后按钮需 active 才显示…当前 retired 无停用按钮）
     故改用 establish 罐头拒绝 + 页面上直接调用函数 none 路径 */
  const noneTest = await page.evaluate(async () => {
    await chRetire('m0', '测试'); /* 被 route 拦成 action:none */
    const row = document.querySelector('.sf-chrow');
    const note = row ? row.querySelector('.ch-note') : null;
    return {
      note: note ? note.innerText.replace(/\n/g, ' ') : null,
      banned: note ? (/已停用。/.test(note.innerText) && !/没有可停用/.test(note.innerText)) : null
    };
  });
  step('罐头 none 反向断言', noneTest);
  await page.unroute(`**/api/workshop/${ws}/member/*/channel/retire`);

  await page.route(`**/api/workshop/${ws}/member/*/channel/establish`, route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ success: false, error: '该成员已有运行中的通道，请先停用后再新建。' })
  }));
  const rejectTest = await page.evaluate(async () => {
    await chEstablish('m0', '');
    const row = document.querySelector('.sf-chrow');
    const note = row ? row.querySelector('.ch-note') : null;
    return { note: note ? note.innerText.replace(/\n/g, ' ') : null, hasError: note ? /请先停用/.test(note.innerText) : false };
  });
  step('罐头 active拒绝', rejectTest);
  await page.unroute(`**/api/workshop/${ws}/member/*/channel/establish`);

  /* 5. 视口 */
  const probe = async (w, h) => {
    await page.setViewportSize({ width: w, height: h });
    await sleep(350);
    return await page.evaluate(() => ({ vw: window.innerWidth, overflow: document.documentElement.scrollWidth > window.innerWidth }));
  };
  const v = { v1600: await probe(1600, 1000), v1280: await probe(1280, 720), v1024: await probe(1024, 768) };
  step('视口', v);

  /* 清理 */
  await fetch(BASE + '/api/workshop/' + ws, { method: 'DELETE' });
  step('清理', { deleted: ws });

  const result = { errors, nonGet, init, afterNew, afterRetire, noneTest, rejectTest, v };
  console.log('=== FINAL ===');
  console.log(JSON.stringify(result, null, 2));
  fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize(result), null, 2), 'utf8');
  await browser.close();
})().catch(e => { console.error('FATAL', e); process.exit(1); });
