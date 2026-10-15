/* docking.js —— 对接中心（V34 P2：**只读总览**）
 *
 * 两个入口、同一份实现（V34 §1）：
 *   /docking.html                 总控（全局，所有工作间）
 *   /docking.html?ws=<工作间id>    单间（锁死这一个间，**不出工作间选择器**）
 *
 * 三条纪律：
 *   ① **本页只读**：P2 阶段零 POST（对接动作 P3/P4 才加，且必须落在 V34 §4.3 白名单内）；
 *   ② **失败要说话**：后端给的中文 error 一律原样显示，不许吞掉、不许显示成"空列表"；
 *   ③ **悬空引用要说**：成员引用了未注册的 harness 时（注销/改名/手改数据），
 *      必须显式列出来 —— 否则界面看着"接上了"，实际谁也找不到。
 */
const DOCK_WS = new URLSearchParams(location.search).get('ws') || '';
const DOCK_GLOBAL = !DOCK_WS;

function esc(s){ return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
  {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

const CH_STATE = {active:'已接入', pending:'未接入', retired:'已停用'};

async function dockLoad(){
  const q = DOCK_GLOBAL ? '' : ('?ws=' + encodeURIComponent(DOCK_WS));
  document.getElementById('dockScopeSub').textContent = DOCK_GLOBAL ? '· 总控（全局）' : '· 单间';
  document.getElementById('railScopeSub').textContent = DOCK_GLOBAL ? 'GLOBAL' : 'SINGLE WS';
  document.getElementById('dockApi').textContent = 'GET /api/docking/overview' + q;
  let d;
  try {
    const r = await fetch('/api/docking/overview' + q);
    d = await r.json();
    if (!r.ok) {                       // 后端的中文原因**原样显示**（V34 §7.3 失败要说话）
      dockError(d && d.error ? d.error : ('读取失败 HTTP ' + r.status));
      return;
    }
  } catch (e) {
    dockError('读取对接总览失败：' + (e && e.message ? e.message : e));
    return;
  }
  dockRender(d);
}

function dockError(msg){
  document.getElementById('dockScopeBanner').innerHTML = '⚠ ' + esc(msg);
  document.getElementById('dockBody').innerHTML =
    '<div class="card"><h3>读不到对接总览</h3><p class="lead">' + esc(msg) + '</p>' +
    '<p class="sub">这一页**不会**把失败显示成"没有内容" —— 请按上面的原因处理后再刷新。</p></div>';
}

function dockRender(d){
  const hs = d.harnesses || [];
  const dang = d.dangling_bindings || [];
  const wsRows = d.workshops || [];

  document.getElementById('dockScopeBanner').innerHTML =
    '◈ 范围：<b>' + (d.scope && d.scope.type === 'workshop'
      ? ('单间 · ' + esc(d.scope.ws)) : '总控（全局）') + '</b>' +
    '　·　已登记 harness <b>' + hs.length + '</b>' +
    '　·　工作间 <b>' + wsRows.length + '</b>' +
    (dang.length ? ('　·　⚠ <b>悬空引用 ' + dang.length + '</b>') : '') +
    '　·　' + esc(d.generated_at || '');

  // ── 左栏：工作间选择器**只在总控渲染**（单间模式整块不出现 —— V34 §1/§9.6）
  const pick = document.getElementById('wsPickerWrap');
  const rail = document.getElementById('railList');
  if (DOCK_GLOBAL){
    pick.innerHTML = '<div style="font-size:11px;color:var(--dim);margin-bottom:4px">按工作间收窄</div>' +
      '<div style="display:flex;flex-direction:column;gap:4px">' +
      wsRows.map(w => '<a class="rail-item" href="/docking.html?ws=' + encodeURIComponent(w.ws_id) + '">' +
        esc(w.name || w.ws_id) + '（接入 ' + (w.docked||0) + '/' + (w.member_count||0) + '）</a>').join('') +
      '</div>';
    rail.innerHTML = wsRows.map(w =>
      '<li>' + esc(w.name || w.ws_id) + '　<span class="rail-sub">' + esc(w.status||'') + '</span><br>' +
      '<span class="rail-sub">接入 ' + (w.docked||0) + ' · 待接 ' + (w.pending||0) +
      ' · 停用 ' + (w.retired||0) + ' · 掉线 ' + (w.offline||0) + '</span></li>').join('');
  } else {
    pick.innerHTML = '';               // 单间：**没有选择器**（不许越权操作别的间）
    const w = wsRows[0];
    rail.innerHTML = w ? ('<li>' + esc(w.name||w.ws_id) + '<br><span class="rail-sub">接入 ' + (w.docked||0) +
      ' · 待接 ' + (w.pending||0) + ' · 停用 ' + (w.retired||0) + ' · 掉线 ' + (w.offline||0) + '</span></li>')
      : '<li class="rail-sub">这个工作间不存在</li>';
  }

  // ── 主区
  const body = document.getElementById('dockBody');
  if (!hs.length && !dang.length){
    // 空白引导（V34 §10 第 5 条：装好就能用）
    body.innerHTML = '<div class="card"><h3>还没有登记任何外部 Agent</h3>' +
      '<p class="lead">三步就能接一个进来：</p>' +
      '<p class="sub">① 在你的 agent 那边准备一句话命令 / 一个目录 / 一个地址（<b>你自己有什么填什么</b>）；<br>' +
      '② 到 <a href="/register.html" target="_blank" rel="noopener">注册页</a> 登记（平台会自己探一次，补齐没填的字段）；<br>' +
      '③ 回到工作间「进行三级讨论」前的成员卡上，点<b>建会话 → 送桥</b>，把它对上岗位。</p>' +
      '<p class="sub">本页会用同一套数据告诉你：<b>谁接进了哪个工作间、对上哪个岗位</b>。</p></div>';
    return;
  }

  let html = '';
  if (dang.length){
    html += '<div class="card"><h3>⚠ 悬空引用（成员指向了未登记的 harness）</h3>' +
      '<p class="lead">这些 harness_id 被工作间成员引用了，但**已不在登记表里**（注销 / 改名 / 手改数据都可能造成）。' +
      '界面不能显示成"接上了"却谁也找不到 —— 请重新登记，或改掉成员的绑定。</p>' +
      dang.map(x => '<p class="sub"><code>' + esc(x.harness_id) + '</code> ← ' +
        (x.bindings||[]).map(b => esc((b.ws_name||b.ws_id) + '/' + (b.role||'') + '/' + (b.member_id||''))).join('、') +
        '</p>').join('') + '</div>';
  }

  html += hs.map(h => {
    const binds = h.bindings || [];
    const facts = h.interface_class_label ? ('· ' + esc(h.interface_class_label)) : '';
    return '<div class="card"><h3>' + esc(h.harness_name || h.harness_id) +
      ' <span class="sub">(' + esc(h.harness_id) + ')</span></h3>' +
      '<p class="lead">' + (h.online ? '在线' : '离线') + ' · 唤醒方式 <code>' + esc(h.wakeup_method || '—') + '</code> ' +
      facts + ' · ' +
      (h.can_be_woken_by_platform ? '平台能自动叫醒它' : '<b>平台叫不醒它</b>（只能人工/桥兜底）') + '</p>' +
      (binds.length
        ? ('<p class="sub" style="margin-top:6px">接在 ' + binds.length + ' 处（已接入 ' + (h.docked_count||0) + '）：</p>' +
           '<ul>' + binds.map(b => '<li>' + esc(b.ws_name || b.ws_id) + '　' + esc(b.role||'') +
             '　' + esc(CH_STATE[b.channel_state] || b.channel_state || '') +
             '　<span class="sub">会话名：' + esc(b.session_name || '（还没命名）') + '</span></li>').join('') + '</ul>')
        : '<p class="sub" style="margin-top:6px">还没有接进任何工作间。</p>') +
      '</div>';
  }).join('');

  body.innerHTML = html;
}

/* ── 写请求白名单（V34 §4.3）─────────────────────────────────────────────
 * 本页**只允许**调用这些写端点；`/discuss` 出现即 FAIL（对接中心不做讨论）。
 * 守卫 `test_docking_ui_static.py` 会把本文件里所有 POST 路径抽出来跟这张表对。
 */
const DOCK_WRITE_WHITELIST = [
  '/api/harness/pre-register',
  '/api/harness/probe-register',
  '/api/harness/register',
];

async function dockPost(path, body){
  if (DOCK_WRITE_WHITELIST.indexOf(path) === -1){
    // 白名单是**代码里的一道闸**：写错路径时当场说清，而不是偷偷发出去
    return {ok:false, data:{error:'这个页面不允许调用 ' + path + '（对接中心只做接入与激活）'}};
  }
  const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
                               body: JSON.stringify(body || {})});
  const d = await r.json().catch(()=>({error:'返回不是 JSON（HTTP ' + r.status + '）'}));
  return {ok: r.ok, data: d, status: r.status};
}

