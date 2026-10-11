/* 通道 91 号：register.html 右上角四链接重叠修复的验收（规则 16：走界面 + 截图）
   断言：1680/1440/1280 三档下 4 链接互不重叠、不压标题与「远程接入」按钮、无横向滚动；链接可点。 */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');
const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, 'register_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', 'register_验证');
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
    if (typeof v === 'string') return v.replace(drive, '<本机路径>');
    if (Array.isArray(v)) return v.map(walk);
    if (v && typeof v === 'object') { const r = {}; for (const k in v) r[k] = walk(v[k]); return r; }
    return v;
  };
  return walk(o);
}
const BASE = process.env.AC_FE_BASE || 'http://127.0.0.1:18930';
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1680, height: 950 } });
  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  const step = (n, d) => console.log('>> ' + n, JSON.stringify(d));
  const fail = [];

  const probe = () => page.evaluate(() => {
    const rects = [...document.querySelectorAll('.top-actions .back')].map(a => {
      const r = a.getBoundingClientRect();
      return { text: a.textContent.trim(), href: a.getAttribute('href'),
               x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
    });
    const hit = (a, b) => a && b && a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
    const title = document.querySelector('.top h1');
    const btn = document.querySelector('.remote-open-btn');
    const tr = title ? title.getBoundingClientRect() : null;
    const br = btn ? btn.getBoundingClientRect() : null;
    const titleR = tr ? { x: Math.round(tr.x), y: Math.round(tr.y), w: Math.round(tr.width), h: Math.round(tr.height) } : null;
    const btnR = br ? { x: Math.round(br.x), y: Math.round(br.y), w: Math.round(br.width), h: Math.round(br.height) } : null;
    const overlaps = [];
    for (let i = 0; i < rects.length; i++)
      for (let j = i + 1; j < rects.length; j++)
        if (hit(rects[i], rects[j])) overlaps.push(rects[i].text + '×' + rects[j].text);
    return {
      count: rects.length, rects, overlaps,
      hitsTitle: titleR ? rects.some(r => hit(r, titleR)) : null,
      hitsBtn: btnR ? rects.some(r => hit(r, btnR)) : null,
      hScroll: document.documentElement.scrollWidth > window.innerWidth + 1
    };
  });

  const viewports = [[1680, 950, '1680'], [1440, 900, '1440'], [1280, 800, '1280']];
  const results = {};
  for (const [w, h, name] of viewports) {
    await page.setViewportSize({ width: w, height: h });
    await page.goto(BASE + '/register.html', { waitUntil: 'domcontentloaded', timeout: 25000 });
    await sleep(700);
    const r = await probe();
    results[name] = { count: r.count, overlaps: r.overlaps, hitsTitle: r.hitsTitle, hitsBtn: r.hitsBtn, hScroll: r.hScroll };
    await page.screenshot({ path: path.join(OUT, `01_${name}_顶栏.png`) });
    step(name, r);
    if (r.count !== 4) fail.push(name + ' 链接数=' + r.count);
    if (r.overlaps.length) fail.push(name + ' 重叠: ' + r.overlaps.join(','));
    if (r.hitsTitle) fail.push(name + ' 压到标题');
    if (r.hitsBtn) fail.push(name + ' 压到远程接入按钮');
    if (r.hScroll) fail.push(name + ' 横向滚动条');
  }

  /* 可点性：真点「📡 看板」应跳到 /ops.html */
  await page.setViewportSize({ width: 1680, height: 950 });
  await page.goto(BASE + '/register.html', { waitUntil: 'domcontentloaded' });
  await sleep(600);
  await page.click('.top-actions a[href="/ops.html"]');
  await sleep(700);
  const clicked = page.url();
  step('点击看板', { url: clicked });
  if (!/\/ops\.html/.test(clicked)) fail.push('看板链接点击未跳转: ' + clicked);

  if (errors.length) fail.push('Console 红错 ' + errors.length);

  const out = { pass: fail.length === 0, fail, viewports: results, clicked, errors,
                screenshots: fs.readdirSync(OUT) };
  fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize(out), null, 2), 'utf8');
  step('结论', { pass: out.pass, fail });
  await browser.close();
  process.exit(fail.length ? 1 : 0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
