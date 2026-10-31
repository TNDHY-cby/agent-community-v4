/* mind.js — 心智档案页（2026-10-30，D后端-56 §3-2）
 *
 * 口径（与 v5.js 一致，别在这里另立）：
 * - 只把后端真实返回照实显示；每个失败都渲染 code / error / next，不许只弹"失败"；
 * - 本页不做审批与落地（那两步在控制台 PANEL-B 两段式执行），防同一红线两份实现。
 */
'use strict';

const _KEY0 = new URLSearchParams(location.search).get('key') || '';
let CUR = '';                       // 当前已加载档案的 agent_key

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
function banner(kind, html) { return '<div class="banner ' + kind + '">' + html + '</div>'; }
function errHtml(d, status) {
  const code = d && d.code ? d.code : ('HTTP ' + status);
  let h = '<div class="bad">✗ ' + esc(code) + '：' + esc((d && d.error) || '请求失败') + '</div>';
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

function keyOrSay() {
  const key = $('mKey').value.trim();
  if (!key) { say('mMsg', '要 agent_key（心智根键，建工作间时填的那个）', 'bad'); return ''; }
  return key;
}

/* ── 档案实况 ── */
async function loadMind() {
  const key = keyOrSay();
  if (!key) return;
  CUR = key;
  const r = await api('GET', '/api/mind/' + encodeURIComponent(key));
  if (!r.ok) {
    const noMind = r.data && r.data.code === 'NO_SUCH_MIND';
    $('mOut').innerHTML = (noMind
      ? banner('warn', '还没有这个心智根 —— 下面「建骨架（没有时）」点一次就有（幂等，不动已有文件）。')
      : '') + errHtml(r.data, r.status);
    $('pOut').innerHTML = '<div class="msg">档案都没有，谈不上待审 —— 先建根。</div>';
    say('mMsg', '没读到档案 · ' + ((r.data && r.data.code) || ('HTTP ' + r.status)), 'bad');
    return;
  }
  const d = r.data;
  const files = d.files || {};
  const counts = d.counts || {};
  const chips = function (arr) {
    return (arr || []).length
      ? '<div class="chips">' + arr.map(function (n) { return '<span class="chip">' + esc(n) + '</span>'; }).join('') + '</div>'
      : '<div class="msg">（还没有）</div>';
  };
  $('mOut').innerHTML =
    '<div class="kv">' +
      '<span class="k">根A 路径</span><span><code>' + esc(d.mind_path || '') + '</code></span>' +
      '<span class="k">00_宪章</span><span><code>' + esc(String(files['00_宪章.md'] ?? 0)) + '</code> 字节</span>' +
      '<span class="k">00_核心基模</span><span><code>' + esc(String(files['00_核心基模.md'] ?? 0)) + '</code> 字节</span>' +
      '<span class="k">01_记忆索引表</span><span><code>' + esc(String(files['01_记忆索引表.md'] ?? 0)) + '</code> 字节</span>' +
    '</div>' +
    '<div class="stat3">' +
      '<div><b>' + esc(String(counts.memories ?? 0)) + '</b><span>条记忆（memories/）</span></div>' +
      '<div><b>' + esc(String(counts.task_logs ?? 0)) + '</b><span>篇任务日志（02_任务日志/）</span></div>' +
      '<div><b>' + esc(String(counts.dead_ends ?? 0)) + '</b><span>条死胡同（06_死胡同/）</span></div>' +
    '</div>' +
    '<div class="msg">最近记忆（后端只给最近 20 个文件名；正文不在接口里，要看内容去根A 目录）：</div>' +
    chips(d.memories) +
    '<div class="msg">最近任务日志（最近 10 个）：</div>' +
    chips(d.task_logs) +
    (d.hint ? '<div class="msg">' + esc(d.hint) + '</div>' : '');
  say('mMsg', '档案已读 · ' + key, 'ok');
  await loadPending();
}

/* 建骨架（幂等）：404 时给的自救路径（后端 next 原话就是 POST 建） */
async function ensureMind() {
  const key = keyOrSay();
  if (!key) return;
  const name = $('mKey').value.trim();
  const r = await api('POST', '/api/mind/' + encodeURIComponent(key),
                      { display_name: name });
  if (!r.ok) { $('mOut').innerHTML = errHtml(r.data, r.status); return; }
  $('mOut').innerHTML = banner('ok', '✓ 骨架就绪（幂等：已有的一个字节没动）。下面是实况：');
  await loadMind();
}

/* ── 待审队列（只读；审批/落地在控制台 PANEL-B） ── */
async function loadPending() {
  if (!CUR) return;
  const r = await api('GET', '/api/mind/' + encodeURIComponent(CUR) + '/pending');
  if (!r.ok) { $('pOut').innerHTML = errHtml(r.data, r.status); return; }
  const d = r.data;
  let h = '';
  if (d.hint) h += '<div class="msg">' + esc(d.hint) + '</div>';
  const items = d.items || [];
  if (!items.length) {
    h += banner('warn', 'count=' + esc(String(d.count)) + '：这个根还没有待审项 —— 空就是空。');
    $('pOut').innerHTML = h;
    return;
  }
  h += '<table><tr><th>待审项</th><th>目标</th><th>状态</th><th>diff</th><th></th></tr>' +
    items.map(function (it) {
      let st;
      if (it.status === 'pending') st = '<span class="msg warn">待审</span>';
      else if (it.status === 'approved') st = '<span class="msg ok">已批</span>' +
        '<div class="msg">' + esc(it.approved_by || '') + ' · ' + esc(it.approved_at || '') + '</div>';
      else st = '<span class="msg bad">审批记录坏了（' + esc(it.status) + '）—— 照实标</span>';
      return '<tr><td><code>' + esc(it.name) + '</code><div class="msg">' + esc(it.modified_at || '') +
        ' · ' + esc(String(it.bytes || 0)) + ' 字节</div></td>' +
        '<td>' + esc(it.target || '（diff 头里没读出目标）') + '</td>' +
        '<td>' + st + '</td>' +
        '<td class="msg">' + esc(String(it.diff_lines || 0)) + ' 行</td>' +
        '<td><button onclick="showDiff(\'' + esc(it.name) + '\')">看 diff</button></td></tr>';
    }).join('') + '</table>' +
    '<pre id="mpDiff" hidden></pre>' +
    '<div class="row"><button onclick="copyEl(\'mpDiff\')">复制 diff 原文</button>' +
    '<span class="msg">这里只看；<b>记审批 / 落地到基模去控制台 PANEL-B</b>（两段式，先批后落）。</span></div>';
  $('pOut').innerHTML = h;
}

async function showDiff(name) {
  if (!CUR) return;
  const r = await api('GET', '/api/mind/' + encodeURIComponent(CUR) + '/pending/' + encodeURIComponent(name));
  const pre = $('mpDiff');
  if (!r.ok) {
    if (pre) { pre.hidden = false; pre.textContent = ''; }
    $('pOut').insertAdjacentHTML('beforeend', errHtml(r.data, r.status));
    return;
  }
  if (!pre) return;
  pre.hidden = false;
  pre.textContent = r.data.text || '';
  say('mMsg', 'diff 已展开（' + name + '）', 'ok');
}

function copyEl(id) {
  const el = $(id);
  if (!el) return;
  navigator.clipboard.writeText(el.textContent || '').then(
    function () { say('mMsg', '已复制', 'ok'); },
    function () { say('mMsg', '复制失败（浏览器没给剪贴板权限），手动全选复制', 'bad'); });
}

/* ── 追加一条记忆 ── */
async function addMemo() {
  const key = CUR || keyOrSay();
  if (!key) { say('mmMsg', '先在上面读一个档案（或填 agent_key）', 'bad'); return; }
  const text = $('mmText').value.trim();
  if (!text) { say('mmMsg', '记忆内容不能为空 —— 后端也会拒（EMPTY）', 'bad'); return; }
  const tags = $('mmTags').value.split(/[,，]/).map(function (s) { return s.trim(); }).filter(Boolean);
  const source = $('mmSource').value.trim() || 'human';
  const r = await api('POST', '/api/mind/' + encodeURIComponent(key) + '/memories',
                      { text: text, tags: tags, source: source });
  if (!r.ok) {
    $('mmOut').innerHTML = errHtml(r.data, r.status);
    say('mmMsg', '没写进去 · ' + ((r.data && r.data.code) || ('HTTP ' + r.status)), 'bad');
    return;
  }
  const d = r.data;
  $('mmOut').innerHTML = banner('ok', '✓ 已追加 <code>' + esc(d.file || '') + '</code>' +
    '<br><span class="msg">' + esc(d.note || '记忆只增不改') + '</span>');
  $('mmText').value = '';
  say('mmMsg', '已追加 · ' + (d.file || ''), 'ok');
  await loadMind();               // 计数与最近列表会跟着变
}

/* ── 时钟 + 启动 ── */
(function clock() {
  const el = $('mhClock');
  if (!el) return;
  (function tick() {
    el.textContent = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    setTimeout(tick, 1000);
  })();
})();

(function boot() {
  if (_KEY0) $('mKey').value = _KEY0;
})();
