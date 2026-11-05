// 判据（D后端-71）：监控室「会话观察窗」——真浏览器 + 真 DOM，API 用桩数据
// 用法：node monitor_session_ui_check.cjs <baseUrl> [shotPath]
//   ① 三种 watch_kind 都在左栏（画面 / 会话 / 看不了，视觉不同）
//   ② 会话格是**文字流**，不是图片
//   ③ 会话格标出谁说的（用户 / 它）
//   ④ 读不了时必须显示 why + 怎么补，且**不显示空会话、不给假按钮**
//   ⑤ 会话格钉住「它说对得上/已完成不算平台核过」
//   ⑥ 三态分开：在干活（未复核）/ 干完了（平台调它的终态）/ 干得对（证据档+采信态）
//   ⑦ 无 JS 报错
const pw = require('playwright-core');
const fs = require('fs');
const os = require('os');
const path = require('path');

const BASE = process.argv[2] || 'http://127.0.0.1:18799';
const SHOT = process.argv[3] || path.join(os.tmpdir(), 'monitor_session.png');

/** 浏览器可执行：显式给就用；否则在 ms-playwright 缓存里挑最新的 chrome.exe（换机也能跑） */
function chromeExe() {
  if (process.env.AC_MONITOR_UI_CHROME && fs.existsSync(process.env.AC_MONITOR_UI_CHROME)) return process.env.AC_MONITOR_UI_CHROME;
  const root = path.join(process.env.LOCALAPPDATA || '', 'ms-playwright');
  if (!fs.existsSync(root)) return '';
  const dirs = fs.readdirSync(root).filter((d) => /^chromium-\d+$/.test(d))
    .sort((a, b) => Number(b.split('-')[1]) - Number(a.split('-')[1]));
  for (const d of dirs) {
    for (const sub of ['chrome-win64', 'chrome-win']) {
      const p = path.join(root, d, sub, 'chrome.exe');
      if (fs.existsSync(p)) return p;
    }
  }
  return '';
}

const AGENTS = {
  supported: true, max_watch: 4, watched_on: 0,
  agents: [
    { harness_id: 'demo-win', harness_name: '示例Harness-A（桌面）', status: 'idle',
      bridge: { status: 'ready', ready: true, why: '' }, eligible: true,
      monitor: { on: false, state: 'stopped', why: '' },
      window: { hwnd: 1, can_capture: true, why: '' },
      watch_kind: 'window', session_watchable: false, session_why: '它不是协议型（没有可读的会话接口）' },
    { harness_id: 'demo-mcp', harness_name: '示例Harness-B（MCP）', status: 'idle',
      bridge: { status: '', ready: false, why: '协议型：没有窗口' }, eligible: false,
      monitor: { on: false, state: 'stopped', why: '' },
      window: { hwnd: 0, can_capture: false, why: '没有窗口' },
      watch_kind: 'session', session_watchable: true, session_why: '' },
    { harness_id: 'demo-mute', harness_name: '示例Harness-C（只对话）', status: 'idle',
      bridge: { status: '', ready: false, why: '协议型：没有窗口' }, eligible: false,
      monitor: { on: false, state: 'stopped', why: '' },
      window: { hwnd: 0, can_capture: false, why: '没有窗口' },
      watch_kind: 'none', session_watchable: false,
      session_why: '它没有暴露「读会话」工具（session_messages 之类）—— 让它补一个' }
  ]
};

