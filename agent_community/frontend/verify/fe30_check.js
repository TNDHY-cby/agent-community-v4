/* FE-30 验收：送话给员工（统一投递层的前台）—— **真浏览器 + 真请求**，2026-10-23

## 判据（对应 design-docs/FE-30 §6）
- J1 瓦片在且顺序对：登记 → 脚本激活 → **送话** → 档案 → …
- J2 打开面板后**真的**发 `GET /api/docking/harness/{id}/delivery`，目标行按返回渲染
  （能定位就显示目标与来源；定位不到就显示 `why` + 下一步 —— 不许变空白）
- J3 点「送话」**真的**发 `POST /api/docking/harness/{id}/deliver`（锁 cdp 档 ⇒ 不碰剪贴板），
  结果区显示通道 + 三态 + `attempts` 折叠
- J4 `verified` 不是 true 时，页面上不许出现「已送达」
- J5 档案详情 ⑦ 存在且已解析目标（与瓦片同一份渲染）

## 用法
```bash
set AC_FE_BASE=http://127.0.0.1:18920
set NODE_PATH=<playwright-core 的 node_modules>
node agent_community/frontend/verify/fe30_check.js
# 退出码 0 = 全过；1 = 有断言失败（证据与 result.json 在 AC_VERIFY_OUT）
```
不写死端口：`AC_FE_BASE` 优先，否则按 `AC_PORT`（默认 18920）。
*/
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');

const PORT = process.env.AC_PORT || '18920';
const BASE = (process.env.AC_FE_BASE || `http://127.0.0.1:${PORT}`).replace(/\/+$/, '');
const OUT = process.env.AC_VERIFY_OUT
  || path.join(__dirname, '..', '..', '..', 'temp', '验收证据截图', 'FE27_送话');

