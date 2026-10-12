/* FE-21 接入话术纠正稿落地验收（V39 → register.html；规则 19/20；规则 16/17）
   隔离实例 + AC_FE_BASE + 自建自清 + AC_VERIFY_OUT
   断言：① 话术三段落位（四选一/三步自检/起桥恢复+编码坑）② 禁词（规则19+铁律6）
   ③ 未送达 N 列（delivery-status 打桩）④ 自检四态灯（self-check 打桩）⑤ Console 零红错 */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');
const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, 'FE21_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', 'FE21_验证');
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
  const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });
  const errors = [];
  const nonGet = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('request', r => { if (r.url().includes('/api/') && r.method() !== 'GET') nonGet.push(r.method() + ' ' + r.url().replace(BASE, '').split('?')[0]); });
  const step = (n, d) => console.log('>> ' + n, JSON.stringify(d));
  const fail = [];
  const shot = n => page.screenshot({ path: path.join(OUT, n) });

  /* 打桩：delivery-status（未送达 4）与 self-check（no_reply 四态灯） */
  await page.route('**/delivery-status', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ workshop_id: 'x', member_count: 1, stuck_count: 1, total_pending: 4,
      headline: '有 1 个成员的消息没送到（共 4 条积压）',
      members: [{ member_id: 'm0', harness_id: 'harness-fe21-probe', inbox_pending: 4,
        stuck: true, stuck_reason: '还有 4 条消息压在邮箱里', advice: '桥没有在运行', status_text: '还有 4 条消息压在邮箱里' }] }) }));
  await page.route('**/self-check', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ state: 'no_reply', ok: false, probe_id: 'probe-fe21',
      advice: '桥在跑、消息送到了，卡在 agent 侧：多半没配「叫醒命令」——用 wake.cmd 包一层再自检',
      workshop_id: 'x', member_id: 'm0', harness_id: 'harness-fe21-probe' }) }));

  /* 自建：harness + 工作间 + 成员（自建自清） */
  const HID = 'harness-fe21-probe';
  const reg = await fetch(BASE + '/api/harness/register', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ harness_id: HID, harness_name: '示例Harness-A',
      acp_command: '示例执行文件.exe --noop', ai: { model_name: '示例模型' } }) }).then(r => r.json()).catch(e => ({ error: String(e) }));
  const ws = await (async () => {
    const r = await fetch(BASE + '/api/workshop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'FE21验收-自建', hall_content: 'FE-21 话术验收' }) });
    return (await r.json()).workshop_id;
  })();
  if (ws) await fetch(BASE + `/api/workshop/${ws}/members`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ members: [{ role: '码农', harness_ids: [HID], display_name: '示例Harness-A' }] }) });
  step('自建', { ws, reg: reg.success !== false });

  try {
    await page.goto(BASE + '/register.html', { waitUntil: 'domcontentloaded', timeout: 25000 });
    /* FE-24 收纳后模板默认收起 —— 只改进入方式：先展开再读（断言原样，同 fe09 先例） */
    await page.waitForSelector('#tplDetails', { timeout: 15000 });
    await page.evaluate(() => { document.getElementById('tplDetails').open = true; });
    await page.waitForSelector('#aiPrompt', { timeout: 15000 });
    await sleep(1200);

    /* ① 话术三段落位 */
    const promptTxt = await page.evaluate(() => document.getElementById('aiPrompt').innerText);
    /* ★2026-10-11 更新并加强（DSH 改的模板，同步改断言——不是放宽，是跟着真相走）：
       原话术只说"问了怎么叫醒"，**却没让 AI 把那条命令交给平台** ⇒ 收集到了也无处安放
       ⇒ 注册完叫醒命令还是空的（老板"唤不醒"的病根）。
       现在改成**四步**，第 2 步就是 `POST …/wake`。⇒ 断言据此：
       · 必须出现「四步接入」与「/wake」（这一环是本次修复的核心）；
       · **不得**再出现旧的「三步接入」（留旧文案 = 两处口径打架）； */
    /* ★2026-10-11 再更新（同一天第二轮）：模板当天又改了一次 ——
       原问法是让**用户**回答「怎么被叫醒」，实测**用户答不上来**、外端 agent 反过来问他
       （某**桌面型**外端 agent 那次就卡在这里，还回了"我什么接口都没有"；
        源码/对外文字只写形态，不写具体产品名 —— 规则 6）。
       ⇒ 现在改成让 **agent 先自测**（四项动手自测 + 测不出来选 E，平台如实说明差哪一步）。
       另外把当天修的两个真坑也写进模板：**别把桥型名当 wakeup_method**（照填会被 400 拒）。
       断言跟着新口径走，**不是放宽**：仍然要求四步接入 / `/wake` 顺序 / 编码坑 / wake.cmd。 */
    /* ★2026-10-12 再更新（**规则 20：界面文案与验收脚本必须同改**）：
       真机实测发现，话术只到第 4 步（自检）就结束 ⇒ 新 agent 拿到"配置可行"就**停住**，
       永远不知道还要**把桥常驻跑起来**才能真正接到活（平台派的活只会躺在队列里）。
       ⇒ 模板补了**第 5 步**，本断言同步改为要求「五步接入」+ 第 5 步那几个关键点。
       ⚠️注意这条正是我上一次改文案时踩到的：**改文案不同步改断言 ⇒ 前端验收直接红**。 */
    const needPrompt = ['先自己测', '不要先问人', 'cli_acp', 'file_poll', 'http_api', 'clipboard',
      '五步接入', '/wake', 'wrapper_path', 'self-check', 'not_picked', 'no_reply',
      'PYTHONIOENCODING=utf-8', 'wake.cmd', 'unrecognized arguments',
      'wakeup_method',
      /* 第 5 步（持久跑桥）与"验的是什么范围"—— 这两组是 2026-10-12 新增的必须项 */
      'bridge/generate', 'launch_hint', '常驻', 'verified_scope', 'delivered_confirmed'];
    needPrompt.forEach(k => { if (!promptTxt.includes(k)) fail.push('① aiPrompt 缺「' + k + '」'); });
    if (promptTxt.includes('三步接入')) fail.push('① aiPrompt 仍写「三步接入」（旧口径残留）');
    if (promptTxt.includes('四步接入')) fail.push('① aiPrompt 仍写「四步接入」（已补第 5 步，须与五步一致）');
    /* 顺序也要对：桥是"起"的时候才带上叫醒命令 ⇒ 必须先配 wake 再 auto-connect */
    const iWake = promptTxt.indexOf('/wake');
    const iConn = promptTxt.indexOf('auto-connect');
    if (iWake < 0 || iConn < 0 || !(iWake < iConn)) {
      fail.push('① 话术未把「先配叫醒、再起桥」的顺序说对（/wake 应出现在 auto-connect 之前）');
    }
    /* ⚠️2026-10-11：这里原先直接读 `innerText` —— 但第 2 段现在**默认折叠**
       （老板要求：它已降级为参考，注册后不必再复制），而 `innerText` 对
       **不可见元素返回空串** ⇒ 断言误报"架桥速查缺 XXX"。
       改法：**先展开该折叠区再读**（既真实渲染，又保持"看得见的文本"这个语义）。
       不是放宽：文本内容一个字都没少（同一处仍可读，见下方长度断言）。 */
    const bridgeTxt = await page.evaluate(() => {
      const el = document.getElementById('copyBridge');
      if (!el) return '';
      const d = el.closest('details');
      if (d) d.open = true;
      return el.innerText || el.textContent || '';
    });
    ['PYTHONIOENCODING=utf-8', 'wake.cmd', 'unrecognized arguments'].forEach(k => {
      if (!bridgeTxt.includes(k)) fail.push('② 架桥速查缺「' + k + '」');
    });
    await shot('01_话术.png');
    step('① 话术落位', { prompt: promptTxt.length, bridge: bridgeTxt.length });

    /* ③ 未送达 N（delivery-status 打桩） */
    await page.waitForFunction(() => /未送达 4/.test((document.getElementById('list') || {}).innerText || ''),
      null, { timeout: 15000 }).catch(() => {});
    const listTxt = await page.evaluate(() => (document.getElementById('list') || {}).innerText || '');
    await shot('02_未送达.png');
    step('③ 未送达列', { hit: /未送达 4/.test(listTxt) });
    if (!/未送达 4/.test(listTxt)) fail.push('③ 清单未显示「未送达 4」: ' + listTxt.slice(0, 160));

    /* ④ 自检灯（self-check 打桩 no_reply） */
    await page.selectOption('#scWs', ws);
    await sleep(600);
    await page.selectOption('#scMember', 'm0');
    await page.click('#scBtn');
    await page.waitForFunction(() => {
      const b = document.getElementById('scResult');
      return b && b.style.display !== 'none' && /没回话/.test(b.innerText);
    }, null, { timeout: 20000 }).catch(() => {});
    const scTxt = await page.evaluate(() => (document.getElementById('scResult') || {}).innerText || '');
    await shot('03_自检灯.png');
    step('④ 自检灯', { head: scTxt.slice(0, 200) });
    if (!/没回话/.test(scTxt)) fail.push('④ 四态灯未显示 no_reply 标题');
    if (!/wake\.cmd 包一层/.test(scTxt)) fail.push('④ advice 原文未显示');

    /* ② 禁词（规则 19 + 铁律 6） */
    const bodyTxt = await page.evaluate(() => document.body.innerText);
    ['harness-小白龙', 'harness-测试甲', 'harness-测试乙', 'harness-测试丙', 'C:\\Users\\'].forEach(bad => {
      if (bodyTxt.includes(bad)) fail.push('② 出现禁词/本机路径「' + bad + '」');
    });

    /* ⑤ Console + 写白名单（页面仅 self-check 一个写请求） */
    const badReq = nonGet.filter(u => !/\/member\/[^/]+\/self-check$/.test(u));
    if (badReq.length) fail.push('非白名单写请求: ' + badReq.join(','));
    if (errors.length) fail.push('Console 红错 ' + errors.length);

    const out = { pass: fail.length === 0, fail, promptLen: promptTxt.length,
                  undelivered: /未送达 4/.test(listTxt), scHead: scTxt.split('\n')[0] || '',
                  nonGet, errors, screenshots: fs.readdirSync(OUT) };
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
