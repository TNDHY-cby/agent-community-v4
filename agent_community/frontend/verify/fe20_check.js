/* FE-20 工作间右侧栏按阶段收敛验收（FE-17 §2/§4；规则 16/17）
   隔离实例 + AC_FE_BASE + 自建自清 + AC_VERIFY_OUT
   场景：活体真端点 → discuss1 → select → working → review(+blocked) → 500降级
   断言：门控「不渲染」、同屏主操作计数 ≤5、effects 文案、blocked 原文、降级=显示全部+提示 */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');
const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, 'FE20_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', 'FE20_验证');
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
const stageJson = (id, label, index, primary, more, blocked) => JSON.stringify({
  success: true, workshop_id: 'x', status: 'stub', stage: { id, label, index, total: 5 },
  visible_actions: primary, more_actions: more || [], blocked_reason: blocked || null,
  member_count: 1, note: 'FE-20 场景注入'
});
const act = (id, label) => ({ id, label, kind: 'primary', effects: '按了会发生：' + label + '（场景桩）', endpoint: 'POST …/' + id });

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const errors = [];
  const nonGet = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('request', r => { if (r.url().includes('/api/') && r.method() !== 'GET') nonGet.push(r.method() + ' ' + r.url().replace(BASE, '').split('?')[0]); });
  const step = (n, d) => console.log('>> ' + n, JSON.stringify(d));
  const fail = [];
  const shot = n => page.screenshot({ path: path.join(OUT, n) });

  /* 讨论桩：不打真 AI；回复刻意避开「需求已明确/分工已明确/任务已完成」等触发句（防误推进阶段） */
  await page.route('**/api/workshop/*/discuss', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ reply: '（验收桩）收到，继续讨论。', status: 'discussing',
      stage: 'orchestrator', zone: 1 }) }));

  /* FE-23 联动后测试数据要真实：harness 必须真注册——否则成员算「孤儿」，
     roster 会被 FE-23 §2 的孤儿联动强制放开，破坏各阶段门控断言 */
  const HID = 'harness-fe20-a';
  await fetch(BASE + '/api/harness/register', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ harness_id: HID, harness_name: '示例Harness-A',
      acp_command: '示例执行文件.exe --noop', ai: { model_name: '示例模型' } }) }).catch(() => {});

  const ws = await (async () => {
    const r = await fetch(BASE + '/api/workshop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'FE20验收-自建', hall_content: 'FE-20 阶段收敛验收' }) });
    return (await r.json()).workshop_id;
  })();
  if (!ws) process.exit(1);
  await fetch(BASE + `/api/workshop/${ws}/members`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ members: [{ role: '码农', harness_ids: [HID], display_name: '示例Harness-A' }] }) });
  step('自建', { ws });

  const readState = () => page.evaluate(() => {
    const g = id => (document.getElementById(id) || {}).style ? document.getElementById(id).style.display : 'NO';
    const note = document.getElementById('sbNote');
    const blk = document.getElementById('sbBlocked');
    const inDet = el => !!(el && el.closest('details'));
    let n = 0; const names = [];
    ['.sb-act', '#selectBtn', '#addMemberBtn', '#saveMembersBtn', '#confirmBtn', '#ofScanBtn'].forEach(sel => {
      document.querySelectorAll(sel).forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0 && !inDet(el)) { n++; names.push((el.id || el.textContent || '').trim().slice(0, 20)); }
      });
    });
    const prim = [...document.querySelectorAll('#sbPrimary .sb-act')];
    return {
      stage: (document.getElementById('sbStage') || {}).textContent,
      idx: (document.getElementById('sbStageIdx') || {}).textContent,
      primary: prim.length,
      primaryNoEffects: prim.filter(b => !(b.querySelector('span') && b.querySelector('span').textContent.trim())).length,
      moreCount: document.querySelectorAll('#sbMore .sb-act').length,
      moreVisible: (document.getElementById('sbMoreBox') || {}).style ? document.getElementById('sbMoreBox').style.display !== 'none' : false,
      roster: g('rosterBlock'), harness: g('harnessBlock'), offline: g('offlinePanel'),
      fab: (document.getElementById('interjectFab') || { style: {} }).style.display,
      sBtn: g('selectBtn'), aBtn: g('addMemberBtn'), cfBtn: g('confirmBtn'),
      noteShown: note ? note.style.display !== 'none' : false,
      noteText: note ? note.textContent : '',
      blockedShown: blk ? blk.style.display !== 'none' : false,
      blockedText: blk ? blk.textContent : '',
      actsN: n, actNames: names
    };
  });

  try {
    /* 1. 活体真端点 */
    await page.goto(`${BASE}/workshop.html?ws_id=${ws}`, { waitUntil: 'domcontentloaded', timeout: 25000 });
    await page.waitForFunction(() => {
      const b = document.getElementById('stageBar');
      return b && b.style.display !== 'none';
    }, null, { timeout: 30000 });
    await sleep(800);
    const live = await readState();
    await shot('01_活体.png');
    step('1 活体', live);
    if (!live.stage || live.stage === '（阶段未取到）' || live.stage === '—') fail.push('1 活体 stage 未渲染: ' + live.stage);
    if (live.primary < 1 || live.primary > 5) fail.push('1 活体主操作数=' + live.primary);
    if (live.primaryNoEffects) fail.push('1 有主操作缺 effects 文案');
    if (live.noteShown) fail.push('1 活体不该显示降级提示');

    /* 场景路由：此后 sidebar 全部走注入 */
    let sbBody = null;
    await page.route('**/sidebar', route => {
      if (sbBody === 'ERR') return route.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"场景注入故障"}' });
      return route.fulfill({ status: 200, contentType: 'application/json', body: sbBody });
    });
    const runScene = async (name, body) => {
      sbBody = body;
      await page.evaluate(() => window.__stageBar.reload());
      await sleep(400);
      const s = await readState();
      step(name, s);
      return s;
    };

    /* 2. discuss1：阶段专属面板全部不渲染 */
    const s1 = await runScene('2 discuss1', stageJson('discuss1', '一级讨论', 1,
      [act('discuss', '发送'), act('finish_discuss', '结束讨论，选定员工 →')],
      [act('leader_check', '📋 组长检查员工状态'), act('register_resource', '⬆️ 登记到资源库'), act('activate_template', '📋 手动激活模板')]));
    await shot('02_discuss1.png');
    if (s1.primary !== 2) fail.push('2 discuss1 主操作=' + s1.primary);
    if (s1.roster !== 'none') fail.push('2 discuss1 rosterBlock 未隐藏');
    if (s1.harness !== 'none') fail.push('2 discuss1 harnessBlock 未隐藏');
    if (s1.offline !== 'none') fail.push('2 discuss1 抢救面板未隐藏');
    if (s1.fab !== 'none') fail.push('2 discuss1 插话钮未隐藏');
    if (!s1.moreVisible || s1.moreCount !== 3) fail.push('2 discuss1 更多⋯异常: ' + s1.moreCount);
    if (s1.actsN > 5) fail.push('2 discuss1 计数=' + s1.actsN + ' >5');

    /* 3. select：名单+Harness 可见、旧四按钮由 stageBar 接管、计数 ≤5 */
    const s2 = await runScene('3 select', stageJson('select', '选定员工', 2,
      [act('select_members', '让平台 AI 选定员工'), act('add_member', '+ 添加职位 / harness'),
       act('save_roster', '保存名单'), act('confirm_members', '确认名单，进入二级讨论')],
      [act('rescue_scan', '🔄 全间抢救扫描'), act('leader_check', '📋 组长检查员工状态')]));
    await shot('03_select.png');
    if (s2.primary !== 4) fail.push('3 select 主操作=' + s2.primary);
    if (s2.roster !== '') fail.push('3 select rosterBlock 未显示');
    if (s2.harness !== '') fail.push('3 select harnessBlock 未显示');
    if (s2.offline !== 'none') fail.push('3 select 抢救面板不该显示');
    if (s2.sBtn !== 'none' || s2.aBtn !== 'none' || s2.cfBtn !== 'none') fail.push('3 select 旧按钮未被接管(去重失败)');
    if (s2.actsN > 5) fail.push('3 select 计数=' + s2.actsN + ' >5');

    /* 4. working：抢救面板+插话钮可见、计数 ≤5 */
    const s3 = await runScene('4 working', stageJson('working', '工作中', 4,
      [act('rescue_scan', '🔄 全间抢救扫描'), act('leader_check', '📋 组长检查员工状态'), act('integrate', '📦 收口整合产出')],
      [act('register_resource', '⬆️ 登记到资源库'), act('activate_template', '📋 手动激活模板')]));
    await shot('04_working.png');
    if (s3.primary !== 3) fail.push('4 working 主操作=' + s3.primary);
    if (s3.offline !== '') fail.push('4 working 抢救面板未显示');
    if (s3.fab === 'none') fail.push('4 working 插话钮未显示');
    if (s3.roster !== 'none') fail.push('4 working rosterBlock 不该显示');
    if (s3.actsN > 5) fail.push('4 working 计数=' + s3.actsN + ' >5');
    if (!s3.actNames.some(x => /整合/.test(x))) fail.push('4 working 缺 integrate 主操作');

    /* 5. review + blocked_reason 原文 */
    const s4 = await runScene('5 review+blocked', stageJson('review', '三级讨论（收口）', 5,
      [act('third_discuss', '📋 进行三级讨论'), act('continue_work', '▶ 继续工作'), act('complete_task', '✅ 完成任务')],
      [act('register_resource', '⬆️ 登记到资源库')],
      '该工作间被标记为受阻。先看「组长检查员工状态」或运维看板，确认卡在哪一步，再决定继续还是收口。'));
    await shot('05_review_blocked.png');
    if (s4.primary !== 3) fail.push('5 review 主操作=' + s4.primary);
    if (s4.fab === 'none') fail.push('5 review 插话钮未显示');
    if (!s4.blockedShown || !/受阻/.test(s4.blockedText)) fail.push('5 blocked_reason 未原文显示');
    if (s4.actsN > 5) fail.push('5 review 计数=' + s4.actsN + ' >5');

    /* 6. 500 降级：提示 + 显示全部 */
    const errIdx = errors.length;
    const s5 = await runScene('6 降级500', 'ERR');
    errors.splice(errIdx);   /* 500 的资源报错是场景预期，不计 Console 红错 */
    await shot('06_降级500.png');
    if (!s5.noteShown || !/不隐藏任何入口/.test(s5.noteText)) fail.push('6 降级提示缺失');
    if (s5.roster === 'none' || s5.harness === 'none' || s5.offline === 'none' || s5.fab === 'none')
      fail.push('6 降级没有显示全部: ' + JSON.stringify({ r: s5.roster, h: s5.harness, o: s5.offline, f: s5.fab }));
    if (s5.sBtn === 'none' || s5.aBtn === 'none' || s5.cfBtn === 'none') fail.push('6 降级旧按钮被藏');

    /* 7. 三档视口 + Console + 零写请求（本页 sidebar/discuss 全 GET/桩） */
    const vp = {};
    for (const [w, h, name] of [[1920,1080,'1920'],[1280,720,'1280'],[1024,768,'1024']]) {
      await page.setViewportSize({ width: w, height: h });
      await sleep(350);
      vp[name] = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
      await shot('07_' + name + '.png');
      if (!vp[name]) fail.push(name + ' 横向破版');
    }
    step('7 视口', vp);
    const badReq = nonGet.filter(u => !/\/api\/workshop\/[^/]+\/(discuss|channel\/retire|channel\/establish|library\/update|select-members|members|confirm-members|start|recover-scan|leader-status|resources|review|continue|complete|integrate|halt-and-reset)/.test(u));
    if (badReq.length) fail.push('非白名单写请求: ' + badReq.join(','));
    if (errors.length) fail.push('Console 红错 ' + errors.length);

    const out = { pass: fail.length === 0, fail, live: { stage: live.stage, primary: live.primary, actsN: live.actsN },
                  scenes: { discuss1: { p: s1.primary, n: s1.actsN }, select: { p: s2.primary, n: s2.actsN },
                            working: { p: s3.primary, n: s3.actsN }, review: { p: s4.primary, blocked: s4.blockedShown },
                            fallback: s5.noteShown },
                  viewports: vp, nonGet, errors, screenshots: fs.readdirSync(OUT) };
    fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize(out), null, 2), 'utf8');
    step('结论', { pass: out.pass, fail });
  } catch (e) {
    fail.push('FATAL: ' + (e && e.message || e));
    step('FATAL', fail[fail.length - 1]);
    fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize({ pass: false, fail, errors, nonGet }), null, 2), 'utf8');
  } finally {
    await fetch(BASE + '/api/workshop/' + ws, { method: 'DELETE' }).catch(() => {});
    await fetch(BASE + '/api/harness/' + HID, { method: 'DELETE' }).catch(() => {});
    await browser.close();
  }
  process.exit(fail.length ? 1 : 0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
