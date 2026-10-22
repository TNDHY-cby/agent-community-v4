/* ══ 流程画布（V79 / DSH 230）══════════════════════════════════
 * 三态分开：可跑(next) / 等待(waiting_approval·依赖未满足) / 卡住(blocked带why)。
 * 纪律：能不能跑只认后端 next/status；预算拦截 409 按"被闸门拦住"黄色告警显示（两条出路），
 * 与 400 失败分样式；页面加载绝不自动 POST run（花钱动作必须用户点）。
 */
'use strict';
const FC_ST_CLS = {planned:'fc-planned', running:'fc-running', waiting_approval:'fc-wait', retry:'fc-wait',
                   routed:'fc-running', done:'fc-done', failed:'fc-failed', blocked:'fc-failed'};
const FC_ST_TEXT = {planned:'待跑', running:'运行中', waiting_approval:'等审批', retry:'等重试',
                    routed:'已重路由', done:'完成', failed:'失败', blocked:'卡住'};
const FC_KIND_TEXT = {task:'任务', subtask:'子任务', review:'评审', approval:'审批', note:'备注'};
let FC_WS = '', FC_DATA = null, FC_RUN = null;

function fcWsId(){
  if (typeof wsId !== 'undefined' && wsId) return wsId;
  const m = location.search.match(/ws(?:_id)?=([^&]+)/);
  return m ? decodeURIComponent(m[1]) : '';
}
function fcToggle(){
  const p = document.getElementById('flowCanvasPanel');
  if (!p) return;
  p.style.display = p.style.display === 'none' ? 'block' : 'none';
  if (p.style.display !== 'none'){ FC_WS = fcWsId(); if (FC_WS) fcLoad(); }
}
function fcRefresh(){ FC_WS = fcWsId(); if (FC_WS) fcLoad(); }

async function fcLoad(){
  const sv = document.getElementById('fcSvg'), st = document.getElementById('fcState');
  if (!sv || !st) return;
  st.textContent = '加载中…';
  try{
    const [w, r] = await Promise.all([
      fetch('/api/workshops/' + encodeURIComponent(FC_WS) + '/workflow').then(x => x.json()),
      fetch('/api/workshops/' + encodeURIComponent(FC_WS) + '/workflow/run').then(x => x.json())
    ]);
    if (w.error){ st.textContent = '✗ ' + w.error; return; }
    FC_DATA = w; FC_RUN = (r && !r.error) ? r : null;
    st.textContent = FC_RUN ? (FC_RUN.why || FC_RUN.state || '') : '执行状态不可用';
    fcRender();
    fcLoadReplayIfOpen();
  }catch(e){ st.textContent = '加载失败：' + e.message; }
}

