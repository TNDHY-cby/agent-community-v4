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

/** 时间戳按人读的样子显示（评审第「小」条：别把 ISO 串原样摆在横幅里）；解析不了就原样给。 */
function _dockClock(s){
  if (!s) return '';
  const dt = new Date(s);
  if (isNaN(dt.getTime())) return String(s);
  const hh = String(dt.getHours()).padStart(2, '0');
  const mm = String(dt.getMinutes()).padStart(2, '0');
  return '更新于 ' + hh + ':' + mm;
}

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
    '　·　' + esc(_dockClock(d.generated_at));

  // ── 左栏：工作间选择器**只在总控渲染**（单间模式整块不出现 —— V34 §1/§9.6）
  // 2026-10-17：链接与状态合并为**一份**列表（此前同一批工作间列两遍 = 混乱感来源）
  const pick = document.getElementById('wsPickerWrap');
  const rail = document.getElementById('railList');
  if (DOCK_GLOBAL){
    /* 「工作间」小标题已移除（老板 2026-10-19 裁定：无意义）——列表本身自明 */
    pick.innerHTML =
      '<div style="display:flex;flex-direction:column;gap:4px">' +
      wsRows.map(w => '<a class="rail-item" href="/docking.html?ws=' + encodeURIComponent(w.ws_id) + '">' +
        '<span class="ri-name">' + esc(w.name || w.ws_id) + '</span>' +
        '<span class="ri-sub">' + esc(w.status || '') + ' · 接入 ' + (w.docked||0) + '/' + (w.member_count||0) +
        ((w.pending||0) ? ' · 待接 ' + w.pending : '') +
        ((w.offline||0) ? ' · <b class="ri-bad">掉线 ' + w.offline + '</b>' : '') +
        '</span></a>').join('') +
      '</div>';
    rail.innerHTML = '';               // 不再重复列一遍状态（合并进上面）
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
  html += '<div class="dock-section-head">已登记员工 <span class="n">' + hs.length + '</span>' +
    (dang.length ? ' · ⚠ 悬空 ' + dang.length : '') + '</div>';
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

/* ── 写请求白名单（FE-26 §5：页面里非 GET 的请求只能落在这 27 条上）──────
 * `*` = 一个路径段（容纳 ws/member/harness id）。
 * 守卫核对：这张表必须**正好**是设计稿那 27 条 —— 多一条是偷偷扩权，少一条按钮点不动。
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
  // ★FE-30（V92 统一投递层）：送话给某个员工 —— 路径比 '/api/docking/harness/*' 多一段，
  //   白名单按段比对 ⇒ 必须单独列出（少这一行，按钮会被自己的闸拦住）。
  '/api/docking/harness/*/deliver',
  // ★FE-30（D后端-3 表层交互）：教 / 清「输入框位置」——只记相对坐标，不改别的
  '/api/docking/harness/*/anchor',
  '/api/docking/harness/*/regions',   // ★D后端-17/18：框选区域（截图是 GET，不用进白名单）
  '/api/docking/harness/*/bind',
  '/api/docking/harness/*/visual-probe', // ★FE-31：测试新建会话（点框中心 → 前后比画面）
  '/api/docking/harness/*/visual-run',   // ★D后端-23：一次跑完 新建会话→粘→发送（测试发送消息 / 平时叫醒） // ★FE-31：测试新建会话（点框中心 → 前后比画面）      // ★2026-10-24 老板真机：框选保存时把这扇窗绑给该员工（否则按员工那条路认不出目标）
  // ★FE-30（D后端-4 浏览器直连 CDP）：扫输入框 / 往输入框塞文本（真的会往目标里打字）
  '/api/docking/web/editors',
  '/api/docking/web-send',
  // ★对端公司管理 + 对外任务（FE-26 §5 增补 / V62 V63，2026-10-20 由 DSH 加，
  //   与判据 test_docking_ui_static 的 27 条一一对应；前端同学改界面时**保留这 6 行**）
  '/api/peers',
  '/api/peers/*',
  '/api/peers/*/verify',
  '/api/peers/*/dispatch',
  '/api/peers/*/enabled',
  '/api/peer-tasks/*/refresh',
  // ★跨端中转两笔（FE-26 §5 增补二 / V66 V67，2026-10-21 由 DSH 加，MiMo 224 申请，
  //   与判据 test_docking_ui_static 的 27 条一一对应；前端同学改界面时**保留这 2 行**）
  '/api/relay/send',
  '/api/relay/dispatch',
  // 回话探针（V74，DSH 235 放行）：查状态 GET 不受限；发起 auto/human_confirm 走此行
  '/api/harness/*/probe-reply',
  // Agent Manager（安装器，V80/DSH 234）：检测/安装/升级；安装默认关（403=开关没开，不是失败）
  '/api/agents/*/detect',
  '/api/agents/*/install',
  '/api/agents/*/upgrade',
  // V85 名册健康（隐藏/申诉）：隐藏要理由，申诉即恢复并留痕
  '/api/harness/*/hide',
  '/api/harness/*/appeal',
  // V83 附属记录（存证：签名/扫描/审计；verify 吃整条记录）与 V84 可发现开关
  '/api/attest/*',
  '/api/attest/*/*',
  '/api/workshops/*/discoverable',
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
  /* ★FE-30：送话（读目标 / 送一段话）—— 上层只说"送给这个员工"，通道与目标由后端定 */
  deliverTarget: '/api/docking/harness/{hid}/delivery',
  deliverHarness:'/api/docking/harness/{hid}/deliver',
  /* ★FE-30：表层交互（教位置）与浏览器直连（CDP）—— 两者都是"投递的备选路" */
  anchor:        '/api/docking/harness/{hid}/anchor',
  webProbe:      '/api/docking/web/probe',
  webEditors:    '/api/docking/web/editors',
  webSend:       '/api/docking/web-send',
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

/** DELETE 也走**同一道白名单闸**（D后端-3 的「清掉已教位置」是 DELETE）。 */
async function dockDelete(path){
  if (!dockAllowed(path)){
    return {ok:false, data:{error:'这个页面不允许调用 ' + path + '（对接中心只做接入与激活）'}};
  }
  const r = await fetch(path, {method:'DELETE'});
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

/* ══ 服务菜单导航：点瓦片 → 弹窗当场办（2026-10-17 老板裁定：不要长文定位）══ */
function _dockScrollTo(id){
  const el = document.getElementById(id);
  if (el && el.scrollIntoView) el.scrollIntoView({behavior:'smooth', block:'start'});
}
const DOCK_SVC = {
  register:        { sec:'dockRegisterWrap', title:'登记一位新员工', mount(){ dockMountRegister(); } },
  script_activate: { sec:'dockScriptWrap',   title:'脚本激活工作台', mount(){ dockMountScript(); } },
  deliver:         { sec:'dockDeliverWrap',  title: DOCK_GLOBAL ? '送到哪位 harness（平台自己找目标、选通道）' : '送话给员工（平台自己找目标、选通道）', mount(){ dockMountDeliver(); } },
  archive:         { sec:'dockArchiveWrap',  title:'员工档案',       mount(){ dockLoadArchive(); } },
  ask_frontdesk:   { sec:'dockAskWrap',      title:'问前台',         mount(){} },
  update:          { sec:'dockUpdateWrap',   title:'检查更新',       mount(){ dockLoadUpdate(); } },
  agent_manager:   { sec:'dockAgentsWrap',   title:'Agent 安装器（检测 / 安装 / 升级）', mount(){ dockLoadAgents(); } },
  peers:           { sec:'dockPeersWrap',    title:'外部公司（登记对端 / 验证 / 派活 / 隧道）', mount(){ dockMountPeers(); } },
  ws_ops:          { sec:'dockActivateWrap', title:'工作间操作（建会话 / 送桥 / 抢救 / 通道 / 架桥）', mount(){ dockMountWsGuide(); } }
};
const DOCK_WS_TILES = ['session_bridge', 'onboard_recover', 'channel', 'bridge_helper'];

/* ══ 外部公司（V62/V63）════════════════════════════════════════
 * 纪律：令牌只提交不保存（input type=password，提交后清空）；token 永远只显示 token_masked；
 * 验证结论 last_verify.ok：true 绿 / false 红 / null·缺失 黄「未验证」；
 * 对外任务状态按后端枚举原样显示——unreachable 绝不显示成进行中/成功；
 * 自动收回是部署侧环境变量（AC_PEER_POLL_*），界面不做开关，只按 poll-status 如实描述；
 * 无任何「把 18920 映射到公网」的引导；不提具体穿透服务商名。 */
const PEER_STATUS_TEXT = {
  sent:'刚投出', working:'对端在做', done:'完成', failed:'失败', unreachable:'够不着对端'
};
function _peerVerifyDot(lv){
  const ok = lv && typeof lv.ok === 'boolean' ? lv.ok : null;
  if (ok === true) return '<span class="dotv ok" title="上次验证通过' + (lv.checked_at?(' · '+esc(String(lv.checked_at))):'') + '"></span>';
  if (ok === false) return '<span class="dotv bad" title="上次验证失败' + (lv.detail?(' · '+esc(String(lv.detail))):'') + '"></span>';
  return '<span class="dotv unknown" title="未验证"></span>';
}
function dockMountPeers(){
  dockLoadPeers();
  dockLoadPeerTasks();
  dockLoadTunnelAdvice();
  dockLoadRelay();
}
async function dockLoadPeers(){
  const box = document.getElementById('peersList');
  if (!box) return;
  try{
    const r = await fetch('/api/peers');
    const d = await r.json();
    const rows = d.peers || d.items || [];
    if (rows.length === 0){ box.innerHTML = '<p class="sub">还没有登记外部公司。右侧表单登记一个：名字 + 对端地址（不带路径）+ 对方令牌。</p>'; return; }
    box.innerHTML = rows.map(p => {
      const en = p.enabled !== false;
      return '<div class="peer-row" data-peer="' + esc(p.peer_id||'') + '">' +
        '<div class="pr-top">' + _peerVerifyDot(p.last_verify) +
        '<span class="pr-name">' + esc(p.display_name || p.peer_id || '') + '</span>' +
        (en ? '' : '<span class="dotv dis" title="已停用：派活会被对端/本侧拒绝"></span><span class="sub">已停用</span>') +
        '<span class="sub">令牌 ' + esc(p.token_masked || '（未存）') + '</span></div>' +
        '<div class="pr-sub">' + esc(p.base_url || '') + (p.note?(' · ' + esc(p.note)):'') + '</div>' +
        '<div class="pr-acts">' +
        '<button class="btn mini" onclick="dockPeerVerify(\'' + esc(p.peer_id||'') + '\')">验证</button>' +
        '<button class="btn mini" onclick="dockPeerDispatchAsk(\'' + esc(p.peer_id||'') + '\')">派活</button>' +
        '<button class="btn mini" onclick="dockPeerToggle(\'' + esc(p.peer_id||'') + '\',' + (en?'false':'true') + ')">' + (en?'停用':'启用') + '</button>' +
        '<button class="btn mini" onclick="dockPeerEdit(\'' + esc(p.peer_id||'') + '\')">编辑</button>' +
        '<button class="btn mini" onclick="dockPeerDelete(\'' + esc(p.peer_id||'') + '\')">删除</button>' +
        '</div><div class="pr-sub" data-peermsg></div></div>';
    }).join('');
  }catch(e){ box.innerHTML = '<p class="sub">加载失败：' + esc(e.message) + '</p>'; }
}
function _peerRowMsg(id, text, bad){
  const row = document.querySelector('.peer-row[data-peer="' + id + '"] [data-peermsg]');
  if (row){ row.textContent = text || ''; row.style.color = bad ? 'var(--red,#FF0000)' : ''; }
}
async function dockPeerVerify(id){
  _peerRowMsg(id, '验证中…（读对端卡片，不花 AI 费用）');
  const res = await dockPost('/api/peers/' + encodeURIComponent(id) + '/verify', {});
  if (res.ok && res.data && res.data.ok){ _peerRowMsg(id, '验证通过' + (res.data.elapsed_ms?('（' + res.data.elapsed_ms + ' ms）'):'')); dockLoadPeers(); }
  else _peerRowMsg(id, '验证未通过：' + ((res.data && (res.data.reason || res.data.error)) || ('HTTP ' + res.status)) + ((res.data && res.data.how_to_fix) ? ' · ' + res.data.how_to_fix : ''), true);
}
function dockPeerEdit(id){
  /* 列表里已有打码信息；把可改字段填进表单（令牌永不回填明文） */
  fetch('/api/peers').then(r=>r.json()).then(d=>{
    const p = (d.peers||d.items||[]).find(x=>x.peer_id===id);
    if(!p)return;
    document.getElementById('peerEditId').value = id;
    document.getElementById('peerName').value = p.display_name || '';
    document.getElementById('peerUrl').value = p.base_url || '';
    document.getElementById('peerToken').value = '';
    document.getElementById('peerNote').value = p.note || '';
    document.getElementById('peerFormTitle').textContent = '编辑外部公司（' + id + '）· 令牌留空=不改';
  }).catch(()=>{});
}
function dockPeerFormReset(){
  ['peerEditId','peerName','peerUrl','peerToken','peerNote'].forEach(i=>{const e=document.getElementById(i);if(e)e.value='';});
  document.getElementById('peerFormTitle').textContent = '登记外部公司';
  document.getElementById('peerFormMsg').textContent = '';
}
async function dockPeerSave(){
  const msg = document.getElementById('peerFormMsg');
  const editId = document.getElementById('peerEditId').value;
  const body = {
    display_name: document.getElementById('peerName').value.trim(),
    base_url: document.getElementById('peerUrl').value.trim(),
    note: document.getElementById('peerNote').value.trim()
  };
  const token = document.getElementById('peerToken').value;
  if (token) body.token = token;
  if (editId) body.peer_id = editId;
  msg.textContent = '保存中...';
  const res = await dockPost('/api/peers', body);
  document.getElementById('peerToken').value = '';   /* 明文即用即清，不留在前端 */
  if (!res.ok || res.data.error){
    msg.textContent = '保存未通过：' + ((res.data && (res.data.error || res.data.reason)) || ('HTTP ' + res.status)) + ((res.data && res.data.how_to_fix) ? ' · ' + res.data.how_to_fix : '');
    msg.style.color = 'var(--red,#FF0000)';
    return;
  }
  msg.style.color = '';
  msg.textContent = '已保存' + (res.data.token_masked ? (' · 令牌 ' + res.data.token_masked) : '');
  dockPeerFormReset(); dockLoadPeers();
}
async function dockPeerToggle(id, enabled){
  const res = await dockPost('/api/peers/' + encodeURIComponent(id) + '/enabled', {enabled});
  if (!(res.ok && !(res.data && res.data.error))) _peerRowMsg(id, '操作未成功：' + ((res.data && (res.data.error||res.data.reason)) || ('HTTP '+res.status)), true);
  dockLoadPeers();
}
async function dockPeerDelete(id){
  if (!confirm('确定删除这个外部公司登记？（只删本机登记，不动对端）')) return;
  const path = '/api/peers/' + encodeURIComponent(id);
  if (!dockAllowed(path)){ _peerRowMsg(id, '这个页面不允许调用 ' + path, true); return; }
  try{
    const r = await fetch(path, {method:'DELETE'});
    if (!r.ok){ const d = await r.json().catch(()=>({})); _peerRowMsg(id, '删除未成功：' + (d.error || ('HTTP ' + r.status)) + (d.how_to_fix ? (' · ' + d.how_to_fix) : ''), true); return; }
    dockLoadPeers();
  }catch(e){ _peerRowMsg(id, '删除失败：' + e.message, true); }
}
async function dockPeerDispatchAsk(id){
  const title = prompt('要派给外部公司的任务标题：');
  if (title === null) return;
  if (!title.trim()){ _peerRowMsg(id, '标题不能为空', true); return; }
  _peerRowMsg(id, '派活中…');
  const res = await dockPost('/api/peers/' + encodeURIComponent(id) + '/dispatch', {title: title.trim()});
  if (res.ok && !(res.data && res.data.error)){
    const pt = res.data.peer_task_id ? ('（对外任务 ' + res.data.peer_task_id + '）') : '';
    _peerRowMsg(id, '已派给对端' + pt);
    dockLoadPeerTasks();
  } else {
    _peerRowMsg(id, '派活未成功：' + ((res.data && (res.data.error || res.data.reason)) || ('HTTP ' + res.status)) + ((res.data && res.data.how_to_fix) ? ' · ' + res.data.how_to_fix : ''), true);
  }
}
const PT_STATUS_TEXT = PEER_STATUS_TEXT;
async function dockLoadPeerTasks(){
  const box = document.getElementById('peerTasksList');
  if (!box) return;
  try{
    /* 自动收回口径（V63 §6 / DSH 214）：开关是环境变量，界面不做开关按钮，只按 poll-status 如实描述 */
    let poll = null;
    try{ poll = (await (await fetch('/api/peer-tasks/poll-status')).json()); }catch(e){}
    const note = document.getElementById('peerPollNote');
    if (note){
      if (poll && poll.enabled) note.textContent = '（自动收回开着：每 ' + (poll.interval_s||60) + ' 秒收一次，每轮最多 ' + (poll.max_per_round||3) + ' 条 · 运行中=' + (poll.running?'是':'否') + '）';
      else note.textContent = '（自动收回未开——状态只在你点「刷新回执」时更新）';
    }
    const r = await fetch('/api/peer-tasks');
    const d = await r.json();
    const rows = d.tasks || d.items || [];
    if (rows.length === 0){ box.innerHTML = '<p class="sub">还没有派出去的活。在对端行的「派活」按钮里填标题即可。</p>'; return; }
    box.innerHTML = rows.map(t => {
      const st = String(t.status||'');
      const stText = PT_STATUS_TEXT[st] || st;   /* 枚举原样；unreachable 显示为"够不着对端"，绝不写成进行中 */
      /* V64 对端交付物：只有清单没有内容——显示名字/大小/归属 + 「在对端机器上」，不做下载/打开按钮（本侧拿不到文件，做了就是假承诺）；
         artifacts=[] 且 note 为空时什么都不写，不暗示有交付物 */
      let arts = '';
      const al = Array.isArray(t.artifacts) ? t.artifacts : [];
      if (al.length){
        const fmt = a => esc(a.name||'?') + '（' + (a.bytes!=null ? (a.bytes>=1024 ? (a.bytes/1024).toFixed(1)+' KB' : a.bytes+' B') : '?') + '）' + (a.role?(' · '+esc(a.role)):'');
        arts = '<div class="pt-arts"><span class="sub">对端交付：</span>' +
          al.slice(0,3).map(fmt).join(' · ') +
          (al.length>3 ? (' <span class="sub">还有 ' + (al.length-3) + ' 个</span>') : '') +
          ' <span class="sub">· 在对端机器上，本侧不自动下载</span></div>';
      }
      return '<div class="pt-row" data-pt="' + esc(t.peer_task_id||t.id||'') + '">' +
        '<b>' + esc(t.title || '(无标题)') + '</b>' +
        '<span class="sub">投给 ' + esc(t.peer_display_name || t.peer_id || '?') + '</span>' +
        (t.via ? '<span class="pt-via ' + esc(t.via) + '">' + (t.via==='hub'?'经中转':'点对点') + '</span>' : '') +
        '<span class="pt-status ' + esc(st) + '">' + esc(stText) + '</span>' +
        '<span class="sub">' + esc(t.dispatched_at || t.created_at || '') + (t.receipt_at || t.updated_at ? (' · 回执 ' + esc(t.receipt_at || t.updated_at)) : '') + '</span>' +
        '<button class="btn mini" onclick="dockPtRefresh(\'' + esc(t.peer_task_id||t.id||'') + '\')" title="点对点的那条路才需要手动核对；经中转的回执会自动回来">手动核对</button>' +
        (st==='unreachable' ? '<span class="sub" style="color:var(--red,#FF0000)">我们查不到对端（隧道断/服务停/令牌失效）</span>' : '') +
        arts +
        '</div>';
    }).join('');
  }catch(e){ box.innerHTML = '<p class="sub">加载失败：' + esc(e.message) + '</p>'; }
}
async function dockPtRefresh(ptId){
  const res = await dockPost('/api/peer-tasks/' + encodeURIComponent(ptId) + '/refresh', {});
  if (!(res.ok && !(res.data && res.data.error))){
    /* 失败原因原样说话（常见：够不着对端） */
    const row = document.querySelector('.pt-row[data-pt="' + ptId + '"]');
    if (row){ let s = row.querySelector('span[style]'); row.insertAdjacentHTML('beforeend','<span class="sub" style="color:var(--red,#FF0000)">' + esc((res.data && (res.data.error || res.data.reason)) || ('HTTP ' + res.status)) + ((res.data && res.data.how_to_fix)?(' · '+res.data.how_to_fix):'') + '</span>'); }
  }
  dockLoadPeerTasks();
}
async function dockLoadTunnelAdvice(){
  const box = document.getElementById('peerAdvice');
  if (!box) return;
  try{
    const r = await fetch('/api/peers/tunnel-advice');
    const d = await r.json();
    const sug = d.suggest || [];
    const bad = d.must_not_expose || [];
    box.innerHTML =
      (sug.length ? sug.map(s=>'<p class="lead">· ' + esc(typeof s==='string'?s:JSON.stringify(s)) + '</p>').join('') : '<p class="sub">（后端暂无建议）</p>') +
      (bad.length ? '<p class="lead" style="color:var(--red,#FF0000)">绝对不能暴露：' + bad.map(b=>esc(typeof b==='string'?b:JSON.stringify(b))).join('；') + '</p>' : '');
  }catch(e){ box.innerHTML = '<p class="sub">加载失败：' + esc(e.message) + '</p>'; }
}

/* ── 跨端中转（V63 之后的本地优先架构，DSH 221/222/223）──
 * 只读展示（状态+收件箱）；send/dispatch 表单等 /api/relay/* 进白名单后另行添加。
 * 口径：configured=false 是"本机照常、跨端不可用"，不是错误态；
 * 在线与否只有 online=true 才算投到；handled.ok=false 是对端处理失败，detail 要显示。 */

/* ══ Agent 安装器（V80 / DSH 234）════════════════════════════
 * 四种返回分样式：403 开关关（中性灰，不是失败）/ 400 要确认 / 真失败（红，exit_code+tail 原样）/ dry_run（显示 cmd + 确认执行）。 */

/* ══ V85 名册健康：隐藏（要理由）/ 申诉（要说明，申诉即恢复并留痕）════════ */
async function dockHarnessHideAsk(id){
  const reason = prompt('隐藏「' + id + '」的理由（必填，会留痕）：');
  if (reason === null) return;
  if (!reason.trim()){ alert('理由必填。'); return; }
  const res = await dockPost('/api/harness/' + encodeURIComponent(id) + '/hide', {on: true, reason: reason.trim()});
  const d = res.data || {};
  alert((res.ok && d.success!==false) ? '已隐藏（数据未删，可申诉恢复）' : '隐藏未成功：' + ((d.error||d.reason) || ('HTTP ' + res.status)) + (d.how_to_fix ? (' · ' + d.how_to_fix) : ''));
  dockLoadArchive();
}
async function dockHarnessAppeal(id){
  const note = prompt('申诉说明（必填，为什么应该恢复）：');
  if (note === null) return;
  if (!note.trim()){ alert('说明必填。'); return; }
  const res = await dockPost('/api/harness/' + encodeURIComponent(id) + '/appeal', {note: note.trim()});
  const d = res.data || {};
  alert((res.ok && d.success!==false) ? '申诉已提交——名册已恢复（留痕）' : '申诉未成功：' + ((d.error||d.reason) || ('HTTP ' + res.status)) + (d.how_to_fix ? (' · ' + d.how_to_fix) : ''));
  dockLoadArchive();
}

async function dockLoadAgents(){
  const box=document.getElementById('agentsList');
  if(!box) return;
  try{
    const d=await (await fetch('/api/agents/catalog')).json();
    const rows=d.agents||[];
    if(!rows.length){ box.innerHTML='<p class="sub">目录为空。</p>'; return; }
    box.innerHTML=rows.map(a=>{
      const can=a.install_allowed_now===true;
      const aid=String(a.id||'').replace(/'/g,'');
      return '<div class="peer-row" data-ag="'+esc(aid)+'">'+
        '<div class="pr-top"><span class="pr-name">'+esc(a.name||aid)+'</span>'+
        '<span class="sub">'+esc(a.kind||'')+'</span>'+
        (a.install_supported?'':'<span class="sub">· 不支持自动安装</span>')+
        (can?'<span class="sub" style="color:#8FB39B">· 开关已开</span>':'<span class="sub">· 开关未开</span>')+'</div>'+
        (a.note?'<div class="pr-sub">'+esc(a.note)+'</div>':'')+
        (a.docs_url?'<div class="pr-sub">文档：'+esc(a.docs_url)+'</div>':'')+
        '<div class="pr-acts">'+
        '<button class="btn mini" onclick="dockAgentDetect(\''+esc(aid)+'\')">检测</button>'+
        (a.install_supported?('<button class="btn mini" onclick="dockAgentInstall(\''+esc(aid)+'\',true)">预览安装命令</button>'+
        '<button class="btn mini" onclick="dockAgentInstall(\''+esc(aid)+'\',false)">安装</button>'+
        '<button class="btn mini" onclick="dockAgentUpgrade(\''+esc(aid)+'\')">升级</button>'):'')+
        '</div><div class="pr-sub" data-agmsg style="white-space:pre-wrap"></div></div>';
    }).join('');
  }catch(e){ box.innerHTML='<p class="sub">加载失败：'+esc(e.message)+'</p>'; }
}
function _agMsg(id,text,bad){
  const row=document.querySelector('.peer-row[data-ag="'+id+'"] [data-agmsg]');
  if(row){ row.textContent=text||''; row.style.color=bad?'var(--red,#FF0000)':''; }
}
async function dockAgentDetect(id){
  _agMsg(id,'检测中…');
  try{
    const d=await (await fetch('/api/agents/'+encodeURIComponent(id)+'/detect',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json();
    _agMsg(id,(d.installed?('已安装'+(d.version?(' · 版本 '+d.version):'')+(d.path?(' · '+d.path):'')):'未安装')+(d.why?(' · '+d.why):''), d.installed===false&&d.why);
  }catch(e){ _agMsg(id,'检测失败：'+e.message,true); }
}
async function dockAgentInstall(id,dry){
  _agMsg(id,dry?'生成预览中…':'安装中…（可能要几分钟）');
  const res=await dockPost('/api/agents/'+encodeURIComponent(id)+'/install', dry?{dry_run:true}:{confirm:true});
  const d=res.data||{};
  if(res.status===403){ _agMsg(id,'安装开关关着（默认关，不是失败）：'+((d.error||'')+(d.how_to_fix?(' · '+d.how_to_fix):''))); return; }
  if(res.status===400){ _agMsg(id,'需要确认才能装：'+((d.error||'')+(d.how_to_fix?(' · '+d.how_to_fix):''))); return; }
  if(dry && d.cmd){ _agMsg(id,'将执行：\n'+d.cmd+'\n——确认无误再点「安装」'); return; }
  if(d.executed===true && d.ok!==true){
    _agMsg(id,'安装失败：exit_code='+String(d.exit_code!=null?d.exit_code:'?')+'\n输出：'+String(d.tail||'')+(d.how_to_fix?('\n怎么办：'+d.how_to_fix):''), true);
    return;
  }
  _agMsg(id,(d.ok?'安装完成':'已返回')+(d.cmd?(' · '+d.cmd):'')+(d.exit_code!=null?(' · exit '+d.exit_code):''));
  dockLoadAgents();
}
async function dockAgentUpgrade(id){ await dockAgentInstall(id,false); }

async function dockLoadRelay(){
  const sbox = document.getElementById('relayStatus');
  const ibox = document.getElementById('relayInbox');
  if (!sbox || !ibox) return;
  try{
    const st = await (await fetch('/api/relay/status')).json();
    if (st.configured){
      sbox.innerHTML = '<p class="lead">' + (st.connected?'<span class="dotv ok"></span> 已连接中转 <code>' + esc(st.hub_url||'') + '</code>（终端 <code>' + esc(st.terminal_id||'') + '</code>）' : '<span class="dotv bad"></span> 未连接中转：' + esc(st.last_error || st.note || '')) +
        ' · 收 ' + esc(String(st.received!=null?st.received:'-')) + ' / 发 ' + esc(String(st.sent!=null?st.sent:'-')) + '</p>';
    } else {
      sbox.innerHTML = '<p class="sub">' + esc(st.note || '没配置中转——本机一切照常，跨端不可用。') + '</p>';
    }
  }catch(e){ sbox.innerHTML = '<p class="sub">状态加载失败：' + esc(e.message) + '</p>'; }
  try{
    const r = await (await fetch('/api/relay/inbox?limit=50')).json();
    const msgs = (r.messages || []).slice().sort((a,b)=>(a.seq||0)-(b.seq||0));   /* 按 seq 升序 */
    if (msgs.length === 0){ ibox.innerHTML = '<p class="sub">收件箱为空。</p>'; return; }
    ibox.innerHTML = msgs.map(m => {
      let h = '';
      if (m.handled && typeof m.handled === 'object'){
        h = m.handled.ok ? '<span class="sub" style="color:var(--green,#8FB39B)">已处理</span>'
                         : '<span class="sub" style="color:var(--red,#FF0000)">对端处理失败：' + esc(m.handled.detail||'') + '</span>';
      }
      const pay = typeof m.payload === 'string' ? m.payload : JSON.stringify(m.payload||'');
      return '<div class="pt-row"><span class="sub">#' + esc(String(m.seq||'')) + '</span><b>' + esc(m.from||'?') + '</b>' +
        '<span class="pt-status sent">' + esc(m.kind||'text') + '</span>' +
        '<span class="sub" style="word-break:break-all">' + esc(pay.slice(0,120)) + '</span>' +
        '<span class="sub">' + esc(m.at!=null?new Date(m.at*1000).toLocaleString('zh-CN',{hour12:false}):'') + '</span>' + h + '</div>';
    }).join('');
  }catch(e){ ibox.innerHTML = '<p class="sub">收件箱加载失败：' + esc(e.message) + '</p>'; }
}

/* 发送/跨端派活（白名单 27 条已含 /api/relay/*，走 dockPost 单通道）。
 * 口径：online=false 也是 200 —— 说「已投递，等它上线来取」，绝不说「已送达」；
 * dispatch 失败 success=false 时 pt_id 照给（本地外部任务不假成功），error+how_to_fix 原话显示。 */
async function dockRelaySend(){
  const msg = document.getElementById('relaySendMsg');
  const to = document.getElementById('relayTo').value.trim();
  const payload = document.getElementById('relayText').value;
  if (!to || !payload.trim()){ msg.textContent = '终端 id 与内容都必填'; msg.style.color='var(--red,#FF0000)'; return; }
  msg.textContent = '发送中...'; msg.style.color='';
  const res = await dockPost('/api/relay/send', {to_terminal: to, payload: payload, kind: 'text'});
  if (res.ok && res.data && res.data.success){
    msg.textContent = (res.data.online ? '对方在线，已投递' : '对方不在线：已暂存在中转，等它上线来取') + (res.data.note ? (' · ' + res.data.note) : '');
    document.getElementById('relayText').value = '';
  } else {
    msg.style.color = 'var(--red,#FF0000)';
    msg.textContent = '发送未成功：' + ((res.data && (res.data.error || res.data.reason)) || ('HTTP ' + res.status)) + ((res.data && res.data.how_to_fix) ? (' · ' + res.data.how_to_fix) : '');
  }
}
async function dockRelayDispatch(){
  const msg = document.getElementById('relayDispMsg');
  const to = document.getElementById('relayDispTo').value.trim();
  const title = document.getElementById('relayDispTitle').value.trim();
  if (!to || !title){ msg.textContent = '终端 id 与任务标题都必填'; msg.style.color='var(--red,#FF0000)'; return; }
  msg.textContent = '派活中...'; msg.style.color='';
  const res = await dockPost('/api/relay/dispatch', {to_terminal: to, title: title});
  if (res.ok && res.data && res.data.success){
    msg.textContent = (res.data.online ? '对方在线，已投递' : '对方不在线：已暂存在中转，等它上线来取') +
      (res.data.pt_id ? (' · 对外任务 ' + res.data.pt_id + '（可在下方列表看进度）') : '');
    document.getElementById('relayDispTitle').value = '';
    dockLoadPeerTasks();
  } else {
    msg.style.color = 'var(--red,#FF0000)';
    msg.textContent = '派活未成功：' + ((res.data && (res.data.error || res.data.reason)) || ('HTTP ' + res.status)) + ((res.data && res.data.how_to_fix) ? (' · ' + res.data.how_to_fix) : '') +
      (res.data && res.data.pt_id ? (' · 本地外部任务 ' + res.data.pt_id + ' 已留记录（未成功不算成功）') : '');
  }
}

function dockOpenModal(svc){
  const meta = DOCK_SVC[svc] || (DOCK_WS_TILES.indexOf(svc) >= 0 ? DOCK_SVC.ws_ops : null);
  if (!meta) return;
  try { meta.mount(); } catch (e) { /* 挂载失败时下面仍把区块放进弹窗，错误在区块内说话 */ }
  const sec = document.getElementById(meta.sec);
  const ov = document.getElementById('dockModalOverlay');
  const body = document.getElementById('dockModalBody');
  const title = document.getElementById('dockModalTitle');
  if (!sec || !ov || !body) { _dockScrollTo(meta.sec); return; }   // 弹窗容器缺失时退回旧行为
  body.appendChild(sec);
  if (title) title.textContent = meta.title;
  ov.classList.add('show');
  document.body.style.overflow = 'hidden';
}
function dockCloseModal(){
  const ov = document.getElementById('dockModalOverlay');
  const body = document.getElementById('dockModalBody');
  const home = document.getElementById('dockModalHome');
  if (!ov || !body || !home) return;
  while (body.firstChild) home.appendChild(body.firstChild);
  ov.classList.remove('show');
  document.body.style.overflow = '';
}
document.addEventListener('keydown', function(e){
  if (e.key === 'Escape') dockCloseModal();
});

function dockGo(svc){ dockOpenModal(svc); }

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
  dockPromptLoad();   // 首选栏默认就给出「我还不确定」的话术（来自端点，不自己拼）
}

/* ══ 登记前话术（V54 · 通道 121 号）═══════════════════════════════
 * 三条不许：① 话术只来自 GET /api/harness/register-prompt（前端不拼）；
 *          ② 复制成功 ≠ 注册成功（反馈里写明）；
 *          ③ 不写死端口/本机路径（地址由端点 platform_url 给）。
 */
let _dockPromptText = '';
async function dockPromptLoad(){
  const sel = document.getElementById('regPromptProto');
  const pre = document.getElementById('regPromptPre');
  const msg = document.getElementById('regPromptMsg');
  const copyBtn = document.getElementById('regPromptCopy');
  if (!sel || !pre) return;
  _dockPromptText = '';
  if (copyBtn) copyBtn.disabled = true;
  const proto = sel.value;
  if (msg) msg.textContent = '正在取平台话术…';
  try {
    const u = '/api/harness/register-prompt' + (proto ? ('?wakeup_method=' + encodeURIComponent(proto)) : '');
    const r = await fetch(u);
    const d = await r.json().catch(() => ({ error: '响应不可解析' }));
    if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
    pre.textContent = d.prompt || '';
    _dockPromptText = d.prompt || '';
    if (copyBtn && _dockPromptText) copyBtn.disabled = false;
    if (msg) {
      msg.textContent = (d.known_protocol === false)
        ? '已按「我还不确定」给通用话术（先跑通基础四项）'
        : ('协议模板：' + (d.wakeup_method || ''));
    }
  } catch (e) {
    pre.textContent = '';
    if (msg) msg.innerHTML = '<span class="dock-err">✗ 话术取不到：' + esc(e.message || e) +
      '（后端 /api/harness/register-prompt 不可用；可改用下方「或者你自己填」）</span>';
  }
}
function dockPromptCopy(){
  const msg = document.getElementById('regPromptMsg');
  if (!_dockPromptText) { if (msg) msg.textContent = '还没有话术 —— 上面没取到，先看错误提示。'; return; }
  const done = ok => {
    if (msg) msg.innerHTML = ok
      ? '<span class="dock-ok2">✓ 已复制（复制成功 ≠ 注册成功 —— 发给你的 AI，它注册完再回来刷新）</span>'
      : '<span class="dock-err">✗ 复制失败 —— 请手动选中上面文字复制</span>';
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(_dockPromptText).then(() => done(true)).catch(() => done(false));
  } else {
    try {
      const ta = document.createElement('textarea');
      ta.value = _dockPromptText; document.body.appendChild(ta); ta.select();
      document.execCommand('copy'); ta.remove(); done(true);
    } catch (e) { done(false); }
  }
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
  /* ★2026-10-24（FE-31）：**总控**里的脚本激活＝"给这个 Harness 注册一套视觉脚本"（模板性质）；
     单间（?ws=）那版老板说另给，先保持原来的三段面板。 */
  if (DOCK_GLOBAL) { dockMountScriptTemplate(wrap); return; }
  wrap.innerHTML =
    '<div class="card"><h3>脚本激活 <span class="sub">（给一个还没醒来的 agent 存一份"叫醒话术"）</span></h3>' +

    /* ① 选窗口 */
    '<div class="dk-step">' +
    '<div class="dk-step-h"><span class="no">①</span>选窗口<span class="sub">填一个会出现在那个窗口标题里的字</span></div>' +
    '<div class="dock-form">' +
    '<select id="dkHid" onchange="dockScriptPick()"><option value="">先选是哪位员工…</option></select>' +
    '<input id="dkKw" placeholder="会出现在那个窗口标题里的字，如：示例App|示例工具">' +
    '</div>' +
    '<div id="dkSug" class="dock-sug"><p class="sub">候选关键词（平台从档案与线索里推，每条带出处）—— 选员工后给。</p></div>' +
    '<div class="dock-form-actions">' +
    '<button class="btn mini" onclick="dockLocate()">让平台去找</button>' +
    '<span class="dock-msg" id="dkLocateMsg"></span></div>' +
    '<div id="dkLocateOut"><p class="sub">填好关键词再点。</p></div>' +
    '<div id="dkPickMsg"><p class="sub">选好窗口后，下一步是框选要写字的那一行。</p></div>' +
    '<div class="dock-form-actions">' +
    '<button class="btn primary" onclick="dockGotoBoxFromScript()">去框选输入框（推荐）</button>' +
    '<span class="dock-msg" id="dkReason"></span></div>' +
    '</div>' +

    /* ② 话术与正文 */
    '<div class="dk-step">' +
    '<div class="dk-step-h"><span class="no">②</span>话术与正文<span class="sub">这段字会被粘进对方窗口，把它叫醒</span></div>' +
    '<textarea id="dkScriptText" rows="5" placeholder="点「生成脚本正文」自动生成；也可以自己改。"></textarea>' +
    '<p class="sub">正文在<b>你自己的会话里</b>执行：不依赖平台、没有地址/端口。退出码 <code>0</code> 已核实 · ' +
    '<code>3</code> 送出但未核实 · <code>4</code> 没找到窗口 · <code>5</code> 没走到前台或没点进去 · <code>6</code> 其它。</p>' +
    '<div class="dock-form-actions">' +
    '<button class="btn mini" onclick="dockComposeScript()">生成脚本正文</button>' +
    '<a class="dock-detail-btn" id="dkExport" href="#" onclick="return dockExportPs1()">导出 .ps1 副本（同一份正文＋注释头）</a>' +
    '<span class="dock-msg" id="dkSaveMsg"></span></div>' +
    '</div>' +

    /* ③ 执行与结果 */
    '<div class="dk-step">' +
    '<div class="dk-step-h"><span class="no">③</span>执行与结果<span class="sub">存进档案 / 让平台现在就叫醒它</span></div>' +
    '<div class="dock-form-actions">' +
    '<button class="btn primary" onclick="dockSaveScript()">保存为该员工的激活方式</button>' +
    '<button class="btn mini" onclick="dockRunScript()">一键脚本激活</button>' +
    '</div>' +
    '<div id="dkSaveOut"></div>' +
    '</div>' +

    '</div>';
wrap.dataset.mounted = '1';
  dockScriptFillEmployees();
  dockAnchorLoad('dkScript');
}

/* ══════════════════════════════════════════════════════════════════════════
   视觉脚本注册模板（FE-31 · 只在大厅总控里）
   老板 2026-10-24 口述规格：无协议 harness 只能靠视觉脚本激活
   —— 注册脚本 → 拉到最前 → 点输入框 → 粘固定话术（提醒检查邮箱）→ 发送。
   四段：① 选窗口（点一条即绑定）② 框四块+引导词+固定话术 ③ 两个测试 ④ 保存
   ══════════════════════════════════════════════════════════════════════════ */
const REGTPL = { title: '', hwnd: '', cw: 0, ch: 0, usable: null, why: '', pickable: [], tests: { new_session: null, send: null }, humanSend: false };

function dockMountScriptTemplate(wrap){
  wrap.innerHTML =
    '<div class="card"><h3>脚本激活 · 注册模板 <span class="sub">给一个"自己不会醒"的 Harness 注册一套视觉脚本（大厅这里是模板，工作间里再配置）</span></h3>' +

    /* ① 选窗口 */
    '<div class="dk-step">' +
    '<div class="dk-step-h"><span class="no">①</span>选窗口<span class="sub">输入一个模糊名，确定后从下拉里点真实窗口（点一条就算绑定）</span></div>' +
    '<div class="dock-form">' +
    '<input id="regTplQ" placeholder="输入一个模糊的窗口名，例如：trae" onkeydown="if(event.key===\'Enter\'){event.preventDefault();regTplSearch();}">' +
    '<button class="btn mini" onclick="regTplSearch()">确定</button>' +
    '</div>' +
    '<p class="sub">点确定后，平台按<b>相似程度</b>把相近的窗口列在下面 —— 点一条就算绑定。' +
    '（大厅这里是<b>harness 通用模板</b>，不绑某个具体员工；到工作间里再指定给谁用。）</p>' +
    '<div id="regTplList" class="dock-sug"></div>' +
    '<p class="sub" id="regTplBound">当前未绑定</p>' +
    '</div>' +

    /* ② 框选 */
    '<div class="dk-step">' +
    '<div class="dk-step-h"><span class="no">②</span>框选<span class="sub">一张图上框四块：会话列表 / 新建会话 / 输入框 / 发送按钮（别过分超出目标本身）</span></div>' +
    '<div class="dock-form-actions">' +
    '<button class="btn mini" id="regTpl-shot" onclick="regTplShot()">读取剪贴板截图</button>' +
    '<label class="btn mini quiet dk-filebtn">上传图片<input type="file" accept="image/*" hidden onchange="regTplUpload(this)"></label>' +
    '<button class="btn mini quiet" onclick="regTplReload()">重新读取截图</button>' +
    '<button class="btn mini quiet" onclick="regTplReset()">重新框选</button>' +
    '<span class="dock-msg" id="regTpl-img-msg"></span>' +
    '</div>' +
    '<div class="dock-form-actions">' +
    '<button class="btn mini quiet" data-box-mode="sessions" onclick="dockBoxMode(\'regTpl\',\'sessions\')">框会话列表</button>' +
    '<button class="btn mini quiet" data-box-mode="new_session" onclick="dockBoxMode(\'regTpl\',\'new_session\')">框新建会话</button>' +
    '<button class="btn mini quiet" data-box-mode="input" onclick="dockBoxMode(\'regTpl\',\'input\')">框输入框</button>' +
    '<button class="btn mini quiet" data-box-mode="send" onclick="dockBoxMode(\'regTpl\',\'send\')">框发送按钮</button>' +
    '<span class="dock-msg" id="regTpl-box-msg"></span>' +
    '</div>' +
    '<div id="regTpl-box-canvas" class="dk-boxwrap" hidden>' +
    '<img id="regTpl-box-img" alt="目标窗口截图" draggable="false">' +
    '<div class="dk-boxrect" id="regTpl-box-r-sessions" hidden><span>会话列表</span></div>' +
    '<div class="dk-boxrect" id="regTpl-box-r-new_session" hidden><span>新建会话</span></div>' +
    '<div class="dk-boxrect" id="regTpl-box-r-input" hidden><span>输入框</span></div>' +
    '<div class="dk-boxrect send" id="regTpl-box-r-send" hidden><span>发送</span></div>' +
    '</div>' +
    '<div class="dock-form"><label>输入框里的引导词（可选，逗号分隔）' +
    '<input id="regTplKw" placeholder="例如：在这里输入, 发消息"></label></div>' +
    '<div class="dock-form"><label>激活话术（固定那一句；工作间可覆盖）' +
    '<input id="regTplMsg" placeholder="缺省用平台默认话术"></label></div>' +
    '<div id="regTplBoxes"><p class="sub">还没框：会话列表 / 新建会话 / 输入框 / 发送按钮</p></div>' +
    '<p class="sub" id="regTplCurHint"><b>请先把目标会话打开</b> —— 平台只往<b>当前会话</b>送通知' +
    '（不做会话选择：闭源应用同时只接一个会话，切哪个会话由你决定）。</p>' +
    '</div>' +

    /* ③ 测试 */
    '<div class="dk-step">' +
    '<div class="dk-step-h"><span class="no">③</span>测试<span class="sub">两个都通过才允许保存</span></div>' +
    '<div class="dock-form-actions">' +
    '<button class="btn mini" onclick="regTplTestNew()">测试新建会话</button>' +
    '<button class="btn mini" onclick="regTplTestSend()">测试发送消息（你好）</button>' +
    '<label class="dk-inline"><input type="checkbox" id="regTplNewOnTest" checked> 连「新建会话」一起测</label>' +
    '<label class="dk-inline"><input type="checkbox" id="regTplHuman" onchange="regTplHumanConfirm(this.checked)"> 我看到它回话了</label>' +
    '</div>' +
    '<div id="regTplTestOut"><p class="sub">还没测。</p></div>' +
    '</div>' +

    /* ④ 保存 */
    '<div class="dk-step">' +
    '<div class="dk-step-h"><span class="no">④</span>保存<span class="sub">两个测试都过了才能存</span></div>' +
    '<div class="dock-form"><label>这套脚本属于哪位 harness' +
    '<select id="dkHid"><option value="">先选是哪位员工…</option></select></label></div>' +
    '<div class="dock-form-actions">' +
    '<button class="btn primary" id="regTplSave" onclick="regTplSave()" disabled>保存为该 Harness 的脚本激活方式</button>' +
    '<span class="dock-msg" id="regTplSaveMsg"></span>' +
    '</div>' +
    '<div id="regTplSaveOut"></div>' +
    '</div>' +
    '</div>';
  wrap.dataset.mounted = '1';
  _boxBind('regTpl');
  /* 总览可能还没拉过（它本来是送话面板懒加载的）——这里补一次，再回填并默认选第一位 */
  (async function () {
    try { if (!DOCK_LAST_OVERVIEW) await dockLoad(); } catch (e) { /* 拉不到就保持空，按钮会提示 */ }
    dockScriptFillEmployees();
    const sel = document.getElementById('dkHid');
    if (sel && !sel.value && sel.options && sel.options.length > 1) sel.selectedIndex = 1;
  })();
}

/** ① 模糊名 → 平台探测到的真实窗口（下拉，点一条即绑定） */
/** 两个标题的相似度（0~1）：先看包含关系，再按"公共二元组"算。
 *  为什么自己算：后端 locate 目前是"包含"匹配；这里再排一次，让"像的排前面"，
 *  即使将来后端改了排序，界面口径也不变。 */
function _regTplSim(query, title){
  const q = String(query || '').toLowerCase().trim();
  const t = String(title || '').toLowerCase();
  if (!q || !t) return 0;
  if (t === q) return 1;
  if (t.indexOf(q) >= 0) return 0.92;                       /* 含关键字：很像 */
  const bigrams = function (s){
    const out = {};
    for (let i = 0; i < s.length - 1; i++){ const g = s.slice(i, i + 2); out[g] = (out[g] || 0) + 1; }
    return out;
  };
  const a = bigrams(q), b = bigrams(t);
  let inter = 0, total = 0;
  Object.keys(a).forEach(function (g){ inter += Math.min(a[g], b[g] || 0); total += a[g]; });
  Object.keys(b).forEach(function (g){ total += b[g]; });
  const dice = total ? (2 * inter / total) : 0;
  /* 字符多重集：拼错顺序（trea / trae）时也算像 —— 用户想不起准确拼写是常事 */
  const chars = function (x){ const o = {}; for (const ch of x){ if (ch !== ' ') o[ch] = (o[ch] || 0) + 1; } return o; };
  const ca = chars(q), cb = chars(t);
  let ci = 0, ct = 0;
  Object.keys(ca).forEach(function (ch){ ci += Math.min(ca[ch], cb[ch] || 0); ct += ca[ch]; });
  Object.keys(cb).forEach(function (ch){ ct += cb[ch]; });
  const multi = ct ? (2 * ci / ct) : 0;
  /* 再给"前缀/后缀相同"一点加成（用户常只记得开头或结尾） */
  let bonus = 0;
  const n = Math.min(q.length, t.length);
  for (let i = 0; i < n; i++){ if (q[i] !== t[i]) break; bonus = (i + 1) / Math.max(q.length, 4) * 0.25; }
  return Math.min(1, Math.max(dice * 0.9, multi) + bonus);
}
/** 按相似度排序（保留后端顺序作为同分次序），并带上 sim 字段 */
function _regTplRank(cands, query){
  return cands.map(function (c, i){
    return Object.assign({}, c, { sim: _regTplSim(query, c.title), _i: i });
  }).sort(function (a, b){ return (b.sim - a.sim) || (a._i - b._i); });
}

/** 真·相似度要靠后端（现在 locate 是"标题包含"）；在那之前，整串搜不到就用**前缀**再搜一次，
 *  并在列表上方说清"是按哪个字找到的" —— 不静默、不假装是相似度。 */
async function _regTplLocate(kw){
  const call = async function (q){
    const r = await dockPost(DOCK_PATH.locate, {keywords: [q], limit: 20, fuzzy: true});
    return { ok: r.ok, status: r.status, data: r.data || {}, cands: (r.data && r.data.candidates) || [] };
  };
  const first = await call(kw);
  if (!first.ok || first.cands.length || kw.length <= 2) return first;
  const tries = [];
  for (let n = Math.min(3, kw.length - 1); n >= 2; n--){
    const q = kw.slice(0, n);
    if (tries.indexOf(q) >= 0) continue;
    tries.push(q);
    const r = await call(q);
    if (r.ok && r.cands.length){
      r.data = r.data || {};
      r.data.note = '「' + kw + '」没有被任何窗口标题完整包含；下面是按「' + q + '」找到的（相似程度从上到下）';
      return r;
    }
  }
  first.tries = tries;
  return first;
}

async function regTplSearch(){
  const q = (document.getElementById('regTplQ') || {}).value || '';
  const list = document.getElementById('regTplList');
  const kw = String(q).trim();
  if (!kw){ if (list) list.innerHTML = '<p class="sub">先填一个会出现在窗口标题里的字。</p>'; return; }
  const cacheKey = kw.toLowerCase();
  if (REGTPL.cache && REGTPL.cache[cacheKey]){
    const cached = REGTPL.cache[cacheKey];
    renderRegTplList(list, cached, kw);
    return;
  }
  if (list) list.innerHTML = '<p class="sub">正在找…（平台会把窗口挨个看一遍，通常 1~2 秒；' +
    '如果没有任何标题包含你填的字，会再按前缀找一次，可能多等一两秒）</p>';
  const found = await _regTplLocate(kw);
  REGTPL.cache = REGTPL.cache || {};
  REGTPL.cache[cacheKey] = found;
  const res = found, d = found.data || {};
  const cands = _regTplRank(found.cands || [], kw);
  if (!res.ok || d.error){ if (list) list.innerHTML = '<div class="dock-err">✗ ' + escPlain(d.error || ('HTTP ' + res.status)) + '</div>'; return; }
  REGTPL.pickable = cands;
  renderRegTplList(list, found, kw);
  return cands;
}
/** 列表渲染（缓存命中与正常搜索共用一处） */
function renderRegTplList(list, found, kw){
  const d = (found && found.data) || {};
  const cands = _regTplRank((found && found.cands) || [], kw);
  if (!list) return;
  if (!cands.length){
    if (list) list.innerHTML = '<p class="sub">没找到。平台扫了 <b>' + esc(String(d.total_windows || 0)) + '</b> 个窗口，' +
      '按「' + esc(kw) + '」' + ((found.tries && found.tries.length) ? ('、以及「' + found.tries.map(esc).join('」「') + '」') : '') +
      '都没命中 —— 换个更短的字，或只填开头两三个字母。</p>';
    return;
  }
  list.innerHTML = (d.note ? ('<p class="sub">' + esc(d.note) + '</p>') : '') + cands.map(function (c, i) {
    return '<button type="button" class="dock-cand" style="width:100%;text-align:left" onclick="regTplPick(' + i + ')">' +
      (c.usable === false ? ('<span class="cand-bad">' + esc(c.minimized ? '已最小化' : '截不了') + '</span> ') : '') +
      '<b>' + esc(c.title || '（无标题）') + '</b>' +
      (i === 0 ? '<span class="sub" style="float:right">最相近</span>' : '') + '<br>' +
      '<span class="sub">窗口类 <code>' + esc(c.class || '—') + '</code> · 客户区 <code>' +
      esc(String(c.client_w || c.width || '?')) + '×' + esc(String(c.client_h || c.height || '?')) + '</code></span></button>';
  }).join('');
}

/** 点一条 ⇒ 真实标题回填 + 当场绑定（点错就清空重输，重新确定、重新下拉） */
async function regTplPick(i){
  const c = REGTPL.pickable[i];
  if (!c) return;
  REGTPL.title = c.title || '';
  REGTPL.hwnd = c.hwnd || '';
  REGTPL.cw = Number(c.client_w || c.width || 0);
  REGTPL.ch = Number(c.client_h || c.height || 0);
  REGTPL.usable = c.usable !== false;
  REGTPL.why = c.minimized ? '这个窗口最小化了' : (c.usable === false ? '这个窗口现在截不了' : '');
  const qEl = document.getElementById('regTplQ');
  if (qEl) qEl.value = REGTPL.title;
  const list = document.getElementById('regTplList');
  if (list) list.innerHTML = '';
  const bound = document.getElementById('regTplBound');
  if (bound) bound.textContent = '当前已绑定：' + REGTPL.title + (REGTPL.usable ? '' : ('（' + REGTPL.why + '）'));
  const st = _boxState('regTpl');
  st.hwnd = REGTPL.hwnd; st.title = REGTPL.title;
  _boxApplyClient('regTpl', REGTPL.cw, REGTPL.ch, c);
  const shot = document.getElementById('regTpl-shot');
  if (shot) shot.disabled = (REGTPL.usable === false);
  regTplRefreshBoxes();
}

/** 把已框的四块列出来（缺哪块一眼看出） */
function regTplRefreshBoxes(){
  const box = document.getElementById('regTplBoxes');
  if (!box) return;
  const st = _boxState('regTpl');
  const one = function (b){ return b ? ('(' + Number(b.x).toFixed(3) + ',' + Number(b.y).toFixed(3) + ',' +
    Number(b.w).toFixed(3) + '×' + Number(b.h).toFixed(3) + ')') : '—'; };
  box.innerHTML = '<p class="sub">已框：会话列表 <code>' + one(st.box.sessions) + '</code>　新建会话 <code>' + one(st.box.new_session) +
    '</code>　输入框 <code>' + one(st.box.input) + '</code>　发送按钮 <code>' + one(st.box.send) + '</code></p>';
  regTplRefreshSave();
}

function regTplSaveGate(){
  const st = _boxState('regTpl');
  const need = ['sessions', 'new_session', 'input', 'send'];
  const missing = need.filter(function (k) { return !st.box[k]; });
  const okNew = REGTPL.tests.new_session === true;
  const okSend = REGTPL.tests.send === true || REGTPL.humanSend;
  return { missing: missing, okNew: okNew, okSend: okSend, ok: !missing.length && okNew && okSend };
}
function regTplRefreshSave(){
  const g = regTplSaveGate();
  const btn = document.getElementById('regTplSave');
  if (btn) btn.disabled = !g.ok;
  const msg = document.getElementById('regTplSaveMsg');
  if (msg && !g.ok){
    msg.textContent = g.missing.length ? ('还差：' + g.missing.map(function (k){
      return ({sessions:'会话列表', new_session:'新建会话', input:'输入框', send:'发送按钮'})[k];
    }).join('、')) : (g.okNew ? '' : '新建会话还没测过') + (g.okNew && !g.okSend ? '发送还没测过' : '');
  }
}

/** 图源：剪贴板 / 上传 / 重新读取 —— 都复用送话面板那套画布逻辑（一处真相） */
async function regTplLoadImage(prefix){
  _boxRememberWindow(prefix);
  const st = _boxState(prefix);
  if (!st.cw && REGTPL.cw) _boxApplyClient(prefix, REGTPL.cw, REGTPL.ch, {usable: REGTPL.usable, minimized: REGTPL.usable === false});
  if (!REGTPL.hwnd && !REGTPL.title){ _msgBox('regTpl-img-msg', '先在①里选一个窗口', true); return; }
  _msgBox('regTpl-img-msg', '正在取图…');
  try {
    const r = await fetch('/api/docking/clipboard-image?t=' + Date.now());
    const bl = await r.blob();
    if (!r.ok){
      const d = JSON.parse(await bl.text().catch(function(){ return '{}'; }));
      _msgBox('regTpl-img-msg', '✗ ' + (d.error || ('HTTP ' + r.status)) + ' —— 先自己截一张（Win+Shift+S），或点「上传图片」', true);
      return;
    }
    const url = URL.createObjectURL(bl);
    _boxUseImage(prefix, url, '剪贴板里的图');
    _msgBox('regTpl-img-msg', '已读入剪贴板里的图');
  } catch (e) {
    _msgBox('regTpl-img-msg', '✗ 取图失败：' + (e && e.message ? e.message : e), true);
  }
}
async function regTplShot(){ await regTplLoadImage('regTpl'); regTplRefreshBoxes(); }
async function regTplReload(){ await regTplLoadImage('regTpl'); regTplRefreshBoxes(); }
function regTplUpload(input){
  const f = input && input.files && input.files[0];
  if (!f){ _msgBox('regTpl-img-msg', '没选到文件', true); return; }
  const st = _boxState('regTpl');
  if (!st.cw && REGTPL.cw) _boxApplyClient('regTpl', REGTPL.cw, REGTPL.ch, {usable: REGTPL.usable});
  const rd = new FileReader();
  rd.onload = function () { _boxUseImage('regTpl', String(rd.result || ''), '上传的图：' + f.name); regTplRefreshBoxes(); };
  rd.onerror = function () { _msgBox('regTpl-img-msg', '✗ 这个文件读不出来', true); };
  rd.readAsDataURL(f);
}
/** 只清框、图不动 */
function regTplReset(){
  const st = _boxState('regTpl');
  st.box = { sessions: null, new_session: null, input: null, send: null };
  ['sessions','new_session','input','send'].forEach(function (k){
    const el = document.getElementById('regTpl-box-r-' + k);
    if (el) el.hidden = true;
  });
  REGTPL.tests = { new_session: null, send: null };
  regTplRefreshBoxes();
  const out = document.getElementById('regTplTestOut');
  if (out) out.innerHTML = '<p class="sub">框已清空，请重新框。</p>';
}
function _msgBox(id, text, bad){
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text || '';
  el.style.color = bad ? 'var(--red,#FF3526)' : '';
}

/** ③ 测试新建会话：点「新建会话」框 → 前后比这块画面（后端 visual-probe） */
async function regTplTestNew(){
  const st = _boxState('regTpl');
  const out = document.getElementById('regTplTestOut');
  if (!REGTPL.hwnd && !REGTPL.title){ if (out) out.innerHTML = '<div class="dock-err">✗ 先在①里选一个窗口</div>'; return; }
  if (!st.box.new_session){ if (out) out.innerHTML = '<div class="dock-err">✗ 还没框「新建会话」</div>'; return; }
  if (!st.box.sessions){ if (out) out.innerHTML = '<div class="dock-err">✗ 还没框「会话列表」—— 没它判断不了新建成没成</div>'; return; }
  if (out) out.innerHTML = '<p class="sub">正在点「新建会话」并比画面…</p>';
  const hid = (document.getElementById('dkHid') || {}).value || REGTPL.hwnd || 'template';
  const res = await dockPost(_dockHidPath('/api/docking/harness/{hid}/visual-probe', hid), {
    box: st.box.new_session, sessions_box: st.box.sessions, restore_focus: true,
    hwnd: REGTPL.hwnd ? Number(REGTPL.hwnd) : undefined, title: REGTPL.title || ''});
  const d = res.data || {};
  if (!res.ok || d.error){
    REGTPL.tests.new_session = false;
    if (out) out.innerHTML = '<div class="dock-err">✗ 没测成：' + escPlain(d.error || ('HTTP ' + res.status)) + '</div>' +
      (d.why ? ('<p class="sub">' + escPlain(d.why) + '</p>') : '');
    regTplRefreshSave(); return;
  }
  REGTPL.tests.new_session = d.changed === true;
  if (out){
    out.innerHTML = (d.changed === true
      ? '<div class="dock-ok">✓ 新建会话：那块画面变了（变化 ' + (Number(d.ratio || 0) * 100).toFixed(2) + '%）</div>'
      : '<div class="dock-warn">⚠ 新建会话：那块画面没看出变化（' + (d.why ? escPlain(d.why) : '可能没点进去') + '）</div>') +
      '<p class="sub">' + (d.clicked ? '点到了框的正中' : '没点到') +
      (d.focus_restored === false ? '　·　收尾没能把前台还回去' : '　·　收尾已把前台还回去') + '</p>';
  }
  regTplRefreshSave();
}

/** ③ 测试发送：真送一句「你好（请直接回复你好）」 */
async function regTplTestSend(){
  const st = _boxState('regTpl');
  const out = document.getElementById('regTplTestOut');
  if (!st.box.input){ if (out) out.innerHTML = '<div class="dock-err">✗ 还没框「输入框」</div>'; return; }
  const hid = (document.getElementById('dkHid') || {}).value;
  if (!hid){ if (out) out.innerHTML = '<div class="dock-err">✗ 先在④里选这位 harness</div>'; return; }
  const withNew0 = !!(document.getElementById('regTplNewOnTest') || {}).checked;
  if (out) out.innerHTML = '<p class="sub">正在跑一条链：抬前台 → ' + (withNew0 ? '新建会话 → ' : '') +
    '点输入框 → 粘一句 → 发送 → 还前台…</p>';
  /* ★老板 2026-10-24：「新建会话之后就没办法发送通知了」⇒ 一次请求把整条链跑完（后端 visual-run） */
  const res = await dockPost(_dockHidPath('/api/docking/harness/{hid}/visual-run', hid), {
    text: '你好（请直接回复你好）', enter: true, new_session: withNew0, restore_focus: true});
  const d = res.data || {};
  if (!res.ok || d.error){
    REGTPL.tests.send = false;
    if (out) out.innerHTML = '<div class="dock-err">✗ 没跑成：' + escPlain(d.error || ('HTTP ' + res.status)) + '</div>';
    regTplRefreshSave(); return;
  }
  const steps = d.steps || [];
  const allOk = steps.length > 0 && steps.every(function (x){ return x.ok === true; });
  const pasted = steps.some(function (x){ return x.pasted === true; });
  REGTPL.tests.send = allOk && (d.ok !== false);
  if (out){
    out.innerHTML = (REGTPL.tests.send
      ? '<div class="dock-ok">✓ 整条链跑通了：新建会话 → 粘进去 → 发送</div>'
      : '<div class="dock-warn">⚠ 没跑通，停在：' + escPlain((steps.filter(function (x){ return !x.ok; })[0] || {}).name || '某一步') + '</div>') +
      '<ul class="sub" style="margin:6px 0 0;padding-left:18px">' + steps.map(function (x){
        return '<li>' + (x.ok ? '✓' : '✗') + ' ' + escPlain(x.name) +
          (x.pasted === true ? '（粘进去了' + (typeof x.ratio === 'number' ? '，画面变化 ' + (x.ratio * 100).toFixed(2) + '%' : '') + '）' : '') +
          (x.why ? '：' + escPlain(x.why) : '') + '</li>';
      }).join('') + '</ul>' +
      '<p class="sub">' + (pasted ? '' : '（没看到粘贴成功的痕迹）') +
      (d.state ? '　状态：' + escPlain(d.state) : '') +
      (d.focus_restored === false ? '　·　收尾没能把前台还回去' : '　·　收尾已把前台还回去') +
      '　·　看不到回话就勾上面「我看到它回话了」</p>';
  }
  regTplRefreshSave();
}
function regTplHumanConfirm(v){
  REGTPL.humanSend = !!v;
  regTplRefreshSave();
}

/** ④ 保存：四块框 + 引导词 + 固定话术 + 测试结果 + hwnd/title（保存即绑定） */
async function regTplSave(){
  const st = _boxState('regTpl');
  const g = regTplSaveGate();
  const out = document.getElementById('regTplSaveOut');
  if (!g.ok){ if (out) out.innerHTML = '<div class="dock-warn">⚠ 还不满足保存条件（见上面）</div>'; return; }
  const hid = (document.getElementById('dkHid') || {}).value;
  if (!hid){ if (out) out.innerHTML = '<div class="dock-err">✗ 先选这位 Harness（下面的员工下拉）</div>'; return; }
  const kwEl = document.getElementById('regTplKw');
  const msgEl = document.getElementById('regTplMsg');
  const body = {
    sessions: st.box.sessions, new_session: st.box.new_session,
    input: st.box.input, send: st.box.send,
    keywords: ((kwEl && kwEl.value) || '').split(/[,，]/).map(function (x){ return x.trim(); }).filter(Boolean),
    frame: (st.cw && st.ch) ? {w: st.cw, h: st.ch} : undefined,
    tests: { new_session: {changed: REGTPL.tests.new_session === true},
             send: {paste_seen: REGTPL.tests.send === true, press_ok: REGTPL.tests.send === true,
                    human: REGTPL.humanSend || undefined} },
    hwnd: REGTPL.hwnd ? Number(REGTPL.hwnd) : undefined,
    title: REGTPL.title || ''
  };
  const m = ((msgEl && msgEl.value) || '').trim();
  if (m) body.activation_message = m;
  if (out) out.innerHTML = '<p class="sub">正在保存…</p>';
  const res = await dockPost(_dockHidPath('/api/docking/harness/{hid}/regions', hid), body);
  const d = res.data || {};
  if (!res.ok || d.error){
    if (out) out.innerHTML = '<div class="dock-err">✗ ' + escPlain(d.error || ('HTTP ' + res.status)) + '</div>';
    return;
  }
  const r = d.regions || {};
  const one = function (b){ return b ? ('(' + Number(b.x).toFixed(3) + ',' + Number(b.y).toFixed(3) + ',' +
    Number(b.w).toFixed(3) + '×' + Number(b.h).toFixed(3) + ')') : '—'; };
  if (out){
    out.innerHTML = '<div class="dock-ok">✓ 已保存为该 Harness 的脚本激活方式' +
      (d.bind ? '（窗口已绑定）' : '') + '：会话列表 ' + one(r.sessions) + '　新建会话 ' + one(r.new_session) +
      '　输入框 ' + one(r.input) + '　发送 ' + one(r.send) + '</div>' +
      (r.activation_message ? ('<p class="sub">激活话术：' + escPlain(r.activation_message) + '</p>') : '');
  }
}

function dockScriptFillEmployees(){
  const sel = document.getElementById('dkHid');
  if (!sel) return;
  const hs = (DOCK_LAST_OVERVIEW && DOCK_LAST_OVERVIEW.harnesses) || [];
  const cur = sel.value || DOCK_SCRIPT.hid || '';
  sel.innerHTML = '<option value="">' + (DOCK_GLOBAL ? '先选是哪位 harness…' : '先选是哪位员工…') + '</option>' +
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
  if (!hid){ out.innerHTML = '<p class="sub">先选是哪位' + (DOCK_GLOBAL ? ' harness' : '员工') + ' —— 候选是从它的档案和线索里推的。</p>'; return; }
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
      '" data-hwnd="' + esc(String(c.hwnd || '')) + '"' +
      ' data-usable="' + (c.usable === false ? '0' : '1') + '"' +
      ' data-why="' + esc(c.minimized ? '这个窗口最小化了' : (c.usable === false ? '这个窗口现在截不了（太小或被遮挡）' : '')) + '"' +
      ' data-cw="' + esc(String(c.client_w || c.width || '')) + '"' +
      ' data-ch="' + esc(String(c.client_h || c.height || '')) + '"' +
      ' onclick="dockPickCand(this)" onkeydown="if(event.key===\'Enter\'){event.preventDefault();dockPickCand(this);}">' +
      '<span class="cand-pick">点选</span><b>' + esc(c.title || '（无标题）') + '</b>' +
      (c.hwnd ? ('<button class="btn mini quiet cand-bind" onclick="event.stopPropagation();dockBindCand(\'' +
        esc(String(c.hwnd)) + '\',\'' + esc(String(c.title || '').replace(/'/g, '')) + '\')">绑定到这个员工</button>') : '') + '<br>' +
      '<span class="sub">窗口类 <code>' + esc(c.class || '—') + '</code> · pid <code>' + esc(c.pid || '—') +
      '</code> · 匹配分 <code>' + esc(String(c.score != null ? c.score : '—')) + '</code>' +
      ((c.hits && c.hits.length) ? (' · 命中 <code>' + esc(c.hits.join('、')) + '</code>') : '') +
      '</span></div>').join('');
}

function dockPickCand(el){
  document.querySelectorAll('.dock-cand.sel').forEach(x => x.classList.remove('sel'));
  el.classList.add('sel');
  DOCK_SCRIPT.picked = el.getAttribute('data-title') || '';
  DOCK_SCRIPT.pickedInfo = {
    hwnd: el.getAttribute('data-hwnd') || '',
    usable: el.getAttribute('data-usable') === '1',
    why: el.getAttribute('data-why') || '',
    client_w: Number(el.getAttribute('data-cw') || 0),
    client_h: Number(el.getAttribute('data-ch') || 0)
  };
  const out = document.getElementById('dkPickMsg');
  if (out) out.innerHTML = '<p class="sub">已选：<b>' + esc(DOCK_SCRIPT.picked) +
    '</b> —— 下一步点「去框选输入框（推荐）」，把要写字的那一行框出来。</p>';
}

/** 屏3：确认 + 试投（必须用户点；三态如实：送出去 ≠ 送到了）。 */
async function dockTryAct(){
  const out = document.getElementById('dkTryOut');
  const msg = document.getElementById('dkTryMsg');
  if (!out) return;
  if (!DOCK_SCRIPT.picked){ if (msg) msg.textContent = '先在上面选中一个候选窗口'; return; }
  if (msg) msg.textContent = '试投中…（往那个窗口送一句探针文本并读回）';
  out.innerHTML = '<p class="sub">试投中…</p>';
  /* ★D后端-14：带 `harness_id` —— 后端才拿得到这位员工"教过的位置"，
     试投就会**先点输入框再粘贴**（否则 Electron 里窗口在前台也落空）。 */
  const res = await dockPost(DOCK_PATH.tryActivate,
    {title: DOCK_SCRIPT.picked, keywords: DOCK_SCRIPT.kws,
     harness_id: (DOCK_SCRIPT.hid || undefined)});
  const d = res.data || {};
  if (msg) msg.textContent = '';
  if (!res.ok || d.error){
    out.innerHTML = '<div class="dock-err">✗ ' + esc(d.error || ('HTTP ' + (res.status || '?'))) + '</div>' +
      (d.next_step ? ('<p class="lead">下一步：' + esc(d.next_step) + '</p>') : '') +
      '<p class="sub">按上面的原因改完再点一次；也可以回屏1放宽关键词。</p>';
    return;
  }
  DOCK_SCRIPT.lastTry = d;
  /* ★FE-30（D后端-7 §2）：`via === "harness"` ⇒ 这次是**按员工**走的通用通道，标题别再写"投给这个窗口"；
     `channel` 优先显示（cdp/native/uia/clipboard），`attempts` 收进折叠区，每档都带原因。 */
  const ch = d.channel || '';
  const chTxt = ch ? ('　通道 ' + dockChannelChip(ch)) : '';
  /* ★D后端-10：试投也读 state（四态互斥）；没有 state 才用下面这套旧口径。 */
  const stHtml = dockStateHtml(d, chTxt + (d.via === 'harness' ? '　按员工投递' : ''));
  if (stHtml){
    out.innerHTML = stHtml + _dkAnchorUsedHtml(d) + _dkMinimizedHint(d) + dockTextControlHtml(d) +
      (d.detail ? ('<p class="sub">' + escPlain(d.detail) + '</p>') : '') +
      (d.next_step ? ('<p class="lead">下一步：' + escPlain(d.next_step) + '</p>') : '') +
      ((d.resolved && d.resolved.source)
        ? ('<p class="sub">目标来源：<b>' + esc(d.resolved.source) + '</b></p>') : '') +
      dockAttemptsHtml(d) +
      (d.state === 'delivered_verified' ? '<p class="lead">这条路能通 —— 到屏4把脚本存进它的档案。</p>' : '');
    return;
  }
  const byHarness = d.via === 'harness';
  const tgt = (d.resolved && d.resolved.title) ? ('「' + esc(d.resolved.title) + '」') : '';
  // ★三态如实：verified === true 才是「已自证」；其余一律「⚠ 未核实」（送出去 ≠ 送到了）
  if (d.verified === true){
    out.innerHTML = '<div class="dock-ok">✓ 已自证：' +
      (byHarness
        ? ('按员工投递 —— 平台找到 ' + (tgt || '目标') + ' 并投递成功，读回一致')
        : '探针文本送进了那个窗口，且读回一致') + chTxt + '。</div>' +
      _dkAnchorUsedHtml(d) + _dkMinimizedHint(d) +
      (byHarness && d.resolved && d.resolved.source
        ? ('<p class="sub">目标来源：<b>' + esc(d.resolved.source) + '</b></p>') : '') +
      dockAttemptsHtml(d) +
      '<p class="lead">这条路能通 —— 到屏4把脚本存进它的档案。</p>';
  } else {
    /* ★D后端-2 §2 的文案纪律（2026-10-23）：
       ① `ok=false` / `need_human=true` / detail 含「没有投递」 ⇒ 这是**根本没投出去**（窗口没置顶 / 剪贴板写不进），
          不许写成"送出去了"；
       ② `verified=null` ⇒ 只是"送出了输入"，按实际走的那一档说人话（剪贴板档＝已按 Ctrl+V，表层＝点到位置并键入）。 */
    const noDelivery = (d.ok === false) || (d.need_human === true) || /没有投递/.test(String(d.detail || ''));
    const howDone = d.method === 'clipboard' ? '已放进剪贴板并按了 Ctrl+V'
      : d.method === 'surface' ? '已点到教过的位置并逐字键入'
        : d.method === 'wmpaste' ? '已按消息式注入送出' : '已送出输入';
    if (noDelivery){
      out.innerHTML = '<div class="dock-err">✗ 没有投递：' + escPlain(d.detail || d.error || '平台没说原因') + '</div>' +
        (d.next_step ? ('<p class="lead">下一步：' + escPlain(d.next_step) + '</p>') : '') +
        dockAttemptsHtml(d);
    } else if (d.verified === false){
      out.innerHTML = '<div class="dock-warn">⚠ 未核实：读回不一致（投递没成）' +
        (byHarness ? '　·　按员工投递' : '') + chTxt + '</div>' +
        (d.detail ? ('<p class="sub">' + escPlain(d.detail) + '</p>') : '') +
        (d.next_step ? ('<p class="lead">下一步：' + escPlain(d.next_step) + '</p>') : '') +
        dockAttemptsHtml(d) +
        '<p class="sub">也可以换一个候选窗口再试一次；都不通就走剪贴板兜底档（试投时选 clipboard）。</p>';
    } else {
      out.innerHTML = '<div class="dock-warn">⚠ 未核实：' + esc(howDone) +
        ' —— 这一路没有可读回的文本框，<b>不能确认对方收到了</b>' +
        (byHarness ? '　·　按员工投递' : '') + chTxt + '</div>' +
        (d.detail ? ('<p class="sub">' + escPlain(d.detail) + '</p>') : '') +
        (d.next_step ? ('<p class="lead">下一步：' + escPlain(d.next_step) + '</p>') : '') +
        dockAttemptsHtml(d);
    }
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
  /* ★D后端-11：`method` 是档案里记的那一档（原样回传），`tier` 是脚本**内部**实际走的那档
     （auto＝先试消息式注入 / clipboard＝只走剪贴板半自动）。两个都说，别混成一个"方式"。 */
  const out = document.getElementById('dkSaveOut');
  if (out){
    const m = script.method || 'auto', t = script.tier || (m === 'clipboard' ? 'clipboard' : 'auto');
    out.innerHTML = '<p class="sub">这份正文的档位：档案记的 <code>' + escPlain(m) + '</code>　' +
      '脚本内部实际 <code>' + escPlain(t) + '</code>' +
      (t === 'clipboard' ? '（只走剪贴板半自动：一定会说"未核实"）' : '（先试消息式注入，读得回就算核实）') +
      '。上次试投：' + (script.verified === true ? '✓ 已自证' : '⚠ 未核实（别当成已验证）') + '</p>';
  }
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
  /* ★FE-30（D后端-7 §2）：优先显示 `channel`（四通道里这次实际走的那条），`attempts` 折叠。 */
  const ch = d.channel || used.channel || '';
  const chTxt = ch ? ('　通道 ' + dockChannelChip(ch)) : '';
  /* ★D后端-10：一键激活的结论同样只读 state（它与脚本正文是两件事：
     正文＝在你自己的会话里跑的脚本；这里＝**平台按档案里的标题/关键词走统一投递层**送一次）。 */
  const tierTxt = used.method ? ('　档案档位 ' + escPlain(used.method)) : '';
  const head = dockStateHtml(d, chTxt + tierTxt) ||
    (d.verified === true
      ? '<div class="dock-ok">✓ 已自证：脚本按档案里的参数跑通，读回一致' + chTxt + tierTxt + '。</div>'
      : '<div class="dock-warn">⚠ 未核实' + (d.verified === false ? '（投递没成功）' : '（送出去了，但没法确认读回）') +
        chTxt + tierTxt + '</div>');
  out.innerHTML = head + dockTextControlHtml(d) +
    (d.detail ? ('<p class="sub">' + escPlain(d.detail) + '</p>') : '') +
    (d.next_step ? ('<p class="lead">下一步：' + escPlain(d.next_step) + '</p>') : '') +
    dockAttemptsHtml(d) +
    '<p class="sub">这条走的是<b>平台投递层</b>（按档案里的标题/关键词在四通道里挑）。' +
    '想让脚本在你自己的会话里执行，点「导出 .ps1 副本」自己跑。</p>';
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

/* ══ 送话给员工（FE-30 · V92 统一投递层的前台）════════════════════════
 * 老板口径（2026-10-23）：上层只说"把这段话送给这个员工"——界面**不猜通道、不猜目标**。
 * 四条纪律：
 *   ① 目标与来源照 GET …/delivery 的返回显示（`source` 让用户看出平台有没有认错人）；
 *   ② `verified` 三态如实：true 才是「已自证」；false 是「投递没成功」；
 *      null 是**半自动**（内容在剪贴板，要人到窗口按 Ctrl+V）—— 绝不写「已送达」；
 *   ③ `prefer` 是排障锁档：锁了就锁死，那档失败就如实失败，界面不替它换档；
 *   ④ 两个入口（瓦片 / 档案 ⑦）共用**同一份**渲染与请求，只差"员工能不能选"。
 */
const DELIVER_CH_TEXT = {
  cdp:     '浏览器页面（Chromium/Electron，要目标开了调试端口；能读回）',
  native:  '原生窗口（Win32 文本框；能读回）',
  uia:     'UI Automation（Win11 记事本 / Office 这类；能读回）',
  clipboard: '其它任何窗口（半自动：人按 Ctrl+V，平台不能核实）'
};

/** 后端的中文原因里带 markdown 强调（`**会话名**`）—— 页面显示前把成对的星号去掉，
 *  只去掉成对的那两个字符，内容与语义一点都不改（否则用户看到的是 `**` 这种排版垃圾）。 */
function _dkPlain(s){
  return String(s == null ? '' : s).replace(/\*\*([^*]+)\*\*/g, '$1');
}
function escPlain(s){ return esc(_dkPlain(s)); }

/** `<details>` 的正文容器 —— **绝不直接改 details.innerHTML**：那会把 `<summary>` 一起删掉，
 *  浏览器就把标题退回默认的「详细信息」（2026-10-23 走查在档案 ⑤/⑥ 抓到的既有缺陷）。
 *  `reset=true` 先清空正文，返回可写的容器。 */
function _dkDetailsBody(box, reset){
  if (!box) return null;
  let body = box.querySelector('.dk-det-body');
  if (!body){
    body = document.createElement('div');
    body.className = 'dk-det-body';
    box.appendChild(body);
  }
  if (reset) body.innerHTML = '';
  return body;
}

function dockChannelChip(ch){
  if (!ch) return '';
  const cls = /^[a-z0-9_-]+$/i.test(String(ch)) ? (' ch-' + String(ch)) : '';
  return '<span class="dk-ch' + cls + '">' + esc(ch) + '</span>';
}

/** 路径表里只有 {hid} 的那两条：单独替换（dockPath 只管 {ws}/{mid}）。 */
function _dockHidPath(tpl, hid){
  return String(tpl).replace('{hid}', encodeURIComponent(hid || ''));
}

/** 面板外形（一处真相：瓦片与档案 ⑦ 都调它）。`pickable` 时才渲染员工选择器。
 *  ⚠️ **不在这里再放一个标题**：弹窗头 / 档案 ⑦ 的 summary 已经是这一块的标题，
 *  同一个意思连说两层只会挤掉真正要看的东西（2026-10-23 评审第 7 条）。
 *  选项文案：名字与标识**相同就只写一次**，离线做成后缀而不是名字的一部分 —— 同名员工靠标识分开。 */
function dockDeliverShell(prefix, hid, pickable, hs){
  const optText = h => {
    const name = String(h.harness_name || h.harness_id || '');
    const id = String(h.harness_id || '');
    const short = (s, n) => (s.length > n ? (s.slice(0, n) + '…') : s);
    return short(name, 28) + (name && id && name !== id ? (' · ' + short(id, 24)) : '') +
      (h.online ? '' : '（离线）');
  };
  const opts = (hs || []).map(h => '<option value="' + esc(h.harness_id) + '"' +
    (h.harness_id === hid ? ' selected' : '') + '>' + esc(optText(h)) + '</option>').join('');
  return '<div class="card dk-deliver">' +
    '<p class="lead">你只说<b>送什么、送给谁</b>；能读回核对的才说「已自证」。</p>' +
    /* ── 框选输入框（D后端-17/18 · 老板要的"作业帮式框题"）：现在是**主路径** ── */
    '<div class="dk-boxsel">' +
    '<div class="dk-alt-head">框选输入框 <span class="sub">截一张图 → 拖出「输入框」和（可选）「发送按钮」→ 保存；之后送话优先用它</span></div>' +
    '<div class="dock-form-actions">' +
    '<button class="btn mini" id="' + prefix + '-box-shot" onclick="dockBoxShot(\'' + prefix + '\')">截一张图</button>' +
    '<button class="btn mini quiet" onclick="dockBoxPaste(\'' + prefix + '\')">用剪贴板里的图</button>' +
    '<label class="btn mini quiet dk-filebtn">上传图片<input type="file" accept="image/*" hidden onchange="dockBoxUpload(\'' + prefix + '\', this)"></label>' +
    '<span class="dock-msg" id="' + prefix + '-box-msg"></span>' +
    '</div>' +
    '<div class="dock-form-actions">' +
    '<button class="btn mini quiet" data-box-mode="input" onclick="dockBoxMode(\'' + prefix + '\',\'input\')">框输入框</button>' +
    '<button class="btn mini quiet" data-box-mode="send" onclick="dockBoxMode(\'' + prefix + '\',\'send\')">框发送按钮</button>' +
    '<button class="btn mini" onclick="dockBoxSave(\'' + prefix + '\')">保存框选</button>' +
    '<button class="btn mini quiet" onclick="dockBindWindow(\'' + prefix + '\')">把这个窗口绑给当前员工</button>' +
    '<button class="btn mini quiet" onclick="dockBoxClear(\'' + prefix + '\')">清掉</button>' +
    '</div>' +
    '<p class="sub">自己截图请<b>截整扇窗</b>（含输入框那一整块）—— 只截局部会对不上位置。' +
    '框的时候<b>框到能写字的那一行最稳</b>（上面那行提示/按钮排别框进去）。</p>' +
    '<div id="' + prefix + '-box-status" class="dk-anchor"><p class="sub">正在读已框的区域…</p></div>' +
    '<div id="' + prefix + '-box-canvas" class="dk-boxwrap" hidden>' +
    '<img id="' + prefix + '-box-img" alt="目标窗口截图" draggable="false">' +
    '<div class="dk-boxrect" id="' + prefix + '-box-r-input" hidden><span>输入框</span></div>' +
    '<div class="dk-boxrect send" id="' + prefix + '-box-r-send" hidden><span>发送</span></div>' +
    '</div>' +
    '<div class="dock-form">' +
    '<label>引导词（可选：输入框里的提示字，逗号分隔）' +
    '<input id="' + prefix + '-box-kw" placeholder="例如：在这里输入, 发消息"></label>' +
    '</div>' +
    '<div class="dock-form-actions">' +
    '<button class="btn mini" onclick="dockBoxTrial(\'' + prefix + '\')">试投一句「请回复数字 7」</button>' +
    '<span class="sub">试投会真送到目标；下面会照实说「像粘进去了 / 看不出粘进去」，以及发没发出去。</span>' +
    '</div>' +
    '<div id="' + prefix + '-box-out"></div>' +
    '</div>' +
    /* ── 左栏：选目标 + 写话（主线，一屏办完） ── */
    '<div class="dk-left">' +
    (pickable
      ? '<div class="dock-deliver-pick"><label>送给哪位员工' +
        '<select id="' + prefix + '-sel" onchange="dockDeliverTarget(\'' + prefix + '\')">' + opts + '</select></label>' +
        '<button class="btn mini quiet" onclick="dockDeliverTarget(\'' + prefix + '\')" title="重新问一次平台：这个员工现在能把话送到哪儿">重新解析</button>' +
        '</div>'
      : '<p class="sub"><button class="btn mini quiet" onclick="dockDeliverTarget(\'' + prefix + '\')" title="重新问一次平台：这个员工现在能把话送到哪儿">重新解析</button></p>') +
    '<div id="' + prefix + '-target" class="dk-target"><p class="sub">正在解析目标…</p></div>' +
    '<div class="dock-form">' +
    '<label>要送的话' +
    '<textarea id="' + prefix + '-text" rows="4" placeholder="要送给它的一段话（会让它干活 / 回话的那种）"' +
    ' onkeydown="if(event.ctrlKey&&event.key===\'Enter\'){event.preventDefault();dockDeliverSend(\'' + prefix + '\');}"></textarea>' +
    '</label>' +
    '<div class="dock-form-actions">' +
    '<select id="' + prefix + '-prefer" style="max-width:210px" onchange="_dockSendLabel(\'' + prefix + '\')" title="锁档只用于排障：锁了就锁死，那档失败会如实失败">' +
    '<option value="">自动选路</option>' +
    '<option value="cdp">锁 cdp</option>' +
    '<option value="native">锁原生</option>' +
    '<option value="uia">锁 UIA</option>' +
    '<option value="clipboard">只用半自动（剪贴板）</option>' +
    '</select>' +
    '<label class="dk-inline"><input type="checkbox" id="' + prefix + '-enter"> 送完回车</label>' +
    '<button class="btn primary" id="' + prefix + '-send" onclick="dockDeliverSend(\'' + prefix + '\')">送话</button>' +
    '<span class="dock-msg" id="' + prefix + '-msg"></span>' +
    '</div>' +
    '<p class="sub" id="' + prefix + '-hint">「半自动」只把内容放进剪贴板，要你到目标窗口点一下再 Ctrl+V —— ' +
    '那一档<b>不能核实</b>，界面不会把它写成成功。按 Ctrl+Enter 也能发。</p>' +
    '</div>' +
    '</div>' +   /* /dk-left */
    /* ── 右栏：通道 → 结果 → 备选路（只读与备选，自己滚，不挤主线） ── */
    '<div class="dk-right">' +
    '<div class="dk-aside-head">状态 · 通道</div>' +
    '<div id="' + prefix + '-chans" class="dk-chans"></div>' +
    '<div id="' + prefix + '-out"><p class="sub">送完的结果会显示在这里（通道 / 是否读回 / 每一档为什么）。</p></div>' +
    '<details class="dk-alt" id="' + prefix + '-altbox"><summary>上面那条路走不通时：表层交互 / 浏览器直连（两个备选）</summary>' +
    /* ── 备选二：浏览器直连（D后端-4 CDP）：不抢焦点、能读回核对 ── */
    '<div class="dk-alt-sec">' +
    '<div class="dk-alt-head">浏览器直连 <span class="sub">Chromium/Electron 开了调试端口时：探页面 → 指认输入框 → 塞文本（不抢焦点、能读回）</span></div>' +
    '<div class="dock-form-actions">' +
    '<button class="btn mini" onclick="dockCdpProbe(\'' + prefix + '\')">① 找页面（调试端口）</button>' +
    '<select id="' + prefix + '-cdp-mode" style="max-width:190px" title="三档自动降级；锁一档用于排障">' +
    '<option value="auto">自动选档</option><option value="insert">锁 insert</option>' +
    '<option value="paste-event">锁 paste 事件</option><option value="clipboard">锁 剪贴板+Ctrl+V</option>' +
    '</select>' +
    '<label class="dk-inline"><input type="checkbox" id="' + prefix + '-cdp-enter"> 送完回车</label>' +
    '<span class="dock-msg" id="' + prefix + '-cdp-msg"></span>' +
    '</div>' +
    '<div id="' + prefix + '-cdp-targets"></div>' +
    '<div id="' + prefix + '-cdp-editors"></div>' +
    '<div class="dock-form-actions"><button class="btn mini" id="' + prefix + '-cdp-send" onclick="dockCdpSend(\'' + prefix + '\')" disabled>③ 投递到选中的输入框</button>' +
    '<span class="dock-msg" id="' + prefix + '-cdp-sel"></span></div>' +
    '<div id="' + prefix + '-cdp-out"></div>' +
    '</div>' +            /* /cdp alt-sec */
    '</details>' +
    '</div>' +            /* /dk-right */
    '</div>';             /* /card */
}

/** 当前要送给谁：选择器 > 调用方给死的 hid > 脚本激活里选中的那位。 */
function dockDeliverHid(prefix, hid){
  const sel = document.getElementById(prefix + '-sel');
  return (sel && sel.value) || hid || (DOCK_SCRIPT && DOCK_SCRIPT.hid) || '';
}

/** 主按钮文案跟着**能核实的那条路**走（评审第 1 条）：只有半自动可用时，按钮写「放到剪贴板」——
 *  因为那一下做的事就是"备好让你粘"，写「送话」等于替平台承诺送达。 */
const _DOCK_CD = {};
function _dockSendLabel(prefix, cd){
  if (cd !== undefined) _DOCK_CD[prefix] = cd;
  const cur = _DOCK_CD[prefix] || null;
  const btn = document.getElementById(prefix + '-send');
  if (!btn) return;
  const prefer = ((document.getElementById(prefix + '-prefer') || {}).value) || '';
  const chans = (cur && cur.channels) || [];
  const canVerified = chans.some(c => c.available && c.channel !== 'clipboard');
  const onlyHalf = chans.length > 0 && !canVerified && chans.some(c => c.available && c.channel === 'clipboard');
  if (prefer === 'clipboard' || (!prefer && onlyHalf)){
    btn.textContent = '放到剪贴板';
    btn.title = '半自动：只把内容放进剪贴板，你到目标窗口按 Ctrl+V —— 平台不能核实';
  } else if (!cur){
    btn.textContent = '试送一次';
    btn.title = '平台还没认出目标，这一次多半会失败；结果会照实说明为什么';
  } else {
    btn.textContent = '送话';
    btn.title = '';
  }
}

/** 目标 + 通道：**全部照后端返回显示**（这是"现在能不能送、送到哪儿"的唯一真相）。 */
async function dockDeliverTarget(prefix, hid){
  const tBox = document.getElementById(prefix + '-target');
  const cBox = document.getElementById(prefix + '-chans');
  if (!tBox) return;
  const hidNow = dockDeliverHid(prefix, hid);
  if (!hidNow){
    tBox.innerHTML = '<p class="sub">先选一位员工。</p>';
    if (cBox) cBox.innerHTML = '';
    return;
  }
  tBox.dataset.hid = hidNow;
  tBox.innerHTML = '<p class="sub">正在解析目标…</p>';
  let d = null;
  try {
    const r = await fetch(_dockHidPath(DOCK_PATH.deliverTarget, hidNow));
    d = await r.json();
    if (!r.ok) throw new Error(d && d.error ? d.error : ('HTTP ' + r.status));
  } catch (e) {
    tBox.innerHTML = '<div class="dock-err">✗ 解析目标失败：' + esc(e && e.message ? e.message : e) + '</div>' +
      '<p class="sub">端点 ' + esc(DOCK_PATH.deliverTarget) + ' 没给出结果 —— 按上面的原因处理后再刷新；可以直接试送，结果会照实说用了哪条。</p>';
    if (cBox) cBox.innerHTML = '';
    return;
  }
  if (d && d.ok === true){
    const src = d.source ? ' · 来源：<b>' + esc(d.source) + '</b>' : '';
    tBox.dataset.title = d.title || '';        /* 表层交互要拿它去定位窗口 */
    tBox.innerHTML = '<div class="dk-target-line"><span class="dk-ok-dot"></span>目标：<b>' +
      esc(d.title || d.ws || '（没给标题）') + '</b>' + src + '</div>' +
      (d.ws ? '<p class="sub">页面目标 <code>' + esc(String(d.ws).slice(0, 96)) + (String(d.ws).length > 96 ? '…' : '') + '</code></p>' : '') +
      (d.hwnd ? '<p class="sub">窗口句柄 <code>' + esc(d.hwnd) + '</code></p>' : '');
  } else {
    tBox.dataset.title = '';
    /* ★评审第 1 条：这里说"还没认出目标 / 现在能做的下一步"，不说得像页面坏了。
       认不出目标时**也不去问通道**（没有目标，通道无从谈起 —— 否则会出现
       "定位不到" 与 "剪贴板 推荐 可用" 并存的矛盾）。 */
    tBox.innerHTML = '<div class="dk-target-line"><span class="dk-ok-dot bad"></span><b>平台还没认出这个员工的目标</b></div>' +
      '<div class="dock-warn">⚠ ' + escPlain((d && d.why) || '平台说不出这个员工的目标在哪') + '</div>' +
      '<p class="lead">下一步：去<b>工作间</b>的成员卡上点「建会话」，会话名写成和它窗口标题一致。</p>' +
      '<p class="sub">浏览器类目标还要让它带着调试端口开着；改完回来点上面的「重新解析」。</p>';
    if (cBox) cBox.innerHTML = '<p class="sub">还没有认出目标，所以谈不上走哪条通道。</p>';
    _dockSendLabel(prefix, null);
    return;
  }
  if (!cBox) return;
  let cd = null;
  try {
    const q = new URLSearchParams();
    if (d && d.hwnd) q.set('hwnd', String(d.hwnd));
    if (d && d.ws) q.set('ws', String(d.ws));
    if (d && d.title) q.set('title', String(d.title));
    const r2 = await fetch('/api/docking/delivery/channels?' + q.toString());
    cd = await r2.json();
  } catch (e) {
    cd = null;
  }
  if (!(cd && cd.channels && cd.channels.length)){
    cBox.innerHTML = '<p class="sub">通道清单取不到（端点 /api/docking/delivery/channels 没给出结果）—— 仍可直接试送，' +
      '结果里会照实说走了哪条、为什么。</p>';
    _dockSendLabel(prefix, null);
    return;
  }
  cBox.innerHTML = '<div class="dk-chans-head">这个目标能用哪条通道</div>' +
    cd.channels.map(c => '<div class="dk-chan ' + (c.available ? 'on' : 'off') + '">' +
      '<span class="dk-chan-name" title="' + esc(DELIVER_CH_TEXT[c.channel] || '后端新通道，界面按原样显示') + '">' +
      esc(c.channel) + '</span>' +
      (c.channel === cd.recommended ? '<span class="cap-src src-probed">推荐</span>' : '') +
      '<span class="sub">' + (c.available ? '可用' : '不可用') +
      (c.why ? ' —— ' + escPlain(c.why) : '') + '</span></div>').join('');
  _dockSendLabel(prefix, cd);
}

/** 每一档试了什么（折叠）—— 送话/一键激活/试投**共用**，失败时每档都必须带原因。 */
/* ══ 结论行唯一口径（★D后端-10）════════════════════════════════
 * 后端在所有投递路径上都给 `state`（四态互斥）与 `text_control`；
 * 界面**只读这两个字段**，不再从 need_human / verified / detail 的文字里推断
 * （那正是"✗ 没有投递"下面又写"已按 Ctrl+V"的矛盾来源）。
 */
const DK_STATE_LINE = {
  delivered_verified: { cls: 'dock-ok',   text: '✓ 已送达（读回一致）' },
  sent_unverified:    { cls: 'dock-warn', text: '⚠ 已送出（未核实 —— 确实送出/按键了，但没法读回确认）' },
  queued_for_human:   { cls: 'dock-warn', text: '… 已放进剪贴板，等你按 Ctrl+V（还没送到）' },
  not_sent:           { cls: 'dock-err',  text: '✗ 没有送出去' }
};
/** 有 state 就按 state 渲染；没有（老后端）返回空串，调用方退回原来的三态逻辑。 */
function dockStateHtml(d, extra){
  const line = DK_STATE_LINE[(d && d.state) || ''];
  if (!line) return '';
  return '<div class="' + line.cls + '">' + line.text + (extra || '') + '</div>';
}
/** "为什么没检测到输入框" —— 直接显示后端给的整句话，不再自拟。 */
function dockTextControlHtml(d){
  const tc = (d && d.text_control) || null;
  if (!tc) return '';
  if (tc.found === true){
    return '<p class="sub">识别到文本框' + (tc.class ? ('（' + esc(String(tc.class)) + '）') : '') +
      ' —— 这一档能读回核对。</p>';
  }
  if (tc.found === false){
    return '<p class="sub">' + escPlain(tc.why || '这个窗口的输入框是应用自己画的，系统层看不到它 —— 不是它没有输入框。') + '</p>';
  }
  return '';   /* null：窗口都没找到，谈不上有没有输入框 —— 别下结论 */
}

function dockAttemptsHtml(d){
  const atts = (d && Array.isArray(d.attempts)) ? d.attempts : [];
  if (!atts.length) return '';
  return '<details><summary>每一档试了什么（' + atts.length + ' 档）</summary><ul class="dock-kv">' +
    atts.map(a => '<li><code>' + esc(a.channel || '?') + '</code>：' + (a.ok ? '成了' : '没成') +
      (a.why ? (' —— ' + escPlain(a.why)) : '') + '</li>').join('') + '</ul></details>';
}

/** 送话结果：三态 + 通道 + attempts 折叠 + 目标来源（**不许**把未核实写成成功）。 */
function dockDeliverResultHtml(d, res){
  d = d || {};
  const ch = d.channel ? dockChannelChip(d.channel) : '';
  const att = dockAttemptsHtml(d);
  const src = (d.resolved && d.resolved.source)
    ? ('<p class="sub">目标来源：<b>' + esc(d.resolved.source) + '</b>（平台"怎么找到它的" —— 认错人时先看这里）</p>')
    : '';
  /* 后端把同一个原因也放在 resolved.why 里 —— 已经在上面的目标行说过了，别再说第二遍。 */
  const dup = (d.resolved && d.resolved.why && d.detail && d.resolved.why === d.detail);
  const det = (d.detail && !dup) ? ('<p class="sub">' + escPlain(d.detail) + '</p>') : '';
  const nx = d.next_step ? ('<p class="lead">下一步：' + escPlain(d.next_step) + '</p>') : '';
  if (!res || res.ok === false || d.error){
    return '<div class="dock-err">✗ ' + escPlain(d.error || d.detail || ('HTTP ' + ((res && res.status) || '?'))) + '</div>' + nx;
  }
  /* ★D后端-10：有 state 就只按 state 说；没有才退回旧三态（老后端/单间模式）。 */
  const tc = dockTextControlHtml(d);
  const head = dockStateHtml(d, ch ? ('　通道 ' + ch) : '') ||
    (d.verified === true
      ? '<div class="dock-ok">✓ 已自证：话送到了这个目标，且读回一致' + (ch ? '　通道 ' + ch : '') + '</div>'
      : d.verified === false
        ? '<div class="dock-warn">⚠ 未核实：投递没成功' + (ch ? '（锁定的通道 ' + ch + '）' : '') + '</div>'
        : '<div class="dock-warn">⚠ 半自动：内容已放进剪贴板，需要你到目标窗口点一下再按 Ctrl+V —— ' +
          '平台<b>不能</b>核实这一档 —— 这不等于话已经到了。</div>');
  return head + tc + _dkAnchorSourceHtml(d) + _dkPasteHtml(d) + det + nx + src + att;
}

async function dockDeliverSend(prefix){
  const hidNow = dockDeliverHid(prefix);
  const out = document.getElementById(prefix + '-out');
  const msg = document.getElementById(prefix + '-msg');
  const ta = document.getElementById(prefix + '-text');
  const prefer = ((document.getElementById(prefix + '-prefer') || {}).value) || '';
  const enter = !!((document.getElementById(prefix + '-enter') || {}).checked);
  if (!out) return;
  if (!hidNow){ if (msg) msg.textContent = '先选一位员工'; return; }
  const text = ((ta && ta.value) || '').trim();
  if (!text){ if (msg) msg.textContent = '要送的话还是空的 —— 先写一句再送'; return; }
  if (msg) msg.textContent = '送话中…（定位目标 → 逐通道投递 → 读回核对）';
  const btn = document.getElementById(prefix + '-send');
  if (btn) btn.disabled = true;
  out.innerHTML = '<p class="sub">送话中…</p>';
  let res = null;
  try {
    res = await dockPost(_dockHidPath(DOCK_PATH.deliverHarness, hidNow), {text: text, prefer: prefer, enter: enter});
  } catch (e) {
    res = {ok: false, data: {error: '请求发不出去：' + (e && e.message ? e.message : e)}, status: 0};
  } finally {
    if (btn) btn.disabled = false;
  }
  if (msg) msg.textContent = '';
  out.innerHTML = dockDeliverResultHtml(res.data || {}, res);
  if (res.ok && (res.data || {}).verified === true && ta) ta.value = '';
}

/* ══ 备选路一：表层交互（D后端-3）—— 教一次位置 → 点到那里 → 逐字键入 ══════════
 * 硬口径：
 *   ① **位置只能由人教**（鼠标停在输入框上）——平台不猜坐标，猜错了会往别处打字；
 *   ② 返回 400（鼠标不在窗口里）＝**如实说**，不硬存；
 *   ③ 结果口径：Electron 一律 `verified=null` + `need_human=true`（文字通常真进去了，但**读不回**）。
 */
const _DK_SURF = {};   /* prefix → {title} */

async function dockAnchorLoad(prefix){
  const box = document.getElementById(prefix + '-anchor');
  if (!box) return;
  const hid = dockDeliverHid(prefix);
  if (!hid){ box.innerHTML = '<p class="sub">先选一位员工。</p>'; return; }
  try {
    const r = await fetch(_dockHidPath(DOCK_PATH.anchor, hid));
    const d = await r.json();
    if (!r.ok) throw new Error(d && d.error ? d.error : ('HTTP ' + r.status));
    const a = d.anchor;
    if (!a){
      box.innerHTML = '<p class="sub">输入框位置：<b>还没教过位置</b></p>' +
        (d.hint && !/^还没教过位置/.test(d.hint) ? ('<p class="sub">' + escPlain(d.hint) + '</p>') : '');
      return;
    }
    /* ★D后端-13：多了 source / frame / win_w / win_h / history —— 把"位置从哪来、教的时候窗口多大"说清楚。 */
    const SRC = { manual: '人教的', cursor: '用鼠标教的', 'auto-from-cdp': '平台自动校准的' };
    const src = SRC[a.source] || a.source || '人教的';
    const size = (a.win_w && a.win_h)
      ? ('　·　教位置时内容区 <code>' + esc(String(a.win_w)) + '×' + esc(String(a.win_h)) + '</code>')
      : '';
    const hist = Array.isArray(a.history) ? a.history : [];
    const prev = hist.length ? hist[0] : null;
    box.dataset.history = JSON.stringify(hist);
    box.innerHTML = '<p class="sub">已教过位置：窗口内相对坐标 <code>(' + esc(String(a.rel_x)) + ', ' +
      esc(String(a.rel_y)) + ')</code>' + (a.title ? ('　窗口：' + escPlain(a.title)) : '') +
      '　·　来源：<b>' + escPlain(src) + '</b>' + size + '</p>' +
      '<p class="sub">窗口大小变了仍对得上（记的是相对位置）；<b>界面改版</b>（输入框换了地方）就要重新教一次。</p>' +
      (prev
        ? ('<p class="sub">上一个位置：<code>(' + esc(String(prev.rel_x)) + ', ' + esc(String(prev.rel_y)) + ')</code>' +
           '　<button class="btn mini quiet" onclick="dockAnchorRestore(\'' + esc(prefix) + '\')">换回上一个位置</button></p>')
        : '');
  } catch (e) {
    box.innerHTML = '<p class="sub">读不到已教位置：' + esc(e && e.message ? e.message : e) + '</p>';
  }
}

/** 换回上一个位置（★D后端-13：`GET /anchor` 的 history[0]）：用已知相对坐标重教一次。 */
async function dockAnchorRestore(prefix){
  const hid = dockDeliverHid(prefix);
  const msg = document.getElementById(prefix + '-anchor-msg');
  if (!hid){ if (msg) msg.textContent = '先选一位员工'; return; }
  const box = document.getElementById(prefix + '-anchor');
  const hist = (box && box.dataset.history) ? JSON.parse(box.dataset.history) : [];
  const prev = hist[0];
  if (!prev){ if (msg) msg.textContent = '没有可换回的上一个位置'; return; }
  const t = (document.getElementById(prefix + '-target') || {}).dataset || {};
  const res = await dockPost(_dockHidPath(DOCK_PATH.anchor, hid),
    {rel_x: prev.rel_x, rel_y: prev.rel_y, title: t.title || ''});
  const d = res.data || {};
  if (msg) msg.textContent = (!res.ok || d.error)
    ? ('换不回去：' + escPlain(d.error || ('HTTP ' + res.status)))
    : '已换回上一个位置';
  dockAnchorLoad(prefix);
}

async function dockAnchorTeach(prefix){
  const hid = dockDeliverHid(prefix);
  const msg = document.getElementById(prefix + '-anchor-msg');
  const box = document.getElementById(prefix + '-anchor');
  if (!hid){ if (msg) msg.textContent = '先选一位员工'; return; }
  if (msg) msg.textContent = '正在读鼠标位置…';
  const res = await dockPost(_dockHidPath(DOCK_PATH.anchor, hid), {from_cursor: true});
  const d = res.data || {};
  if (msg) msg.textContent = '';
  if (!res.ok || d.error){
    if (box) box.innerHTML = '<div class="dock-err">✗ 没记下来：' + escPlain(d.error || ('HTTP ' + res.status)) + '</div>' +
      '<p class="sub">把鼠标移到那个窗口的输入框上，再点一次「教一次位置」。</p>';
    if (d.anchor) _DK_SURF[prefix] = _DK_SURF[prefix] || {};
    return;
  }
  if (box) box.innerHTML = '<div class="dock-ok">✓ 已记下位置：窗口内相对坐标 <code>(' +
    esc(String((d.anchor || {}).rel_x)) + ', ' + esc(String((d.anchor || {}).rel_y)) + ')</code>' +
    '　·　之后「用表层交互送这一句」会点到那里再逐字打字。</div>';
  dockAnchorLoad(prefix);
}

async function dockAnchorClear(prefix){
  const hid = dockDeliverHid(prefix);
  const msg = document.getElementById(prefix + '-anchor-msg');
  if (!hid){ if (msg) msg.textContent = '先选一位员工'; return; }
  const res = await dockDelete(_dockHidPath(DOCK_PATH.anchor, hid));
  if (msg) msg.textContent = (!res.ok || (res.data || {}).error)
    ? ('没清掉：' + ((res.data && res.data.error) || ('HTTP ' + res.status)))
    : '已清除（平台不会拿旧位置去点）';
  dockAnchorLoad(prefix);
}

async function dockSurfaceSend(prefix){
  const hid = dockDeliverHid(prefix);
  const out = document.getElementById(prefix + '-surf-out');
  const msg = document.getElementById(prefix + '-anchor-msg');
  const ta = document.getElementById(prefix + '-text');
  const tBox = document.getElementById(prefix + '-target');
  if (!out) return;
  if (!hid){ if (msg) msg.textContent = '先选一位员工'; return; }
  const text = ((ta && ta.value) || '').trim();
  if (!text){ if (msg) msg.textContent = '先把要送的话写进上面的框里'; return; }
  const title = (tBox && tBox.dataset.title) || '';
  if (!title){ if (msg) msg.textContent = '平台还没认出这个员工的目标 —— 先点「重新解析」'; return; }
  if (msg) msg.textContent = '表层交互投递中…（置顶 → 点到教过的位置 → 逐字键入）';
  out.innerHTML = '<p class="sub">投递中…</p>';
  const enter = !!((document.getElementById(prefix + '-surf-enter') || {}).checked);
  const res = await dockPost(DOCK_PATH.tryActivate, {title: title, text: text, method: 'surface', harness_id: hid, enter: enter});
  if (msg) msg.textContent = '';
  const d = res.data || {};
  out.innerHTML = _dkSurfaceResultHtml(d, res);
}

/* ══ 试投/送话结果里的两条新说法（★D后端-14）════════════════════════════
 * `anchor_used`：true ⇒ 已先点一下教过的输入框再粘贴；false ⇒ 没教过位置，Electron 里可能落空。
 * 目标最小化是**新增的一种失败原因**：如实说，并给"先恢复窗口"的动作提示。
 */
function _dkAnchorUsedHtml(d){
  if (!d || typeof d.anchor_used !== 'boolean') return '';
  return d.anchor_used
    ? '<p class="sub">已先点一下<b>教过的输入框位置</b>，再粘贴 —— 这样 Electron 里才真能进框。</p>'
    : '<p class="sub">这次<b>没有</b>教过输入框位置 ⇒ 只能把内容放剪贴板；Electron 这类窗口里可能落空 —— 回屏3 教一次位置再试。</p>';
}
function _dkMinimizedHint(d){
  const det = String((d && (d.detail || d.error)) || '');
  if (!/最小化/.test(det)) return '';
  return '<p class="lead">先把目标窗口<b>恢复出来</b>（别最小化、也别被别的窗口挡住），再试一次。</p>';
}

/** 候选窗口 → 绑定到这个员工（★D后端-15 §3.1）：让"还没会话名"的员工也能送话。 */
async function dockBindCand(hwnd, title){
  const hid = DOCK_SCRIPT.hid;
  const msg = document.getElementById('dkTryMsg');
  if (!hid){ if (msg) msg.textContent = '先在屏1选一位员工，再绑定'; return; }
  if (msg) msg.textContent = '正在绑定…';
  const res = await dockPost('/api/docking/harness/' + encodeURIComponent(hid) + '/bind',
    {hwnd: Number(hwnd), title: title || ''});
  const d = res.data || {};
  if (msg) msg.textContent = (!res.ok || d.error)
    ? ('没绑上：' + escPlain(d.error || ('HTTP ' + res.status)))
    : ('已把这个窗口绑给' + (d.harness_name ? ('「' + d.harness_name + '」') : '这位员工') + '—— 现在可以直接送话');
  if (res.ok && !d.error) dockAnchorLoad('dkScript');
}

function _dkSurfaceResultHtml(d, res){
  if (!res || res.ok === false || d.error){
    return '<div class="dock-err">✗ ' + escPlain(d.error || d.detail || ('HTTP ' + ((res && res.status) || '?'))) + '</div>' +
      (d.next_step ? ('<p class="lead">下一步：' + escPlain(d.next_step) + '</p>') : '');
  }
  const bits = [];
  if (d.clicked === true) bits.push('点到了教过的位置');
  if (d.typed === true) bits.push('逐字键入完成');
  const what = bits.length ? ('（' + bits.join('、') + '）') : '';
  /* ★D后端-10：这条路也有 state 了 —— 有就按四态说，别再用 verified 猜。 */
  const st = dockStateHtml(d, what);
  const nx = d.next_step ? ('<p class="lead">下一步：' + escPlain(d.next_step) + '</p>') : '';
  const det = d.detail ? ('<p class="sub">' + escPlain(d.detail) + '</p>') : '';
  if (st) return st + dockTextControlHtml(d) + det + nx;
  if (d.verified === true) return '<div class="dock-ok">✓ 已自证：文字进了那个输入框，读回一致' + what + '</div>' + nx;
  if (d.verified === false) return '<div class="dock-warn">⚠ 未核实：读回不一致（可能没进到输入框）' + what + '</div>' + det + nx;
  return '<div class="dock-warn">⚠ 未核实：已按教过的位置点进去并逐字键入' + what +
    ' —— 这类目标（Electron）没有可读回的文本框，<b>平台不能确认对方收到了</b>。</div>' + det + nx;
}

/* ══ 备选路二：浏览器直连（D后端-4 · CDP）═════════════════════════════════════
 * ①②③ 都由用户点：① 找页面（probe）② 指认输入框（editors）③ 投递（web-send，默认不发回车）。
 * 三档 insert → paste-event → clipboard，每档后读回核对；`readback` 可折叠预览。
 */
const _DK_CDP = {};   /* prefix → {ws, idx, title} */

function _cdpMsg(prefix, text, bad){
  const el = document.getElementById(prefix + '-cdp-msg');
  if (!el) return;
  el.textContent = text || '';
  el.style.color = bad ? 'var(--red,#FF3526)' : '';
}

async function dockCdpProbe(prefix){
  const box = document.getElementById(prefix + '-cdp-targets');
  const eBox = document.getElementById(prefix + '-cdp-editors');
  if (!box) return;
  _cdpMsg(prefix, '正在找开了调试端口的页面…');
  box.innerHTML = '<p class="sub">探测中…</p>';
  if (eBox) eBox.innerHTML = '';
  let d = null;
  try { d = await (await fetch(DOCK_PATH.webProbe)).json(); }
  catch (e) { _cdpMsg(prefix, ''); box.innerHTML = '<div class="dock-err">✗ 探测失败：' + esc(e.message) + '</div>'; return; }
  _cdpMsg(prefix, '');
  const targets = [];
  (d.ports || []).forEach(p => (p.targets || []).forEach(t => targets.push(Object.assign({port: p.port}, t))));
  if (!targets.length){
    /* 空结果时**原样**把 why 显示出来 —— 那是"怎么开调试端口"的说明，不是我们的措辞 */
    box.innerHTML = '<div class="dock-warn">⚠ 没找到开了调试端口的页面</div>' +
      (d.why ? ('<p class="sub">' + escPlain(d.why) + '</p>') : '');
    return;
  }
  box.innerHTML = targets.map((t, i) => '<div class="dock-cand" role="button" tabindex="0" data-prefix="' + esc(prefix) +
    '" data-ws="' + esc(t.ws) + '" data-title="' + esc(t.title || '') + '" onclick="dockCdpPick(this)"' +
    ' onkeydown="if(event.key===\'Enter\'){event.preventDefault();dockCdpPick(this);}">' +
    '<span class="cand-pick">选这个页面</span><b>' + esc(t.title || '（无标题）') + '</b><br>' +
    '<span class="sub">端口 <code>' + esc(t.port) + '</code> · ' + esc(t.url || '') + '</span></div>').join('');
}

async function dockCdpPick(el){
  const id = el.getAttribute('data-prefix') || '';
  if (!id) return;
  document.querySelectorAll('#' + CSS.escape(id) + '-cdp-targets .dock-cand.sel').forEach(x => x.classList.remove('sel'));
  el.classList.add('sel');
  const ws = el.getAttribute('data-ws') || '';
  _DK_CDP[id] = Object.assign({}, _DK_CDP[id] || {}, {ws: ws, title: el.getAttribute('data-title') || '', idx: 0});
  _cdpMsg(id, '正在列出这个页面里能输入的地方…');
  const eBox = document.getElementById(id + '-cdp-editors');
  const res = await dockPost(DOCK_PATH.webEditors, {ws: ws});
  _cdpMsg(id, '');
  if (!eBox) return;
  const d = res.data || {};
  if (!res.ok || d.error){
    eBox.innerHTML = '<div class="dock-err">✗ 列不出输入框：' + escPlain(d.error || ('HTTP ' + res.status)) + '</div>';
    return;
  }
  const eds = d.editors || [];
  if (!eds.length){ eBox.innerHTML = '<p class="sub">这个页面里没扫到能输入的地方 —— 换个页面，或先在页面里点开输入框。</p>'; return; }
  eBox.innerHTML = '<p class="sub" style="margin-top:6px">这个页面里能输入的地方（点一个指认它是聊天框 —— 平台不猜是哪个）：</p>' +
    eds.map(e => '<div class="dock-cand sel" role="button" tabindex="0" data-prefix="' + esc(id) + '" data-idx="' + esc(e.idx) + '"' +
      ' onclick="dockCdpPickEditor(this)" onkeydown="if(event.key===\'Enter\'){event.preventDefault();dockCdpPickEditor(this);}">' +
      '<span class="cand-pick">#' + esc(String(e.idx)) + '</span><b>' + esc(e.tag || '?') + '</b>' +
      (e.placeholder ? (' · <span class="sub">' + esc(e.placeholder) + '</span>') : '') + '<br>' +
      '<span class="sub">尺寸 ' + esc(String(e.w || '?')) + '×' + esc(String(e.h || '?')) +
      (e.cls ? (' · class <code>' + esc(e.cls) + '</code>') : '') + '</span></div>').join('');
  const btn = document.getElementById(id + '-cdp-send');
  if (btn) btn.disabled = false;
}

function dockCdpPickEditor(el){
  const id = el.getAttribute('data-prefix') || '';
  const idx = Number(el.getAttribute('data-idx') || 0);
  document.querySelectorAll('#' + CSS.escape(id) + '-cdp-editors .dock-cand').forEach(x => x.classList.remove('sel'));
  el.classList.add('sel');
  _DK_CDP[id] = Object.assign({}, _DK_CDP[id] || {}, {idx: idx});
  const sel = document.getElementById(id + '-cdp-sel');
  if (sel) sel.textContent = '已指认：第 ' + idx + ' 个输入框';
}

async function dockCdpSend(prefix){
  const st = _DK_CDP[prefix] || {};
  const out = document.getElementById(prefix + '-cdp-out');
  const ta = document.getElementById(prefix + '-text');
  const hid = dockDeliverHid(prefix);
  if (!out) return;
  if (!st.ws){ _cdpMsg(prefix, '先「① 找页面」并选一个', true); return; }
  const text = ((ta && ta.value) || '').trim();
  if (!text){ _cdpMsg(prefix, '要送的文本是空的 —— 上面那个框里写一句', true); return; }
  const mode = ((document.getElementById(prefix + '-cdp-mode') || {}).value) || 'auto';
  const enter = !!((document.getElementById(prefix + '-cdp-enter') || {}).checked);
  _cdpMsg(prefix, '投递中…（三档降级 + 每档读回核对）');
  out.innerHTML = '<p class="sub">投递中…</p>';
  const res = await dockPost(DOCK_PATH.webSend, {ws: st.ws, text: text, idx: st.idx || 0, mode: mode, enter: enter,
    harness_id: hid || undefined, title: st.title || ''});
  _cdpMsg(prefix, '');
  const d = res.data || {};
  if (!res.ok || d.error){
    out.innerHTML = '<div class="dock-err">✗ ' + escPlain(d.error || ('HTTP ' + res.status)) + '</div>';
    return;
  }
  const tiers = (d.tiers || []).map(t => '<li><code>' + esc(t.mode || t.tier || '?') + '</code>：' +
    (t.ok ? '成了' : '没成') + (t.why ? (' —— ' + escPlain(t.why)) : '') + '</li>').join('');
  const rb = d.readback ? ('<details><summary>读回内容预览</summary><pre>' + esc(String(d.readback).slice(0, 800)) + '</pre></details>') : '';
  let head;
  if (d.verified === true) head = '<div class="dock-ok">✓ 已自证：文本进了那个输入框，读回一致（档位 ' + esc(d.mode || '?') + '）</div>';
  else if (d.verified === false) head = '<div class="dock-warn">⚠ 未核实：读回不一致（档位 ' + esc(d.mode || '?') + '）</div>';
  else head = '<div class="dock-warn">⚠ 未核实：送出了输入，但这一路读不回（档位 ' + esc(d.mode || '?') + '）</div>';
  out.innerHTML = head +
    (d.detail ? ('<p class="sub">' + escPlain(d.detail) + '</p>') : '') +
    (d.next_step ? ('<p class="lead">下一步：' + escPlain(d.next_step) + '</p>') : '') +
    (tiers ? ('<details><summary>每一档试了什么</summary><ul class="dock-kv">' + tiers + '</ul></details>') : '') +
    (d.enter ? ('<p class="sub">回车：' + ((d.enter.ok === true) ? '已发送' : ('没发出去 —— ' + escPlain((d.enter.why || '')))) + '</p>') : '') +
    rb;
}

/** 瓦片入口：员工可选（列表来自对接总览，避免第二个数据源）。 */
/* ══ 投递结果里的新硬信号（★D后端-16/17/18）════════════════════════════
 * `anchor_source`：taught=人教的 / auto-vision(0.87)=平台视觉估的 / 缺省=没估出来。
 * `paste_ok`：到底有没有按成 Ctrl+V；`paste_seen`：粘贴前后输入框那一条带的画面变没变
 *   （Electron 读不回文字时，这是唯一硬信号：true=像粘进去了 / false=看不出 / null=没法比）。
 * `paste_ratio`：变化像素比例（静止 0.0000 / 空框→一句话 0.0127）。
 * `press_ok/press_how/press_note`：回车或点发送按钮到底做了没有。
 */
function _dkAnchorSourceHtml(d){
  const src = (d && d.anchor_source) || '';
  if (!src) return '';
  const map = { taught: '用的是你教过的位置', 'taught(bind)': '用的是你教过的位置' };
  const txt = map[src] || (/^auto-vision/.test(src)
    ? ('位置由平台视觉估得（把握 ' + escPlain(String(src).replace(/^auto-vision\(?|\)$/g, '')) + '）—— 不放心就框一次')
    : ('位置来源：' + escPlain(src)));
  return '<p class="sub">' + txt + '</p>';
}
function _dkPasteHtml(d){
  if (!d) return '';
  const bits = [];
  if (d.paste_ok === true) bits.push('已按成 Ctrl+V');
  else if (d.paste_ok === false) bits.push('没按成 Ctrl+V');
  if (d.paste_seen === true) bits.push('画面变了 ⇒ 像粘进去了');
  else if (d.paste_seen === false) bits.push('画面没变 ⇒ 看不出粘进去');
  else if (d.paste_seen === null) bits.push('没法比对画面（窗口挡着/最小化）');
  if (typeof d.paste_ratio === 'number') bits.push('变化 ' + (d.paste_ratio * 100).toFixed(2) + '%');
  const press = (d.press_note || '');
  if (!bits.length && !press) return '';
  return '<p class="sub">' + bits.join(' · ') + (press ? ('　发送：' + escPlain(press)) : '') + '</p>';
}

/* ══ 框选输入框（★D后端-17/18）：截图 → 拖框 → 保存 → 试投 ════════════
 * 纪律：
 *  ① 相对比例 = 框在图上的比例（图是客户区的等比缩略）÷ 图上尺寸；`frame` 用响应头里的**真实客户区**，
 *     不能用 PNG 像素（图按宽 ≤1100 缩过，后端实测会差 3 倍）；
 *  ② 输入框必填、发送按钮可选（只框输入框也行，粘完自己点发送）；
 *  ③ 每次操作的结果都写进 `-box-msg` / `-box-out`，失败**原文显示**后端的 400。
 */
const _DK_BOX = {};
function _boxState(prefix){
  if (!_DK_BOX[prefix]) _DK_BOX[prefix] = {imgw: 0, imgh: 0, cw: 0, ch: 0, box: {input: null, send: null}, mode: 'input', bound: false};
  return _DK_BOX[prefix];
}
function _boxTitle(prefix){
  const t = document.getElementById(prefix + '-target');
  let title = (t && t.dataset && t.dataset.title) || '';
  if (!title && typeof DOCK_SCRIPT !== 'undefined' && DOCK_SCRIPT) title = DOCK_SCRIPT.picked || '';
  return title;
}
function _boxMsg(prefix, text, bad){
  const el = document.getElementById(prefix + '-box-msg');
  if (!el) return;
  el.textContent = text || '';
  el.style.color = bad ? 'var(--red,#FF3526)' : '';
}
/** 记住"这次框的是哪扇窗"（保存时要拿它去绑定）。三个图源都调。 */
function _boxRememberWindow(prefix){
  const st = _boxState(prefix);
  const picked = (typeof DOCK_SCRIPT !== 'undefined' && DOCK_SCRIPT) ? DOCK_SCRIPT.pickedInfo : null;
  if (picked){
    if (picked.hwnd) st.hwnd = String(picked.hwnd);
    if (DOCK_SCRIPT.picked) st.title = DOCK_SCRIPT.picked;
  }
  if (!st.title) st.title = _boxTitle(prefix) || '';
  return st;
}
/** 客户区尺寸的唯一入口：**拒收最小化占位值**（Windows 会给 237×39 这种东西）。 */
function _boxApplyClient(prefix, w, h, info){
  const st = _boxState(prefix);
  const minimized = !!(info && info.minimized);
  const tooSmall = !(w >= 200 && h >= 200);
  if (minimized || tooSmall){
    st.cw = 0; st.ch = 0;
    st.usable = false;
    st.why = minimized ? '这个窗口最小化了（尺寸是占位值，不是真大小）' : '这个窗口太小（可能是影子窗口）';
    return false;
  }
  st.cw = Number(w); st.ch = Number(h);
  st.usable = !(info && info.usable === false);
  st.why = (info && info.why) || (st.usable ? '' : '这个窗口现在截不了');
  return true;
}

async function dockBoxStatus(prefix){
  const box = document.getElementById(prefix + '-box-status');
  const hid = dockDeliverHid(prefix);
  if (!box) return;
  if (!hid){ box.innerHTML = '<p class="sub">先选一位员工。</p>'; return; }
  try {
    const r = await fetch(_dockHidPath('/api/docking/harness/{hid}/regions', hid));
    const d = await r.json();
    const g = d.regions;
    if (!g){ box.innerHTML = '<p class="sub">还没框过 —— 点「截一张图」，把「输入框」拖出来（发送按钮可选）。</p>'; return; }
    const f = v => (v == null ? '—' : Number(v).toFixed(3));
    const one = (b) => b ? ('(' + f(b.x) + ',' + f(b.y) + ',' + f(b.w) + '×' + f(b.h) + ')') : '—';
    box.innerHTML = '<p class="sub">已框：输入框 <code>' + one(g.input) + '</code>　发送按钮 <code>' + one(g.send) +
      '</code>　关键词：' + ((g.keywords && g.keywords.length) ? escPlain(g.keywords.join('、')) : '—') +
      (g.frame ? ('　·　客户区 ' + esc(String(g.frame.w)) + '×' + esc(String(g.frame.h))) : '') + '</p>';
  } catch (e) {
    box.innerHTML = '<p class="sub">读不到已框区域：' + esc(e && e.message ? e.message : e) + '</p>';
  }
}
/** 客户区尺寸从 `locate` 里那条拿（截图可能因为窗口最小化失败，locate 的尺寸永远给得出来）。 */
async function _boxClientSize(prefix){
  const st = _boxState(prefix);
  if (st.cw && st.ch) return true;
  /* ① 先用"屏2/3 选中的那条"（老板第 4 条：基准取 locate 的 client_w/client_h） */
  const picked = (typeof DOCK_SCRIPT !== 'undefined' && DOCK_SCRIPT) ? DOCK_SCRIPT.pickedInfo : null;
  if (picked && (picked.client_w || picked.client_h)){
    if (_boxApplyClient(prefix, picked.client_w || 0, picked.client_h || 0, picked)) return true;
    /* 占位值/太小 ⇒ 不当作基准，继续往下查一次（也可能查不到，那就如实说） */
  }
  const title = _boxTitle(prefix);
  if (!title) return false;
  try {
    const res = await dockPost(DOCK_PATH.locate, {keywords: [title], limit: 5});
    const d = res.data || {};
    const c = ((d.candidates || [])[0]) || null;
    if (!c) return false;
    if (c.hwnd) st.hwnd = String(c.hwnd);
    if (c.title) st.title = st.title || c.title;
    return _boxApplyClient(prefix, c.client_w || c.width || 0, c.client_h || c.height || 0, c);
  } catch (e) { return false; }
}
/** 三图源共用：把一张图铺到画布上（`cw/ch` 是客户区，用来把框的比例算对）。 */
function _boxUseImage(prefix, src, label){
  const st = _boxState(prefix);
  const wrap = document.getElementById(prefix + '-box-canvas');
  const img = document.getElementById(prefix + '-box-img');
  if (!wrap || !img) return;
  img.onload = () => {
    st.imgw = img.naturalWidth; st.imgh = img.naturalHeight;
    st.box = {input: null, send: null};
    wrap.hidden = false;
    const ri = document.getElementById(prefix + '-box-r-input');
    const rs = document.getElementById(prefix + '-box-r-send');
    if (ri) ri.hidden = true;
    if (rs) rs.hidden = true;
    _boxMsg(prefix, label + '（图 ' + st.imgw + '×' + st.imgh + '）' +
      (st.cw && st.ch ? ('　客户区 ' + st.cw + '×' + st.ch + '（比例按客户区算）') : '　⚠ 拿不到客户区尺寸，比例可能对不上') +
      ' —— 选「框输入框」后在图上拖');
  };
  img.src = src;
}
async function dockBoxShot(prefix){
  const hid = dockDeliverHid(prefix);
  const wrap = document.getElementById(prefix + '-box-canvas');
  const img = document.getElementById(prefix + '-box-img');
  if (!hid || !wrap || !img){ _boxMsg(prefix, '先选一位员工', true); return; }
  const st = _boxState(prefix);
  _boxRememberWindow(prefix);
  const picked0 = (typeof DOCK_SCRIPT !== 'undefined' && DOCK_SCRIPT) ? DOCK_SCRIPT.pickedInfo : null;
  if (!st.cw && picked0 && picked0.client_w){
    _boxApplyClient(prefix, picked0.client_w, picked0.client_h || 0, picked0);
  }
  await _boxClientSize(prefix);
  if (st.usable === false){
    _boxMsg(prefix, '✗ ' + (st.why || '这个窗口现在截不了') + ' —— 先把它恢复出来，或者用「用剪贴板里的图 / 上传图片」自己截一张', true);
    return;
  }
  _boxMsg(prefix, '正在截图…');
  const title = _boxTitle(prefix);
  const url = _dockHidPath('/api/docking/harness/{hid}/screenshot', hid) + (title ? ('?title=' + encodeURIComponent(title)) : '');
  try {
    const r = await fetch(url);
    if (!r.ok){
      const d = await r.json().catch(() => ({error: 'HTTP ' + r.status}));
      _boxMsg(prefix, '✗ ' + (d.error || ('HTTP ' + r.status)) + '（也可以自己截一张：Win+Shift+S，再点「用剪贴板里的图」）', true);
      return;
    }
    const blob = await r.blob();
    if (st.url) URL.revokeObjectURL(st.url);
    st.url = URL.createObjectURL(blob);
    _boxUseImage(prefix, st.url, '窗口截图');
  } catch (e) {
    _boxMsg(prefix, '✗ 截图请求发不出去：' + (e && e.message ? e.message : e), true);
  }
}
/** 用**剪贴板里那张图**：先试平台端点（不经过浏览器权限），再退回 navigator.clipboard。 */
async function dockBoxPaste(prefix){
  const st = _boxState(prefix);
  _boxRememberWindow(prefix);
  await _boxClientSize(prefix);
  _boxMsg(prefix, '正在取剪贴板里的图…');
  try {
    const r = await fetch('/api/docking/clipboard-image?t=' + Date.now());
    if (r.ok){
      const blob = await r.blob();
      if (st.url) URL.revokeObjectURL(st.url);
      st.url = URL.createObjectURL(blob);
      _boxUseImage(prefix, st.url, '剪贴板里的图');
      return;
    }
    const d = await r.json().catch(() => ({error: 'HTTP ' + r.status}));
    if (r.status === 501){
      _boxMsg(prefix, '这台系统取不了剪贴板图片（' + (d.error || '') + '）—— 改用「上传图片」', true);
      return;
    }
    _boxMsg(prefix, '✗ ' + (d.error || '剪贴板里没有图') + ' —— 先截一张（Win+Shift+S），再点这里', true);
  } catch (e) {
    _boxMsg(prefix, '✗ 取剪贴板失败：' + (e && e.message ? e.message : e), true);
  }
}
/** 上传一张图（纯前端，不经过后端）。 */
async function dockBoxUpload(prefix, input){
  const file = input && input.files && input.files[0];
  if (!file){ _boxMsg(prefix, '没选到文件', true); return; }
  _boxRememberWindow(prefix);
  await _boxClientSize(prefix);
  const reader = new FileReader();
  reader.onload = () => _boxUseImage(prefix, String(reader.result || ''), '上传的图：' + file.name);
  reader.onerror = () => _boxMsg(prefix, '✗ 这个文件读不出来', true);
  reader.readAsDataURL(file);
}
/** 「去框选输入框（推荐）」：滚到框选块 + 聚焦第一个按钮；不可用时把原因写在旁边。 */
function dockGotoBox(prefix){
  const p = prefix || 'dkDeliver';
  const block = document.querySelector('.dk-boxsel');
  if (!block){ _boxMsg(p, '找不到框选块', true); return; }
  block.scrollIntoView({behavior: 'smooth', block: 'center'});
  const first = document.getElementById(p + '-box-shot');
  if (first){ try { first.focus({preventScroll: true}); } catch (e) { /* 老浏览器 */ } }
  const st = _boxState(p);
  if (st.usable === false){
    _boxMsg(p, '注意：' + (st.why || '这个窗口现在截不了') + ' —— 先把它恢复出来，或用「用剪贴板里的图 / 上传图片」', true);
  } else {
    _boxMsg(p, '点「截一张图」（或用剪贴板/上传），然后把要写字的那一行框出来');
  }
}
/** 屏3 的入口：把当前员工带过去，打开送话面板并滚到框选块。 */
function dockGotoBoxFromScript(){
  const sel = document.getElementById('dkHid');
  const hid = (sel && sel.value) || DOCK_SCRIPT.hid || '';
  if (hid) DOCK_SCRIPT.hid = hid;
  dockGo('deliver');
  setTimeout(() => {
    /* 把屏2/3 选中窗口的客户区尺寸带进框选块（比例基准），并如实说能不能截 */
    const picked = DOCK_SCRIPT.pickedInfo;
    if (picked){
      const st = _boxState('dkDeliver');
      st.cw = Number(picked.client_w || 0);
      st.ch = Number(picked.client_h || 0);
      st.usable = picked.usable !== false;
      st.why = picked.why || '';
      const shotBtn = document.getElementById('dkDeliver-box-shot');
      if (shotBtn) shotBtn.disabled = (st.usable === false);
    }
    dockGotoBox('dkDeliver');
  }, 700);
}
function dockBoxMode(prefix, mode){
  const st = _boxState(prefix);
  st.mode = mode || 'input';
  document.querySelectorAll('[data-box-mode]').forEach(b => {
    b.classList.toggle('primary', b.dataset.boxMode === st.mode);
  });
  _boxMsg(prefix, '现在拖出的框是「' + (st.mode === 'send' ? '发送按钮' : '输入框') + '」');
}
function _boxBind(prefix){
  const st = _boxState(prefix);
  const wrap = document.getElementById(prefix + '-box-canvas');
  if (!wrap || st.bound) return;
  st.bound = true;
  let start = null;
  const toPx = (ev) => {
    const img = document.getElementById(prefix + '-box-img');
    const r = img.getBoundingClientRect();
    return {x: Math.max(0, Math.min(r.width, ev.clientX - r.left)),
            y: Math.max(0, Math.min(r.height, ev.clientY - r.top)), w: r.width, h: r.height, left: r.left, top: r.top};
  };
  wrap.addEventListener('mousedown', (ev) => {
    if (!st.imgw) return;
    ev.preventDefault();
    start = toPx(ev);
  });
  wrap.addEventListener('mousemove', (ev) => {
    if (!start) return;
    const cur = toPx(ev);
    _boxPaint(prefix, start, cur);
  });
  window.addEventListener('mouseup', (ev) => {
    if (!start) return;
    const cur = toPx(ev);
    const x = Math.min(start.x, cur.x), y = Math.min(start.y, cur.y);
    const w = Math.abs(cur.x - start.x), h = Math.abs(cur.y - start.y);
    start = null;
    if (w < 6 || h < 4){ _boxMsg(prefix, '框太小了，重拖一次', true); return; }
    const rel = {x: +(x / cur.w).toFixed(4), y: +(y / cur.h).toFixed(4),
                 w: +(w / cur.w).toFixed(4), h: +(h / cur.h).toFixed(4)};
    st.box[st.mode] = rel;
    const box = document.getElementById(prefix + '-box-r-' + st.mode);
    if (box){ box.hidden = false; box.style.left = (rel.x * 100) + '%'; box.style.top = (rel.y * 100) + '%';
      box.style.width = (rel.w * 100) + '%'; box.style.height = (rel.h * 100) + '%'; }
    _boxMsg(prefix, '已记下「' + st.mode + '」框');
    /* 模板注册面板（FE-31）要按"四块齐没齐"控制保存按钮 */
    if (typeof regTplRefreshBoxes === 'function') regTplRefreshBoxes();
  });
}
function _boxPaint(prefix, a, b){
  const st = _boxState(prefix);
  const box = document.getElementById(prefix + '-box-r-' + st.mode);
  if (!box) return;
  const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y);
  box.hidden = false;
  box.style.left = (x / a.w * 100) + '%'; box.style.top = (y / a.h * 100) + '%';
  box.style.width = (Math.abs(b.x - a.x) / a.w * 100) + '%';
  box.style.height = (Math.abs(b.y - a.y) / a.h * 100) + '%';
}
/** 把这个员工绑到"刚框的那扇窗"（★老板真机踩到：没绑窗口 ⇒ 按员工那条路认不出目标）。
 *  `quiet=true` 时只回布尔并写 `-box-msg`；否则写"已绑"这类话。 */
async function dockBindWindow(prefix, quiet){
  const hid = dockDeliverHid(prefix);
  const st = _boxState(prefix);
  if (!hid){ if (!quiet) _boxMsg(prefix, '先选一位员工', true); return false; }
  if (!st.hwnd && !st.title){ if (!quiet) _boxMsg(prefix, '还没有可绑的窗口 —— 先「截一张图」或在屏2 选一个候选窗口', true); return false; }
  const body = st.hwnd ? {hwnd: Number(st.hwnd), title: st.title || ''} : {title: st.title || ''};
  const res = await dockPost(_dockHidPath('/api/docking/harness/{hid}/bind', hid), body);
  const d = res.data || {};
  if (!res.ok || d.error){
    if (!quiet) _boxMsg(prefix, '✗ 没绑上：' + (d.error || ('HTTP ' + (res.status || '?'))), true);
    return false;
  }
  if (!quiet) _boxMsg(prefix, '✓ 已把这个窗口绑给当前员工（试投/送话都走它了）');
  return true;
}

async function dockBoxSave(prefix){
  const hid = dockDeliverHid(prefix);
  const st = _boxState(prefix);
  if (!hid){ _boxMsg(prefix, '先选一位员工', true); return; }
  if (!st.box.input){ _boxMsg(prefix, '还没框「输入框」—— 至少框它一个（发送按钮可选）', true); return; }
  const kwEl = document.getElementById(prefix + '-box-kw');
  const kws = ((kwEl && kwEl.value) || '').split(/[,，]/).map(s => s.trim()).filter(Boolean);
  const body = {input: st.box.input};
  if (st.box.send) body.send = st.box.send;
  if (kws.length) body.keywords = kws;
  if (!st.cw || !st.ch) await _boxClientSize(prefix);
  if (st.cw && st.ch) body.frame = {w: st.cw, h: st.ch};
  _boxMsg(prefix, '正在保存…');
  try {
    const resR = await dockPost(_dockHidPath('/api/docking/harness/{hid}/regions', hid), body);
    const d = resR.data || {};
    if (!resR.ok || d.error){ _boxMsg(prefix, '✗ ' + (d.error || ('HTTP ' + (resR.status || '?'))), true); return; }
    /* ★老板真机踩到的坑（2026-10-24）：框得再准，员工没绑窗口 ⇒ 后端"按员工"那条路认不出目标。
       ⇒ 保存框选时就把这扇窗绑给这位员工（用户刚框的就是它，不需要再点一次）。 */
    let bindTxt = '';
    if (st.hwnd || st.title){
      const okBind = await dockBindWindow(prefix, true);
      bindTxt = okBind
        ? ('　并把窗口「' + (st.title || (st.hwnd ? ('#' + st.hwnd) : '刚框的这扇')) + '」绑给了这位员工')
        : '　⚠ 窗口没绑上（下面那行有原文）—— 试投会认不出目标';
    }
    _boxMsg(prefix, '✓ 已保存框选' + bindTxt + '（之后试投/送话都走它）');
    dockBoxStatus(prefix);
  } catch (e) {
    _boxMsg(prefix, '✗ 保存请求发不出去：' + (e && e.message ? e.message : e), true);
  }
}
async function dockBoxClear(prefix){
  const hid = dockDeliverHid(prefix);
  if (!hid){ _boxMsg(prefix, '先选一位员工', true); return; }
  try {
    const r = await fetch(_dockHidPath('/api/docking/harness/{hid}/regions', hid), {method: 'DELETE'});
    const d = await r.json().catch(() => ({error: 'HTTP ' + r.status}));
    _boxMsg(prefix, (!r.ok || d.error) ? ('✗ ' + (d.error || ('HTTP ' + r.status))) : '已清掉框选（退回教位置/视觉估位）', !r.ok || d.error);
    const wrap = document.getElementById(prefix + '-box-canvas');
    if (wrap) wrap.hidden = true;
    _boxState(prefix).box = {input: null, send: null};
    dockBoxStatus(prefix);
  } catch (e) {
    _boxMsg(prefix, '✗ 清掉请求发不出去：' + (e && e.message ? e.message : e), true);
  }
}
async function dockBoxTrial(prefix){
  const hid = dockDeliverHid(prefix);
  const out = document.getElementById(prefix + '-box-out');
  if (!hid || !out){ _boxMsg(prefix, '先选一位员工', true); return; }
  out.innerHTML = '<p class="sub">试投中…（点框 → Ctrl+V → 看图核对 → 可选回车/点发送）</p>';
  try {
    const res = await dockPost(_dockHidPath(DOCK_PATH.deliverHarness, hid), {text: '请回复数字 7', enter: true});
    out.innerHTML = dockDeliverResultHtml(res.data || {}, res);
  } catch (e) {
    out.innerHTML = '<div class="dock-err">✗ 请求发不出去：' + escPlain(e && e.message ? e.message : e) + '</div>';
  }
}

async function dockMountDeliver(){
  const wrap = document.getElementById('dockDeliverWrap');
  if (!wrap) return;
  if (!wrap.dataset.mounted){
    let ov = DOCK_LAST_OVERVIEW;
    if (!ov || !(ov.harnesses || []).length){
      try { await dockLoad(); } catch (e) { /* 总览失败下面按"没有员工"如实说 */ }
      ov = DOCK_LAST_OVERVIEW;
    }
    const hs = (ov && ov.harnesses) || [];
    if (!hs.length){
      wrap.innerHTML = '<div class="card"><h3>还没有登记任何员工</h3>' +
        '<p class="lead">送话的对象是<b>已登记的员工</b> —— 先去「登记一位新员工」，回来这里就能送。</p>' +
        '<div class="dock-form-actions">' +
        '<button class="btn mini" onclick="dockGo(\'register\')">登记一位新员工</button>' +
        '<button class="btn mini" onclick="dockGo(\'ask_frontdesk\')">先问前台</button>' +
        '</div></div>';
      return;
    }
    const pre = (DOCK_SCRIPT && DOCK_SCRIPT.hid) || hs[0].harness_id;
    wrap.innerHTML = dockDeliverShell('dkDeliver', pre, true, hs);
    wrap.dataset.mounted = '1';
  }
  await dockDeliverTarget('dkDeliver');
  dockBoxStatus('dkDeliver');
  _boxBind('dkDeliver');
}

/** 档案 ⑦ 入口：员工**定死**在这一份档案上（不在 A 的档案里给 B 送话）。 */
function dockMountDeliverIn(boxId, hid){
  const box = document.getElementById(boxId);
  if (!box) return;
  box.innerHTML = dockDeliverShell('dkDlvDrawer', hid, false, []);
  dockDeliverTarget('dkDlvDrawer', hid);
  dockBoxStatus('dkDlvDrawer');
  _boxBind('dkDlvDrawer');
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
  let health = {};
  try{ const hd = await (await fetch('/api/registry/health')).json(); (hd.agents||[]).forEach(a=>{health[a.harness_id]=a;}); }catch(e){}
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
      '<span class="sub">名册健康：样本 ' + esc(String((health[h.harness_id]||{}).samples!=null?health[h.harness_id].samples:'—')) +
      ' · 连续全失败 ' + esc(String((health[h.harness_id]||{}).days_all_failing!=null?health[h.harness_id].days_all_failing:'—')) + ' 天</span>' +
      ((health[h.harness_id]||{}).candidate ? '<span class="cap-src src-probed" style="color:#FFB000;border-color:#FFB000">建议隐藏</span>' : '') +
      ((health[h.harness_id]||{}).hidden ? '<span class="sub" style="color:var(--red,#FF0000)">· 已隐藏（数据仍在，可申诉恢复）</span>' : '') + '<br>' +
      '<div class="dock-form-actions" style="margin-top:5px">' +
      '<button class="dock-detail-btn" onclick="dockOpenArchive(\'' + esc(h.harness_id) + '\')">详情</button>' +
      '<button class="dock-detail-btn" onclick="dockHarnessHideAsk(\'' + esc(h.harness_id) + '\')">隐藏</button>' +
      '<button class="dock-detail-btn" onclick="dockHarnessAppeal(\'' + esc(h.harness_id) + '\')">申诉</button>' +
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
  const _wake = (d.archive && d.archive.wakeup_method) || (d.harness && d.harness.wakeup_method) || '';
  dockLoadCapability(hid, _wake === 'clipboard');
  dockLoadAttest(hid);   /* ⑤ 能力与证据：抽屉打开即异步加载；clipboard=人工中转，只给「我确认它回了」 */
  dockMountDeliverIn('dkArcDeliverBox-' + hid, hid);   /* ⑦ 送话（FE-30）：档案一打开就能跟这个人说话 */
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
    '<p class="sub">改完保存，平台会重新判定档位事实并回显在这里 —— 不会出现"改了还显示旧的"。</p>' +
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
    '\');dockGo(\'script_activate\')">去脚本激活工作台改</button>' +
    '<button class="dock-detail-btn" onclick="dockExportPs1From(\'' + esc(d.harness_id) + '\')">导出 .ps1 副本</button></div>' +
    (sc.text ? ('<pre>' + esc(sc.text) + '</pre><p class="sub">上次试投：' +
      (sc.verified === true ? '✓ 已自证' : '⚠ 未核实（别当成已验证）') +
      (sc.method ? (' · 注入方式 ' + esc(sc.method)) : '') + '</p>')
      : '<p class="sub">还没存过脚本 —— 去「脚本激活」工作台做一份（填关键词 → 让平台去找 → 试投 → 保存）。</p>') +
    '</details>' +
    '<details id="dkCapBlock-' + esc(d.harness_id) + '"><summary>⑤ 能力与证据（三段分离：配置 ≠ 在跑 ≠ 会回话）</summary><div class="dk-det-body"><p class="sub">加载中…（只读端点，不探活不发外网）</p></div></details>' +
    '<details id="dkAttestBlock-' + esc(d.harness_id) + '"><summary>⑥ 附属记录（存证：签名 / 扫描 / 审计）</summary><div class="dk-det-body"><p class="sub">加载中…</p></div></details>' +
    // ★FE-30 ⑦：送话（按员工走统一投递层）—— 与「送话给员工」瓦片是**同一份渲染**
    '<details id="dkArcDeliver-' + esc(d.harness_id) + '" open><summary>⑦ 送话（按员工 · 平台自己找目标与通道）</summary>' +
    '<div id="dkArcDeliverBox-' + esc(d.harness_id) + '"><p class="sub">正在解析目标…</p></div></details>' +
    '<div class="dock-facts">平台刚判定的档位事实（只读）：档位 <b>' + esc(f.interface_class || '—') +
    '</b> · 平台能叫醒：<b>' + (f.can_be_woken_by_platform ? '能' : '不能') +
    '</b> · 导出候选与脚本的真相都在档案里。</div>' +
    '<div class="dock-form-actions">' +
    '<button class="btn primary" onclick="dockSaveArchive(\'' + esc(d.harness_id) + '\')">保存档案改动</button>' +
    '<span class="dock-msg" id="dkArcMsg"></span></div>' +
    '<div id="dkArcOut"></div></div>';
}

/* ══ 档案 ⑥ 附属记录（V83 存证，DSH 237）════════════════════════
 * 硬口径：每条 trust=platform-self（平台自签）必须原样显示，绝不写成「已认证/已背书」；
 * 验签调 POST /api/attest/verify（吃整条记录），绿=valid、红=不匹配+why；scan 的 items 逐条列出。 */
async function dockLoadAttest(hid){
  const box = document.getElementById('dkAttestBlock-' + hid);
  const body = _dkDetailsBody(box, true);
  if (!box || !body) return;
  try{
    const d = await (await fetch('/api/attest/harness/' + encodeURIComponent(hid))).json();
    const rows = d.records || d.items || [];
    if (!rows.length){ body.innerHTML = '<p class="sub">还没有附属记录——下面可记一条审计。</p>'; }
    else {
      body.innerHTML = '<div class="peers-list">' + rows.map(r => {
        const rid = String(r.att_id || r.id || '').replace(/'/g, '');
        let scan = '';
        if (r.kind === 'scan' && r.result && r.result.items){
          scan = '<div class="pr-sub">' + r.result.items.map(it =>
            '· ' + esc(it.item || '') + '：' + (it.passed ? '通过' : '未通过——' + esc(it.why || ''))).join('<br>') + '</div>';
        }
        return '<div class="peer-row"><div class="pr-top"><b>' + esc(r.kind || '?') + '</b>' +
          '<span class="sub">' + esc(r.at || '') + '</span>' +
          '<span class="cap-src src-declared">trust: ' + esc(r.trust || 'platform-self') + '</span></div>' +
          (r.note ? '<div class="pr-sub">' + esc(r.note) + '</div>' : '') + scan +
          '<div class="pr-acts"><button class="btn mini" onclick="dockAttestVerify(\'' + esc(hid) + '\',\'' + rid + '\')">验签</button>' +
          '<span class="dock-msg" id="capAttestMsg-' + esc(rid) + '"></span></div></div>';
      }).join('') + '</div>';
    }
    body.innerHTML += '<div class="dock-form-actions" style="margin-top:8px">' +
      '<button class="btn mini" onclick="dockAttestAudit(\'' + esc(hid) + '\')">记一条审计</button>' +
      '<span class="dock-msg" id="capAttestAdd-' + esc(hid) + '"></span></div>';
    box.dataset.records = JSON.stringify(rows);
  }catch(e){ body.innerHTML = '<p class="sub">加载失败：' + esc(e.message) + '</p>'; }
}
async function dockAttestVerify(hid, attId){
  const msg = document.getElementById('capAttestMsg-' + attId);
  const box = document.getElementById('dkAttestBlock-' + hid);
  if (!msg || !box) return;
  let rec = null;
  try{ rec = (JSON.parse(box.dataset.records || '[]') || []).find(r => String(r.att_id || r.id || '') === String(attId)); }catch(e){}
  if (!rec){ msg.textContent = '找不到这条记录'; msg.style.color = 'var(--red,#FF0000)'; return; }
  msg.textContent = '验签中…';
  const res = await dockPost('/api/attest/verify', rec);
  const d = res.data || {};
  if (d.valid === true){ msg.textContent = '✓ 验签通过（trust: ' + esc(rec.trust || 'platform-self') + '）'; msg.style.color = 'var(--green,#8FB39B)'; }
  else { msg.textContent = '✗ 不匹配：' + (d.why || ('HTTP ' + res.status)); msg.style.color = 'var(--red,#FF0000)'; }
}
async function dockAttestAudit(hid){
  const note = prompt('审计内容（必填）：');
  if (note === null) return;
  if (!note.trim()){ alert('内容必填。'); return; }
  const msg = document.getElementById('capAttestAdd-' + hid);
  if (msg) msg.textContent = '记录中…';
  const res = await dockPost('/api/attest/harness/' + encodeURIComponent(hid), {kind: 'audit', note: note.trim()});
  const d = res.data || {};
  if (res.ok && d.success !== false){
    if (msg) msg.textContent = '已记录（trust: ' + esc(d.trust || 'platform-self') + '，平台自签）';
    dockLoadAttest(hid);
  } else if (msg){
    msg.textContent = '记录未成功：' + ((d.error || d.reason) || ('HTTP ' + res.status)) + (d.how_to_fix ? (' · ' + d.how_to_fix) : '');
    msg.style.color = 'var(--red,#FF0000)';
  }
}

/* ══ 档案 ⑤ 能力与证据（V73，DSH 226）════════════════════════
 * 三条硬要求：三段分开显示；connected=false 绝不出现「已接上/可用/已就绪」；
 * 能力挂来源徽标（declared灰/probed黄/verified绿/failed红）；notes 原样显示。
 * verified 只证明"桥能连平台/领件/回报"，不代表 agent 能干这事——照显示。 */
const CAP_STATE_TEXT = {ok:'已验证', failed:'失败', unknown:'未知', unverified:'未验证'};
const CAP_SRC_TEXT = {declared:'声明', probed:'已探到', verified:'已验证', failed:'失败'};
const CAP_SRC_CLS = {declared:'src-declared', probed:'src-probed', verified:'src-verified', failed:'src-failed'};
/* ④ agent 能回话的状态文案（DSH 228 映射：source 必须区分，人工确认绝不冒充自动验证） */
function capReplyText(o){
  const st = String((o||{}).state||'unverified');
  if (st==='ok') return (o.source==='human_confirm') ? '人工确认（用户说它回了）' : '已验：投递后它回了带回执的消息';
  if (st==='failed') return '验过但失败：' + (o.why||'探针超时（没收到回执）');
  if (st==='pending') return '探针进行中…（等它回话，最长 120 秒）';
  return '没验过——不代表接上了';
}
/* 回话探针（V74）：默认关（409 显示"还没开探针"不是失败）；人工中转型只给「我确认它回了」；
 * 不在页面加载时自动发探针——只在用户点按钮时发。 */
async function dockProbeReply(hid, humanRelay){
  const msgBox = document.getElementById('capProbeMsg-' + hid);
  const setMsg = (t, bad) => { const e=document.getElementById('capProbeMsg-' + hid); if(e){e.textContent=t; e.style.color = bad?'var(--red,#FF0000)':'';} };
  if (!msgBox) return;
  if (humanRelay){
    setMsg('确认中...');
    const res = await dockPost('/api/harness/' + encodeURIComponent(hid) + '/probe-reply', {mode:'human_confirm'});
    if (res.ok && res.data && res.data.ok!==false){ setMsg('已记录：人工确认它回了（徽标为「人工确认」，与自动验证分开）'); dockLoadCapability(hid, true); }
    else setMsg('确认未成功：' + ((res.data && (res.data.error||res.data.reason)) || ('HTTP ' + res.status)) + ((res.data && res.data.how_to_fix)?(' · '+res.data.how_to_fix):''), true);
    return;
  }
  /* 先看当前探针状态（GET 只读） */
  try{
    const gs = await (await fetch('/api/harness/' + encodeURIComponent(hid) + '/probe-reply')).json();
    if (gs.state === 'pending'){ setMsg('上一次探针还在进行中…'); await dockProbeWait(hid, gs); return; }
  }catch(e){}
  setMsg('探针发起中…（给它发一条消息，等它回话）');
  const res = await dockPost('/api/harness/' + encodeURIComponent(hid) + '/probe-reply', {mode:'auto'});
  if (!res.ok || (res.data && res.data.ok === false)){
    const code = res.status;
    if (code === 409){ setMsg('还没开探针（' + ((res.data&&(res.data.error||res.data.how_to_fix))||'需要 AC_REPLY_PROBE_ENABLED=1') + '）——这不是失败，开了才能自动验', false); return; }
    if (code === 400){ setMsg('这台是人工中转型，自动验不了：' + ((res.data && (res.data.error||res.data.reason)) || '') + '——请用「我确认它回了」', true); return; }
    setMsg('发起未成功：' + ((res.data && (res.data.error||res.data.reason)) || ('HTTP ' + code)) + ((res.data && res.data.how_to_fix)?(' · '+res.data.how_to_fix):''), true);
    return;
  }
  await dockProbeWait(hid, res.data);
}
async function dockProbeWait(hid, start){
  const setMsg = (t, bad) => { const e=document.getElementById('capProbeMsg-' + hid); if(e){e.textContent=t; e.style.color = bad?'var(--red,#FF0000)':'';} };
  const deadline = Date.now() + 120000;
  setMsg('探针进行中…（每 5 秒看一眼，最长 120 秒）');
  while (Date.now() < deadline){
    await new Promise(r=>setTimeout(r,5000));
    let d;
    try{ d = await (await fetch('/api/harness/' + encodeURIComponent(hid) + '/probe-reply')).json(); }catch(e){ continue; }
    if (d.state === 'ok'){ setMsg('✓ ' + capReplyText(d)); dockLoadCapability(hid, false); return; }
    if (d.state === 'failed'){ setMsg('✗ 验过但失败：' + (d.why||'探针超时（没收到回执）'), true); dockLoadCapability(hid, false); return; }
  }
  setMsg('✗ 探针超时（没收到回执）', true);
  dockLoadCapability(hid, false);
}
async function dockLoadCapability(hid, humanRelay){
  const box = document.getElementById('dkCapBlock-' + hid);
  const body = _dkDetailsBody(box, true);
  if (!box || !body) return;
  try{
    const r = await fetch('/api/harness/' + encodeURIComponent(hid) + '/capability');
    const d = await r.json();
    if (!r.ok) { body.innerHTML = '<p class="sub">✗ ' + esc(d.error || ('HTTP ' + r.status)) + ((d.how_to_fix)?(' · ' + d.how_to_fix):'') + '</p>'; return; }
    const seg = d.segments || {};
    const segCard = (k, obj) => {
      const o = obj || {};
      const st = String(o.state||'unknown');
      const cls = st==='ok' ? 'dotv ok' : st==='failed' ? 'dotv bad' : 'dotv unknown';
      if (k === 'agent_can_reply'){
        /* 第三段单独处理：状态文案按 228 映射（source 区分），旁挂「验一次/我确认它回了」按钮 */
        const btn = humanRelay
          ? '<button class="btn mini" onclick="dockProbeReply(\'' + esc(hid) + '\',true)">我确认它回了</button>'
          : '<button class="btn mini" onclick="dockProbeReply(\'' + esc(hid) + '\',false)">验一次</button>';
        const badge = (st==='ok' && o.source==='human_confirm') ? '<span class="cap-src src-probed">人工确认</span>' : '';
        return '<div class="peer-row"><div class="pr-top"><span class="' + cls + '"></span><b>④ agent 能回话</b>' +
          '<span class="pt-status ' + (st==='ok'?'done':st==='failed'?'failed':'sent') + '">' + esc(CAP_STATE_TEXT[st]||st) + '</span>' + badge + '</div>' +
          '<div class="pr-sub">' + esc(capReplyText(o)) + '</div>' +
          (o.why && st==='failed' ? '<div class="pr-sub">' + esc(o.why) + '</div>' : '') +
          '<div class="pr-acts">' + btn + '<span class="dock-msg" id="capProbeMsg-' + esc(hid) + '"></span></div></div>';
      }
      return '<div class="peer-row"><div class="pr-top"><span class="' + cls + '"></span><b>' +
        ({card_conformance:'① 声明合规', bridge_config:'② 配置能连', harness_running_bridge:'③ 桥在跑', agent_can_reply:'④ agent 能回话'}[k]||esc(k)) +
        '</b><span class="pt-status ' + (st==='ok'?'done':st==='failed'?'failed':'sent') + '">' + esc(CAP_STATE_TEXT[st]||st) + '</span></div>' +
        (o.why ? '<div class="pr-sub">' + esc(o.why) + '</div>' : '') +
        (o.evidence_scope ? '<div class="pr-sub">' + esc(o.evidence_scope) + '</div>' : '') +
        '</div>';
    };
    const caps = (d.capabilities||[]).map(c =>
      '<div class="pt-row"><b>' + esc(c.display||c.name||'?') + '</b>' +
      '<span class="cap-src ' + esc(CAP_SRC_CLS[c.source]||'src-declared') + '">' + esc(CAP_SRC_TEXT[c.source]||c.source||'声明') + '</span>' +
      '<span class="sub">证据：' + esc(c.evidence||'—') + '</span>' +
      (c.also_in_tools ? '<span class="sub">· 也在工具表</span>' : '') +
      ((c.raw&&c.raw.length>1) ? '<span class="sub">（原始声明：' + esc(c.raw.join(', ')) + '）</span>' : '') +
      '</div>').join('');
    const tools = (d.tools||[]).map(t =>
      '<div class="pt-row"><b>' + esc(t.display||t.name||'?') + '</b>' +
      '<span class="sub"><code>' + esc(t.name||'') + '</code></span>' +
      (t.params_known===false ? '<span class="sub">参数未知（记录里没有，别画成可填表单）</span>' : '') +
      '</div>').join('');
    const probed = (d.probed||[]).map(p => '<span class="sub">· ' + esc(p.kind||'') + ': ' + esc(p.value||'') + '</span>').join(' ');
    body.innerHTML =
      '<p class="lead"><b>' + esc(d.verdict_text||'') + '</b>' +
      (d.connected===true ? '' : '　<span class="sub" style="color:var(--red,#FF0000)">（connected=false——下面的能力与证据<b>不代表已接上、可用、已就绪</b>）</span>') + '</p>' +
      '<div class="peers-list" style="margin:6px 0">' + segCard('bridge_config',seg.bridge_config) + segCard('harness_running_bridge',seg.harness_running_bridge) + segCard('agent_can_reply',seg.agent_can_reply) + '</div>' +
      '<p class="sub" style="margin-top:8px">能力（带来源徽标）</p><div class="peers-list">' + (caps||'<p class="sub">（无）</p>') + '</div>' +
      '<p class="sub" style="margin-top:8px">工具</p><div class="peers-list">' + (tools||'<p class="sub">（无）</p>') + '</div>' +
      (probed ? '<p class="sub" style="margin-top:8px">探测线索</p><p class="sub">' + probed + '</p>' : '') +
      ((d.notes&&d.notes.length) ? '<p class="lead" style="color:var(--red,#FF0000);margin-top:8px">平台注意到的矛盾（原样）</p>' + d.notes.map(n=>'<p class="sub" style="color:var(--red,#FF0000)">· ' + esc(n) + '</p>').join('') : '');
  }catch(e){ body.innerHTML = '<p class="sub">加载失败：' + esc(e.message) + '</p>'; }
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

/* ══ 检查更新（166-2130 契约：只读两接口 + 三态 + 复制命令；不代跑升级）══ */
function dockLoadUpdate(){
  const wrap = document.getElementById('dockUpdateWrap');
  if (!wrap) return;
  wrap.innerHTML =
    '<div class="card"><h3>检查更新</h3>' +
    '<div class="dock-form">' +
    '<div class="dock-form-actions">' +
    '<button class="btn mini" id="verCheckBtn" onclick="dockVerCheck()">检查更新</button>' +
    '<span class="dock-msg" id="verMsg"></span></div>' +
    '<div id="verResult"></div></div>' +
    '<p class="sub">本平台提供的是<b>可回滚的手工升级</b>：查询只读；升级命令在<b>你自己的终端</b>执行，平台不替你改自己机器上的文件。</p>' +
    '</div>';
  /* 页面侧事实（不联网）：当前版本 + 危险提示 notes */
  fetch('/api/version').then(function(r){ return r.json(); }).then(function(d){
    const box = document.getElementById('verResult');
    if (!box) return;
    let html = '<div class="dock-kv">当前版本 <code>' + esc(d.current || '?') + '</code></div>';
    const notes = d.notes || [];
    if (notes.length){
      html += '<div class="dock-err">⚠ 升级前请注意：' + notes.map(esc).join('；') +
        '。此状态下不建议执行升级。</div>';
    }
    box.insertAdjacentHTML('afterbegin', html);
  }).catch(function(){ /* 版本事实拿不到不拦检查按钮 */ });
}
async function dockVerCheck(){
  const btn = document.getElementById('verCheckBtn');
  const msg = document.getElementById('verMsg');
  const box = document.getElementById('verResult');
  if (!box) return;
  if (btn) btn.disabled = true;
  if (msg) msg.textContent = '正在查询最新版本…';
  try {
    const r = await fetch('/api/version/latest');
    const d = await r.json();
    if (msg) msg.textContent = '';
    /* 先清掉旧的检查结果区，保留版本事实行 */
    const old = box.querySelector('.ver-state');
    if (old) old.remove();
    let html = '<div class="ver-state" style="margin-top:8px">';
    if (d.error){
      /* 三态③：查不到 —— 原样显示 error，绝不写「已是最新」 */
      html += '<div class="dock-err">检查失败：' + esc(d.error) + '</div>';
    } else if (d.update_available === false){
      html += '<div class="dock-ok">当前 ' + esc(d.current || '?') + '，已是最新。</div>';
    } else if (d.update_available === true){
      const cmd = (d.howto && d.howto.apply_and_restart) || 'python tools/update.py --apply --restart';
      html += '<div class="dock-warn">当前 ' + esc(d.current || '?') + ' → 最新 ' + esc(d.latest || '?') + '</div>' +
        '<div class="dock-kv" style="margin-left:0"><code id="verCmd" style="user-select:all">' + esc(cmd) + '</code></div>' +
        '<div class="dock-form-actions">' +
        '<button class="btn mini" onclick="dockVerCopy()">复制升级命令</button>' +
        '<span class="dock-msg" id="verCopyMsg"></span></div>' +
        '<p class="sub">在你自己的机器上执行（平台不替用户改自己的文件）。</p>';
    } else {
      html += '<div class="dock-warn2">查不到最新版本信息（未给出结论，不等于已是最新）。可稍后再试。</div>';
    }
    html += '<div class="dock-kv" style="font-size:10.5px">来源 ' + esc(d.source || '—') +
      ' · 查询于 ' + esc(d.checked_at || '') + (d.cached ? '（缓存）' : '') + '</div></div>';
    box.insertAdjacentHTML('beforeend', html);
  } catch (e){
    if (msg) msg.textContent = '';
    const old = box.querySelector('.ver-state');
    if (old) old.remove();
    box.insertAdjacentHTML('beforeend',
      '<div class="ver-state"><div class="dock-err">检查失败：' + esc(e.message || e) + '</div></div>');
  } finally {
    if (btn) btn.disabled = false;
  }
}
function dockVerCopy(){
  const cmd = (document.getElementById('verCmd') || {}).textContent || '';
  const msg = document.getElementById('verCopyMsg');
  const done = ok => { if (msg) msg.textContent = ok ? '✓ 已复制（去你自己的终端执行）' : '✗ 复制失败，请手动选中命令复制'; };
  if (!cmd) return;
  if (navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(cmd).then(function(){ done(true); }).catch(function(){ done(false); });
  } else {
    try {
      const ta = document.createElement('textarea');
      ta.value = cmd; document.body.appendChild(ta); ta.select();
      document.execCommand('copy'); ta.remove(); done(true);
    } catch (e){ done(false); }
  }
}

/* ── 启动 ─────────────────────────────────────────────────────── */
dockMountRegister();
dockLoad();
dockLoadActivation();
