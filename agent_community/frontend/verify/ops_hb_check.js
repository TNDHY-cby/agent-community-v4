const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');
const BASE = process.env.AC_FE_BASE || 'http://127.0.0.1:18920';
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
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));

  /* 阶段1：在线卡片 */
  await page.goto(BASE + '/ops.html', { waitUntil: 'networkidle' });
  await sleep(1000);
  const online = await page.evaluate(() => ({
    summary: document.getElementById('hbSummary').innerText.replace(/\n/g, ' '),
    card: document.querySelector('.hb-card') ? document.querySelector('.hb-card').innerText.replace(/\n/g, ' | ') : null,
    badge: document.querySelector('.hb-badge') ? document.querySelector('.hb-badge').textContent : null
  }));
  /* 注入 escalate_l2 样例看醒目徽章 */
  await page.evaluate(() => window.__opsTest.injectFake('escalate: executing→stuck-paused action=escalate_l2 note=需要人工拍板'));
  await sleep(300);
  await page.screenshot({ path: path.join(OUT, '06_在线卡片与escalate_l2.png') });
  console.log('PHASE1', JSON.stringify(online));

  /* 阶段2：等 65s 让心跳变失联（阈值 30s×2.0） */
  console.log('waiting 65s for stale...');
  await sleep(65000);
  await page.reload({ waitUntil: 'networkidle' });
  await sleep(1200);
  const stale = await page.evaluate(() => ({
    summary: document.getElementById('hbSummary').innerText.replace(/\n/g, ' '),
    groups: [...document.querySelectorAll('.hb-group')].map(g => g.textContent),
    cards: [...document.querySelectorAll('.hb-card')].map(c => ({
      cls: c.className, text: c.innerText.replace(/\n/g, ' | ')
    })),
    warn: document.getElementById('hbWarn').classList.contains('show'),
    warnText: document.getElementById('hbWarn').innerText
  }));
  await page.screenshot({ path: path.join(OUT, '07_失联看板.png') });
  console.log('PHASE2', JSON.stringify(stale, null, 2));
  console.log('ERRORS', JSON.stringify(errors));
  await browser.close();
})().catch(e => { console.error('FATAL', e); process.exit(1); });
