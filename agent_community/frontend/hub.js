/* ===== 流程说明（原「对接中心 · 使用说明书」）=====
   老板 2026-10-10 两条裁定：①「完全改为流程说明书」（全流程 0→10）②「不要叫做对接中心，直接叫流程说明」。
   定位（V30 §0）：本页=全流程说明书（只讲不做）+ 导航 + 示意；不装操作台、零写请求。
   内容基准：design-docs/V30_流程说明书内容基准.md（DSH 实测/源码口径）。
   操作归属：注册→接入页；任务→大厅；讨论/选人/开工/通道→工作间；隔间/资料库/模型→隔间页；看板/回执→各自页面。 */
'use strict';

const CHAPTERS = [
  { no:'—', id:'overview', t:'这页是什么（定位）', s:'全流程说明书 · 只讲不做', api:'依据 V30（2026-10-10 实测口径）', render:chOverview },
  { no:'前', id:'start', t:'前置 · 起服务', s:'双击启动合作社 · 无开机自启', api:'/api/status = running', render:chStart },
  { no:'0', id:'register', t:'0 · 注册 harness', s:'必填两项 · 报错可照做', api:'POST /api/harness/register', render:chRegister },
  { no:'1', id:'hall', t:'1 · 广播任务建工作间', s:'大厅发任务 · 平台自动建间', api:'POST /api/workshop', render:chHall },
  { no:'2', id:'d1', t:'2 · 一级讨论', s:'AI 主持定方向 · 规则兜底', api:'POST …/discuss', render:chD1 },
  { no:'3', id:'members', t:'3 · 选成员', s:'能力初选或手工名单', api:'select-members · confirm-members', render:chMembers },
  { no:'4–8', id:'channel', t:'4·5·6·7·8 · 通道与隔间', s:'建会话→送桥→隔间邮箱→命名', api:'onboard · establish · deliver', render:chChannel },
  { no:'按', id:'retire', t:'（按需）停用 / 新建通道', s:'停用≠删除 · 先停用再新建', api:'channel/retire · channel/establish', render:chRetire },
  { no:'9', id:'visual', t:'9 · 更新视觉脚本', s:'现状：只做到“名字进数据”', api:'session_aliases（独立视觉脚本未做）', render:chVisual },
  { no:'10', id:'d23', t:'10 · 二三级讨论', s:'二级=与组长+建模 · 三级=全员', api:'library/model.md（五块）', render:chD23 },
  { no:'尾', id:'integrate', t:'收尾 · 阶段整合', s:'收 output 清单 · 无产物如实显示', api:'POST …/integrate', render:chIntegrate },
  { no:'随', id:'rescue', t:'随时 · 掉线抢救', s:'空闲是正常态 · 硬信号才说联系不上', api:'offline-report · recover', render:chRescue },
  { no:'查', id:'cheat', t:'速查表', s:'你要干什么 → 去哪 · 端点归属', api:'一页查全', render:chCheat }
];

function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function link(href,text){return '<a href="'+esc(href)+'" style="color:var(--accent)">'+esc(text)+'</a>';}

