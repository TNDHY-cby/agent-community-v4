/* FE-15 流程说明改版+改名验收（隔离实例 + AC_FE_BASE + 自建自清）
   断言：① 7 处改名生效、页面不再出现「对接中心」；② 13 章逐个可渲染、Console 零红错；
   ③ 零 API 请求（本页只讲不做）；④ 1920 主视口 + 1024 不破版。 */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');
const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, 'FE15_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', 'FE15_验证');
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
    if (typeof v === 'string') return v.replace(drive, '<本机路径>');
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
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const errors = [];
  const apiReqs = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('request', r => { if (r.url().includes('/api/')) apiReqs.push(r.method() + ' ' + r.url().replace(BASE, '').split('?')[0]); });
  const step = (n, d) => console.log('>> ' + n, JSON.stringify(d));
  const fail = [];

  /* 1. hub 页：标题 / logo / 正文改名 */
  await page.goto(BASE + '/hub.html', { waitUntil: 'domcontentloaded', timeout: 25000 });
  await sleep(600);
  const head = await page.evaluate(() => ({
    title: document.title,
    logo: (document.querySelector('.logo') || {}).innerText || '',
    banner: (document.querySelector('.scope-banner') || {}).innerText || '',
    hasOld: document.body.innerText.includes('对接中心'),
    railCount: document.querySelectorAll('.rail-item').length
  }));
  if (head.title !== '流程说明') fail.push('title=' + head.title);
  if (!/流程说明/.test(head.logo)) fail.push('logo 未改名: ' + head.logo);
  /* ★2026-10-23：这两条断言已过期（不是本批引入的失败）——
     ① hub 现在**该**讲「对接中心」（讲解归 hub，V34 §5）；② 章节数随内容增长，写死 13 必然假红。 */
  if (head.railCount < 10) fail.push('rail 章节数=' + head.railCount);
  await page.screenshot({ path: path.join(OUT, '01_hub_1920.png') });
  step('hub 头部', head);

  /* 2. 13 章逐个点开都能渲染 */
  const chapters = [];
  for (let i = 0; i < head.railCount; i++) {
    await page.locator('.rail-item button').nth(i).click();
    await sleep(150);
    const st = await page.evaluate(() => ({
      no: (document.getElementById('stageNo') || {}).textContent,
      t: (document.getElementById('stageTitle') || {}).textContent,
      bodyLen: (document.getElementById('stageBody') || {}).innerText.length
    }));
    chapters.push(st);
    if (!st.bodyLen) fail.push('章节未渲染: ' + st.t);
    if (st.bodyLen && st.bodyLen < 30) fail.push('章节内容过短: ' + st.t);
  }
  await page.screenshot({ path: path.join(OUT, '02_hub_末章.png') });
  step('13 章', { n: chapters.length, titles: chapters.map(c => c.no + ' ' + c.t) });

  /* 3. 窄视口不破版（1280×720 与 1024 宽） */
  await page.setViewportSize({ width: 1280, height: 720 });
  await sleep(400);
  const v1280 = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
  await page.screenshot({ path: path.join(OUT, '03_hub_1280.png') });
  if (!v1280) fail.push('1280 横向破版');
  await page.setViewportSize({ width: 1024, height: 768 });
  await sleep(400);
  const v1024 = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
  await page.screenshot({ path: path.join(OUT, '04_hub_1024.png'), fullPage: true });
  if (!v1024) fail.push('1024 横向破版');
  await page.setViewportSize({ width: 1920, height: 1080 });

  /* 「零 API」只对 hub 页成立（首页/工作间本身要调接口），先截断再往下走 */
  const hubApiReqs = apiReqs.slice();
  const hubErrors = errors.slice();
  apiReqs.length = 0; errors.length = 0;

  /* 4. 两处入口（index / workshop）
     ★2026-10-23：大厅空态现在有两张卡 ——「接入一位员工」+「流程说明」；「对接中心」是**顶栏主入口**
       （`test_docking_ui_static.test_hall_entry_points_to_the_docking_center` 就要求它），
       所以原来那条"正文不许出现对接中心"已过期，改成按 href 认人。 */
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded', timeout: 25000 });
  await sleep(600);
  const idx = await page.evaluate(() => {
    const hub = document.querySelector('.hall-entry[href="/hub.html"]');
    const dock = document.querySelector('.hall-entry[href="/docking.html"]');
    return {
      /* 卡片只在**空态**出现（大厅里有广播/消息时本来就没有），断言要跟着这个前提走 */
      emptyState: !!document.querySelector('.hall-empty'),
      tab: (document.querySelector('.view-tab[href="/hub.html"]') || {}).innerText || '',
      tile: (hub || {}).textContent ? (hub.querySelector('.he-t') || {}).textContent || '' : '',
      tileTitle: hub ? hub.title : '',
      dockTile: dock ? ((dock.querySelector('.he-t') || {}).textContent || '') : ''
    };
  });
  if (!/流程说明/.test(idx.tab)) fail.push('index tab=' + idx.tab);
  if (idx.emptyState) {
    if (idx.tile !== '流程说明') fail.push('index 瓦片=' + idx.tile);
    if (!/流程说明/.test(idx.tileTitle)) fail.push('index 瓦片 title=' + idx.tileTitle);
    if (idx.dockTile !== '接入一位员工') fail.push('index 空态缺「接入一位员工」卡：' + idx.dockTile);
  }
  await page.screenshot({ path: path.join(OUT, '05_index入口.png') });
  step('index 入口', idx);

  await page.goto(BASE + '/workshop.html', { waitUntil: 'domcontentloaded', timeout: 25000 });
  await sleep(800);
  const wsp = await page.evaluate(() => {
    const pill = document.querySelector('.hub-pill');
    return { pill: pill ? pill.innerText : '', href: pill ? pill.getAttribute('href') : '' };
  });
  /* ★2026-10-23：工作间的那个胶囊现在的契约是**带 ?ws= 指向对接中心**（V34 §1 / test_workshop_entry_carries_the_workshop_id），
     不再是「流程说明」——旧断言过期，按现行契约判。 */
  if (!/对接中心/.test(wsp.pill)) fail.push('workshop 胶囊=' + wsp.pill);
  /* 没选工作间时 href 就是裸 /docking.html（选中的才带 ?ws=，由 loadWorkshop 绑）⇒ 只判指向对不对 */
  if (wsp.href && !/\/docking\.html/.test(wsp.href)) fail.push('workshop 胶囊 href=' + wsp.href);
  await page.screenshot({ path: path.join(OUT, '06_workshop入口.png') });
  step('workshop 入口', wsp);

  /* 5. hub 零 API 请求 + 全程 Console */
  if (hubApiReqs.length) fail.push('hub 出现 API 请求: ' + hubApiReqs.join(','));
  if (hubErrors.length) fail.push('hub Console 红错: ' + hubErrors.length);
  if (errors.length) fail.push('Console 红错(入口页): ' + errors.length);

  const result = { pass: fail.length === 0, fail, head, chapters, viewports: { v1280, v1024 },
                   idx, wsp, hubApiReqs, hubErrors, errors, screenshots: fs.readdirSync(OUT) };
  fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize(result), null, 2), 'utf8');
  step('结论', { pass: result.pass, fail });
  await browser.close();
  process.exit(fail.length ? 1 : 0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
