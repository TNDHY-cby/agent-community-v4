/* FE-10 对接中心：页面可达 + 两入口 + 讨论红线（零写） + 互链 */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');

const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, 'FE10_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', 'FE10_验证');
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
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const errors = [];
  const nonGet = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('request', r => {
    if (r.url().includes('/api/') && r.method() !== 'GET') nonGet.push(r.method() + ' ' + r.url().replace(BASE, ''));
  });
  const step = (n, d) => console.log('>> ' + n, JSON.stringify(d));

  /* 1. hub 页本体 */
  await page.goto(BASE + '/hub.html', { waitUntil: 'networkidle', timeout: 20000 });
  await sleep(700);
  const hub = await page.evaluate(() => ({
    title: document.title,
    logo: (document.querySelector('.logo') || {}).innerText,
    banner: (document.querySelector('.scope-banner') || {}).innerText.slice(0, 120),
    rail: document.querySelectorAll('.rail-item').length,
    scopeNoDiscuss: /不承载讨论/.test(document.body.innerText),
    hasWorkshopLink: [...document.querySelectorAll('a')].some(a => /workshop\.html/.test(a.getAttribute('href') || '')),
    hasUndefined: /undefined/.test(document.body.innerText)
  }));
  step('hub本体', hub);
  await page.screenshot({ path: path.join(OUT, '01_hub首页.png') });

  /* hash 深链 + 02/08 讨论出口 */
  await page.goto(BASE + '/hub.html#talk', { waitUntil: 'networkidle' });
  await sleep(500);
  const talk = await page.evaluate(() => ({
    stage: document.getElementById('stageTitle').textContent,
    hasExit: /进入工作间/.test(document.getElementById('stageBody').innerText)
  }));
  await page.goto(BASE + '/hub.html#work', { waitUntil: 'networkidle' });
  await sleep(500);
  const work = await page.evaluate(() => ({
    stage: document.getElementById('stageTitle').textContent,
    hasExit: /进入工作间讨论/.test(document.getElementById('stageBody').innerText)
  }));
  step('讨论舞台出口', { talk, work });

  /* 2. 大厅入口（空白卡片 + 常驻 tab）
     ★2026-10-23：空态第一张改成「接入一位员工（对接中心）」，流程说明退居第二（见 FE-30 §9）。
       ⇒ 判据按 href 认人，不按"第一张"认人。 */
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  await sleep(900);
  /* 隔离实例上没配 AI ⇒ 设置面板会自动弹出来挡住点击（不是缺陷，是"先配好 AI"的引导）。
     验的是大厅入口，先把面板关掉，别让遮罩把点击吃掉（否则是假失败）。 */
  if (await page.$('.sdh-close')) { await page.click('.sdh-close').catch(() => {}); await sleep(400); }
  const hall = await page.evaluate(() => {
    const dock = document.querySelector('.hall-entry[href="/docking.html"]');
    const hub = document.querySelector('.hall-entry[href="/hub.html"]');
    const tab = document.querySelector('.view-tab[href="/hub.html"]');
    const box = e => (e && e.getBoundingClientRect().width > 0);
    /* ★2026-10-23：卡片是**空态**的一部分；大厅里已经有广播/消息时它们本来就不出现（不是缺陷）。
       所以这里记 `emptyState`，断言只在空态成立 —— 否则在"有广播的实例"上会假红。 */
    const emptyState = !!document.querySelector('.hall-empty');
    return {
      emptyState,
      dockShown: box(dock), dockText: dock ? dock.innerText.replace(/\n/g, ' | ') : null,
      hubShown: box(hub), hubText: hub ? hub.innerText.replace(/\n/g, ' | ') : null,
      tabShown: box(tab)
    };
  });
  await page.screenshot({ path: path.join(OUT, '02_大厅入口.png') });
  let clickedTo = null;
  if (hall.emptyState) {
    await page.click('.hall-entry[href="/hub.html"]');
    await page.waitForURL(/hub\.html/, { timeout: 8000 }).catch(() => {});
    clickedTo = page.url().includes('hub.html');
  }
  step('大厅入口', { ...hall, clickedTo });

  /* 3. 工作间入口（规则11：不再写死 295f6eaa —— .hub-pill 是静态侧栏元素，无参打开即可验，零 404） */
  await page.goto(BASE + '/workshop.html', { waitUntil: 'domcontentloaded' });
  await sleep(1500);
  const wsp = await page.evaluate(() => {
    const p = document.querySelector('.hub-pill');
    return { shown: !!p && p.getBoundingClientRect().width > 0, href: p ? p.getAttribute('href') : null };
  });
  await page.screenshot({ path: path.join(OUT, '03_工作间入口.png') });
  step('工作间入口', wsp);

  /* 4. 窄窗口 */
  await page.goto(BASE + '/hub.html', { waitUntil: 'networkidle' });
  await page.setViewportSize({ width: 1024, height: 768 });
  await sleep(500);
  const narrow = await page.evaluate(() => ({ overflow: document.documentElement.scrollWidth > window.innerWidth }));
  await page.screenshot({ path: path.join(OUT, '04_hub1024.png'), fullPage: true });
  step('窄窗口', narrow);

  const result = { errors, nonGet, hub, talk, work, hall, wsp, narrow };
  console.log('=== FINAL ===');
  console.log(JSON.stringify(result, null, 2));
  fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize(result), null, 2), 'utf8');
  await browser.close();
})().catch(e => { console.error('FATAL', e); process.exit(1); });
