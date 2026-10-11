/* 通道 90 号：工作间「下一步」条状态文案缺陷的验收（规则 16：真浏览器 + 截图）
   ① 活体：大厅建的间（有任务）任何时候不得显示"回大厅发布任务"；
   ①b/④ 白盒驱动 renderNextStep：draft+任务 ⇒ 选人指引；draft+无任务 ⇒ 保留"回大厅"分支；
   ② 点「结束讨论，选定员工」后立刻按选人阶段渲染；
   ③ division 文案含可执行的 AI 同意引导。
   注：活体页首载会触发一次自动 discuss（产品自身行为；本机实例已配 AI ⇒ 可能产生一次真实调用，
   与 DSH 走查同路径）。 */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');
const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, 'nextstep_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', 'nextstep_验证');
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
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  const step = (n, d) => console.log('>> ' + n, JSON.stringify(d));
  const fail = [];
  const bar = () => page.evaluate(() => ({
    text: (document.getElementById('nextStepText') || {}).textContent || '',
    btns: [...document.querySelectorAll('#nextStepActions .ns-btn')].map(b => b.textContent),
    visible: document.getElementById('nextStepBar').style.display !== 'none'
  }));
  const shot = n => page.screenshot({ path: path.join(OUT, n) });

  const wsHall = (await (await fetch(BASE + '/api/workshop', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ hall_content: '做一个能拖拽的看板（90号验收）' }) })).json()).workshop_id;
  step('自建', { wsHall });
  if (!wsHall) process.exit(1);

  try {
    /* ① 活体：条要早点出现，且绝不出现反向指引 */
    await page.route('**/api/workshop/*/discuss', r => r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ success: true, reply: '（验收桩）需求已明确。', status: 'discussing', zone: 1 }),
    }));
    await page.goto(`${BASE}/workshop.html?ws_id=${wsHall}`, { waitUntil: 'domcontentloaded', timeout: 25000 });
    await page.waitForSelector('#nextStepBar', { state: 'visible', timeout: 25000 });
    await sleep(800);
    const s1 = await bar();
    await shot('01_首屏.png');
    step('① 活体首屏', s1);
    if (!s1.visible) fail.push('① 下一步条未显示');
    if (/回大厅发布任务/.test(s1.text)) fail.push('① 活体出现反向指引: ' + s1.text);

    /* ①b 白盒：draft + 有任务 ⇒「任务已发布」+ 结束讨论按钮 */
    const s1b = await page.evaluate(() => {
      _wsStatus = 'draft'; _wsHall = '做一个能拖拽的看板';
      renderNextStep(); return null;
    }).then(bar);
    await shot('02_draft有任务分支.png');
    step('①b draft+任务', s1b);
    if (!/任务已发布/.test(s1b.text)) fail.push('①b 未走任务分支: ' + s1b.text);
    if (!s1b.btns.includes('结束讨论，选定员工 →')) fail.push('①b 缺结束讨论按钮');

    /* ② 点侧栏「结束讨论，选定员工」→ 立刻按选人阶段渲染 */
    await page.click('#endDiscussBtn');
    await sleep(500);
    const s2 = await bar();
    await shot('03_结束讨论后.png');
    step('② 结束讨论后', s2);
    if (!/初选|选定员工/.test(s2.text)) fail.push('② 未切到选人指引: ' + s2.text);
    if (/草稿|回大厅/.test(s2.text)) fail.push('② 残留 draft 文案: ' + s2.text);

    /* ③ division 文案含可执行引导 */
    const s3 = await page.evaluate(() => { _wsStatus = 'division'; renderNextStep(); return null; }).then(bar);
    await shot('04_division引导.png');
    step('③ division引导', s3);
    if (!/分工可以，开始工作/.test(s3.text) || !/分工已明确/.test(s3.text)) fail.push('③ AI同意引导缺失: ' + s3.text);

    /* ④ 白盒：draft + 无任务 ⇒ 保留「回大厅」分支 */
    const s4 = await page.evaluate(() => {
      _wsStatus = 'draft'; _wsHall = '';
      renderNextStep(); return null;
    }).then(bar);
    await shot('05_纯草稿分支.png');
    step('④ draft+无任务', s4);
    if (!/回大厅发布任务/.test(s4.text)) fail.push('④ 回大厅分支被误改: ' + s4.text);

    if (errors.length) fail.push('Console 红错 ' + errors.length);

    const out = { pass: fail.length === 0, fail, s1, s1b, s2, s3, s4, errors, screenshots: fs.readdirSync(OUT) };
    fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize(out), null, 2), 'utf8');
    step('结论', { pass: out.pass, fail });
  } catch (e) {
    fail.push('FATAL: ' + (e && e.message || e));
    step('FATAL', fail[fail.length - 1]);
    fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize({ pass: false, fail, errors }), null, 2), 'utf8');
  } finally {
    await fetch(BASE + '/api/workshop/' + wsHall, { method: 'DELETE' }).catch(() => {});
    await browser.close();
  }
  process.exit(fail.length ? 1 : 0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