/** 把后端返回**如实**画出来：`error` 原样显示；其余字段逐条列出；原始输出放 details 里。 */
function dockRenderResult(box, res){
  const d = res.data || {};
  if (!res.ok || d.error){
    box.innerHTML = '<div class="dock-err">✗ ' +
      esc(d.error || ('HTTP ' + (res.status || '?'))) + '</div>' +
      '<p class="sub">这一页不会把失败显示成「没内容」—— 按上面的原因改完再点一次。</p>';
    return;
  }
  const skip = {probe_text:1};
  const rows = Object.keys(d).filter(k => !skip[k]).map(k => {
    let v = d[k];
    if (v && typeof v === 'object') v = JSON.stringify(v);
    return '<li><code>' + esc(k) + '</code>：' + esc(String(v)) + '</li>';
  }).join('');
  box.innerHTML = '<div class="dock-ok">✓ 已登记</div><ul class="dock-kv">' + rows + '</ul>' +
    (d.probe_text ? ('<details><summary>平台探测的原始输出</summary><pre>' + esc(d.probe_text) + '</pre></details>') : '') +
    '<p class="sub">接着去<b>工作间</b>的成员卡上点「建会话 → 送桥」，把它对上岗位（对接中心不管讨论与工作）。</p>';
}

function _dockRegBody(){
  const b = {
    harness_id: (document.getElementById('regId').value || '').trim(),
    process_hint: (document.getElementById('regCmd').value || '').trim(),
    path_hint: (document.getElementById('regDir').value || '').trim(),
    wakeup_method: (document.getElementById('regWake').value || '').trim(),
  };
  const port = (document.getElementById('regPort').value || '').trim();
  if (port) b.port_hint = Number(port) || port;
  Object.keys(b).forEach(k => { if (b[k] === '') delete b[k]; });
  return b;
}

