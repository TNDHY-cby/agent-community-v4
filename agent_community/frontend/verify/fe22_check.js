/* FE-22 叫醒闭环与桥型选型验收（通道 105 号 §四/§五；规则 19/20；规则 16/17）
   隔离实例 + AC_FE_BASE + 自建自清 + AC_VERIFY_OUT
   断言：① catalog 4 卡真打 + 第 2 步字段来自响应（非手抄）② 清单 wake 列
   ③ wake POST 打桩显示 wrapper_path/next 原文 ④ 自检 no_reply 回跳锚
   ⑤ 反向：页面无拼好引号的 --trigger "带空格命令" 模式 ⑥ 禁词 0 / 写白名单 / Console 0 */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');
const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, 'FE22_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', 'FE22_验证');
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
  const page = await browser.newPage({ viewport: { width: 1600, height: 1300 } });
  const errors = [];
  const nonGet = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('request', r => { if (r.url().includes('/api/') && r.method() !== 'GET') nonGet.push(r.method() + ' ' + r.url().replace(BASE, '').split('?')[0]); });
  const step = (n, d) => console.log('>> ' + n, JSON.stringify(d));
  const fail = [];
  const shot = n => page.screenshot({ path: path.join(OUT, n) });

  /* 打桩：wake POST（只拦 POST，GET 放行真接口）+ self-check no_reply */
  await page.route('**/harness/*/wake', (route, request) => {
    if (request.method() !== 'POST') return route.fallback();
    return route.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ success: true, harness_id: '示例Harness-A',
        wrapper_path: 'D:\\虚拟目录\\wake_示例Harness-A.cmd', kind: 'cmd',
        command: '我的agent --wake',
        next: '现在跑一次自检确认它真的会被叫醒' }) });
  });
  await page.route('**/self-check', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ state: 'no_reply', ok: false, probe_id: 'probe-fe22',
      advice: '桥在跑、消息送到了，卡在 agent 侧：多半没配「叫醒命令」', member_id: 'm0' }) }));

  /* 真接口 catalog 计数（作为断言基准） */
  const catReal = await fetch(BASE + '/api/bridge-catalog').then(r => r.json()).catch(() => ({ count: 0 }));
  const specReal = await fetch(BASE + '/api/bridge-catalog/file_poll').then(r => r.json()).catch(() => ({ spec: {} }));

  /* 自建：harness + 工作间 + 成员 */
  const HID = 'harness-fe22-probe';
  await fetch(BASE + '/api/harness/register', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ harness_id: HID, harness_name: '示例Harness-A',
      acp_command: '示例执行文件.exe --noop', ai: { model_name: '示例模型' } }) }).catch(() => {});
  const ws = await (async () => {
    const r = await fetch(BASE + '/api/workshop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'FE22验收-自建', hall_content: 'FE-22 叫醒闭环验收' }) });
    return (await r.json()).workshop_id;
  })();
  if (ws) await fetch(BASE + `/api/workshop/${ws}/members`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ members: [{ role: '码农', harness_ids: [HID], display_name: '示例Harness-A' }] }) });
  step('自建', { ws, catalog: catReal.count });

  try {
    await page.goto(BASE + '/register.html', { waitUntil: 'domcontentloaded', timeout: 25000 });
    await page.waitForSelector('#bridgeKindPanel', { timeout: 15000 });
    await sleep(1500);

    /* ① catalog 4 卡真打 */
    await page.waitForFunction(() => document.querySelectorAll('#bkCards button').length >= 4, null, { timeout: 15000 }).catch(() => {});
    const cards = await page.evaluate(() =>
      [...document.querySelectorAll('#bkCards button')].map(b => b.innerText.replace(/\n/g, ' | ')));
    step('① catalog 卡', { n: cards.length, cards });
    if (cards.length !== catReal.count) fail.push('① 卡数=' + cards.length + ' ≠ 真接口 ' + catReal.count);

    /* 第 2 步：点 file_poll → 字段来自响应 */
    await page.evaluate(() => {
      const btns=[...document.querySelectorAll('#bkCards button')];
      const hit=btns.find(b=>/文件夹邮箱|file_poll/.test(b.innerText));
      (hit||btns[0]).click();
    });
    await page.waitForFunction(() => {
      const s=document.getElementById('bkSpec');
      return s && s.style.display!=='none' && !/读取该型规格/.test(s.innerText);
    }, null, { timeout: 15000 }).catch(() => {});
    await sleep(300);
    const specTxt = await page.evaluate(() => (document.getElementById('bkSpec') || {}).innerText || '');
    await shot('01_选型与你填.png');
    step('② 第2步', { head: specTxt.slice(0, 300) });
    if (!/你填：/.test(specTxt)) fail.push('② 缺「你填」区');
    if (!/TRIGGER_CMD|叫醒命令/.test(specTxt)) fail.push('② 缺叫醒命令字段');
    if (!/平台自动填/.test(specTxt)) fail.push('② 缺平台字段行');
    if (!/怎么自证/.test(specTxt)) fail.push('② 缺 verify 文本');
    if (specTxt.indexOf('常见坑') < 0 && !(specReal.spec.pitfalls || []).length) fail.push('② 缺 pitfalls（真接口也没有）');
    /* 字段非手抄：真接口 user 字段名应出现在 spec 文本里 */
    ((specReal.spec.fields || []).filter(f => f.provides === 'user').map(f => f.name)).forEach(nm => {
      if (!specTxt.includes(nm)) fail.push('② user 字段 ' + nm + ' 未渲染（应来自接口）');
    });

    /* ③ wake 表单（POST 打桩） */
    const wakeSel = await page.evaluate(() => {
      const sel=document.getElementById('bkWakeHid');
      return sel ? { opts:[...sel.options].map(o=>o.value) } : null;
    });
    if (!wakeSel || !wakeSel.opts.includes(HID)) fail.push('③ 叫醒表单 harness 下拉缺自建项: ' + JSON.stringify(wakeSel));
    else {
      await page.selectOption('#bkWakeHid', HID);
      await page.fill('#bkWakeCmd', '我的agent --wake');
      await page.click('#bkWakeBtn');
      await page.waitForFunction(() => /已写入包装脚本/.test((document.getElementById('bkWakeRes')||{}).innerText||''),
        null, { timeout: 10000 }).catch(() => {});
      const wr = await page.evaluate(() => (document.getElementById('bkWakeRes') || {}).innerText || '');
      await shot('02_wake写入.png');
      step('③ wake POST', { head: wr.slice(0, 200) });
      if (!/已写入包装脚本/.test(wr)) fail.push('③ wake 写入未成功显示');
      if (!/wake_示例Harness-A\.cmd/.test(wr)) fail.push('③ 未显示返回的 wrapper_path');
      if (!/去自检确认/.test(wr)) fail.push('③ 缺去自检回跳');
    }

    /* ④ 清单 wake 列（真 GET） */
    await page.waitForFunction(() => /📣/.test((document.getElementById('list')||{}).innerText||''),
      null, { timeout: 15000 }).catch(() => {});
    const listTxt = await page.evaluate(() => (document.getElementById('list') || {}).innerText || '');
    await shot('03_清单wake列.png');
    step('④ wake 列', { hit: /📣/.test(listTxt) });
    if (!/📣/.test(listTxt)) fail.push('④ 清单缺 wake 列');

    /* ⑤ 自检 no_reply 回跳锚 */
    await page.selectOption('#scWs', ws);
    await sleep(600);
    await page.selectOption('#scMember', 'm0');
    await page.click('#scBtn');
    await page.waitForFunction(() => /去配叫醒命令/.test((document.getElementById('scResult')||{}).innerText||''),
      null, { timeout: 20000 }).catch(() => {});
    const scTxt = await page.evaluate(() => (document.getElementById('scResult') || {}).innerText || '');
    await shot('04_自检回跳.png');
    step('⑤ 自检回跳', { hit: /去配叫醒命令/.test(scTxt) });
    if (!/去配叫醒命令/.test(scTxt)) fail.push('⑤ no_reply 缺回跳锚');

    /* ⑤b 反向：不得出现拼好引号的带空格 --trigger 模式 */
    const bodyTxt = await page.evaluate(() => document.body.innerText);
    if (/--trigger\s+"/.test(bodyTxt)) fail.push('⑤b 出现拼好引号的 --trigger "…" 模式（105 §四-1 禁止）');

    /* ⑥ 禁词 + 白名单 + Console */
    ['harness-小白龙', 'harness-测试甲', 'harness-测试乙', 'harness-测试丙', 'C:\\Users\\'].forEach(bad => {
      if (bodyTxt.includes(bad)) fail.push('⑥ 出现禁词/本机路径「' + bad + '」');
    });
    const badReq = nonGet.filter(u =>
      !/\/member\/[^/]+\/self-check$/.test(u) && !/\/harness\/[^/]+\/wake$/.test(u));
    if (badReq.length) fail.push('非白名单写请求: ' + badReq.join(','));
    if (errors.length) fail.push('Console 红错 ' + errors.length);

    const out = { pass: fail.length === 0, fail, catalogCards: cards.length,
                  specHead: specTxt.slice(0, 300), wakeColumn: /📣/.test(listTxt),
                  selfCheckBack: /去配叫醒命令/.test(scTxt),
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
