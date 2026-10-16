/* docking.js —— 对接中心 · 前台（FE-26：当场办事）
 *
 * 两个入口、同一份实现（V34 §1）：
 *   /docking.html                 总控（全局，所有工作间）
 *   /docking.html?ws=<工作间id>    单间（锁死这一个间，**不出工作间选择器**）
 *
 * 页面结构（对应硬约束）：
 *   ① 第一屏＝功能模块（8 块 data-dock-service，点进去当场办）
 *   ② 脚本激活工作台四屏：填关键词 → 让平台去找 → 确认试投 → 存进档案
 *   ③ 员工档案：列表 + 「详情」抽屉四块（框架性能/适配模型/桥模板/激活方式）
 *   ④ 激活操作台（单间范围）：建会话/送桥/抢救/通道/架桥辅助
 *
 * 三条纪律：
 *   ① **失败要说话**：后端的中文 error 一律原样显示，不许吞掉、不许显示成"空列表"；
 *   ② **三态如实**：verified 不是 true 就显示「⚠ 未核实/待人工」，绝不显示成办完了；
 *   ③ **写请求只有 dockPost 一条通道**：白名单闸在它里面，绕过它就等于没有闸。
 */
const DOCK_WS = new URLSearchParams(location.search).get('ws') || '';
const DOCK_GLOBAL = !DOCK_WS;

function esc(s){ return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
  {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

const CH_STATE = {active:'已接入', pending:'未接入', retired:'已停用'};

/** 注册入口的显隐：注册是全局能力，单间模式整块不出现（V34 §1）。 */
function dockRegTileVisibility(){
  const regTile = document.querySelector('[data-dock-service="register"]');
  if (regTile) regTile.style.display = DOCK_GLOBAL ? '' : 'none';
}

/* 最近一次总览数据（档案列表 / 脚本激活选员工从这里取，避免重复请求） */
let DOCK_LAST_OVERVIEW = null;

async function dockLoad(){
  const q = DOCK_GLOBAL ? '' : ('?ws=' + encodeURIComponent(DOCK_WS));
  document.getElementById('dockScopeSub').textContent = DOCK_GLOBAL ? '· 总控（全局）' : '· 单间';
  document.getElementById('railScopeSub').textContent = DOCK_GLOBAL ? 'GLOBAL' : 'SINGLE WS';
  document.getElementById('dockApi').textContent = 'GET /api/docking/overview' + q;
  dockRegTileVisibility();
  let d;
  try {
    const r = await fetch('/api/docking/overview' + q);
    d = await r.json();
    if (!r.ok) {                       // 后端的中文原因**原样显示**（失败要说话）
      dockError(d && d.error ? d.error : ('读取失败 HTTP ' + r.status));
      return;
    }
  } catch (e) {
    dockError('读取对接总览失败：' + (e && e.message ? e.message : e));
    return;
  }
  DOCK_LAST_OVERVIEW = d;
  dockRender(d);
}

function dockError(msg){
  document.getElementById('dockScopeBanner').innerHTML =
    '<span class="pulse"></span>⚠ ' + esc(msg);
  document.getElementById('dockBody').innerHTML =
    '<div class="card"><h3>读不到对接总览</h3><p class="lead">' + esc(msg) + '</p>' +
    '<p class="sub">这一页**不会**把失败显示成"没有内容" —— 按上面的原因处理后再刷新。</p>' +
    '<div class="dock-form-actions"><button class="btn mini" onclick="dockLoad()">重新读取</button></div></div>';
}

function dockRender(d){
  const hs = d.harnesses || [];
  const dang = d.dangling_bindings || [];
  const wsRows = d.workshops || [];

  document.getElementById('dockScopeBanner').innerHTML =
    '<span class="pulse"></span>◈ 范围：<b>' + (d.scope && d.scope.type === 'workshop'
      ? ('单间 · ' + esc(d.scope.ws)) : '总控（全局）') + '</b>' +
    '　·　已登记员工 <b>' + hs.length + '</b>' +
    '　·　工作间 <b>' + wsRows.length + '</b>' +
    (dang.length ? ('　·　⚠ <b>悬空引用 ' + dang.length + '</b>') : '') +
    '　·　' + esc(d.generated_at || '');

  // ── 左栏：工作间选择器**只在总控渲染**（单间模式整块不出现 —— V34 §1/§9.6）
  const pick = document.getElementById('wsPickerWrap');
  const rail = document.getElementById('railList');
  if (DOCK_GLOBAL){
    pick.innerHTML = '<div style="font-size:11px;color:var(--dk-dim);margin-bottom:4px">按工作间收窄</div>' +
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

  // ── 点名册（在功能模块下面，不是第一屏主角）
  const body = document.getElementById('dockBody');
  if (!hs.length && !dang.length){
    // 空状态给**动作**（点什么），不给一段解释
    body.innerHTML = '<div class="card"><h3>还没有登记任何外部 Agent</h3>' +
      '<p class="lead">从上面的「登记一位新员工」开始 —— 你手头有什么给什么，平台自己探一次补齐。</p>' +
      '<div class="dock-form-actions">' +
      '<button class="btn mini" onclick="dockGo(\'register\')">登记一位新员工</button>' +
      '<button class="btn mini" onclick="dockGo(\'ask_frontdesk\')">先问前台</button>' +
      '</div></div>';
    return;
  }

  let html = '';
  if (dang.length){
    html += '<div class="card"><h3>⚠ 悬空引用（成员指向了未登记的员工）</h3>' +
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
      '<div class="dock-form-actions">' +
      '<button class="dock-detail-btn" onclick="dockGo(\'archive\');dockOpenArchive(\'' + esc(h.harness_id) + '\')">详情</button>' +
      '<button class="dock-detail-btn" onclick="dockGo(\'script_activate\');dockScriptPick(\'' + esc(h.harness_id) + '\')">给它配激活脚本</button>' +
      '</div>' +
      '</div>';
  }).join('');

  body.innerHTML = html;
}

/* ── 写请求白名单（FE-26 §5：页面里非 GET 的请求只能落在这 17 条上）──────
 * `*` = 一个路径段（容纳 ws/member/harness id）。
 * 守卫核对：这张表必须**正好**是设计稿那 17 条 —— 多一条是偷偷扩权，少一条按钮点不动。
 */
const DOCK_WRITE_WHITELIST = [
  '/api/harness/pre-register',
  '/api/harness/probe-register',
  '/api/harness/register',
  '/api/workshop/*/onboard-members',
  '/api/workshop/*/recover-scan',
  '/api/workshop/*/member/*/establish-session',
  '/api/workshop/*/member/*/deliver-bridge',
  '/api/workshop/*/member/*/recover',
  '/api/workshop/*/member/*/channel/retire',
  '/api/workshop/*/member/*/channel/establish',
  // 架桥辅助（V34 §2）：按 harness 作用，参数走 body
  '/api/harness/bridge-path',
  '/api/harness/bridge-verify',
  '/api/harness/auto-connect',
  // 平台 Agent 直聊（协助架桥的一部分）
  '/api/assistant/chat',
  // 脚本激活工作台（FE-26 §5 + §12：定位/试投/档案读写/生成正文/一键激活）
  '/api/docking/locate',
  '/api/docking/try-activate',
  '/api/docking/compose-script',
  '/api/docking/run-script',
  '/api/docking/harness/*',
];

/** 路径模板（**字面量、唯一来源**）：界面上的每个动作都从这里取路径。 */
const DOCK_PATH = {
  preRegister:   '/api/harness/pre-register',
  probeRegister: '/api/harness/probe-register',
  register:      '/api/harness/register',
  onboard:       '/api/workshop/{ws}/onboard-members',
  rescan:        '/api/workshop/{ws}/recover-scan',
  establish:     '/api/workshop/{ws}/member/{mid}/establish-session',
  deliver:       '/api/workshop/{ws}/member/{mid}/deliver-bridge',
  recover:       '/api/workshop/{ws}/member/{mid}/recover',
  retire:        '/api/workshop/{ws}/member/{mid}/channel/retire',
  newChannel:    '/api/workshop/{ws}/member/{mid}/channel/establish',
  // 架桥辅助：按 **harness** 作用 —— 路径不带 {ws}/{mid}，参数走 body
  bridgePath:    '/api/harness/bridge-path',
  bridgeVerify:  '/api/harness/bridge-verify',
  autoConnect:   '/api/harness/auto-connect',
  chat:          '/api/assistant/chat',
  locate:        '/api/docking/locate',
  tryActivate:   '/api/docking/try-activate',
  composeScript: '/api/docking/compose-script',
  runScript:     '/api/docking/run-script',
  archiveGet:    '/api/docking/harness/{hid}',
  archiveSave:   '/api/docking/harness/{hid}',
  exportPs1:     '/api/docking/harness/{hid}/export.ps1',
  suggestions:   '/api/docking/keyword-suggestions',
};

function dockAllowed(path){
  const a = String(path).split('/');
  return DOCK_WRITE_WHITELIST.some(p => {
    const s = p.split('/');
    return s.length === a.length && s.every((seg, i) => seg === '*' || seg === a[i]);
  });
}

function dockPath(tpl, ws, mid){
  return String(tpl)
    .replace('{ws}', encodeURIComponent(ws || ''))
    .replace('{mid}', encodeURIComponent(mid || ''));
}

async function dockPost(path, body){
  if (!dockAllowed(path)){
    // 白名单是**代码里的一道闸**：写错路径时当场说清，而不是偷偷发出去
    return {ok:false, data:{error:'这个页面不允许调用 ' + path + '（对接中心只做接入与激活）'}};
  }
  const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
                               body: JSON.stringify(body || {})});
  const d = await r.json().catch(()=>({error:'返回不是 JSON（HTTP ' + r.status + '）'}));
  return {ok: r.ok, data: d, status: r.status};
}

