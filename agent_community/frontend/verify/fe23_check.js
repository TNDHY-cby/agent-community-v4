/* FE-23 职业候选与孤儿成员提示验收（DSH 106 号；规则 19/20；规则 16/17）
   隔离实例 + AC_FE_BASE + 自建自清 + AC_VERIFY_OUT
   ① 职业下拉候选来自真 role-catalog（组/数量一致）+ 自由输入不被拦 + 提示行含旧叫法
   ② 真造孤儿（注册→建间→删 harness）：会话流卡 orphan 警示 + 横幅 headline + 去名单入口
   ③ FE-20 联动：非 select 阶段因孤儿放开名单；全程无成员删除请求（只提示不代删） */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');
const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, 'FE23_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', 'FE23_验证');
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
const BASE = process.env.AC_FE_BASE || 'http://127.0.0.1:18930';
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
  const errors = [];
  const nonGet = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('request', r => { if (r.url().includes('/api/') && r.method() !== 'GET') nonGet.push(r.method() + ' ' + r.url().replace(BASE, '').split('?')[0]); });
  const step = (n, d) => console.log('>> ' + n, JSON.stringify(d));
  const fail = [];
  const shot = n => page.screenshot({ path: path.join(OUT, n) });

  /* 讨论桩：不打真 AI、不推进阶段 */
  await page.route('**/api/workshop/*/discuss', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ reply: '（验收桩）收到，继续讨论。', status: 'discussing', stage: 'orchestrator', zone: 1 }) }));

  /* 真接口基准 */
  const rc = await fetch(BASE + '/api/role-catalog').then(r => r.json()).catch(() => ({}));
  const realCount = rc.count || 0;
  const realGroups = rc.groups ? Object.keys(rc.groups).length : 0;

  /* 真造孤儿：注册 → 建间带成员 → 删 harness */
  const HID = 'harness-fe23-probe';
  await fetch(BASE + '/api/harness/register', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ harness_id: HID, harness_name: '示例Harness-A',
      acp_command: '示例执行文件.exe --noop', ai: { model_name: '示例模型' } }) }).catch(() => {});
  const ws = await (async () => {
    const r = await fetch(BASE + '/api/workshop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'FE23验收-自建', hall_content: 'FE-23 孤儿与职业验收' }) });
    return (await r.json()).workshop_id;
  })();
  if (ws) await fetch(BASE + `/api/workshop/${ws}/members`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ members: [{ role: '组长', harness_ids: [HID], display_name: '示例Harness-A' }] }) });
  await fetch(BASE + '/api/harness/' + HID, { method: 'DELETE' }).catch(() => {});
  step('自建并制造孤儿', { ws, realCount, realGroups });

  try {
    await page.goto(`${BASE}/workshop.html?ws_id=${ws}`, { waitUntil: 'domcontentloaded', timeout: 25000 });
    await page.waitForSelector('#roleHint', { timeout: 15000 });
    /* 等真实条件：孤儿横幅亮起且 _orphanCount>0（门控随之放开名单）。
       不用固定 sleep、不吞超时——提示行由预取先出，不能当"loadWorkshop 走完"的信号 */
    await page.waitForFunction(() =>
      typeof _orphanCount !== 'undefined' && _orphanCount > 0 &&
      (() => { const s = document.getElementById('orphanStrip'); return s && s.style.display !== 'none'; })(),
      null, { timeout: 25000 });
    await sleep(400);

    /* ① 提示行（真目录） */
    const hint = await page.evaluate(() => (document.getElementById('roleHint') || {}).innerText || '');
    step('① 职业提示行', { head: hint.slice(0, 200) });
    if (!/职业自由填/.test(hint)) fail.push('① 提示行缺「职业自由填」');
    if (realCount && !hint.includes(String(realCount))) fail.push('① 候选数未反映真接口 count=' + realCount);
    if (!/画师 → 美术/.test(hint)) fail.push('① 缺旧叫法 画师→美术');
    if (!/推荐/.test(hint)) fail.push('① 缺 note「推荐」口径');

    /* ② 孤儿：会话流卡 + 横幅（先行断言——职业步的 syncMembers 会整单替换成员，可能把孤儿冲掉） */
    await page.waitForFunction(() => {
      const s=document.getElementById('sfBody');
      return s && /孤儿成员/.test(s.innerText||'');
    }, null, { timeout: 20000 }).catch(() => {});
    await page.waitForFunction(() => {
      const s=document.getElementById('orphanStrip');
      return s && s.style.display!=='none';
    }, null, { timeout: 15000 }).catch(() => {});
    const orphan = await page.evaluate(() => {
      const card=document.querySelector('.sf-orphan');
      const strip=document.getElementById('orphanStrip');
      const rb=document.getElementById('rosterBlock');
      return {
        cardTxt: card?card.innerText:'',
        stripShown: strip?strip.style.display!=='none':false,
        stripTxt: strip?(strip.innerText||''):'',
        rosterShown: rb?rb.style.display!=='none':false
      };
    });
    await shot('02_孤儿提示.png');
    step('② 孤儿', { card: orphan.cardTxt.slice(0, 180), strip: orphan.stripTxt.slice(0, 160), rosterShown: orphan.rosterShown });
    if (!/孤儿成员/.test(orphan.cardTxt)) fail.push('② 会话流卡缺孤儿警示');
    if (!/接不到/.test(orphan.cardTxt)) fail.push('② 卡缺 orphan_text 关键词');
    if (!/移除/.test(orphan.cardTxt)) fail.push('② 卡缺 orphan_advice');
    if (!orphan.stripShown || !/接不到活/.test(orphan.stripTxt)) fail.push('② 横幅未显示 headline');
    /* ③ FE-20 联动：非 select 阶段因孤儿放开名单 */
    if (!orphan.rosterShown) fail.push('③ 孤儿时名单仍被门控藏起（FE-20 联动失败）');
    /* 去名单入口 + 不代删 */
    const delBefore = nonGet.filter(u => /\/members$/.test(u) && u.startsWith('DELETE')).length;
    const goBtn = await page.evaluate(() => {
      const b=document.getElementById('osGoRoster');
      if(!b) return null;
      b.click();
      const rb=document.getElementById('rosterBlock');
      return { hasBtn: true, outline: rb?(rb.style.outline||''):'' };
    });
    await shot('03_去名单入口.png');
    step('③ 去名单入口', goBtn);
    if (!goBtn || !goBtn.hasBtn) fail.push('③ 缺去名单按钮');
    if (!/solid/.test(goBtn && goBtn.outline ? goBtn.outline : '')) fail.push('③ 点击未闪烁名单区');
    const delAfter = nonGet.filter(u => /DELETE.*\/members/.test(u)).length;
    if (delAfter > delBefore) fail.push('③ 出现了成员删除请求（只提示不代删）');

    /* ①b 职业下拉（本地 renderRoster 一行，不走 addMemberRow→syncMembers——那会整单替换、把孤儿成员冲掉） */
    await page.evaluate(async () => {
      const hs = await loadHarnesses();
      await renderRoster([{ member_id: 'mTest', role: '组长', harness_ids: ['harness-fe23-x'], display_name: '示例Harness-A' }], hs);
    });
    await sleep(400);
    const rows = await page.evaluate(() => document.querySelectorAll('#roster .row').length);
    if (rows < 1) fail.push('①b roster 行未创建');
    await page.click('#roster .row .role-btn');
    await sleep(300);
    const dd = await page.evaluate(() => {
      const list=document.querySelector('#roster .row .dd-list.open')||document.querySelector('#roster .row .dd-list');
      return {
        open: !!(list&&list.classList.contains('open')),
        groups: list?list.querySelectorAll('.dd-group').length:0,
        opts: list?list.querySelectorAll('.dd-opt[data-role]').length:0,
        hasCustom: !!(list&&list.querySelector('.role-custom')),
        note: list&&list.querySelector('.dd-note')?(list.querySelector('.dd-note').innerText||''):''
      };
    });
    step('①b 下拉', dd);
    if (!dd.open) fail.push('①b 下拉未展开');
    if (!dd.hasCustom) fail.push('①b 缺自由输入行');
    if (realCount && dd.opts !== realCount) fail.push('①b 候选数=' + dd.opts + ' ≠ 真接口 ' + realCount);
    if (realGroups && dd.groups !== realGroups) fail.push('①b 组数=' + dd.groups + ' ≠ ' + realGroups);
    if (!dd.note) fail.push('①b 下拉缺 note 原文');
    /* 自由输入不被拦（规则20：候选非白名单）；applyRole 会触发 syncMembers（既有产品行为，白名单内） */
    await page.fill('#roster .row .role-custom', '产品经理X');
    await page.press('#roster .row .role-custom', 'Enter');
    await sleep(600);
    const roleBtnTxt = await page.evaluate(() => (document.querySelector('#roster .row .role-btn')||{}).textContent||'');
    await shot('04_职业下拉与自由输入.png');
    step('①c 自由职业', { roleBtnTxt });
    if (!/产品经理X/.test(roleBtnTxt)) fail.push('①c 自由职业未被采用: ' + roleBtnTxt);

    /* ④ 视口 + 禁词 + 白名单 + Console */
    const vp = {};
    for (const [w, h, name] of [[1920,1080,'1920'],[1024,768,'1024']]) {
      await page.setViewportSize({ width: w, height: h });
      await sleep(350);
      vp[name] = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
      if (!vp[name]) fail.push(name + ' 横向破版');
    }
    const bodyTxt = await page.evaluate(() => document.body.innerText);
    ['harness-小白龙', 'harness-测试甲', 'C:\\Users\\'].forEach(bad => {
      if (bodyTxt.includes(bad)) fail.push('禁词「' + bad + '」');
    });
    const badReq = nonGet.filter(u =>
      !/\/api\/workshop\/[^/]+\/(members|discuss|select-members|confirm-members)$/.test(u));
    if (badReq.length) fail.push('非白名单写请求: ' + badReq.join(','));
    if (errors.length) fail.push('Console 红错 ' + errors.length);

    const out = { pass: fail.length === 0, fail, catalog: { count: realCount, groups: realGroups },
                  dd, roleFree: roleBtnTxt, orphan: { card: orphan.cardTxt.slice(0, 200),
                  strip: orphan.stripTxt.slice(0, 200), rosterShown: orphan.rosterShown },
                  viewports: vp, nonGet, errors, screenshots: fs.readdirSync(OUT) };
    fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize(out), null, 2), 'utf8');
    step('结论', { pass: out.pass, fail });
  } catch (e) {
    fail.push('FATAL: ' + (e && e.message || e));
    step('FATAL', fail[fail.length - 1]);
    fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize({ pass: false, fail, errors, nonGet }), null, 2), 'utf8');
  } finally {
    if (ws) await fetch(BASE + '/api/workshop/' + ws, { method: 'DELETE' }).catch(() => {});
    await fetch(BASE + '/api/harness/' + HID, { method: 'DELETE' }).catch(() => {});
    await browser.close();
  }
  process.exit(fail.length ? 1 : 0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
