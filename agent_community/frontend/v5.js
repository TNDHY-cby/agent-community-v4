/* v5.js — 工作间对接中心（V116 十一步的界面骨干；2026-10-31 正名：工作间专属，不带大厅级动作）
 *
 * 设计口径（与后端 V116/V117 一致，别在这里另立口径）：
 * - 平台**不判断、不推荐、不代写**；这里只把后端的真实返回照实显示；
 * - 每个动作失败都要显示 `code` / `four`（谁/缺什么/去哪补/怎么做）——不许只弹一句"失败"；
 * - 证据、验收、讨论都按后端强校验走：缺证据就不许 active，L3 一律拒，自由聊天 422。
 */
'use strict';

// 老板 2026-10-29：从工作间页点进来时带 `?ws=<id>`（也兼容 `?ws_id=`）
const _QP = new URLSearchParams(location.search);
const WANT_WS = (_QP.get('ws') || _QP.get('ws_id') || '').trim();

const S = { ws: '', name: '', list: [], mind: '', path: '', members: [], slots: [] };

function $(id) { return document.getElementById(id); }
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
  });
}
function say(id, text, kind) {
  const el = $(id);
  if (!el) return;
  el.textContent = text || '';
  el.className = 'msg' + (kind ? ' ' + kind : '');
}
function banner(kind, html) {
  return '<div class="banner ' + kind + '">' + html + '</div>';
}
function fourHtml(four) {
  if (!four) return '';
  return '<div class="msg">谁：' + esc(four.who) + '　缺什么：' + esc(four.what) +
    '<br>去哪补：' + esc(four.where) + '　怎么做：' + esc(four.how) + '</div>';
}
function errHtml(d, status) {
  const code = d && d.code ? d.code : ('HTTP ' + status);
  let h = '<div class="bad">✗ ' + esc(code) + '：' + esc((d && d.error) || '请求失败') + '</div>';
  if (d && d.four) h += fourHtml(d.four);
  if (d && d.errors && d.errors.length) {
    h += '<div class="msg">' + d.errors.slice(0, 5).map(function (e) {
      return esc(e.code) + ' ' + esc(e.field) + '：' + esc(e.message) +
        (e.fix ? '（' + esc(e.fix) + '）' : '');
    }).join('<br>') + '</div>';
  }
  if (d && d.next) h += '<div class="msg">下一步：' + esc(d.next) + '</div>';
  return h;
}

async function api(method, path, body) {
  const opt = { method: method, headers: {} };
  if (body !== undefined) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  let res, data;
  try {
    res = await fetch(path, opt);
  } catch (e) {
    return { ok: false, status: 0, data: { code: 'NET', error: '连不上平台（' + e + '）' } };
  }
  const text = await res.text();
  try { data = text ? JSON.parse(text) : {}; } catch (e) { data = { raw: text.slice(0, 300) }; }
  return { ok: res.ok, status: res.status, data: data };
}

/* ── 0. 工作间 ─────────────────────────────────────────────── */
async function loadWs() {
  const r = await api('GET', '/api/workshops');
  if (!r.ok) {
    /* ★2026-10-31（老板）：拉不到列表就等于「不知道有哪些工作间」——
       以前这里静默过去，页面就退化成一张看不出绑了谁的通用页；现在明说。 */
    say('wsMsg', '拉不到工作间列表：' +
      ((r.data && (r.data.error || r.data.code)) || ('HTTP ' + r.status)) + ' —— 刷新重试', 'bad');
    applyWsScope();
    return;
  }
  const list = (r.data && r.data.workshops) || [];
  S.list = list;   /* 名字/状态缓一份：铭牌、顶栏读数、换间都要用 */
  const sel = $('wsSel');
  sel.innerHTML = '<option value="">（未选工作间 —— 从工作间页进入会自动选中）</option>' +
    (list.map(function (w) {
      return '<option value="' + esc(w.workshop_id) + '">' + esc(w.name || w.workshop_id) +
        '（' + esc(w.workshop_id) + ' · ' + esc(w.status || '?') + '）</option>';
    }).join('') || '<option value="">（还没有工作间）</option>');
  const want = S.ws || WANT_WS;
  if (list.length && want) {
    const hit = list.some(function (w) { return w.workshop_id === want; });
    sel.value = hit ? want : '';
    if (!hit) say('wsMsg', 'URL 里的工作间 ' + want + ' 不存在（或已删）—— 请在下面下拉里重选', 'warn');
    if (hit) pickWs();
  }
  else if (!list.length) say('wsMsg', '还没有工作间 —— 上面填名字和路径，点「建工作间 + 建心智根」', 'warn');
  /* ★2026-10-30（老板「只能从工作间进入」）：URL 没带 ?ws= ⇒ **不自动挑第一个**。
     自动挑等于"随便给一个工作间就让你派活/验收"，正是老板要禁的那件事。
     这时只留 UNIT-00 让人自己选（或从工作间页进来）。 */
  applyWsScope();   /* 没有工作间语境时收起 01–09，只留选间卡 */
}

function pickWs() {
  S.ws = $('wsSel').value;
  const meta = (S.list || []).filter(function (w) { return w.workshop_id === S.ws; })[0] || null;
  S.name = meta ? (meta.name || '') : '';
  applyWsScope();
  loadWsDetail();
  loadRoster2();
  /* fillConsoleContext 会把 agent_key 填进 leadKey —— 拉人列表要等它落定再预填 */
  fillConsoleContext().then(loadLeadPull);
  loadLibDir();
  loadTasks();
  loadAilp();
}

/* 本间铭牌 / 顶栏读数 / 标签页标题 —— 一处算、三处显，别各算一套 */
function paintIdentity() {
  const has = !!S.ws;
  const name = S.name || S.ws;
  const hw = $('hdrWs');
  if (hw) {
    hw.textContent = has ? ('工作间 ' + name) : '未选工作间';
    hw.dataset.led = has ? 'ok' : 'off';
    hw.title = has ? ('本页只干这个工作间：' + S.ws + '（换间请回工作间列表）')
                   : '还没绑定工作间 —— 从工作间页的「本间对接中心」进来会自动带上';
  }
  const chip = $('mhWs');
  if (chip) {
    chip.dataset.led = has ? 'ok' : 'off';
    chip.innerHTML = has
      ? '本间 <b>' + esc(name) + '</b>' + (S.name ? '<span style="color:var(--dim);margin-left:8px">' + esc(S.ws) + '</span>' : '')
      : '未绑定工作间 —— 从工作间页的「本间对接中心」进来';
  }
  document.title = has ? (name + ' · 工作间对接中心') : '工作间对接中心 · 从接组长到验收';

  /* ★2026-10-31（老板「无法从工作间对接中心回到刚才的工作间」）：
     顶栏回程键与导航「工作间」**都要带 ?ws_id=** —— 原来那只跳 /workshop.html（列表页），
     等于把人从本间丢回一串工作间的清单里。没绑定工作间时回程键藏起来，导航回列表。 */
  const back = $('backWs');
  if (back) {
    back.href = has ? ('/workshop.html?ws_id=' + encodeURIComponent(S.ws)) : '/';
    back.hidden = !has;
    back.title = has ? ('回本间工作页：' + name + '（讨论与工作都在那儿）') : '';
  }
  const nav = $('navWs');
  if (nav) {
    nav.href = has ? ('/workshop.html?ws_id=' + encodeURIComponent(S.ws)) : '/workshop.html';
    nav.title = has ? ('回本间工作页：' + name) : '工作间列表（还没绑定工作间）';
  }
  /* ★2026-10-31（老板拍板「切成本间模式」）：顶栏「对接中心」带 ?ws= ——
     从本间进去就是本间的对接台（激活/送话只对本间），不是全局总控。 */
  const dock = $('navDock');
  if (dock) {
    dock.href = has ? ('/docking.html?ws=' + encodeURIComponent(S.ws)) : '/docking.html';
    dock.title = has ? ('本间对接中心 · ' + name + '（登记/激活/送话，只对本间）') : '对接中心 · 全局登记';
  }
}

/* ★2026-10-30（老板）：对接中心只能从工作间进入；2026-10-31 补充：本页是工作间专属的对接中心。
   ★2026-10-31（老板，发火那轮说透了）：这张页是**工作间专属的对接中心** —— 从工作间进来后
   只干本间的活：建工作间/换工作间是大厅级动作，**不属于本页**，选中后 UNIT-00 一并收起；
   没有工作间语境时（直接敲地址）才留 UNIT-00 做兜底选间。
   做法：用**类**切换而不是写 inline display —— 各卡自己的 display 保持原样，
   一旦选中工作间就原封不动地回来。 */
function applyWsScope() {
  const has = !!S.ws;
  document.querySelectorAll('.card[id]').forEach(function (el) {
    const id = el.id;
    if (id === 'noWsNotice') return;
    if (!/^sec\d+$/.test(id) && !/^sec[A-Z]/.test(id)) return;
    /* secLib（资料库指向）也是本间动作 —— 此前漏了它，没选工作间时它会裸奔（7f7a199 引入的洞） */
    /* 有工作间 ⇒ 本间模式：连 UNIT-00（选间/建间）一起收；没有 ⇒ 只留 UNIT-00 兜底 */
    el.classList.toggle('ws-out', id === 'sec0' ? has : !has);
  });
  const howto = document.querySelector('.howto');
  if (howto) howto.classList.toggle('ws-out', !has);
  document.querySelectorAll('.bay-rail a[data-target]').forEach(function (a) {
    const t = a.getAttribute('data-target');
    a.classList.toggle('ws-out', t === 'sec0' ? has : !has);
  });
  const n = $('noWsNotice');
  if (n) n.style.display = has ? 'none' : '';
  paintIdentity();   /* 顶栏读数 + 刊头铭牌 + 标签页标题：本间是谁，一处算三处显 */
}

// 从工作间页进来：把**本间已知的东西**填好（路径/方向/心智根键），不让用户手抄
async function fillConsoleContext() {
  if (!S.ws) return;
  const r = await api('GET', '/api/workshop/' + encodeURIComponent(S.ws) + '/console-context');
  if (!r.ok) return;
  const d = r.data || {};
  S.path = d.workshop_path || S.path;
  S.mind = d.mind_path || S.mind;
  S.libPath = (typeof d.library_path === 'string') ? d.library_path : '';  /* 资料库指向：空 = 默认 */
  const dir = d.direction || {};
  if (dir.brief && !$('dirBrief').value) $('dirBrief').value = dir.brief;
  if ((dir.enum || []).length && !$('dirEnum').value) $('dirEnum').value = dir.enum.join(',');
  if (d.agent_key && !$('leadKey').value) $('leadKey').value = d.agent_key;
  const hub = $('wsMsg');
  if (hub) {
    const bits = [];
    if (d.workshop_path) bits.push('根B ' + d.workshop_path);
    if (d.mind_path) bits.push('根A ' + d.mind_path);
    bits.push('名单 ' + ((d.members || {}).total || 0) + ' 人（接上 ' + ((d.members || {}).active || 0) + '）');
    if (!d.manifest_ok) bits.push('manifest 不合法');
    hub.textContent = bits.join(' · ');
    hub.className = 'msg' + (d.manifest_ok ? '' : ' warn');
    if ((d.notes || []).length) hub.title = d.notes.join('；');
  }
}

async function loadWsDetail() {
  if (!S.ws) return;
  const r = await api('GET', '/api/workshop/' + encodeURIComponent(S.ws));
  if (!r.ok) return;
  S.path = r.data.workspace_dir || r.data.workshop_path || '';
  const d = r.data.direction || {};
  if (d.brief) $('dirBrief').value = d.brief;
  if (d.enum && d.enum.length) $('dirEnum').value = d.enum.join(',');
}

async function createWs() {
  const name = $('newName').value.trim();
  const path = $('newPath').value.trim();
  const mind = $('newMind').value.trim();
  if (!name || !path) { say('mkOut', '名字和路径都要填', 'bad'); return; }
  say('mkOut', '正在建工作间…');
  const r = await api('POST', '/api/workshop', { name: name, hall_content: name, workshop_path: path });
  if (!r.ok) { $('mkOut').innerHTML = errHtml(r.data, r.status); return; }
  const wid = r.data.workshop_id;
  S.ws = wid;
  let h = '<div class="ok">✓ 工作间 ' + esc(wid) + ' 建好了（根B：' + esc(path) + '）</div>';
  if (mind) {
    const mk = await api('POST', '/api/mind/' + encodeURIComponent(mind), { display_name: name });
    if (mk.ok) {
      S.mind = mk.data.mind_path;
      h += '<div class="ok">✓ 心智根建好：' + esc(S.mind) + '（建了 ' +
        ((mk.data.created || []).length) + ' 项）</div>';
      $('leadKey').value = mind;
      $('newMind').value = mind;
      const mf = await api('POST', '/api/workshop/' + encodeURIComponent(wid) + '/manifest',
        { mind_path: S.mind, entry_points: ['_lead/建模.md'], task_focus: name });
      if (mf.ok) h += '<div class="ok">✓ manifest 写入：' + esc(mf.data.manifest_hash || '') + '</div>';
      else h += errHtml(mf.data, mf.status) +
        '<div class="msg">提示：manifest 要求入口文件真实存在 —— 先让组长建 <code>_lead/建模.md</code>，再回来写一次。</div>';
    } else {
      h += errHtml(mk.data, mk.status);
    }
  }
  $('mkOut').innerHTML = h;
  await loadWs();
}

/* ── 1. 方向 + 组长候选 ─────────────────────────────────────── */
async function saveDirection() {
  if (!S.ws) { say('dirMsg', '先选工作间', 'bad'); return; }
  const brief = $('dirBrief').value.trim();
  const arr = $('dirEnum').value.split(/[,，\s]+/).filter(Boolean);
  if (!brief || !arr.length) { say('dirMsg', '方向长文和下拉都要填（平台不体检内容，但不许空）', 'bad'); return; }
  const r = await api('POST', '/api/workshop/' + encodeURIComponent(S.ws),
    { direction: { brief: brief, enum: arr } });
  if (r.ok) say('dirMsg', '✓ 方向已存（' + arr.join('、') + '）', 'ok');
  else $('dirMsg').innerHTML = errHtml(r.data, r.status);
}

async function loadRoster() {
  const dir = $('dirEnum').value.split(/[,，\s]+/).filter(Boolean)[0] || '';
  const r = await api('GET', '/api/lead-roster?direction=' + encodeURIComponent(dir));
  if (!r.ok) { $('rosterOut').innerHTML = errHtml(r.data, r.status); return; }
  const d = r.data;
  const note = $('rosterNote');
  if (d.note) { note.hidden = false; note.textContent = d.note; } else { note.hidden = true; }
  let h = '<div class="msg">' + esc(d.sort_note || '') + '</div>';
  if (!d.candidates.length) {
    h += banner('warn', '现在没有能当组长的 harness。组长门槛：<b>能写文件</b> 且 <b>能把回执送回来</b>' +
      '（协议或桥）。下面是被排除的与原因；可以让它报 <code>tools</code>，或点「实测」写一次盘。');
  }
  h += '<div class="row"><button onclick="loadTooling()">看所有 harness 的实测状态</button></div><div id="toolOut"></div>';
  h += '<table><tr><th>harness</th><th>模型</th><th>方向</th><th>能不能当组长</th><th></th></tr>' +
    d.candidates.map(function (c) {
      return '<tr><td>' + esc(c.harness_id) + '<div class="msg">' + esc(c.harness_name) + '</div></td>' +
        '<td>' + esc(c.model) + '</td><td>' + esc((c.directions || []).join('、') || '—') + '</td>' +
        '<td class="ok">能</td><td><button class="p" onclick="chooseLead(\'' + esc(c.harness_id) + '\')">选它当组长</button></td></tr>';
    }).join('') + '</table>';
  if ((d.excluded || []).length) {
    h += '<div class="msg" style="margin-top:8px">被排除（' + d.excluded.length + '）：<br>' +
      d.excluded.map(function (x) {
        return '· ' + esc(x.harness_id) + ' —— ' + esc(x.why);
      }).join('<br>') + '</div>';
  }
  $('rosterOut').innerHTML = h;
}