function _dockRegGuard(){
  const b = _dockRegBody();
  const msg = document.getElementById('regMsg');
  if (!b.harness_id){ if (msg) msg.textContent = '至少要给它起个标识'; return null; }
  if (msg) msg.textContent = '';
  return b;
}

async function dockPreRegister(){
  const b = _dockRegGuard(); if (!b) return;
  const msg = document.getElementById('regMsg'); if (msg) msg.textContent = '提交中…';
  const res = await dockPost('/api/harness/pre-register', b);
  if (msg) msg.textContent = res.ok ? '草稿已存' : '失败';
  dockRenderResult(document.getElementById('regResult'), res);
  dockLoad();
}

async function dockProbeRegister(){
  const b = _dockRegGuard(); if (!b) return;
  const msg = document.getElementById('regMsg'); if (msg) msg.textContent = '平台正在探测…（可能几秒）';
  const res = await dockPost('/api/harness/probe-register', b);
  if (msg) msg.textContent = res.ok ? '已登记' : '失败，看下面的原因';
  dockRenderResult(document.getElementById('regResult'), res);
  dockLoad();
}

function dockMountRegister(){
  const wrap = document.getElementById('dockRegisterWrap');
  if (!wrap) return;
  // ★注册是**全局能力**：单间模式整块不挂（V34 §1「工作间的只能进入对应的」）
  if (!DOCK_GLOBAL){ wrap.innerHTML = ''; return; }
  if (wrap.dataset.mounted) return;
  const tpl = document.getElementById('tpl-dock-register');
  wrap.appendChild(tpl.content.cloneNode(true));
  wrap.dataset.mounted = '1';
}

dockMountRegister();
dockLoad();