function fcRender(){
  const svg = document.getElementById('fcSvg'); if (!svg || !FC_DATA) return;
  const nodes = (FC_DATA.nodes || []), edges = (FC_DATA.edges || []);
  const runNodes = {};
  ((FC_RUN && FC_RUN.nodes) || []).forEach(n => { runNodes[String(n.node_id)] = n; });
  const next = (FC_RUN && FC_RUN.next) || null;
  const W = 240, H = 64, GX = 70, GY = 30, x0 = 30, y0 = 26;
  const pos = {};
  function lay(list, depth, center){
    list.forEach((n, i) => {
      const kids = nodes.filter(k => String(k.parent_id) === String(n.node_id));
      const y = kids.length ? center - ((kids.length) * (H + GY)) / 2 + (H + GY) / 2
                            : center + i * 0;
      pos[n.node_id] = {x: x0 + depth * (W + GX), y: y + (kids.length ? 0 : i * (H + GY))};
      lay(kids, depth + 1, pos[n.node_id].y + H / 2 - (H + GY) / 2 + (H + GY) / 2);
    });
  }
  /* 简单分层：父节点竖排，子节点挂在父右侧行上；重叠时向下让位 */
  const tops = nodes.filter(n => !n.parent_id);
  let row = 0;
  const used = {};
  tops.forEach(t => {
    function place(n, depth){
      if (used[n.node_id]) return;
      used[n.node_id] = 1;
      pos[n.node_id] = {x: x0 + depth * (W + GX), y: y0 + row * (H + GY)};
      nodes.filter(k => String(k.parent_id) === String(n.node_id)).forEach(k => place(k, depth + 1));
      row++;
    }
    place(t, 0);
  });
  nodes.forEach(n => { if (!pos[n.node_id]) { pos[n.node_id] = {x: x0, y: y0 + row * (H + GY)}; row++; } });
  const maxDepth = Math.max.apply(null, nodes.map(n => pos[n.node_id].x));
  const maxRow = Math.max.apply(null, nodes.map(n => pos[n.node_id].y));
  let edgeHtml = edges.map(e => {
    const a = pos[e.from], b = pos[e.to]; if (!a || !b) return '';
    const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x, y2 = b.y + H / 2, mx = (x1 + x2) / 2;
    return '<path d="M' + x1 + ' ' + y1 + ' C ' + mx + ' ' + y1 + ', ' + mx + ' ' + y2 + ', ' + x2 + ' ' + y2 +
           '" fill="none" stroke="rgba(255,0,0,.45)" stroke-width="1.2"/>';
  }).join('');
  const nodeHtml = nodes.map(n => {
    const p = pos[n.node_id], rn = runNodes[String(n.node_id)] || {}, s = rn.status || n.status || 'planned';
    const isNext = next && String(next.node_id) === String(n.node_id);
    const cls = FC_ST_CLS[s] || 'fc-planned';
    const safeId = String(n.node_id).replace(/'/g, '');
    return '<g transform="translate(' + p.x + ',' + p.y + ')" style="cursor:pointer" onclick="fcSelect(\'' + safeId + '\')">' +
      '<rect width="' + W + '" height="' + H + '" class="fc-node ' + cls + '"' + (isNext ? ' style="stroke-width:2.5"' : '') + '/>' +
      '<text x="12" y="22" class="fc-t1">' + esc(n.label || n.node_id) + (isNext ? ' ▶' : '') + '</text>' +
      '<text x="12" y="42" class="fc-t2">' + esc(FC_KIND_TEXT[n.kind] || n.kind || '') + ' · ' + esc(FC_ST_TEXT[s] || s) +
      ((rn.retry_count > 0) ? (' · 重试' + rn.retry_count) : '') + '</text></g>';
  }).join('');
  svg.innerHTML = '<style>' +
    '.fc-node{fill:#1A1A1A;stroke:rgba(255,0,0,.45);stroke-width:1.2}' +
    '.fc-node.fc-done{stroke:rgba(143,179,148,.8)}' +
    '.fc-node.fc-running{stroke:#FF0000;stroke-width:2}' +
    '.fc-node.fc-wait{stroke:rgba(255,176,0,.8)}' +
    '.fc-node.fc-failed{stroke:#FF0000;fill:rgba(255,0,0,.10)}' +
    '.fc-t1{fill:#F2C9C9;font-size:12.5px}.fc-t2{fill:#AE7F7F;font-size:10.5px}' +
    '</style>' + edgeHtml + nodeHtml;
  svg.setAttribute('width', Math.max(640, maxDepth + W + 60));
  svg.setAttribute('height', Math.max(300, maxRow + H + 40));
  /* 三态汇总条（可跑 / 等待 / 卡住） */
  const det = document.getElementById('fcDetail');
  if (det && FC_RUN){
    const blocked = (FC_RUN.blocked || []);
    const waiting = nodes.filter(n => { const s = (runNodes[String(n.node_id)] || {}).status; return s === 'waiting_approval' || s === 'retry'; });
    det.innerHTML = '三态：' +
      '<b style="color:#FF3333">可跑</b>：' + (FC_RUN.next ? esc(FC_RUN.next.label || FC_RUN.next.node_id) : '（无——可能等审批或依赖未满足）') +
      '　<b style="color:#FFB000">等待</b>：' + waiting.length + ' 个' +
      '　<b style="color:#FF0000">卡住</b>：' + (blocked.length ? (esc(blocked.join(', ')) + ' — ' + (FC_RUN.why || '')) : '0');
  }
}

function fcSelect(id){
  if (!FC_DATA) return;
  const n = (FC_DATA.nodes || []).find(x => String(x.node_id) === String(id)); if (!n) return;
  const rn = ((FC_RUN && FC_RUN.nodes) || []).find(x => String(x.node_id) === String(id)) || {};
  const s = rn.status || n.status || 'planned';
  const isApproval = String(n.kind) === 'approval';
  const safeId = String(id).replace(/'/g, '');
  let html = '<b>' + esc(n.label || n.node_id) + '</b>（' + esc(FC_KIND_TEXT[n.kind] || n.kind || '') +
    ' · ' + esc(FC_ST_TEXT[s] || '') + '）' + (rn.retry_count > 0 ? (' · 重试 ' + rn.retry_count) : '');
  if (isApproval){
    html += '<div class="dock-form-actions" style="margin-top:6px"><b>审批：</b>' +
      '<button class="btn mini" onclick="fcApprove(\'' + safeId + '\',true)">批准</button>' +
      '<button class="btn mini ghost" onclick="fcApproveReject(\'' + safeId + '\')">驳回（必须填理由）</button>' +
      '<span class="dock-msg" id="fcApMsg"></span></div>';
  }
  if (s === 'failed'){
    html += '<div class="dock-form-actions" style="margin-top:6px">' +
      '<button class="btn mini" onclick="fcNodeDone(\'' + safeId + '\')">人工确认该节点完成</button>' +
      '<span class="dock-msg" id="fcDoneMsg"></span></div>';
  }
  document.getElementById('fcDetail').innerHTML = html;
}

async function fcApprove(id, ok){
  let note = '';
  if (!ok){ note = prompt('驳回理由（必填）：') || ''; if (!note.trim()) return; }
  const msg = document.getElementById('fcApMsg'); if (msg) msg.textContent = '提交中…';
  try{
    const r = await fetch('/api/tasks/' + encodeURIComponent(id) + '/approval',
      {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({decision: ok ? 'approve' : 'reject', note: note})});
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.error){ if (msg) msg.textContent = '审批未成功：' + (d.error || ('HTTP ' + r.status)); return; }
    if (msg) msg.textContent = ok ? '已批准' : '已驳回';
    fcLoad();
  }catch(e){ if (msg) msg.textContent = '审批失败：' + e.message; }
}
function fcApproveReject(id){ fcApprove(id, false); }

async function fcNodeDone(id){
  const msg = document.getElementById('fcDoneMsg'); if (msg) msg.textContent = '提交中…';
  try{
    const r = await fetch('/api/tasks/' + encodeURIComponent(id) + '/workflow-node/done',
      {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ok: true})});
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.error){ if (msg) msg.textContent = '未成功：' + (d.error || ('HTTP ' + r.status)) + (d.how_to_fix ? (' · ' + d.how_to_fix) : ''); return; }
    if (msg) msg.textContent = '已标记，状态：' + (d.state || '?') + (d.routed_to ? ('（重路由到 ' + d.routed_to + '）') : '');
    fcLoad();
  }catch(e){ if (msg) msg.textContent = '失败：' + e.message; }
}

