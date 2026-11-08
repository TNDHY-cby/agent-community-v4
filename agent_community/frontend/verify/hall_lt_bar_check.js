/**
 * 大厅「今天该管谁」摘要条 —— 可见性回归判据（不是截图好看，是"真没被盖住"）
 *
 * 背景：2026-11-08 老板反馈"大厅看不到摘要条"，D后端-86/87 定位到
 * .hall-cards 是 position:absolute + z-index:6 的不透明玻璃覆盖层，
 * .lt-bar 作为 .hall-view 直接子节点被整块压住 —— 接口全通、判据全绿、界面空白。
 *
 * 判据（任一不满足即 FAIL）：
 *  1. #ltBar 未被 hidden，且是 .hall-cards 的后代（待在玻璃层里才浮得起来）
 *  2. elementFromPoint 在摘要条上中下若干采样点，命中的必须是 #ltBar 内部元素
 *     —— 这是"遮挡"的唯一硬证据，CSS 写得再对也不能替代
 *  3. 展开后高度变化，--cards-h 必须同步（消息区靠它让位）
 *  4. 页面无 console error / pageerror
 *  5. 数字口径同源：「共 N 个」必须等于接口 totals.workshops，
 *     且 totals 自洽（workshops_with_todo + empty_workshops = workshops）
 *     —— D后端-88 §3 提出「19 vs 20」差异后补的防漂移判据
 *
 * 用法：node frontend/verify/hall_lt_bar_check.js [--base http://127.0.0.1:18920] [--shots <dir>]
 * 依赖：playwright；需要本地服务已起（默认 18920）。
 * 环境（D后端-88 §2 已装好，dev-only 不进仓库）：
 *   NODE_PATH / AC_PLAYWRIGHT_PATH = D:\DSH工作区1\dsh-tooling\pw\node_modules
 * ⚠ node 必须用系统 node（C:\Program Files\nodejs\node.exe，本机 v24）；
 *   用 Marvis 自带 node（v22, binaries\node\current）跑会
 *   "Subprocess crashed with STATUS_ACCESS_VIOLATION (0xC0000005)"。
 */
const fs = require('fs');
const path = require('path');

function arg(name, dflt) {
  const i = process.argv.indexOf(name);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}

const BASE = arg('--base', 'http://127.0.0.1:18920');
const SHOTS = arg('--shots', '');

let playwright;
try {
  playwright = require('playwright');
} catch (e) {
  console.error('缺少 playwright：npm i playwright（或设 NODE_PATH 指向已装目录）');
  process.exit(2);
}

const SIZES = [
  [1440, 900],
  [1440, 1600],
];
const fails = [];

function check(ok, label, detail) {
  console.log((ok ? '  OK    ' : '  FAIL  ') + label + (detail ? '  ' + detail : ''));
  if (!ok) fails.push(label);
}

