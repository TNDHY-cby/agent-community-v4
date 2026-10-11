// FE-27: click 「🔬 大厅快速验证」 in a REAL browser on an ISOLATED instance and assert
// the report tells the truth: 跑通 → 模板标记已验证 → 验证间已拆掉（只留模板）.
const {chromium}=require('playwright-core');
const fs=require('fs');
const path=require('path');
const cp=require('child_process');
const BASE=process.env.AC_FE_BASE||'http://127.0.0.1:18933';
const OUT=process.env.AC_VERIFY_OUT||'D:\\Temp\\ac_fe27';
const PY='D:\\Program Files\\Tencent\\Marvis\\MarvisAgent\\1.60.2800.657\\runtime\\python311\\python.exe';
fs.mkdirSync(OUT,{recursive:true});

const HID='fe27-probe';
const AGENT=path.join('D:\\Temp','ac_fe27_agent');

function sh(cmd){return cp.execSync(cmd,{stdio:'pipe'}).toString();}

(async()=>{
  // ── 1) 建假 agent + 无参包装脚本（纯 ASCII、无 BOM）──
  fs.rmSync(AGENT,{recursive:true,force:true});
  fs.mkdirSync(AGENT,{recursive:true});
  const agentSrc=fs.readFileSync(path.join(__dirname,'standin_agent3.py'),'utf-8');
  fs.writeFileSync(path.join(AGENT,'standin_agent.py'),agentSrc,'utf-8');
  fs.writeFileSync(path.join(AGENT,'wake.cmd'),
    '@echo off\r\nrem bare trigger\r\n"'+PY+'" "%~dp0standin_agent.py"\r\n',
    {encoding:'ascii'});
  const TRIGGER='cmd.exe /c "'+path.join(AGENT,'wake.cmd')+'"';

  // ── 2) 平台侧：注册 harness + 写桥模板（INBOX_DIR 用**平台自己的隔间邮箱**）──
  // ⚠️ 邮箱路径必须来自**这个实例真正使用的 data 目录**（AC_DATA_DIR），
  //   不能拿 process.cwd() 去猜 —— 隔离实例用 -DataDir 时两者不同，
  //   猜错就变成"平台写这个邮箱、桥轮询另一个邮箱"（我上一轮就掉过这个坑）。
  const DATA=process.env.AC_DATA_DIR||path.join(process.cwd(),'agent_community','data');
  const inbox=path.join(DATA,'verify_sandbox','vs_'+HID,'验证员_v0','inbox');
  console.log('data 目录:',DATA);
  await fetch(BASE+'/api/harness/'+HID,{method:'DELETE'}).catch(()=>{});
  let r=await fetch(BASE+'/api/harness/register',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({harness_id:HID,harness_name:'FE27大厅验证演示',
      harness_type:'desktop-agent',wakeup_method:'file_poll',wakeup_dir:inbox,
      acp_command:'probe.exe --x',ai:{model_name:'unknown'}})});
  console.log('注册:',r.status);
  r=await fetch(BASE+'/api/harness/'+HID+'/bridge-template',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:'FE27桥',kind:'file_poll',
      params:{INBOX_DIR:inbox,TRIGGER_CMD:TRIGGER}})});
  console.log('存模板:',r.status);

  // 假 agent 从**它自己旁边**的 inbox.txt 读路径（standin_agent3.py 就是这么找的）
  fs.writeFileSync(path.join(AGENT,'inbox.txt'),inbox,'utf-8');

  // ── 3) 真浏览器：点按钮 ──
  const browser=await chromium.launch({channel:'msedge'});
  const page=await browser.newPage({viewport:{width:1500,height:1000}});
  const errs=[];page.on('pageerror',e=>errs.push(String(e).slice(0,140)));
  await page.goto(BASE+'/register.html',{waitUntil:'domcontentloaded'});
  await page.waitForTimeout(2500);
  // 报告是插到**列表卡片**里的 ⇒ 必须先等列表把这个 harness 渲染出来
  await page.waitForFunction(id=>!!document.querySelector('.h-item[data-hid="'+CSS.escape(id)+'"]'),
                             HID,{timeout:15000});
  console.log('卡片已渲染');

  const hasBtn=await page.evaluate(()=>!!document.querySelector('button[onclick^="sandboxVerify"]'));
  console.log('按钮存在:',hasBtn);

  await page.evaluate(id=>{ window.event={target:document.querySelector('button[onclick^="sandboxVerify"]')};
    return sandboxVerify(id); },HID);
  await page.waitForTimeout(25000);   // 等验证跑完（自检约 16 秒）

  const report=await page.evaluate(id=>{
    const host=document.querySelector('.h-item[data-hid="'+CSS.escape(id)+'"]');
    let el=host?host.querySelector('.verify-report'):null;
    if(!el) el=document.querySelector('#sandboxReportHost .verify-report');
    if(!el) el=document.querySelector('.verify-report');
    return el?el.innerText.replace(/\s+/g,' '):'(无报告)';
  },HID);
  console.log('报告:',report.slice(0,600));
  await page.screenshot({path:path.join(OUT,'fe27_report.png'),fullPage:true});

  const msg=await page.evaluate(()=>{const e=document.getElementById('msg');return e?e.innerText:'';});
  console.log('顶部提示:',msg.slice(0,180));

  // ── 4) 断言 ──
  const checks={
    '按钮存在':hasBtn,
    '报告说跑通':/跑通了 ✅/.test(report),
    '报告说模板已标记已验证':/已验证/.test(report),
    '报告说验证间已拆掉只留模板':/只留模板/.test(report),
    '自检状态=通了':/通了（收到回话）/.test(report),
  };
  console.log('\n断言:');
  Object.entries(checks).forEach(([k,v])=>console.log('  '+(v?'✓':'✗')+' '+k));
  console.log('pageErrors:',errs.length?JSON.stringify(errs.slice(0,2)):'none');

  // 验证间确实没了
  const shops=await fetch(BASE+'/api/workshops').then(x=>x.json()).catch(()=>({}));
  const items=Array.isArray(shops)?shops:(shops.workshops||[]);
  const lingering=items.filter(w=>String(w.workshop_id||'').startsWith('vs_')).map(w=>w.workshop_id);
  console.log('残留验证间:',lingering.length?lingering:'（无）');

  await fetch(BASE+'/api/harness/'+HID,{method:'DELETE'}).catch(()=>{});
  fs.rmSync(AGENT,{recursive:true,force:true});
  await browser.close();
  const allOk=Object.values(checks).every(Boolean)&&lingering.length===0;
  console.log('\nFE-27:',allOk?'PASS':'FAIL');
  process.exit(allOk?0:1);
})().catch(e=>{console.error(e);process.exit(1)});
