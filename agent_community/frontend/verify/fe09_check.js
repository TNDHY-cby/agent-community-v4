/* FE-09 掉线抢救面板验收（隔离实例 + AC_FE_BASE + 自建自清；三条反向断言） */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');

const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, 'FE09_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', 'FE09_验证');
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

/* 罐头 offline-report 形态（按 34/36 号契约手工构造） */
function member(over){
  return Object.assign({
    member_id: 'm0', role: '码农', display_name: '示例Harness-B', harness_id: 'h1',
    verdict: 'idle', needs_rescue: false, pending_work: false,
    pending_work_why: '当前没有在进行的工作（空闲是正常态）',
    available: true, hard_reasons: [], soft_reasons: [], reasons: [],
    attempts: 0, max_attempts: 3, escalated: false, cooldown_left: 0,
    entry_state: 'dispatched', entry_reason: ''
  }, over || {});
}
function report(members, summary){
  return { success: true, workshop_id: 'x', max_attempts: 3, members,
    offline_count: members.filter(m => m.verdict === 'rescue').length,
    needs_rescue: members.filter(m => m.verdict === 'rescue').map(m => m.member_id),
    idle: members.filter(m => m.verdict === 'idle').map(m => m.member_id),
    escalated: members.filter(m => m.escalated).map(m => m.member_id),
    summary };
}
const CANS = {
  rescueHard: report([member({ verdict: 'rescue', needs_rescue: true, pending_work: true,
    pending_work_why: '', available: false,
    hard_reasons: ['桥进程已退出（没人取邮箱，确实收不到活）'],
    soft_reasons: ['harness 心跳失联（超时未上报；心跳在本项目中是可选的）'],
    reasons: ['桥进程已退出', '心跳失联'], attempts: 1, escalated: true, cooldown_left: 59 })],
    '1 名成员有活却不对劲，抢救中'),
  softOnly: report([member({ verdict: 'rescue', needs_rescue: true, pending_work: true,
    pending_work_why: '', available: true, hard_reasons: [],
    soft_reasons: ['harness 心跳失联（超时未上报；心跳在本项目中是可选的）'],
    reasons: ['心跳失联'], attempts: 1 })],
    '1 名成员有活却不对劲，抢救中'),
  err: { error: '工作间不存在' }
};
let mode = 'real';

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

  /* 0. 自建数据 */
  const ws = await (async () => {
    const r = await fetch(BASE + '/api/workshop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'FE09验收-自建', hall_content: 'FE-09 自建自清' }) });
    return (await r.json()).workshop_id;
  })();
  await fetch(BASE + `/api/workshop/${ws}/members`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ members: [{ role: '码农', harness_ids: ['harness-fe09-fake'], display_name: 'harness-fe09-fake' }] }) });
  step('自建', { ws });
  if (!ws) process.exit(1);

  /* FE-20 §5（契约性进入方式，非放水）：按 FE-17，成员状态面板是「④ 工作中」的阶段专属面板，
     草稿态工作间默认**不渲染**。本脚本注入 working 阶段样例让面板进入渲染，
     面板内容/抢救/反向断言等既有断言一概不动。 */
  await page.route('**/sidebar', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({
      success: true, workshop_id: ws, status: 'running',
      stage: { id: 'working', label: '工作中', index: 4, total: 5 },
      visible_actions: [{ id: 'rescue_scan', label: '🔄 全间抢救扫描', kind: 'primary',
        effects: '检查全间谁掉线了，能救的救、救不了的上报给你', endpoint: 'POST …/recover-scan' }],
      more_actions: [], blocked_reason: null, member_count: 1,
      note: 'FE-20 注入：④工作中（其余断言不变）'
    })
  }));

  /* A. 真实空闲态（反向断言①：idle 卡片内无「掉线」无红样式；载入链可能含自动讨论，放宽等待） */
  await page.goto(`${BASE}/workshop.html?ws_id=${ws}`, { waitUntil: 'domcontentloaded', timeout: 25000 });
  await page.waitForFunction(() => document.querySelectorAll('.of-card').length > 0, null, { timeout: 45000 }).catch(() => {});
  await sleep(500);
  const idleCard = await page.evaluate(() => {
    const panel = document.getElementById('offlinePanel');
    const card = document.querySelector('.of-card');
    const cardTxt = card ? card.innerText : '';
    return {
      shown: panel && panel.style.display !== 'none',
      summary: (document.getElementById('ofSummary') || {}).textContent,
      cardCls: card ? card.className : null,
      verdictLabel: (document.querySelector('.of-v') || {}).textContent,
      why: (document.querySelector('.of-why') || {}).textContent,
      hasRescueBtn: !!document.querySelector('.of-card .of-acts'),
      contains_掉线_inCard: /掉线/.test(cardTxt),
      hasRedHard: !!document.querySelector('.of-hard'),
      hasUndefined: /undefined/.test(cardTxt), hasNaN: /\bNaN\b/.test(cardTxt)
    };
  });
  step('A idle真实态', idleCard);
  await page.screenshot({ path: path.join(OUT, '01_idle中性.png') });

  /* A2. 真实扫描（反向断言②：acted 空 → 我方无「已抢救/抢救成功」） */
  await page.click('#ofScanBtn');
  await sleep(2200);
  const scan = await page.evaluate(() => {
    const box = document.getElementById('ofScan');
    const panel = document.getElementById('offlinePanel');
    return {
      shown: box && box.style.display !== 'none',
      text: box ? box.innerText.replace(/\n/g, ' | ') : '',
      panelTxt: panel ? panel.innerText : '',
      banned: /已抢救|抢救成功/.test(panel ? panel.innerText : '')
    };
  });
  step('A2 真实扫描', { shown: scan.shown, text: scan.text.slice(0, 260), banned: scan.banned });
  await page.screenshot({ path: path.join(OUT, '02_真实扫描未打扰.png') });

  /* B. 罐头 rescue+硬信号+escalated */
  await page.route('**/api/workshop/*/offline-report', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(CANS[mode]) }));
  await page.route('**/api/workshop/*/member/*/recover', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ success: true, member_id: 'm0', action: 'retry', attempts: 1,
      reasons: ['桥进程已退出'], revive: false, entry_state: 'dispatched', entry_reason: '' }) }));
  mode = 'rescueHard';
  await page.click('#ofRefreshBtn');
  await sleep(900);
  const rescue = await page.evaluate(() => {
    const card = document.querySelector('.of-card.v-rescue');
    if (!card) return { found: false };
    return {
      found: true,
      cls: card.className,
      vLabel: (card.querySelector('.of-v') || {}).textContent,
      escalated: [...card.querySelectorAll('.of-v')].some(e => e.textContent === '需要你处理'),
      hardTag: (card.querySelector('.of-hard') || {}).textContent,
      softInDetails: /软信号详情/.test(card.innerText) && /心跳在本项目中可选/.test(card.innerText),
      attempts: /尝试 1\/3/.test(card.innerText),
      cooldown: /冷却 59s/.test(card.innerText),
      hasBtn: [...card.querySelectorAll('button')].some(b => /对该成员抢救/.test(b.textContent))
    };
  });
  step('B rescue硬信号', rescue);
  /* recover 罐头 */
  await page.click('.of-card.v-rescue .of-acts .btn');
  await sleep(1000);
  const rec = await page.evaluate(() => {
    const res = document.querySelector('.of-card .sf-res.show');
    return res ? { badge: (res.querySelector('.sf-badge') || {}).textContent, cls: res.className } : { found: false };
  });
  step('B2 recover罐头', rec);
  await page.screenshot({ path: path.join(OUT, '03_rescue硬信号.png') });

  /* C. 软信号 only（反向断言③：无「联系不上」） */
  mode = 'softOnly';
  await page.click('#ofRefreshBtn');
  await sleep(900);
  const soft = await page.evaluate(() => {
    const card = document.querySelector('.of-card');
    return {
      hasHardTag: !!card.querySelector('.of-hard'),
      contains_联系不上: /联系不上/.test(card.innerText),
      softDetails: /软信号详情/.test(card.innerText)
    };
  });
  step('C 软信号only(反向③)', soft);
  await page.screenshot({ path: path.join(OUT, '04_软信号不报联系不上.png') });

  /* D. 错误态 */
  mode = 'err';
  await page.click('#ofRefreshBtn');
  await sleep(900);
  const errCase = await page.evaluate(() => ({
    err: (document.querySelector('#ofBody .sf-err') || {}).textContent
  }));
  step('D 错误态', errCase);

  /* E. 视口 */
  mode = 'real';
  await page.unroute('**/api/workshop/*/offline-report');
  const probe = async (w, h) => {
    await page.setViewportSize({ width: w, height: h });
    await sleep(350);
    return await page.evaluate(() => ({ vw: window.innerWidth, overflow: document.documentElement.scrollWidth > window.innerWidth }));
  };
  const v = { v1600: await probe(1600, 1000), v1280: await probe(1280, 720), v1024: await probe(1024, 768) };
  step('E 视口', v);

  /* 清理 */
  await fetch(BASE + '/api/workshop/' + ws, { method: 'DELETE' });
  step('清理', { deleted: ws });

  const result = { errors, nonGet, idleCard, scan, rescue, rec, soft, errCase, v };
  console.log('=== FINAL ===');
  console.log(JSON.stringify(result, null, 2));
  fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize(result), null, 2), 'utf8');
  await browser.close();
})().catch(e => { console.error('FATAL', e); process.exit(1); });
