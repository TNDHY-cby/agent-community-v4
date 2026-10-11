const { chromium } = require('playwright-core');
const fs = require('fs');
const path = require('path');
const BASE = process.env.AC_FE_BASE || 'http://127.0.0.1:18930';
const RESULT = process.env.AC_VERIFY_OUT || path.join(__dirname,'FE25_验证');
const SHOTS = process.env.AC_VERIFY_OUT || path.resolve(__dirname,'../../../temp/验收证据截图/FE25_验证');
fs.mkdirSync(RESULT,{recursive:true});
fs.mkdirSync(SHOTS,{recursive:true});
(async()=>{
  const created=await fetch(BASE+'/api/workshop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'FE25验收-自建'})});
  const ws=(await created.json()).workshop_id;
  if(!ws) throw new Error('自建工作间失败');
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const failures=[], evidence=[], writes=[], errors=[];
  try{
    for(const name of ['ops','booths']){
      const page=await browser.newPage({viewport:{width:1440,height:1000}});
      page.on('request',r=>{if(r.url().includes('/api/')&&r.method()!=='GET') writes.push(r.method());});
      page.on('pageerror',e=>errors.push(e.message));
      const ids=name==='ops'?['q3Orphans','q3Delivery']:['mOrphans','mDelivery'];
      await page.goto(BASE+'/'+name+'.html?ws_id='+ws);
      await page.waitForFunction(ids=>ids.every(id=>document.getElementById(id).textContent.includes('0 ')),ids);
      const snapshot=()=>page.evaluate(ids=>ids.map(id=>{const n=document.getElementById(id);return {text:n.textContent,title:n.title,color:getComputedStyle(n).color};}),ids);
      evidence.push({page:name,state:'真实零值',values:await snapshot()});
      let state='counts';
      for(const suffix of ['orphans','delivery-status']){
        await page.route('**/api/workshop/*/'+suffix,route=>{
          if(state==='failed') return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'汇总暂不可用'})});
          const body=suffix==='orphans'?{orphan_count:2,headline:'有2名孤儿'}:{total_pending:5,stuck_count:1,headline:'有5条未送达'};
          return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
        });
      }
      await page.click('#refreshBtn');
      await page.waitForFunction(ids=>document.getElementById(ids[0]).textContent==='孤儿 2 人'&&document.getElementById(ids[1]).textContent==='未送达 5 条',ids);
      const counts=await snapshot();
      if(counts[0].title!=='有2名孤儿'||counts[1].title!=='有5条未送达') failures.push(name+' headline缺失');
      evidence.push({page:name,state:'非零汇总',values:counts});
      await page.screenshot({path:path.join(SHOTS,name+'_汇总.png')});
      state='failed';
      await page.click('#refreshBtn');
      await page.waitForFunction(ids=>ids.every(id=>document.getElementById(id).textContent.includes('加载失败')),ids);
      evidence.push({page:name,state:'接口失败',values:await snapshot()});
      if(name==='ops'){
        await page.evaluate(()=>{q3WsId='';renderQ3Counts(null,null);});
      }else{
        await page.evaluate(()=>{renderMemberCounts(null,null);});
      }
      const empty=await snapshot();
      if(empty.some(x=>!x.text.includes('—')||x.title)) failures.push(name+'未选工作间残留数字');
      evidence.push({page:name,state:'未选择',values:empty});
      await page.close();
    }
    if(writes.length||errors.length) failures.push('出现页面写请求或脚本错误');
    const result={pass:!failures.length,failures,evidence,writes,errors};
    fs.writeFileSync(path.join(RESULT,'result.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify(result,null,2));
    process.exitCode=failures.length?1:0;
  }finally{
    await browser.close();
    await fetch(BASE+'/api/workshop/'+ws,{method:'DELETE'});
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