async function loadTooling() {
  const r = await api('GET', '/api/harness-tooling');
  if (!r.ok) { $('toolOut').innerHTML = errHtml(r.data, r.status); return; }
  $('toolOut').innerHTML = '<table><tr><th>harness</th><th>fs_write</th><th>来源</th><th>唤醒</th>' +
    '<th>协议/桥</th><th>能当组长</th><th></th></tr>' +
    r.data.items.map(function (it) {
      const t = it.tooling || {};
      const can = it.lead_eligible ? '<span class="ok">能</span>' : '<span class="bad">不能</span>';
      return '<tr><td>' + esc(it.harness_id) + '</td><td>' + esc(String(t.fs_write)) +
        '</td><td>' + esc(t.fs_write_source || '声明') + '</td><td>' + esc(t.wakeup_method || '') +
        '</td><td>' + (t.protocol_call ? '协议 ' : '') + (t.bridge_poll ? '桥' : '') +
        '</td><td>' + can + '</td><td><button onclick="probe(\'' + esc(it.harness_id) +
        '\')">实测写一次盘</button></td></tr>';
    }).join('') + '</table>' +
    '<div class="msg">「实测」是平台在自己的数据目录真写一次再读回；它只证明<b>平台侧落点可写</b>，' +
    '不等于那个 agent 自己有 fs 工具。</div>';
}

async function probe(hid) {
  const r = await api('POST', '/api/harness/' + encodeURIComponent(hid) + '/probe-tooling', {});
  if (!r.ok) { $('toolOut').innerHTML = errHtml(r.data, r.status); return; }
  await loadTooling();
  await loadRoster();
}

async function chooseLead(hid) {
  if (!S.ws) { say('rosterNote', '先选工作间', 'bad'); return; }
  const r = await api('POST', '/api/workshop/' + encodeURIComponent(S.ws) + '/lead', { harness_id: hid });
  if (!r.ok) {
    $('rosterOut').innerHTML = errHtml(r.data, r.status) + $('rosterOut').innerHTML;
    return;
  }
  say('kitMsg', '✓ 组长已定（' + hid + '），状态 = ' + r.data.status + '；下面生成对接单', 'ok');
  await loadRoster2();
  await leadKit();
}

/* ── 2. 组长拉人列表（老板 10-31）──────────────────────────── */
/* 打开对接中心就自动按本间预填：/api/lead-roster 列能当组长的壳，
   /lead-kit 把工作间路径**和桥参数**一起拼进对接单（D后端-62：kit 自带「## 桥参数」段，
   不再分两块显示）—— 每行按该壳自己的 harness_id 拉一份，复制出去直接能用。
   issue_token=0：列表里恒不带 A 档 token（要签走下面手动那排）。 */
async function loadLeadPull() {
  const box = $('leadPullList');
  if (!box || !S.ws) return;
  const rr = await api('GET', '/api/lead-roster');
  if (!rr.ok) { box.innerHTML = errHtml(rr.data, rr.status); return; }
  const d = rr.data || {};
  const cands = d.candidates || [];
  const excluded = d.excluded || [];
  if (!cands.length) {
    let h = '<div class="msg warn">现在没有能当组长的壳（组长必须能写文件 + 能把回执送回来）。</div>';
    if (excluded.length) {
      h += '<details style="margin:4px 0 8px"><summary style="cursor:pointer;color:var(--dim);font-size:11.5px">' +
        '为什么它们不行（' + excluded.length + ' 个）</summary><ul style="margin:4px 0;padding-left:18px;font-size:11.5px">' +
        excluded.map(function (x) {
          return '<li><code>' + esc(x.harness_id || '') + '</code> —— ' + esc(x.why || '') + '</li>';
        }).join('') + '</ul></details>';
    }
    if (d.note) h += '<div class="msg">' + esc(d.note) + '</div>';
    box.innerHTML = h;
    return;
  }
  const key = $('leadKey').value.trim();
  const kits = await Promise.all(cands.map(function (c) {
    return api('GET', '/api/workshop/' + encodeURIComponent(S.ws) +
      '/lead-kit?format=json&issue_token=0&harness_id=' + encodeURIComponent(c.harness_id) +
      (key ? '&agent_key=' + encodeURIComponent(key) : ''));
  }));
  S.leadPulls = cands.map(function (c, i) {
    const r = kits[i] || {};
    const kd = r.ok ? (r.data || {}) : {};
    return { c: c, kit: kd.kit || '', missing: kd.bridge_missing || [],
             tpl: kd.bridge_template || '', err: r.ok ? null : (r.data || {}) };
  });
  const wsName = S.name || S.ws;
  box.innerHTML =
    '<div class="msg">共 ' + cands.length + ' 个能当组长的壳。每行的对接单已按本间「' + esc(wsName) +
    '」预填好 —— <b>含桥参数（路径平台填好，别手改）</b>；点「复制对接单」，去它的外部会话粘贴发送。</div>' +
    S.leadPulls.map(function (p, i) {
      const c = p.c, t = c.tooling || {};
      const bits = [c.harness_id, c.model, t.wakeup_method ? ('唤醒:' + t.wakeup_method) : '']
        .filter(Boolean).join(' · ');
      const miss = p.missing.length
        ? '<span class="msg warn" style="flex-basis:100%">还缺：' + esc(p.missing.join('、')) +
          '（平台不瞎填 —— 定下组长后重拉，或手填这一项）</span>' : '';
      const bad = p.err ? '<span class="msg" style="flex-basis:100%;color:var(--bad)">对接单没拼出来：' +
        esc(p.err.error || p.err.code || '未知原因') + '</span>' : '';
      return '<div class="leadpull-row" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;' +
        'border:1px solid var(--dim);padding:6px 8px;margin:6px 0">' +
        '<b style="font-size:12px">' + esc(c.harness_name || c.harness_id) + '</b>' +
        '<span class="msg" style="flex:1;min-width:180px">' + esc(bits) + '</span>' +
        (p.kit ? '<button class="p" onclick="copyLeadPull(this,' + i + ')">复制对接单拉它</button>' : '') +
        (p.kit ? '<details style="flex-basis:100%"><summary style="cursor:pointer;color:var(--dim);' +
          'font-size:11px">看对接单全文</summary><pre style="white-space:pre-wrap;font-size:11px">' +
          esc(p.kit) + '</pre></details>' : '') +
        miss + bad + '</div>';
    }).join('') +
    ((d.note) ? '<div class="msg">' + esc(d.note) + '</div>' : '');
}

function copyLeadPull(btn, i) {
  const p = (S.leadPulls || [])[i];
  const text = p && p.kit;
  if (!text) { say('kitMsg', '这份对接单没拼出来 —— 看行内原因', 'bad'); return; }
  if (navigator.clipboard) navigator.clipboard.writeText(text);
  else {
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); document.body.removeChild(ta);
  }
  if (btn) {
    const old = btn.textContent;
    btn.textContent = '✓ 已复制 —— 去粘贴给组长';
    setTimeout(function () { btn.textContent = old; }, 2500);
  }
}

/* ── 2b. 组长对接单（手动版：A 档 token 在这里签）──────────── */
async function leadKit() {
  if (!S.ws) { say('kitMsg', '先选工作间', 'bad'); return; }
  const key = $('leadKey').value.trim() || 'lead';
  const tok = $('leadTok').value === '1' ? 1 : 0;
  const r = await api('GET', '/api/workshop/' + encodeURIComponent(S.ws) + '/lead-kit?agent_key=' +
    encodeURIComponent(key) + '&issue_token=' + tok + '&format=json');
  if (!r.ok) { $('kitMsg').innerHTML = errHtml(r.data, r.status); return; }
  const out = $('leadKitOut');
  out.hidden = false;
  out.textContent = r.data.kit;
  say('kitMsg', '✓ 拼好了（' + (r.data.has_token ? '含 A 档一次性 token，别转贴' : '不含 token') + '）', 'ok');
  $('leadLabel').value = r.data.session_label || '';
}

function copyEl(id) {
  const el = $(id);
  if (!el || el.hidden) { return; }
  const text = el.textContent || '';
  if (navigator.clipboard) navigator.clipboard.writeText(text);
  else {
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); document.body.removeChild(ta);
  }
}

/* ── 2b. 平台自己接组长进来（★2026-11-01 老板：「组长接进工作间很麻烦」）────
   有协议通道的组长（A2A 等）：平台自己发一条探针、自己看回执，
   验过就记 `protocol_ok`（来源 **platform-observed**，不是人打的勾）。
   不再需要：复制对接单 → 去对方会话粘贴 → 回来打勾 → 手写覆盖范围。 */
