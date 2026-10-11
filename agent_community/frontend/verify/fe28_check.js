// FE-28: assert the copy experience is now ONE segment, and segments 2/3 are collapsed
// reference (and still copyable while collapsed — textContent works on hidden nodes).
const {chromium}=require('playwright-core');
const BASE=process.env.AC_FE_BASE||'http://127.0.0.1:18933';
const fs=require('fs');
const OUT=process.env.AC_VERIFY_OUT||'D:\\Temp\\ac_fe28';
fs.mkdirSync(OUT,{recursive:true});

(async()=>{
  const browser=await chromium.launch({channel:'msedge'});
  const page=await browser.newPage({viewport:{width:1500,height:1000}});
  const errs=[];page.on('pageerror',e=>errs.push(String(e).slice(0,150)));
  await page.goto(BASE+'/register.html',{waitUntil:'domcontentloaded'});
  await page.waitForTimeout(2000);

  const d=await page.evaluate(()=>{
    const btns=Array.from(document.querySelectorAll('button.btn')).map(b=>b.innerText.trim());
    const dets=Array.from(document.querySelectorAll('details')).map(x=>({
      open:x.open, summary:(x.querySelector('summary')||{}).innerText||''}));
    // 三段各自的可见性与文本长度（复制读的是 textContent，收起也应完整）
    const g=id=>{const e=document.getElementById(id);return e?{len:(e.textContent||'').length, visible:e.offsetParent!==null}:null;};
    return {
      copyButtons:btns.filter(t=>/复制|不用复制/.test(t)),
      details:dets,
      tplTextLen:(document.getElementById('tplText')||{textContent:''}).textContent.length,
      copyBridge:g('copyBridge'),
      copyLaunch:g('copyLaunch'),
      primaryCopyBtn:(document.querySelector('button[onclick="copyTemplate()"]')||{}).innerText||'',
    };
  });

  console.log('复制类按钮:');
  d.copyButtons.forEach(t=>console.log('   -',t));
  console.log('主按钮文案:',d.primaryCopyBtn);
  console.log('details 状态:',JSON.stringify(d.details));
  console.log('第1段文本长度:',d.tplTextLen);
  console.log('第2段(参考):',JSON.stringify(d.copyBridge));
  console.log('第3段(参考):',JSON.stringify(d.copyLaunch));

  // 点一下第 2 段的复制按钮（收起状态下），确认仍能拿到完整文本
  const copied=await page.evaluate(async()=>{
    const t=document.getElementById('copyBridge').textContent||'';
    return t.length;
  });
  console.log('收起状态可读取的第2段长度:',copied);

  await page.screenshot({path:OUT+'/fe28_page.png',fullPage:true});

  const checks={
    '只有一个主复制按钮':/复制这一段给 AI/.test(d.primaryCopyBtn),
    '第2段按钮标注"通常不用复制"':d.copyButtons.some(t=>/通常不用复制/.test(t)&&/架桥速查/.test(t)),
    '第3段按钮标注"通常不用复制"':d.copyButtons.some(t=>/通常不用复制/.test(t)&&/平台自动接入/.test(t)),
    '第2段默认折叠':d.details.some(x=>/架桥速查/.test(x.summary)&&x.open===false),
    '第3段默认折叠':d.details.some(x=>/平台自动接入/.test(x.summary)&&x.open===false),
    '第1段仍可展开':d.details.some(x=>/展开完整模板/.test(x.summary)),
    '收起时复制文本仍完整(第2段>500字)':copied>500,
    '第1段话术完整(>3000字)':d.tplTextLen>3000,
    '无 JS 错误':errs.length===0,
  };
  console.log('\n断言:');
  Object.entries(checks).forEach(([k,v])=>console.log('  '+(v?'✓':'✗')+' '+k));
  if(errs.length) console.log('pageErrors:',JSON.stringify(errs.slice(0,2)));
  await browser.close();
  const ok=Object.values(checks).every(Boolean);
  console.log('\nFE-28:',ok?'PASS':'FAIL');
  process.exit(ok?0:1);
})().catch(e=>{console.error(e);process.exit(1)});
