/* FE-06 共享资料库面板验收（规则11：自建自清，不写死 ws_id；77 号①改造） */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');
const BASE = process.env.AC_FE_BASE || 'http://127.0.0.1:18930';
const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, '资料库_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', '资料库_验证');
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
  const nonGet = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('request', r => { if (r.url().includes('/api/') && r.method() !== 'GET') nonGet.push(r.method() + ' ' + r.url()); });
  const step = (n, d) => console.log('>> ' + n, JSON.stringify(d));

  /* 0. stateClass 枚举（DSH 13号§四） */
  await page.goto(BASE + '/booths.html', { waitUntil: 'networkidle', timeout: 20000 });
  const unit = await page.evaluate(() => {
    const t = window.__boothsTest.stateClass;
    return {
      pending: t('pending'), dispatched: t('dispatched'), acked: t('acked'),
      entered: t('entered'), blocked: t('blocked'), timeout: t('timeout'),
      needs_human: t('needs_human'), unknown: t('xxx')
    };
  });
  step('stateClass', unit);

  /* 0.5 自建工作间（规则11：数据自建、跑完自清；不再写死 ws_id —— 77 号①） */
  const ws = await (async () => {
    const r = await fetch(BASE + '/api/workshop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'FE06验收-自建' }) });
    return (await r.json()).workshop_id;
  })();
  step('自建', { ws });
  if (!ws) process.exit(1);
  const fail = [];

  /* B. 新工作间：库已创建 + 预览（建库在建间时） */
  await page.goto(BASE + `/booths.html?ws_id=${ws}`, { waitUntil: 'networkidle' });
  await sleep(1000);
  const B = await page.evaluate(() => {
    const files = [...document.querySelectorAll('.lib-file')].map(f => f.textContent);
    const summaries = [...document.querySelectorAll('.lib-prev summary')].map(s => s.innerText.replace(/\n/g, ' '));
    /* 展开 plan.md 读内容（第一个 .lib-prev = plan 块） */
    const det = document.querySelector('.lib-prev');
    if (det) det.open = true;
    const planText = det ? det.querySelector('pre').textContent : '';
    return {
      badge: document.getElementById('libExist').textContent,
      count: document.getElementById('libCount').textContent,
      files, summaries,
      planLen: planText.length,
      planHasRole: /组长|码农|成员/.test(planText),
      rule: (document.querySelector('.lib-rule') || {}).textContent.slice(0, 90),
      hasUndefined: /undefined/.test(document.body.innerText),
      hasNaN: /\bNaN\b/.test(document.body.innerText)
    };
  });
  step('B 自建间资料库', B);
  if (B.badge !== '已创建') fail.push('B badge=' + B.badge);
  if (!B.planLen) fail.push('B plan 预览为空');
  await page.screenshot({ path: path.join(OUT, '01_资料库已创建.png') });

  /* D. 视口（在库可见时测） */
  const probe = async (w, h) => {
    await page.setViewportSize({ width: w, height: h });
    await sleep(350);
    return await page.evaluate(() => ({ vw: window.innerWidth, overflow: document.documentElement.scrollWidth > window.innerWidth }));
  };
  const v = { v1920: await probe(1920, 1080), v1280: await probe(1280, 720), v1024: await probe(1024, 768) };
  await page.setViewportSize({ width: 1920, height: 1080 });
  step('D 视口', v);
  if (v.v1920.overflow || v.v1280.overflow || v.v1024.overflow) fail.push('视口破版');

  /* A. 库未创建空态：删掉自建间的 library 目录（路径取接口 dir，不写死本机路径）再刷新 */
  const libDir = (await (await fetch(BASE + `/api/workshop/${ws}/library`)).json()).dir || '';
  if (libDir) fs.rmSync(libDir, { recursive: true, force: true });
  await page.goto(BASE + `/booths.html?ws_id=${ws}`, { waitUntil: 'networkidle' });
  await sleep(900);
  const A = await page.evaluate(() => ({
    panelShown: document.getElementById('libPanel').style.display !== 'none',
    badge: document.getElementById('libExist').textContent,
    /* 保留换行：若先转成 " | "，sanitize 的盘符正则会一路吞到串尾（踩过） */
    body: document.getElementById('libBody').innerText.slice(0, 300)
  }));
  step('A 库未创建空态', A);
  if (!A.panelShown) fail.push('A 面板未显示');
  if (A.badge !== '未创建') fail.push('A badge=' + A.badge);
  if (!/资料库尚未创建/.test(A.body)) fail.push('A 空态文案缺失');

  /* Console 检查放在 C 之前：C 是故意打开不存在的 ws，其 404 资源报错属预期 */
  if (errors.length) fail.push('Console 红错(A/B/D 段) ' + errors.length);

  /* C. 404 降级 */
  await page.goto(BASE + '/booths.html?ws_id=deadbeef', { waitUntil: 'networkidle' });
  await sleep(900);
  const C = await page.evaluate(() => ({
    err: document.getElementById('mErr').textContent.slice(0, 160),
    libBadge: document.getElementById('libExist').textContent
  }));
  step('C 404', C);
  if (!C.err) fail.push('C 未显示错误条');
  const errsAfterC = errors.length;   /* C 段新增的 404 资源报错是预期的，只记录不断言 */

  /* 清理（自清） */
  await fetch(BASE + '/api/workshop/' + ws, { method: 'DELETE' }).catch(() => {});

  console.log('=== FINAL ===');
  console.log(JSON.stringify({ pass: fail.length === 0, fail, errors, nonGet, unit, ws, A, B, C, v }, null, 2));
  fs.writeFileSync(path.join(RESULT_DIR, 'result.json'),
    JSON.stringify(sanitize({ pass: fail.length === 0, fail, errors, nonGet, unit, ws, A, B, C, v }), null, 2), 'utf8');
  await browser.close();
  process.exit(fail.length ? 1 : 0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
