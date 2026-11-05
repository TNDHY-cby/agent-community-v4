/* ══ 监控室（V88 / DSH 246-248）════════════════════════════════
 * 口径：快照墙 + 连续画面流（PNG 分帧，V89）——不是 MJPEG，别写错对外说法；
 * eligible=false 照样列出、置灰 + bridge.why 原话；
 * window.can_capture 三态：true / false(带原因,黄) / null(还没试过,灰字不标红)；
 * monitor.state=stopped 显示 why（如"超过 60 秒没人看，已自动停抓"）；
 * AI 观察默认关：页面只放一个总开关，不在页面上每帧触发；用户不挑窗口，认不出才走 /sources 手动指定一次。
 */
'use strict';
const $ = id => document.getElementById(id);
let AI_ON = false;
let MON_MAX = '–';
const timers = {};   /* hid -> setInterval，1 帧/秒换 t */
const SESSION_OPEN = new Set();  /* 开着会话观察窗的 harness_id（画面抓取名单是另一回事） */
const sessTimers = {};           /* hid -> setInterval，6 秒刷一次（后端 5 秒短缓存，正好命中） */
/* 深链：/monitor.html?session=<hid>[,<hid>] 直接打开这些会话观察窗（也方便截图/判据） */
try{
  new URLSearchParams(location.search).getAll('session').join(',').split(',')
    .map(x => x.trim()).filter(Boolean).forEach(x => SESSION_OPEN.add(x));
}catch(e){ /* 老浏览器不支持 URLSearchParams：忽略深链，功能照常 */ }

function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

async function monRefresh(){
  try{
    const h = await (await fetch('/api/monitor/health')).json();
    $('mMax').textContent = (h.max_watch != null ? h.max_watch : '–');
    MON_STREAM_OK = h.stream_available === true;
    $('mHealth').textContent = (h.ai && h.ai.ran != null) ? ('AI:' + (h.ai.ran ? '开' : '关')) : '';
  }catch(e){ $('mHealth').textContent = '健康检查失败：' + e.message; }
  await monLoadAgents();
  await monLoadWatched();
}