/** 把后端返回**如实**画出来：error 原样显示；其余字段逐条列出；原始输出放 details 里。 */
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

/* ══ 服务菜单导航（第一屏 → 当场办）══════════════════════════════════ */
function _dockScrollTo(id){
  const el = document.getElementById(id);
  if (el && el.scrollIntoView) el.scrollIntoView({behavior:'smooth', block:'start'});
}

function dockGo(svc){
  if (svc === 'register'){ dockMountRegister(); _dockScrollTo('dockRegisterWrap'); return; }
  if (svc === 'script_activate'){ dockMountScript(); _dockScrollTo('dockScriptWrap'); return; }
  if (svc === 'archive'){ dockLoadArchive(); _dockScrollTo('dockArchiveWrap'); return; }
  if (svc === 'ask_frontdesk'){ _dockScrollTo('dockAskWrap'); return; }
  // session_bridge / onboard_recover / channel / bridge_helper：这些在一个工作间里办
  dockMountWsGuide();
  _dockScrollTo('dockActivateWrap');
}

function dockTileKey(e, svc){
  if (e.key === 'Enter' || e.key === ' '){ e.preventDefault(); dockGo(svc); }
}

/* ══ 注册（全局能力，只在总控）══════════════════════════════════════ */
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
  const res = await dockPost(DOCK_PATH.preRegister, b);
  if (msg) msg.textContent = res.ok ? '草稿已存' : '失败';
  dockRenderResult(document.getElementById('regResult'), res);
  dockLoad();
}

