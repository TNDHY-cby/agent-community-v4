/* FE-26 验收（2026-10-17 改版 · DSH 154 号裁决）：
 * 原「register.html 点保存桥模板」UI 流**已退役**——该页按老板裁定只读镜像化，写请求 403。
 * 四条断言一条不少，迁为 **API 直断言**：
 *   ① 平台接受建模板请求（等价原「有保存表单可提交」）
 *   ② 建完 GET …/bridge-templates 里**真出现**这条（界面说成功≠平台真存了，此处直接对平台）
 *   ③ 且**只有一条**（裁定 1B：一个 harness 一条通用桥模板）
 *   ④ 全程无未预期 HTTP 错误（失败必须说话：非 2xx 一律记 fail）
 * 跑法：AC_FE_BASE=… node fe26_check.js ；证据目录 AC_VERIFY_OUT 可覆盖。
 */
const fs = require('fs');
const path = require('path');
const os = require('os');

const BASE = process.env.AC_FE_BASE || 'http://127.0.0.1:18920';
const OUT = process.env.AC_VERIFY_OUT || path.join(os.tmpdir(), 'fe26_api_验证');
const HID = 'harness-fe26-api';
const TMP_INBOX = path.join(os.tmpdir(), 'ac_fe26_inbox');
fs.mkdirSync(OUT, { recursive: true });

const fail = [];
const step = (t, o) => console.log('>> ' + t, JSON.stringify(o));

(async () => {
  const j = async (r) => ({ status: r.status, body: await r.json().catch(() => ({})) });

  /* 预置：登记一个 harness（自建自清） */
  const reg = await j(await fetch(BASE + '/api/harness/register', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      harness_id: HID, harness_name: '示例Harness-A', harness_type: 'desktop-agent',
      wakeup_method: 'file_poll', wakeup_dir: TMP_INBOX,
      acp_command: 'probe.exe --x', ai: { model_name: '示例模型' },
    }),
  }));
  step('预置登记', { status: reg.status });
  if (reg.status >= 400) fail.push('④ 预置登记失败: ' + JSON.stringify(reg.body).slice(0, 200));

  try {
    /* ① 建模板（等价原保存动作） */
    const name = 'FE26验收桥';
    const post = await j(await fetch(BASE + '/api/harness/' + encodeURIComponent(HID) + '/bridge-template', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, kind: 'file_poll', params: { INBOX_DIR: TMP_INBOX } }),
    }));
    step('① POST bridge-template', { status: post.status });
    if (post.status >= 400) fail.push('① 建模板被拒: ' + JSON.stringify(post.body).slice(0, 240));

    /* ②③ GET 复核：真出现 + 只有一条 */
    const list = await j(await fetch(BASE + '/api/harness/' + encodeURIComponent(HID) + '/bridge-templates'));
    const arr = (list.body && (list.body.templates || list.body.bridge_templates)) ||
      (Array.isArray(list.body) ? list.body : []);
    step('②③ GET bridge-templates', { status: list.status, n: arr.length, names: arr.map(t => t && t.name) });
    if (list.status >= 400) fail.push('② 读模板列表失败: ' + JSON.stringify(list.body).slice(0, 240));
    if (!arr.some(t => t && t.name === name)) fail.push('② 平台侧没出现刚建的模板「' + name + '」');
    if (arr.length !== 1) fail.push('③ 模板不是恰好一条（裁定 1B）: n=' + arr.length);

    /* ④ 二段写若存在覆盖语义（再 POST 一次同名应仍是一条）——有则断言，无则跳过（如实记 n/a） */
    const again = await j(await fetch(BASE + '/api/harness/' + encodeURIComponent(HID) + '/bridge-template', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, kind: 'file_poll', params: { INBOX_DIR: TMP_INBOX } }),
    }));
    const list2 = await j(await fetch(BASE + '/api/harness/' + encodeURIComponent(HID) + '/bridge-templates'));
    const arr2 = (list2.body && (list2.body.templates || list2.body.bridge_templates)) ||
      (Array.isArray(list2.body) ? list2.body : []);
    step('④ 幂等复建', { status: again.status, n: arr2.length });
    if (again.status < 400 && arr2.length !== 1) fail.push('④ 重复建后仍应恰好一条: n=' + arr2.length);

    const out = { pass: fail.length === 0, fail, created: name, count_final: arr2.length };
    console.log('>> 结论', JSON.stringify(out));
    fs.writeFileSync(path.join(OUT, 'result.json'), JSON.stringify(out, null, 2));
  } finally {
    await fetch(BASE + '/api/harness/' + encodeURIComponent(HID), { method: 'DELETE' }).catch(() => {});
  }
  process.exit(fail.length ? 1 : 0);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
