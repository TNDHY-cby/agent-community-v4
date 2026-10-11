/* FE-04 UI 链路巡检：互链可达 + 顶栏不溢出 + Console 干净 */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');
const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, 'ui链路_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', 'ui链路_验证');
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
const BASE = process.env.AC_FE_BASE || 'http://127.0.0.1:18930';

(async () => {
  /* 规则11：不再写死 295f6eaa（隔离实例 404 ⇒ 八连资源报错）。自建间、跑完自清。
     工作间页用 domcontentloaded+固定等待：全新间的首轮自动讨论是真 AI 调用（网络时长不可控），
     等 networkidle 会超时；快照发生在讨论在途时，HTTP 层错误（才是本测试关心的）已可捕获。 */
  const ws = await (async () => {
    const r = await fetch(BASE + '/api/workshop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hall_content: 'UI链路巡检自建间' }) });
    return (await r.json()).workshop_id;
  })();
  const PAGES = ['/', '/workshop.html?ws_id=' + ws, '/register.html', '/ops.html', '/handover.html', '/booths.html', '/hub.html'];
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const report = {};
  const allErrors = {};

  try {
    for (const p of PAGES) {
      const errors = [];
      const onErr = m => { if (m.type() === 'error') errors.push(m.text()); };
      const onPageErr = e => errors.push('pageerror: ' + e.message);
      page.on('console', onErr); page.on('pageerror', onPageErr);
      const isWorkshop = p.startsWith('/workshop.html');
    await page.route('**/api/workshop/*/discuss', r => r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ success: true, reply: '（验收桩）需求已明确。', status: 'discussing', zone: 1 }),
    }));
      await page.goto(BASE + p, { waitUntil: isWorkshop ? 'domcontentloaded' : 'networkidle', timeout: 20000 });
      await new Promise(r => setTimeout(r, isWorkshop ? 1500 : 700));

    /* 从 DOM 收集同源链接（a[href]） */
    const links = await page.evaluate(() =>
      [...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href'))
        .filter(h => h && (h.startsWith('/') || h.startsWith('http://127.0.0.1'))));
    /* 从源码收集 onclick 里的 location.href 目标 */
    const btnTargets = await page.evaluate(() =>
      [...document.querySelectorAll('[onclick]')].map(b => b.getAttribute('onclick') || '')
        .map(s => (s.match(/location\.href\s*=\s*'([^']+)'/) || [])[1])
        .filter(Boolean));
    const targets = [...new Set([...links, ...btnTargets])];

    /* 逐个 HEAD/GET 状态 */
    const status = {};
    for (const t of targets) {
      /* ★2026-10-12：页面里有些链接是**写死端口的绝对地址**（历史遗留）。
         直接访问它们，会把这条验收绑死在"必须先另起一个 18920 实例"上 ——
         我在 18920 停着的时候就被判 FAIL，其实链接没坏、只是目标不在。
         ⇒ 把绝对地址的 host 换成**当前被测实例**再探：检查自包含，不依赖别的实例。
         （同类教训：验收判据不得依赖"只有我这台机器才有"的东西。） */
      const url = t.startsWith('http')
        ? t.replace(/^http:\/\/127\.0\.0\.1:\d+/, BASE)
        : BASE + t;
      try {
        const r = await fetch(url, { method: 'GET' });
        status[t] = r.status;
      } catch (e) { status[t] = 'ERR ' + e.message; }
    }
    const bad = Object.fromEntries(Object.entries(status).filter(([k, v]) => v !== 200));

    report[p] = {
      targets,
      bad,
      ok: Object.keys(bad).length === 0,
      hasSelf: targets.some(t => t.split('?')[0] === p.split('?')[0] && t !== '/') || p === '/' ? true : targets.length > 0
    };
    allErrors[p] = errors;
    page.off('console', onErr); page.off('pageerror', onPageErr);
  }

  /* index 顶栏 1024 溢出检查 */
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  await new Promise(r => setTimeout(r, 500));
  const topbar1024 = await page.evaluate(() => {
    const tb = document.querySelector('.topbar');
    return {
      topbarScroll: tb ? tb.scrollWidth : null,
      topbarClient: tb ? tb.clientWidth : null,
      docOverflow: document.documentElement.scrollWidth > window.innerWidth
    };
  });
  await page.screenshot({ path: path.join(OUT, 'index_1024顶栏.png') });

  /* 1280 与 1920 index 截图 */
  await page.setViewportSize({ width: 1920, height: 1080 });
  await new Promise(r => setTimeout(r, 300));
  await page.screenshot({ path: path.join(OUT, 'index_1920.png') });

    console.log(JSON.stringify({ report, allErrors, topbar1024 }, null, 2));
    const allOk = Object.values(report).every(r => r.ok);
    const noErrors = Object.values(allErrors).every(e => e.length === 0);
    const topbarFits = topbar1024.topbarScroll <= topbar1024.topbarClient;
    try{require('fs').writeFileSync(process.env.AC_VERIFY_OUT+'/ui_links_debug.json',JSON.stringify(report,null,1));}catch(e){}
  console.log('ALL_LINKS_OK:', allOk, '| NO_CONSOLE_ERRORS:', noErrors,
      '| TOPBAR_1024:', topbarFits ? 'fits' : 'OVERFLOW');
    process.exitCode = (allOk && noErrors && topbarFits) ? 0 : 1;
  } catch (e) {
    console.error('FATAL', e);
    process.exitCode = 1;
  } finally {
    if (ws) await fetch(BASE + '/api/workshop/' + ws, { method: 'DELETE' }).catch(() => {});
    await browser.close();
  }
})();
