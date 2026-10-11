const { chromium } = require('playwright-core');
const BASE = process.env.AC_FE_BASE || 'http://127.0.0.1:18920';
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  await page.goto(BASE + '/ops.html', { waitUntil: 'networkidle' });
  await new Promise(r => setTimeout(r, 800));
  /* 三条从未见过的未来样本：孤儿回收 / 非迁移事件混入 / 完全乱码 */
  const out = await page.evaluate(() => {
    const samples = [
      'orphan.recovered: task=4c3cbbf5 reason=stale_60s operator=system by=回收器V22',
      '{"nested": "json-like", "weird": true}',
      '???'
    ];
    const results = samples.map(s => {
      const row = window.__opsTest.injectFake(s);
      return {
        input: s,
        degraded: !!row.querySelector('.unparsed-tag'),
        shown: row.querySelector('.tl-raw') ? row.querySelector('.tl-raw').textContent : row.innerText.replace(/\n/g, ' | '),
        crashed: false
      };
    });
    return { results, itemCount: document.querySelectorAll('.tl-item').length };
  });
  console.log(JSON.stringify({ out, errors }, null, 2));
  await browser.close();
})().catch(e => { console.error('FATAL', e); process.exit(1); });
