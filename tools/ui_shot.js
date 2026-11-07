// 平台页面截图（本项目通用）。由 tools/ui_shot.py 调用，也可单独用：
//   node tools/ui_shot.js <url> <out.png> [--full] [--wait=2500] [--size=1440x900]
// 需要：NODE_PATH 指向含 playwright 的 node_modules；CHROME_PATH 指 chromium 可执行（可省略，走 playwright 默认）。
const { chromium } = require('playwright');

(async () => {
  const args = process.argv.slice(2);
  const url = args[0];
  const out = args[1];
  if (!url || !out) {
    console.error('用法: node ui_shot.js <url> <out.png> [--full] [--wait=ms] [--size=WxH]');
    process.exit(2);
  }
  const full = args.includes('--full');
  const waitArg = args.find((a) => a.startsWith('--wait='));
  const waitMs = waitArg ? parseInt(waitArg.split('=')[1], 10) : 2500;
  const sizeArg = args.find((a) => a.startsWith('--size='));
  const [w, h] = (sizeArg ? sizeArg.split('=')[1] : '1440x900').split('x').map(Number);

  let browser;
  try {
    browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined, headless: true });
  } catch (e) {
    console.error('LAUNCH_FAIL ' + e.message);
    process.exit(3);
  }
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));

  const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(waitMs);
  await page.screenshot({ path: out, fullPage: full });

  console.log(JSON.stringify({
    ok: true, url, out, full,
    httpStatus: resp ? resp.status() : null,
    title: await page.title(),
    consoleErrors: errors.slice(0, 5),
  }));
  await browser.close();
})().catch((e) => {
  console.error('FAIL ' + e.message);
  process.exit(1);
});
