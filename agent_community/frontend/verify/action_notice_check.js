/* FE-05 推进提示验收：action/action_source 可见化 */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');
const BASE = process.env.AC_FE_BASE || 'http://127.0.0.1:18920';
const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, '推进提示_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', '推进提示_验证');
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
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  const step = (n, d) => console.log('>> ' + n, JSON.stringify(d));

  /* 规则11：自建间、跑完自清（不写死 295f6eaa ⇒ 隔离实例不再 404）。
     首轮自动讨论的 discuss 会被下面的 page.route 兜住 ⇒ 不真打 AI、不产生 400。 */
  const ws = await (async () => {
    const r = await fetch(BASE + '/api/workshop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: '推进提示验收-自建' }) });
    return (await r.json()).workshop_id;
  })();
  if (!ws) process.exit(1);

  let canned = null;
  await page.route('**/api/workshop/*/discuss', route =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(canned) }));

  /* A. workshop：select + rule_fallback */
  canned = { success: true, reply: '我们再核对一下细节……', status: 'discussing', stage: 'orchestrator', zone: 1, action: 'select', action_source: 'rule_fallback' };
  await page.goto(BASE + '/workshop.html?ws_id=' + ws, { waitUntil: 'networkidle', timeout: 20000 });
  await sleep(800);
  await page.fill('#msgInput1', '需求明确了，开始选人吧');
  await page.evaluate(() => sendDiscuss(1));
  await sleep(900);
  const A = await page.evaluate(() => {
    const d = document.getElementById('discussion1');
    return {
      hasSelectNotice: /可以选定员工/.test(d.innerText),
      hasFallbackMark: /规则兜底/.test(d.innerText),
      hasDontMisread: /别把它的话当成/.test(d.innerText),
      tail: d.innerText.slice(-260)
    };
  });
  step('A select+rule_fallback', A);
  await page.screenshot({ path: path.join(OUT, '01_workshop_rule_fallback.png') });

  /* B. workshop：select + ai_marker */
  canned = { success: true, reply: '需求已明确，可以选定员工。', status: 'discussing', stage: 'orchestrator', zone: 1, action: 'select', action_source: 'ai_marker' };
  await page.fill('#msgInput1', '再来一次');
  await page.evaluate(() => sendDiscuss(1));
  await sleep(900);
  const B = await page.evaluate(() => {
    const d = document.getElementById('discussion1');
    const notices = [...d.querySelectorAll('.notice')].map(n => n.innerText);
    return {
      aiMarkerNotice: notices.some(t => /✅ 需求已明确/.test(t)),
      noRuleWordInAiNotice: !notices.some(t => /✅/.test(t) && /规则兜底/.test(t)),
      noticeCount: notices.length
    };
  });
  step('B select+ai_marker', B);
  await page.screenshot({ path: path.join(OUT, '02_workshop_ai_marker.png') });

  /* C. index：select + rule_fallback */
  canned = { success: true, reply: '好的，细节都对齐了。', status: 'discussing', action: 'select', action_source: 'rule_fallback' };
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  await sleep(800);
  await page.evaluate(() => {
    currentWorkshopId = 'ws-test';
    const inp = document.getElementById('discussInput');
    inp.value = '需求明确了，开始选人吧';
    return sendDiscussMessage();
  });
  await sleep(900);
  const C = await page.evaluate(() => {
    const d = document.getElementById('discussionList') || document.body;
    return {
      hasSelect: /可以选定员工/.test(d.innerText),
      hasFallback: /【规则兜底判定】/.test(d.innerText),
      hasGuide: /进入该工作间/.test(d.innerText),
      tail: d.innerText.slice(-240)
    };
  });
  step('C index select+rule_fallback', C);
  await page.screenshot({ path: path.join(OUT, '03_index_rule_fallback.png') });

  console.log('=== FINAL ===');
  console.log(JSON.stringify({ errors, A, B, C }, null, 2));
  fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize({ errors, A, B, C }), null, 2), 'utf8');
  await fetch(BASE + '/api/workshop/' + ws, { method: 'DELETE' }).catch(() => {});
  await browser.close();
})().catch(e => { console.error('FATAL', e); process.exit(1); });