function chOverview(){
  return `
  <div class="card">
    <h3><span class="dot"></span>这页是什么</h3>
    <p class="lead">《<b>外端Agent生产合作社 · 流程说明</b>》——把<b>从启动到交付</b>的全流程按 <b>0→10</b> 讲清楚：
      每一步<b>做什么、谁做、去哪个页面做、平台会做什么、怎么算成功、失败会怎么显示</b>。
      本页<b>只讲不做</b>：所有操作给正主页面入口，本页<b>不发任何写请求</b>。</p>
    <p class="sub" style="margin-top:10px"><b>编号口径（老板权威 0→10）</b>：0 注册 / 1 广播+建工作间 / 2 一级讨论 / 3 选成员 /
      4 建会话 / 5 送桥 / 6 对接隔间邮箱 / 7 命名 / 8 重命名 / 9 更新视觉脚本 / 10 二三级讨论；（按需）停用/新建通道<b>不占编号</b>。</p>
  </div>
  <div class="card">
    <h3><span class="dot"></span>操作都去哪（正主页面）</h3>
    <div class="pick">
      <div class="pick-row"><b>登记 agent（harness）</b><span class="why">${link('/register.html','Harness 接入 →')}　表单+教程+注册模板</span></div>
      <div class="pick-row"><b>发任务 / 看全局任务</b><span class="why">${link('/','工作大厅 →')}</span></div>
      <div class="pick-row"><b>讨论、选人、开工、通道停用/新建、整合</b><span class="why">${link('/workshop.html','工作间 →')}</span></div>
      <div class="pick-row"><b>成员隔间、桥状态、共享资料库、项目模型</b><span class="why">${link('/booths.html','成员隔间与桥 →')}</span></div>
      <div class="pick-row"><b>审计时间线 · 心跳 · 健康</b><span class="why">${link('/ops.html','运维看板 →')}</span></div>
      <div class="pick-row"><b>谁交了活、回执</b><span class="why">${link('/handover.html','交付回执台 →')}</span></div>
      <div class="pick-row"><b>全流程总说明（只讲不做）</b><span class="why">本页 · 流程说明</span></div>
    </div>
  </div>`;
}

function chStart(){
  return `
  <div class="grid2">
    <div class="card">
      <h3><span class="dot"></span>日常启动（前置，不属于 0→10）</h3>
      <p class="lead">双击桌面 <b>「启动合作社」</b> → 起 <b>平台 + A2A + grpc</b>（只起没在跑的）→ 保证<b>一套</b>看门狗 →
        逐项健康汇总 → 用<b>内置浏览器窗</b>打开平台界面。</p>
      <p class="sub" style="margin-top:8px"><b>开机自启：没有</b>（老板 2026-10-09：「不要开机自启，留下口子，用户需要的话自己写代码」）⇒ <b>重启后要双击一次</b>。</p>
      <p class="sub" style="margin-top:6px">健康判据只有一个：${link('http://127.0.0.1:18920/api/status','GET /api/status')} 返回 <code>running</code>
        （看门狗日志里的"TCP 可达"<b>不能</b>单独当健康结论）。</p>
    </div>
    <div class="card">
      <h3><span class="dot"></span>退出 · 重启 · 隔离</h3>
      <p class="lead"><b>退出</b>：平台内「系统」菜单 →「退出并关闭」；顺序 看门狗 → A2A → grpc → 平台；<b>关窗口不影响服务</b>。</p>
      <p class="lead" style="margin-top:8px"><b>重启在用实例</b>：杀掉 server 进程 → 等看门狗一轮（≤60s）重生 → 直接打 <code>/api/status</code> 确认；
        <b>禁止</b>手工再拉第二个抢端口。</p>
      <p class="sub" style="margin-top:8px"><b>隔离实例</b>：验收/演练必须用隔离开例（独立数据目录 + 非 18920 端口），前端脚本指向 <code>AC_FE_BASE</code>。</p>
    </div>
  </div>
  <div class="card">
    <h3><span class="dot"></span>页面入口一览</h3>
    <div class="pick">
      <div class="pick-row"><b>工作大厅</b><span class="why">${link('/','index.html →')}　看全局任务 / 发任务</span></div>
      <div class="pick-row"><b>工作间</b><span class="why">${link('/workshop.html','workshop.html →')}　讨论、选人、开工、通道</span></div>
      <div class="pick-row"><b>成员隔间与桥</b><span class="why">${link('/booths.html','booths.html →')}　隔间、桥、共享资料库、项目模型</span></div>
      <div class="pick-row"><b>运维看板</b><span class="why">${link('/ops.html','ops.html →')}　审计时间线、心跳、健康</span></div>
      <div class="pick-row"><b>交付回执台</b><span class="why">${link('/handover.html','handover.html →')}　谁交了活、回执</span></div>
      <div class="pick-row"><b>Harness 接入</b><span class="why">${link('/register.html','register.html →')}　登记 agent</span></div>
    </div>
  </div>`;
}

