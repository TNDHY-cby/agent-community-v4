/* FE-13 资料库「项目模型」块验收：三态 + 建立/编辑入口 + 写路径 + 反向断言
   隔离实例 + AC_FE_BASE + 自建自清；截图进 <仓库根>/temp/验收证据截图/；result.json 脱敏 */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');
const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, 'FE13_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', 'FE13_验证');
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
  const readBlock = () => page.evaluate(() => {
    const m = document.querySelector('.model-block');
    const act = m && m.querySelector('.model-act');
    const msg = document.querySelector('.m-msg');
    return {
      tag: (m && m.querySelector('.model-tag') || {}).textContent,
      act: act ? act.textContent : null,
      meta: (m && m.querySelector('.model-meta') || {}).textContent || '',
      msg: msg ? { cls: msg.className, text: msg.textContent } : null,
      text: m ? m.innerText.replace(/\n/g, ' | ').slice(0, 220) : 'missing'
    };
  });

  /* 0. 自建（不依赖既有 ws_id） */
  const ws = await (async () => {
    const r = await fetch(BASE + '/api/workshop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'FE13验收-自建' }) });
    return (await r.json()).workshop_id;
  })();
  step('自建', { ws });
  if (!ws) process.exit(1);
  let result = { ws: null, s1_未建立: null, s2_编辑器: null, s3_反向断言: null, s4_缺块: null, s5_完整: null, lib: null, nonGet, errors };

  try {
    const url = `${BASE}/booths.html?ws_id=${ws}`;
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 25000 });
    await page.waitForSelector('.model-block', { timeout: 20000 });
    await sleep(500);

    /* 1. 未建立：灰徽 + 「建立模型」入口 */
    const s1 = await readBlock();
    await page.screenshot({ path: path.join(OUT, '01_未建立.png') });
    step('1 未建立', s1);

    /* 2. 点入口 → 编辑器（五块表单 + 执笔人下拉 + 两模式） */
    await page.click('.model-act');
    await page.waitForSelector('.m-edit', { timeout: 8000 });
    await sleep(300);
    const s2 = await page.evaluate(() => ({
      fields: document.querySelectorAll('.m-edit .m-field').length,
      author: (document.querySelector('.m-bar select') || {}).value,
      modes: [...document.querySelectorAll('.m-mode')].map(b => b.textContent),
      save: !!document.querySelector('.m-save')
    }));
    await page.screenshot({ path: path.join(OUT, '02_编辑器.png') });
    step('2 编辑器', s2);

    /* 3. 反向断言：整篇模式空提交 ⇒ 后端 error=未提供 model，界面不许显示「已保存」 */
    await page.click('.m-mode:has-text("整篇编辑")');
    await sleep(200);
    await page.click('.m-save');
    await page.waitForSelector('.m-msg', { timeout: 10000 });
    const s3 = await readBlock();
    await page.screenshot({ path: path.join(OUT, '03_反向断言.png') });
    step('3 反向断言', s3);

    /* 4. 表单填 2 块 → 保存 ⇒ 黄「缺 3 块」+ v1 */
    await page.click('.m-mode:has-text("五块表单")');
    await sleep(200);
    await page.locator('.m-edit textarea').nth(0).fill('目标与验收：做成X，验收=脚本通过');
    await page.locator('.m-edit textarea').nth(1).fill('组成：模块A、模块B');
    await page.click('.m-save');
    await page.waitForSelector('.m-msg.warn', { timeout: 10000 });
    await sleep(300);
    const s4 = await readBlock();
    const lib4 = await getLib(ws);
    await page.screenshot({ path: path.join(OUT, '04_缺3块.png') });
    step('4 缺块保存', { s4, model: lib4.model });

    /* 5. 再编辑 → 补满 5 块 → 保存 ⇒ 绿「已保存 · v2」+ 已完成 */
    await page.click('.model-act');
    await page.waitForSelector('.m-edit', { timeout: 8000 });
    await sleep(300);
    const prefill = await page.evaluate(() =>
      [...document.querySelectorAll('.m-edit textarea')].map(t => t.value.slice(0, 40)));
    await page.locator('.m-edit textarea').nth(2).fill('分工：组长建模，码农写模块A');
    await page.locator('.m-edit textarea').nth(3).fill('依赖：A先B后，可并行部分已标');
    await page.locator('.m-edit textarea').nth(4).fill('产物：各块放对应成员 output/');
    await page.click('.m-save');
    await page.waitForSelector('.m-msg.ok', { timeout: 10000 });
    await sleep(300);
    const s5 = await readBlock();
    const lib5 = await getLib(ws);
    await page.screenshot({ path: path.join(OUT, '05_已完成.png') });
    step('5 完整保存', { s5, prefill, model: lib5.model });

    /* 6. 窄视口不破版（协作书 §7：1280×720 与 1024 宽） */
    const v1280 = await (async () => {
      await page.setViewportSize({ width: 1280, height: 720 });
      await sleep(400);
      await page.screenshot({ path: path.join(OUT, '06_1280x720.png') });
      return page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
    })();
    const v1024 = await (async () => {
      await page.setViewportSize({ width: 1024, height: 768 });
      await sleep(400);
      await page.screenshot({ path: path.join(OUT, '07_1024.png'), fullPage: true });
      return page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
    })();
    await page.setViewportSize({ width: 1600, height: 1000 });
    step('6 视口', { v1280, v1024 });

    /* 7. 断言汇总（失败即 exit 1，不拿"看起来通过"交差） */
    const lib = { v4: lib4.model && lib4.model.version, miss4: (lib4.model && lib4.model.missing || []).length,
                  v5: lib5.model && lib5.model.version, miss5: (lib5.model && lib5.model.missing || []).length,
                  hist5: lib5.model && lib5.model.history_count, exists: lib5.model && lib5.model.exists };
    const fail = [];
    if (!s1.tag || !/未建立/.test(s1.tag)) fail.push('s1 非「未建立」');
    if (s1.act !== '建立模型') fail.push('s1 缺「建立模型」入口');
    if (s2.fields !== 5) fail.push('s2 五块字段数=' + s2.fields);
    if (s2.author !== '组长') fail.push('s2 默认执笔人=' + s2.author);
    if (!s3.msg || !/未提供 model/.test(s3.msg.text)) fail.push('s3 未见「未提供 model」');
    if (s3.msg && /已保存/.test(s3.msg.text)) fail.push('s3 空提交显示了「已保存」');
    if (!s4.msg || !/缺 3 块/.test(s4.msg.text)) fail.push('s4 未见「缺 3 块」');
    if (lib.miss4 !== 3 || lib.v4 !== 1) fail.push(`s4 后端 v=${lib.v4} missing=${lib.miss4}`);
    if (!s5.msg || !/已保存/.test(s5.msg.text)) fail.push('s5 未见「已保存」');
    if (s5.tag !== '已完成') fail.push('s5 tag=' + s5.tag);
    if (lib.v5 !== 2 || lib.miss5 !== 0 || lib.hist5 !== 1) fail.push(`s5 后端 v=${lib.v5} missing=${lib.miss5} hist=${lib.hist5}`);
    const badReq = nonGet.filter(u => !/\/api\/workshop\/[^/]+\/library\/update$/.test(u));
    if (badReq.length) fail.push('非白名单写请求：' + badReq.join(' , '));
    if (errors.length) fail.push('Console 红错：' + errors.length);
    if (!v1280) fail.push('1280 视口横向破版');
    if (!v1024) fail.push('1024 视口横向破版');

    result = { ws, s1_未建立: s1, s2_编辑器: s2, s3_反向断言: s3, s4_缺块: s4, s5_完整: s5,
               viewports: { v1280, v1024 }, lib, nonGet, errors,
               screenshots: fs.readdirSync(OUT), pass: fail.length === 0, fail };
    step('结论', { pass: fail.length === 0, fail });
  } catch (e) {
    result.fatal = String(e && e.message || e);
    step('FATAL', result.fatal);
  } finally {
    await fetch(BASE + '/api/workshop/' + ws, { method: 'DELETE' }).catch(() => {});
    fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize(result), null, 2), 'utf8');
    await browser.close();
  }
  process.exit(result.fail && result.fail.length ? 1 : 0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
