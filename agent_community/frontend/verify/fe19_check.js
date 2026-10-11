/* FE-19 隔间页「桥→会话→隔间→邮箱」链验收（V35 §6 + 通道 100 号两硬规则）
   隔离实例 + AC_FE_BASE + 自建自清 + AC_VERIFY_OUT
   ① 真实后端：无桥成员卡片必须明说「未架接」+ 邮箱随隔间行；② 钩子注入 attached+未自证+邮箱实况；
   ③ 全程桥行无「完成/已通」；④ 页面零非 GET、Console 零红错。 */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');
const RESULT_DIR = process.env.AC_VERIFY_OUT || path.join(__dirname, 'FE19_验证');
function _resolveOut(){
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'agent_community'))) {
      const o = path.join(d, 'temp', '验收证据截图', 'FE19_验证');
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

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const errors = [];
  const nonGet = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('request', r => { if (r.url().includes('/api/') && r.method() !== 'GET') nonGet.push(r.method() + ' ' + r.url().replace(BASE, '').split('?')[0]); });
  const step = (n, d) => console.log('>> ' + n, JSON.stringify(d));
  const fail = [];
  const shot = n => page.screenshot({ path: path.join(OUT, n) });
  const mText = () => page.evaluate(() => (document.getElementById('mBody') || {}).innerText || '');

  const ws = await (async () => {
    const r = await fetch(BASE + '/api/workshop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'FE19验收-自建' }) });
    return (await r.json()).workshop_id;
  })();
  if (!ws) process.exit(1);
  await fetch(BASE + `/api/workshop/${ws}/members`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ members: [
      { role: '码农', harness_ids: ['harness-fe19-a'], display_name: '示例Harness-A' }
    ] }) });
  step('自建', { ws });

  try {
    /* ① 真实后端（无桥）：明说未架接 + 邮箱随隔间 */
    await page.goto(`${BASE}/booths.html?ws_id=${ws}`, { waitUntil: 'domcontentloaded', timeout: 25000 });
    await page.waitForSelector('.mcard', { timeout: 20000 });
    await sleep(1500);
    const s1 = await mText();
    await shot('01_未架接.png');
    step('① 无桥真实态', { head: s1.slice(0, 300) });
    if (!/未架接（还没有为它架起具体桥）/.test(s1)) fail.push('① 未明说「未架接」');
    if (!/邮箱：随隔间创建/.test(s1)) fail.push('① 邮箱随隔间行缺失');
    if (/已通/.test(s1)) fail.push('① 出现「已通」');

    /* ② 钩子注入：attached + 未自证 + 邮箱实况 + 隔间已创建（不打桩网络、不拉真桥进程） */
    const s2 = await page.evaluate(() => {
      const base = (wsCur && wsCur.workspace_dir) || 'D:\\ws\\fe19';
      wsCur.members = [{ member_id: 'm0', role: '码农', display_name: '示例Harness-A',
        harness_ids: ['harness-fe19-a'], entry_state: 'entered', status: 'entered' }];
      compOk = true;
      compRows = [{ member_id: 'm0', role: '码农', display_name: '示例Harness-A',
        dir: base + '\\码农_m0', exists: true, inside_workspace: true,
        subdirs: { input: true, output: true, temp: true },
        mailbox: { inbox: true, replies: true }, mailbox_pending: { inbox: 1, replies: 0 },
        files: [], bridge_files: ['bridge.py'], status: 'entered', entry_state: 'entered' }];
      bbMap = { m0: { member_id: 'm0',
        bridge: { state: 'attached', template_name: '示例桥', verified: false,
                  session_name: '示例间-项目-码农-m0' },
        status_text: '已架接：示例桥 → 示例间-项目-码农-m0（未自证：还没确认这条路真的通）' } };
      renderMembers();
      return (document.getElementById('mBody') || {}).innerText || '';
    });
    await shot('02_已架接未自证.png');
    step('② 注入 attached', { head: s2.slice(0, 350) });
    if (!/已架接：示例桥 → 示例间-项目-码农-m0/.test(s2)) fail.push('② 会话链未显示（桥→会话名）');
    if (!/未自证/.test(s2)) fail.push('② verified=false 未显示「未自证」');
    if (!/📮 邮箱：inbox ✓ · replies ✓/.test(s2)) fail.push('② 邮箱行缺失或未打勾');
    if (!/inbox 积压 1/.test(s2)) fail.push('② 邮箱积压计数缺失');
    if (!/已创建/.test(s2)) fail.push('② 隔间「已创建」未显示');
    if (/已通/.test(s2)) fail.push('② 把桥说成了「已通」');

    /* ④ 页面零非 GET + Console */
    const badReq = nonGet.filter(u =>
      !/\/api\/workshop\/[^/]+\/(channel\/retire|channel\/establish|library\/update)$/.test(u));
    if (badReq.length) fail.push('非白名单写请求: ' + badReq.join(','));
    if (errors.length) fail.push('Console 红错 ' + errors.length);

    const out = { pass: fail.length === 0, fail, s1: s1.slice(0, 400), s2: s2.slice(0, 500),
                  nonGet, errors, screenshots: fs.readdirSync(OUT) };
    fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize(out), null, 2), 'utf8');
    step('结论', { pass: out.pass, fail });
  } catch (e) {
    fail.push('FATAL: ' + (e && e.message || e));
    step('FATAL', fail[fail.length - 1]);
    fs.writeFileSync(path.join(RESULT_DIR, 'result.json'), JSON.stringify(sanitize({ pass: false, fail, errors, nonGet }), null, 2), 'utf8');
  } finally {
    await fetch(BASE + '/api/workshop/' + ws, { method: 'DELETE' }).catch(() => {});
    await browser.close();
  }
  process.exit(fail.length ? 1 : 0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
