/* FE-02 handover.html 端到端验收（DSH 4 号发言四条 + 补充项） */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');
const BASE = process.env.AC_FE_BASE || 'http://127.0.0.1:18920';
const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, 'handover_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', 'handover_验证');
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
    if (typeof v === 'string') {
      return v.replace(drive, '<本机路径>')
        .replace(/harness-小白龙/g, '示例Harness-A')
        .replace(/harness-测试甲/g, '示例Harness-B')
        .replace(/harness-测试乙/g, '示例Harness-C')
        .replace(/harness-测试丙/g, '示例Harness-D')
        .replace(/harness-dsh-web-[A-Za-z0-9.\-]+/g, '示例Harness-E');
    }
    if (Array.isArray(v)) return v.map(walk);
    if (v && typeof v === 'object') { const r = {}; for (const k in v) r[k] = walk(v[k]); return r; }
    return v;
  };
  return walk(o);
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const errors = [];
  const nonGet = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('request', r => {
    if (r.url().includes('/api/') && r.method() !== 'GET') nonGet.push(r.method() + ' ' + r.url().replace(BASE, ''));
  });
  const step = (n, d) => console.log('>> ' + n, JSON.stringify(d));

  await page.goto(BASE + '/handover.html', { waitUntil: 'networkidle', timeout: 20000 });
  await sleep(1200);

  /* 0. 基本渲染：规则来自模板、FE-01 回执在列表 */
  const basic = await page.evaluate(() => ({
    rulesHasRule: (document.querySelector('#rulesBox b') || {}).textContent || '',
    rcCount: document.getElementById('rcCount').textContent,
    cards: document.querySelectorAll('.rc').length,
    firstCard: document.querySelector('.rc') ? document.querySelector('.rc').innerText.replace(/\n/g, ' | ').slice(0, 220) : null,
    deliveredBadge: document.querySelector('.rc-del') ? document.querySelector('.rc-del').textContent : null,
    itemRows: document.querySelectorAll('.item-row').length
  }));
  step('基本渲染', basic);
  await page.screenshot({ path: path.join(OUT, '01_1920x1080.png') });

  /* 1a. 证据全空提交 → 后端 errors 逐条显示 */
  await page.fill('#fTask', 'ws-demo');
  await page.fill('#fWho', 'MiMo');
  await page.fill('.item-row textarea', '空证据冒烟测试');
  await page.click('#submitBtn');
  await sleep(900);
  const err1 = await page.evaluate(() => ({
    shown: document.getElementById('submitErr').classList.contains('show'),
    items: [...document.querySelectorAll('#errList li')].map(li => li.textContent),
    hint: document.getElementById('errHint').textContent,
    keptClaim: document.querySelector('.item-row textarea').value
  }));
  step('验收① 空证据400逐条', err1);
  await page.screenshot({ path: path.join(OUT, '02_空证据400逐条.png') });

  /* 1b. delivered 无 reply_ref → 400 */
  await page.fill('.item-row .ev-grid input:nth-child(3)', 'playwright 空证据路径实测');
  await page.check('#fDelivered');
  await page.fill('#fReply', '');
  await page.click('#submitBtn');
  await sleep(900);
  const err2 = await page.evaluate(() => ({
    shown: document.getElementById('submitErr').classList.contains('show'),
    items: [...document.querySelectorAll('#errList li')].map(li => li.textContent)
  }));
  step('验收①b 送达缺凭据400', err2);
  await page.screenshot({ path: path.join(OUT, '03_送达缺凭据400.png') });

  /* 2. 合规提交 → 200 → 列表出现 */
  await page.fill('#fTask', 'handover-ui-e2e');
  await page.fill('#fWho', 'MiMo');
  await page.fill('.item-row textarea', 'handover.html 表单提交链路冒烟测试（400×2 路径 + 本条 200）');
  await page.fill('.item-row .ev-grid input:nth-child(3)', 'playwright e2e：空证据400/送达缺凭据400/本条200，见 handover_验证');
  await page.uncheck('#fDelivered');
  await page.fill('#fReply', '');
  await page.click('#submitBtn');
  await sleep(1500);
  const ok = await page.evaluate(() => ({
    okShown: document.getElementById('submitOk').classList.contains('show'),
    okText: document.getElementById('submitOk').textContent,
    errShown: document.getElementById('submitErr').classList.contains('show'),
    count: document.getElementById('rcCount').textContent,
    topCard: document.querySelector('.rc') ? document.querySelector('.rc').innerText.replace(/\n/g, ' | ').slice(0, 200) : null,
    formReset: document.getElementById('fTask').value === '' && document.querySelectorAll('.item-row').length === 1
  }));
  step('验收② 合规提交200入列', ok);
  await page.screenshot({ path: path.join(OUT, '04_提交成功入列.png') });

  /* 点击卡片展开原始 JSON */
  await page.evaluate(() => document.querySelector('.rc').click());
  const expanded = await page.evaluate(() => ({
    open: document.querySelector('.rc').classList.contains('open'),
    hasJson: !!document.querySelector('.rc.open .rc-expand')
  }));
  step('卡片展开原文', expanded);

  /* 3. 三档视口 */
  const probe = async (w, h) => {
    await page.setViewportSize({ width: w, height: h });
    await sleep(400);
    return await page.evaluate(() => ({
      vw: window.innerWidth,
      overflow: document.documentElement.scrollWidth > window.innerWidth,
      docW: document.documentElement.scrollWidth
    }));
  };
  const v1920 = await probe(1920, 1080);
  await page.screenshot({ path: path.join(OUT, '05_final_1920.png') });
  const v1280 = await probe(1280, 720);
  await page.screenshot({ path: path.join(OUT, '06_1280x720.png') });
  const v1024 = await probe(1024, 768);
  await page.screenshot({ path: path.join(OUT, '07_1024x768.png'), fullPage: true });
  step('验收③ 视口', { v1920, v1280, v1024 });

  /* task_id 筛选 */
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.fill('#fTaskQ', 'FE-01');
  await sleep(900);
  const filt = await page.evaluate(() => ({
    count: document.getElementById('rcCount').textContent,
    tasks: [...document.querySelectorAll('.rc-task')].map(t => t.textContent)
  }));
  await page.fill('#fTaskQ', '');
  await sleep(700);
  step('task_id 筛选', filt);

  console.log('=== FINAL ===');
  console.log(JSON.stringify({ errors, nonGet, err1, err2, ok, expanded }, null, 2));
  fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize({ errors, nonGet, basic, err1, err2, ok, expanded, v1920, v1280, v1024, filt }), null, 2), 'utf8');
  await browser.close();
})().catch(e => { console.error('FATAL', e); process.exit(1); });
