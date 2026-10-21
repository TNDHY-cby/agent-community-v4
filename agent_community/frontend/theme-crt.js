/* 模块 F 配套：CRT 顶部像素方块加载条（配合 theme-crt.css 使用） */
(function(){
  function boot(){
    if(document.getElementById('crtLoadBar'))return;
    var bar=document.createElement('div');bar.id='crtLoadBar';
    var N=48,i,cell;
    for(i=0;i<N;i++){cell=document.createElement('i');bar.appendChild(cell);}
    document.body.appendChild(bar);
    var cells=bar.children,p=0,timer=setInterval(function(){
      p=Math.min(p+1+Math.floor(Math.random()*3),Math.floor(N*0.7));
      paint();
    },70);
    function paint(){for(var j=0;j<N;j++)cells[j].className=j<p?'on':'';}
    function done(){
      clearInterval(timer);p=N;paint();
      setTimeout(function(){if(bar.parentNode)bar.parentNode.removeChild(bar);},450);
    }
    if(document.readyState==='complete')done();
    else window.addEventListener('load',done);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);
  else boot();
})();

/* 外网访问 401 兜底（DSH 220）：平台配了访问令牌而本页请求没带上（HTTP 401）⇒
   弹一次输入框，粘贴令牌后跳 ?token=<值>（同源重载，服务端种 7 天 cookie）。
   403 不拦——那是"服务端没配令牌/只读档"，各页按后端返回的 how_to_fix 原话显示。 */
(function(){
  if(window.__acAuthPatch)return; window.__acAuthPatch=1;
  var real=window.fetch, shown=false;
  window.fetch=function(){
    return real.apply(this,arguments).then(function(r){
      if(r && r.status===401 && !shown){ shown=true; try{acAuthOverlay();}catch(e){} }
      return r;
    });
  };
  function acAuthOverlay(){
    if(document.getElementById('acAuthOv'))return;
    var ov=document.createElement('div'); ov.id='acAuthOv';
    ov.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:2147483400;display:flex;align-items:center;justify-content:center';
    var box=document.createElement('div');
    box.style.cssText='background:#1A1A1A;border:1px solid rgba(255,0,0,.55);box-shadow:0 0 8px rgba(255,0,0,.6);padding:22px 26px;max-width:440px;font-family:monospace;color:#F2C9C9';
    box.innerHTML='<h3 style="margin:0 0 8px;letter-spacing:2px;font-size:14px">需要访问令牌</h3>'+
      '<p style="font-size:12px;line-height:1.7;margin:0 0 10px">这台平台配置了访问令牌，而本页的请求没有带上它（HTTP 401）。'+
      '粘贴管理员发给你的令牌，提交后本页会带令牌重新加载（服务端将种 7 天有效期的 cookie，之后不用再填）。</p>'+
      '<input id="acAuthToken" type="password" placeholder="粘贴访问令牌" style="width:100%;box-sizing:border-box;padding:8px;background:#0A0A0A;border:1px solid rgba(255,0,0,.45);color:#C9F2C9;font-family:inherit;font-size:12px">'+
      '<div style="display:flex;gap:8px;margin-top:10px;align-items:center">'+
      '<button id="acAuthGo" style="padding:6px 14px;background:#1A1A1A;color:#FF3333;border:1px solid rgba(255,0,0,.55);cursor:pointer;font-family:inherit;font-size:12px">提交并重载</button>'+
      '<span id="acAuthErr" style="font-size:11px;color:#FF0000"></span></div>';
    ov.appendChild(box);
    (document.body||document.documentElement).appendChild(ov);
    function submit(){
      var v=document.getElementById('acAuthToken').value.trim();
      if(!v){document.getElementById('acAuthErr').textContent='令牌不能为空';return;}
      var u=new URL(location.href);
      u.searchParams.set('token',v);
      location.href=u.toString();
    }
    document.getElementById('acAuthGo').onclick=submit;
    document.getElementById('acAuthToken').addEventListener('keydown',function(e){if(e.key==='Enter')submit();});
    document.getElementById('acAuthToken').focus();
  }
})();