/* ── 左栏：候选列表 ── */
async function monLoadAgents(){
  const box = $('agentsList');
  try{
    const d = await (await fetch('/api/monitor/agents')).json();
    const rows = d.agents || [];
    if (!rows.length){ box.innerHTML = '<div class="empty">没有可列的 agent。</div>'; return; }
    box.innerHTML = rows.map(a => {
      const hid = String(a.harness_id || '').replace(/'/g, '');
      const on = a.monitor && a.monitor.on === true;
      const elg = a.eligible !== false;
      const kind = String(a.watch_kind || (elg ? 'window' : 'none'));
      const tag = '<span class="tag ' + kind + '">' +
        (kind === 'window' ? '画面' : kind === 'session' ? '会话' : '看不了') + '</span>';
      const swOpen = SESSION_OPEN.has(hid);
      /* 会话格：能读就给「看会话」，读不到就把 why 原样摆出来（不装） */
      const sessBtn = (kind === 'session')
        ? '<span class="sw' + (swOpen ? ' on' : '') + '" onclick="monToggleSession(\'' + hid + '\')"><i></i>' + (swOpen ? '看会话中' : '看会话') + '</span>'
        : '';
      const wc = a.window ? a.window.can_capture : null;
      let kindLine = '';
      if (kind === 'window'){
        if (wc === true) kindLine = '<span class="win-null">窗口：可抓取</span>';
        else if (wc === false) kindLine = '<span class="win-no">窗口：抓不到——' + esc((a.window && (a.window.why || a.window.reason)) || '原因未给出') + '</span>';
        else kindLine = '<span class="win-null">窗口：还没试过（开一次才知道）</span>';
      } else if (kind === 'session'){
        kindLine = '<div class="why">它没有可抓的窗口，改看它自己的会话（只读）。' +
          (a.session_watchable === false ? ('<br><span class="win-no">读不了：' + esc(a.session_why || '原因未给出') + '</span>') : '') + '</div>';
      } else {
        kindLine = '<div class="why"><span class="win-no">画面和会话都看不了</span><br>' +
          esc(a.session_why || (a.bridge && a.bridge.why) || a.why || '原因未给出') + '</div>';
      }
      const stopped = (a.monitor && a.monitor.state === 'stopped' && a.monitor.why)
        ? '<div class="stopped">已停抓：' + esc(a.monitor.why) + '</div>' : '';
      const watchSw = (kind === 'window')
        ? '<span class="sw' + (on ? ' on' : '') + '" onclick="monWatch(\'' + hid + '\',' + (!on) + ')"><i></i>' + (on ? '监控中' : '关') + '</span>'
        : sessBtn;
      return '<div class="acard' + (kind === 'none' ? '" style="opacity:.75' : '') + '">' +
        '<div class="t"><span class="name">' + esc(a.name || a.harness_name || a.harness_id || '') + '</span>' + tag +
        ((a.monitor && a.monitor.state && (on || a.monitor.state !== 'stopped'))
          ? '<span class="pt-status ' + (on?'running':'sent') + '">' + esc(a.monitor.state) + '</span>' : '') +
        watchSw + '</div>' +
        (kind === 'window' && !elg ? '<div class="why">不能开：' + esc((a.bridge && a.bridge.why) || a.why || '先去把桥测通') + '</div>' : '') +
        kindLine + stopped +
        '<div class="sess-btns"><button onclick="monTri(\'' + hid + '\', this)">状态（在干活 / 干完了 / 干得对）</button></div>' +
        '<div class="tri-host" id="tri-' + hid + '"></div>' +
        '</div>';
    }).join('');
  }catch(e){ box.innerHTML = '<div class="empty">加载失败：' + esc(e.message) + '</div>'; }
}

/* ── 会话观察窗：开关与读取（只读；读不到就说 why，不显示空会话）── */
function monToggleSession(hid){
  const k = String(hid || '');
  if (SESSION_OPEN.has(k)) SESSION_OPEN.delete(k); else SESSION_OPEN.add(k);
  if (!SESSION_OPEN.has(k) && sessTimers[k]){ clearInterval(sessTimers[k]); delete sessTimers[k]; }
  monLoadAgents();
  monLoadWatched();
}

async function monSessLoad(hid, refresh){
  const body = document.getElementById('sbody-' + hid);
  const meta = document.getElementById('smeta-' + hid);
  const note = document.getElementById('snote-' + hid);
  if (!body) return;
  let d = {};
  try{
    const q = '?limit=20' + (refresh ? '&refresh=1' : '');
    const res = await fetch('/api/monitor/' + encodeURIComponent(hid) + '/session' + q);
    d = await res.json().catch(()=>({}));
    if (!res.ok && !d.error && !d.why) d.why = 'HTTP ' + res.status;
  }catch(e){
    if (meta) meta.textContent = '读不到';
    body.innerHTML = '<div class="sess-why">读取失败：' + esc(e.message) + '</div>';
    return;
  }
  /* ① 读不了：把 why 原样摆出来，绝不显示空会话 */
  if (d.capable !== true){
    if (meta) meta.textContent = '读不了';
    body.innerHTML = '<div class="sess-why">' + esc(d.why || d.error || '它没有暴露「读会话」工具') + '</div>' +
      (d.how_to_read ? '<div class="sess-note">怎么补：' + esc(d.how_to_read) + '</div>' : '');
    if (note) note.textContent = '';
    /* 读不了就别放「现在读一次」——按了也不会变，等于给一个假按钮 */
    const tile = document.getElementById('stile-' + hid);
    if (tile){
      tile.querySelectorAll('.sess-btns button').forEach(b => {
        if (/读一次/.test(b.textContent || '')) b.remove();
      });
    }
    return;
  }
  const items = Array.isArray(d.items) ? d.items : [];
  if (meta){
    meta.textContent = (d.tool ? d.tool + ' · ' : '') + (d.read_at ? ('读于 ' + String(d.read_at).replace('T', ' ')) : '') +
      (d.cached ? (' · 缓存 ' + (d.age_ms != null ? Math.round(d.age_ms/1000) + ' 秒前' : '')) : ' · 刚读');
  }
  body.innerHTML = items.length
    ? items.map(it => {
        const role = String(it.role || '').toLowerCase();
        const who = role === 'user' ? '用户' : (role === 'assistant' ? '它' : (it.role || '—'));
        return '<div class="sm ' + (role === 'user' ? 'user' : 'assistant') + '">' +
          '<span class="who">' + esc(who) + (it.at ? (' · ' + esc(it.at)) : '') + '</span>' +
          '<span class="tx">' + esc(it.text || '') + '</span></div>';
      }).join('')
    : '<div class="sess-why">它这次会话里没有可显示的内容（是"这几条没有"，不等于它没在干活——干活看左侧「状态」）。</div>';
  if (note){
    note.innerHTML = esc(d.note || '这是它自己库里的内容，平台只读一次、没存副本') +
      '<div class="sess-caution">⚠ 会话里它说「对得上 / 已完成」不算平台核过 —— 干得对要看证据档与采信态（accepted_self / accepted_peer / disputed / refuted）。</div>';
  }
}

/* ── 三态分开：在干活（它自报，未复核）/ 干完了（平台调它的终态）/ 干得对（证据档+采信态）── */
async function monTri(hid, btn){
  const host = document.getElementById('tri-' + hid);
  if (!host) return;
  if (host.dataset.loaded === '1'){ host.innerHTML = ''; host.dataset.loaded = ''; return; }
  if (btn) btn.disabled = true;
  host.innerHTML = '<div class="tri-note">读取中…</div>';
  try{
    const d = await (await fetch('/api/harness/' + encodeURIComponent(hid) + '/activity?limit=5&read_session=0')).json();
    const c = d.counts || {};
    const rows = Array.isArray(d.work_trace) ? d.work_trace : [];
    const terminal = rows.filter(r => r.kind === 'outbound_call' && r.judge === 'verified').length;
    const outbound = c.outbound != null ? c.outbound : rows.filter(r => r.kind === 'outbound_call').length;
    const progress = c.progress != null ? c.progress : rows.filter(r => r.kind === 'progress').length;
    const caveats = Array.isArray(d.caveats) ? d.caveats : [];
    host.dataset.loaded = '1';
    host.innerHTML =
      '<div class="tri">' +
        '<span class="unv">在干活：它自报 ' + progress + ' 条（未复核）</span>' +
        '<span class="' + (terminal > 0 ? 'ver' : 'unv') + '">干完了：平台调它 ' + outbound + ' 条，终态 ' + terminal + ' 条</span>' +
        '<span>干得对：看证据档与采信态</span>' +
      '</div>' +
      (caveats.length ? '<div class="tri-note">⚠ ' + caveats.map(esc).join('；') + '</div>' : '') +
      '<div class="tri-note">三件事别混：它说自己干了什么＝未复核；平台调它有没有终态＝可核；干得对错要看证据档与采信态（V130 四值）。</div>';
  }catch(e){
    host.innerHTML = '<div class="tri-note">读取失败：' + esc(e.message) + '</div>';
  }
  if (btn) btn.disabled = false;
}

/* ── 就地开关 ── */
async function monWatch(hid, on){
  try{
    const res = await fetch('/api/monitor/watched', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({harness_id: hid, on: on})});
    const d = await res.json().catch(()=>({}));
    if (!res.ok || d.error){
      /* 人工中转/认不出窗口等 → 走「手动指定一次」：列出可抓窗口让用户选，带 hwnd 再发一次 */
      if (!on){ monRefresh(); return; }
      await monManualPick(hid, d);
      return;
    }
    monRefresh();
  }catch(e){ alert('操作失败：' + e.message); }
}