function chRegister(){
  return `
  <div class="grid2">
    <div class="card">
      <h3><span class="dot"></span>0 · 注册要点（去接入页）</h3>
      <p class="lead"><b>必填只有两项</b>：<code>harness_id</code> + <code>ai.model_name</code>。
        ★<b>实测</b>（2026-10-10 真打 400）：其余字段都有默认值，<b><code>harness_name</code> 不是必填</b>。</p>
      <p class="lead" style="margin-top:8px"><b>可选但决定"能不能自动"</b>：三个动作脚本 <code>new_session</code> / <code>rename_session</code> / <code>deliver</code>
        ＋每个动作的 <code>foreground</code>（会不会占用你的桌面）。<b>不配也能用</b>，走"人工模式"。</p>
      <p class="sub" style="margin-top:8px">关联：<code>POST /api/harness/register</code>；真表单在 ${link('/register.html','Harness 接入页 →')}（教程+模板+远程接入都在那里）。</p>
    </div>
    <div class="card">
      <h3><span class="dot"></span>报错是可照做的（示意）</h3>
      <div class="form-demo">
        <div class="field-row"><label for="demoHid">harness_id *</label><input id="demoHid" name="harness_id" autocomplete="off" spellcheck="false" value="" placeholder="如 harness-示例-A…"></div>
        <div class="field-row"><label for="demoWake">唤醒方式</label><input id="demoWake" name="wakeup" autocomplete="off" spellcheck="false" value="" placeholder="如 http_api / file_poll…"></div>
        <div class="err-demo">
          示意（非真提交）：缺字段时后端返回 <b>中文、字段级</b> 的 <code>error</code>，另有 <code>必填</code> 与 <code>提示</code> 字段：<br>
          「缺少必填字段 <b>harness_id</b>」
          <div class="fix">
            <span>最小可用示例：</span>
            <code>{"harness_id":"harness-示例-A","ai":{"model_name":"示例模型"}}</code>
            <button class="btn" type="button" onclick="fillDemo()">一键填入（示意）</button>
          </div>
        </div>
      </div>
    </div>
  </div>`;
}
function fillDemo(){
  document.getElementById('demoHid').value='harness-示例-A';
  document.getElementById('demoWake').value='file_poll';
}

function chHall(){
  return `
  <div class="card">
    <h3><span class="dot"></span>1 · 广播任务 → 建工作间（去工作大厅）</h3>
    <p class="lead"><b>做什么</b>：在大厅发一条任务；平台<b>自动建一个工作间</b>并把状态推到"讨论中"。</p>
    <p class="lead" style="margin-top:8px">也可以直接在工作间页面<b>手工建</b>（草稿态）。</p>
    <p class="sub" style="margin-top:8px">关联：<code>POST /api/workshop</code>　·　入口：${link('/','工作大厅 →')}
      ｜下一步：讨论在 ${link('/workshop.html','工作间 →')}</p>
  </div>`;
}

function chD1(){
  return `
  <div class="card">
    <h3><span class="dot"></span>2 · 一级讨论（在工作间）</h3>
    <p class="lead"><b>做什么</b>：平台 AI 主持讨论，<b>先搞清楚任务方向</b>。</p>
    <p class="lead" style="margin-top:8px">AI 不可用时走<b>规则兜底</b>（响应里会标）——<b>不挡路</b>。</p>
    <p class="sub" style="margin-top:8px">产出：任务方向的共识（下一站是选成员）。入口：${link('/workshop.html','工作间 →')}</p>
  </div>
  <div class="grid2">
    <div class="card">
      <h3><span class="dot"></span>什么算成功</h3>
      <p class="lead">讨论有结论、能进入"选成员"；规则兜底时响应带标记，界面如实显示走的是哪条路。</p>
    </div>
    <div class="card">
      <h3><span class="dot"></span>失败怎么显示</h3>
      <p class="lead">AI 调不通 ⇒ 显示规则兜底标记与中文原因，<b>不装"AI 已处理"</b>。</p>
    </div>
  </div>`;
}

