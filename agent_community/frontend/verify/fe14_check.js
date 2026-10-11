/* FE-14 / V32 资料库界面写入验收（隔离实例 + AC_FE_BASE + 自建自清）
   覆盖 V32 §5 六条：模型未建立→缺块→完整；框架写入→刷新仍在；双反向断言；三档视口；写白名单。 */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');
const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, 'FE14_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', 'FE14_验证');
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

  /* 0. 自建 */
  const ws = await (async () => {
    const r = await fetch(BASE + '/api/workshop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'FE14验收-自建' }) });
    return (await r.json()).workshop_id;
  })();
  step('自建', { ws });
  if (!ws) process.exit(1);
  const readState = () => page.evaluate(() => {
    const g = s => { const e = document.querySelector(s); return e ? { tag: (e.querySelector('.model-tag,.fw-tag')||{}).textContent, act: (e.querySelector('.model-act,.fw-act')||{}).textContent } : { missing: true }; };
    const msg = s => { const e = document.querySelector(s); return e ? { cls: e.className, text: e.textContent } : null; };
    return { model: g('.model-block'), fw: g('.fw-block'), mMsg: msg('.m-msg'), fwMsg: msg('.fw-msg') };
  });

  try {
    const url = `${BASE}/booths.html?ws_id=${ws}`;
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 25000 });
    await page.waitForSelector('.model-block', { timeout: 20000 });
    await page.waitForSelector('.fw-block', { timeout: 10000 });
    await sleep(500);

    /* 1. 两个块的初始态 + 两个入口 */
    const s1 = await readState();
    await shot('01_初始两块.png');
    step('1 初始', s1);
    if (s1.model.tag !== '未建立') fail.push('模型 tag=' + s1.model.tag);
    if (s1.model.act !== '建立模型') fail.push('模型入口=' + s1.model.act);
    if (!/未建立/.test(s1.fw.tag || '')) fail.push('框架 tag=' + s1.fw.tag);
    if (s1.fw.act !== '写框架') fail.push('框架入口=' + s1.fw.act);

    /* 2. 模型：只填两块 → 缺 3 块 + 执笔 · v1（V32 §5.2） */
    await page.click('.model-act');
    await page.waitForSelector('.m-edit', { timeout: 8000 });
    await page.locator('.m-edit textarea').nth(0).fill('目标与验收：做成X');
    await page.locator('.m-edit textarea').nth(1).fill('组成：模块A');
    await page.click('.m-save');
    await page.waitForSelector('.m-msg.warn', { timeout: 10000 });
    await sleep(300);
    const s2 = await readState();
    const lib2 = await getLib(ws);
    await shot('02_模型缺3块.png');
    step('2 模型缺块', { msg: s2.mMsg, model: { v: lib2.model.version, missing: (lib2.model.missing||[]).length, author: lib2.model.author } });
    if (!s2.mMsg || !/缺 3 块/.test(s2.mMsg.text)) fail.push('模型未显示缺 3 块');
    if (!s2.mMsg || !/执笔：组长/.test(s2.mMsg.text)) fail.push('模型未显示执笔');
    if (lib2.model.version !== 1 || (lib2.model.missing||[]).length !== 3) fail.push('后端 v/missing 不符');

    /* 3. 模型：填满五块 → 已完成 · v2 · 历史 1 个（V32 §5.3） */
    await page.click('.model-act');
    await page.waitForSelector('.m-edit', { timeout: 8000 });
    await sleep(300);
    await page.locator('.m-edit textarea').nth(2).fill('分工：组长建模');
    await page.locator('.m-edit textarea').nth(3).fill('依赖：A先B后');
    await page.locator('.m-edit textarea').nth(4).fill('产物：各块 output/');
    await page.click('.m-save');
    await page.waitForSelector('.m-msg.ok', { timeout: 10000 });
    await sleep(300);
    const s3 = await readState();
    const lib3 = await getLib(ws);
    await shot('03_模型已完成.png');
    step('3 模型完整', { msg: s3.mMsg, tag: s3.model.tag, model: { v: lib3.model.version, missing: (lib3.model.missing||[]).length, hist: lib3.model.history_count } });
    if (s3.model.tag !== '已完成') fail.push('模型 tag=' + s3.model.tag);
    if (lib3.model.version !== 2 || (lib3.model.missing||[]).length !== 0 || lib3.model.history_count !== 1) fail.push('后端 v/missing/hist 不符');

    /* 4. 框架：写一段 → 保存 → 刷新后仍在（V32 §5.4） */
    await page.click('.fw-act');
    await page.waitForSelector('.fw-edit', { timeout: 8000 });
    await page.fill('.fw-ta', '## 项目框架（FE14 验收写入）\n\n本项目分三条线推进，验收以脚本通过为准。');
    await page.click('.fw-save');
    await page.waitForSelector('.fw-msg.ok', { timeout: 10000 });
    await sleep(400);
    const s4a = await readState();
    await shot('04_框架已写入.png');
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.fw-block', { timeout: 15000 });
    await sleep(600);
    const s4b = await page.evaluate(() => {
      const b = document.querySelector('.fw-block');
      const pre = b.querySelector('pre');   /* details 折叠时 innerText 不含隐藏内容 → 用 pre.textContent */
      return { tag: (b.querySelector('.fw-tag')||{}).textContent, act: (b.querySelector('.fw-act')||{}).textContent,
               text: (pre ? pre.textContent : '').slice(0, 220) };
    });
    await shot('05_框架刷新后.png');
    step('4 框架写入', { saved: s4a.fwMsg, afterReload: s4b });
    if (!s4a.fwMsg || !/已保存/.test(s4a.fwMsg.text)) fail.push('框架保存未显示已保存');
    if (s4b.tag !== '已建立') fail.push('刷新后框架 tag=' + s4b.tag);
    if (s4b.act !== '编辑') fail.push('刷新后按钮=' + s4b.act);
    if (!/FE14 验收写入/.test(s4b.text)) fail.push('刷新后预览丢失框架内容');

    /* 5. 反向断言 A：框架清空保存 ⇒ 不许显示成功（V32 §5.5） */
    await page.click('.fw-act');
    await page.waitForSelector('.fw-edit', { timeout: 8000 });
    await page.fill('.fw-ta', '');
    await page.click('.fw-save');
    await page.waitForSelector('.fw-msg.err', { timeout: 10000 });
    const s5 = await readState();
    await shot('06_框架空提交反向断言.png');
    step('5 框架空提交', s5.fwMsg);
    if (/已保存|已写入/.test((s5.fwMsg||{}).text || '')) fail.push('框架空提交显示了成功');
    if (!/未写入/.test((s5.fwMsg||{}).text || '')) fail.push('框架空提交文案不符');

    /* 6. 反向断言 B：模型整篇空提交 ⇒ 不许显示成功（V32 §5.5） */
    await page.click('.model-act');
    await page.waitForSelector('.m-edit', { timeout: 8000 });
    await page.click('.m-mode:has-text("整篇编辑")');
    await sleep(200);
    await page.fill('.m-edit textarea.big', '');   /* 编辑态预填了旧内容，清空才构成"空提交" */
    await page.click('.m-save');
    await page.waitForSelector('.m-msg.err', { timeout: 10000 });
    const s6 = await readState();
    await shot('07_模型空提交反向断言.png');
    step('6 模型空提交', s6.mMsg);
    if (/已保存/.test((s6.mMsg||{}).text || '')) fail.push('模型空提交显示了已保存');
    const lib6 = await getLib(ws);
    if (lib6.model.version !== 2) fail.push('空提交把模型版本改了: v=' + lib6.model.version);

    /* 7. 三档视口 */
    await page.click('.m-cancel').catch(() => {});
    const vp = {};
    for (const [w, h, name] of [[1920,1080,'1920'],[1280,720,'1280'],[1024,768,'1024']]) {
      await page.setViewportSize({ width: w, height: h });
      await sleep(400);
      vp[name] = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
      await shot('08_' + name + '.png');
      if (!vp[name]) fail.push(name + ' 横向破版');
    }
    step('7 视口', vp);

    /* 8. 白名单 + Console */
    const badReq = nonGet.filter(u => !/\/api\/workshop\/[^/]+\/library\/update$/.test(u));
    if (badReq.length) fail.push('非白名单写请求: ' + badReq.join(','));
    if (errors.length) fail.push('Console 红错: ' + errors.length);

    const result = { pass: fail.length === 0, fail, ws, s1, s2: s2.mMsg, s3: s3.mMsg, s4: { saved: s4a.fwMsg, afterReload: s4b },
                     s5: s5.fwMsg, s6: s6.mMsg, lib_final: { v: lib6.model.version, hist: lib6.model.history_count },
                     viewports: vp, nonGet, errors, screenshots: fs.readdirSync(OUT) };
    fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize(result), null, 2), 'utf8');
    step('结论', { pass: result.pass, fail });
  } catch (e) {
    fail.push('FATAL: ' + (e && e.message || e));
    step('FATAL', fail[fail.length - 1]);
    const result = { pass: false, fail, ws, nonGet, errors };
    fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize(result), null, 2), 'utf8');
  } finally {
    await fetch(BASE + '/api/workshop/' + ws, { method: 'DELETE' }).catch(() => {});
    await browser.close();
  }
  process.exit(fail.length ? 1 : 0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
