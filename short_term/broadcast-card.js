(()=>{
const root=document.querySelector('#kpl-today'),$=s=>root.querySelector(s);if(!$('[data-bc-text]'))return;
const localGet=(k,f)=>{try{return JSON.parse(localStorage.getItem(k))??f;}catch{return f;}},localSet=(k,v)=>{try{localStorage.setItem(k,JSON.stringify(v));}catch{}};
const today=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
let day=today(),rows=[],selected=null,baseline=false,seen=new Set(),follow=true,unread=0,queue=[],stream=null,pollTimer=null,loading=false,lastRevision=-1,instance='';
const toast=document.createElement('aside');toast.className='bc-toast';toast.hidden=true;toast.setAttribute('aria-label','开盘啦新直播');toast.innerHTML='<header><strong data-toast-title></strong><button data-toast-close aria-label="关闭本条提醒">关闭</button></header><div data-toast-text tabindex="0"></div><footer><button data-toast-open>查看直播</button><span data-toast-count></span></footer>';root.append(toast);
$('[data-bc-day]').value=day;$('[data-bc-popup]').checked=localGet('kpl-live-popup',true);
const body=$('[data-bc-text]');let rendered='';
function positionLabel(){
 $('[data-bc-unread]').textContent=unread?'新增 '+unread+' 条':'';
 $('[data-bc-position]').textContent=follow?'跟随最新 · 向下滚动查看历史':'正在浏览历史 · 新消息不打断阅读';
}
function anchor(){
 const top=body.getBoundingClientRect().top;
 const item=[...body.querySelectorAll('.bc-item')].find(n=>n.getBoundingClientRect().bottom>top+1);
 return item?{id:item.dataset.message,offset:item.getBoundingClientRect().top-top}:null;
}
function draw(){
 const first=rows.at(-1),last=rows[0];
 $('[data-bc-time]').textContent=last?day+' · '+first.published_at.slice(11,19)+' — '+last.published_at.slice(11,19):day+' · 暂无已获取直播';
 const key=JSON.stringify(rows.map(r=>[r.id,r.text,r.published_at,r.version]));
 if(key!==rendered){
  const held=follow?null:anchor(),position=body.scrollTop;
  const existing=new Map([...body.querySelectorAll('.bc-item')].map(n=>[n.dataset.message,n]));
  const fragment=document.createDocumentFragment();
  for(const r of rows){
   let item=existing.get(r.id);
   if(!item){item=document.createElement('article');item.className='bc-item';item.dataset.message=r.id;item.append(document.createElement('header'),document.createElement('p'));}
   item.firstChild.textContent=r.published_at.slice(11,19)+(r.version>1?' · 已更新':'')+' · 开盘啦';
   if(item.lastChild.textContent!==r.text)item.lastChild.textContent=r.text;
   item.classList.toggle('bc-focused',r.id===selected);fragment.append(item);
  }
  if(!rows.length){const empty=document.createElement('p');empty.textContent='当前没有已获取的直播文字；等待查询或选择其他日期。';fragment.append(empty);}
  body.replaceChildren(fragment);rendered=key;
  if(follow)body.scrollTop=0;
  else{
   const kept=[...body.querySelectorAll('.bc-item')].find(n=>n.dataset.message===held?.id);
   body.scrollTop=kept?body.scrollTop+kept.getBoundingClientRect().top-body.getBoundingClientRect().top-held.offset:position;
  }
 }
 positionLabel();
}
body.addEventListener('scroll',()=>{
 follow=body.scrollTop<=12;
 if(follow)unread=0;
 positionLabel();
},{passive:true});
function focusMessage(row){
 const targetDay=row.published_at.slice(0,10);
 if(targetDay!==day){pendingFocus=row.id;changeDay(targetDay,true);return;}
 const item=[...body.querySelectorAll('.bc-item')].find(n=>n.dataset.message===row.id);
 if(!item)return;
 selected=row.id;follow=false;
 body.querySelectorAll('.bc-item').forEach(n=>n.classList.toggle('bc-focused',n===item));
 body.scrollTop+=item.getBoundingClientRect().top-body.getBoundingClientRect().top-6;
 body.scrollIntoView({block:'center'});positionLabel();
}
let pendingFocus=null;
function changeDay(next,manual=false){
 day=next;$('[data-bc-day]').value=day;$('[data-bc-day]').dataset.manual=manual&&day!==today()?'yes':'no';
 baseline=false;lastRevision=-1;seen=new Set();selected=null;rows=[];queue=[];showToast();follow=true;unread=0;
 draw();$('[data-bc-status]').textContent='正在查询所选日期';connect();
}
const newestFirst=(a,b)=>b.published_at.localeCompare(a.published_at)||b.id.localeCompare(a.id,undefined,{numeric:true});
function showToast(){
 // Every batch and late delivery shares the same publication-time order as the timeline.
 queue.sort(newestFirst);
 const r=queue[0];toast.hidden=!r;
 if(!r){toast.dataset.message='';return;}
 const changed=toast.dataset.message!==r.id;
 toast.dataset.message=r.id;
 toast.querySelector('[data-toast-title]').textContent='开盘啦 '+r.published_at.slice(11,19)+(Date.parse(r.received_at)-Date.parse(r.published_at)>120000?' · 补到':'');
 const text=toast.querySelector('[data-toast-text]');text.textContent=r.text;
 if(changed)text.scrollTop=0;
 toast.querySelector('[data-toast-count]').textContent=queue.length+' 条待查看 · 最新优先';
}
async function notify(row){
 const notifyDay=row.published_at.slice(0,10),key=notifyDay+':'+row.id;
 async function claim(){
  if(day!==notifyDay)return;
  const sent=localGet('kpl-live-notified',[]);if(sent.includes(key))return;
  localSet('kpl-live-notified',[...sent.filter(x=>x.startsWith(day+':')),key].slice(-1000));
  if($('[data-bc-popup]').checked){queue.push(row);showToast();}
  if(localGet('kpl-live-desktop',false)&&'Notification'in window&&Notification.permission==='granted'){
   try{const n=new Notification('开盘啦 · '+row.published_at.slice(11,19),{body:row.text,tag:'kpl-'+key});n.onclick=()=>{window.focus();focusMessage(row);n.close();};}catch{$('[data-bc-permission]').textContent='系统通知不可用，仍可使用页面弹窗';}
  }
 }
 if(navigator.locks)await navigator.locks.request('kpl-live-reminder',claim);else await claim();
}
function receive(data){
 if(data.day!==day)return;
 if(data.instance&&data.instance!==instance){if(instance&&data.instance<instance)return;instance=data.instance;lastRevision=-1;}
 if(data.revision<lastRevision)return;lastRevision=data.revision;
 const incoming=[...new Map((data.rows||[]).map(r=>[r.id,r])).values()].sort(newestFirst),fresh=baseline?incoming.filter(r=>!seen.has(r.id)):[];
 incoming.forEach(r=>seen.add(r.id));rows=incoming;
 if(follow)unread=0;else unread+=fresh.length;
 // Cached rows while the first upstream query is pending are not a completed alert baseline.
 baseline=baseline||['ready','partial','failed'].includes(data.status?.state);draw();
 if(pendingFocus){const target=rows.find(r=>r.id===pendingFocus);if(target){pendingFocus=null;focusMessage(target);}}
 const s=data.status||{};$('[data-bc-status]').textContent=s.message||'等待查询';
 $('[data-bc-check]').textContent=s.checked_at?'检查 '+s.checked_at.slice(11,19):'';
 $('[data-bc-note]').textContent='已获取 '+rows.length+' 条 · '+(s.history_complete?'当日分页已取完':'历史覆盖待核实')+' · 每分钟查询'+(s.elapsed_ms!=null?' · 本次 '+s.elapsed_ms+'ms':'')+' · 开盘啦原文';
 if(day===today())fresh.forEach(notify);
}
async function load(){if(loading)return;loading=true;try{const r=await fetch('./broadcast?day='+day,{cache:'no-store'});if(!r.ok)throw Error();receive(await r.json());}catch{$('[data-bc-status]').textContent='连接中断，保留原文';}finally{loading=false;}}
function connect(){
 stream?.close();clearInterval(pollTimer);load();
 if('EventSource'in window){stream=new EventSource('./broadcast/events?day='+day);stream.addEventListener('broadcast',e=>{try{receive(JSON.parse(e.data));}catch{}});stream.onopen=()=>{clearInterval(pollTimer);pollTimer=null;};stream.onerror=()=>{if(!pollTimer)pollTimer=setInterval(load,5000);};}
 else pollTimer=setInterval(load,2000);
}
$('[data-bc-latest]').onclick=()=>{follow=true;unread=0;selected=null;body.querySelectorAll('.bc-focused').forEach(n=>n.classList.remove('bc-focused'));body.scrollTop=0;positionLabel();};
$('[data-bc-day]').onchange=e=>{pendingFocus=null;changeDay(e.target.value||today(),true);};
$('[data-bc-popup]').onchange=e=>{localSet('kpl-live-popup',e.target.checked);if(!e.target.checked){queue=[];showToast();}};
function permissionUI(){const enabled=localGet('kpl-live-desktop',false)&&'Notification'in window&&Notification.permission==='granted';$('[data-bc-notify]').textContent=enabled?'关闭桌面提醒':'开启桌面提醒';}
$('[data-bc-notify]').onclick=async()=>{
 if(localGet('kpl-live-desktop',false)){localSet('kpl-live-desktop',false);permissionUI();return;}
 if(!('Notification'in window)||!window.isSecureContext){$('[data-bc-permission]').textContent='此浏览器暂不支持桌面通知，可使用页面弹窗';return;}
 if(Notification.permission==='denied'){$('[data-bc-permission]').textContent='通知权限已拒绝，可在浏览器设置中调整；页面弹窗仍可用';return;}
 try{const permission=Notification.permission==='granted'?'granted':await Notification.requestPermission();localSet('kpl-live-desktop',permission==='granted');$('[data-bc-permission]').textContent=permission==='granted'?'桌面提醒已开启；长文点击阅读全文':'未开启桌面提醒，页面弹窗仍可用';}catch{$('[data-bc-permission]').textContent='浏览器未能开启桌面通知';}permissionUI();
};
toast.querySelector('[data-toast-close]').onclick=()=>{queue.shift();showToast();};
toast.querySelector('[data-toast-open]').onclick=()=>{const r=queue.shift();if(r){focusMessage(r);}showToast();};
document.addEventListener('visibilitychange',()=>{if(!document.hidden)load();});
window.addEventListener('pagehide',()=>{stream?.close();clearInterval(pollTimer);});
setInterval(()=>{const now=today();if(day!==now&&$('[data-bc-day]').dataset.manual!=='yes'){pendingFocus=null;changeDay(now);}},30000);
permissionUI();connect();
})();
