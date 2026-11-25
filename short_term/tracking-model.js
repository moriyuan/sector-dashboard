/* Shared table/slider membership and quote provenance; also executable in Node tests. */
(function(scope){
'use strict';
const valid=v=>typeof v==='number'&&Number.isFinite(v);
const market=r=>r.market||(/^(60|68)/.test(r.code)?'SH':/^(00|30)/.test(r.code)?'SZ':'BJ');
const key=r=>market(r)+':'+r.code;
function front(view,theme='全市场'){
 const stocks=view.stocks||[],pool=new Map();
 stocks.filter(s=>theme==='全市场'||s.theme===theme).slice()
  .sort((a,b)=>(b.boards||0)-(a.boards||0)||(a.limit_timestamp||0)-(b.limit_timestamp||0)||a.code.localeCompare(b.code))
  .slice(0,8).forEach(s=>pool.set(key(s),{...s}));
 for(const s of view.tenDay?.rows||[]){
  const limit=stocks.find(r=>key(r)===key(s));
  if(theme!=='全市场'&&limit?.theme!==theme&&!String(s.theme||'').split('、').includes(theme))continue;
  pool.set(key(s),{...(pool.get(key(s))||limit||s),return_10d:s.return_10d,tenRank:s.rank});
 }
 return [...pool.values()];
}
function union(view,recent){
 const pool=new Map();
 for(const r of front(view))pool.set(key(r),{...r,sources:[{kind:'intraday',label:'日内前排',day:view.date}]});
 for(const r of recent?.rows||[]){
  const old=pool.get(key(r));
  pool.set(key(r),{...old,...r,sources:[...(old?.sources||[]),...(r.sources||[])]});
 }
 return [...pool.values()];
}
function reconcile(previous,rows){
 const keys=new Set(rows.map(key));
 return [...previous.filter(k=>keys.has(k)),...rows.map(key).filter(k=>!previous.includes(k))];
}
// Each field carries its own supplier date/time; collection time never becomes quote time.
function quote(view,recent,row){
 const code=row.code,k=key(row),candidates=[];
 function add(q,day,label){
  if(!q||q.code&&q.code!==code)return;
  const d=q.quote_day||q.source_time?.slice(0,10)||day;
  if(!d)return;
  candidates.push({q,day:d,label,stamp:q.source_time||null});
 }
 const stock=(view.stocks||[]).find(r=>key(r)===k);
 const ten=view.tenDay?.rows?.find(r=>key(r)===k);
 const core=recent?.rows?.find(r=>key(r)===k);
 add(stock?.quote,view.date,'涨停池');
 add(ten?.quote,view.tenDay?.end,'区间榜盘口');
 add(core?.quote,core?.quote?.quote_day,'核心盘口');
 add(recent?.tracking_quotes?.[code],null,'全池盘口');
 // Date is primary. Within a date prefer independent supplier timestamps.
 candidates.sort((a,b)=>b.day.localeCompare(a.day)||String(b.stamp||'').localeCompare(String(a.stamp||'')));
 const fields={};
 for(const field of ['price','change','amount','turnover','high','low','reason']){
  const options=[];
  for(const c of candidates){
   const value=c.q[field];
   if(field==='reason'?!value:!valid(value))continue;
   const detail=['turnover','high','low','reason'].includes(field)&&c.q.detail_source_time;
   options.push({value,day:detail?detail.slice(0,10):c.day,stamp:detail||c.stamp,label:c.label});
  }
  options.sort((a,b)=>b.day.localeCompare(a.day)||String(b.stamp||'').localeCompare(String(a.stamp||'')));
  if(options.length)fields[field]=options[0];
 }
 return fields;
}
const api={key,market,front,union,reconcile,quote,valid};
if(typeof module!=='undefined'&&module.exports)module.exports=api;
else scope.KplTracking=api;
})(globalThis);
