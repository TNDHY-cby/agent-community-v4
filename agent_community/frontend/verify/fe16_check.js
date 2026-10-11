/* FE-16 模型「一键生成草案」按钮验收（隔离实例 + AC_FE_BASE + 自建自清 + AC_VERIFY_OUT）
   覆盖 FE-16 §3：按钮可见性（仅表单模式）· 真 400 未配 AI 如实显示 · 草案不落盘（反向断言）
   · 成功路径用 page.route 打桩（不花真 AI 钱）· 保存后横幅撤下 · 写白名单 · 三档视口 */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');
const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, 'FE16_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', 'FE16_验证');
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
const getLib = ws => fetch(BASE + `/api/workshop/${ws}/library`).then(r => r.json());

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
  const fail = [];
  const shot = n => page.screenshot({ path: path.join(OUT, n) });

  /* 防计费闸：实例已配 AI 就不真点 draft（真 400 步骤跳过，其余照测） */
  const cfgStatus = await fetch(BASE + '/api/config/status').then(r => r.json()).catch(() => ({}));
  const aiConfigured = !!cfgStatus.configured;
  step('环境', { aiConfigured, base: BASE });

  /* 0. 自建 */
  const ws = await (async () => {
    const r = await fetch(BASE + '/api/workshop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'FE16验收-自建' }) });
    return (await r.json()).workshop_id;
  })();
  step('自建', { ws });
  if (!ws) process.exit(1);

  try {
    await page.goto(`${BASE}/booths.html?ws_id=${ws}`, { waitUntil: 'domcontentloaded', timeout: 25000 });
    await page.waitForSelector('.model-block', { timeout: 20000 });
    await sleep(500);

    /* 1. 按钮可见性：表单模式有、整篇模式没有（FE-16 §2） */
    await page.click('.model-act');
    await page.waitForSelector('.m-edit', { timeout: 8000 });
    await sleep(300);
    const s1 = {
      draftBtnForm: await page.locator('.m-draft').count(),
      mSave: await page.locator('.m-save').count()
    };
    await page.click('.m-mode:has-text("整篇编辑")');
    await sleep(200);
    s1.draftBtnText = await page.locator('.m-draft').count();
    await page.click('.m-mode:has-text("五块表单")');
    await sleep(200);
    await shot('01_按钮可见.png');
    step('1 按钮可见性', s1);
    if (s1.draftBtnForm !== 1) fail.push('表单模式草案按钮数=' + s1.draftBtnForm);
    if (s1.draftBtnText !== 0) fail.push('整篇模式不该有草案按钮');

    /* 2. 400 未配 AI：如实显示 error+hint、表单不动、草案没落盘。
       本机实例可能继承了已配 AI 的用户配置 ⇒ 已配 AI 时**不真点**（防计费），
       改用 page.route 打桩 400（后端原文取自 workshops_lifecycle.py:727）；未配 AI 才真打。 */
    let s2 = { stubbed: aiConfigured };
    const hint400 = '手工填五块即可（界面「建立模型」里有骨架提示）；配好 AI 后可回来一键生成。';
    if (aiConfigured) {
      await page.route('**/library/model/draft', route => route.fulfill({
        status: 400, contentType: 'application/json',
        body: JSON.stringify({ error: '未配置 AI：无法生成草案', hint: hint400, sections: [] })
      }));
    }
    const before = await page.evaluate(() =>
      [...document.querySelectorAll('.m-edit textarea')].map(t => t.value));
    const errIdx400 = errors.length;
    await page.click('.m-draft');
    await page.waitForSelector('.m-msg.err', { timeout: 15000 });
    await sleep(200);
    s2.msg = await page.evaluate(() => (document.querySelector('.m-msg') || {}).textContent);
    s2.after = await page.evaluate(() =>
      [...document.querySelectorAll('.m-edit textarea')].map(t => t.value));
    const lib2 = await getLib(ws);
    s2.modelExists = !!(lib2.model && lib2.model.exists);
    s2.writesBeforeSave = nonGet.filter(u => /library\/update/.test(u)).length;
    await shot('02_400未配AI.png');
    step('2 400路径', s2);
    if (!/未配置 AI/.test(s2.msg || '')) fail.push('400 文案缺「未配置 AI」: ' + s2.msg);
    if (!/手工填五块/.test(s2.msg || '')) fail.push('400 文案缺 hint 原文');
    if (JSON.stringify(before) !== JSON.stringify(s2.after)) fail.push('400 后表单被改动');
    if (s2.modelExists) fail.push('草案把 model.md 落盘了（反向断言失败）');
    if (s2.writesBeforeSave) fail.push('保存前就出现了 library/update 写请求');
    /* 步骤2 的 400（真打或打桩）都会记一条资源日志 —— 预期行为，从 Console 计数里扣除
       （同 library_check 的教训：故意制造的 404/400 不能算"红错"） */
    s2.consoleExpected = errors.splice(errIdx400).length;
    if (aiConfigured) await page.unroute('**/library/model/draft');

    /* 3. 成功路径打桩（page.route，不花真 AI 钱）：填表 + 横幅 + 黄条 */
    await page.route('**/library/model/draft', route => route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        draft: { goal: '目标：做成看板', parts: '组成：前端+后端', roles: '分工：码农做前端',
                 deps: '', outputs: '产物：码农 output/' },
        filled: ['goal', 'parts', 'roles', 'outputs'],
        missing: ['4. 依赖与顺序'],
        verified: false,
        note: '这是**草案**（未经组长确认）：请修改后点「保存」写入 library/model.md；缺块会被如实标为未完成'
      })
    }));
    await page.click('.m-draft');
    await page.waitForSelector('.m-draftbar', { timeout: 10000 });
    await sleep(300);
    const s3 = await page.evaluate(() => ({
      bar: (document.querySelector('.m-draftbar') || {}).textContent,
      msg: (document.querySelector('.m-msg') || {}).textContent,
      vals: [...document.querySelectorAll('.m-edit textarea')].map(t => t.value)
    }));
    await shot('03_草案填充.png');
    step('3 打桩成功', s3);
    if (!/AI 草案 · 待确认/.test(s3.bar || '')) fail.push('草案横幅缺失');
    if (!/草案已生成/.test(s3.msg || '')) fail.push('成功文案缺失');
    if (!/缺 1 块/.test(s3.msg || '')) fail.push('成功文案未报缺块');
    if (s3.vals[0] !== '目标：做成看板' || s3.vals[3] !== '') fail.push('五块填入不符: ' + JSON.stringify(s3.vals));
    await page.unroute('**/library/model/draft');

    /* 4. 真保存（library/update 不在打桩范围）→ 横幅撤下、model.md 落盘。
       草案缺 1 块 ⇒ 保存后是**黄条「已保存 · 缺 1 块」**（准则⑤），不是绿条。 */
    await page.click('.m-save');
    await page.waitForSelector('.m-msg.warn', { timeout: 10000 });
    await sleep(400);
    const s4 = await page.evaluate(() => ({
      bar: document.querySelectorAll('.m-draftbar').length,
      msg: (document.querySelector('.m-msg') || {}).textContent,
      tag: (document.querySelector('.model-block .model-tag') || {}).textContent
    }));
    const lib4 = await getLib(ws);
    await shot('04_确认保存.png');
    step('4 保存确认', { s4, model: { exists: lib4.model.exists, v: lib4.model.version, missing: (lib4.model.missing || []).length } });
    if (s4.bar !== 0) fail.push('保存后草案横幅未撤下');
    if (!/已保存/.test(s4.msg || '')) fail.push('保存文案缺失');
    if (s4.tag !== '缺 1 块') fail.push('缺块未如实显示: ' + s4.tag);
    if (!lib4.model.exists || lib4.model.version !== 1) fail.push('model.md 未按确认落盘');

    /* 5. 三档视口 */
    const vp = {};
    for (const [w, h, name] of [[1920,1080,'1920'],[1280,720,'1280'],[1024,768,'1024']]) {
      await page.setViewportSize({ width: w, height: h });
      await sleep(350);
      vp[name] = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
      await shot('05_' + name + '.png');
      if (!vp[name]) fail.push(name + ' 横向破版');
    }
    step('5 视口', vp);

    /* 6. 写白名单 + Console */
    const badReq = nonGet.filter(u =>
      !/\/api\/workshop\/[^/]+\/library\/(update|model\/draft)$/.test(u));
    if (badReq.length) fail.push('非白名单写请求: ' + badReq.join(','));
    if (errors.length) fail.push('Console 红错 ' + errors.length);

    const result = { pass: fail.length === 0, fail, aiConfigured, ws, s1, s2, s3, s4,
                     lib_final: { exists: lib4.model.exists, v: lib4.model.version },
                     viewports: vp, nonGet, errors, screenshots: fs.readdirSync(OUT) };
    fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize(result), null, 2), 'utf8');
    step('结论', { pass: result.pass, fail });
  } catch (e) {
    fail.push('FATAL: ' + (e && e.message || e));
    step('FATAL', fail[fail.length - 1]);
    fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize({ pass: false, fail, ws, nonGet, errors }), null, 2), 'utf8');
  } finally {
    await fetch(BASE + '/api/workshop/' + ws, { method: 'DELETE' }).catch(() => {});
    await browser.close();
  }
  process.exit(fail.length ? 1 : 0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