function chMembers(){
  return `
  <div class="card">
    <h3><span class="dot"></span>3 · 选成员（在工作间）</h3>
    <p class="lead"><b>做什么</b>：按 harness 能力<b>初选</b>，或你<b>手工给名单</b>，然后<b>确认名单</b>。</p>
    <p class="lead" style="margin-top:8px"><b>没配 AI 也能走</b>：平台会说明原因，手工路径照样可用（<b>不挡路</b>）。</p>
    <p class="sub" style="margin-top:8px">关联：<code>select-members</code> / <code>members</code> / <code>confirm-members</code>
      ；入口：${link('/workshop.html','工作间 · 选人/分工 →')}</p>
  </div>
  <div class="card">
    <h3><span class="dot"></span>确认名单之后</h3>
    <p class="lead">名单确认 ⇒ 进入 4→8（建会话 → 送桥 → 对接隔间与邮箱 → 命名 → 重命名），在成员卡上逐人推进。</p>
  </div>`;
}

function memberCard(m){
  const tpl=document.getElementById('tpl-member');
  const node=tpl.content.cloneNode(true);
  const q=k=>node.querySelector(k);
  q('.m-role').textContent=m.role;
  q('.m-name').textContent=m.name;
  q('.m-state').textContent=m.progress;
  q('.m-assign').textContent=m.assign;
  q('.m-session').textContent='会话名：'+m.session;
  q('.m-auto').innerHTML=m.auto?'<span class="badge auto">自动</span>':'<span class="badge manual">需人工</span>';
  q('.m-fg').innerHTML=(m.fg)?'<span class="badge fg">⚠ 这步会占用桌面</span>':'';
  q('.m-hint').textContent=m.hint?('人工指引（后端原话）：'+m.hint):'脚本可全自动执行';
  q('.m-progress').innerHTML='<span class="progress-pill">'+esc(m.progress)+'</span>';
  const v=q('.m-verified');
  if(m.verified==='ok') v.innerHTML='<span class="v-label ok">✔ 完成（脚本自证）</span>';
  else if(m.verified==='no') v.innerHTML='<span class="v-label no">⚠ 待人工 / 未核实</span>';
  else v.innerHTML='<span class="v-label idle">未开始</span>';
  q('.m-reason').textContent=m.vnote||'';
  return node;
}
const SAMPLE_MEMBERS=[
  { role:'组长', name:'示例Harness-A', state:'entered', assign:'library/示例Harness-A-组长/assignment.md', session:'示例间-项目-组长-m0', auto:true, fg:false, hint:'', progress:'dispatched', verified:'ok', vnote:'建会话已脚本自证' },
  { role:'码农', name:'示例Harness-B', state:'dispatched', assign:'library/示例Harness-B-码农/assignment.md', session:'示例间-项目-码农-m1', auto:false, fg:true, hint:'请在你的 harness 里新建一个对话（随后平台会把它重命名成：示例间-项目-码农-m1）', progress:'dispatched', verified:'no', vnote:'' },
  { role:'搜索者', name:'示例Harness-C', state:'pending', assign:'library/示例Harness-C-搜索者/assignment.md', session:'（未建立）', auto:true, fg:false, hint:'', progress:'pending', verified:'idle', vnote:'尚未开始' }
];

