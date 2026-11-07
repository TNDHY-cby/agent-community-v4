/* ============================================================
   编队词表 team-words.js（2026-11-07 · 老板定词）
   —— 一处真相：界面上「编队」相关的叫法**只在这里改**。

   老板原话（2026-11-07）：
     · 「小队不应该叫分组，叫分队之类的吧，你想想可以叫什么？」
     · 「组长不参加小队，他是领袖」
   ⇒ 定词：
       一个编队单位 = **分队**（对应后端 `squads[]`）
       没进分队的     = **未编队**
       组长在编队语境 = **领袖**，且**领袖不进分队**（他是工作间级的拍板人）

   ⚠️ 这里只改**显示文案**：后端字段仍是 `squads` / `cell_count` / `member_seqs`，
     契约不动（改字段要动后端与判据，不是前端能自己定的）。
   ⚠️ 想换叫法（比如「班组」「支队」）：只改下面 unit / unassigned / leader 三个词，
     v5.html 与 workshop.html 两页同时生效。
   ============================================================ */
window.TEAM_WORDS = {
  unit: '分队',
  unnamed: '未命名分队',
  unassigned: '未编队',
  unassignedHint: '还没编入任何分队',
  leader: '领袖',
  leaderHint: '领袖 · 不进分队 · 拍板 / 验收',
  memberHint: '谁进哪个分队由领袖定',
  unitCount: function (n) { return n + ' 个' + this.unit; },
  emptyUnit: function () { return '这个' + this.unit + '还没有人（' + this.memberHint + '）'; },
  footNote: function () {
    return this.unit + '只是编队：账本仍按任务号逐条、验收逐条，没有整队一键通过。';
  },
  leadMisfiled: function (names) {
    return this.leader + '是领袖、不进' + this.unit + ' —— 但「' + names + '」里配了它；' +
      '平台**不把它算进' + this.unit + '**（请在该' + this.unit + '里把它去掉）。';
  }
};
