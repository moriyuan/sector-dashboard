// Embedded in the terminal closure: uses its selection and render functions.
const TM=globalThis.KplTracking;
const tracking={recent:null,rows:[],queue:[],current:null,returnKey:null,temporary:null,busy:false,serial:0,painted:'',notice:'',priorityTimer:null};
const trackHost=$('[data-detail]').closest('section');trackHost.classList.add('kt-track-panel');
trackHost.innerHTML=`<header class="kt-section-head"><h3>个股追踪</h3><nav class="kt-track-nav" aria-label="个股追踪切换"><button data-track-prev aria-label="上一只股票">‹ 上一只</button><span class="kt-track-count" data-track-count aria-live="polite">0 / 0</span><button data-track-next aria-label="下一只股票">下一只 ›</button></nav></header><div class="kt-track-stage" data-track-stage tabindex="0" role="group" aria-label="个股追踪卡片，点击下一只，左右方向键切换"><div class="kt-track-sheet" data-track-sheet></div></div><div class="kt-track-foot"><span data-track-notice role="status"></span><span>点击卡片 · 查看下一只</span></div>`;
const trackStage=$('[data-track-stage]'),trackSheet=$('[data-track-sheet]');
function syncTracking(){
 const previous=tracking.queue,old=tracking.current,index=previous.indexOf(old);
 tracking.rows=TM.union(D,tracking.recent);tracking.queue=TM.reconcile(previous,tracking.rows);
 if(tracking.temporary&&tracking.queue.includes(TM.key(tracking.temporary))){tracking.current=TM.key(tracking.temporary);tracking.temporary=null;}
 if(!tracking.queue.includes(tracking.current)){
  tracking.current=tracking.queue[Math.min(Math.max(index,0),tracking.queue.length-1)]||null;
  if(old&&!tracking.temporary)tracking.notice='该股已退出当前追踪池，顺移到下一只';
 }
}
function trackingRow(){return tracking.temporary||tracking.rows.find(r=>TM.key(r)===tracking.current);}
function trackSelect(code){
 syncTracking();const row=tracking.rows.find(r=>r.code===code);
 if(row){tracking.current=TM.key(row);tracking.temporary=null;tracking.returnKey=null;}
 else{
  const other=D.stocks.find(r=>r.code===code)||D.tenDay?.rows?.find(r=>r.code===code);
  if(!other)return;
  if(!tracking.temporary)tracking.returnKey=tracking.current;
  tracking.temporary={...other,sources:[{kind:'temporary',label:'临时查看',day:D.date}]};
 }
 tracking.notice='';trackPaint(true);trackPriority();
}
function trackPriority(){
 clearTimeout(tracking.priorityTimer);const row=trackingRow();if(!row||tracking.temporary)return;
 tracking.priorityTimer=setTimeout(()=>fetch('./tracking/select',{method:'POST',headers:{'Content-Type':'application/json','X-Preview-Token':'__REFRESH_TOKEN__'},body:JSON.stringify({code:row.code})}).catch(()=>{}),160);
}
function trackNext(direction){
 if(tracking.busy)return;
 syncTracking();if(!tracking.queue.length)return;
 if(tracking.temporary){tracking.temporary=null;tracking.current=tracking.queue.includes(tracking.returnKey)?tracking.returnKey:tracking.queue[0];tracking.returnKey=null;}
 else{const i=tracking.queue.indexOf(tracking.current);tracking.current=tracking.queue[(i+direction+tracking.queue.length)%tracking.queue.length];}
 tracking.notice='';trackPaint(true,direction);trackPriority();
}
function trackStamp(f){return f?`${f.day||'日期未提供'} ${f.stamp?.slice(11,19)||'时点未提供'}`:'未取得';}
function trackHTML(row){
 if(!row)return '<div class="kt-empty">当前追踪池暂无个股<br>等待日内前排或近期核心名单</div>';
 const q=TM.quote(D,tracking.recent,row),p=q.price,c=q.change;
 const stale=Object.values(tracking.recent?.sources||{}).filter(s=>s.status?.state!=='ready'||s.packet?.day!==D.date).length;
 const latest=p?.day||c?.day,meta=latest===D.date?'行情日期 '+latest:'最近可用行情 '+(latest||'未提供');
 const field=(label,value,source)=>`<div><span>${label}</span><b>${value}</b><small>${esc(trackStamp(source))}</small></div>`;
 const n=v=>TM.valid(v)?v.toFixed(2):'—';
 const limit=D.stocks.find(s=>TM.key(s)===TM.key(row));
 const ten=row.sources.find(s=>s.kind==='ten'),twenty=row.sources.find(s=>s.kind==='twenty');
 const notes=[`板块：${row.theme||limit?.theme||'未提供'}`,
  limit?`${D.date} · ${limit.boards}板 · 涨停时间 ${limit.limit_time||'未提供'}`:'当前涨停池未收录',
  `10日涨幅 ${pct(row.return_10d)}${ten?' · '+ten.day+' 榜第'+ten.rank+'名':''}　20日涨幅 ${pct(row.return_20d)}${twenty?' · '+twenty.day+' 榜第'+twenty.rank+'名':''}`,
  q.reason?`供应商说明（${trackStamp(q.reason)}）：\n${String(q.reason.value).replace(/\\r\\n/g,'\n')}`:'供应商个股说明未取得',
  '— 表示未取得；各指标保留各自日期与来源时点。'].join('\n');
 const tags=row.sources.map(s=>`<span class="kt-track-tag ${s.kind==='intraday'?'is-day':''}" title="${esc(s.day||s.at?.slice(0,10)||'日期未提供')}">${esc(s.label)}${s.rank?' #'+s.rank:''}</span>`).join('');
 return `<div class="kt-track-identity"><div><div class="kt-track-name">${esc(row.name)}</div><span class="kt-track-code">${TM.market(row)} ${esc(row.code)}</span></div><div class="kt-track-price ${color(p?.value===undefined?null:c?.value)}">${n(p?.value)}<small class="${color(c?.value)}">${pct(c?.value)}</small></div></div><div class="kt-track-tags">${tags}</div><div class="kt-track-meta">${esc(meta)} · 价格 ${esc(p?.stamp?.slice(11,19)||'时点未提供')} · 涨幅 ${esc(trackStamp(c))}<br>名单日期 ${esc(D.date)} · ${tracking.recent?'近期核心已接入':'近期核心读取中'}${stale?' · '+stale+'路名单待更新/旧快照':''} · 全市场追踪</div><div class="kt-track-grid">${field('成交额',money(q.amount?.value),q.amount)}${field('换手率',pct(q.turnover?.value),q.turnover)}${field('最高',n(q.high?.value),q.high)}${field('最低',n(q.low?.value),q.low)}</div><div class="kt-track-notes" tabindex="0" aria-label="个股说明，可滚动阅读">${esc(notes)}</div>`;
}
function trackPaint(animate=false,direction=1){
 const row=trackingRow(),k=row?TM.key(row):'',changed=k!==tracking.painted;
 // A refresh never restarts an in-progress transition. Its latest data is painted at completion.
 if(tracking.busy&&!animate)return;
 const version=++tracking.serial;
 trackSheet.getAnimations?.().forEach(a=>a.cancel());tracking.busy=false;
 const paint=()=>{
  const current=trackingRow();const notes=trackSheet.querySelector('.kt-track-notes');
  const scroll=changed?0:notes?.scrollTop||0;
  const focused=notes&&document.activeElement===notes,html=trackHTML(current);
  if(trackSheet.innerHTML!==html){trackSheet.innerHTML=html;trackSheet.querySelector('.kt-track-notes')?.scrollTo(0,scroll);if(focused)trackSheet.querySelector('.kt-track-notes')?.focus({preventScroll:true});}
  tracking.painted=current?TM.key(current):'';state.stock=current?.code;
  const index=tracking.queue.indexOf(tracking.current);
  $('[data-track-count]').textContent=tracking.temporary?`临时 / ${tracking.queue.length}`:`${index<0?0:index+1} / ${tracking.queue.length}`;
  $('[data-track-notice]').textContent=tracking.notice|| (tracking.temporary?'临时查看 · 下一只返回核心池':tracking.recent?`${tracking.queue.length}只去重核心 · 手动切换`:'正在连接近期核心池');
  const disabled=tracking.busy||(!tracking.temporary&&tracking.queue.length<2)||!tracking.queue.length;
  $('[data-track-prev]').disabled=disabled;$('[data-track-next]').disabled=disabled;
  trackStage.setAttribute('aria-busy',String(tracking.busy));
 };
 const finish=()=>{if(version!==tracking.serial)return;tracking.busy=false;paint();renderCore();renderLadder();renderTape();remember();};
 if(!animate||!changed||!tracking.painted||matchMedia('(prefers-reduced-motion: reduce)').matches||!trackSheet.animate){paint();finish();return;}
 tracking.busy=true;trackStage.setAttribute('aria-busy','true');$('[data-track-prev]').disabled=true;$('[data-track-next]').disabled=true;
 trackSheet.animate([{transform:'translateX(0) rotate(0)',opacity:1},{transform:`translateX(${-direction*90}%) rotate(${-direction*9}deg)`,opacity:0}],{duration:145,easing:'ease-in',fill:'none'}).finished.then(()=>{
  if(version!==tracking.serial)return;paint();
  return trackSheet.animate([{transform:`translateX(${direction*85}%) rotate(${direction*9}deg)`,opacity:0},{transform:'translateX(0) rotate(0)',opacity:1}],{duration:175,easing:'cubic-bezier(.2,.75,.3,1)',fill:'none'}).finished;
 }).then(finish).catch(()=>{if(version===tracking.serial)finish();});
}
function renderDetail(){syncTracking();trackPaint();}
trackHost.addEventListener('click',event=>{
 if(event.target.closest('[data-track-prev]'))return trackNext(-1);
 if(event.target.closest('[data-track-next]'))return trackNext(1);
 if(!event.target.closest('[data-track-stage]')||event.target.closest('button,a,input,select,textarea,.kt-track-notes')||getSelection()?.toString())return;
 trackNext(1);
});
trackStage.addEventListener('keydown',event=>{
 if(event.target!==trackStage)return;
 if(['ArrowRight','ArrowLeft','Enter',' '].includes(event.key)){event.preventDefault();trackNext(event.key==='ArrowLeft'?-1:1);}
});
root.addEventListener('click',event=>{const button=event.target.closest('[data-track-open]');if(button){trackSelect(button.dataset.trackOpen);trackHost.scrollIntoView({block:'center',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});}});
root.addEventListener('kpl-recent-data',event=>{tracking.recent=event.detail;if(tracking.notice.startsWith('近期核心本轮'))tracking.notice='';renderDetail();});
root.addEventListener('kpl-recent-error',()=>{tracking.notice='近期核心本轮未更新，保留已有名单与报价';renderDetail();});
