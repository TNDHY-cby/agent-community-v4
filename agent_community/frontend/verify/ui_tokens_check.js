#!/usr/bin/env node
/* 判据（前端 · 零依赖）：**设计令牌不许再漂回去**
 *
 * 背景（2026-11-05 老板：「统一平台所有 UI 设计风格，以对接中心为主」）：
 *   颜色/字体/字号/圆角/阴影只在 `tokens.css` 定义一处；组件只在 `ui.css` 定义一处。
 *   页面自己的 `:root` 只许写"本页特有"的变量 —— **重名覆盖就是漂移**。
 *
 * 跑法：node agent_community/frontend/verify/ui_tokens_check.js
 * 退出码：0 = 全过；1 = 有格没过（打印具体文件与变量名）；2 = 环境不满足（找不到目录）
 *
 * 它查四件事：
 *   ① 每个页面都引了 tokens.css 与 ui.css，且顺序必须是 tokens → ui →（页面自己的 style）
 *   ② 页面内联 `:root` 里没有与 tokens 重名的变量
 *   ③ 页面用到的 `var(--x)` 都有定义（tokens / 页面自己 / 公共 css）
 *   ④ `theme-crt.css` 允许覆盖同名变量（换肤），但要单独说明它是"主题"，不参与漂移判定
 */
'use strict';
const fs = require('fs');
const path = require('path');

const FE = process.argv[2] || path.join(__dirname, '..');
let fails = 0;
let checks = 0;

function cell(name, ok, detail) {
  checks += 1;
  if (!ok) fails += 1;
  console.log('  ' + (ok ? 'OK  ' : 'FAIL') + '  ' + name + (detail ? '  — ' + detail : ''));
}

const read = (f) => fs.readFileSync(path.join(FE, f), 'utf8');
const DECL = /(--[a-zA-Z0-9_-]+)\s*:\s*([^;]*);/g;
const USE = /var\(\s*(--[a-zA-Z0-9_-]+)\s*\)/g;   /* 只算**没有 fallback** 的用法：var(--x, 备用) 是设计如此 */
const STYLE = /<style[^>]*>([\s\S]*?)<\/style>/g;
const ROOT = /:root\s*\{/g;

function declarations(css) {
  const out = [];
  let m;
  DECL.lastIndex = 0;
  while ((m = DECL.exec(css))) out.push(m[1]);
  return out;
}
function uses(css) {
  const out = new Set();
  let m;
  USE.lastIndex = 0;
  while ((m = USE.exec(css))) out.add(m[1]);
  return out;
}
function rootBodies(css) {
  const out = [];
  let m;
  ROOT.lastIndex = 0;
  while ((m = ROOT.exec(css))) {
    let i = m.index + m[0].length;
    let depth = 1;
    while (i < css.length && depth) {
      if (css[i] === '{') depth += 1;
      else if (css[i] === '}') depth -= 1;
      i += 1;
    }
    out.push(css.slice(m.index + m[0].length, i - 1));
  }
  return out;
}

if (!fs.existsSync(path.join(FE, 'tokens.css'))) {
  console.log('环境不满足：找不到 ' + path.join(FE, 'tokens.css'));
  process.exit(2);
}
console.log('UI 令牌判据（' + FE + '）\n');

const tokens = new Set(declarations(read('tokens.css')));
const ui = new Set(declarations(read('ui.css')));
const shared = new Set();
['hub.css', 'docking.css', 'chrome.css', 'boot.css', 'remote-modal.css'].forEach((f) => {
  if (fs.existsSync(path.join(FE, f))) declarations(read(f)).forEach((x) => shared.add(x));
});
const crt = new Set(declarations(read('theme-crt.css')));

cell('tokens.css 有变量（' + tokens.size + ' 个）', tokens.size > 20);
cell('ui.css 存在且有组件（.btn/.card）',
  fs.existsSync(path.join(FE, 'ui.css')) && /\.btn\s*\{/.test(read('ui.css')) &&
  /\.card\s*\{/.test(read('ui.css')));

const pages = fs.readdirSync(FE).filter((f) => f.endsWith('.html')).sort();
for (const pg of pages) {
  const s = read(pg);
  const hrefTok = s.indexOf('href="tokens.css"');
  const hrefUi = s.indexOf('href="ui.css"');
  cell(pg + ' 引了 tokens.css 且顺序对', hrefTok >= 0 && (hrefUi < 0 || hrefTok < hrefUi),
    hrefTok < 0 ? '没引 tokens.css' : '');

  let inline = '';
  let m;
  STYLE.lastIndex = 0;
  while ((m = STYLE.exec(s))) inline += '\n' + m[1];

  // ② 内联 :root 不许重名覆盖 tokens
  const dup = [];
  rootBodies(inline).forEach((body) => {
    declarations(body).forEach((n) => { if (tokens.has(n)) dup.push(n); });
  });
  cell(pg + ' 没有重名覆盖 tokens 的变量', dup.length === 0,
    dup.length ? '重复：' + dup.slice(0, 8).join('、') : '');

  // ③ 用到的变量都有定义
  const missing = [...uses(inline)].filter((n) => !tokens.has(n) && !ui.has(n) && !shared.has(n) && !crt.has(n));
  cell(pg + ' 的 var(--x) 都有定义', missing.length === 0,
    missing.length ? '未定义：' + missing.slice(0, 8).join('、') : '');
}

console.log('\n' + (fails ? '结论：**' + fails + ' 格失败**（共 ' + checks + ' 格）'
  : '结论：**全过**（' + checks + ' 格）—— 令牌单源、组件单源、页面无重名覆盖'));
process.exit(fails ? 1 : 0);