(async () => {
  const exe = chromeExe();
  const launchOpt = { headless: true, args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'] };
  if (exe) launchOpt.executablePath = exe;
  const browser = await pw.chromium.launch(launchOpt);
  const page = await browser.newPage({ viewport: { width: 1500, height: 980 } });
  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 120)); });
  const apiHits = [];
  page.on('request', (r) => { if (/\/api\//.test(r.url())) apiHits.push(r.url().replace(BASE, '')); });

  await page.route('**/api/monitor/health', (r) => r.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify({ supported: true, max_watch: 4, stream_available: false }) }));
  await page.route('**/api/monitor/agents', (r) => r.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify(AGENTS) }));
  await page.route('**/api/monitor/watched', (r) => r.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify({ watched: [] }) }));
  await page.route('**/api/monitor/*/session**', (r) => {
    const u = r.request().url();
    if (u.includes('demo-mute')) {
      return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        capable: false, why: '它没有暴露「读会话」工具（session_messages 之类）',
        how_to_read: '在它的 MCP 设置里允许 session_messages', items: [] }) });
    }
    return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      capable: true, tool: 'session_messages', read_at: '2026-11-05T11:20:00',
      cached: false, age_ms: 0, note: '这是它自己库里的内容，平台只读一次、没存副本',
      items: [
        { role: 'user', text: '把这份表格整理成三列', at: '2026-11-05T11:10:02' },
        { role: 'assistant', text: '已完成，三列都在 table.md 里了，对得上。', at: '2026-11-05T11:12:40' }] }) });
  });
  await page.route('**/api/harness/*/activity**', (r) => r.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify({ counts: { progress: 3, outbound: 2, platform_tool_calls: 0 },
      work_trace: [ { kind: 'progress', judge: 'unverified', source: 'self_reported', text: 'a' },
                    { kind: 'outbound_call', judge: 'verified', source: 'platform_observed', text: 'b' } ],
      caveats: [] }) }));

  await page.goto(BASE + '/monitor.html?session=demo-mcp,demo-mute', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);

  const left = await page.evaluate(() => ({
    cards: document.querySelectorAll('.acard').length,
    tags: [...document.querySelectorAll('.acard .tag')].map((x) => (x.textContent || '').trim() + '|' + x.className.replace('tag ', '')),
    sessionSw: !!Array.from(document.querySelectorAll('.acard .sw')).find((b) => /看会话/.test(b.textContent))
  }));

  const wall = await page.evaluate(() => {
    const q = (s) => document.querySelector(s);
    const stMcp = q('#stile-demo-mcp');
    const stMute = q('#stile-demo-mute');
    return {
      sessionTiles: document.querySelectorAll('.tile.kind-session').length,
      mcpHasImg: !!(stMcp && stMcp.querySelector('img')),
      mcpMsgs: stMcp ? stMcp.querySelectorAll('.sm').length : 0,
      mcpWho: stMcp ? [...stMcp.querySelectorAll('.sm .who')].map((x) => x.textContent.trim()) : [],
      mcpCaution: (q('#snote-demo-mcp') || {}).innerText || '',
      muteWhy: (q('#sbody-demo-mute') || {}).innerText || '',
      muteEmptyList: stMute ? stMute.querySelectorAll('.sm').length : -1,
      muteFakeBtn: stMute ? [...stMute.querySelectorAll('.sess-btns button')].filter((b) => /读一次/.test(b.textContent || '')).length : -1
    };
  });

  await page.evaluate(() => {
    const b = [...document.querySelectorAll('.acard button')].find((x) => /状态/.test(x.textContent));
    if (b) b.click();
  });
  await page.waitForTimeout(900);
  const tri = await page.evaluate(() => (document.querySelector('.acard .tri') || {}).innerText || '');

  await page.screenshot({ path: SHOT });
  await browser.close();

  const checks = [
    ['① 左栏三种 kind 都在，且带不同标签', left.tags.some(t => t.includes('window')) && left.tags.some(t => t.includes('session')) && left.tags.some(t => t.includes('none'))],
    ['② 右墙出现会话格（与画面格并列）', wall.sessionTiles === 2],
    ['③ 会话格是文字流不是图片', wall.mcpHasImg === false && wall.mcpMsgs === 2],
    ['④ 会话格标出谁说的', wall.mcpWho.some(w => w.startsWith('用户')) && wall.mcpWho.some(w => w.startsWith('它'))],
    ['⑤ 读不了时显示 why + 怎么补，不显示空会话', /读会话」工具/.test(wall.muteWhy) && /怎么补/.test(wall.muteWhy) && wall.muteEmptyList === 0],
    ['⑥ 读不了时**没有**假按钮「现在读一次」', wall.muteFakeBtn === 0],
    ['⑦ 会话格钉住「不算平台核过」', /不算平台核过/.test(wall.mcpCaution)],
    ['⑧ 三态分开显示', /在干活/.test(tri) && /干完了/.test(tri) && /干得对/.test(tri)],
    ['⑨ 无 JS 报错', errs.length === 0]
  ];
  let bad = 0;
  for (const [name, ok] of checks) { if (!ok) bad++; console.log((ok ? '  ✔ ' : '  ✘ ') + name); }
  console.log('\n左栏：' + JSON.stringify(left));
  console.log('右墙：' + JSON.stringify(wall));
  console.log('三态：' + tri.replace(/\n/g, ' | '));
  console.log('API 调用：' + apiHits.slice(0, 8).join(' , '));
  console.log('截图：' + SHOT);
  if (errs.length) console.log('JS 错误：' + errs.slice(0, 3).join(' ; '));
  console.log(bad ? ('\n结论：**' + bad + ' 格没过**') : '\n结论：**9 格全过**');
  process.exit(bad ? 1 : 0);
})().catch((e) => { console.log('脚本异常：' + e.message); process.exit(2); });