async function dockProbeRegister(){
  const b = _dockRegGuard(); if (!b) return;
  const msg = document.getElementById('regMsg'); if (msg) msg.textContent = '平台正在探测…（可能几秒）';
  const res = await dockPost(DOCK_PATH.probeRegister, b);
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

/* ══ 总控下的工作间引导（建会话/送桥/抢救/通道/架桥在一个间里办）════════ */
function dockMountWsGuide(){
  const wrap = document.getElementById('dockActivateWrap');
  if (!wrap) return;
  if (!DOCK_GLOBAL) return;            // 单间走 dockLoadActivation，不走这个引导
  wrap.innerHTML =
    '<div class="card"><h3>这四件事在一个工作间里办</h3>' +
    '<p class="lead">建会话 / 送桥 / 批量对接 / 通道 / 架桥辅助都是<b>对某一个工作间</b>的操作 —— ' +
    '在左边<b>选一个工作间进入</b>（或直接点下面哪一间），到那间里当场办。</p>' +
    '<div style="display:flex;flex-direction:column;gap:4px;margin-top:8px">' +
    ((DOCK_LAST_OVERVIEW && DOCK_LAST_OVERVIEW.workshops) || []).map(w =>
      '<a class="rail-item" href="/docking.html?ws=' + encodeURIComponent(w.ws_id) + '">' +
      esc(w.name || w.ws_id) + '（接入 ' + (w.docked||0) + '/' + (w.member_count||0) + '）</a>').join('') +
    '</div></div>';
}

/* ══ 激活操作台（单间范围）══════════════════════════════════════════
 * 三条纪律（V34 §7）：
 *   ① **逐项如实回报**：批量操作分栏（自动/人工），不合并、不美化；
 *   ② verified=false **一律显示「⚠ 待人工/未核实」，绝不显示"完成"**；
 *   ③ 每个写动作**逐个点击**，不做"一键点完所有成员的所有步骤"这种会掩盖失败的按钮。
 */
function dockStepBadge(st){
  const auto = !!(st && st.automatic), ver = !!(st && st.verified);
  if (auto && ver) return '<span class="dock-ok2">✓ 已自证</span>';
  if (auto && !ver) return '<span class="dock-warn2">⚠ 未核实</span>';
  return '<span class="dock-warn2">⚠ 人工模式</span>';
}

function dockRenderSteps(steps){
  if (!steps) return '';
  return '<div class="dock-steps">' + Object.keys(steps).map(k =>
    '<span class="dock-step"><code>' + esc(k) + '</code>' + dockStepBadge(steps[k]) + '</span>').join('') + '</div>';
}

async function dockLoadActivation(){
  const wrap = document.getElementById('dockActivateWrap');
  if (!wrap) return;
  if (DOCK_GLOBAL){ wrap.innerHTML = ''; return; }   // 激活是"这个间"的事，总控不挂
  const ws = DOCK_WS;
  let d;
  try {
    const r = await fetch('/api/workshop/' + encodeURIComponent(ws) + '/members-status');
    d = await r.json();
    if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
  } catch (e){
    wrap.innerHTML = '<div class="card"><h3>这个工作间的成员</h3><div class="dock-err">✗ ' +
      esc(e.message || e) + '</div><p class="sub">读不到成员状态 —— 不显示成"没有成员"。</p>' +
      '<div class="dock-form-actions"><button class="btn mini" onclick="dockLoadActivation()">重新读取</button></div></div>';
    return;
  }
  const ms = d.members || [];
  const sum = d.summary || {};
  const head = '<div class="card"><h3>把岗位和 agent 对上 <span class="sub">（本工作间）</span></h3>' +
    '<p class="lead">成员 ' + ms.length + ' 名' +
    (sum.manual_members && sum.manual_members.length
      ? '　·　⚠ <b>' + sum.manual_members.length + ' 名有"人工模式"的步骤</b>（那些步骤**没被核实**，不算办完）'
      : '　·　全部步骤都由脚本自证') + '</p>' +
    '<div class="dock-form-actions">' +
    '<button class="btn mini" onclick="dockWsAct(\'onboard\')">全员对接（建会话+命名）</button>' +
    '<button class="btn mini" onclick="dockWsAct(\'rescan\')">掉线扫描</button>' +
    '<span class="dock-msg" id="actMsg"></span></div></div>';

  const cards = ms.map(m => {
    const ch = m.channel || {};
    const st = {active:'已接入', pending:'未接入', retired:'已停用'}[ch.state] || ch.state || '';
    return '<div class="card">' +
      '<h3>' + esc(m.role || '') + '　' + esc(m.display_name || m.member_id) +
      ' <span class="sub">(' + esc(m.member_id) + ')</span></h3>' +
      '<p class="lead">通道 <b>' + esc(st) + '</b> · 第 ' + esc(ch.seq || 1) + ' 条' +
      '　·　会话名：<code>' + esc(m.session_name || '（还没命名）') + '</code>' +
      '　·　状态 ' + esc(m.status || '') +
      '　·　桥文件 ' + ((m.bridge_files || []).length) + ' 个</p>' +
      (m.orphan ? ('<div class="dock-warn">⚠ ' + esc(m.orphan_text || '这个成员的 agent 已不在平台上') +
                   '　' + esc(m.orphan_advice || '') + '</div>') : '') +
      dockRenderSteps(m.steps) +
      '<div class="dock-form-actions">' +
      '<button class="btn mini" onclick="dockMemberAct(\'establish\',\'' + esc(m.member_id) + '\')">建会话</button>' +
      '<button class="btn mini" onclick="dockMemberAct(\'deliver\',\'' + esc(m.member_id) + '\')">送桥</button>' +
      '<button class="btn mini" onclick="dockMemberAct(\'recover\',\'' + esc(m.member_id) + '\')">抢救</button>' +
      '<button class="btn mini" onclick="dockMemberAct(\'retire\',\'' + esc(m.member_id) + '\')">停用通道</button>' +
      '<button class="btn mini" onclick="dockMemberAct(\'newChannel\',\'' + esc(m.member_id) + '\')">新建通道</button>' +
      '</div>' +
      // 架桥辅助（V34 §2）：按 **harness** 作用 —— 成员没绑 harness 就不给按钮（别给空动作）
      (m.harness_id
        ? ('<div class="dock-bridge"><span class="sub">架桥辅助（作用于 ' + esc(m.harness_id) + '）：</span>' +
           '<button class="btn mini" onclick="dockBridgeAct(\'bridgePath\',\'' + esc(m.harness_id) + '\',\'' + esc(m.member_id) + '\')">桥路径</button>' +
           '<button class="btn mini" onclick="dockBridgeAct(\'bridgeVerify\',\'' + esc(m.harness_id) + '\',\'' + esc(m.member_id) + '\')">验证桥</button>' +
           '<button class="btn mini" onclick="dockBridgeAct(\'autoConnect\',\'' + esc(m.harness_id) + '\',\'' + esc(m.member_id) + '\')">自动连接</button>' +
           '</div>')
        : '<div class="dock-bridge"><span class="sub">这个成员还没绑 harness（先在工作间里给他绑一个）。</span></div>') +
      '<div id="actResult-' + esc(m.member_id) + '"></div></div>';
  }).join('');

  wrap.innerHTML = head + (cards || '<div class="card"><p class="lead">这个工作间还没有成员。</p></div>');
}

async function dockMemberAct(kind, mid){
  const path = dockPath(DOCK_PATH[kind], DOCK_WS, mid);
  const box = document.getElementById('actResult-' + mid);
  if (box) box.innerHTML = '<p class="sub">执行中…（要起子进程/等回报，可能几秒）</p>';
  const res = await dockPost(path, {});
  if (!box) return;
  // ★verified=false 一律说"待人工/未核实"，**绝不**显示"完成"（V34 §7.3）
  const d = res.data || {};
  let head = '';
  if (res.ok){
    if (d.all_verified === true) head = '<div class="dock-ok">✓ 全部动作由脚本完成并自证</div>';
    else if (d.all_verified === false || (d.manual && d.manual.length))
      head = '<div class="dock-warn">⚠ 部分步骤为**人工模式**（未核实）—— 按下面的指引做，平台不会把它当成已完成</div>';
    else head = '<div class="dock-ok2">已执行（返回里没有 all_verified，请看下面的原始结果）</div>';
  }
  box.innerHTML = head;
  dockRenderResult(box, Object.assign({}, res, {_keepHead:true}));
}

async function dockBridgeAct(kind, hid, mid){
  const path = DOCK_PATH[kind];
  const box = document.getElementById('actResult-' + mid);
  if (box) box.innerHTML = '<p class="sub">执行中…（要起子进程/连一次，可能几秒）</p>';
  // 架桥辅助按 **harness** 作用：参数走 body（V34 §2 口径），路径不带 ws/mid
  const res = await dockPost(path, {harness_id: hid});
  if (box) dockRenderResult(box, res);
}

async function dockWsAct(kind){
  const msg = document.getElementById('actMsg');
  if (msg) msg.textContent = '执行中…';
  const res = await dockPost(dockPath(DOCK_PATH[kind], DOCK_WS, ''), {});
  if (msg) msg.textContent = res.ok ? '已执行' : '失败，看下方原因';
  const box = document.getElementById('actResult-_ws');
  const target = box || (function(){
    const d = document.createElement('div'); d.id = 'actResult-_ws';
    document.getElementById('dockActivateWrap').appendChild(d); return d;
  })();
  dockRenderResult(target, res);
  dockLoadActivation();          // 状态刷新（逐项结果仍在上面那条里）
}

/* ══ 脚本激活工作台（本版核心，四屏）══════════════════════════════════
 * ① 用户填关键词 → ② 让平台去找 → ③ 确认 + 试投 → ④ 存进档案。
 * 边界（FE-26 §3.3）：候选/定位/生成正文 —— 平台自动做；
 * **试投**与**保存**必须用户点（一次一按、可审计）。平台不替你动桌面。
 */
const DOCK_SCRIPT = {hid:'', kws:[], picked:'', lastTry:null, lastKwText:''};

function dockMountScript(){
  const wrap = document.getElementById('dockScriptWrap');
  if (!wrap || wrap.dataset.mounted) return;
  wrap.innerHTML =
    '<div class="card"><h3>脚本激活 <span class="sub">（填关键词 → 让平台去找 → 确认试投 → 存进档案）</span></h3>' +
    '<p class="lead">我会拿关键词去你桌面上找那个窗口，把话送进去。</p>' +
    '<div class="dock-screen"><h4><span class="no">屏1</span>填关键词（多个用 | 分隔）</h4>' +
    '<div class="dock-form">' +
    '<select id="dkHid" onchange="dockScriptPick()"><option value="">先选是哪位员工…</option></select>' +
    '<input id="dkKw" placeholder="会出现在那个窗口标题里的字，如：合作社|TraeCode">' +
    '</div>' +
    '<div id="dkSug" class="dock-sug"><p class="sub">候选关键词（由平台从档案与线索里推，每条带出处）—— 选员工后给。</p></div>' +
    '</div>' +
    '<div class="dock-screen"><h4><span class="no">屏2</span>让平台去找</h4>' +
    '<div class="dock-form-actions">' +
    '<button class="btn mini" onclick="dockLocate()">让平台去找</button>' +
    '<span class="dock-msg" id="dkLocateMsg"></span></div>' +
    '<div id="dkLocateOut"><p class="sub">填好关键词再点。</p></div>' +
    '</div>' +
    '<div class="dock-screen"><h4><span class="no">屏3</span>确认 + 试投一次</h4>' +
    '<div class="dock-form-actions">' +
    '<button class="btn primary" id="dkTryBtn" onclick="dockTryAct()">就是这个窗口，试一次</button>' +
    '<span class="dock-msg" id="dkTryMsg"></span></div>' +
    '<div id="dkTryOut"><p class="sub">先在上面选中一个候选窗口。</p></div>' +
    '</div>' +
    '<div class="dock-screen"><h4><span class="no">屏4</span>存进档案（脚本正文可看、可复制、可手改）</h4>' +
    '<textarea id="dkScriptText" rows="9" placeholder="试投之后平台会生成正文；也可直接点「生成脚本正文」或手写。"></textarea>' +
    '<div class="dock-form-actions">' +
    '<button class="btn mini" onclick="dockComposeScript()">生成脚本正文</button>' +
    '<button class="btn primary" onclick="dockSaveScript()">保存为该员工的激活方式</button>' +
    '<button class="btn mini" onclick="dockRunScript()">一键脚本激活</button>' +
    '<a class="dock-detail-btn" id="dkExport" href="#" onclick="return dockExportPs1()">导出 .ps1 副本</a>' +
    '<span class="dock-msg" id="dkSaveMsg"></span></div>' +
    '<div id="dkSaveOut"></div>' +
    '</div>' +
    '</div>';
  wrap.dataset.mounted = '1';
  dockScriptFillEmployees();
}

function dockScriptFillEmployees(){
  const sel = document.getElementById('dkHid');
  if (!sel) return;
  const hs = (DOCK_LAST_OVERVIEW && DOCK_LAST_OVERVIEW.harnesses) || [];
  const cur = sel.value || DOCK_SCRIPT.hid || '';
  sel.innerHTML = '<option value="">先选是哪位员工…</option>' +
    hs.map(h => '<option value="' + esc(h.harness_id) + '">' +
      esc(h.harness_name || h.harness_id) + '（' + esc(h.harness_id) + '）</option>').join('');
  if (cur) sel.value = cur;
}

/** 选定员工 → 拉候选关键词（平台给，每条带出处；自己编＝抄平台知识，不做）。 */
async function dockScriptPick(hidArg){
  const sel = document.getElementById('dkHid');
  const hid = hidArg || (sel && sel.value) || '';
  DOCK_SCRIPT.hid = hid;
  const out = document.getElementById('dkSug');
  if (!out) return;
  if (!hid){ out.innerHTML = '<p class="sub">先选是哪位员工 —— 候选是从它的档案和线索里推的。</p>'; return; }
  out.innerHTML = '<p class="sub">平台正在翻档案与本机线索…</p>';
  let d;
  try {
    const r = await fetch('/api/docking/keyword-suggestions?harness_id=' + encodeURIComponent(hid) + '&probe=1');
    d = await r.json();
    if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
  } catch (e){
    out.innerHTML = '<div class="dock-err">✗ ' + esc(e.message || e) + '</div>' +
      '<p class="sub">候选拿不到 —— 直接在上面手填一个**会出现在那个窗口标题里**的字。</p>';
    return;
  }
  const sugs = d.suggestions || [];
  const notes = d.notes || [];
  let html = sugs.length
    ? sugs.map(s => '<button type="button" onclick="dockSugFill(this)">' + esc(s.text) +
        '　<span class="sug-src">← ' + esc(s.source) + '：' + esc(s.why || '') + '</span></button>').join('')
    : '<p class="sub">档案和线索里都推不出候选 —— 直接手填一个**会出现在那个窗口标题里**的字。</p>';
  if (!d.supported) html += '<p class="sub">⚠ ' + esc(d.probe_note || '这台机器上平台看不到窗口列表') + '</p>';
  notes.forEach(n => { html += '<p class="sub">' + esc(n) + '</p>'; });
  out.innerHTML = html;
}

function dockSugFill(btn){
  const kw = document.getElementById('dkKw');
  if (!kw || !btn) return;
  const t = (btn.textContent || '').split('←')[0].trim();
  if (!t) return;
  kw.value = kw.value ? (kw.value.replace(/\|$/, '') + '|' + t) : t;
  kw.focus();
}

/** 屏2：让平台去找（只读定位：列窗口、按关键词打分，不注入、不改状态）。 */
async function dockLocate(){
  const kw = document.getElementById('dkKw');
  const out = document.getElementById('dkLocateOut');
  const msg = document.getElementById('dkLocateMsg');
  const kws = (kw && kw.value || '').split('|').map(s => s.trim()).filter(Boolean);
  DOCK_SCRIPT.kws = kws;
  if (!out) return;
  if (!kws.length){ if (msg) msg.textContent = '先填至少一个关键词'; return; }
  if (msg) msg.textContent = '平台在列窗口并按关键词打分…';
  out.innerHTML = '<p class="sub">找窗口中…</p>';
  const res = await dockPost(DOCK_PATH.locate, {keywords: kws, limit: 20});
  const d = res.data || {};
  if (!res.ok || d.error){
    out.innerHTML = '<div class="dock-err">✗ ' + esc(d.error || ('HTTP ' + (res.status || '?'))) + '</div>' +
      '<p class="sub">按上面的原因改完再点一次。</p>';
    if (msg) msg.textContent = '';
    return;
  }
  if (msg) msg.textContent = '';
  const cands = d.candidates || [];
  if (!cands.length){
    // 找不到要说清试了什么 + 给「放宽关键词」的下一步（不留"没反应"）
    out.innerHTML = '<div class="dock-warn">⚠ 一个都没找到。平台扫了 <b>' + esc(d.total_windows || 0) +
      '</b> 个窗口，按关键词逐个比对都没命中。</div>' +
      (d.probe_note ? ('<p class="sub">' + esc(d.probe_note) + '</p>') : '') +
      '<p class="lead">下一步：<b>把关键词放宽</b> —— 只留窗口标题里最稳定的那一两个字' +
      '（比如平台名、产品名），不要整句；改完再点一次「让平台去找」。</p>' +
      '<div class="dock-form-actions"><button class="btn mini" onclick="dockLocate()">放宽后再找一次</button></div>';
    return;
  }
  DOCK_SCRIPT.picked = '';
  out.innerHTML =
    '<p class="lead">扫到 <b>' + esc(d.total_windows || 0) + '</b> 个窗口，命中 <b>' + cands.length + '</b> 个' +
    '（匹配分＝命中的关键词数 ÷ 关键词总数，分高在前）：</p>' +
    cands.map((c, i) => '<div class="dock-cand" role="button" tabindex="0" data-title="' + esc(c.title || '') +
      '" onclick="dockPickCand(this)" onkeydown="if(event.key===\'Enter\'){event.preventDefault();dockPickCand(this);}">' +
      '<span class="cand-pick">点选</span><b>' + esc(c.title || '（无标题）') + '</b><br>' +
      '<span class="sub">窗口类 <code>' + esc(c.class || '—') + '</code> · pid <code>' + esc(c.pid || '—') +
      '</code> · 匹配分 <code>' + esc(String(c.score != null ? c.score : '—')) + '</code>' +
      ((c.hits && c.hits.length) ? (' · 命中 <code>' + esc(c.hits.join('、')) + '</code>') : '') +
      '</span></div>').join('');
}

function dockPickCand(el){
  document.querySelectorAll('.dock-cand.sel').forEach(x => x.classList.remove('sel'));
  el.classList.add('sel');
  DOCK_SCRIPT.picked = el.getAttribute('data-title') || '';
  const out = document.getElementById('dkTryOut');
  if (out) out.innerHTML = '<p class="sub">已选：<b>' + esc(DOCK_SCRIPT.picked) +
    '</b> —— 点「就是这个窗口，试一次」会把一句探针文本真的送进那个窗口（试投 ≠ 送达确认）。</p>';
}

/** 屏3：确认 + 试投（必须用户点；三态如实：送出去 ≠ 送到了）。 */
async function dockTryAct(){
  const out = document.getElementById('dkTryOut');
  const msg = document.getElementById('dkTryMsg');
  if (!out) return;
  if (!DOCK_SCRIPT.picked){ if (msg) msg.textContent = '先在上面选中一个候选窗口'; return; }
  if (msg) msg.textContent = '试投中…（往那个窗口送一句探针文本并读回）';
  out.innerHTML = '<p class="sub">试投中…</p>';
  const res = await dockPost(DOCK_PATH.tryActivate,
    {title: DOCK_SCRIPT.picked, keywords: DOCK_SCRIPT.kws});
  const d = res.data || {};
  if (msg) msg.textContent = '';
  if (!res.ok || d.error){
    out.innerHTML = '<div class="dock-err">✗ ' + esc(d.error || ('HTTP ' + (res.status || '?'))) + '</div>' +
      (d.next_step ? ('<p class="lead">下一步：' + esc(d.next_step) + '</p>') : '') +
      '<p class="sub">按上面的原因改完再点一次；也可以回屏1放宽关键词。</p>';
    return;
  }
  DOCK_SCRIPT.lastTry = d;
  // ★三态如实：verified === true 才是「已自证」；其余一律「⚠ 未核实」（送出去 ≠ 送到了）
  if (d.verified === true){
    out.innerHTML = '<div class="dock-ok">✓ 已自证：探针文本送进了那个窗口，且读回一致。</div>' +
      '<p class="lead">这条路能通 —— 到屏4把脚本存进它的档案。</p>';
  } else {
    out.innerHTML = '<div class="dock-warn">⚠ 未核实' + (d.verified === false ? '（投递没成功）' : '（送出去了，但没法确认读回）') +
      '</div>' +
      (d.detail ? ('<p class="sub">' + esc(d.detail) + '</p>') : '') +
      (d.next_step ? ('<p class="lead">下一步：' + esc(d.next_step) + '</p>') : '') +
      '<p class="sub">也可以换一个候选窗口再试一次；都不通就走剪贴板兜底档（试投时选 clipboard）。</p>';
  }
  dockComposeScript();
}

/** 屏4：让平台生成脚本正文（确定性、可复现、不落盘；**平台给，界面不自己编**）。 */
async function dockComposeScript(){
  const ta = document.getElementById('dkScriptText');
  const msg = document.getElementById('dkSaveMsg');
  if (!ta) return;
  const hid = DOCK_SCRIPT.hid;
  const kws = DOCK_SCRIPT.kws || [];
  const prev = DOCK_SCRIPT.lastTry || {};
  if (!kws.length && !DOCK_SCRIPT.picked){
    if (msg) msg.textContent = '先在屏2/屏3拿到定位结果，平台才能生成正文';
    return;
  }
  if (msg) msg.textContent = '平台在生成脚本正文…';
  const res = await dockPost(DOCK_PATH.composeScript, {
    keywords: kws, title: DOCK_SCRIPT.picked || '',
    text: prev.text || '', method: prev.method || '', harness_id: hid,
  });
  const d = res.data || {};
  if (msg) msg.textContent = '';
  if (!res.ok || d.error){
    ta.value = '';
    const out = document.getElementById('dkSaveOut');
    if (out) out.innerHTML = '<div class="dock-err">✗ ' + esc(d.error || ('HTTP ' + (res.status || '?'))) + '</div>' +
      '<p class="sub">正文生成不了 —— 按上面的原因回屏2/屏3补定位结果。</p>';
    return;
  }
  const script = d.script || {};
  DOCK_SCRIPT.lastScript = script;         // 平台生成的原件（保存时原样进档案）
  ta.value = String(script.text || '');
  if (msg) msg.textContent = '正文已生成（可手改）';
}

/** 保存为该员工的激活方式（必须用户点；走档案写端点，不重建会话/桥）。 */
async function dockSaveScript(){
  const ta = document.getElementById('dkScriptText');
  const msg = document.getElementById('dkSaveMsg');
  const out = document.getElementById('dkSaveOut');
  if (!ta || !out) return;
  const hid = DOCK_SCRIPT.hid;
  if (!hid){ if (msg) msg.textContent = '先在屏1选是哪位员工'; return; }
  const text = (ta.value || '').trim();
  if (!text){ if (msg) msg.textContent = '脚本是空的 —— 先试投一次让平台生成正文，或直接手写'; return; }
  if (msg) msg.textContent = '保存中…';
  const prev = DOCK_SCRIPT.lastScript || DOCK_SCRIPT.lastTry || {};
  const res = await dockPost(dockPath(DOCK_PATH.archiveSave, '', hid), {
    activation_script: {
      text: text,
      keywords: prev.keywords || DOCK_SCRIPT.kws || [],
      title: prev.title || DOCK_SCRIPT.picked || '',
      message: prev.message || prev.text || '',
      method: prev.method || '',
      verified: prev.verified === true ? true : (prev.verified === false ? false : null),
    },
  });
  const d = res.data || {};
  if (msg) msg.textContent = '';
  if (!res.ok || d.error){
    out.innerHTML = '<div class="dock-err">✗ ' + esc(d.error || ('HTTP ' + (res.status || '?'))) + '</div>' +
      '<p class="sub">按上面的原因改完再点一次。</p>';
    return;
  }
  out.innerHTML = '<div class="dock-ok">✓ 已保存：激活方式写进了「' + esc(hid) + '」的档案' +
    '（试投状态如实记档：' + (d.archive && d.archive.activation_script && d.archive.activation_script.verified === true
      ? '已自证' : '⚠ 未核实') + '）。</div>' +
    '<p class="sub">之后可以点「一键脚本激活」用档案里的参数直接跑；导出 .ps1 副本见上面的按钮（真相始终在档案里）。</p>';
  dockRefreshFacts(d);
}

/** 一键脚本激活（必须用户点；用档案里**已保存**的参数跑一次，结果三态如实回报）。 */
async function dockRunScript(){
  const out = document.getElementById('dkSaveOut');
  const msg = document.getElementById('dkSaveMsg');
  if (!out) return;
  const hid = DOCK_SCRIPT.hid;
  if (!hid){ if (msg) msg.textContent = '先在屏1选是哪位员工'; return; }
  if (msg) msg.textContent = '平台在按档案里的参数跑…';
  out.innerHTML = '<p class="sub">一键激活中…</p>';
  const res = await dockPost(DOCK_PATH.runScript, {harness_id: hid});
  const d = res.data || {};
  if (msg) msg.textContent = '';
  if (!res.ok || d.error){
    out.innerHTML = '<div class="dock-err">✗ ' + esc(d.error || ('HTTP ' + (res.status || '?'))) + '</div>' +
      (d.next_step ? ('<p class="lead">下一步：' + esc(d.next_step) + '</p>') : '');
    return;
  }
  const used = d.used || {};
  if (d.verified === true){
    out.innerHTML = '<div class="dock-ok">✓ 已自证：脚本按档案里的参数跑通，读回一致' +
      (used.method ? ('（' + esc(used.method) + '）') : '') + '。</div>';
  } else {
    out.innerHTML = '<div class="dock-warn">⚠ 未核实' + (d.verified === false ? '（投递没成功）' : '（送出去了，但没法确认读回）') +
      '</div>' +
      (d.detail ? ('<p class="sub">' + esc(d.detail) + '</p>') : '') +
      (d.next_step ? ('<p class="lead">下一步：' + esc(d.next_step) + '</p>') : '');
  }
}

/** 导出 .ps1 副本（只读 GET；没存过脚本时后端 404 + 中文原因，原样显示）。 */
async function dockExportPs1(){
  const hid = DOCK_SCRIPT.hid;
  if (!hid) return false;
  const r = await fetch(dockPath(DOCK_PATH.exportPs1, '', hid));
  if (!r.ok){
    const d = await r.json().catch(()=>({error:'HTTP ' + r.status}));
    const out = document.getElementById('dkSaveOut');
    if (out) out.innerHTML = '<div class="dock-err">✗ ' + esc(d.error || ('HTTP ' + r.status)) + '</div>';
    return false;
  }
  const blob = await r.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'activation_' + hid + '.ps1';
  document.body.appendChild(a);
  a.click();
  a.remove();
  return false;
}

/* ══ 员工档案（列表 + 「详情」抽屉四块，可看可改）══════════════════════
 * 四块（老板裁定）：① 框架性能（平台算的档位事实只读）② 适配模型 ③ 具体桥模板 ④ 激活方式。
 * 改完**必须回显平台重新判定的事实**（换唤醒方式 ⇒ 档位事实跟着变），不许"改了还显示旧的"。
 */
async function dockLoadArchive(){
  if (!DOCK_LAST_OVERVIEW) await (async () => { await dockLoad(); })();
  const wrap = document.getElementById('dockArchiveWrap');
  if (!wrap) return;
  const hs = (DOCK_LAST_OVERVIEW && DOCK_LAST_OVERVIEW.harnesses) || [];
  if (!hs.length){
    wrap.innerHTML = '<div class="card"><h3>员工档案</h3>' +
      '<p class="lead">还没有员工 —— 从「登记一位新员工」开始。</p>' +
      '<div class="dock-form-actions"><button class="btn mini" onclick="dockGo(\'register\')">登记一位新员工</button></div></div>';
    return;
  }
  wrap.innerHTML = '<div class="card"><h3>员工档案 <span class="sub">（' + hs.length +
    ' 位 · 点「详情」可看可改四块）</span></h3>' +
    hs.map(h => '<div class="dock-cand" style="cursor:default">' +
      '<b>' + esc(h.harness_name || h.harness_id) + '</b> <span class="sub">(' + esc(h.harness_id) + ')</span><br>' +
      '<span class="sub">' + (h.online ? '在线' : '离线') + ' · 唤醒方式 <code>' + esc(h.wakeup_method || '—') +
      '</code>' + (h.interface_class_label ? (' · ' + esc(h.interface_class_label)) : '') + '</span><br>' +
      '<div class="dock-form-actions" style="margin-top:5px">' +
      '<button class="dock-detail-btn" onclick="dockOpenArchive(\'' + esc(h.harness_id) + '\')">详情</button>' +
      '</div></div>').join('') +
    '</div><div id="dkArchiveDrawer"></div>';
}

async function dockOpenArchive(hid){
  const drawer = document.getElementById('dkArchiveDrawer');
  if (!drawer) { await dockLoadArchive(); }
  const box = document.getElementById('dkArchiveDrawer');
  if (!box) return;
  box.innerHTML = '<p class="sub">读档案中…</p>';
  let d;
  try {
    const r = await fetch('/api/docking/harness/' + encodeURIComponent(hid));
    d = await r.json();
    if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
  } catch (e){
    box.innerHTML = '<div class="dock-err">✗ ' + esc(e.message || e) + '</div>' +
      '<p class="sub">档案读不到 —— 不显示成"没有档案"。确认 id 后重试。</p>';
    return;
  }
  DOCK_SCRIPT.hid = hid;
  box.innerHTML = dockDrawerHtml(d);
  _dockScrollTo('dkArchiveDrawer');
}

function _dkTable(rows){
  return '<table>' + rows.map(r => '<tr><td class="k">' + esc(r[0]) + '</td><td>' + esc(r[1]) +
    '</td><td><input data-k="' + esc(r[2]) + '" value="' + esc(r[3] == null ? '' : r[3]) +
    '" placeholder="留空＝不改"></td></tr>').join('') + '</table>';
}

function dockDrawerHtml(d){
  const a = d.archive || {};
  const f = d.facts || {};
  const ai = a.ai || {};
  const sc = a.activation_script || {};
  const wake = a.wakeup_method || '';
  const wm = wake === 'http_api'
    ? _dkTable([['api_base_url', a.api_base_url, 'api_base_url', a.api_base_url],
                ['api_message_path', a.api_message_path, 'api_message_path', a.api_message_path],
                ['api_outbox_dir', a.api_outbox_dir, 'api_outbox_dir', a.api_outbox_dir]])
    : wake === 'file_poll'
      ? _dkTable([['wakeup_dir', a.wakeup_dir, 'wakeup_dir', a.wakeup_dir]])
      : wake === 'acp'
        ? _dkTable([['acp_command', a.acp_command, 'acp_command', a.acp_command],
                    ['acp_cwd', a.acp_cwd, 'acp_cwd', a.acp_cwd]])
        : wake === 'http'
          ? _dkTable([['wakeup_url', a.wakeup_url, 'wakeup_url', a.wakeup_url]])
          : '';
  return '<div class="dock-drawer"><h3>档案 · ' + esc(a.harness_name || d.harness_id) +
    ' <span class="sub">(' + esc(d.harness_id) + ')</span></h3>' +
    '<p class="sub">改完保存，平台会**重新判定档位事实**并回显在这里 —— 不会出现"改了还显示旧的"。</p>' +
    '<details open><summary>① 框架性能（平台判定的档位事实：只读）</summary>' +
    '<table><tr><td class="k">档位 interface_class</td><td>' + esc(f.interface_class || '—') + '</td><td></td></tr>' +
    '<tr><td class="k">平台能不能叫醒</td><td>' + (f.can_be_woken_by_platform ? '能' : '不能（人工/桥兜底）') + '</td><td></td></tr>' +
    '<tr><td class="k">要不要协议 needs_protocol</td><td>' + esc(String(f.needs_protocol != null ? f.needs_protocol : '—')) + '</td><td></td></tr></table>' +
    _dkTable([['harness_type', a.harness_type, 'harness_type', a.harness_type],
              ['harness_version', a.harness_version, 'harness_version', a.harness_version],
              ['transports', (a.transports || []).join(','), 'transports', (a.transports || []).join(',')],
              ['preferred_transport', a.preferred_transport, 'preferred_transport', a.preferred_transport]]) +
    '</details>' +
    '<details><summary>② 适配模型（ai.*）</summary>' +
    _dkTable([['model_name', ai.model_name, 'ai.model_name', ai.model_name],
              ['provider', ai.provider, 'ai.provider', ai.provider],
              ['capabilities', (ai.capabilities || []).join(','), 'ai.capabilities', (ai.capabilities || []).join(',')],
              ['max_tokens', ai.max_tokens, 'ai.max_tokens', ai.max_tokens]]) +
    '<p class="sub">can_see / can_code / can_browse / can_file_ops：' +
    esc(JSON.stringify({can_see: ai.can_see, can_code: ai.can_code,
      can_browse: ai.can_browse, can_file_ops: ai.can_file_ops})) + '</p>' +
    '</details>' +
    '<details><summary>③ 具体桥模板</summary>' +
    _dkTable([['bridge_dir', a.bridge_dir, 'bridge_dir', a.bridge_dir],
              ['bridge_status', a.bridge_status, 'bridge_status', a.bridge_status]]) +
    '</details>' +
    '<details><summary>④ 激活方式（含视觉激活脚本本体）</summary>' +
    '<div class="row"><span class="k sub">wakeup_method</span>' +
    '<select data-k="wakeup_method" onchange="dockWakeSwapped(this)">' +
    ['acp', 'file_poll', 'http_api', 'mcp', 'clipboard', 'http'].map(m =>
      '<option value="' + m + '"' + (m === wake ? ' selected' : '') + '>' + m + '</option>').join('') +
    '</select></div>' + wm +
    '<div class="row"><span class="k sub">视觉激活脚本</span>' +
    '<button class="dock-detail-btn" onclick="dockScriptPick(\'' + esc(d.harness_id) +
    '\');dockMountScript();_dockScrollTo(\'dockScriptWrap\')">去脚本激活工作台改</button>' +
    '<button class="dock-detail-btn" onclick="dockExportPs1From(\'' + esc(d.harness_id) + '\')">导出 .ps1 副本</button></div>' +
    (sc.text ? ('<pre>' + esc(sc.text) + '</pre><p class="sub">上次试投：' +
      (sc.verified === true ? '✓ 已自证' : '⚠ 未核实（别当成已验证）') +
      (sc.method ? (' · 注入方式 ' + esc(sc.method)) : '') + '</p>')
      : '<p class="sub">还没存过脚本 —— 去「脚本激活」工作台做一份（填关键词 → 让平台去找 → 试投 → 保存）。</p>') +
    '</details>' +
    '<div class="dock-facts">平台刚判定的档位事实（只读）：档位 <b>' + esc(f.interface_class || '—') +
    '</b> · 平台能叫醒：<b>' + (f.can_be_woken_by_platform ? '能' : '不能') +
    '</b> · 导出候选与脚本的真相都在档案里。</div>' +
    '<div class="dock-form-actions">' +
    '<button class="btn primary" onclick="dockSaveArchive(\'' + esc(d.harness_id) + '\')">保存档案改动</button>' +
    '<span class="dock-msg" id="dkArcMsg"></span></div>' +
    '<div id="dkArcOut"></div></div>';
}

/** 换唤醒方式后立刻重拉档案（回显平台重算的档位事实 —— 不等保存，先让用户看见会变成什么）。 */
async function dockWakeSwapped(sel){
  const hid = DOCK_SCRIPT.hid;
  if (!hid) return;
  const box = document.getElementById('dkArchiveDrawer');
  if (!box) return;
  const r = await fetch('/api/docking/harness/' + encodeURIComponent(hid));
  const d = await r.json();
  if (r.ok && d.archive){
    d.archive.wakeup_method = sel.value;      // 本地预览：还没保存，先按新方式算事实
    const f = d.facts || {};
    box.innerHTML = dockDrawerHtml(Object.assign({}, d, {
      facts: (sel.value === (d.archive.wakeup_method || ''))
        ? f : f,
    }));
    const note = document.getElementById('dkArcMsg');
    if (note) note.textContent = '唤醒方式改成 ' + sel.value + '（还没保存 —— 保存后平台才落档）';
  }
}

async function dockSaveArchive(hid){
  const box = document.getElementById('dkArchiveDrawer');
  const msg = document.getElementById('dkArcMsg');
  const out = document.getElementById('dkArcOut');
  if (!box || !out) return;
  const patch = {};
  box.querySelectorAll('input[data-k], select[data-k]').forEach(el => {
    const k = el.getAttribute('data-k');
    const v = (el.value || '').trim();
    if (v === '') return;
    if (k === 'transports') patch.transports = v.split(',').map(s => s.trim()).filter(Boolean);
    else if (k === 'ai.capabilities') patch.ai = Object.assign({}, (DOCK_ARC_AI || {}), {capabilities: v.split(',').map(s => s.trim()).filter(Boolean)});
    else if (k === 'ai.model_name') patch.ai = Object.assign({}, (DOCK_ARC_AI || {}), {model_name: v});
    else if (k === 'ai.provider') patch.ai = Object.assign({}, (DOCK_ARC_AI || {}), {provider: v});
    else if (k === 'ai.max_tokens') patch.ai = Object.assign({}, (DOCK_ARC_AI || {}), {max_tokens: Number(v) || v});
    else patch[k] = v;
  });
  if (!Object.keys(patch).length){ if (msg) msg.textContent = '没有要改的（都留空＝不改）'; return; }
  if (msg) msg.textContent = '保存中…';
  const res = await dockPost(dockPath(DOCK_PATH.archiveSave, '', hid), patch);
  const d = res.data || {};
  if (msg) msg.textContent = '';
  if (!res.ok || d.error){
    out.innerHTML = '<div class="dock-err">✗ ' + esc(d.error || ('HTTP ' + (res.status || '?'))) + '</div>' +
      '<p class="sub">按上面的原因改完再点一次。</p>';
    return;
  }
  // ★改完必须回显平台重新判定的事实（响应里带回重算后的 archive + facts）
  DOCK_ARC_AI = (d.archive && d.archive.ai) || {};
  out.innerHTML = '<div class="dock-ok">✓ 已保存：改了 ' + esc((d.changed || []).join('、') || '（无）') +
    '；平台已重算档位事实（见上）。</div>';
  box.innerHTML = dockDrawerHtml(d);
}

let DOCK_ARC_AI = {};

/** 档案抽屉里的 .ps1 导出（与脚本激活工作台同一个端点）。 */
async function dockExportPs1From(hid){
  const r = await fetch(dockPath(DOCK_PATH.exportPs1, '', hid));
  if (!r.ok){
    const d = await r.json().catch(()=>({error:'HTTP ' + r.status}));
    const box = document.getElementById('dkArchiveDrawer');
    if (box) box.innerHTML = '<div class="dock-err">✗ ' + esc(d.error || ('HTTP ' + r.status)) + '</div>' + box.innerHTML;
    return;
  }
  const blob = await r.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'activation_' + hid + '.ps1';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/* ══ 平台 Agent 直聊（问前台）═══════════════════════════════════════ */
async function dockChat(){
  const box = document.getElementById('chatInput');
  const msg = document.getElementById('chatMsg');
  const out = document.getElementById('chatReply');
  const text = (box && box.value || '').trim();
  if (!text){ if (msg) msg.textContent = '先写一句你要问的'; return; }
  if (msg) msg.textContent = '平台助手在想…（可能要十几秒）';
  const res = await dockPost(DOCK_PATH.chat, {message: text});
  if (msg) msg.textContent = res.ok ? '' : '失败，看下面原因';
  const d = res.data || {};
  if (!res.ok || d.error){
    out.innerHTML = '<div class="dock-err">✗ ' + esc(d.error || ('HTTP ' + (res.status || '?'))) +
      '</div><p class="sub">这一页不会把失败显示成「没内容」。</p>';
    return;
  }
  const steps = d.steps || [];
  out.innerHTML = '<div class="dock-ok2">平台助手：</div><div class="dock-chat">' + esc(d.reply || '') + '</div>' +
    (steps.length ? ('<details><summary>它用了哪些工具（' + steps.length + ' 步）</summary><pre>' +
                     esc(JSON.stringify(steps, null, 2)) + '</pre></details>') : '');
  if (box) box.value = '';
}

/* ── 启动 ─────────────────────────────────────────────────────── */
dockMountRegister();
dockLoad();
dockLoadActivation();
