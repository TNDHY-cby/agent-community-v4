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
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  await page.goto(BASE + '/ops.html', { waitUntil: 'networkidle' });
  await new Promise(r => setTimeout(r, 1200));
  const chips = await page.evaluate(() =>
    [...document.querySelectorAll('.ws-chip')].map(c => ({
      cls: c.className, text: c.textContent, title: c.title
    })));
  const rows = await page.evaluate(() =>
    [...document.querySelectorAll('.tl-item')].map(r => r.innerText.replace(/\n/g, ' | ')));
  await page.screenshot({ path: path.join(OUT, '08_工单名映射.png') });
  console.log(JSON.stringify({ chips, rows, errors }, null, 2));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
