/* FE-03 booths.html 验收（规则11：自建自清，不写死 ws_id；77 号①改造） */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');
const BASE = process.env.AC_FE_BASE || 'http://127.0.0.1:18930';
const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, 'booths_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', 'booths_验证');
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
  page.on('request', r => {
    if (r.url().includes('/api/') && r.method() !== 'GET') nonGet.push(r.method() + ' ' + r.url().replace(BASE, ''));
  });
  const step = (n, d) => console.log('>> ' + n, JSON.stringify(d));

  /* 1. 公式对拍 + 分类判据 */
  await page.goto(BASE + '/booths.html', { waitUntil: 'networkidle', timeout: 20000 });
  await sleep(800);
  const unit = await page.evaluate(() => ({
    formula: window.__boothsTest.selfTest(),
    classifyNew: window.__boothsTest.classify('D:\\ws\\ws_x\\码农_m1\\bridge.py', 'D:\\ws\\ws_x\\码农_m1'),
    classifyOld: window.__boothsTest.classify('D:\\ws\\ws_x\\bridge.py', 'D:\\ws\\ws_x\\码农_m1'),
    emptyState: document.getElementById('mBody').innerText.slice(0, 80)
  }));
  step('公式与分类', unit);
  const fail = [];
  if (unit.formula.fail.length) fail.push('公式对拍失败 ' + unit.formula.fail.length);

  /* 0.5 自建工作间 + 成员（规则11：数据自建、跑完自清；不再写死 295f6eaa —— 77 号①） */
  const ws = await (async () => {
    const r = await fetch(BASE + '/api/workshop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'FE03验收-自建' }) });
    return (await r.json()).workshop_id;
  })();
  step('自建', { ws });
  if (!ws) process.exit(1);
  await fetch(BASE + `/api/workshop/${ws}/members`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ members: [
      { role: '码农', harness_ids: ['harness-booths-e2e'], display_name: 'harness-booths-e2e' },
      { role: '搜索者', harness_ids: ['harness-booths-e2e-b'], display_name: 'harness-booths-e2e-b' }
    ] }) });

  /* 2. 自建工作间（compartments 权威路径 + 磁盘实况） */
  await page.goto(BASE + `/booths.html?ws_id=${ws}`, { waitUntil: 'networkidle' });
  await sleep(1200);
  const rendered = await page.evaluate(async (id) => {
    const api = await (await fetch('/api/workshop/' + id + '/compartments')).json();
    const paths = [...document.querySelectorAll('.mcard .booth .path')].map(p => p.textContent);
    const dirs = (api.compartments || []).map(c => c.dir);
    return {
      count: document.getElementById('mCount').textContent,
      exCount: document.getElementById('exCount').textContent,
      cards: document.querySelectorAll('.mcard').length,
      shared: document.getElementById('sharedPath').textContent.slice(0, 60),
      firstCard: document.querySelector('.mcard') ? document.querySelector('.mcard').innerText.replace(/\n/g, ' | ').slice(0, 320) : null,
      /* 权威对拍：渲染出的路径（去掉高亮拆分后应拼回 dir）应与接口 dir 一致；长度守卫防越界 */
      pathMatchesApi: paths.length > 0 && paths.length === dirs.length
        && paths.every((t, i) => dirs[i] != null && (dirs[i] === t || dirs[i].endsWith(t))),
      pathsLen: paths.length, dirsLen: dirs.length, apiErr: api.error || api.detail || null,
      apiExistsCount: (api.exists_count != null ? api.exists_count : '?') + '/' + (api.count != null ? api.count : '?'),
      realityText: (document.querySelector('.derived') || {}).textContent || '',
      subdirsOff: document.querySelectorAll('.subdir.off').length,
      exBadges: [...document.querySelectorAll('.ex-badge')].map(b => b.textContent),
      bridges: [...document.querySelectorAll('.bridge')].map(b => b.className + ' :: ' + b.innerText.split('\n')[0]),
      hasUndefined: /undefined/.test(document.body.innerText),
      hasNaN: /\bNaN\b/.test(document.body.innerText)
    };
  }, ws);   /* ★ id 必须作为参数传入：漏传 ⇒ 页面里 fetch 到 /undefined/ ⇒ 404 ⇒ dirs 空、对拍越界崩 */
  const wsStep = rendered;
  step('自建间渲染', wsStep);
  if (!wsStep.cards) fail.push('成员卡未渲染');
  if (!wsStep.pathMatchesApi) fail.push(`路径对拍不符 paths=${wsStep.pathsLen} dirs=${wsStep.dirsLen} apiErr=${wsStep.apiErr}`);
  if (wsStep.hasUndefined) fail.push('页面含 undefined');
  if (errors.length) fail.push('Console 红错 ' + errors.length);
  await page.screenshot({ path: path.join(OUT, '01_1920x1080.png') });

  /* 3. 选择器切换 + URL 同步 */
  await page.goto(BASE + '/booths.html', { waitUntil: 'networkidle' });
  await sleep(900);
  const opts = await page.evaluate(() => document.getElementById('wsSel').options.length);
  await page.selectOption('#wsSel', ws);
  await sleep(900);
  const switched = await page.evaluate(() => ({
    url: location.search,
    cards: document.querySelectorAll('.mcard').length
  }));
  step('选择器', { opts, switched });
  if (!switched.url.includes(ws)) fail.push('选择器未同步 URL');

  /* 4. 已删除工作间降级 */
  await page.goto(BASE + '/booths.html?ws_id=deadbeef', { waitUntil: 'networkidle' });
  await sleep(900);
  const del = await page.evaluate(() => ({
    err: document.getElementById('mErr').textContent.slice(0, 140),
    errShown: document.getElementById('mErr').classList.contains('show'),
    body: document.getElementById('mBody').innerText.slice(0, 90)
  }));
  step('已删除降级', del);
  await page.screenshot({ path: path.join(OUT, '02_已删除降级.png') });

  /* 5. 三档视口 */
  await page.goto(BASE + `/booths.html?ws_id=${ws}`, { waitUntil: 'networkidle' });
  await sleep(900);
  const probe = async (w, h) => {
    await page.setViewportSize({ width: w, height: h });
    await sleep(400);
    return await page.evaluate(() => ({ vw: window.innerWidth, overflow: document.documentElement.scrollWidth > window.innerWidth }));
  };
  const v1920 = await probe(1920, 1080);
  await page.screenshot({ path: path.join(OUT, '03_final_1920.png') });
  const v1280 = await probe(1280, 720);
  await page.screenshot({ path: path.join(OUT, '04_1280x720.png') });
  const v1024 = await probe(1024, 768);
  await page.screenshot({ path: path.join(OUT, '05_1024x768.png'), fullPage: true });
  step('视口', { v1920, v1280, v1024 });
  if (v1920.overflow || v1280.overflow || v1024.overflow) fail.push('视口破版');

  /* 清理（自清） */
  await fetch(BASE + '/api/workshop/' + ws, { method: 'DELETE' }).catch(() => {});

  const out = { pass: fail.length === 0, fail, errors, nonGet, unit, ws, render: wsStep, switched, del, v1920, v1280, v1024 };
  console.log('=== FINAL ===');
  console.log(JSON.stringify(out, null, 2));
  fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize(out), null, 2), 'utf8');
  await browser.close();
  process.exit(fail.length ? 1 : 0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