/* 手动指定一次：GET /api/monitor/sources 选一个窗口，带 hwnd 重发 */
async function monManualPick(hid, prev){
  try{
    const d = await (await fetch('/api/monitor/sources')).json();
    const srcs = d.sources || d.windows || [];
    if (!srcs.length){ alert('没找到可抓的窗口。' + (prev && (prev.error || prev.why) ? ('\n后端原话：' + (prev.error || prev.why)) : '')); monRefresh(); return; }
    const lines = srcs.map((s, i) => (i + 1) + '. ' + (s.title || s.name || s.hwnd));
    const pick = prompt('认不出该用哪个窗口——请选择（输入序号）：\n' + lines.join('\n'));
    if (pick === null) { monRefresh(); return; }
    const s = srcs[Number(pick) - 1];
    if (!s){ alert('序号无效。'); monRefresh(); return; }
    const res = await fetch('/api/monitor/watched', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({harness_id: hid, on: true, hwnd: (s.hwnd != null ? s.hwnd : s.id)})});
    const d2 = await res.json().catch(()=>({}));
    if (!res.ok || d2.error){ alert('手动指定后仍未成功：' + (d2.error || ('HTTP ' + res.status)) + (d2.how_to_fix ? ('\n' + d2.how_to_fix) : '')); }
    monRefresh();
  }catch(e){ alert('手动指定失败：' + e.message); monRefresh(); }
}

