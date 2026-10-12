/* FE-24 注册模板收纳展开 + 叫醒结果反馈验收（109/107 号；规则 16/17）
   隔离实例 + AC_FE_BASE + 自建自清 + AC_VERIFY_OUT
   ① 默认收纳（正文不可见、按钮区可见）② 展开/收起 + 两态复制载体一致
   ③ 收起态复制按钮可用 ④ wake 写后 GET 确认「已配：…」 ⑤ auto-connect wake_pending_restart 提示 */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');
const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, 'FE24_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', 'FE24_验证');
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
  const ctx = await browser.newContext({ permissions: ['clipboard-read', 'clipboard-write'],
    viewport: { width: 1600, height: 1200 } });
  const page = await ctx.newPage();
  const errors = [];
  const nonGet = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('request', r => { if (r.url().includes('/api/') && r.method() !== 'GET') nonGet.push(r.method() + ' ' + r.url().replace(BASE, '').split('?')[0]); });
  const step = (n, d) => console.log('>> ' + n, JSON.stringify(d));
  const fail = [];
  const shot = n => page.screenshot({ path: path.join(OUT, n) });

  /* 打桩（全部写请求；真实只读 catalog/list 保留） */
  await page.route('**/harness/*/wake', (route, request) => {
    if (request.method() === 'POST') return route.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ success: true, harness_id: '示例Harness-A',
        wrapper_path: 'D:\\虚拟目录\\wake_示例Harness-A.cmd', kind: 'cmd',
        command: '我的agent --wake', next: '现在跑一次自检确认它真的会被叫醒' }) });
    return route.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ success: true, configured: true, command: '我的agent --wake',
        status_text: '已配：收到任务会执行「我的agent --wake」来叫醒它',
        why: '平台猜不到你的 agent 怎么被拉起 —— 这一项只能由你提供' }) });
  });
  await page.route('**/api/harness/pre-register', route => route.fulfill({ status: 200,
    contentType: 'application/json', body: JSON.stringify({ success: true, harness_id: '示例Harness-A', agent_token: 'tok-demo' }) }));
  await page.route('**/api/harness/probe-register', route => route.fulfill({ status: 200,
    contentType: 'application/json', body: JSON.stringify({ success: true, harness_id: '示例Harness-A', wakeup_method: 'file_poll' }) }));
  await page.route('**/api/harness/auto-connect', route => route.fulfill({ status: 200,
    contentType: 'application/json', body: JSON.stringify({ success: true, status: 'connected',
      pid: 12345, bridge_type: 'file_poll', wake_pending_restart: true,
      note: '已有一只桥在看这个邮箱（pid=12345）；没有重复启动。但你配了「叫醒命令」，它要**桥重启后**才生效 —— 先停掉这只桥再调本端点，或在界面点该 harness 的「重新连接」。' }) }));

  /* 自建 harness（wake 表单下拉用） */
  const HID = 'harness-fe24-probe';
  await fetch(BASE + '/api/harness/register', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ harness_id: HID, harness_name: '示例Harness-A',
      acp_command: '示例执行文件.exe --noop', ai: { model_name: '示例模型' } }) }).catch(() => {});
  step('自建', { HID });

  try {
    await page.goto(BASE + '/register.html', { waitUntil: 'domcontentloaded', timeout: 25000 });
    await page.waitForSelector('#tplDetails', { timeout: 15000 });
    await sleep(800);

    /* ① 默认收纳 */
    const s1 = await page.evaluate(() => {
      const d=document.getElementById('tplDetails');
      const pre=document.getElementById('aiPrompt');
      const sum=document.querySelector('#tplDetails>summary');
      const btn=document.querySelector('#tplBar, #copyAll .btn'); /* 按钮在 details 外 */
      /* ★2026-10-12 修：原来按**按钮文案**找（`/复制接入教程/`），而主按钮文案后来改成了
         「📋 复制这一段给 AI（…）」⇒ 找不到 ⇒ `copyBtnOutside/copyBtnVisible` 双双为 false，
         **本检查一直红着**（更糟的是 sweep 的汇总有 bug，只有一条失败时汇总显示"全过"，
         所以没人发现 —— 一次假绿掩盖了另一次）。改用**稳定 id**，正是本文件自己的教训。 */
      const copyBtn=document.getElementById('copyPrimaryBtn');
      return {
        open: d ? d.open : null,
        preVisible: pre ? pre.checkVisibility() : false,
        summaryVisible: sum ? sum.getClientRects().length > 0 : false,
        copyBtnOutside: copyBtn ? !d.contains(copyBtn) : false,
        copyBtnVisible: copyBtn ? copyBtn.getClientRects().length > 0 : false
      };
    });
    await shot('01_默认收纳.png');
    step('① 默认态', s1);
    if (s1.open !== false) fail.push('① details 默认不该 open');
    if (s1.preVisible) fail.push('① 模板正文默认可见');
    if (!s1.summaryVisible) fail.push('① summary 不可见');
    if (!s1.copyBtnOutside) fail.push('① 复制按钮应在 details 之外');
    if (!s1.copyBtnVisible) fail.push('① 复制按钮不可见');

    /* ② 展开/收起 + 两态载体一致 */
    await page.click('#tplDetails>summary');
    await sleep(250);
    const s2a = await page.evaluate(() => {
      const d=document.getElementById('tplDetails');
      const t=document.getElementById('tplText');
      return { open:d.open, preVisible:document.getElementById('aiPrompt').checkVisibility(),
        len:t?t.textContent.length:0,
        hasWake:/怎么被「叫醒」/.test(t?t.textContent:''), hasSC:/self-check/.test(t?t.textContent:'') };
    });
    await shot('02_展开态.png');
    await page.click('#tplDetails>summary');
    await sleep(250);
    const s2b = await page.evaluate(() => {
      const d=document.getElementById('tplDetails');
      const t=document.getElementById('tplText');
      return { open:d.open, preVisible:document.getElementById('aiPrompt').checkVisibility(),
        len:t?t.textContent.length:0 };
    });
    await shot('02_收起态.png');
    step('② 展开/收起', { a: s2a, b: s2b });
    if (!s2a.open || !s2a.preVisible) fail.push('② 展开失败');
    if (s2b.open || s2b.preVisible) fail.push('② 收起失败');
    if (!s2a.hasWake || !s2a.hasSC) fail.push('② 模板关键内容缺失');
    if (s2a.len !== s2b.len || !s2a.len) fail.push('② 两态复制载体长度不一致: ' + s2a.len + ' vs ' + s2b.len);

    /* ③ 收起态点复制：按钮可用 + 反馈 + （若剪贴板可用）内容一致 */
    const readCopy = async () => {
      await page.click('#copyPrimaryBtn');
      await sleep(400);
      return page.evaluate(async () => {
        const source = document.getElementById('tplText').textContent;
        const clip = await navigator.clipboard.readText();
        const normalized = clip.replace(/\r\n/g, '\n');
        return { msg: (document.getElementById('msg')||{}).textContent||'',
          clipLen: clip.length, sourceLen: source.length,
          sourceLF: (source.match(/\n/g)||[]).length,
          clipboardCRLF: (clip.match(/\r\n/g)||[]).length,
          clipEq: normalized === source, normalized };
      });
    };
    const copyRes = await readCopy();
    await shot('03_收起态复制.png');
    step('③ 收起态复制', { msg: copyRes.msg, clipLen: copyRes.clipLen, clipEq: copyRes.clipEq });
    if (!/已复制第 1 段/.test(copyRes.msg)) fail.push('③ 收起态复制无成功反馈: ' + copyRes.msg);
    if (!copyRes.clipEq) fail.push('③ 收起态剪贴板内容与模板正文不一致');
    await page.click('#tplDetails>summary');
    const expandedCopy = await readCopy();
    if (!expandedCopy.clipEq || expandedCopy.normalized !== copyRes.normalized)
      fail.push('③ 展开态复制内容与收起态或正文不一致');
    step('③ 两态剪贴板换行', { sourceLen: copyRes.sourceLen, clipLen: copyRes.clipLen,
      sourceLF: copyRes.sourceLF, clipboardCRLF: copyRes.clipboardCRLF,
      expandedEq: expandedCopy.clipEq, statesEqual: expandedCopy.normalized === copyRes.normalized });
    await page.click('#tplDetails>summary');

    /* ④ wake 写后 GET 确认「已配：…」 */
    await page.waitForFunction(() => document.querySelectorAll('#bkCards button').length >= 4, null, { timeout: 15000 }).catch(() => {});
    await page.evaluate(() => {
      const btns=[...document.querySelectorAll('#bkCards button')];
      const hit=btns.find(b=>/文件夹邮箱|file_poll/.test(b.innerText));
      (hit||btns[0]).click();
    });
    await page.waitForSelector('#bkWakeCmd', { timeout: 15000 });
    await page.selectOption('#bkWakeHid', HID).catch(() => {});
    await page.fill('#bkWakeCmd', '我的agent --wake');
    await page.click('#bkWakeBtn');
    await page.waitForFunction(() => /📣 已配：收到任务会执行/.test((document.getElementById('bkWakeRes')||{}).innerText||''),
      null, { timeout: 10000 }).catch(() => {});
    const wk = await page.evaluate(() => (document.getElementById('bkWakeRes')||{}).innerText||'');
    await shot('04_wake已配反馈.png');
    step('④ wake 反馈', { head: wk.slice(0, 200) });
    if (!/📣 已配：收到任务会执行/.test(wk)) fail.push('④ 缺 GET 确认的「已配：…」行');

    /* ⑤ 一键注册 → wake_pending_restart 提示（三端点已打桩） */
    await page.click('#copyAll .btn:has-text("一键注册")');
    await page.waitForFunction(() => /等桥重启后才生效/.test((document.getElementById('oneClickResult')||{}).innerText||''),
      null, { timeout: 15000 }).catch(() => {});
    const oc = await page.evaluate(() => (document.getElementById('oneClickResult')||{}).innerText||'');
    await shot('05_pending_restart提示.png');
    step('⑤ pending_restart', { head: oc.slice(-260) });
    if (!/等桥重启后才生效/.test(oc)) fail.push('⑤ 缺 wake_pending_restart 提示');

    /* ⑥ 禁词 + 白名单 + Console */
    const bodyTxt = await page.evaluate(() => document.body.innerText);
    ['harness-小白龙', 'harness-测试甲', 'C:\\Users\\'].forEach(bad => {
      if (bodyTxt.includes(bad)) fail.push('⑥ 禁词「' + bad + '」');
    });
    const badReq = nonGet.filter(u =>
      !/\/harness\/[^/]+\/wake$/.test(u) &&
      !/\/api\/harness\/(pre-register|probe-register|auto-connect)$/.test(u));
    if (badReq.length) fail.push('非白名单写请求: ' + badReq.join(','));
    if (errors.length) fail.push('Console 红错 ' + errors.length);

    const out = { pass: fail.length === 0, fail, defaultState: s1,
                  snapEqual: s2a.len === s2b.len, copy: { msg: copyRes.msg, clipEq: copyRes.clipEq,
                     expandedEq: expandedCopy.clipEq, statesEqual: expandedCopy.normalized === copyRes.normalized,
                     sourceLen: copyRes.sourceLen, clipLen: copyRes.clipLen,
                     sourceLF: copyRes.sourceLF, clipboardCRLF: copyRes.clipboardCRLF },
                  wakeConfirmed: /📣 已配/.test(wk), pendingRestart: /等桥重启后才生效/.test(oc),
                  nonGet, errors, screenshots: fs.readdirSync(OUT) };
    fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize(out), null, 2), 'utf8');
    step('结论', { pass: out.pass, fail });
  } catch (e) {
    fail.push('FATAL: ' + (e && e.message || e));
    step('FATAL', fail[fail.length - 1]);
    fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize({ pass: false, fail, errors, nonGet }), null, 2), 'utf8');
  } finally {
    await fetch(BASE + '/api/harness/' + HID, { method: 'DELETE' }).catch(() => {});
    await browser.close();
  }
  process.exit(fail.length ? 1 : 0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
