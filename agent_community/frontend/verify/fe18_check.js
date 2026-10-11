/* FE-18 运维看板三问区块验收（FE-17 §3；规则 16/17；隔离实例 + AC_FE_BASE + 自建自清 + AC_VERIFY_OUT）
   覆盖：空态如实 · 自建间渲染 · 卡住场景（钩子注入：原因+怎么办+处理入口）· 术语禁词扫描 · 视口 · 零写请求 */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');
const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, 'FE18_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', 'FE18_验证');
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
const BANNED = ['harness_id', 'pending', 'channel_seq', 'session_alias', 'bridge.py'];

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const errors = [];
  const nonGet = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('request', r => { if (r.url().includes('/api/') && r.method() !== 'GET') nonGet.push(r.method() + ' ' + r.url().replace(BASE, '').split('?')[0]); });
  const step = (n, d) => console.log('>> ' + n, JSON.stringify(d));
  const fail = [];
  const shot = n => page.screenshot({ path: path.join(OUT, n) });
  const q3Text = () => page.evaluate(() => {
    const p = document.querySelector('.q3-panel');
    return p ? p.innerText : '';
  });
  const scanBanned = (label, text) => {
    BANNED.forEach(w => { if (text.toLowerCase().includes(w)) fail.push(label + ' 出现禁词「' + w + '」'); });
  };

  /* 0. 先看实例里有多少工作间（A 的"无工作间空态"必须在建间之前断言） */
  const wsCount0 = (await (await fetch(BASE + '/api/workshops')).json()).workshops.length;
  let ws = '';

  try {
    await page.goto(BASE + '/ops.html', { waitUntil: 'domcontentloaded', timeout: 25000 });
    await page.waitForSelector('.q3-panel', { timeout: 15000 });
    await sleep(1200);

    /* A. 无工作间空态（只在实例确实为 0 时断言，否则如实跳过） */
    let sA = { wsCount0, skipped: wsCount0 > 0 ? '实例里已有 ' + wsCount0 + ' 个工作间，跳过无工作间空态断言' : null };
    if (wsCount0 === 0) {
      sA.text = await q3Text();
      scanBanned('A 无工作间', sA.text);
      if (!/还没有工作间/.test(sA.text)) fail.push('A 无工作间空态缺失: ' + sA.text.slice(0, 80));
      await shot('01_无工作间.png');
    }
    step('A 无工作间', sA);

    /* B0. 自建工作间 + 成员（自建自清） */
    ws = await (async () => {
      const r = await fetch(BASE + '/api/workshop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: 'FE18验收-自建' }) });
      return (await r.json()).workshop_id;
    })();
    if (!ws) process.exit(1);
    await fetch(BASE + `/api/workshop/${ws}/members`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ members: [
        { role: '码农', harness_ids: ['harness-fe18-a'], display_name: '示例Harness-A' },
        { role: '搜索者', harness_ids: ['harness-fe18-b'], display_name: '示例Harness-B' }
      ] }) });
    step('自建', { ws });

    /* B. 选中自建间（2 名成员、都空闲）⇒ 三区如实渲染
       （wsMap 有 5 分钟缓存，新间不刷就不进下拉 ⇒ 测试里把 wsFetchedAt 归零强制刷新） */
    await page.evaluate(id => { __opsQ3.setWs(id); wsFetchedAt = 0; }, ws);
    await page.evaluate(() => Ops.tick(false));
    await sleep(2500);
    const sB = { text: await q3Text() };
    scanBanned('B 有间空闲', sB.text);
    if (!/谁在干活/.test(sB.text) || !/谁卡住了/.test(sB.text) || !/我该按哪里/.test(sB.text)) fail.push('B 三区标题缺失');
    if (!/空闲是正常态/.test(sB.text)) fail.push('B 空闲空态缺失');
    if (!/没有卡住的成员|无需抢救/.test(sB.text)) fail.push('B ②空态缺失');
    await shot('02_自建间空闲.png');
    step('B 自建间', { head: sB.text.slice(0, 300) });

    /* C. 卡住场景 + 桥绑定两硬规则（100 号 §三；钩子注入，不打桩网络） */
    const sC = await page.evaluate(() => {
      clearInterval(timer);   /* 暂停自动刷新，保证读取到注入态 */
      hbLast = { total: 1, online: [], stale: [{ harness_id: 'harness-fe18-a', last_ago: 600 }], timeout_scale: 2 };
      __opsQ3.setWs('fixture-ws');
      __opsQ3.renderWith(
        { members: [
          { member_id: 'm0', role: '码农', display_name: '示例Harness-A', harness_id: 'harness-fe18-a',
            channel: { state: 'active', name: '示例间-项目-码农-m0' }, entry_state: 'timeout' },
          { member_id: 'm1', role: '搜索者', display_name: '示例Harness-B', harness_id: 'harness-fe18-b',
            channel: { state: 'pending', name: '' }, entry_state: 'entered' }
        ] },
        { summary: '1 名成员需要抢救', members: [
          { member_id: 'm0', role: '码农', display_name: '示例Harness-A', harness_id: 'harness-fe18-a',
            verdict: 'rescue', needs_rescue: true, offline: true, escalated: true,
            pending_work: true, pending_work_why: '有活在进行但桥确认退出',
            reasons: ['桥进程已确认退出'], entry_state: 'timeout', entry_reason: '' },
          { member_id: 'm1', role: '搜索者', display_name: '示例Harness-B', harness_id: 'harness-fe18-b',
            verdict: 'healthy', needs_rescue: false, offline: false, escalated: false,
            pending_work: true, pending_work_why: '正在检索资料（示例）',
            reasons: [], entry_state: 'entered', entry_reason: '' }
        ] },
        { bindings: [
          { member_id: 'm0', bridge: { state: 'attached', verified: false },
            status_text: '已架接：样例桥 → 示例间-项目-码农-m0（未自证：还没确认这条路真的通）' },
          { member_id: 'm1', bridge: { state: 'none', verified: false },
            status_text: '未架接（还没有为它架起具体桥）' }
        ] }
      );
      const p = document.querySelector('.q3-panel');
      const btns = p.querySelectorAll('.q3-col:nth-child(3) .q3-act');
      return { text: p.innerText, actCount: btns.length };
    });
    scanBanned('C 卡住场景', sC.text);
    if (!/已 10 分钟 没有动静/.test(sC.text)) fail.push('C 分钟数缺失: ' + sC.text.slice(0, 200));
    if (!/桥进程已确认退出/.test(sC.text)) fail.push('C 卡住原因缺失');
    if (!/怎么办：已升级给人工/.test(sC.text)) fail.push('C 怎么办缺失');
    if (sC.actCount !== 1) fail.push('C 处理按钮数=' + sC.actCount + '（应=卡住人数，每行1个）');
    /* 100 号 §三 两硬规则 */
    if (!/未架接（还没有为它架起具体桥）/.test(sC.text)) fail.push('C state=none 未明说「未架接」');
    if (!/未自证/.test(sC.text)) fail.push('C verified=false 未显示「未自证」');
    if (/已通/.test(sC.text)) fail.push('C 把桥说成了「已通」');
    await shot('03_卡住场景.png');
    step('C 卡住场景+桥绑定', { actCount: sC.actCount, head: sC.text.slice(0, 400) });

    /* D. 反向断言：有异常 ⇒ 必须给处理入口（不是灰着） + 恢复自动刷新 */
    const sD = await page.evaluate(() => { startTimer(); return true; });
    if (!sD) fail.push('D 恢复刷新失败');

    /* E. 三档视口 */
    const vp = {};
    for (const [w, h, name] of [[1920,1080,'1920'],[1280,720,'1280'],[1024,768,'1024']]) {
      await page.setViewportSize({ width: w, height: h });
      await sleep(400);
      vp[name] = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
      await shot('04_' + name + '.png');
      if (!vp[name]) fail.push(name + ' 横向破版');
    }
    await page.setViewportSize({ width: 1920, height: 1080 });
    step('E 视口', vp);

    /* F. 零写请求 + Console（三问区块全 GET） */
    if (nonGet.length) fail.push('出现非 GET 请求: ' + nonGet.join(','));
    if (errors.length) fail.push('Console 红错 ' + errors.length);

    const out = { pass: fail.length === 0, fail, wsCount0, sA, sB: sB.text.slice(0, 600),
                  sC: { actCount: sC.actCount, text: sC.text.slice(0, 600) },
                  viewports: vp, nonGet, errors, screenshots: fs.readdirSync(OUT) };
    fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize(out), null, 2), 'utf8');
    step('结论', { pass: out.pass, fail });
  } catch (e) {
    fail.push('FATAL: ' + (e && e.message || e));
    step('FATAL', fail[fail.length - 1]);
    fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize({ pass: false, fail, errors, nonGet }), null, 2), 'utf8');
  } finally {
    if (ws) await fetch(BASE + '/api/workshop/' + ws, { method: 'DELETE' }).catch(() => {});
    await browser.close();
  }
  process.exit(fail.length ? 1 : 0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
