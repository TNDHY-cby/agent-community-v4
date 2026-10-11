/* ===== 远程接入弹窗（公司间 · 令牌授权制）=====
   独立于 register.html 内联脚本：注册页「远程接入」按钮 openRemoteModal() 打开。
   后端 API：GET /api/companies、POST /api/companies、POST /api/company/token/issue、
   POST /api/company/token/revoke、POST /api/company/token/verify（见 routers/company_admin.py）。
*/
(function () {
  var ov = null;
  var tokenCache = {}; // company_id -> 最近签发 token（本弹窗生命周期内）

  function ensure() {
    if (ov) return ov;
    ov = document.createElement('div');
    ov.className = 'rm-overlay';
    ov.innerHTML =
      '<div class="rm-modal">' +
      '<div class="rm-head"><span class="rm-title">🛰 远程接入（公司间 · 令牌授权制）</span><span class="rm-close" title="关闭">✕</span></div>' +
      '<div class="rm-body"></div>' +
      '<div class="rm-foot"><button class="rm-btn" data-close>关闭</button></div>' +
      '</div>';
    ov.querySelector('.rm-close').onclick = hide;
    ov.querySelector('[data-close]').onclick = hide;
    ov.addEventListener('mousedown', function (e) { if (e.target === ov) hide(); });
    document.body.appendChild(ov);
    return ov;
  }

  function hide() {
    if (!ov) return;
    ov.classList.remove('show');
    setTimeout(function () { ov.style.display = 'none'; }, 180);
  }

  function show() {
    var m = ensure();
    ov.style.display = 'flex';
    requestAnimationFrame(function () { ov.classList.add('show'); });
    renderBody();
    refreshCompanies();
  }

  window.openRemoteModal = show;
  window.closeRemoteModal = hide;

  function renderBody() {
    var b = ensure().querySelector('.rm-body');
    b.innerHTML =
      '<div class="rm-intro"><b>什么是远程接入？</b>这是给<b>另一家公司 / 另一台机器</b>上的 Agent 开放本平台能力（9104 端口）的通道。' +
      '它与下方本地 Harness 注册是<b>两条独立通道</b>：本地 harness 走「两阶段注册」进注册列表；' +
      '<b>远程公司不在注册页登记</b>，只凭你签发的<b>公司令牌</b>直连 9104，远程身份由令牌承载。</div>' +

      '<div class="rm-step"><h3><span class="rm-no">1</span>登记合作公司</h3>' +
      '<div class="rm-grid">' +
      '<div class="rm-field"><label>公司名称 *</label><input id="rmName" placeholder="如：合作社 B（远程端）"></div>' +
      '<div class="rm-field"><label>公司标识（可选，留空自动生成）</label><input id="rmId" placeholder="如：co-remote-b"></div>' +
      '<div class="rm-field" style="grid-column:1/-1"><label>备注（可选）</label><input id="rmNote" placeholder="对方是谁、主要用来干什么"></div>' +
      '</div>' +
      '<button class="rm-btn primary" onclick="rmRegisterCompany()">登记并签发令牌</button>' +
      '<div class="rm-hint" id="rmRegHint">登记后自动生成第一张令牌，令牌会出现在第 2 步。</div>' +
      '</div>' +

      '<div class="rm-step"><h3><span class="rm-no">2</span>签发 / 重签令牌</h3>' +
      '<div class="rm-grid">' +
      '<div class="rm-field"><label>选择公司</label><select id="rmCompanySel"></select></div>' +
      '<div class="rm-field"><label>有效期（天，0 = 永不过期）</label><input id="rmDays" type="number" min="0" value="0" placeholder="0"></div>' +
      '<div class="rm-field"><label>&nbsp;</label><button class="rm-btn primary" onclick="rmIssueToken()">签发令牌</button></div>' +
      '</div>' +
      '<div id="rmTokenArea"></div>' +
      '</div>' +

      '<div class="rm-step"><h3><span class="rm-no">3</span>把接入包交给对方</h3>' +
      '<div class="rm-hint">把下面这段「接入指引」发给对方公司的负责人，连同 <b>a2a_client.py</b>（零依赖单文件，项目 <code>agent_community/a2a_client.py</code>）与第 2 步签发的令牌。<b>令牌等同通行证</b>，只能发给信任的公司；吊销后即刻失效。</div>' +
      '<pre class="rm-pre" id="rmHandoff">[远程接入指引（发给公司 B）]\n1. 装好 Tailscale，双方同一网络，拿到本平台的 9104 地址（形如 http://100.x.x.x:9104）。\n2. 保存 a2a_client.py 到本机（零依赖，Python 3.8+ 即可）。\n3. 用平台签发给你的公司令牌调用（把 TOKEN 与 你的公司ID 换成实际值）：\n   python -m agent_community.a2a_client --company 你的公司ID --token TOKEN --url 平台9104地址 card\n   python -m agent_community.a2a_client --company 你的公司ID --token TOKEN --url 平台9104地址 send "任务书标题"\n   python -m agent_community.a2a_client --company 你的公司ID --token TOKEN --url 平台9104地址 get 任务ID\n   python -m agent_community.a2a_client --company 你的公司ID --token TOKEN --url 平台9104地址 msg 任务ID "回执：已收到"\n4. 验证：card 能读到 Agent Card = 网络通；send 成功 = 令牌有效。</pre>' +
      '<button class="rm-btn" onclick="rmCopyHandoff()">📋 一键复制接入指引</button>' +
      '</div>' +

      '<div class="rm-step"><h3><span class="rm-no">★</span>已授权公司</h3><div id="rmCompanies"></div></div>';
  }

  function toast(msg, err) {
    var t = document.querySelector('.rm-toast');
    if (!t) { t = document.createElement('div'); t.className = 'acm-toast'; document.body.appendChild(t); }
    t.textContent = msg;
    t.classList.toggle('err', !!err);
    t.classList.add('show');
    setTimeout(function () { t.classList.remove('show'); }, 2600);
  }

  function copyText(text, okMsg) {
    function done() { toast(okMsg || '已复制'); }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, function () { fallback(); });
    } else { fallback(); }
    function fallback() {
      var ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); done(); } catch (e) { toast('复制失败，请手动复制', true); }
      document.body.removeChild(ta);
    }
  }

  // ── 公司列表 / 下拉 ──
  function refreshCompanies() {
    fetch('/api/companies').then(function (r) { return r.json(); }).then(function (d) {
      if (!d || !d.ok) { toast((d && d.error) || '加载公司列表失败', true); return; }
      var list = d.companies || [];
      // 下拉
      var sel = ensure().querySelector('#rmCompanySel');
      if (sel) {
        sel.innerHTML = '';
        list.forEach(function (c) {
          var o = document.createElement('option');
          o.value = c.company_id;
          o.textContent = c.company_name + '（' + c.company_id + '）' + (c.expired ? ' [已过期]' : '');
          sel.appendChild(o);
        });
        if (!list.length) {
          var oo = document.createElement('option');
          oo.value = '';
          oo.textContent = '（暂无公司，请先完成第 1 步）';
          sel.appendChild(oo);
        }
      }
      // 表格
      var box = ensure().querySelector('#rmCompanies');
      if (!list.length) {
        box.innerHTML = '<div class="rm-empty">还没有授权公司。完成第 1 步后，这里会列出已登记的合作公司。</div>';
        return;
      }
      var html = '<table class="rm-table"><thead><tr><th>公司</th><th>状态</th><th>签发 / 过期</th><th>操作</th></tr></thead><tbody>';
      list.forEach(function (c) {
        var tag = c.expired ? '<span class="rm-tag expired">已过期</span>'
          : '<span class="rm-tag active">有效</span>';
        html += '<tr><td><b>' + esc(c.company_name) + '</b><br><code style="font-size:10.5px;color:var(--ink-soft)">' + esc(c.company_id) + '</code></td>' +
          '<td>' + tag + '</td>' +
          '<td style="font-size:11px;color:var(--ink-soft)">' + (c.issued_at ? esc(c.issued_at) : '-') + '<br>' + (c.expires_at ? esc(c.expires_at) : '永不过期') + '</td>' +
          '<td><div class="rm-actions">' +
          '<button class="rm-btn" onclick="rmCopyToken(\'' + c.company_id + '\')">复制令牌</button>' +
          '<button class="rm-btn" onclick="rmVerifyToken(\'' + c.company_id + '\')">校验</button>' +
          '<button class="rm-btn danger" onclick="rmRevokeCompany(\'' + c.company_id + '\')">吊销</button>' +
          '</div></td></tr>';
      });
      html += '</tbody></table>';
      box.innerHTML = html;
    }).catch(function () { toast('网络错误：加载公司列表失败', true); });
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (m) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m];
    });
  }

  window.rmRegisterCompany = function () {
    var name = document.getElementById('rmName');
    var id = document.getElementById('rmId');
    var note = document.getElementById('rmNote');
    var hint = document.getElementById('rmRegHint');
    if (!name || !name.value.trim()) { toast('请填写公司名称', true); name && name.focus(); return; }
    var btn = event && event.target;
    if (btn) { btn.disabled = true; btn.textContent = '登记中…'; }
    fetch('/api/companies', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ company_name: name.value.trim(), company_id: id.value.trim(), note: note.value.trim() })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (btn) { btn.disabled = false; btn.textContent = '登记并签发令牌'; }
      if (!d || !d.ok) { toast((d && d.error) || '登记失败', true); return; }
      var cid = d.company.company_id;
      toast(d.created ? '已登记并签发令牌' : '公司已存在，信息已更新');
      if (hint) hint.textContent = '✅ 已登记 ' + d.company.company_name + '（' + cid + '）。第 2 步可重新签发令牌。';
      // 自动签发第一张令牌（新登记时）
      if (d.created) { issueTokenFor(cid, 0, true); }
      refreshCompanies();
    }).catch(function () {
      if (btn) { btn.disabled = false; btn.textContent = '登记并签发令牌'; }
      toast('网络错误：登记失败', true);
    });
  };

  window.rmIssueToken = function () {
    var sel = document.getElementById('rmCompanySel');
    var days = document.getElementById('rmDays');
    var cid = sel ? sel.value : '';
    if (!cid) { toast('请先登记公司（第 1 步）', true); return; }
    var d = parseInt(days && days.value, 10);
    issueTokenFor(cid, isNaN(d) ? 0 : d, false);
  };

  function issueTokenFor(cid, days, silent) {
    var area = document.getElementById('rmTokenArea');
    if (area) area.innerHTML = '<div class="rm-hint">签发中…</div>';
    fetch('/api/company/token/issue', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ company_id: cid, days: days })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (!d || !d.ok) { if (area) area.innerHTML = ''; toast((d && d.error) || '签发失败', true); return; }
      tokenCache[cid] = d.token;
      if (area) {
        area.innerHTML =
          '<div class="rm-token-box">' +
          '<textarea readonly id="rmTokenValue" placeholder="令牌将显示在这里">' + esc(d.token) + '</textarea>' +
          '<div class="rm-token-side">' +
          '<button class="rm-btn primary" onclick="rmCopyTokenValue()">复制令牌</button>' +
          '<button class="rm-btn" onclick="rmVerifyToken(\'' + cid + '\')">校验</button>' +
          '</div></div>' +
          '<div class="rm-hint">有效期：' + (d.expires_at ? esc(d.expires_at) : '永不过期') + '。' + esc(d.note || '') + '</div>';
      }
      if (!silent) toast('令牌已签发');
      refreshCompanies();
    }).catch(function () { if (area) area.innerHTML = ''; toast('网络错误：签发失败', true); });
  }

  window.rmCopyTokenValue = function () {
    var ta = document.getElementById('rmTokenValue');
    if (ta && ta.value) copyText(ta.value, '令牌已复制');
    else toast('暂无可复制的令牌', true);
  };

  window.rmCopyToken = function (cid) {
    if (tokenCache[cid]) { copyText(tokenCache[cid], '令牌已复制'); return; }
    // 未签发过则先签发 0 天（永不过期）再复制
    issueTokenFor(cid, 0, true);
    setTimeout(function () {
      if (tokenCache[cid]) copyText(tokenCache[cid], '令牌已复制');
      else toast('签发失败，请重试', true);
    }, 900);
  };

  window.rmVerifyToken = function (cid) {
    var tk = tokenCache[cid];
    if (!tk) { toast('本弹窗未签发过该公司的令牌，请先签发', true); return; }
    fetch('/api/company/token/verify', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ company_id: cid, token: tk })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.valid) toast('✓ 令牌有效');
      else toast((d && d.reason) || '校验失败', true);
    }).catch(function () { toast('网络错误：校验失败', true); });
  };

  window.rmRevokeCompany = function (cid) {
    if (!window.confirm('确定吊销公司 ' + cid + ' 的令牌？吊销后该公司即刻无法再访问 9104，且不可恢复。')) return;
    fetch('/api/company/token/revoke', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ company_id: cid })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (!d || !d.ok) { toast((d && d.error) || '吊销失败', true); return; }
      delete tokenCache[cid];
      toast('已吊销 ' + cid);
      refreshCompanies();
    }).catch(function () { toast('网络错误：吊销失败', true); });
  };

  window.rmCopyHandoff = function () {
    var pre = document.getElementById('rmHandoff');
    if (pre) copyText(pre.textContent, '接入指引已复制');
    else toast('暂无可复制内容', true);
  };
})();
