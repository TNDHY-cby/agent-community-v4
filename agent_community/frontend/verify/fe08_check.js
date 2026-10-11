/* FE-08 会话流程面板验收（隔离实例 + AC_FE_BASE + 自建自清；含准则⑤反向断言） */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');

const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, 'FE08_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', 'FE08_验证');
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

  /* 0. 自建：工作间 + 一名成员（假 harness，未配脚本 ⇒ 必走人工/verified=false 路径） */
  const ws = await (async () => {
    const r = await fetch(BASE + '/api/workshop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'FE08验收-自建', hall_content: 'FE-08 自建自清' }) });
    return (await r.json()).workshop_id;
  })();
  const addRes = await (async () => {
    const r = await fetch(BASE + `/api/workshop/${ws}/members`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ members: [{ role: '码农', harness_ids: ['harness-fe08-fake'], display_name: 'harness-fe08-fake' }] }) });
    return { status: r.status, body: await r.json() };
  })();
  step('自建', { ws, addStatus: addRes.status, addOk: addRes.body && addRes.body.success });
  if (!ws) { console.error('无法自建'); process.exit(1); }

  /* 1. 面板渲染（等待面板真正出现——loadWorkshop 异步链可能晚于 domcontentloaded） */
  await page.goto(`${BASE}/workshop.html?ws_id=${ws}`, { waitUntil: 'domcontentloaded', timeout: 25000 });
  await page.waitForSelector('.sf-card, .sf-empty', { timeout: 12000 }).catch(() => {});
  await sleep(500);
  const render = await page.evaluate(() => {
    const p = document.getElementById('sessionFlow');
    return {
      shown: p && p.style.display !== 'none',
      summary: (document.getElementById('sfSummary') || {}).textContent,
      summaryCls: (document.getElementById('sfSummary') || {}).className,
      cards: document.querySelectorAll('.sf-card').length,
      sessionName: (document.querySelector('.sf-line') || {}).textContent,
      chips: [...document.querySelectorAll('.sf-chip')].map(c => c.textContent),
      meta: (document.querySelector('.sf-meta') || {}).textContent,
      buttons: [...document.querySelectorAll('.sf-acts .btn')].map(b => b.textContent),
      entryState: (document.querySelector('.sf-es') || {}).textContent,
      hasUndefined: /undefined/.test(document.body.innerText),
      hasNaN: /\bNaN\b/.test(document.body.innerText)
    };
  });
  step('渲染', render);
  await page.screenshot({ path: path.join(OUT, '01_面板渲染.png') });

  /* 2. establish 实打（真·未配脚本 ⇒ verified=false）——反向断言 */
  await page.click('.sf-acts .btn:nth-child(1)');
  await sleep(1800);
  const est = await page.evaluate(() => {
    const res = document.querySelector('.sf-res.show');
    if (!res) return { shown: false };
    return {
      shown: true,
      cls: res.className,
      badge: (res.querySelector('.sf-badge') || {}).textContent,
      steps: [...res.querySelectorAll('.sf-step')].map(s => s.textContent),
      manualCount: res.querySelectorAll('.sf-manual li').length,
      manualFirst: (res.querySelector('.sf-manual li') || {}).textContent,
      greenOkBadge: !!res.querySelector('.sf-badge.ok') || /✔ 完成/.test((res.querySelector('.sf-badge')||{}).textContent||''),
      bodyHasMySuccessClaim: /状态：完成|操作成功/.test(document.body.innerText)
    };
  });
  /* 反向断言：pending 态不得有绿色 ✔ 完成徽章、不得有 .sf-step.ok */
  const reverse = await page.evaluate(() => {
    const res = document.querySelector('.sf-res.show.pending');
    if (!res) return { pass: false, why: 'no pending result' };
    const badge = (res.querySelector('.sf-badge') || {}).textContent || '';
    return {
      pass: !/完成|成功/.test(badge) && res.querySelectorAll('.sf-step.ok').length === 0 && !!res.className.match(/pending/),
      badge, okSteps: res.querySelectorAll('.sf-step.ok').length
    };
  });
  step('establish(真实false路径)', { ...est, reverse });
  await page.screenshot({ path: path.join(OUT, '02_establish待人工.png') });

  /* 3. 罐头 verified:true 正向对照 */
  await page.route(`**/api/workshop/${ws}/member/*/establish-session`, r => r.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ success: true, name: '罐头-会话名', steps: {}, verified: { '4_new_session': true, '8_rename_session': true },
      all_verified: true, manual: [], session_ref: { id: 'canned' }, note: '全部动作由脚本完成并自证' })
  }));
  await page.click('.sf-acts .btn:nth-child(1)');
  await sleep(1200);
  const canned = await page.evaluate(() => {
    const res = document.querySelector('.sf-res.show');
    return { cls: res.className, badge: (res.querySelector('.sf-badge') || {}).textContent,
      okSteps: res.querySelectorAll('.sf-step.ok').length };
  });
  step('establish(罐头true对照)', canned);
  await page.screenshot({ path: path.join(OUT, '03_establish自证.png') });
  await page.unroute(`**/api/workshop/${ws}/member/*/establish-session`);

  /* 4. deliver 实打（会写隔离数据目录里的隔间，属自建数据范畴） */
  await page.click('.sf-acts .btn:nth-child(2)');
  await sleep(2500);
  const del = await page.evaluate(() => {
    const res = document.querySelector('.sf-res.show');
    if (!res) return { shown: false };
    return {
      cls: res.className,
      badge: (res.querySelector('.sf-badge') || {}).textContent,
      hasText: !!res.querySelector('.sf-text'),
      hasPath: /text_path/.test(res.innerText),
      hasCopyBtn: [...res.querySelectorAll('button')].some(b => /复制投递文本/.test(b.textContent)),
      manualFirst: (res.querySelector('.sf-manual li') || {}).textContent || ''
    };
  });
  step('deliver', del);
  await page.screenshot({ path: path.join(OUT, '04_deliver.png') });

  /* 5. 窄视口（右栏在 1024 是否破版） */
  const probe = async (w, h) => {
    await page.setViewportSize({ width: w, height: h });
    await sleep(350);
    return await page.evaluate(() => ({ vw: window.innerWidth, overflow: document.documentElement.scrollWidth > window.innerWidth }));
  };
  const v = { v1600: await probe(1600, 1000), v1280: await probe(1280, 720), v1024: await probe(1024, 768) };
  step('视口', v);
  await page.setViewportSize({ width: 1600, height: 1000 });

  /* 6. 错误态：不存在的成员 */
  const errCase = await page.evaluate(o => {
    return (async () => {
      const r = await fetch(`${o.B}/api/workshop/${o.id}/member/nope/establish-session`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      return { status: r.status, body: await r.json() };
    })();
  }, { B: BASE, id: ws });
  step('错误态(业务404)', { status: errCase.status, err: errCase.body && errCase.body.error });

  /* 清数据 */
  await fetch(BASE + '/api/workshop/' + ws, { method: 'DELETE' });
  step('清理', { deleted: ws });

  const result = { errors, nonGet, render, est, reverse, canned, del, v, errCase };
  console.log('=== FINAL ===');
  console.log(JSON.stringify(result, null, 2));
  fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize(result), null, 2), 'utf8');
  await browser.close();
})().catch(e => { console.error('FATAL', e); process.exit(1); });
