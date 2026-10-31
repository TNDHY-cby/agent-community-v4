/* ============================================================
   chrome.js —— 全站骨架层运行时（2026-10-29）
   只做三件事，不碰任何业务逻辑、不发请求：
     1. 实测 fixed 顶栏高度 → 写回 --tb，并给 body 加正确的占位类
     2. 自动给当前页导航打 aria-current="page"
     3. 驱动 [data-chrome-clock] 实时时钟
   依赖：chrome.css。各页在 </body> 前引入。
   ============================================================ */
(function () {
  'use strict';

  var root = document.documentElement;

  /* ---------- 1. 顶栏高度补偿 ---------- */
  /* 为什么需要检测而不是统一 padding：
     各页两种布局并存 ——
       a) 普通文档流（内容可长可短）→ body padding-top
       b) 固定视口高（内部滚动，如监控室 overflow:hidden）→ padding 会把内容顶出视口
     判据：内容是否已经溢出视口。溢出的按普通流处理，不溢出的按固定高处理
     （短内容页用固定高也不会被裁，因为本来就不满一屏）。 */
  function applyOffset() {
    var bar = document.querySelector('.topbar.chrome-fixed');
    if (!bar) return;   // 流内顶栏（chrome-top 但无 chrome-fixed）不占位补偿

    var h = Math.round(bar.getBoundingClientRect().height);
    if (h > 0) root.style.setProperty('--tb', h + 'px');

    // 量之前先摘掉旧类，避免上一轮补偿影响判断
    document.body.classList.remove('chrome-offset', 'chrome-fh');
    var overflowed = document.documentElement.scrollHeight > window.innerHeight + 2;
    document.body.classList.add(overflowed ? 'chrome-offset' : 'chrome-fh');

    // 徽记高度随字体加载可能变，重测一次
    requestAnimationFrame(function () {
      var h2 = Math.round(bar.getBoundingClientRect().height);
      if (h2 > 0 && h2 !== h) root.style.setProperty('--tb', h2 + 'px');
    });
  }

  /* ---------- 2. 当前页导航 ---------- */
  /* 用于给导航项加红边（红只标「你在哪」，不标「可点」） */
  function markCurrent() {
    var path = location.pathname.replace(/\/+$/, '') || '/';
    var links = document.querySelectorAll('.topbar.chrome-top .live-links a[href]');
    Array.prototype.forEach.call(links, function (a) {
      var href;
      try { href = new URL(a.getAttribute('href'), location.href).pathname.replace(/\/+$/, '') || '/'; }
      catch (e) { return; }
      // 根路径 "/" 对应 index，避免任何页都命中
      var hit = (href === path) || (path === '/' && /^\/(index\.html)?$/.test(href));
      if (hit) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
  }

  /* ---------- 3. 时钟 ---------- */
  function pad(n) { return n < 10 ? '0' + n : '' + n; }
  function tick() {
    var els = document.querySelectorAll('[data-chrome-clock]');
    if (!els.length) return;
    var d = new Date();
    var t = pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
    Array.prototype.forEach.call(els, function (el) { el.textContent = t; });
  }

  function init() {
    markCurrent();
    applyOffset();
    tick();
    setInterval(tick, 1000);
    /* 全局面板交互层：常态隐框，hover 边缘泛光 + 阴影突起（chrome.css 3b 段）。
       只加类，不改任何元素样式，删掉这一行即全站回退到各页原生面板。 */
    document.body.classList.add('chrome-soft');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  var rt;
  window.addEventListener('resize', function () {
    clearTimeout(rt);
    rt = setTimeout(applyOffset, 120);
  });
  // 字体晚到会把顶栏撑高几像素，加载完再校一次
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(applyOffset);
  }
})();
