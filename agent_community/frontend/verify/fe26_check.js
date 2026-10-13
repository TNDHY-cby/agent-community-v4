/* FE-26 验收：桥型选型面板**真的把选择保存成通用桥模板**（V41 裁定 1B · P2）
 *
 * ⚠️ 为什么编号是 FE-26 而不是 FE-25：我最初把它写成 `fe25_check.js`，
 *    而 **示例Harness-F 的 FE-25（看板/隔间汇总）同时用了同一个文件名**，两边撞号、
 *    提交时**互相覆盖**（实测：他的 9dbca11 盖掉了我的脚本）。
 *    ⇒ 我改号到 FE-26。教训已记入《前端工作手册》：**验收脚本编号要先在通道确认**。
 *
 * 为什么这条必须有：此前「🧭 桥型选型」面板只**展示**"该填什么" + 配叫醒命令，
 * **没有任何提交** ⇒ 用户选完型、读完字段，平台并不知道 ⇒ 工作间里架不起桥
 * （成员卡会显示"桥模板：还没有"）。裁定 1B 后"每条 harness 一条通用桥模板"是架桥前提，
 * 这一步不落地，整条接入链在最后一米断掉。
 *
 * 断言：① 面板有保存表单（选 harness + 名字 + 按钮 + 该型 user 字段输入框）
 *       ② 点保存后**平台侧真的出现这条模板**（GET …/bridge-templates 复核——界面说成功≠平台真存了）
 *       ③ 且**只有一条**（裁定 1B）
 *       ④ Console 零红错
 */
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright-core');

const BASE = process.env.AC_FE_BASE || 'http://127.0.0.1:18920';
const OUT = process.env.AC_VERIFY_OUT || path.join(__dirname, 'FE26_验证');
const HID = 'harness-fe26-probe';
fs.mkdirSync(OUT, { recursive: true });

const fail = [];
const step = (t, o) => console.log('>> ' + t, JSON.stringify(o));

(async () => {
  const j = (r) => r.json();
  await fetch(BASE + '/api/harness/register', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      harness_id: HID, harness_name: '示例Harness-A', harness_type: 'desktop-agent',
      wakeup_method: 'file_poll', wakeup_dir: 'D:\\Temp\\ac_fe26_inbox',
      acp_command: 'probe.exe --x', ai: { model_name: '示例模型' },
    }),
  }).then(j);

  const browser = await chromium.launch({ channel: 'msedge' });
  const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
  const errs = [];
  page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text().slice(0, 160)); });
  page.on('pageerror', (e) => errs.push('pageerror: ' + String(e).slice(0, 160)));

  try {
    await page.goto(BASE + '/register.html', { waitUntil: 'domcontentloaded', timeout: 25000 });
    await page.waitForTimeout(2200);

    await page.evaluate(() => bkSelect('file_poll'));
    await page.waitForTimeout(1500);
    const form = await page.evaluate(() => ({
      hasHid: !!document.getElementById('bkTplHid'),
      hasName: !!document.getElementById('bkTplName'),
      hasBtn: !!document.getElementById('bkTplBtn'),
      tplFields: Array.from(document.querySelectorAll('#bkSpec [data-tplf]')).map((i) => i.getAttribute('data-tplf')),
    }));
    step('① 保存表单', form);
    if (!form.hasHid || !form.hasName || !form.hasBtn) fail.push('① 面板缺保存表单（选 harness/名字/按钮）');
    if (!form.tplFields.length) fail.push('① 面板没有该型的 user 字段输入框（"该填什么"没变成可填的空）');

    const NAME = 'FE26验收桥';
    await page.selectOption('#bkTplHid', HID);
    await page.fill('#bkTplName', NAME);
    await page.evaluate(() => {
      const i = document.querySelector('#bkSpec [data-tplf="INBOX_DIR"]');
      if (i) i.value = 'D:\\Temp\\ac_fe26_inbox';
    });
    await page.click('#bkTplBtn');
    await page.waitForTimeout(2500);
    const panel = await page.evaluate(() => (document.getElementById('bkTplRes') || {}).innerText || '');
    step('② 面板反馈', { head: panel.replace(/\s+/g, ' ').slice(0, 140) });
    if (!/已保存/.test(panel)) fail.push('② 面板未显示保存成功：' + panel.slice(0, 80));

    const stored = await fetch(`${BASE}/api/harness/${HID}/bridge-templates`).then(j);
    const names = (stored.templates || []).map((t) => t.name);
    step('③ 平台侧复核', { count: stored.count, names });
    if (!names.includes(NAME)) fail.push('③ 平台里没出现刚保存的模板：' + JSON.stringify(names));
    if (stored.count !== 1) fail.push('③ 裁定 1B 要求只留一条，实得 ' + stored.count + ' 条');

    step('④ Console', { errors: errs.length });
    if (errs.length) fail.push('④ Console 红错 ' + errs.length + '：' + errs.slice(0, 2).join(' | '));

    await page.screenshot({ path: path.join(OUT, 'saved.png') });
  } catch (e) {
    fail.push('FATAL: ' + String(e && e.message || e));
  } finally {
    await fetch(`${BASE}/api/harness/${HID}`, { method: 'DELETE' }).catch(() => {});
    await browser.close();
  }

  const result = { pass: fail.length === 0, fail };
  fs.writeFileSync(path.join(OUT, 'result.json'), JSON.stringify(result, null, 2));
  console.log('>> 结论 ' + JSON.stringify(result));
  process.exit(result.pass ? 0 : 1);
})();