async function fcRun(){
  const btn = document.getElementById('fcRunBtn'), warn = document.getElementById('fcWarn');
  if (!btn) return;
  btn.disabled = true;
  try{
    const r = await fetch('/api/workshops/' + encodeURIComponent(FC_WS) + '/workflow/run',
      {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
    const d = await r.json().catch(() => ({}));
    if (r.status === 409){
      /* 预算拦截=闸门，不是失败：黄告警+两条出路（调高预算 / 停掉） */
      warn.style.display = 'block';
      warn.innerHTML = '⚠ 被预算闸门拦住：' + esc(d.error || d.why || '') + '　出路：' +
        '<a href="javascript:void(0)" onclick="fcBudgetAsk()" style="color:#FFB000">调高预算</a> 或 ' +
        '<a href="javascript:void(0)" onclick="fcStopTask()" style="color:#FFB000">停掉这个任务</a>';
      return;
    }
    if (d.error){
      warn.style.display = 'block';
      warn.textContent = '✗ ' + d.error + (d.how_to_fix ? (' · ' + d.how_to_fix) : '');
      return;
    }
    warn.style.display = 'none';
    if (d.waiting_approval) alert('进入审批等待：请点画布中的审批节点处理。');
    fcLoad();
  }catch(e){
    warn.style.display = 'block';
    warn.textContent = '运行失败：' + e.message;
  }finally{ btn.disabled = false; }
}

/* 预算与停止：按任务记；画布上以后端 next 的节点 id 为最近任务参照，没有就提示去任务侧 */
function _fcTaskRef(){
  return (FC_RUN && FC_RUN.next && FC_RUN.next.node_id) ||
         (FC_DATA && FC_DATA.nodes && FC_DATA.nodes[0] && FC_DATA.nodes[0].node_id) || '';
}
function fcBudgetAsk(){
  const tid = _fcTaskRef();
  if (!tid){ alert('预算按任务记——请先在任务卡片上设置。'); return; }
  const v = prompt('给任务 ' + tid + ' 设预算上限（max_cost，0=取消该项）：');
  if (v === null) return;
  fetch('/api/tasks/' + encodeURIComponent(tid) + '/budget',
    {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({max_cost: Number(v) || 0})})
    .then(r => r.json()).then(d => { alert(d.success ? '预算已更新' : '设置未成功：' + (d.error || '')); fcLoad(); })
    .catch(e => alert('失败：' + e.message));
}
function fcStopTask(){
  const tid = _fcTaskRef();
  if (!tid){ alert('停止按任务记——请先在任务卡片上操作。'); return; }
  if (!confirm('确定停掉任务 ' + tid + '？')) return;
  fetch('/api/tasks/' + encodeURIComponent(tid) + '/stop',
    {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'})
    .then(r => r.json()).then(d => { alert(d.success ? '已请求停止' : '未成功：' + (d.error || '')); fcLoad(); })
    .catch(e => alert('失败：' + e.message));
}

async function fcReset(){
  if (!confirm('重置流程执行状态？（节点定义不动）')) return;
  const res = await fetch('/api/workshops/' + encodeURIComponent(FC_WS) + '/workflow/reset',
    {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
  const d = await res.json().catch(() => ({}));
  alert(d.success ? ('已重置 ' + (d.reset || []).length + ' 个节点') : '重置未成功：' + (d.error || ''));
  fcLoad();
}

/* ══ V84 工作间可被外部发现（DSH 237）════════════════════
 * 默认关；开启后显示可复制的 /.well-known/workshop/<id>.json 链接；
 * 单卡只含 name/status/members 数/nodes 计数/next/gate/trust——不含成员姓名/路径/任务正文（如实转述后端口径）。 */
async function fcDiscoverToggle(){
  const btn = document.getElementById('fcDiscBtn');
  const cur = btn ? btn.textContent : '';
  const on = cur.indexOf('已开启') >= 0;
  if (!confirm(on ? '关闭外部发现？（外部将查不到这张卡）' : '允许外部发现本工作间？（只读卡片：不含成员姓名/路径/任务正文）')) return;
  try{
    const res = await fetch('/api/workshops/' + encodeURIComponent(fcWsId()) + '/discoverable',
      {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({on: !on})});
    const d = await res.json().catch(()=>({}));
    if (!res.ok || d.error){ alert('设置未成功：' + (d.error || ('HTTP ' + res.status)) + (d.how_to_fix ? (' · ' + d.how_to_fix) : '')); return; }
    fcDiscoverRender(!on);
  }catch(e){ alert('失败：' + e.message); }
}
async function fcDiscoverRender(on){
  const btn = document.getElementById('fcDiscBtn');
  if (btn) btn.textContent = on ? '发现：已开启' : '发现：未开启';
  let old = document.getElementById('fcDiscLink');
  if (old) old.remove();
  if (!on) return;
  const link = location.origin + '/.well-known/workshop/' + fcWsId() + '.json';
  const bar = document.createElement('div');
  bar.id = 'fcDiscLink';
  bar.style.cssText = 'padding:6px 12px;border-bottom:1px solid var(--rule);font-size:11.5px;color:var(--muted);word-break:break-all';
  bar.innerHTML = '外部只读卡（含 trust，不含成员姓名/路径/正文）：<code>' + link.replace(/</g,'&lt;') + '</code> ' +
    '<button class="btn mini ghost" onclick="fcCopyDiscover()">复制链接</button>';
  const panel = document.getElementById('flowCanvasPanel');
  const head = panel && panel.firstElementChild;
  if (head && head.nextSibling) head.parentNode.insertBefore(bar, head.nextSibling);
  else if (panel) panel.appendChild(bar);
  try{ const card = await (await fetch('/.well-known/workshop/' + fcWsId() + '.json')).json();
    bar.title = '单卡字段：' + Object.keys(card).join(', ') + '（trust 如实显示）';
  }catch(e){}
}
function fcCopyDiscover(){
  const link = location.origin + '/.well-known/workshop/' + fcWsId() + '.json';
  navigator.clipboard.writeText(link).then(()=>{ alert('已复制：' + link); }).catch(()=>{ prompt('复制下面的链接：', link); });
}
/* 打开画布时如实同步开关状态 */
(function(){
  const _load = fcLoad;
  fcLoad = async function(){
    await _load();
    try{
      const st = await (await fetch('/api/workshops/' + encodeURIComponent(fcWsId()) + '/discoverable', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({on:null})}));
      void st;
    }catch(e){}
    /* 用 well-known 是否列出本间来判定（后端只列开启的）——不做假状态 */
    try{
      const list = await (await fetch('/.well-known/workshops.json')).json();
      const wid = fcWsId();
      const items = (list && (list.workshops || list.items)) || [];
      const on = items.some(w => String(w.workshop_id || w.id || '') === String(wid));
      fcDiscoverRender(on);
    }catch(e){ fcDiscoverRender(false); }
  };
})();

function fcToggleReplay(){
  const p = document.getElementById('fcReplay'); if (!p) return;
  const show = p.style.display === 'none';
  p.style.display = show ? 'block' : 'none';
  if (show) fcLoadReplayIfOpen();
}
function fcLoadReplayIfOpen(){
  const p = document.getElementById('fcReplay');
  if (!p || p.style.display === 'none' || !FC_WS) return;
  const tid = _fcTaskRef();
  if (!tid){ p.innerHTML = '<p class="sub">还没有任务可回放。</p>'; return; }
  fetch('/api/tasks/' + encodeURIComponent(tid) + '/snapshots?limit=50').then(r => r.json()).then(d => {
    const rows = d.snapshots || [];
    if (!rows.length){ p.innerHTML = '<p class="sub">该节点还没有快照。</p>'; return; }
    p.innerHTML = '<b>回放时间轴</b>（节点 ' + esc(tid) + ' · 共 ' + esc(String(d.count)) + ' 条）<div style="margin-top:6px">' +
      rows.map(s => {
        const t = String(s.kind || '');
        if (t === 'truncated') return '<div class="pt-row"><span class="sub" style="color:#FFB000">更早的记录已截断（' + esc(String(s.note || s.count || 'N')) + ' 条）</span></div>';
        return '<div class="pt-row"><span class="pt-status sent">' + esc(t) + '</span><span class="sub">' +
          esc(s.actor || '') + ' · ' + esc(s.at || '') + '</span><span class="sub" style="word-break:break-all">' +
          esc(String(s.detail || s.summary || '').slice(0, 160)) + '</span></div>';
      }).join('') + '</div>';
  }).catch(e => { p.innerHTML = '<p class="sub">回放加载失败：' + esc(e.message) + '</p>'; });
}