function chChannel(){
  return `
  <div class="card">
    <h3><span class="dot"></span>4·5·6·7·8 · 通道与隔间（在工作间 · 成员卡）</h3>
    <p class="lead"><b>做什么</b>：给每个成员<b>建会话 → 送桥 → 对接隔间与邮箱 → 平台给名字 → 重命名</b>。</p>
    <p class="lead" style="margin-top:8px"><b>自动的条件（两条同时满足）</b>：① 注册时配了 <code>new_session</code> 脚本；
      ② 该动作 <b>不抢占你的桌面</b>（<code>foreground ≠ required</code>）。会抢桌面的<b>自动跳过</b>，留给界面按钮 + 人工确认。</p>
    <div class="band">
      <div class="b-ok"><b>✔ verified=true</b>脚本做了并自证 → 才允许显示「完成」</div>
      <div class="b-no"><b>⚠ verified=false</b>可能做了但无法自证 → 永远「待人工 / 未核实」</div>
      <div class="b-idle"><b>○ 未开始</b>还没轮到 → 中性，不抢戏</div>
    </div>
    <p class="sub" style="margin-top:8px"><b>★最重要的规矩（全篇）</b>：<b>脚本做了 ≠ 做成了</b>。凡不能自证的动作，界面一律显示「<b>⚠ 待人工/未核实</b>」，
      <b>禁止</b>显示"完成/成功"。</p>
    <p class="sub" style="margin-top:6px"><b>一人一间一桥一邮箱</b>：隔间 <code>&lt;职位&gt;_&lt;成员id&gt;/</code>（跟人、跨会话存活）；
      平台<b>只往该成员自己的邮箱</b>投消息。</p>
    <p class="sub" style="margin-top:6px">操作入口在工作间右栏：${link('/workshop.html','工作间 · 会话流程面板 →')}（本页不代操作）。</p>
  </div>
  <div class="card">
    <h3><span class="dot"></span>成员卡长什么样（示意卡，静态数据）</h3>
    <div class="grid2" id="channelCards"></div>
  </div>`;
}

function chRetire(){
  return `
  <div class="grid2">
    <div class="card">
      <h3><span class="dot"></span>（按需）什么时候用</h3>
      <p class="lead">你对这个成员/对话不满意，或<b>对话太长"老化"了</b>，要<b>换人或换对话</b>。</p>
      <p class="lead" style="margin-top:8px"><b>停用通道</b>：把当前对话断开（桥归档、不再投消息），<b>隔间里的东西一样不动</b>（<b>停用≠删除</b>）。</p>
    </div>
    <div class="card">
      <h3><span class="dot"></span>怎么新建</h3>
      <p class="lead"><b>新建通道</b>：换一条新对话；<b>同一个成员同时只能有一条通道</b>（所以要<b>先停用再新建</b>）。</p>
      <p class="lead" style="margin-top:8px">新对话的名字会<b>带序号</b>以便区分（如 <code>…-码农-m0-2</code>）。</p>
      <p class="sub" style="margin-top:8px">关联：<code>POST …/channel/retire</code>、<code>POST …/channel/establish</code>
        ；<b>界面在工作间</b>，不在本页。入口：${link('/workshop.html','工作间 · 成员卡 →')}</p>
    </div>
  </div>`;
}

function chVisual(){
  return `
  <div class="card">
    <h3><span class="dot"></span>9 · 更新视觉脚本 —— 现状：只做到"名字进数据"</h3>
    <p class="lead">平台会把平台生成的名字写进成员数据（<code>session_aliases</code>）。</p>
    <p class="lead" style="margin-top:8px"><b>独立的"视觉脚本"尚未做</b>——本页<b>如实写</b>：只做到"名字进数据"，
      <b>不写</b>"已完成"（准则⑤：不许假装完成）。</p>
    <p class="sub" style="margin-top:8px">成员数据实况：${link('/workshop.html','工作间 · 成员卡 →')}　·　隔间实况：${link('/booths.html','成员隔间与桥 →')}</p>
  </div>`;
}

