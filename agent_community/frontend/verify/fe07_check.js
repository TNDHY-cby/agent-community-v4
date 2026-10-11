/* FE-07 主流程串联验收（隔离实例 + AC_FE_BASE + 自建自清数据） */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');

const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, 'FE07_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', 'FE07_验证');
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

  /* 0. 自建数据（隔离实例，Node 侧直连，避免 about:blank 跨域） */
  const created = await (async () => {
    const r = await fetch(BASE + '/api/workshop', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'FE07验收-自建', hall_content: 'FE-07 自建自清验收工作间' })
    });
    const d = await r.json();
    return d.workshop_id;
  })();
  step('自建工作间', { ws: created, base: BASE });
  if (!created) { console.error('无法创建工作间'); process.exit(1); }

  /* 1. workshop 指引条：真实载入 + 各状态断言 */
  await page.goto(`${BASE}/workshop.html?ws_id=${created}`, { waitUntil: 'domcontentloaded', timeout: 25000 });
  await sleep(1200);
  const loaded = await page.evaluate(() => ({
    barShown: document.getElementById('nextStepBar').style.display !== 'none',
    text: document.getElementById('nextStepText').textContent,
    buttons: [...document.querySelectorAll('#nextStepActions .ns-btn')].map(b => b.textContent),
    wsStatus: typeof _wsStatus !== 'undefined' ? _wsStatus : null
  }));
  step('载入(真实status)', loaded);

  const states = {};
  for (const st of ['discussing', 'selecting', 'division', 'running', 'review', 'done', '未来状态x']) {
    states[st] = await page.evaluate(s => {
      _wsStatus = s; renderNextStep();
      return {
        text: document.getElementById('nextStepText').textContent,
        buttons: [...document.querySelectorAll('#nextStepActions .ns-btn')].map(b => b.textContent)
      };
    }, st);
  }
  step('状态映射', states);
  await page.screenshot({ path: path.join(OUT, '01_指引条_running.png') });

  /* 2. start 成功 → notice + 指引条切 running */
  await page.route('**/api/workshop/*/start', r => r.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ success: true, action: 'continue', status: 'running' })
  }));
  await page.evaluate(() => startWork());
  await sleep(800);
  const started = await page.evaluate(() => ({
    barText: document.getElementById('nextStepText').textContent,
    notice: [...document.querySelectorAll('#discussion2 .msg')].map(m => m.textContent).join(' | ').slice(-220),
    hasPath: /booths\.html\?ws_id=/.test(document.getElementById('discussion2').innerText)
  }));
  step('start出口', started);
  await page.screenshot({ path: path.join(OUT, '02_start出口.png') });

  /* 3. index 阶段条 */
  await page.route('**/api/tasks/tFE07/workshop', r => r.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ workshop_id: created, name: 'FE07验收-自建', status: 'selecting', member_count: 0, next_step: '' })
  }));
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded', timeout: 25000 });
  await sleep(1000);
  await page.evaluate(tid => {
    window.tasks = window.tasks || [];
    tasks.push({ task_id: tid, title: 'FE07链路验收任务', status: 'pending' });
    openWorkshop(tid);
  }, 'tFE07');
  await sleep(900);
  const strip = await page.evaluate(() => ({
    shown: document.getElementById('flowStrip').style.display !== 'none',
    dots: [...document.querySelectorAll('.fs-dot')].map(d => d.textContent + (d.classList.contains('on') ? '(当前)' : d.classList.contains('done') ? '(完)' : '')),
    statusChip: (document.querySelector('.fs-status') || {}).textContent,
    enter: (document.querySelector('.fs-enter') || {}).getAttribute ? document.querySelector('.fs-enter').getAttribute('href') : null
  }));
  step('阶段条', strip);
  await page.screenshot({ path: path.join(OUT, '03_大厅阶段条.png') });

  /* 点击进入工作间 */
  if (strip.enter) {
    await page.click('.fs-enter');
    await page.waitForURL(/workshop\.html\?ws_id=/, { timeout: 8000 }).catch(() => {});
    step('进入跳转', { url: page.url() });
  }

  /* 4. booths 返回工作间 */
  await page.goto(`${BASE}/booths.html?ws_id=${created}`, { waitUntil: 'domcontentloaded' });
  await sleep(900);
  const back = await page.evaluate(() => {
    const b = document.getElementById('backWs');
    return { shown: b.style.display !== 'none', href: b.getAttribute('href') };
  });
  step('booths返回', back);
  await page.screenshot({ path: path.join(OUT, '04_booths返回.png') });

  /* 5. 404 反查（不存在的任务） */
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
  await sleep(700);
  const nf = await page.evaluate(async () => {
    tasks.push({ task_id: 'tNoWsFE07', title: '未建间任务', status: 'pending' });
    openWorkshop('tNoWsFE07');
    await new Promise(r => setTimeout(r, 700));
    return {
      chip: (document.querySelector('.fs-status') || {}).textContent,
      none: (document.querySelector('.fs-none') || document.createElement('span')).textContent,
      shown: document.getElementById('flowStrip').style.display !== 'none'
    };
  });
  step('404区分', nf);

  /* 清数据（Node 侧） */
  await fetch(BASE + '/api/workshop/' + created, { method: 'DELETE' });
  step('清理', { deleted: created });

  const result = { errors, nonGet, loaded, states, started, strip, back, nf };
  console.log('=== FINAL ===');
  console.log(JSON.stringify(result, null, 2));
  fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize(result), null, 2), 'utf8');
  await browser.close();
})().catch(e => { console.error('FATAL', e); process.exit(1); });
