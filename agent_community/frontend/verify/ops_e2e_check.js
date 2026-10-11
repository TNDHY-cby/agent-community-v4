/* FE-01 ops.html 端到端验收脚本（playwright-core + 系统 Edge） */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');

const BASE = (process.env.AC_FE_BASE || 'http://127.0.0.1:18920') + '/ops.html';
const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, 'ops_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', 'ops_验证');
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
    if (typeof v === 'string') {
      return v.replace(drive, '<本机路径>')
        .replace(/harness-小白龙/g, '示例Harness-A')
        .replace(/harness-测试甲/g, '示例Harness-B')
        .replace(/harness-测试乙/g, '示例Harness-C')
        .replace(/harness-测试丙/g, '示例Harness-D')
        .replace(/harness-dsh-web-[A-Za-z0-9.\-]+/g, '示例Harness-E');
    }
    if (Array.isArray(v)) return v.map(walk);
    if (v && typeof v === 'object') { const r = {}; for (const k in v) r[k] = walk(v[k]); return r; }
    return v;
  };
  return walk(o);
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });

  const errors = [];
  const badRequests = [];   // 非 GET 或失败的请求
  page.on('console', m => { if (m.type() === 'error') errors.push('console.error: ' + m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('request', r => {
    if (r.url().includes('/api/') && r.method() !== 'GET') badRequests.push(r.method() + ' ' + r.url());
  });

  const result = { steps: [] };
  const step = (name, data) => { result.steps.push({ name, data }); console.log('>> ' + name, JSON.stringify(data)); };

  /* ---- 1. 首次加载（1920×1080） ---- */
  await page.goto(BASE, { waitUntil: 'networkidle', timeout: 20000 });
  await sleep(800);
  step('加载', { title: await page.title(), errors: errors.slice() });

  /* ---- 2. 正则自测（任务书 §4 三条样本 + 畸形） ---- */
  const selfTest = await page.evaluate(() => window.__opsTest.selfTest());
  step('detail解析自测', selfTest);

  /* ---- 3. 空态/渲染检查 ---- */
  const dom = await page.evaluate(() => {
    const txt = id => (document.getElementById(id) || {}).textContent || '';
    const bodyText = document.body.innerText;
    return {
      healthClass: document.getElementById('health').className,
      healthText: txt('healthText'),
      tlCount: txt('tlCount'),
      tlRows: document.querySelectorAll('.tl-item').length,
      hasWorkshopChip: !!document.querySelector('.ws-chip'),
      hbSummary: txt('hbSummary'),
      hbEmpty: !!document.querySelector('#hbBody .empty'),
      bannerShown: document.getElementById('banner').classList.contains('show'),
      containsUndefined: /undefined/.test(bodyText),
      containsNaN: /\bNaN\b/.test(bodyText),
      rows: [...document.querySelectorAll('.tl-item')].map(r => r.innerText.replace(/\n/g, ' | ').slice(0, 200)),
      chips: [...document.querySelectorAll('.tl-item .chip')].map(c => c.textContent),
      acts: [...document.querySelectorAll('.tl-item .act')].map(c => c.textContent)
    };
  });
  step('DOM检查', dom);
  await page.screenshot({ path: path.join(OUT, '01_1920x1080.png') });

  /* ---- 4. 畸形 detail 降级渲染 ---- */
  const fake = await page.evaluate(() => {
    window.__opsTest.injectFake('malformed garbage line');
    window.__opsTest.injectFake('future_event: open→closed action=weird note=任意 备注: 含冒号');
    const items = document.querySelectorAll('.tl-item');
    const f = items[0], p = items[1];
    return {
      unparsedShown: !!f.querySelector('.unparsed-tag'),
      unparsedText: f.innerText.replace(/\n/g, ' | '),
      parsedFuture: p.innerText.replace(/\n/g, ' | '),
      stillAlive: items.length
    };
  });
  step('畸形detail降级', fake);
  await page.screenshot({ path: path.join(OUT, '02_畸形detail降级.png') });

  /* ---- 5. 展开/收起 ---- */
  await page.evaluate(() => { document.querySelector('.tl-item').click(); });
  const opened = await page.evaluate(() => document.querySelector('.tl-item').classList.contains('open'));
  await page.evaluate(() => { document.querySelector('.tl-item').click(); });
  const closed = await page.evaluate(() => !document.querySelector('.tl-item').classList.contains('open'));
  step('点击展开/收起', { opened, closed });

  /* ---- 6. 筛选 ---- */
  await page.fill('#fActor', 'user');
  await sleep(1200);
  const filtered = await page.evaluate(() => ({
    count: document.getElementById('tlCount').textContent,
    url: location.href
  }));
  await page.fill('#fActor', '不存在的actor_xyz');
  await sleep(1200);
  const filteredEmpty = await page.evaluate(() => document.getElementById('tlList').innerText.slice(0, 80));
  await page.fill('#fActor', '');
  await sleep(800);
  step('筛选', { user: filtered, emptyResult: filteredEmpty });

  /* ---- 7. 窄窗口 1280×720 与 1024 ---- */
  await page.setViewportSize({ width: 1280, height: 720 });
  await sleep(500);
  const overflow1280 = await page.evaluate(() => ({
    docW: document.documentElement.scrollWidth, winW: window.innerWidth
  }));
  await page.screenshot({ path: path.join(OUT, '03_1280x720.png') });
  await page.setViewportSize({ width: 1024, height: 768 });
  await sleep(500);
  const overflow1024 = await page.evaluate(() => ({
    docW: document.documentElement.scrollWidth, winW: window.innerWidth
  }));
  await page.screenshot({ path: path.join(OUT, '04_1024x768.png'), fullPage: true });
  step('窄窗口', { w1280: overflow1280, w1024: overflow1024 });

  /* ---- 8. 错误分支：拦截 /api/** 模拟后端挂掉 ---- */
  await page.route('**/api/**', r => r.abort('failed'));
  await page.reload({ waitUntil: 'domcontentloaded' });
  await sleep(1200);
  const errState = await page.evaluate(() => ({
    bannerShown: document.getElementById('banner').classList.contains('show'),
    bannerText: document.getElementById('banner').innerText.replace(/\n/g, ' | ').slice(0, 300),
    healthText: document.getElementById('healthText').textContent,
    tlErr: document.getElementById('tlErr').innerText.slice(0, 200),
    hbErr: document.getElementById('hbErr').innerText.slice(0, 200)
  }));
  await page.screenshot({ path: path.join(OUT, '05_错误态.png') });
  step('错误分支', errState);

  /* ---- 9. 恢复：解除拦截后应自愈 ---- */
  await page.unroute('**/api/**');
  await page.reload({ waitUntil: 'networkidle' });
  await sleep(1000);
  const recovered = await page.evaluate(() => ({
    bannerShown: document.getElementById('banner').classList.contains('show'),
    health: document.getElementById('healthText').textContent
  }));
  step('恢复', recovered);

  /* ---- 汇总 ---- */
  result.consoleErrors = errors;
  result.nonGetRequests = badRequests;
  result.screenshots = fs.readdirSync(OUT);
  fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize(result), null, 2), 'utf8');
  console.log('=== FINAL ===');
  console.log(JSON.stringify({
    selfTest, consoleErrors: errors, nonGetRequests: badRequests,
    containsUndefined: dom.containsUndefined, containsNaN: dom.containsNaN,
    overflow1280, overflow1024, bannerInError: errState.bannerShown, recovered
  }, null, 2));
  await browser.close();
})().catch(e => { console.error('FATAL', e); process.exit(1); });