function chD23(){
  return `
  <div class="grid2">
    <div class="card">
      <h3><span class="dot"></span>二级讨论 —— 与组长，产出「项目模型」</h3>
      <p class="lead"><b>做什么</b>：与<b>组长</b>讨论项目要求与分工 ⇒ 产出 <b>项目模型</b>（资料库 <code>model.md</code>，<b>五块</b>：
        目标与验收／组成分解／分工对应／依赖与顺序／产物落点；<b>缺块要显示"未完成"</b>）。</p>
      <p class="sub" style="margin-top:8px">界面：隔间页资料库面板可<b>建立 / 编辑</b>（五块表单或整篇，记录执笔人；每次保存前自动备份）。</p>
      <p class="sub" style="margin-top:6px">入口：${link('/booths.html','成员隔间与桥 · 资料库面板 →')}</p>
    </div>
    <div class="card">
      <h3><span class="dot"></span>三级讨论 —— 全员上场</h3>
      <p class="lead"><b>做什么</b>：<b>全员</b>上场，各居自己隔间。</p>
      <p class="lead" style="margin-top:8px"><b>组长不被员工隔间框住</b>：组长可读共享资料库 + 全员状态，并可执笔项目模型；
        普通成员只写自己隔间。</p>
      <p class="sub" style="margin-top:8px">入口：${link('/workshop.html','工作间 →')}</p>
    </div>
  </div>`;
}

function chIntegrate(){
  return `
  <div class="card">
    <h3><span class="dot"></span>收尾 · 阶段整合</h3>
    <p class="lead"><b>做什么</b>：平台把各成员 <code>output/</code> 里的产物汇成清单，交整合者按路径取。</p>
    <p class="lead" style="margin-top:8px"><b>诚实点</b>：若<b>全员都没有产物</b>，要<b>如实显示"无产物可整合"</b>，<b>不算成功</b>。</p>
    <p class="sub" style="margin-top:8px">关联：<code>POST …/integrate</code>　·　入口：${link('/workshop.html','工作间 →')}</p>
  </div>`;
}

function chRescue(){
  return `
  <div class="tri">
    <div class="s idle">
      <h4>⚪ 当前无进行中的工作</h4>
      <p>空闲——没活干是<b>正常态</b>。不标红、不叫"掉线"、<b>界面上没有抢救按钮</b>（结构上就打扰不到它）。</p>
    </div>
    <div class="s ok">
      <h4>🟢 正常</h4>
      <p>有活在进行，桥等信号正常。</p>
    </div>
    <div class="s rescue">
      <h4>⚠ 需要抢救</h4>
      <p>有活却不对劲：通知组长 → 试着重连 → 多次不成再通知你。</p>
      <div class="act"><span class="sub">按钮只出现在需要抢救的卡上 · 入口在工作间「成员状态」面板</span></div>
    </div>
  </div>
  <div class="card">
    <h3><span class="dot"></span>口径三条</h3>
    <p class="lead">① <b>判据两级</b>：先问"它现在有没有在进行的活"？没有 ⇒ 空闲（正常态，不救、不叫人）；有活 + 异常信号 ⇒ 抢救。</p>
    <p class="lead" style="margin-top:6px">② <b>硬/软信号</b>：只有"桥进程确认退出"这类<b>硬信号</b>才允许说"联系不上"；心跳失联（本项目<b>心跳可选</b>）只进详情。</p>
    <p class="lead" style="margin-top:6px">③ <b>自动扫描默认关</b>；手动按钮是主路径。</p>
    <p class="sub" style="margin-top:8px">真实面板：${link('/workshop.html','工作间 · 成员状态 →')}</p>
  </div>`;
}