(async () => {
  const browser = await playwright.chromium.launch({
    executablePath: process.env.CHROME_PATH || undefined,
    headless: true,
  });

  for (const [w, h] of SIZES) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
    const page = await ctx.newPage();
    const errs = [];
    page.on('pageerror', (e) => errs.push('pageerror: ' + e.message));
    page.on('console', (m) => {
      if (m.type() === 'error') errs.push('console: ' + m.text());
    });

    await page.goto(BASE + '/index.html', { waitUntil: 'networkidle', timeout: 90000 });
    await page.waitForTimeout(3500);

    const closed = await page.evaluate(() => {
      const bar = document.getElementById('ltBar');
      const cards = document.getElementById('hallCards');
      if (!bar || !cards) return { err: '缺少 #ltBar 或 #hallCards' };
      const r = bar.getBoundingClientRect();
      const pts = [
        [r.left + 20, r.top + r.height / 2],
        [r.left + r.width / 2, r.top + r.height / 2],
        [r.left + Math.min(r.width - 20, 200), r.bottom - 6],
      ];
      const hits = pts.map(([x, y]) => {
        const el = document.elementFromPoint(x, y);
        return { x: Math.round(x), y: Math.round(y), inside: !!(el && bar.contains(el)),
          top: el ? el.id || el.className || el.tagName : null };
      });
      return {
        hidden: bar.hidden,
        inCards: cards.contains(bar),
        rect: { y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
        hits,
        allInside: hits.every((x) => x.inside),
        sum: (document.getElementById('ltSum') || {}).textContent || '',
        /* ★2026-11-08（D后端-88 §3）：数字口径 —— 页面「共 N 个」必须与接口 totals 同源，
           否则工作间增删后两个数字会各说各话。 */
        sumTotal: (() => {
          const t = (document.getElementById('ltSum') || {}).textContent || '';
          const m = t.match(/共\s*(\d+)\s*个/);
          return m ? Number(m[1]) : null;
        })(),
        totals: (window._ltData && window._ltData.totals) || null,
        cardsVar: getComputedStyle(document.documentElement).getPropertyValue('--cards-h').trim(),
      };
    });

    console.log(`\n[${w}x${h}] 收起态`);
    if (closed.err) {
      check(false, closed.err);
      await ctx.close();
      continue;
    }
    check(!closed.hidden, '摘要条没有被 hidden');
    check(closed.inCards, '摘要条在 .hall-cards 内部（玻璃层同一个堆叠上下文）');
    check(closed.allInside, 'elementFromPoint 采样点全部命中摘要条内部（无遮挡）',
      JSON.stringify(closed.hits.map((x) => (x.inside ? 'ok' : 'BLOCKED:' + x.top))));
    check(/待办|全清|取不到/.test(closed.sum), '摘要文案有内容', JSON.stringify(closed.sum.slice(0, 60)));

    /* 判据 5：数字口径同源（D后端-88 §3）
       —— 页面「共 N 个」来自 totals.workshops，必须与接口一致；
       —— totals 自身要自洽：有事的间 + 空的间 = 总间数。 */
    const tt = closed.totals || {};
    check(closed.sumTotal == null || tt.workshops == null || closed.sumTotal === tt.workshops,
      '「共 N 个」与接口 totals.workshops 同源',
      `页面 ${closed.sumTotal} / 接口 ${tt.workshops}`);
    check(tt.workshops == null ||
      (Number(tt.workshops_with_todo || 0) + Number(tt.empty_workshops || 0)) === Number(tt.workshops),
      '接口 totals 自洽（有事的间 + 空的间 = 总数）',
      `${tt.workshops_with_todo || 0} + ${tt.empty_workshops || 0} = ${tt.workshops}`);

    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `hall_ltbar_${w}x${h}.png`) });

    await page.evaluate(() => window.ltToggle && window.ltToggle());
    await page.waitForTimeout(400);
    const open = await page.evaluate(() => {
      const bar = document.getElementById('ltBar');
      const list = document.getElementById('ltList');
      const r = bar.getBoundingClientRect();
      const el = document.elementFromPoint(r.left + 30, r.bottom - 12);
      return { open: !list.hidden, h: Math.round(r.height), bottomInside: !!(el && bar.contains(el)),
        cardsVar: getComputedStyle(document.documentElement).getPropertyValue('--cards-h').trim() };
    });
    console.log(`[${w}x${h}] 展开态`);
    check(open.open, '展开后明细可见');
    check(open.h > closed.rect.h, '展开后高度变大', `收起 ${closed.rect.h}px → 展开 ${open.h}px`);
    check(open.bottomInside, '展开后底部也在摘要条内部（不被卡片盖住）');
    check(open.cardsVar && open.cardsVar !== '0px', '--cards-h 同步更新', open.cardsVar);
    check(errs.length === 0, '无 console error / pageerror', errs.slice(0, 3).join(' | '));

    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `hall_ltbar_open_${w}x${h}.png`) });
    await ctx.close();
  }

  await browser.close();
  console.log('\n结论：' + (fails.length ? `**FAIL ${fails.length}** —— ` + fails.join('；') : '**全过**'));
  process.exit(fails.length ? 1 : 0);
})().catch((e) => {
  console.error('异常：' + e.message);
  process.exit(2);
});
