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
