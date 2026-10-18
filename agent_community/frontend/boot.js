/* boot.js —— 启动过场 v3（生产合作社 · 工业HUD）
 * 只在启动器导航（?boot=1）时播一次；普通访问大厅/对接中心不播。
 * 手动重播：?replay=1 ｜ 强制不播：?noboot ｜ 自动化(webdriver)不播。 */
(function () {
  try {
    var q = location.search || '';
    if (/[?&]noboot/.test(q)) return;
    if (!/[?&](boot|replay)=1/.test(q)) return;   /* 没有启动器参数就不播 */
    if (navigator.webdriver) return;

    var wrap = document.createElement('div');
    wrap.id = 'boot';
    wrap.setAttribute('aria-hidden', 'true');
    wrap.innerHTML =
      '<div class="boot-scan"></div>' +
      '<div class="brand-plate">' +
        '<h1>生产合作社</h1>' +
        '<div class="en">PRODUCTION COOPERATIVE</div>' +
        '<div class="sys">SYSTEM: INTEGRATING HETEROGENEOUS AGENTS…</div>' +
        '<div class="rev-badge">WEB PROFILE' +
          '<div class="ticks"><i class="on"></i><i class="on"></i><i class="on"></i><i class="on"></i><i></i><i></i><i></i><i></i></div>' +
        '</div>' +
      '</div>' +
      '<div class="boot-hud tr" data-boot-skip>[ 跳过 SKIP ]</div>' +
      '<div class="boot-core">' +
        '<div class="hs-wrap"><img src="/boot_emblem.png" alt="镰刀锤子"></div>' +
        '<div class="boot-brand">生 产 合 作 社</div>' +
        '<div class="boot-brand-en">PRODUCTION COOPERATIVE</div>' +
        '<div class="radar">' +
          '<svg class="rings" viewBox="0 0 280 280">' +
            '<circle class="dial-outer" cx="140" cy="140" r="126"/>' +
            '<g class="dial-ticks">' +
              '<circle cx="140" cy="140" r="118" fill="none" stroke="#6A655D" stroke-width="2" stroke-dasharray="2 10"/>' +
              '<circle class="boot-dot g" cx="140" cy="22" r="3"/>' +
              '<circle class="boot-dot g" cx="38" cy="199" r="3"/>' +
              '<circle class="boot-dot g" cx="242" cy="199" r="3"/>' +
            '</g>' +
          '</svg>' +
          '<div class="gear-g"><img class="gear-img" src="/boot_gear.png" alt=""></div>' +
        '</div>' +
        '<div class="boot-stage" data-boot-stage>' +
          '<div class="boot-line" data-boot-line>读取显示核心</div>' +
          '<div class="boot-sub" data-boot-en>LOADING DISPLAY CORE</div>' +
          '<div class="boot-tag" data-boot-tag></div>' +
        '</div>' +
      '</div>' +
      '<div class="boot-foot">' +
        '<div><span class="code" data-boot-code>[00/04]</span> BOOT</div>' +
        '<div>' +
          '<div class="pct-row"><span data-boot-pctl>[ INIT ]</span><span data-boot-pct>0%</span></div>' +
          '<div class="track"><div class="fill" data-boot-fill></div></div>' +
        '</div>' +
        '<div class="br">Powered By 生产合作社 <span class="rd">▌</span></div>' +
      '</div>' +
      '<div class="cr-panel" data-boot-credits aria-hidden="true"></div>';
    document.body.appendChild(wrap);

    var line = wrap.querySelector('[data-boot-line]');
    var en = wrap.querySelector('[data-boot-en]');
    var tag = wrap.querySelector('[data-boot-tag]');
    var code = wrap.querySelector('[data-boot-code]');
    var pct = wrap.querySelector('[data-boot-pct]');
    var pctL = wrap.querySelector('[data-boot-pctl]');
    var fill = wrap.querySelector('[data-boot-fill]');
    var stage = wrap.querySelector('[data-boot-stage]');
    var steps = [
      { c: '[01/04]', l: '读取显示核心', e: 'LOADING DISPLAY CORE', t: 'DISPLAY / OK', p: 25 },
      { c: '[02/04]', l: '探测平台端点', e: 'PROBING PLATFORM ENDPOINT', t: 'ENDPOINT / OK', p: 50 },
      { c: '[03/04]', l: '核验访问令牌', e: 'VERIFYING ACCESS TOKEN', t: 'TOKEN / VERIFIED', p: 75 },
      { c: '[04/04]', l: '对接完成', e: 'DOCKING COMPLETE', t: 'PERMISSION AUTHORIZED', p: 100 }
    ];
    var i = 0, done = false;
    function markSeen() { /* v3：播放由 ?boot=1 门控，无需会话标记 */ }
    /* 片尾式滚动：把启动过程的文本按行滑入右侧（与进度同步；真实日志仍落盘） */
    var creditLines = [
      '[launcher] 启动合作社 · 便携启动',
      '[launcher] 工作目录 → 仓库根',
      '[1/5] 选择解释器 … OK',
      '[2/5] 拉起平台服务（后台托管）…',
      '[static] 前端已挂载',
      '[env] 访问模式 → 本机',
      '[ai] Provider … ready',
      '[listen] 绑定端口 …',
      '[persist] 恢复工作间 / 成员 …',
      '[bridge] 桥接通道检查 …',
      '[health] GET /api/status → running',
      '[browser] 打开大厅 → /',
      '[ok] 启动完成 · 欢迎进入生产合作社'
    ];
    var crBox = wrap.querySelector('[data-boot-credits]');
    var crIdx = 0;
    function crTick() {
      if (done || crIdx >= creditLines.length) return;
      var el = document.createElement('div');
      el.className = 'cr-line';
      el.innerHTML = creditLines[crIdx++].replace(/\[(ok|health)\]/g, '<span class="rd">[$1]</span>');
      crBox.appendChild(el);
      setTimeout(crTick, 300);
    }
    setTimeout(crTick, 350);
    function finish() {
      if (done) return; done = true; markSeen();
      code.textContent = '[04/04]';
      pctL.textContent = '[ DOCK ]';
      stage.innerHTML =
        '<div class="boot-final">' +
          '<div class="boot-line">对 接 完 成</div>' +
          '<div class="boot-sub" style="color:#E5332C">DOCKING COMPLETE</div>' +
        '</div>';
      /* 短暂停留后自动接入当前页（启动器打开的是大厅 /） */
      setTimeout(function () { wrap.classList.add('gone'); }, 1000);
    }
    function tick() {
      if (done) return;
      if (i >= steps.length) { finish(); return; }
      var s = steps[i++];
      code.textContent = s.c; line.textContent = s.l; en.textContent = s.e;
      tag.textContent = s.t; pct.textContent = s.p + '%'; fill.style.width = s.p + '%';
      setTimeout(tick, 850);
    }
    function skip() { done = true; markSeen(); wrap.classList.add('gone'); }
    wrap.querySelector('[data-boot-skip]').addEventListener('click', skip);
    wrap.addEventListener('click', skip);
    window.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' || e.key === 'Enter') skip();
    });
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      finish(); return;
    }
    setTimeout(tick, 400);
  } catch (e) { /* 过场失败绝不挡住主界面 */ }
})();