/* ── 右栏：画面墙（只放 monitor.on=true 的）── */
async function monLoadWatched(){
  const wall = $('wall');
  let rows = [];
  try{
    const d = await (await fetch('/api/monitor/watched')).json();
    rows = d.watched || d.agents || [];
  }catch(e){ wall.innerHTML = '<div class="empty">已开名单加载失败：' + esc(e.message) + '</div>'; return; }
  const byHid = {}; rows.forEach(r => { byHid[String(r.harness_id || '')] = r; });
  const on = rows.filter(r => r.monitor ? (r.monitor.on === true) : true);
  const sessIds = Array.from(SESSION_OPEN);
  $('mCount').textContent = String(on.length + sessIds.length);
  Object.keys(timers).forEach(k => { if (!on.some(r => r.harness_id === k)){ clearInterval(timers[k]); delete timers[k]; } });
  if (!on.length && !sessIds.length){ wall.innerHTML = '<div class="empty">还没开任何一路——在左侧把开关或「看会话」打开。</div>'; return; }
  wall.innerHTML = on.map(r => {
    const hid = String(r.harness_id || '').replace(/'/g, '');
    const stopped = (r.monitor && r.monitor.state === 'stopped' && r.monitor.why)
      ? '<div class="stopped">已停抓：' + esc(r.monitor.why) + '</div>' : '';
    return '<div class="tile kind-window" id="tile-' + hid + '" onclick="monZoom(\'' + hid + '\',\'' + esc(String(r.name || r.harness_id || '')).replace(/'/g,'') + '\')">' +
      '<img id="img-' + hid + '" src="/api/monitor/' + encodeURIComponent(hid) + '/frame?max_w=320&t=' + Date.now() + '" alt="画面">' +
      '<div class="tt"><b>' + esc(r.name || r.harness_id || '') + '</b><span class="fc-t2">1 帧/秒</span></div>' + stopped + '</div>';
  }).join('');
  on.forEach(r => {
    const hid = r.harness_id;
    if (timers[hid]) return;
    timers[hid] = setInterval(() => {
      const img = document.getElementById('img-' + hid);
      if (!img){ clearInterval(timers[hid]); delete timers[hid]; return; }
      img.src = '/api/monitor/' + encodeURIComponent(hid) + '/frame?max_w=320&t=' + Date.now();
    }, 1000);
  });
  /* ★会话观察窗：没有窗口的成员也在这面墙上，格子是文字流（D后端-71） */
  if (sessIds.length){
    wall.insertAdjacentHTML('beforeend', sessIds.map(hid => {
      const r = byHid[hid] || {};
      return '<div class="tile kind-session" id="stile-' + esc(hid) + '">' +
        '<div class="sess-head"><span class="tag session">会话</span><b>' + esc(r.name || r.harness_name || hid) + '</b>' +
        '<span class="sess-meta" id="smeta-' + esc(hid) + '">读中…</span></div>' +
        '<div class="sess-body" id="sbody-' + esc(hid) + '"></div>' +
        '<div class="sess-btns"><button onclick="monSessLoad(\'' + esc(hid).replace(/'/g,'') + '\', 1)">⟳ 现在读一次</button>' +
        '<button onclick="monToggleSession(\'' + esc(hid).replace(/'/g,'') + '\')">收起</button></div>' +
        '<div id="snote-' + esc(hid) + '" class="sess-note"></div></div>';
    }).join(''));
    sessIds.forEach(hid => {
      monSessLoad(hid, 0);
      if (!sessTimers[hid]){
        sessTimers[hid] = setInterval(() => monSessLoad(hid, 0), 6000);  /* 后端 5 秒缓存，别每拍打它 */
      }
    });
  }
  Object.keys(sessTimers).forEach(k => {
    if (!SESSION_OPEN.has(k)){ clearInterval(sessTimers[k]); delete sessTimers[k]; }
  });

  /* 图片加载失败（停抓/够不着）→ 显示原因而不是黑图 */
  on.forEach(r => {
    const img = document.getElementById('img-' + r.harness_id);
    if (!img) return;
    img.onerror = () => {
      const t = document.getElementById('tile-' + r.harness_id);
      if (t && !t.querySelector('.err')) t.insertAdjacentHTML('beforeend', '<div class="err">这一路的画面取不到（可能已停抓或窗口不可见）——左侧看 why。</div>');
    };
  });
}

/* ── 放大：stream_available 时用连续画面流（PNG 分帧），否则回落单帧 ── */
let MON_STREAM_OK = false;
function monZoom(hid, name){
  const img = $('zoomImg');
  const cap = $('zoomCap');
  $('zoomOv').style.display = 'flex';
  if (MON_STREAM_OK){
    img.onerror = () => {
      /* 断流：不显示黑框，给重连 */
      img.removeAttribute('src');
      img.onerror = null;
      cap.innerHTML = '画面流已停（<a href="javascript:void(0)" onclick="monZoom(\'' + hid.replace(/'/g,'') + '\',\'' + String(name||'').replace(/'/g,'') + '\')" style="color:#00FFFF">点一下重连</a>）';
    };
    img.src = '/api/monitor/' + encodeURIComponent(hid) + '/stream?max_w=0';
    cap.textContent = name + ' · 连续画面流（PNG 分帧）· 点击任意处关闭';
  } else {
    img.onerror = null;
    img.src = '/api/monitor/' + encodeURIComponent(hid) + '/frame?max_w=0&t=' + Date.now();
    cap.textContent = name + ' · 快照 · 点击任意处关闭';
  }
}
function monZoomClose(){
  const img = $('zoomImg');
  if (img) img.removeAttribute('src');   /* 关弹层必须摘 src，别让连接挂着 */
  $('zoomOv').style.display = 'none';
}

/* ── AI 观察总开关（默认关；开了后每路给一个「AI 观察」按钮，一按一次 POST observe，不做每帧触发）── */
function monInitAi(){
  const sw = $('aiSw');
  sw.onclick = () => {
    AI_ON = !AI_ON;
    sw.classList.toggle('on', AI_ON);
    sw.childNodes[sw.childNodes.length - 1].textContent = AI_ON ? '开' : '关';
    $('aiNote').textContent = AI_ON ? '已开：每路瓦片出现「AI 观察」按钮（后端限频 ≥30 分钟一次）' : '';
    renderAiButtons();
  };
}
function renderAiButtons(){
  document.querySelectorAll('.tile.kind-window').forEach(t => {
    const hid = (t.id || '').replace('tile-', '');
    let b = t.querySelector('.aiob');
    if (AI_ON && !b){
      b = document.createElement('button');
      b.className = 'aiob btn mini ghost';
      b.textContent = 'AI 观察';
      b.style.cssText = 'margin-top:5px;background:transparent;border:1px solid var(--rule-strong);color:var(--muted);font-family:inherit;font-size:10.5px;padding:2px 8px;cursor:pointer';
      b.onclick = ev => { ev.stopPropagation(); monObserve(hid, b); };
      t.appendChild(b);
    } else if (!AI_ON && b) b.remove();
  });
}
async function monObserve(hid, btn){
  const old = btn.textContent; btn.disabled = true; btn.textContent = '观察中…';
  try{
    const d = await (await fetch('/api/monitor/' + encodeURIComponent(hid) + '/observe', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'})).json();
    const ai = d.ai || {};
    let line = 'changed=' + d.changed + ' diff=' + (d.diff_ratio != null ? d.diff_ratio : '—');
    if (ai.ran === true) line += ' · AI：' + (d.note || d.why || '已分析');
    else line += ' · AI 没跑（默认关或限频）：' + (ai.why || '');
    btn.title = line;
    btn.textContent = 'AI 观察';
    const t = btn.closest('.tile');
    let n = t.querySelector('.ai-note');
    if (!n){ n = document.createElement('div'); n.className = 'ai-note stopped'; t.appendChild(n); }
    n.textContent = line;
  }catch(e){ btn.textContent = 'AI 观察'; btn.title = '观察失败：' + e.message; }
  btn.disabled = false;
}

monInitAi();
monRefresh();