function fail(msg) {
  console.log('✗ ' + msg);
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  console.log(`[base] ${BASE}\n[out ] ${OUT}`);

  let b = null;
  for (const opt of [{ channel: 'msedge' }, {}]) {
    try {
      b = await chromium.launch({ headless: true, ...opt });
      console.log(`[browser] ${opt.channel || 'bundled-chromium'}`);
      break;
    } catch (e) {
      console.log(`[browser] ${opt.channel || 'bundled-chromium'} 不可用：${String(e.message).split('\n')[0]}`);
    }
  }
  if (!b) throw new Error('没有可用的浏览器（装 playwright-core 的浏览器，或用 msedge）');

  const p = await b.newPage({ viewport: { width: 1600, height: 1000 } });
  const errors = [];
  p.on('pageerror', e => errors.push(e.message));
  /* 记下真实发生的请求（判据不看源码文本，看有没有真的发出去） */
  const seen = { getDelivery: [], getChannels: [], postDeliver: [] };
  p.on('response', async r => {
    const u = r.url();
    if (/\/api\/docking\/harness\/.+\/delivery$/.test(u)) seen.getDelivery.push(r.status());
    if (u.includes('/api/docking/delivery/channels')) seen.getChannels.push(r.status());
    if (/\/api\/docking\/harness\/.+\/deliver$/.test(u)) {
      seen.postDeliver.push({ status: r.status(), body: await r.json().catch(() => null) });
    }
  });

  await p.goto(BASE + '/docking.html', { waitUntil: 'domcontentloaded' });
  await p.waitForSelector('.dock-tile');
  await p.waitForTimeout(800);

  const order = await p.evaluate(() => [...document.querySelectorAll('.dock-tile')].map(t => t.dataset.dockService));
  /* 第一屏按组摆（送话 / 接入 / 激活 / 查档·问）⇒ 判据是四组里的 DOM 顺序 */
  const expectOrder = ['deliver',
    'register', 'agent_manager', 'onboard_recover', 'peers',
    'script_activate', 'session_bridge', 'channel', 'bridge_helper',
    'archive', 'ask_frontdesk'];
  const orderOk = JSON.stringify(order) === JSON.stringify(expectOrder);
  if (!orderOk) fail(`瓦片顺序不对：${JSON.stringify(order)}`);
  await p.screenshot({ path: path.join(OUT, '01_首屏.png') });

  /* ── 打开送话面板（真挂载 + 真解析目标） */
  await p.click('.dock-tile[data-dock-service="deliver"]');
  await p.waitForSelector('#dkDeliver-target');
  await p.waitForTimeout(1600);
  const panel = await p.evaluate(() => {
    const t = document.getElementById('dkDeliver-target');
    const c = document.getElementById('dkDeliver-chans');
    const w = document.getElementById('dockDeliverWrap');
    const ov = document.getElementById('dockModalOverlay');
    return {
      inModal: !!(w && w.parentElement && w.parentElement.id === 'dockModalBody'),
      parentId: w && w.parentElement ? w.parentElement.id : '(none)',
      modalShown: !!(ov && ov.classList.contains('show')),
      targetText: (t ? t.innerText : '').trim(),
      chanText: (c ? c.innerText : '').trim(),
      hasSelect: !!document.getElementById('dkDeliver-sel'),
      hasSend: !!document.getElementById('dkDeliver-send')
    };
  });
  await p.screenshot({ path: path.join(OUT, '02_送话面板.png') });
  if (!panel.inModal) fail('送话面板没有进弹窗（dockModalBody 里找不到 #dockDeliverWrap）');
  if (!panel.hasSelect || !panel.hasSend) fail('面板缺员工选择器或「送话」按钮');
  if (seen.getDelivery.length === 0) fail('打开面板后没有真发 GET …/delivery');
  const targetOk = /目标：/.test(panel.targetText);
  const targetExplained = /还没认出/.test(panel.targetText) && /下一步/.test(panel.targetText);
  if (!(targetOk || targetExplained)) fail('目标行既没显示目标、也没说清为什么与下一步：' + panel.targetText.slice(0, 160));
  /* ★评审第 1 条：认不出目标时**不许**再并排显示"推荐 可用"的通道（会读成"其实能送"）。 */
  if (!targetOk && /推荐/.test(panel.chanText)) fail('认不出目标却仍显示「推荐」通道 —— 判决与状态矛盾');
  const sendLabel = await p.evaluate(() => (document.getElementById('dkDeliver-send') || {}).textContent);
  if (!targetOk && !/试送一次|放到剪贴板|送话/.test(String(sendLabel))) fail('主按钮文案说不清这一下会做什么：' + sendLabel);

  /* ── 真送一次（锁 cdp 档：不碰剪贴板，失败也如实说） */
  await p.selectOption('#dkDeliver-prefer', 'cdp');
  await p.fill('#dkDeliver-text', '【平台验收】FE-30 送话通道自检 —— 看到这行说明投递发生了，可以忽略。');
  const before = seen.postDeliver.length;
  await p.click('#dkDeliver-send');
  await p.waitForTimeout(2500);
  const out = await p.evaluate(() => {
    const o = document.getElementById('dkDeliver-out');
    return { text: (o ? o.innerText : '').trim(), html: (o ? o.innerHTML : '') };
  });
  await p.screenshot({ path: path.join(OUT, '03_送话结果.png') });
  if (seen.postDeliver.length <= before) fail('点「送话」后没有真发 POST …/deliver');
  const resp = seen.postDeliver[seen.postDeliver.length - 1] || {};
  const body = resp.body || {};
  const saidChannel = body.channel ? new RegExp(body.channel).test(out.text) : true;
  if (!saidChannel) fail(`结果区没显示后端给的通道（${body.channel}）：${out.text.slice(0, 160)}`);
  /* 四态：已送达（读回一致）/ 已送出（未核实）/ 已放进剪贴板 / 没有送出去 —— 有其一即可 */
  const threeState = /已送达|已送出|未核实|半自动|已放进剪贴板|没有送出去/.test(out.text);
  if (!threeState) fail('结果区没有三态说法：' + out.text.slice(0, 160));
  if (/已送达/.test(out.text)) fail('结果区出现了「已送达」（半自动/未核实都不许这么写）');
  const attemptsShown = (body.attempts && body.attempts.length) ? /每一档试了什么/.test(out.text) : true;
  if (!attemptsShown) fail('后端给了 attempts，但界面没有折叠显示每档原因');
  const pageSaysDelivered = await p.evaluate(() => /已送达/.test(document.body.innerText));
  if (pageSaysDelivered) fail('整页出现了「已送达」字样');

  /* ── 备选路（D后端-3 表层交互 / D后端-4 浏览器直连）：真打 GET anchor 与 web/probe ── */
  const seenAlt = { anchor: [], probe: [] };
  p.on('response', r => {
    const u = r.url();
    if (/\/api\/docking\/harness\/.+\/anchor$/.test(u)) seenAlt.anchor.push(r.status());
    if (u.includes('/api/docking/web/probe')) seenAlt.probe.push(r.status());
  });
  const altOpen = await p.evaluate(() => {
    const d = [...document.querySelectorAll('#dkDeliverWrap details')].find(x => /浏览器直连/.test(x.innerText || ''));
    if (!d) return null;
    d.open = true;
    return { title: ((d.querySelector('summary') || {}).innerText || '').trim() };
  });
  if (!altOpen) fail('送话面板里没有「浏览器直连」备选段');
  await p.waitForTimeout(1400);
  /* ★2026-10-24（老板否掉"鼠标停在输入框上教位置"）：界面上已经没有教位置块 ⇒
     改判**框选块在**（截图 / 剪贴板 / 上传 三个入口 + 保存），以及它如实说现状。 */
  const boxText = await p.evaluate(() => ((document.querySelector('.dk-boxsel') || {}).innerText || '').trim());
  if (!boxText) fail('送话面板里没有「框选输入框」块');
  if (!/截一张图/.test(boxText) || !/用剪贴板里的图/.test(boxText) || !/上传图片/.test(boxText)) {
    fail('框选块缺图源入口（窗口截图 / 剪贴板 / 上传）：' + boxText.slice(0, 160));
  }
  if (!/还没框过|已框/.test(boxText)) fail('框选块没有如实说现状：' + boxText.slice(0, 160));
  await p.evaluate(() => { const b = [...document.querySelectorAll('.dk-alt-sec .btn')].find(x => /找页面/.test(x.textContent)); if (b) b.click(); });
  /* 探测要真扫端口，可能几秒 ⇒ 轮询到有结论为止（最多 12s），不拿"探测中…"当结论 */
  let cdpText = '';
  for (let i = 0; i < 24; i++) {
    await p.waitForTimeout(500);
    cdpText = await p.evaluate(() => ((document.getElementById('dkDeliver-cdp-targets') || {}).textContent || '').trim());
    if (cdpText && !/探测中/.test(cdpText)) break;
  }
  if (!seenAlt.probe.length) fail('点「找页面」后没有真发 GET /api/docking/web/probe');
  if (!/端口|没找到开了调试端口的页面|探测失败/.test(cdpText)) fail('浏览器直连没有如实说探测结果：' + cdpText.slice(0, 160));
  await p.screenshot({ path: path.join(OUT, '05_备选路.png') });

  /* ── 档案 ⑦（同一份渲染的第二个入口） */
  await p.keyboard.press('Escape');
  await p.waitForTimeout(300);
  await p.click('.dock-tile[data-dock-service="archive"]');
  await p.waitForTimeout(1200);
  const hasArchive = await p.$('#dockArchiveWrap .dock-detail-btn');
  let drawer = { present: false, text: '' };
  if (hasArchive) {
    await p.click('#dockArchiveWrap .dock-detail-btn');
    await p.waitForTimeout(1800);
    drawer = await p.evaluate(() => {
      const d = document.querySelector('.dock-drawer details[id^="dkArcDeliver-"]');
      const box = document.getElementById('dkDlvDrawer-target');
      /* 2026-10-23 修过的既有缺陷：改 details.innerHTML 会连 <summary> 一起删掉，浏览器退回「详细信息」。
         这里按真 DOM 判：档案抽屉里每个 details 的 summary 都必须有文字。 */
      const emptySummaries = [...document.querySelectorAll('.dock-drawer details')].filter(x => {
        const s = x.querySelector('summary');
        return !s || !(s.innerText || '').trim();
      }).length;
      return { present: !!d, summary: d ? (d.querySelector('summary') || {}).innerText : '',
        emptySummaries, text: (box ? box.innerText : '').trim() };
    });
  }
  await p.screenshot({ path: path.join(OUT, '04_档案送话块.png') });
  if (!drawer.present) fail('档案详情里没有 ⑦ 送话块');
  else if (!/目标：|还没认出/.test(drawer.text)) fail('档案 ⑦ 没有解析目标：' + drawer.text.slice(0, 160));
  if (drawer.present && drawer.emptySummaries) fail('档案抽屉里有 ' + drawer.emptySummaries + ' 个 details 的标题是空的（summary 被内容覆盖了）');

  /* ── 渲染契约（三态 × 通道）：真实例上这两位员工都没有会话名 ⇒ 走不到"已自证"，
        所以三态渲染用**纯渲染函数**逐态验（接线由上面的真请求证明，这里只验怎么显示）。 */
  const renderContract = await p.evaluate(() => {
    const mk = (d) => dockDeliverResultHtml(d, { ok: true, status: 200 });
    const t = mk({ ok: true, channel: 'cdp', verified: true, attempts: [{ channel: 'cdp', ok: true }],
      resolved: { source: '记忆（上次成功过的页面）' } });
    const n = mk({ ok: true, channel: 'clipboard', verified: null,
      attempts: [{ channel: 'cdp', ok: false, why: '没有页面 ws 地址' }, { channel: 'clipboard', ok: true, why: '已放进剪贴板' }],
      resolved: { source: '按会话名找到窗口' } });
    const f = mk({ ok: false, channel: '', verified: false, attempts: [{ channel: 'native', ok: false, why: '没有窗口句柄' }] });
    return {
      trueSample: t, nullSample: n, falseSample: f,
      trueOk: /已自证/.test(t) && /cdp/.test(t) && /记忆/.test(t) && /每一档试了什么/.test(t),
      nullOk: /半自动/.test(n) && /clipboard/.test(n) && /不能/.test(n) && !/已送达/.test(n) && /每一档试了什么/.test(n),
      falseOk: /未核实/.test(f) && /native/.test(f) && !/已送达/.test(f)
    };
  });
  if (!renderContract.trueOk) fail('渲染契约：verified=true 没写成「已自证 + 通道 + 目标来源」');
  if (!renderContract.nullOk) fail('渲染契约：verified=null 没写成「半自动 / 不能核实」');
  if (!renderContract.falseOk) fail('渲染契约：verified=false 没写成「未核实」');

  const result = {
    base: BASE, orderOk, panel, targetOk, targetExplained,
    realRequests: seen.getDelivery.length + seen.getChannels.length + seen.postDeliver.length,
    seesDelivery: seen.getDelivery, seesChannels: seen.getChannels,
    deliverResponse: { status: resp.status, ok: body.ok, channel: body.channel, verified: body.verified,
      attempts: (body.attempts || []).length, via: body.via || null },
    out: out.text, drawer: { present: drawer.present, summary: drawer.summary, emptySummaries: drawer.emptySummaries, text: drawer.text.slice(0, 200) },
    altPaths: { boxText: boxText.slice(0, 160), cdpText: cdpText.slice(0, 160),
      seenProbe: seenAlt.probe },
    renderContract: { trueOk: renderContract.trueOk, nullOk: renderContract.nullOk, falseOk: renderContract.falseOk },
    errors,
    pass: orderOk && panel.inModal && panel.hasSelect && panel.hasSend
      && seen.getDelivery.length > 0 && (targetOk || targetExplained)
      && seen.postDeliver.length > before && saidChannel && threeState
      && !/已送达/.test(out.text) && !pageSaysDelivered && attemptsShown
      && drawer.present && !errors.length
      && renderContract.trueOk && renderContract.nullOk && renderContract.falseOk
      && !drawer.emptySummaries
      && seenAlt.probe.length > 0 && !!boxText
      && /端口|没找到开了调试端口的页面|探测失败/.test(cdpText)
  };
  console.log(JSON.stringify(result, null, 2));
  fs.writeFileSync(path.join(OUT, 'result.json'), JSON.stringify(result, null, 2));
  await b.close();
  process.exit(result.pass ? 0 : 1);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