async function autoProtocolProbe(btn) {
  const msg = $('ppMsg'), out = $('ppOut');
  if (!S.ws) { if (msg) msg.textContent = '还没选中工作间'; return; }
  if (btn) btn.disabled = true;
  if (msg) msg.textContent = '找本间的组长工位…';
  if (out) out.innerHTML = '';
  try {
    /* 1) 组长是谁：**v5 名单**里 slot_dir === '_lead' 的那个工位。
       ⚠️ 别用 /members-status —— 那个端点回的是**旧名单**（WorkshopMember，只有 m0/m1，
       没有 slot_dir / member_seq），拿它永远找不到组长（实测踩过）。v5 名单在 /roster。 */
    const st = await api('GET', '/api/workshop/' + encodeURIComponent(S.ws) + '/roster');
    const members = (st.ok && st.data && st.data.members) || [];
    const lead = members.find(function (m) { return String(m.slot_dir || '') === '_lead'; });
    if (!lead) {
      if (msg) msg.textContent = '这间还没选组长 —— 先在上面的列表里点「选它当组长」';
      return;
    }
    if (msg) msg.textContent = '平台发探针、等它回执（最多 25 秒）…';
    // 2) 平台自验（后端发 + 后端看回执 + 后端记 platform-observed）
    const r = await api('POST', '/api/workshop/' + encodeURIComponent(S.ws) +
      '/members/' + encodeURIComponent(lead.member_seq) + '/protocol-probe', { wait: 25 });
    const d = r.data || {};
    if (out) {
      out.innerHTML = (r.ok && d.ok)
        ? ('<div class="msg" style="color:var(--ok)">✓ 平台自己接上了：' + esc(d.detail || '') +
           '<br>证据来源：<b>platform-observed</b>（平台自己看到它把任务做完了，不是人打的勾）' +
           (d.task_id ? ('<br>对端任务：<code>' + esc(d.task_id) + '</code>') : '') + '</div>')
        : ('<div class="msg" style="color:var(--bad)">✗ 没接上：' + esc(d.error || d.why || ('HTTP ' + r.status)) +
           (d.detail ? ('<br>' + esc(d.detail)) : '') +
           (d.next ? ('<br>下一步：' + esc(d.next)) : '') + '</div>');
    }
    if (msg) msg.textContent = (r.ok && d.ok) ? '已接上' : '没接上';
    if (r.ok && d.ok) { try { loadRoster(); loadLeadPull(); } catch (e) { /* 刷新失败不影响判定 */ } }
  } catch (e) {
    if (msg) msg.textContent = '出错：' + (e && e.message ? e.message : e);
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* ── 3. 接组长（证据）─────────────────────────────────────── */
/* 名单镜像回显（D后端-56 §2）：写名单的响应都带 mirror 字段，照实带出，不吞。
   {ok:true,path,members} / {ok:false,why}；镜像不是真源，失败也不阻断主库。 */
function mirrorLine(m) {
  if (!m) return '';
  if (m.ok) return '<div class="msg">名单镜像：已写 <code>' + esc(m.path || '') + '</code>' +
    (m.members != null ? '（' + esc(String(m.members)) + ' 人）' : '') + '</div>';
  return '<div class="msg warn">名单镜像：没写（' + esc(m.why || '后端没给原因') + '）—— 名单主库不受影响。</div>';
}

/* ── 3b. 谁验的（D后端-68/69）：platform-observed 与 user-check 必须长得不一样 ──
   ⚠ 单看时间戳分不出来 —— 打勾也留 *_at；区分点只有 evidence_sources。
   打勾的不许写成"已验证"；什么都没有就明写「证据 0 项」，不装模作样。 */
function evidenceSrcHTML(m) {
  const src = (m && m.evidence_sources) || {};
  const at = (m && m.evidence_at) || {};
  const keys = Object.keys(src);
  if (!keys.length) return '<span class="msg">证据 0 项（平台没观测到）</span>';
  /* evidence_at 的键带 _at 后缀（如 protocol_ok_at），对不上的兜底找一遍 */
  const when = function (k) { return at[k + '_at'] || at[k] || ''; };
  const plat = keys.filter(function (k) { return src[k] === 'platform-observed'; });
  const user = keys.filter(function (k) { return src[k] !== 'platform-observed'; });
  let h = '';
  if (plat.length) {
    h += '<span class="ok">✓ 平台验的</span><div class="msg">' +
      plat.map(function (k) { return esc(k) + (when(k) ? ' · ' + esc(when(k)) : ''); }).join('<br>') + '</div>';
  }
  if (user.length) {
    h += (h ? '<br>' : '') + '<span class="warn">你打的勾</span><div class="msg">' +
      user.map(function (k) { return esc(k) + (when(k) ? ' · ' + esc(when(k)) : ''); }).join('<br>') +
      '（人工申报 —— 平台没观测到）</div>';
  }
  return h;
}

function currentLead() {
  return (S.members || []).filter(function (m) { return m.slot_dir === '_lead'; })[0] || null;
}

async function markEvidence() {
  const m = currentLead();
  if (!m) { say('evMsg', '还没有组长工位 —— 先在「方向 + 组长」里点「选它当组长」', 'bad'); return; }
  const scope = $('evScope').value.trim();
  if (!scope) { say('evMsg', '「覆盖范围」必填：写清覆盖到哪一段（不写就永远到不了 active）', 'bad'); return; }
  const body = {
    tier: $('leadTier').value,
    paste_seen: $('evPaste').checked,
    press_ok: $('evPress').checked,
    rename_seen: $('evRename').checked,
    protocol_ok: $('evProto').checked,
    bridge_ok: $('evBridge').checked,
    /* D后端-63：B 档 = 桥通 **且** 真从 _inbox 取走一次 ⇒ active；只通只记 acked */
    mailbox_ok: $('evMailbox') ? $('evMailbox').checked : false,
    verified_scope: scope,
    who: 'user'
  };
  /* D后端-67 §2：`actual_name` 可选 —— 填了平台才比（只提醒不拦），不填就不许显示成"已核对" */
  if ($('evActual') && $('evActual').value.trim()) body.actual_name = $('evActual').value.trim();
  const r = await api('POST', '/api/workshop/' + encodeURIComponent(S.ws) + '/members/' +
    m.member_seq + '/evidence', body);
  if (!r.ok) { $('evOut').innerHTML = errHtml(r.data, r.status); return; }
  const d = r.data;
  let h = '';
  if (d.state === 'active') {
    h += banner('ok', '✓ 已接上（active）：' + esc(d.verified_scope || ''));
  } else {
    const miss = (d.need && d.need.missing_evidence) || [];
    h += banner('warn', '还没到 active，现在记的是 <b>' + esc(d.state) + '</b>。' +
      '平台不会替你打勾（V116 §3：未打勾最高只记 acked）。' +
      (d.need ? '<br>还缺：' + esc(miss.join('、') || '—') +
        '　覆盖范围：' + esc(d.need.verified_scope || '') : '') +
      /* D后端-63：B 档「只通没取件」最高只记 acked —— 这句要说清，否则会被当成 bug */
      (miss.indexOf('mailbox_ok') >= 0
        ? '<br><b>B 档：桥通了但还没真从 <code>_inbox</code> 取走一次</b> —— 老板口径「只通没有取件，最高只记 acked」；' +
          '让它真取一次件、勾上「B 档真从 _inbox 取走一次」再来。'
        : ''));
  }
  /* 名字核对：只提醒，不改状态（老板选项："填了就比，不一致只提醒"） */
  const nc = d.name_check || {};
  if (nc.expected || nc.actual) {
    h += nc.match
      ? '<div class="msg">名字对得上：<code>' + esc(nc.actual || '') + '</code> ＝ 平台给的 <code>' +
        esc(nc.expected || '') + '</code></div>'
      : banner('warn', '⚠ 名字对不上：名单上叫「' + esc(nc.expected || '') + '」，你说它叫「' +
          esc(nc.actual || '') + '」。<br>' + esc(nc.note || '') +
          '<br><b>只提醒，不拦</b> —— 状态和派活都不受影响（改名很常见）。');
  } else {
    h += '<div class="msg">这个名字是你给它的，<b>平台没核过它真实叫什么</b>。' +
      '接人时在「它现在实际叫什么」里填一下，平台才会跟名单比（不一致也只提醒）。</div>';
  }
  $('evOut').innerHTML = h + mirrorLine(d.mirror);
  say('evMsg', '档位：' + (d.tier || '') + '　状态：' + (d.state || ''), d.state === 'active' ? 'ok' : 'warn');
  await loadRoster2();
}

/* ── 3c. B 档平台自验（D后端-69）：平台自己写测试消息、自己看它被取走没有 ──
   取件发生在两次点击**之间**：第一次新发（拿 probe_id），之后每次都带同一条 probe_id 再看 ——
   每次都新发就永远看不到「被取走」。没取走 ⇒ 证据一个字不记（PROBE_NOT_PICKED，这不是失败）。 */
async function bridgeProbe(seq) {
  if (!S.ws) return;
  if (!seq) seq = (currentLead() || {}).member_seq;
  if (!seq) { say('evMsg', '还没有工位可查 —— 先把名单建起来', 'bad'); return; }
  const btn = $('probeBtn');
  if (btn) { btn.disabled = true; btn.textContent = '正在看邮箱…'; }
  S.probeIds = S.probeIds || {};
  const body = {};
  if (S.probeIds[seq]) body.probe_id = S.probeIds[seq];
  const r = await api('POST', '/api/workshop/' + encodeURIComponent(S.ws) +
    '/members/' + encodeURIComponent(seq) + '/bridge-probe', body);
  if (btn) { btn.disabled = false; btn.textContent = '检查取件（平台自验）'; }
  const out = $('probeOut');
  if (!out) return;
  const d = r.data || {};
  const p = d.probe || {};
  const pid = p.id || d.probe_id || '';
  if (pid) { S.probeIds[seq] = pid; if (btn) btn.title = 'probe_id=' + pid; }
  /* 没取走（409）：显示 probe.reason / probe.advice，保留 probe_id 等「再看同一条」 */
  if (!r.ok && d.code === 'PROBE_NOT_PICKED') {
    out.innerHTML = banner('warn', '⏳ 还没看到它取走 —— <b>证据一个字没记</b>' +
      '（这不是失败：取件发生在两次点击之间）。等一会儿<b>再点一次</b>（会带同一条 probe_id）。') +
      '<div class="msg">probe：<code>' + esc(pid || '—') + '</code></div>' +
      (p.reason ? '<div class="msg">现状：' + esc(p.reason) + '</div>' : '') +
      (p.advice ? '<div class="msg">建议：' + esc(p.advice) + '</div>' : '') +
      (p.inbox ? '<div class="msg">邮箱：<code>' + esc(p.inbox) + '</code>' +
        (p.inbox_guessed ? '（路径是<b>按工位名推的</b> —— 同岗位多人会共用；要精确到人，桥的隔间里写清邮箱）' : '') +
        '</div>' : '');
    return;
  }
  if (!r.ok) { out.innerHTML = errHtml(d, r.status); return; }
  let h;
  if (p.answered) {
    h = banner('ok', '✓ 平台亲眼看到：测试消息被<b>取走</b>了，它还<b>回了话</b> —— bridge_ok + mailbox_ok 都记成「平台验的」' +
      (d.state === 'active' ? '，工位推进到 <b>active</b>' : ''));
  } else if (p.picked_up) {
    h = banner('ok', '✓ 平台亲眼看到测试消息被取走了 —— bridge_ok 记「平台验的」' +
      '<br><span class="msg">它还没回话（mailbox_ok 没记）—— 回话是另一件事，别混。</span>');
  } else {
    h = banner('warn', '写了测试消息，这一眼还没看到取走 —— 证据没记。再点一次（同一条 probe_id）');
  }
  h += '<div class="msg">probe：<code>' + esc(pid || '—') + '</code>（下次点带同一条 —— 每次都新发就永远看不到取走）</div>' +
    (p.verdict ? '<div class="msg">判定：' + esc(p.verdict) + '</div>' : '') +
    (p.inbox ? '<div class="msg">邮箱：<code>' + esc(p.inbox) + '</code>' +
      (p.inbox_guessed ? '（路径是<b>按工位名推的</b> —— 同岗位多人会共用）' : '') + '</div>' : '');
  out.innerHTML = h;
  await loadRoster2();
}

/* ── 4. 回执 + 工位 ───────────────────────────────────────── */
async function submitRcpt() {
  if (!S.ws) { say('rcptMsg', '先选工作间', 'bad'); return; }
  const text = $('rcptText').value;
  if (!text.trim()) { say('rcptMsg', '把回执原样贴进来', 'bad'); return; }
  if (!$('rcptConfirm').checked) {
    say('rcptMsg', 'C 档是人工粘贴，必须自己打勾确认（否则平台不收）', 'warn');
    return;
  }
  const r = await api('POST', '/api/workshop/' + encodeURIComponent(S.ws) + '/intake?source=C&confirm=1',
    { text: text });
  if (!r.ok) { $('rcptMsg').innerHTML = errHtml(r.data, r.status); return; }
  const d = r.data;
  say('rcptMsg', '✓ 收下：' + d.type + '（' + (d.row_count || 0) + ' 行）' +
    (d.applied ? '，已落库' : '，只记账'), 'ok');
  if (d.result) {
    $('rcptMsg').innerHTML += '<div class="msg">落库结果：' +
      esc(JSON.stringify(d.result).slice(0, 300)) + '</div>';
  }
  $('rcptMsg').innerHTML += mirrorLine(d.mirror);
  await loadRoster2();
}

async function loadRoster2() {
  if (!S.ws) return;
  const r = await api('GET', '/api/workshop/' + encodeURIComponent(S.ws) + '/roster');
  if (!r.ok) { $('slotsOut').innerHTML = errHtml(r.data, r.status); return; }
  S.members = r.data.members || [];
  S.slots = r.data.slots || [];
  /* D后端-67（V116 §8.0 口径一）：名单和实到对不上时，平台给**一句话**，界面直接显示，别自己拼数字。
     `sentence` 里带 `**强调**`（后端同一套写法），走 mdInline 转义；`detail` 是补充的两句（近/告警）。 */
  const rs = r.data.roster_summary || {};
  let head = '';
  if (rs.sentence) {
    const kind = rs.all_active ? 'ok' : (rs.can_dispatch_anyone ? 'warn' : 'bad');
    head = banner(kind, mdInline(rs.sentence) +
      ((rs.detail || []).length
        ? '<br><span class="msg">' + (rs.detail || []).map(mdInline).join('<br>') + '</span>'
        : ''));
  }
  let h = '<table><tr><th>工位</th><th>harness</th><th>状态</th><th>谁验的</th><th>档位</th><th>覆盖范围</th><th>会话名</th><th>成员拉人</th></tr>' +
    S.members.map(function (m) {
      const cls = m.state === 'active' ? 'ok' : (m.state === 'pending' ? 'warn' : '');
      /* ★老板 10-31 SOP：名单定了之后，平台把每个成员的对接单拼好（自动带工作间路径），
         用户手动拉人 —— 平台不代发、不代建会话。组长走 UNIT-02，不在这里重复出按钮。 */
      const pull = m.slot_dir === '_lead'
        ? '<span class="msg">组长走 UNIT-02</span>'
        : '<button class="p" onclick="memberKit(' + Number(m.member_seq) + ')">生成对接单</button>';
      /* D后端-69：B 档成员也能平台自验 —— 点一次发测试消息，之后每次都带同一条 probe_id 再看 */
      const probeBtn = (m.tier === 'b_bridge')
        ? '<br><button onclick="bridgeProbe(' + Number(m.member_seq) + ')">检查取件（平台自验）</button>'
        : '';
      /* D后端-67 §2：actual_name 是**人给的**，没填就没核过 —— 不许显示出"已核对"的样子 */
      const nameLine = m.actual_name
        ? '<div class="msg">实际名：' + esc(m.actual_name) +
          (m.name_check && m.name_check.match === false ? ' <b class="warn">（与名单不一致，只提醒）</b>' : '') + '</div>'
        : '<div class="msg">未核过实际名</div>';
      return '<tr><td>' + esc(m.slot_dir) + '<div class="msg">#' + m.member_seq + '</div></td>' +
        '<td>' + esc(m.harness_id || '—') + '</td>' +
        '<td class="' + cls + '">' + esc(m.state) + '</td>' +
        '<td>' + evidenceSrcHTML(m) + '</td>' +
        '<td>' + esc(m.tier || '—') + '</td>' +
        '<td>' + esc(m.verified_scope || '（没写）') + '</td>' +
        '<td>' + esc(m.session_label || '') + nameLine + '</td>' +
        '<td>' + pull + probeBtn + '</td></tr>';
    }).join('') + '</table>';
  h += '<div class="msg">' + esc(r.data.hint || '') + '</div>';
  $('slotsOut').innerHTML = head + h;

  const sel = $('dspSeq');
  const actives = S.members.filter(function (m) { return m.state === 'active'; });
  /* 空工位不给"能派活"的错觉：选项上写清没接上（点派活会拿到 409 GATE_G5，四要素会说是谁缺什么） */
  sel.innerHTML = (actives.length ? actives : S.members).map(function (m) {
    const tag = m.state === 'active' ? '' : '（没接上，不能派活）';
    return '<option value="' + m.member_seq + '">' + esc(m.slot_dir) + '（' + esc(m.state) + '）' + tag + '</option>';
  }).join('') || '<option value="">（没有工位）</option>';
  if (!actives.length && S.members.length) {
    $('dspOut').innerHTML = banner('warn',
      mdInline(rs.sentence || '现在没有 active 工位 —— 派活会被 G5 拦住（这是对的）。先接人。') +
      '<br><span class="msg">只要求一个工位接上就能开工（G4）；没接上的那个不能派活，但不会把整间活儿停住。</span>');
  }
}

/* ── 4b. 资料库指向（D后端-60 §2①，第 7–9 步）＋ 成员对接单（§2②，第 3/11 步）── */
/* 名单定了 → 每个工位拼对接单（后端 POST /members/{seq}/kit 自动带工作间路径/会话名/回执头，
   **并且**把该成员自己的工位路径拼成「## 桥参数」段放进正文）—— 用户手动拉人，平台不代发。
   2026-10-31（D后端-64）：原来另起一块拉 bridge-fill 显示同样的参数，两处显示只会对不上，已退役。 */
async function memberKit(seq) {
  if (!S.ws) return;
  const r = await api('POST', '/api/workshop/' + encodeURIComponent(S.ws) +
    '/members/' + encodeURIComponent(seq) + '/kit', { issue_token: 0 });
  const box = $('mkOut');
  if (!box) return;
  if (!r.ok) { box.hidden = false; box.innerHTML = errHtml(r.data, r.status); return; }
  const d = r.data || {};
  box.hidden = false;
  box.textContent = d.kit || '';
  const note = $('mkNote');
  if (note) {
    const miss = d.member_bridge_missing || [];
    note.innerHTML = miss.length
      ? banner('bad', '正文里「## 桥参数」还缺：<b>' + miss.map(esc).join('、') + '</b>' +
          '（平台不瞎填 —— 先把这个工位的 harness 定下来，再拼一份）')
      : banner('ok', '桥参数（模板 <code>' + esc(d.member_bridge_template || '—') + '</code>）' +
          '已拼在正文末尾 —— 填的是<b>它自己的工位路径</b>，整段复制，别手改');
    /* D后端-68：A 档对接单多了「## 6b. 回话口令」—— 一次性口令在正文里，
       它答对了平台**自己**记 protocol_ok（platform-observed），不用你打勾。 */
    if (d.handshake_issued) {
      note.innerHTML += banner('ok', '正文里有「## 6b. 回话口令」—— <b>整段复制别漏</b>：' +
        '它要用那份一次性口令打一次 <code>' + esc(d.handshake_endpoint || 'handshake 端点') + '</code>，' +
        '答对了平台<b>自己</b>记「它真读到过这张单子」（平台验的，不用你打勾）' +
        '<br><span class="msg">⚠ 成员单<b>每次拼都换新口令，旧的当场作废</b> —— 别拿两次拼的比对，也别存旧口令。</span>');
    }
  }
  say('rcptMsg', '✓ #' + seq + ' 对接单拼好了（工作间路径 + 会话名 + 回执头 + 桥参数都在这一段里）—— 整段复制发给该成员，手动拉它进来', 'ok');
}

/* 资料库指向走专用端点 /library-path：平台只记指向、不代建目录；GET /library 按 library_path 列实况。 */
async function loadLibDir() {
  if (!S.ws) return;
  const out = $('libOut'), st = $('libState');
  if (!out && !st) return;
  const r = await api('GET', '/api/workshop/' + encodeURIComponent(S.ws) + '/library');
  if (!r.ok) { if (out) out.innerHTML = errHtml(r.data, r.status); return; }
  const d = r.data || {};
  if (st) {
    const custom = typeof S.libPath === 'string' ? S.libPath.trim() : '';
    st.innerHTML = '<div class="msg">当前指向：' +
      (custom ? '自定义 <code>' + esc(d.dir || custom) + '</code>' : '默认 <code>' + esc(d.dir || '') + '</code>（没设过自定义）') +
      (d.exists ? ' <b class="ok">✓ 目录存在</b>'
                : ' <b class="bad">✗ 目录不存在 —— 请先去新建它（平台不代建）</b>') + '</div>';
  }
  const files = d.files || [], dirs = d.directories || [];
  out.innerHTML = (files.length || dirs.length)
    ? '<table><tr><th>资料库实况</th><th>内容</th></tr>' +
      dirs.map(function (x) {
        return '<tr><td>📁 ' + esc(x.name || '') + '</td><td class="msg">' +
          ((x.files || []).map(function (f) { return esc(f.name); }).join('、') || '（空文件夹）') + '</td></tr>';
      }).join('') +
      files.map(function (f) {
        return '<tr><td>' + esc(f.name || '') + '</td><td class="msg">' + (f.size || 0) + ' 字节</td></tr>';
      }).join('') + '</table>'
    : '<div class="msg">资料库还是空的 —— 组长把「项目建模 + 分工明细」放进来后，这里能看到实况。</div>';
}

async function saveLibDir() {
  if (!S.ws) { say('libMsg', '先选工作间', 'bad'); return; }
  const p = $('libPath').value.trim();
  if (!p) { say('libMsg', '要组长给你的资料库文件夹路径（绝对路径）；要回默认请点「清空」', 'bad'); return; }
  const r = await api('POST', '/api/workshop/' + encodeURIComponent(S.ws) + '/library-path', { path: p });
  const d = r.data || {};
  if (!r.ok) {
    say('libMsg', '没记下：' + (d.error || d.code || ('HTTP ' + r.status)) + (d.next ? (' —— ' + d.next) : ''), 'bad');
    return;
  }
  S.libPath = d.library_path || '';
  say('libMsg', d.exists ? '✓ 指向已记下 —— 组长可以把建模与分工写进去'
                         : '指向已记下，但这个目录现在不存在 —— 请先去新建它（平台不代建）', d.exists ? 'ok' : 'bad');
  $('libPath').value = '';
  await loadLibDir();
}

async function clearLibDir() {
  if (!S.ws) { say('libMsg', '先选工作间', 'bad'); return; }
  const r = await api('POST', '/api/workshop/' + encodeURIComponent(S.ws) + '/library-path', { path: '' });
  if (!r.ok) { say('libMsg', '清空失败：' + ((r.data && r.data.error) || ('HTTP ' + r.status)), 'bad'); return; }
  S.libPath = '';
  say('libMsg', '✓ 已清空自定义指向，回到默认 工作区/library/', 'ok');
  await loadLibDir();
}

/* 2026-10-31（D后端-62/64）：组长与成员的对接单**都**由后端拼进「## 桥参数」段，
   这里原来单独拉 bridge-fill 显示/复制的块（leadBridgeFill / memberBridgeFill / bridgeFillHTML /
   bridgeFillText / copyMemberFill）全部退役 —— 同一份数据两处显示只会对不上。 */

/* ── 5b. 派活四步（D后端-65）：卡在哪一步，说清 ─────────────────────────
   后端钉死的三条语义（别读错）：
   · `ok`            = **前三步落盘**（任务文件/_notice/manifest），不代表"它接活了"；
   · `manual_required` = 触达是 C 档人工贴，**正常终态、不是失败**（此时 `stuck_at` 为空）；
   · `stuck_at`       = 只在**前三步没成**或**触达 failed（真发不出去）**时给值，配 `why` + `next`。 */
const DSP_STEPS = ['任务文件', '_notice 追加', 'manifest.active_tasks', '触达'];

/* 后端的说明文字里带 `**强调**` 与反引号（对接单同一套写法）；界面别把星号原样吐给用户。 */
function mdInline(s) {
  return esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>');
}

function dspWhatCell(s) {
  const v = String((s && s.what) || '');
  if (!v) return '<span class="msg">（后端没给落点）</span>';
  const pathLike = v.indexOf('\\') >= 0 || v.indexOf('/') >= 0 || v.indexOf('sha256:') === 0;
  return pathLike && v.length <= 120 ? '<code>' + esc(v) + '</code>'
                                     : '<span class="msg">' + mdInline(v) + '</span>';
}
function dispatchStepsHTML(d) {
  const steps = (d && d.steps) || [];
  if (!steps.length) return '';
  let h = '<table><tr><th>步</th><th>动作</th><th>成没成</th><th>落点 / 为什么</th></tr>';
  steps.forEach(function (s) {
    const manual = s.ok === false && s.result === 'manual';
    const cls = s.ok ? 'ok' : (manual ? 'warn' : 'bad');
    const mark = s.ok ? '✓' : (manual ? '✗ 人工' : (s.result === 'failed' ? '✗ 失败' : '✗'));
    const extra = (s.ok && s.result) ? '（' + esc(s.result) + '）' : '';
    h += '<tr><td>' + esc(String(s.n)) + '</td><td>' + esc(s.name || '') + '</td>' +
      '<td class="' + cls + '">' + mark + extra + '</td><td>' + dspWhatCell(s) + '</td></tr>';
  });
  return h + '</table>';
}
/* 一条派活的结论条 + 四步表（派活返回与账本视图共用这一份渲染 —— 两处必须长得一样）。 */
function dispatchFlowHTML(d, seq) {
  d = d || {};
  const stuck = String(d.stuck_at || '').trim();
  const wake = ((d.steps || [])[3] || {}).result || ((d.wakeup || {}).result) || '';
  let h = '';
  if (stuck) {
    h += banner('bad', '✗ 卡在「' + esc(stuck) + '」' +
      (d.why ? '<br>为什么：' + mdInline(d.why) : '') +
      (d.next ? '<br>下一步：' + mdInline(d.next) : ''));
  } else if (d.manual_required) {
    h += banner('warn', '⚠ 文件三步都落盘了，就差「触达」—— 这一步<b>只能人工</b>' +
      '（这是正常终态，<b>不是失败</b>' + (d.manual_hint ? '' : '；平台不假装叫醒它') + '）' +
      (d.manual_hint ? '<br>' + mdInline(d.manual_hint) : '') +
      '<div class="row" style="margin-top:8px">' +
      '<button onclick="confirmPasted(' + Number(seq || 0) + ')">我已贴好，记一笔</button>' +
      '<span class="msg" id="pasteMsg"></span></div>');
  } else {
    h += banner('ok', '✓ 四步都成了 —— 它已被真叫醒（' + esc(wake || 'ok') + '）');
  }
  return h + dispatchStepsHTML(d);
}

/* 账本视图（GET …/dispatch/{seq}）与派活返回同源：这里只渲染，不自己算状态。 */
async function loadDispatchDetail(seq) {
  if (!S.ws) { say('dspOut', '先选工作间', 'bad'); return; }
  const box = $('dspDetail');
  if (!box) return;
  const r = await api('GET', '/api/workshop/' + encodeURIComponent(S.ws) +
    '/dispatch/' + encodeURIComponent(seq));
  if (!r.ok) { box.innerHTML = errHtml(r.data, r.status); return; }
  const d = r.data || {};
  const e = d.entry || {};
  box.innerHTML = '<div class="msg">T-' + esc(String(seq).padStart(3, '0')) + '「' +
      esc(e.title || '') + '」的账（与派活返回<b>同源</b> —— 两处不一致就是 bug）' +
      '　文件在盘上：' + (d.file_exists ? '✓' : '✗') +
      '　在 manifest.active_tasks：' + (d.in_manifest ? '✓' : '✗') + '</div>' +
    dispatchFlowHTML(d, seq) +
    (d.hint ? '<div class="msg">' + mdInline(d.hint) + '</div>' : '');
  const sec5 = $('sec5');
  if (sec5 && sec5.scrollIntoView) sec5.scrollIntoView({ block: 'start' });
}

/* 「我已贴好」：只记**人真的贴了**这一笔（paste_seen），推不推进状态由后端按档位判，
   推不到 active 就如实说还缺什么 —— 平台不代打勾。 */
async function confirmPasted(seq) {
  if (!S.ws) return;
  const r = await api('POST', '/api/workshop/' + encodeURIComponent(S.ws) +
    '/members/' + encodeURIComponent(seq) + '/evidence',
    { paste_seen: true, who: 'user' });
  const d = r.data || {};
  if (!r.ok) { say('pasteMsg', '没记上：' + (d.error || d.code || ('HTTP ' + r.status)), 'bad'); return; }
  say('pasteMsg', '✓ 已记「已贴好」；该工位现在 ' + (d.state || '') + '，还没打勾的档位证据照旧要补' +
    (d.next ? ' —— ' + d.next : ''), d.state === 'active' ? 'ok' : 'warn');
  await loadRoster2();
}

/* ── 5c. 目录校验 ─────────────────────────────────────────── */
async function verifyDirs() {
  if (!S.ws) { say('vdMsg', '先选工作间', 'bad'); return; }
  const r = await api('GET', '/api/workshop/' + encodeURIComponent(S.ws) + '/verify-dirs');
  if (!r.ok) { $('vdOut').innerHTML = errHtml(r.data, r.status); return; }
  const d = r.data;
  if (d.ok) {
    $('vdOut').innerHTML = banner('ok', '✓ 目录都齐了（检查了 ' + d.checked + ' 项）' +
      '<br><span class="msg">' + esc(d.next || '可以直接派活') + '</span>');
    say('vdMsg', '通过', 'ok');
    return;
  }
  $('vdOut').innerHTML = banner('bad', '✗ 缺 ' + d.missing.length + ' 项（平台<b>不代建</b>）') +
    '<table><tr><th>缺什么</th><th>路径</th><th>谁补 / 怎么做</th></tr>' +
    d.missing.map(function (m) {
      const f = m.four || {};
      return '<tr><td>' + esc(m.what) + '</td><td><code>' + esc(m.path) + '</code></td>' +
        '<td>' + esc(f.who || '') + '：' + esc(f.how || '') + '</td></tr>';
    }).join('') + '</table>';
  say('vdMsg', '缺 ' + d.missing.length + ' 项', 'bad');
}

async function doDispatch() {
  if (!S.ws) { say('dspOut', '先选工作间', 'bad'); return; }
  const seq = parseInt($('dspSeq').value || '0', 10);
  if (!seq) { $('dspOut').innerHTML = banner('bad', '先有工位再派活'); return; }
  const body = {
    member_seq: seq,
    title: $('dspTitle').value.trim() || '未命名任务',
    goal: $('dspTitle').value.trim() || '未命名任务',
    accept: $('dspAccept').value.trim() || ''
  };
  const r = await api('POST', '/api/workshop/' + encodeURIComponent(S.ws) + '/dispatch', body);
  const d = r.data || {};
  if (!r.ok || !d.ok) {
    $('dspOut').innerHTML = errHtml(d, r.status) + dispatchStepsHTML(d);
    return;
  }
  const tid = d.task_id || ('T-' + String(d.task_seq || 0).padStart(3, '0'));
  let h = dispatchFlowHTML(d, seq);
  h += '<div class="row"><span class="msg">' + esc(tid) + '　任务文件 <code>' +
      esc(d.file) + '</code>' + (d.file_created ? '（平台代写）' : '（组长自己写的）') + '</span>' +
    '<button onclick="loadDispatchDetail(' + Number(d.task_seq || 0) + ')">看这条派活（账本同源）</button></div>';
  /* ★2026-11-02（老板：「工作过程中没有回复，让用户以为是掉线了」）：
     派完**立刻**给一块"在途状态"，并按任务号轮询它的进度 —— 屏幕上要有东西在动，
     用户才知道"它在干"，而不是"平台断了"。 */
  const wk = d.wakeup || {};
  h += '<div id="dspLive" class="dsp-live"></div>';
  $('dspOut').innerHTML = h;
  if (wk.a2a_task_id && wk.harness_id) {
    dispatchWatch(String(wk.harness_id), String(wk.a2a_task_id), String(tid));
  } else if (wk.result === 'ok' || wk.result === 'queued') {
    const box = $('dspLive');
    if (box) box.innerHTML = '<div class="msg">已派出（通道 ' + esc(String(wk.method || '')) +
      '）。这类接入平台**查不到实时进度** —— 它做完会通过回执或桥回来，别以为断了。</div>';
  }
  await loadTasks();
}

/** 派活之后盯住这条 A2A 任务的进度（每 4 秒一次，最多 5 分钟）。
 *
 *  ★2026-11-02（老板：「工作过程中没有回复，让用户以为是掉线了」）：
 *  A2A 的活要跑一会儿，界面上**必须有东西在动**：任务号 / 当前状态 / 已经等了多久 /
 *  终态时把它的回复贴出来。查不到就如实说"查不到"，不假装还在跑。
 */
async function dispatchWatch(hid, taskId, tid){
  const box = $('dspLive');
  if (!box) return;
  const t0 = Date.now();
  let sec = 0;
  const paint = (state, extra) => {
    box.innerHTML =
      '<div class="dsp-live-row"><b>正在干：</b>' + esc(tid) +
      '　任务 <code>' + esc(taskId) + '</code></div>' +
      '<div class="dsp-live-row">状态 <b>' + esc(state || '查询中') + '</b>' +
      '　已等 <b>' + Math.round(sec) + '</b> 秒' + (extra ? ('　' + esc(extra)) : '') + '</div>' +
      '<div class="dsp-live-row dsp-dim">它做完后，结果会自动写进任务账本（这里也会显示它回的话）。</div>';
  };
  paint('已派出，等它开始', '');
  while (Date.now() - t0 < 300000) {
    await new Promise(r => setTimeout(r, 4000));
    sec = (Date.now() - t0) / 1000;
    try {
      const r = await api('GET', '/api/docking/harness/' + encodeURIComponent(hid) +
        '/a2a-task/' + encodeURIComponent(taskId));
      const d = r.data || {};
      if (!r.ok || !d.ok) { paint('查不到进度', '(接口' + (r.status || '异常') + '；派活本身已成功)'); continue; }
      if (d.terminal) {
        box.innerHTML =
          '<div class="dsp-live-row"><b>它做完了：</b>' + esc(tid) + '　任务 <code>' + esc(taskId) + '</code></div>' +
          '<div class="dsp-live-row">最终状态 <b>' + esc(d.state || '') + '</b>　耗时 <b>' + Math.round(sec) + '</b> 秒</div>' +
          (d.text ? ('<div class="dsp-live-say">' + esc(String(d.text)) + '</div>')
                  : '<div class="dsp-dim">（它没给出文字回复 —— 结果看任务账本/产物）</div>');
        return;
      }
      paint(d.state || '在做', '');
    } catch (e) { /* 单次失败继续轮 */ }
  }
  paint('5 分钟还没完', '(可能还在做；去对端会话或任务账本看，别当成断了)');
}

/* ── 6. 产物 ───────────────────────────────────────────────── */
async function scanArts() {
  if (!S.ws) { say('artMsg', '先选工作间', 'bad'); return; }
  const r = await api('POST', '/api/workshop/' + encodeURIComponent(S.ws) + '/verify-artifacts', {});
  if (!r.ok) { $('artOut').innerHTML = errHtml(r.data, r.status); return; }
  const d = r.data;
  const diff = d.diff || {};
  let h = banner(d.total ? 'ok' : 'warn',
    '盘上真扫到 <b>' + d.total + '</b> 个产物文件（' + (d.total ? 'L0' : '空就是空，别把自报当产物') + '）');
  /* D后端-66 §4：`new`/`changed` 是**相对上一次快照**的增量 —— 先扫过一次再扫会是空的，
     而文件明明在盘上。所以"有没有交东西"只看盘上现存总数，diff 只当参考。 */
  const filesNow = [].concat.apply([], Object.keys(d.slots || {}).map(function (slot) {
    return Object.keys((d.slots[slot] || {}).files || {}).map(function (rel) { return slot + '/' + rel; });
  }));
  h += '<div class="msg">盘上现存 <b>' + filesNow.length + '</b> 个文件（判"有没有交东西"只看这个数）</div>';
  h += '<div class="msg">相对<b>上一次快照</b>：新增 ' + (diff.new || []).length +
    '　变更 ' + (diff.changed || []).length +
    '　删除 ' + (diff.removed || []).length + '　未变 ' + (diff.same || 0) +
    '　—— 「新增」为 0 只说明上次扫过了，<b>不代表没有产物</b></div>';
  const rows = [];
  Object.keys(d.slots || {}).forEach(function (slot) {
    const files = (d.slots[slot] || {}).files || {};
    Object.keys(files).forEach(function (rel) {
      rows.push([slot + '/' + rel, files[rel]]);
    });
  });
  if (rows.length) {
    h += '<table><tr><th>文件</th><th>字节</th><th>sha256</th><th>改于</th></tr>' +
      rows.slice(0, 50).map(function (r2) {
        return '<tr><td><code>' + esc(r2[0]) + '</code></td><td>' + esc(r2[1].bytes) + '</td>' +
          '<td class="msg">' + esc((r2[1].sha256 || '').slice(0, 12)) + '…</td>' +
          '<td class="msg">' + esc(r2[1].at || '') + '</td></tr>';
      }).join('') + '</table>';
  }
  $('artOut').innerHTML = h;
  say('artMsg', d.total + ' 个文件', d.total ? 'ok' : 'warn');
}

/* ── 7. 验收 / 账本 / 归档 ─────────────────────────────────── */
/* 证据档人话（D后端-66：L1 一律标「平台未核对」—— 平台没去查那个库，不许显示成"已验证"）。 */
const LEVEL_LABEL = {
  L0: 'L0（平台实做）',
  L1: 'L1（人工凭据，平台未核对）',
  L2: 'L2（平台留痕）',
  L3: 'L3（自报 —— 一律拒）',
  L4: 'L4（人眼确认）'
};
function levelLabel(lv) { return LEVEL_LABEL[lv] || String(lv || ''); }

/* 四步表：第 1/4 步实心（硬前置 / 收尾），第 2/3 步标成「可作证据：L0/L2」——
   老板规则是"证据有一档够格就行"，所以**不做成四格进度条**，也不显示"完成度"。 */
function evidenceStepsHTML(d) {
  const steps = (d && d.steps) || [];
  if (!steps.length) return '';
  const have = (d && d.evidence_have) || [];
  let h = '<table><tr><th>步</th><th>动作</th><th>状态</th><th>说明</th></tr>';
  steps.forEach(function (s, i) {
    const lv = s.level ? '<div class="msg">可作证据：' + esc(levelLabel(s.level)) + '</div>' : '';
    /* ★2026-11-05：后端四步表的元素**没有** `n` 字段（只有 name/ok/what/level）⇒
       原来显示 "undefined"；序号这里自己数，别指望后端给。 */
    const no = (s.n != null && s.n !== '') ? s.n : (i + 1);
    h += '<tr><td>' + esc(String(no)) + '</td><td>' + esc(s.name || '') + '</td>' +
      '<td class="' + (s.ok ? 'ok' : 'bad') + '">' + (s.ok ? '✓' : '✗') + lv + '</td>' +
      '<td>' + mdInline(s.what) + '</td></tr>';
  });
  h += '</table>';
  h += '<div class="msg">现在可用：' + (have.length
    ? have.map(function (x) { return esc(levelLabel(x)); }).join('、')
    : '还没有可用档（只能按 L4 人眼确认）') +
    '　—— 有一档够格就能过，<b>不是四步都要打勾</b></div>';
  const snap = (d && d.artifacts_snapshot) || {};
  /* ★2026-11-05：没扫过时后端 `slot_exists=false` 是"还没看"，**不是"目录不在"** ——
     原来直接显示"不在（多半岗位名与目录名不一致）"，会把人引去改名单。 */
  const slotTxt = snap.scanned_at
    ? (snap.slot_exists ? '在' : '<b class="bad">不在</b>（多半岗位名与目录名不一致）')
    : '<span class="msg">未知（还没扫过 —— 不等于不在）</span>';
  h += '<div class="msg">上次扫盘：' + (snap.scanned_at ? esc(snap.scanned_at) : '还没扫过') +
    '　工位目录' + slotTxt +
    '　盘上文件 <b>' + esc(String(snap.total_files || 0)) + '</b> 个' +
    ((snap.files_preview || []).length ? '：' + esc(snap.files_preview.join('、')) : '') + '</div>';
  return h;
}
/* 证据卡 = 卡点条 + 四步表（GET …/evidence-view 与验收失败态共用这一份）。 */
function evidenceViewHTML(d) {
  d = d || {};
  let h = '';
  if (d.blocking) {
    h += banner('warn', '现在卡在「' + esc(d.blocking) + '」' +
      (d.who_should_fix ? '（该 <b>' + esc(d.who_should_fix) + '</b> 补）' : '') +
      (d.why ? '<br>' + mdInline(d.why) : '') +
      (d.next ? '<br>下一步：' + mdInline(d.next) : ''));
  } else {
    h += banner('ok', '四步都齐了' + (d.why ? '：' + mdInline(d.why) : ''));
  }
  h += evidenceStepsHTML(d);
  if (d.given_level) {
    h += '<div class="msg">预演：按 ' + esc(levelLabel(d.given_level)) + ' 过 —— ' +
      (d.blocking ? '会卡在「' + esc(d.blocking) + '」' : '卡不住，能过') + '</div>';
  }
  if (d.note) h += '<div class="msg">' + esc(d.note) + '</div>';
  return h;
}

async function loadEvidenceView() {
  if (!S.ws) { say('evMsg', '先选工作间', 'bad'); return; }
  const box = $('evView');
  if (!box) return;
  const seq = $('revTask') ? $('revTask').value : '';
  if (!seq) { box.innerHTML = banner('warn', '先有任务再谈证据 —— 账本是空的，先去 UNIT-05 派活。'); return; }
  const lv = ($('evLevel') && $('evLevel').value) || '';
  const r = await api('GET', '/api/workshop/' + encodeURIComponent(S.ws) +
    '/tasks/' + encodeURIComponent(seq) + '/evidence-view' + (lv ? '?level=' + encodeURIComponent(lv) : ''));
  if (!r.ok) { box.innerHTML = errHtml(r.data, r.status); return; }
  box.innerHTML = evidenceViewHTML(r.data);
  say('evMsg', lv ? ('预演 ' + lv + ' 已算') : '只读视图已取', 'ok');
  await loadPeerView();
}

/* ── 7b. 跨组织采信（V130）：交付登记 / 本侧重算 / 争议冻结 ──────────────────
 * 硬口径（老板 2026-11-04 拍板，别在界面上改写）：
 *   ① `accepted_self` **不是通过**（只有它自己说）；② `accepted_peer` 才算本侧核过；
 *   ③ `disputed` 冻结待裁（记录不删）；④ `refuted` 已证伪（可直接降级）；
 *   ⑤ **平台不判谁对** —— 界面只摆事实与双方主张，不替人裁决。
 * 判据：没有 `accepted_peer` 的跨组织结果，后端 `review` 会拒（PEER_NOT_REVIEWED / DISPUTE_OPEN / PEER_REFUTED）。
 */
const PEER_STATE_TEXT = {
  accepted_self: '未复核：只有它自己说',
  accepted_peer: '已复核：本侧重算一致',
  disputed: '有争议，不采信（冻结待裁）',
  refuted: '已证伪（可直接降级）'
};
const PEER_STATE_CLS = { accepted_self: 'st-self', accepted_peer: 'st-peer',
  disputed: 'st-dispute', refuted: 'st-refuted' };

function peerBadge(state) {
  const st = String(state || '');
  if (!st) return '<span class="peer-badge st-none">不是跨组织任务</span>';
  return '<span class="peer-badge ' + (PEER_STATE_CLS[st] || 'st-none') + '">' +
    esc(PEER_STATE_TEXT[st] || st) + '</span>';
}

function _peerDiffHtml(v) {
  const d = v.peer_diff || {};
  const files = d.files || [];
  if (!files.length) return '';
  const bad = files.filter(function (f) { return f.status !== 'match'; });
  let h = '<div class="peer-diff">逐文件比对：一致 <b>' + Number(d.matched || 0) + '</b>　不一致 <b>' +
    Number(d.mismatched || 0) + '</b>　本侧缺 <b>' + Number(d.missing_local || 0) + '</b>　' +
    '对端没给 hash <b>' + Number(d.no_hash || 0) + '</b>' + (d.why ? '<br>' + esc(d.why) : '') + '</div>';
  if (bad.length) {
    h += '<table><tr><th>文件</th><th>结论</th><th>为什么</th></tr>' + bad.slice(0, 10).map(function (f) {
      return '<tr class="bad-row"><td><code>' + esc(f.key) + '</code></td><td>' + esc(f.status) +
        '</td><td>' + esc(f.why || '') + '</td></tr>';
    }).join('') + '</table>';
  }
  return h;
}

function _peerDisputeHtml(v) {
  const d = v.dispute;
  if (!d) return '';
  return '<div class="peer-dispute"><b>争议记录（记录不删，冻结待裁）</b><br>' +
    '发起：' + esc(d.raised_by || '未记') + '　待裁：' + esc(d.adjudicator || '未指定') +
    (d.why ? '<br>为什么：' + esc(d.why) : '') +
    '<br>双方主张：' + (d.claims || []).map(function (c) {
      return '<br>· <b>' + esc(c.by || '未署名') + '</b>：' + esc(c.claim || '');
    }).join('') + '</div>';
}

function peerViewHTML(v) {
  v = v || {};
  let h = '<div class="peer-box"><div class="peer-head"><b>跨组织采信</b>' + peerBadge(v.accept_state) +
    (v.peer_checked_at ? '<span class="msg">复核于 ' + esc(v.peer_checked_at) + '</span>' : '') + '</div>';
  if (!v.external) {
    h += '<div class="msg">这条是<b>内部任务</b>：验收只看证据档（上面四步表），没有采信态。' +
      '如果这活儿其实是外部公司交的，展开下面「这活儿是对端交的」，把它的交付登记进来。</div>';
  } else {
    h += '<div class="msg">' + mdInline(v.why || '') + '</div>';
    h += '<div class="msg"><b>它会说「对得上 / 已完成」不算平台核过</b> —— 采信只认本侧重算与可指向的凭据。</div>';
    h += _peerDiffHtml(v);
    h += _peerDisputeHtml(v);
    /* ★2026-11-05：后端的 `next` 是**通用**口径（"去做复核"/"先挂争议"），
       在这两种状态下会指错路（已经复核过没对齐 / 已经挂了争议还在说"先挂"）——
       界面按**当前状态**给下一步，别让人原地打转。 */
    const pc = v.peer_check || {};
    let nextTxt = mdInline(v.next || '');
    if (v.accept_state === 'accepted_peer') nextTxt = '';
    else if (v.accept_state === 'disputed') nextTxt = '等裁决方给结论：对得上就重新派活/重交证据再复核；证伪就保持 refuted。';
    else if (v.accept_state === 'refuted') nextTxt = '这条已被证伪，不能验收通过；要重交得重新派活。';
    else if (v.accept_state === 'accepted_self' && pc.at) nextTxt = '本侧已经重算过、但没对齐 —— 对不齐就挂争议交人裁（③），别在这里反复点。';
    if (nextTxt) h += '<div class="msg">下一步：' + nextTxt + '</div>';
  }
  h += '<details class="peer-form-wrap"><summary class="msg">' +
    (v.external ? '登记新的交付 / 重做复核 / 挂争议' : '这活儿是对端交的（登记它的交付）') + '</summary>' +
    '<div class="peer-form">' +
    '<label>对端给的说法（原话，可空）<textarea id="peerResult" placeholder="例：已完成，产物在 报告.md"></textarea></label>' +
    '<label>对端给的产物清单（每行：相对路径 | 64 位 sha256 | 字节数）' +
    '<textarea id="peerFiles" class="peer-files" placeholder="报告.md | 2f1c…（64 位十六进制） | 128"></textarea></label>' +
    '<div class="peer-actions"><button onclick="peerDelivery()">① 登记这条交付</button>' +
    '<span class="msg" id="peerMsg"></span></div>' +
    '<div class="peer-actions"><button class="p" onclick="peerReview()">② 本侧重算并复核</button>' +
    '<span class="msg">平台真扫本侧 <code>产物/</code> 逐个比 sha256 —— 对不上就停在「未复核」</span></div>' +
    '</div>' +
    '<div class="peer-form" style="border-top:1px solid var(--rule);margin-top:10px;padding-top:10px">' +
    '<label>争议发起方<input id="peerBy" placeholder="例：接收方 / 组长"></label>' +
    '<label>交付方主张<textarea id="peerClaimA" placeholder="例：我交的就是 v2，sha256 是 0…"></textarea></label>' +
    '<label>接收方主张<textarea id="peerClaimB" placeholder="例：本侧重算 sha256 对不上，是另一个文件"></textarea></label>' +
    '<label>待谁裁<input id="peerJudge" placeholder="例：组长 / 第三方复核"></label>' +
    '<label>处置<select id="peerOutcome">' +
    '<option value="disputed">有争议未决（冻结待裁，不能验收通过）</option>' +
    '<option value="refuted">已被证据证伪（可直接降级）</option></select></label>' +
    '<div class="peer-actions"><button onclick="peerDispute()">③ 挂争议 / 判证伪</button>' +
    '<span class="msg" id="peerJudgeMsg"></span></div>' +
    '</div></details></div>';
  return h;
}

function _peerOpen() {
  const d = document.querySelector('#peerView .peer-form-wrap');
  if (d) d.open = true;
}

async function loadPeerView() {
  const box = $('peerView');
  if (!box) return;
  if (!S.ws || !$('revTask') || !$('revTask').value) {
    box.innerHTML = '<div class="peer-box"><div class="peer-head"><b>跨组织采信</b>' +
      peerBadge('') + '</div><div class="msg">先选一条任务。</div></div>';
    return;
  }
  const seq = $('revTask').value;
  const r = await api('GET', '/api/workshop/' + encodeURIComponent(S.ws) +
    '/tasks/' + encodeURIComponent(seq) + '/peer-review');
  if (!r.ok) {
    box.innerHTML = '<div class="peer-box"><div class="peer-head"><b>跨组织采信</b>' + peerBadge('') +
      '</div>' + errHtml(r.data, r.status) + '</div>';
    return;
  }
  box.innerHTML = peerViewHTML(r.data);
}

/** 解析「相对路径 | sha256 | 字节数」清单；**看不懂就当场说**，不静默丢弃。 */
function _peerParseFiles() {
  const box = $('peerFiles');
  const lines = String((box && box.value) || '').split('\n');
  const out = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    const parts = line.split('|').map(function (x) { return x.trim(); });
    if (parts.length < 2) return { error: '第 ' + (i + 1) + ' 行看不懂：「' + line + '」（要 相对路径 | sha256 | 字节数）' };
    const sha = parts[1];
    if (!/^[0-9a-fA-F]{64}$/.test(sha)) return { error: '第 ' + (i + 1) + ' 行的 sha256 不是 64 位十六进制：' + sha };
    const bytes = parts[2] ? Number(parts[2]) : undefined;
    if (parts[2] && (isNaN(bytes) || bytes < 0)) return { error: '第 ' + (i + 1) + ' 行的字节数不是数字：' + parts[2] };
    out.push({ name: parts[0], sha256: sha.toLowerCase(), bytes: bytes });
  }
  return { files: out };
}

async function peerDelivery() {
  if (!S.ws || !$('revTask') || !$('revTask').value) { say('peerMsg', '先选任务', 'bad'); return; }
  const p = _peerParseFiles();
  if (p.error) { say('peerMsg', p.error, 'bad'); return; }
  const seq = $('revTask').value;
  const body = {
    source: 'manual',
    result: ($('peerResult') ? $('peerResult').value.trim() : ''),
    artifacts: p.files
  };
  say('peerMsg', '正在登记…');
  const r = await api('POST', '/api/workshop/' + encodeURIComponent(S.ws) +
    '/tasks/' + encodeURIComponent(seq) + '/peer-delivery', body);
  if (!r.ok) { $('peerMsg').innerHTML = errHtml(r.data, r.status).replace(/<\/?div[^>]*>/g, ''); return; }
  await loadPeerView();
  _peerOpen();
  say('peerMsg', '✓ 已登记（采信态 accepted_self：只有它自己说，**不算通过**）', 'ok');
}

async function peerReview() {
  if (!S.ws || !$('revTask') || !$('revTask').value) { say('peerMsg', '先选任务', 'bad'); return; }
  const p = _peerParseFiles();
  if (p.error) { say('peerMsg', p.error, 'bad'); return; }
  if (!p.files.length) {
    say('peerMsg', '先在清单里贴对端给的 sha256（没有 hash 就没有可重算的东西）', 'bad');
    return;
  }
  const seq = $('revTask').value;
  say('peerMsg', '正在本侧重算…');
  const r = await api('POST', '/api/workshop/' + encodeURIComponent(S.ws) +
    '/tasks/' + encodeURIComponent(seq) + '/peer-review',
    { peer_files: p.files, checked_by: 'workbench' });
  if (!r.ok) { $('peerMsg').innerHTML = errHtml(r.data, r.status).replace(/<\/?div[^>]*>/g, ''); return; }
  const d = r.data || {};
  await loadPeerView();
  _peerOpen();
  say('peerMsg', d.accept_state === 'accepted_peer'
    ? '✓ 复核通过：本侧重算一致（accepted_peer）'
    : ('✗ 复核没对齐 ⇒ 仍停在「只有它自己说」：' + (d.why || '')),
    d.accept_state === 'accepted_peer' ? 'ok' : 'bad');
}

async function peerDispute() {
  if (!S.ws || !$('revTask') || !$('revTask').value) { say('peerJudgeMsg', '先选任务', 'bad'); return; }
  const seq = $('revTask').value;
  const by = ($('peerBy') ? $('peerBy').value.trim() : '');
  const a = ($('peerClaimA') ? $('peerClaimA').value.trim() : '');
  const b = ($('peerClaimB') ? $('peerClaimB').value.trim() : '');
  if (!a || !b) { say('peerJudgeMsg', '两边主张都要写（争议记录必须留下双方各自说了什么）', 'bad'); return; }
  say('peerJudgeMsg', '正在记…');
  const r = await api('POST', '/api/workshop/' + encodeURIComponent(S.ws) +
    '/tasks/' + encodeURIComponent(seq) + '/dispute', {
      raised_by: by || '未署名',
      claims: [{ by: '交付方', claim: a }, { by: '接收方', claim: b }],
      adjudicator: ($('peerJudge') ? $('peerJudge').value.trim() : ''),
      outcome: ($('peerOutcome') ? $('peerOutcome').value : 'disputed')
    });
  if (!r.ok) { $('peerJudgeMsg').innerHTML = errHtml(r.data, r.status).replace(/<\/?div[^>]*>/g, ''); return; }
  await loadPeerView();
  _peerOpen();
  const st = (r.data || {}).accept_state;
  say('peerJudgeMsg', st === 'refuted'
    ? '✓ 已记为「已证伪」（这条不能验收通过）'
    : '✓ 已挂争议（冻结待裁；记录不删，不能验收通过）', 'ok');
}

async function loadTasks() {
  if (!S.ws) return;
  const r = await api('GET', '/api/workshop/' + encodeURIComponent(S.ws) + '/tasks');
  if (!r.ok) { $('taskOut').innerHTML = errHtml(r.data, r.status); return; }
  const tasks = r.data.tasks || [];
  $('revTask').innerHTML = tasks.map(function (t) {
    return '<option value="' + t.task_seq + '">T-' + String(t.task_seq).padStart(3, '0') +
      '　' + esc(t.title || '') + '（' + esc(t.state) + '）</option>';
  }).join('') || '<option value="">（还没有任务）</option>';
  if (!tasks.length) { $('taskOut').innerHTML = banner('warn', '任务账本还是空的 —— 先派活。'); return; }
  $('taskOut').innerHTML = '<table><tr><th>任务</th><th>工位</th><th>状态</th><th>证据</th>' +
    '<th>验收备注</th><th>文件</th><th>派活账</th></tr>' + tasks.map(function (t) {
      const cls = t.state === 'accepted' ? 'ok' : (t.state === 'rework' ? 'warn' : '');
      return '<tr><td>T-' + String(t.task_seq).padStart(3, '0') + ' ' + esc(t.title || '') + '</td>' +
        '<td>' + esc(t.slot_dir) + '</td><td class="' + cls + '">' + esc(t.state) + '</td>' +
        '<td>' + esc(t.evidence_level || '—') + '</td><td>' + esc(t.review_note || '') + '</td>' +
        '<td><code>' + esc(t.file || '') + '</code></td>' +
        '<td><button onclick="loadDispatchDetail(' + Number(t.task_seq || 0) + ')">看</button></td></tr>';
    }).join('') + '</table>';
  await loadEvidenceView();   /* 选中任务就顺带把证据卡摆出来，别让人先点 */
}

async function doReview() {
  if (!S.ws) { say('revMsg', '先选工作间', 'bad'); return; }
  const seq = $('revTask').value;
  if (!seq) { say('revMsg', '先有任务', 'bad'); return; }
  const lv = $('revLevel').value;
  const proof = ($('revProof') ? $('revProof').value.trim() : '');
  if (lv === 'L1' && !proof) {
    /* 后端会把没凭据的 L1 按 L4 算并拒（L1_NEEDS_PROOF）—— 前端先拦一次，别白跑一趟网络 */
    $('revOut').innerHTML = banner('bad', '✗ L1 是「它库已有」，但没写凭据 —— 平台不会去替你查那个库。<br>' +
      '下一步：<b>把这个库/会话/谁核的/用什么核的写进「L1 凭据」</b>，或直接把等级改成 L4（人眼确认）。');
    return;
  }
  const body = {
    result: $('revResult').value,
    evidence_level: lv,
    evidence_ref: proof,
    note: $('revNote').value.trim()
  };
  const r = await api('POST', '/api/workshop/' + encodeURIComponent(S.ws) + '/tasks/' + seq + '/review', body);
  if (!r.ok) {
    const d0 = r.data || {};
    $('revOut').innerHTML = errHtml(d0, r.status) +
      (d0.steps ? evidenceStepsHTML(d0) : '') +
      (d0.next_detail ? '<div class="msg">下一步：' + mdInline(d0.next_detail) + '</div>' : '') +
      ((d0.code === 'PEER_NOT_REVIEWED' || d0.code === 'DISPUTE_OPEN' || d0.code === 'PEER_REFUTED')
        ? '<div class="msg">这是<b>跨组织任务</b>的采信问题（不是证据档不够）—— 去下面「跨组织采信」' +
          '做本侧重算或挂争议，再回来验收。<b>它会说「对得上」不算平台核过。</b></div>'
        : '');
    say('revMsg', '没落闸：' + (d0.code || ('HTTP ' + r.status)), 'bad');
    return;
  }
  const d = r.data || {};
  const arts = d.artifacts || {};
  let h = banner(d.state === 'accepted' ? 'ok' : 'warn',
    '任务状态：<b>' + esc(d.state) + '</b>（证据 ' +
    esc(d.evidence_label || levelLabel(d.evidence_level) || '—') + '）' +
    (d.evidence_warn ? '<br><span class="msg">' + esc(d.evidence_warn) + '</span>' : '') +
    (d.next ? '<br>' + mdInline(d.next) : ''));
  if (d.evidence_level === 'L1') {
    h += '<div class="msg">L1 的凭据是人给的，<b>平台没有去查那个库</b> —— 别把它当成平台核对过的结论。</div>';
  }
  if (arts.total_files !== undefined) {
    h += '<div class="msg">盘上现存 <b>' + esc(String(arts.total_files)) + '</b> 个文件' +
      ((arts.files_now || []).length ? '：' + esc(arts.files_now.join('、')) : '') +
      (arts.new_note ? '<br>' + esc(arts.new_note) : '') + '</div>';
  }
  if (d.evidence_view) h += evidenceViewHTML(d.evidence_view);
  $('revOut').innerHTML = h;
  say('revMsg', '已记：' + d.state, d.state === 'accepted' ? 'ok' : 'warn');
  await loadTasks();
}

async function integrate() {
  if (!S.ws) return;
  const r = await api('POST', '/api/workshop/' + encodeURIComponent(S.ws) + '/integrate-report', {});
  if (!r.ok) { $('revOut').innerHTML = errHtml(r.data, r.status); return; }
  const s = r.data.summary || {};
  $('revOut').innerHTML = banner('ok', '✓ 汇总已写：' + esc(r.data.written) +
    '<br>任务 ' + esc(s.total) + '　通过 ' + esc(s.accepted) + '　未完 ' + esc(s.open) +
    '<br><span class="msg">只写清单，不动产物文件。</span>');
}

async function dailyClose() {
  if (!S.ws) return;
  const key = $('leadKey').value.trim();
  if (!key) { say('revMsg', '要 agent_key（日结写给谁的心智根）', 'bad'); return; }
  const done = [$('revNote').value.trim() || '（今天做了点什么，写在这里）'];
  const r = await api('POST', '/api/workshop/' + encodeURIComponent(S.ws) + '/daily-close',
    { agent_key: key, done: done });
  if (!r.ok) { $('revOut').innerHTML = errHtml(r.data, r.status); return; }
  $('revOut').innerHTML = banner('ok', '✓ 日结写回根A：' + esc((r.data.written || []).join('、')) +
    '<br><span class="msg">压缩成 3 行、重写索引是夜间审忆员的活（默认关）。</span>');
}

async function closeWs() {
  if (!S.ws) return;
  if (!confirm('归档会：置 closed + 吊销全部 token。确定？')) return;
  const r = await api('POST', '/api/workshop/' + encodeURIComponent(S.ws) + '/close', {});
  if (!r.ok) { $('revOut').innerHTML = errHtml(r.data, r.status); return; }
  $('revOut').innerHTML = banner('ok', '✓ 已归档（closed）。吊销了 ' +
    esc((r.data.revoked || []).length) + ' 个 token；工作区目录原样保留。');
}

/* ── 8. 结构化讨论（AILP 2.0：类型与字段表**一律来自 /api/ailp/types**）──────
 * ★2026-11-01（V122 批 1）：四类 → 七类（新增 CLARIFY / ACT / RECOMMEND）。
 *   纪律：这里**不许再写死类型清单** —— 下拉与表单都按 types 生成，以后加类型不用改前端。 */
const AILP_LABELS = {
  proposal: '提案', confidence: '把握程度', basis_ids: '依据',
  contradiction: '哪里对不上', evidence_ids: '证据',
  target_id: '改哪条', modifications: '要改什么', new_confidence: '改成多确定',
  echo_target: '呼应哪条', shared_context: '共享的上下文',
  counter_proposal: '反提案',
  /* 新三类的字段（V122 批 1） */
  question: '你的问题', to_ref: '问的是哪条',
  task_ref: '任务号', owner: '谁负责',
  due: '截止', reason: '为什么'
};
const AILP_COLORS = {
  PROPOSE: 'var(--accent)', CHALLENGE: 'var(--bad)', REFINE: 'var(--warn)', CONTEXT_ECHO: 'var(--ok)',
  CLARIFY: 'var(--muted)', ACT: 'var(--accent)', RECOMMEND: 'var(--ok)'
};
const AILP = { types: null, items: [], tasks: [], refs: [] };

/** 超过这么多条才在块内上下滚（短列表保持自然高度，不给 3 条内容加个小滚动条）。 */
const AILP_SCROLL_AT = 6;

/* ★2026-11-05（老板："结构化讨论区有啥用，用户体验不好"）：
 * 原来这张表单是**把 agent 的报文格式直接摊给人填**（msg_id / 0~1 置信度 / 手抄任务号）——
 * 人当然不知道那些是什么。现在的口径：
 *   ① 先让人**看懂这间在讨论什么**（列表在上，卡片说人话）；
 *   ② 想说一句时，**选一句人话**（提方案/质疑/问一句/派个活），字段能选就不手填；
 *   ③ 协议细节（信封/字段表）仍然由后端 `/api/ailp/types` 决定，前端不另立口径。 */

/** 人话入口 → 后端类型（值必须与 `/api/ailp/types` 的 msg_type 一致）。 */
const AILP_QUICK = {
  PROPOSE: { label: '提个方案', hint: '一句话说清你建议做什么；依据可以从下面已有条目里选' },
  CHALLENGE: { label: '质疑一条', hint: '指出哪一条对不上：证据可以选任务号或已有讨论；外部证据（链接/文件/实验）要先「登记成证据」再引用' },
  CLARIFY: { label: '问一句', hint: '问清楚一件事；问的是哪条可以选' },
  ACT: { label: '派个活', hint: '谁做什么、什么时候；任务号从账本里选' }
};

/** 一条消息的可读摘要（给"引用哪条"的下拉 + 卡片标题用）。 */
function _ailpBrief(it) {
  const p = (it && it.payload) || {};
  const keys = ['proposal', 'question', 'modifications', 'shared_context', 'reason',
    'contradiction', 'echo_target', 'task_ref'];
  let s = '';
  for (let i = 0; i < keys.length; i++) {
    const v = p[keys[i]];
    if (v) { s = Array.isArray(v) ? v.join('、') : String(v); if (s) break; }
  }
  if (!s) {
    const k = Object.keys(p)[0];
    if (k) s = Array.isArray(p[k]) ? p[k].join('、') : String(p[k]);
  }
  s = s.replace(/\s+/g, ' ').slice(0, 46);
  return (s || '（这条没写正文）');
}

/** 「引用哪条」的选项：只列**账本里真实存在**的东西（后端 A014 会校验，前端先给对的选择）。
 *
 *  ★2026-11-05 实测（AILP 的 `evidence_ids` 用 `任务/T-002` 被 422）：
 *    后端认的是**三类账本内实体** —— `msg_id` / 任务号（`T-001`）/ manifest 条目（`_lead/建模.md`）。
 *    外部证据（URL/文件/实验）必须先 `POST …/evidence/register` 变成 `ev-xxxx` 才能引用。
 *    ⇒ 下拉直接把这三类列出来，**第一条讨论也有得选**（原来只能选已有 msg_id ⇒ 首条永远发不出去）。
 */
function _ailpRefOptions(kind) {
  const out = [];
  (AILP.items || []).forEach(function (it, i) {
    const id = String(it.msg_id || '');
    if (!id) return;
    out.push([id, '讨论 #' + (it.seq != null ? it.seq : (i + 1)) + '　' +
      String(it.msg_type || '') + ' · ' + _ailpBrief(it)]);
  });
  (AILP.tasks || []).forEach(function (t) {
    const id = 'T-' + String(t.task_seq).padStart(3, '0');
    out.push([id, '任务 ' + id + '　' + String(t.title || '').slice(0, 40)]);
  });
  (AILP.files || []).forEach(function (f) {
    out.push([String(f), '文件 ' + String(f)]);
  });
  if (!out.length) {
    return '<option value="">（这间还没有可引用的东西：先派个活、或先在 manifest 里登记文件）</option>';
  }
  return out.map(function (x) {
    return '<option value="' + esc(x[0]) + '">' + esc(x[1]) + '</option>';
  }).join('');
}

/** 七个类型的中文名（后端枚举不动；界面不让人看英文）。 */
const AILP_TYPE_ZH = {
  PROPOSE: '提案', CHALLENGE: '质疑', REFINE: '改进', CONTEXT_ECHO: '呼应',
  CLARIFY: '提问', ACT: '行动项', RECOMMEND: '推荐'
};

function _ailpTypeLabel(t) {
  const zh = AILP_TYPE_ZH[t] || _ailpQuickLabel(t) || '';
  return zh ? (zh + '（' + t + '）') : String(t || '');
}
function _ailpQuickLabel(t) {
  return (AILP_QUICK[t] || {}).label || '';
}

/** 人话按钮：设类型 + 展开 + 提示下一步（不代替用户写内容）。 */
function ailpQuick(type) {
  const d = document.getElementById('ailpCompose');
  if (d) d.open = true;
  const sel = $('ailpType');
  if (sel) {
    const has = Array.prototype.some.call(sel.options, function (o) { return o.value === type; });
    if (has) sel.value = type;
  }
  renderAilpForm();
  const h = (AILP_QUICK[type] || {}).hint || '';
  say('ailpSendMsg', h ? ('已切到「' + (AILP_QUICK[type].label || type) + '」：' + h) : '');
  const box = $('ailpForm');
  if (box && box.scrollIntoView) box.scrollIntoView({ block: 'center', behavior: 'smooth' });
}

/* 下拉选项由 types 生成（以前写死在 v5.html ⇒ 后端加了新类型，界面上根本选不到） */
function fillAilpTypeOptions() {
  const sel = $('ailpType');
  if (!sel) return;
  const types = AILP.types || [];
  const keep = sel.value;
  sel.innerHTML = types.map(function (t) {
    return '<option value="' + esc(t.msg_type) + '">' + esc(_ailpTypeLabel(t.msg_type)) + '</option>';
  }).join('');
  if (keep && types.some(function (t) { return t.msg_type === keep; })) sel.value = keep;
}

function ailpTypeDef(t) {
  const list = AILP.types || [];
  for (let i = 0; i < list.length; i++) if (list[i].msg_type === t) return list[i];
  return null;
}
function ailpFieldKind(typeStr) {
  const s = String(typeStr || '');
  if (s.indexOf('list') >= 0) return 'list';
  if (s.indexOf('int') >= 0 || s.indexOf('float') >= 0) return 'number';
  return 'str';
}
async function loadAilpTypes() {
  if (AILP.types) { fillAilpTypeOptions(); renderAilpForm(); return; }
  const r = await api('GET', '/api/ailp/types');
  if (!r.ok) { $('ailpForm').innerHTML = errHtml(r.data, r.status); return; }
  AILP.types = r.data.types || [];
  /* ★必须先填下拉，再渲染表单 —— 下拉是空的，renderAilpForm 会拿不到 def（选不出类型）。
     2026-11-01 实测抓到的坑：只加函数不调用 = 下拉空 = 什么都发不出去。 */
  fillAilpTypeOptions();
  renderAilpForm();
}
/** 一个字段 → 一个控件。原则：**能选就不手填**（msg_id / 任务号 / 置信度这些不是人该背的）。 */
function _ailpControl(f, optional) {
  const name = String(f.name || '');
  const kind = ailpFieldKind(f.type);
  const id = 'aip_' + name;
  const label = esc(AILP_LABELS[name] || name) + (optional ? '<span class="msg">（可选）</span>' : '');
  const tail = optional ? ' data-optional="1"' : '';

  if (kind === 'number') {
    /* 置信度这类：原来要人敲 0~1 —— 改成三档人话 */
    return '<label class="f">' + label +
      '<select id="' + id + '" data-kind="number"' + tail + '>' +
      '<option value="">（不填）</option>' +
      '<option value="0.8">很有把握（0.8）</option>' +
      '<option value="0.5">一般（0.5）</option>' +
      '<option value="0.2">还没底（0.2）</option>' +
      '</select></label>';
  }
  if (name === 'basis_ids' || name === 'evidence_ids') {
    return '<label class="f" style="flex-basis:100%;min-width:320px">' + label +
      '<select id="' + id + '" data-kind="list" multiple size="4"' + tail +
      ' style="min-width:320px;max-height:150px">' +
      _ailpRefOptions('list') + '</select>' +
      '<span class="msg">按住 Ctrl / ⌘ 可多选 —— 选项就是这间已有的讨论 / 任务 / 文件，不用抄 id</span></label>';
  }
  if (name === 'target_id' || name === 'to_ref') {
    return '<label class="f" style="flex:1;min-width:250px">' + label +
      '<select id="' + id + '" data-kind="str"' + tail + '>' +
      '<option value="">（不指定 —— 指整段讨论）</option>' + _ailpRefOptions('one') + '</select></label>';
  }
  if (name === 'task_ref') {
    const opts = (AILP.tasks || []).map(function (t) {
      return '<option value="T-' + String(t.task_seq).padStart(3, '0') + '">T-' +
        String(t.task_seq).padStart(3, '0') + '　' + esc(t.title || '') + '（' + esc(t.state || '') + '）</option>';
    }).join('');
    return '<label class="f">' + label +
      '<select id="' + id + '" data-kind="str"' + tail + '>' +
      (opts || '<option value="">（账本里还没有任务）</option>') + '</select></label>';
  }
  if (name === 'owner') {
    const opts = (S.members || []).map(function (m) {
      return '<option value="' + esc(m.display_name || m.role || '') + '">' +
        esc((m.role || '') + ' · ' + (m.display_name || '')) + '</option>';
    }).join('');
    return '<label class="f">' + label +
      '<select id="' + id + '" data-kind="str"' + tail + '>' +
      (opts || '<option value="">（名单里还没有人）</option>') + '</select></label>';
  }
  const ph = kind === 'list' ? '逗号分隔，例：T-001,T-002' : '一句话说清';
  return '<label class="f" style="flex:1;min-width:220px">' + label +
    '<input id="' + id + '" data-kind="' + kind + '" placeholder="' + esc(ph) + '"' + tail + '></label>';
}

function renderAilpForm() {
  const def = ailpTypeDef($('ailpType').value);
  const box = $('ailpForm');
  if (!box) return;
  if (!def) { box.innerHTML = '<div class="msg">字段表还没取到（GET /api/ailp/types）。</div>'; return; }
  const q = AILP_QUICK[def.msg_type] || {};
  box.innerHTML = (q.hint ? ('<div class="msg">' + esc(q.hint) + '</div>') : '') +
    '<div class="row">' + (def.required_payload || []).map(function (f) {
      return _ailpControl(f, false);
    }).join('') + ((def.optional_payload || []).map(function (f) {
      return _ailpControl(f, true);
    }).join('')) + '</div>';
}

function _ailpRead(el, kind) {
  if (!el) return undefined;
  if (kind === 'list') {
    if (el.multiple) {
      return Array.prototype.map.call(el.selectedOptions || [], function (o) { return o.value; })
        .filter(Boolean);
    }
    return String(el.value || '').split(/[,，\s]+/).filter(Boolean);
  }
  const raw = String(el.value || '').trim();
  if (raw === '') return undefined;
  if (kind === 'number') {
    const n = parseFloat(raw);
    return isNaN(n) ? null : n;
  }
  return raw;
}

function ailpPayloadFromForm() {
  const def = ailpTypeDef($('ailpType').value);
  const payload = {};
  const bad = [];
  ((def && def.required_payload) || []).forEach(function (f) {
    const el = $('aip_' + f.name);
    const kind = el ? el.getAttribute('data-kind') : ailpFieldKind(f.type);
    const v = _ailpRead(el, kind);
    if (v === undefined || (Array.isArray(v) && !v.length)) {
      bad.push(AILP_LABELS[f.name] || f.name);
      return;
    }
    if (v === null) { bad.push((AILP_LABELS[f.name] || f.name) + '（要数字）'); return; }
    payload[f.name] = v;
  });
  ((def && def.optional_payload) || []).forEach(function (f) {
    const el = $('aip_' + f.name);
    if (!el) return;
    const kind = el.getAttribute('data-kind') || ailpFieldKind(f.type);
    const v = _ailpRead(el, kind);
    if (v === undefined || (Array.isArray(v) && !v.length)) return;   /* 可选：留空就不发 */
    if (v === null) { bad.push((AILP_LABELS[f.name] || f.name) + '（要数字）'); return; }
    payload[f.name] = v;
  });
  return { payload: payload, bad: bad };
}

function _ailpMsgLabel(id) {
  const items = AILP.items || [];
  for (let i = 0; i < items.length; i++) {
    if (String(items[i].msg_id || '') === String(id)) {
      return '#' + (items[i].seq != null ? items[i].seq : (i + 1)) + ' ' + _ailpBrief(items[i]);
    }
  }
  return String(id || '');
}

function _ailpWho(s) {
  return (s === 'user') ? '我' : String(s || '（未署名）');
}

/** ISO → 本地 `11-05 19:47`（原值留在 title 里，取证时还能看原文）。 */
function _ailpWhen(iso) {
  if (!iso) return '';
  const dt = new Date(iso);
  if (isNaN(dt.getTime())) return String(iso);
  const p = function (x) { return String(x).padStart(2, '0'); };
  return p(dt.getMonth() + 1) + '-' + p(dt.getDate()) + ' ' +
    p(dt.getHours()) + ':' + p(dt.getMinutes());
}

function _ailpClip(v, n) {
  const s = Array.isArray(v) ? v.join('、') : String(v == null ? '' : v);
  return s.length > n ? (s.slice(0, n) + '…') : s;
}

const AILP_REF_FIELDS = {
  basis_ids: 1, evidence_ids: 1, target_id: 1, to_ref: 1, echo_target: 1, task_ref: 1
};
const AILP_NUM_FIELDS = { confidence: 1, new_confidence: 1 };

/** 一个字段值 → 显示 HTML：引用类做成 chip（`msg_id` 映射成 `#2 改进`），其余走弱 markdown。 */
function _ailpValueHtml(key, v) {
  if (Array.isArray(v)) {
    if (!v.length) return '';
    return v.map(function (x) {
      return '<span class="ailp-chip">' + esc(AILP_REF_FIELDS[key] ? _ailpMsgLabel(x) : String(x)) + '</span>';
    }).join('');
  }
  if (v === null || v === undefined || v === '') return '';
  if (typeof v === 'object') return '<code>' + esc(JSON.stringify(v)) + '</code>';
  if (AILP_REF_FIELDS[key]) {
    return '<span class="ailp-chip">' + esc(_ailpMsgLabel(String(v))) + '</span>';
  }
  return mdInline(String(v));
}

/** 一条讨论的卡片（2026-11-05 老板：「卡片是否过小，有很多信息怎么办」）。
 *
 *  三处结构性改动：
 *    ① 不再 `.card` 套 `.card`（换成 `.ailp-item`，有效宽度回来）；
 *    ② 宽屏**左元信息 / 右正文**（`.ailp-item` 是 grid），信息多也不往下堆；
 *    ③ 字段 >2 个或正文 >160 字 ⇒ **默认只给摘要**，点「展开全部 N 项」看细节。
 */
function ailpCard(it, idx) {
  const color = AILP_COLORS[it.msg_type] || 'var(--line)';
  const def = ailpTypeDef(it.msg_type);
  const p = it.payload || {};
  const keys = [];
  ((def && def.required_payload) || []).forEach(function (f) { keys.push(f.name); });
  Object.keys(p).forEach(function (k) { if (keys.indexOf(k) < 0) keys.push(k); });

  const rows = [];
  let textLen = 0;
  keys.forEach(function (k) {
    const raw = p[k];
    const empty = (raw === null || raw === undefined || raw === '' ||
      (Array.isArray(raw) && !raw.length));
    if (empty) return;                     /* ★空字段不显示 —— 不再拿「（空）」占地方 */
    rows.push([k, AILP_LABELS[k] || k, raw]);
    textLen += String(Array.isArray(raw) ? raw.join('、') : raw).length;
  });

  /* 折叠规则：**短条目全显示**（3 个字段且不超 200 字就是一眼能看完的），
     只有"字段多"或"正文长"才收起 —— 否则每张卡都挂一个「展开」，等于白加一层点击。 */
  const collapse = rows.length > 3 || textLen > 200;
  const no = (it.seq != null) ? ('#' + it.seq) : ('#' + (idx + 1));
  const when = _ailpWhen(it.timestamp);

  const meta = '<div class="ailp-meta">' +
    '<span class="ailp-no">' + esc(no) + '</span>' +
    '<span class="ailp-type" style="color:' + color + '">' + esc(_ailpTypeLabel(it.msg_type)) + '</span>' +
    '<span class="ailp-route" title="' + esc(_ailpWho(it.sender) + ' → ' + (it.target || '')) + '">' +
    esc(_ailpWho(it.sender)) + ' → ' + esc(it.target || '') + '</span>' +
    (when ? '<span class="ailp-when" title="' + esc(it.timestamp || '') + '">' + esc(when) + '</span>' : '') +
    (it.manifest_ref ? '<span class="ailp-ref" title="挂在哪份文件/任务">挂：' + esc(it.manifest_ref) + '</span>' : '') +
    '</div>';

  const fieldRows = rows.map(function (r2) {
    return '<div class="ailp-f"><div class="ailp-k">' + esc(r2[1]) + '</div>' +
      '<div class="ailp-v">' + _ailpValueHtml(r2[0], r2[2]) + '</div></div>';
  }).join('');

  let body;
  if (!rows.length) {
    body = '<div class="ailp-sum msg">（这条没有正文 —— 只记了类型与去向）</div>';
  } else if (!collapse) {
    body = fieldRows;
  } else {
    /* 摘要取**最长的正文类字段**（提案/要改什么/问题/为什么…），
       引用类（任务号/依据）与数字（把握程度）留在展开里 —— 拿"谁负责"当摘要没有意义。 */
    const textRows = rows.filter(function (r2) {
      return !AILP_REF_FIELDS[r2[0]] && !AILP_NUM_FIELDS[r2[0]];
    });
    const head = textRows.slice().sort(function (a, b) {
      return String(b[2] == null ? '' : b[2]).length - String(a[2] == null ? '' : a[2]).length;
    })[0] || rows[0];
    body = '<div class="ailp-sum">' + mdInline(_ailpClip(head[2], 150)) + '</div>' +
      '<details class="ailp-more"><summary>展开全部 ' + rows.length + ' 项</summary>' +
      fieldRows + '</details>';
  }

  return '<article class="ailp-item" style="border-left-color:' + color + '">' +
    meta + '<div class="ailp-body">' + body + '</div></article>';
}

async function loadAilp() {
  if (!S.ws) return;
  await loadAilpTypes();
  const base = '/api/workshop/' + encodeURIComponent(S.ws);
  /* 三件事一起取：能挂的文件/任务、可引用的已有条目、账本任务（派活用） */
  const got = await Promise.all([
    api('GET', base + '/manifest'),
    api('GET', base + '/tasks'),
    api('GET', base + '/ailp?limit=30')
  ]);
  const mf = got[0]; const tk = got[1]; const r = got[2];

  const refs = [];
  if (mf.ok && mf.data && mf.data.manifest) {
    (mf.data.manifest.entry_points || []).forEach(function (x) { refs.push(x); });
    (mf.data.manifest.active_tasks || []).forEach(function (x) { refs.push(x); });
  }
  const refSel = $('ailpRef');
  if (refSel) {
    refSel.innerHTML = refs.map(function (x) {
      return '<option value="' + esc(x) + '">' + esc(x) + '</option>';
    }).join('') || '<option value="">（manifest 里还没有条目）</option>';
  }

  AILP.tasks = (tk.ok && tk.data && tk.data.tasks) || [];
  AILP.items = (r.ok && r.data && r.data.items) || [];
  AILP.files = refs.slice();          /* 依据下拉里的"文件"选项 = manifest 的真实条目 */
  /* 引用下拉的选项来自 AILP.items ⇒ 拿到新条目后要重画表单（否则选不到刚发的那条） */
  if (AILP.types) renderAilpForm();

  if (!r.ok) {
    $('ailpOut').innerHTML = errHtml(r.data, r.status);
    const l0 = $('ailpList');
    if (l0) { l0.innerHTML = ''; l0.classList.remove('is-scroll'); }
    return;
  }
  const list = $('ailpList');
  if (!AILP.items.length) {
    $('ailpOut').innerHTML = banner('ok',
      '<b>这间还没有结构化讨论（0 条）。</b>它本来就不是必填项 —— ' +
      '只有当你想留下「谁提了什么、依据是哪条」时才用：展开下面「我要说一句」，' +
      '点一句人话（提个方案 / 质疑一条 / 问一句 / 派个活），填两句就能发。');
    if (list) { list.innerHTML = ''; list.classList.remove('is-scroll'); }
    say('ailpLoadMsg', '0 条', '');
    return;
  }
  const counts = (r.data && r.data.counts) || {};
  const sum = Object.keys(counts).map(function (k) {
    return (_ailpTypeLabel(k) || k) + ' ' + counts[k];
  }).join(' · ');
  const total = String(r.data.count || AILP.items.length);
  /* 条目多才内滚（见 .ailp-list.is-scroll）；统计行放「↓ 最新」——长列表滚上去后一键回到底 */
  const willScroll = AILP.items.length > AILP_SCROLL_AT;
  $('ailpOut').innerHTML = '<div class="row" style="margin:2px 0 6px;flex-wrap:nowrap;align-items:center">' +
    '<span class="msg" style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"' +
    (sum ? ' title="' + esc(sum) + '"' : '') + '>共 <b>' + esc(total) + '</b> 条' +
    (sum ? '<span class="msg">　·　' + esc(sum) + '</span>' : '') +
    '（按时间正序，最新的在下面）</span>' +
    (willScroll ? '<button class="btn mini" onclick="ailpScrollLatest()" style="flex:none" ' +
      'title="条目多，这一块内部上下滚；点这里回到底部最新一条">↓ 最新</button>' : '') +
    '</div>';
  if (list) {
    list.innerHTML = AILP.items.map(ailpCard).join('');
    list.classList.toggle('is-scroll', willScroll);
    if (willScroll) {
      /* 打开就定位到**最新**（时间正序 ⇒ 底部）；这是"看讨论"最常用的落点 */
      list.scrollTop = list.scrollHeight;
    } else {
      list.scrollTop = 0;
    }
  }
  say('ailpLoadMsg', '已刷新（' + total + ' 条' + (willScroll ? ' · 块内可滚' : '') + '）', 'ok');
}

/** 「↓ 最新」：滚到列表底部（最新一条）。 */
function ailpScrollLatest() {
  const l = $('ailpList');
  if (!l) return;
  l.scrollTop = l.scrollHeight;
  if (l.focus) l.focus({ preventScroll: true });
}

async function sendAilp() {
  if (!S.ws) { say('ailpSendMsg', '先选工作间', 'bad'); return; }
  const got = ailpPayloadFromForm();
  if (got.bad.length) {
    say('ailpSendMsg', '这几项没填/填错：' + got.bad.join('、'), 'bad');
    return;
  }
  const body = {
    ailp_version: '2.0',
    msg_type: $('ailpType').value,
    sender: 'user',
    target: $('ailpTarget').value.trim() || '组长',
    manifest_ref: $('ailpRef').value,
    payload: got.payload
  };
  const r = await api('POST', '/api/workshop/' + encodeURIComponent(S.ws) + '/ailp', body);
  const msg = $('ailpSendMsg');
  if (!r.ok) {
    /* ★失败不再把上面的讨论列表冲掉（原来直接写 ailpOut ⇒ 一报错就看不见历史） */
    const d = r.data || {};
    if (msg) {
      msg.innerHTML = '<span class="bad">✗ ' + esc(d.code || ('HTTP ' + r.status)) + '：' +
        esc(d.error || '没发出去') + '</span>' + (d.next ? '<br>下一步：' + esc(d.next) : '');
    }
    return;
  }
  if (msg) {
    msg.innerHTML = '<span class="ok">✓ 收下了（' + esc(r.data.msg_id || '') +
      '）—— 平台只校验与记账，没有替你或替它说话</span>';
  }
  await loadAilp();
}

/* ── 9. 闸门自省 ──────────────────────────────────────────── */
async function checkGate() {
  if (!S.ws) { say('gateMsg', '先选工作间', 'bad'); return; }
  const act = $('gateAct').value;
  const seq = $('dspSeq').value || '0';
  const r = await api('GET', '/api/workshop/' + encodeURIComponent(S.ws) + '/gate/' + act +
    '?member_seq=' + encodeURIComponent(seq));
  if (!r.ok) { $('gateOut').innerHTML = errHtml(r.data, r.status); return; }
  const d = r.data;
  if (d.allowed) {
    $('gateOut').innerHTML = banner('ok', '✓ 这个动作现在能做（' + esc(act) + '）');
    say('gateMsg', '放行', 'ok');
    return;
  }
  $('gateOut').innerHTML = banner('bad', '✗ 被拦住：' + esc(d.code)) + fourHtml(d.four);
  say('gateMsg', d.code, 'bad');
}

/* ── 10. 协议台（V116 §1.1：按层分组，不按"最好"排）────────────── */
const LAYER_NAMES = {
  transport_task: '任务语义（能派活 / 查 / 撤 / 回传产物）',
  transport_session: '会话轮次',
  transport_tools: '工具面（它调我们的工具）',
  transport_generic: '通用通道',
  transport_events: '事件流',
  transport_local: '本机进程间'
};
const LAYER_ORDER = Object.keys(LAYER_NAMES);

function protoStatusBadge(st) {
  if (st === 'available') return '<span class="ok">可用</span>';
  if (st === 'partial') return '<span class="warn">部分</span>';
  return '<span style="color:var(--dim)">未实现</span>';
}

function protoRows(rows, withLayerCol) {
  const head = withLayerCol
    ? '<tr><th>协议</th><th>层</th><th>状态</th><th>语义与边界（后端原话）</th></tr>'
    : '<tr><th>协议</th><th>状态</th><th>语义与边界（后端原话）</th><th>适合</th></tr>';
  return '<table>' + head + rows.map(function (p) {
    return '<tr><td><b>' + esc(p.name || p.id) + '</b><div class="msg">' + esc(p.id) + '</div></td>' +
      (withLayerCol ? '<td class="msg">' + esc(p.layer || '') + '</td>' : '') +
      '<td>' + protoStatusBadge(p.status) + '</td>' +
      '<td>' + esc(p.semantics || '（平台没给语义说明 —— 照实说，界面不编）') + '</td>' +
      (withLayerCol ? '' : '<td class="msg">' + esc(p.best_for || '') + '</td>') +
      '</tr>';
  }).join('') + '</table>';
}

async function loadProtoPanel() {
  const r = await api('GET', '/api/protocols');
  if (!r.ok) { $('protoOut').innerHTML = errHtml(r.data, r.status); return; }
  const list = r.data.protocols || [];
  if (!list.length) {
    $('protoOut').innerHTML = banner('warn', '协议目录是空的（平台没给条目）—— 照实显示，不放假协议。');
    return;
  }
  let h = '';
  LAYER_ORDER.forEach(function (layer) {
    const rows = list.filter(function (p) { return p.layer === layer; });
    if (!rows.length) return;
    h += '<div class="msg" style="margin-top:10px"><b>' + esc(LAYER_NAMES[layer]) + '</b>' +
      '　<span class="msg">' + esc(layer) + '</span></div>' + protoRows(rows, false);
  });
  const odd = list.filter(function (p) { return LAYER_ORDER.indexOf(p.layer) < 0; });
  if (odd.length) {
    h += '<div class="msg" style="margin-top:10px"><b>其它层</b>' +
      '　<span class="msg">后端新给的层名，界面上还没有固定展示名 —— 照实列出，不猜</span></div>' +
      protoRows(odd, true);
  }
  h += '<div class="msg" style="margin-top:8px">分层口径（V116 §1.1）：身份与任务归属在平台自己的名单与账本里（第①②层），' +
    '这里列的是第③层「传输与调用」的通道；第④层「触达」见对接单与唤醒结果。<b>不按「哪个协议最好」排序。</b></div>';
  $('protoOut').innerHTML = h;
}

async function loadToolingPanel() {
  const r = await api('GET', '/api/harness-tooling');
  if (!r.ok) { $('protoToolOut').innerHTML = errHtml(r.data, r.status); return; }
  const items = r.data.items || [];
  if (!items.length) {
    $('protoToolOut').innerHTML = banner('warn', '还没有登记的 harness —— 先去对接中心登记。');
    return;
  }
  $('protoToolOut').innerHTML = '<table><tr><th>harness</th><th>模型</th><th>实测写盘</th><th>来源</th>' +
    '<th>唤醒</th><th>协议/桥</th><th>能不能当组长</th><th>原因（平台原话）</th><th></th></tr>' +
    items.map(function (it) {
      const t = it.tooling || {};
      const fs = t.fs_write === true ? '<span class="ok">能</span>'
        : (t.fs_write === false ? '<span class="bad">不能</span>'
          : '<span style="color:var(--dim)">测不到</span>');
      const can = it.lead_eligible ? '<span class="ok">能</span>' : '<span class="bad">不能</span>';
      return '<tr><td>' + esc(it.harness_id) + '<div class="msg">' + esc(it.harness_name || '') + '</div></td>' +
        '<td>' + esc(it.model || '') + '</td>' +
        '<td>' + fs + '</td><td>' + esc(t.fs_write_source || '声明') + '</td>' +
        '<td>' + esc(t.wakeup_method || '') + '</td>' +
        '<td>' + (t.protocol_call ? '协议 ' : '') + (t.bridge_poll ? '桥' : '') + '</td>' +
        '<td>' + can + '</td>' +
        '<td class="msg">' + esc(t.why || '（平台没给原因）') + '</td>' +
        '<td><button onclick="probePanel(\'' + esc(it.harness_id) + '\')">实测写一次盘</button></td></tr>';
    }).join('') + '</table>' +
    '<div class="msg">组长门槛（V116 §9）：<b>能写文件</b> 且 <b>能把回执送回来</b>（协议或桥）。' +
    '「实测」是平台在自己的数据目录真写一次再读回；它只证明<b>平台侧落点可写</b>，' +
    '不等于那个 agent 自己有 fs 工具。</div>';
}

async function probePanel(hid) {
  const r = await api('POST', '/api/harness/' + encodeURIComponent(hid) + '/probe-tooling', {});
  if (!r.ok) {
    $('protoToolOut').innerHTML = errHtml(r.data, r.status) + $('protoToolOut').innerHTML;
    return;
  }
  await loadToolingPanel();
  await loadTooling();
  await loadRoster();
}

/* ── 11. 审忆待审（只读列表 + 看/复制 diff + 记审批 + 落地；平台不代改认知，V117 §9）── */
/* 落地端点 D后端-55：POST /api/mind/{key}/pending/{name}/apply
   两段式（老板定的红线）：先「记审批」，approved 之后才允许「落地到基模」。
   ★D后端-58：列表接口现在读 *.applied.json ⇒ status=applied 是后端真状态（本页不再做任何本地标记）。 */

function pendingKeyOrSay() {
  const key = $('pdKey').value.trim();
  if (!key) { say('pdMsg', '要 agent_key（心智根键，建工作间时填的那个）', 'bad'); return ''; }
  return key;
}

async function loadPending() {
  const key = pendingKeyOrSay();
  if (!key) return;
  const r = await api('GET', '/api/mind/' + encodeURIComponent(key) + '/pending');
  if (!r.ok) { $('pdOut').innerHTML = errHtml(r.data, r.status); return; }
  const d = r.data;
  let h = '';
  if (d.hint) h += '<div class="msg">' + esc(d.hint) + '</div>';
  const items = d.items || [];
  if (!items.length) {
    h += banner('warn', 'count=' + esc(String(d.count)) + '：还没有待审项 —— 空就是空，不放假条目。');
    $('pdOut').innerHTML = h;
    say('pdMsg', '没有待审项', 'warn');
    return;
  }
  h += '<table><tr><th>待审项</th><th>目标</th><th>状态</th><th>diff</th><th></th></tr>' +
    items.map(function (it) {
      let st, applyBtn = '';
      if (it.status === 'pending') {
        st = '<span class="warn">待审</span>';
      } else if (it.status === 'approved') {
        st = '<span class="ok">已批</span>' +
          '<div class="msg">' + esc(it.approved_by || '') + ' · ' + esc(it.approved_at || '') + '</div>';
        /* 落地按钮只在 approved 时出现：没批就让它出现等于诱导跳过审批（红线） */
        applyBtn = ' <button class="p" onclick="applyPending(\'' + esc(it.name) + '\')">落地到基模</button>';
      } else if (it.status === 'applied') {
        /* ★D后端-58：后端读真 *.applied.json —— 已落地的显示双 hash + 归档路径，不可再批再落地 */
        st = '<span class="ok">已落地</span>' +
          '<div class="msg">' + esc(it.applied_by || '') + ' · ' + esc(it.applied_at || '') + '</div>' +
          '<div class="msg">before <code>' + esc(String(it.before_sha256 || '').slice(0, 12)) +
          '</code> → after <code>' + esc(String(it.after_sha256 || '').slice(0, 12)) + '</code></div>' +
          (it.archive ? '<div class="msg">原文归档：<code>' + esc(it.archive) + '</code></div>' : '');
      } else if (it.status === 'applied_record_unreadable') {
        /* 落地记录读坏 ⇒ 别当 applied，也别再出按钮（D后端-58 §2） */
        st = '<span class="bad">落地记录读坏了（applied_record_unreadable）—— 不当已落地，落地按钮也不出，先查那条 .applied.json</span>';
      } else if (it.status === 'approved_record_unreadable') {
        st = '<span class="bad">审批记录坏了（approved_record_unreadable）—— 照实标，不当已批，不出落地按钮</span>';
      } else {
        st = '<span class="bad">审批记录坏了（' + esc(it.status) + '）—— 照实标，不当已批</span>';
      }
      const approveBtn = it.status === 'pending'
        ? ' <button class="p" onclick="approvePending(\'' + esc(it.name) + '\')">记审批</button>'
        : '';
      return '<tr><td><code>' + esc(it.name) + '</code><div class="msg">' + esc(it.modified_at || '') +
        ' · ' + esc(String(it.bytes || 0)) + ' 字节</div></td>' +
        '<td>' + esc(it.target || '（diff 头里没读出目标）') + '</td>' +
        '<td>' + st + '</td>' +
        '<td class="msg">' + esc(String(it.diff_lines || 0)) + ' 行</td>' +
        '<td><button onclick="showPendingDiff(\'' + esc(it.name) + '\')">看 diff</button>' + approveBtn + applyBtn +
        '</td></tr>';
    }).join('') + '</table>' +
    '<pre id="pdDiff" hidden></pre>' +
    '<div class="row"><button onclick="copyEl(\'pdDiff\')">复制 diff 原文</button>' +
    '<span class="msg">看/复制不改任何文件；「落地到基模」只在<b>已批</b>后出现，点一次就真写核心基模（原文先归档）。</span></div>';
  $('pdOut').innerHTML = h;
  say('pdMsg', '待审 ' + items.length + ' 项', 'warn');
}

async function showPendingDiff(name) {
  const key = pendingKeyOrSay();
  if (!key) return;
  const r = await api('GET', '/api/mind/' + encodeURIComponent(key) + '/pending/' + encodeURIComponent(name));
  const pre = $('pdDiff');
  if (!r.ok) {
    if (pre) { pre.hidden = false; pre.textContent = ''; }
    $('pdActOut').innerHTML = errHtml(r.data, r.status);
    return;
  }
  if (!pre) return;
  pre.hidden = false;
  pre.textContent = r.data.text || '';
  say('pdMsg', 'diff 已展开（' + name + '）', 'ok');
}

async function approvePending(name) {
  const key = pendingKeyOrSay();
  if (!key) return;
  const who = $('pdApprover').value.trim();
  if (!who) { say('pdMsg', '「批准人」必填 —— 谁批的要留痕（例：boss / user）', 'bad'); return; }
  if (!confirm('给 ' + name + ' 记审批（批准人：' + who + '）？\n这只记审批：核心基模不会被平台改动。')) return;
  const r = await api('POST', '/api/mind/' + encodeURIComponent(key) + '/pending/' +
    encodeURIComponent(name) + '/approve', { approver: who });
  if (!r.ok) { $('pdActOut').innerHTML = errHtml(r.data, r.status); return; }
  const d = r.data;
  if (d.already) {
    $('pdActOut').innerHTML = banner('warn',
      '这条已批过（批准人 ' + esc(d.approved_by || '') + ' · ' + esc(d.approved_at || '') +
      '）；<b>没有覆盖原记录</b>。');
  } else {
    $('pdActOut').innerHTML = banner('ok',
      '✓ 已记审批 · 批准人 ' + esc(d.approved_by || '') + ' · ' + esc(d.approved_at || '') +
      '<br><b>核心基模未改 —— 真正写入要人按 diff 手工应用</b>');
  }
  say('pdMsg', d.already ? '已批过（不覆盖）' : '审批已记', d.already ? 'warn' : 'ok');
  await loadPending();
}

/* 落地到基模（D后端-55）：真写 00_核心基模.md，原文先归档 _archive/。
   错误码照实显示（errHtml 已渲染 code/error/next）：NOT_APPROVED 要先批、
   CORE_CHANGED_SINCE_PROPOSAL 说明旧补丁过期、NOT_A_UNIFIED_DIFF 是建议稿不能套、
   DIFF_DOES_NOT_APPLY 把带行号的 error 原文摊出来。 */
async function applyPending(name) {
  const key = pendingKeyOrSay();
  if (!key) return;
  const who = $('pdApprover').value.trim();
  if (!who) { say('pdMsg', '「批准人」必填 —— 谁落的这一笔要留痕（例：boss / user）', 'bad'); return; }
  if (!confirm('把 ' + name + ' 落地到基模（批准人：' + who + '）？\n\n' +
    '会真正改写 00_核心基模.md（原文会先归档到 _archive/，可回溯）。\n' +
    '这一步不是记账：点下去 AI 的认知就变了。')) return;
  const r = await api('POST', '/api/mind/' + encodeURIComponent(key) + '/pending/' +
    encodeURIComponent(name) + '/apply', { approver: who });
  if (!r.ok) {
    /* 三类 409 给一句人话在上、后端原文在下，别只丢一张错误码表给人猜 */
    const code = (r.data && r.data.code) || '';
    const lead = {
      NOT_APPROVED: '还没批 —— 先点「记审批」，再点「落地到基模」。',
      CORE_CHANGED_SINCE_PROPOSAL: '基模在批准后被改过，这条旧补丁不能再套；重新让审忆员生成待审项。',
      NOT_A_UNIFIED_DIFF: '这条是建议稿（说明+改前 hash），不是可套用的补丁 —— 请人按它手改。',
      DIFF_DOES_NOT_APPLY: 'diff 对不上原文（下面 error 里有具体行号与期望/实际）。',
      NO_SUCH_PENDING: '这条待审项已经没了 —— 刷新列表。'
    }[code] || '';
    $('pdActOut').innerHTML = (lead ? banner('bad', esc(lead)) : '') + errHtml(r.data, r.status);
    say('pdMsg', '落地失败 · ' + (code || ('HTTP ' + r.status)), 'bad');
    return;
  }
  const d = r.data || {};
  $('pdActOut').innerHTML = banner('ok',
    '✓ 已落地 · <code>' + esc(name) + '</code> 已写进核心基模' +
    '<br>before <code>' + esc(String(d.before_sha256 || '').slice(0, 12)) + '</code> → after <code>' +
    esc(String(d.after_sha256 || '').slice(0, 12)) + '</code>' +
    '<br>原文已归档：<code>' + esc(d.archive || '（后端没回 archive 路径）') + '</code>' +
    '<br><span class="msg">落地人 ' + esc(d.applied_by || who) + ' · ' + esc(d.applied_at || '') +
    '　—— 这是人拍板之后的落地，平台没有自己改认知。</span>');
  say('pdMsg', '已落地（基模已变，可回溯）', 'ok');
  await loadPending();
}

function locate() {
  loadWs();
  loadProtoPanel();
  loadToolingPanel();
  loadAilpTypes();
}
document.addEventListener('DOMContentLoaded', function () { locate(); });