function chCheat(){
  const pageRows=[
    ['看全局任务 / 发任务','工作大厅'],
    ['讨论、选人、开工、通道停用/新建、整合','工作间'],
    ['成员隔间、桥状态、共享资料库、项目模型','成员隔间与桥'],
    ['审计时间线、心跳、健康','运维看板'],
    ['谁交了活、回执','交付回执台'],
    ['登记 agent（harness）','Harness 接入'],
    ['全流程总说明（只讲不做）','本页（流程说明）']
  ].map(r=>`<div class="pick-row"><b>${esc(r[0])}</b><span class="why">${esc(r[1])}</span></div>`).join('');
  const rows=[
    ['接入注册','POST /api/harness/register','接入页'],
    ['建会话/送桥/批量','establish · deliver · onboard-members','工作间'],
    ['开工（工作决策）','POST …/start','工作间'],
    ['选人','select-members / members / confirm-members','工作间'],
    ['讨论','POST …/discuss（message）','工作间'],
    ['通道停用/新建','POST …/channel/retire · establish（V28 已实现）','工作间'],
    ['成员卡数据','GET …/members-status','只读'],
    ['隔间实况','GET …/compartments','只读'],
    ['资料库读','GET …/library','隔间页（只读）'],
    ['项目模型/框架写入','POST …/library/update','隔间页'],
    ['成员状态/抢救','offline-report · recover · recover-scan','工作间/只读'],
    ['审计/心跳看板','GET /api/audit · /api/heartbeat','运维看板'],
    ['交付回执','handover 家族','回执台'],
    ['阶段整合','POST …/integrate（无产物如实显示）','工作间']
  ].map(r=>`<div class="pick-row"><b>${esc(r[0])}</b><span class="mono" style="font-size:11px;color:var(--muted)">${esc(r[1])}</span><span class="why">${esc(r[2])}</span></div>`).join('');
  return `
  <div class="card">
    <h3><span class="dot"></span>你要干什么 → 去哪（页面速查）</h3>
    <div class="pick">${pageRows}</div>
  </div>
  <div class="card">
    <h3><span class="dot"></span>端点归属速查</h3>
    <div class="pick">${rows}</div>
  </div>
  <div class="grid2">
    <div class="card">
      <h3><span class="dot"></span>默认开关</h3>
      <p class="sub" style="line-height:2">
        在用实例端口 <code>18920</code> · 隔离实例须改数据目录 · 前端验收指向 <code>AC_FE_BASE</code><br>
        掉线自动扫描 <b>默认关</b> · 动作脚本 foreground 默认 optional（required 要提前告知占桌面）<br>
        开机自启 <b>没有</b>（保留口子，需要自己写代码）
      </p>
    </div>
    <div class="card">
      <h3><span class="dot"></span>两条硬规矩</h3>
      <p class="lead">① <b>verified=false 一律「待人工/未核实」</b>，界面禁止出现"完成/成功"；<br>② 平台不认识的 harness 明写"先注册或改绑"，不装"已派发"。</p>
      <p class="sub" style="margin-top:8px">本页依据：V30 流程说明书内容基准（2026-10-10）· RUNBOOK ·《项目流程详情》。</p>
    </div>
  </div>`;
}

function fillWindow(id, members){
  const box=document.getElementById(id);
  if(!box) return;
  box.innerHTML='';
  members.forEach(m=>box.appendChild(memberCard(m)));
}

function selectChapter(id){
  if(!CHAPTERS.some(c=>c.id===id)) id='overview';
  try{const h='#'+id; if(location.hash!==h) history.replaceState(null,'',h);}catch(e){}
  const ch=CHAPTERS.find(c=>c.id===id);
  document.querySelectorAll('.rail-item').forEach(li=>li.classList.toggle('on',li.dataset.stage===id));
  document.getElementById('stageNo').textContent=ch.no;
  document.getElementById('stageTitle').textContent=ch.t;
  document.getElementById('stageUser').textContent=ch.s;
  document.getElementById('stageApi').textContent=ch.api;
  const body=document.getElementById('stageBody');
  body.innerHTML=ch.render();
  if(ch.id==='channel') fillWindow('channelCards', SAMPLE_MEMBERS);
}

function buildRail(){
  const ol=document.getElementById('railList');
  CHAPTERS.forEach(ch=>{
    const li=document.createElement('li');
    li.className='rail-item'; li.dataset.stage=ch.id;
    li.innerHTML=`<button class="rail-btn" type="button"><span class="no">${esc(ch.no)}</span><span class="t">${esc(ch.t)}</span><span class="s">${esc(ch.s)}</span></button>`;
    li.querySelector('button').addEventListener('click',()=>selectChapter(ch.id));
    ol.appendChild(li);
  });
}

buildRail();
window.addEventListener('hashchange',()=>{ selectChapter((location.hash||'#overview').slice(1)); });
selectChapter((location.hash||'#overview').slice(1));
