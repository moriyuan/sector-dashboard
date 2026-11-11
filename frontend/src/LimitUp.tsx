import {useEffect,useMemo,useState} from 'react';
import {Button,Empty,Select,Spin,Tag} from 'antd';
import './limitup.css';

type Stock={code:string;name:string;boards:number|null;board_label:string|null;first_time:string|null;
 last_time:string|null;open_num:number|null;seal_fund:number|null;change:number|null;
 reason:string|null;reason_full:string|null;tags:string[];sources:string[]};
type Theme={theme_code:string;name:string;stock_count:number;strength:number|null;strength_rank:number|null;
 change:number|null;logic:string|null;stocks:Stock[]};
type Daily={date:string;stats:Record<string,number|string>;sources:Record<string,string>;
 collected_at:string;themes:Theme[]};

const SRC_NAMES:Record<string,string>={eastmoney:'东方财富',ths:'同花顺',kaipanla:'开盘啦',kpl_strength:'开盘啦强度',jiuyangongshe:'韭研公社'};
const boardsLabel=(s:Stock)=>s.board_label||(s.boards!=null?(s.boards>1?`${s.boards}连板`:'首板'):'—');
const sealText=(v:number|null)=>v==null?'—':Math.abs(v)>=1e8?(v/1e8).toFixed(1)+'亿':(v/1e4).toFixed(0)+'万';
const pctText=(v:number|null)=>v==null?'—':`${v>0?'+':''}${v.toFixed(2)}%`;
const strengthClass=(v:number|null)=>v==null?'':v>=5000?'hot':v>=2000?'warm':'cool';

function StatItem({label,value,title}:{label:string;value:React.ReactNode;title?:string}){
 return <div className="lu-stat" title={title}><span>{label}</span><b>{value}</b></div>;
}

function StockRow({s,open,onToggle}:{s:Stock;open:boolean;onToggle:()=>void}){
 const reason=s.reason_full||s.reason;
 return <li className={`lu-stock ${open?'open':''}`}>
  <button className="lu-stock-row" onClick={onToggle} aria-expanded={open}>
   <span className={`lu-boards ${(s.boards??1)>1?'multi':''}`}>{boardsLabel(s)}</span>
   <span className="lu-name">{s.name}<small>{s.code}</small></span>
   <span className="lu-time">{s.first_time?.slice(0,5)||'—'}</span>
   <span className={s.change!=null&&s.change>=0?'up':'down'}>{pctText(s.change)}</span>
   <span className="lu-seal">{sealText(s.seal_fund)}</span>
   <span className={`lu-caret ${open?'up':''}`} aria-hidden="true"/>
  </button>
  {open&&<div className="lu-stock-reason">
   {reason?<p>{reason}</p>:<p className="muted">涨停原因待补齐</p>}
   {s.tags.length>0&&<span className="lu-tags">{s.tags.map(t=><em key={t}>{t}</em>)}</span>}
   <span className="lu-srcs">{s.sources.map(x=>SRC_NAMES[x]||x).join(' · ')}</span>
  </div>}
 </li>;
}

function ThemeRow({t,rank,open,onToggle}:{t:Theme;rank:number;open:boolean;onToggle:()=>void}){
 const [openReasons,setOpenReasons]=useState<Set<string>>(new Set());
 const toggleReason=(code:string)=>setOpenReasons(v=>{
  const n=new Set(v);n.has(code)?n.delete(code):n.add(code);return n;});
 return <article className={`lu-theme ${open?'open':''}`}>
  <button className="lu-theme-row" onClick={onToggle} aria-expanded={open}>
   <span className="lu-rank">{rank}</span>
   <span className="lu-theme-name">{t.name}<b className="lu-count">{t.stock_count}</b></span>
   {t.strength!=null
    ?<span className={`lu-strength ${strengthClass(t.strength)}`} title="开盘啦题材强度">
      强度 {Math.round(t.strength)}{t.strength_rank!=null&&<i>#{t.strength_rank}</i>}</span>
    :<span className="lu-strength none">强度 —</span>}
   {t.change!=null&&<span className={`lu-change ${t.change>=0?'up':'down'}`}>{pctText(t.change)}</span>}
   <span className="lu-expand">{open?'收起':'展开板块'}<i className={`lu-caret ${open?'up':''}`}/></span>
  </button>
  {open&&<div className="lu-theme-body">
   {t.logic&&<p className="lu-logic">{t.logic}</p>}
   <div className="lu-stock-head">
    <span>身位</span><span>股票</span><span>首封</span><span>涨幅</span><span>封单</span><span></span>
   </div>
   <ul>{t.stocks.map(s=><StockRow key={s.code} s={s} open={openReasons.has(s.code)}
     onToggle={()=>toggleReason(s.code)}/>)}</ul>
  </div>}
 </article>;
}

export default function LimitUp(){
 const [dates,setDates]=useState<string[]>([]);
 const [date,setDate]=useState<string>();
 const [data,setData]=useState<Daily|null>(null);
 const [error,setError]=useState<string>();
 const [loading,setLoading]=useState(false);
 const [collecting,setCollecting]=useState(false);
 const [openThemes,setOpenThemes]=useState<Set<string>>(new Set());

 useEffect(()=>{window.scrollTo({top:0});
  fetch('api/limitup/dates').then(r=>r.json()).then(d=>{
   const list:string[]=d.dates||[];setDates(list);setDate(v=>v||list[0]);
  }).catch(()=>setError('读取日期列表失败'));
 },[]);

 useEffect(()=>{if(!date)return;setLoading(true);setError(undefined);setOpenThemes(new Set());
  fetch('api/limitup/daily?'+new URLSearchParams({date})).then(async r=>{
   if(!r.ok){const d=await r.json().catch(()=>({}));throw Error(typeof d.detail==='string'?d.detail:'读取失败');}
   return r.json();
  }).then(d=>setData(d)).catch(e=>{setData(null);setError(e.message);}).finally(()=>setLoading(false));
 },[date]);

 const recollect=()=>{if(!date)return;setCollecting(true);setError(undefined);
  fetch('api/limitup/collect?'+new URLSearchParams({date}),{method:'POST'}).then(async r=>{
   if(!r.ok){const d=await r.json().catch(()=>({}));throw Error(typeof d.detail==='string'?d.detail:'采集失败');}
   return r.json();
  }).then(d=>{setData(d);setOpenThemes(new Set());if(!dates.includes(d.date))setDates(v=>[d.date,...v]);})
   .catch(e=>setError(e.message)).finally(()=>setCollecting(false));};

 const toggleTheme=(code:string)=>setOpenThemes(v=>{
  const n=new Set(v);n.has(code)?n.delete(code):n.add(code);return n;});
 const allCodes=useMemo(()=>new Set((data?.themes||[]).map(t=>t.theme_code)),[data]);
 const allOpen=allCodes.size>0&&openThemes.size>=allCodes.size;

 const stats=data?.stats||{};
 return <main className="lu-page">
  <header className="lu-head">
   <div><h1>涨停汇总</h1><p className="muted">题材按开盘啦强度排名 · 组内按连板高度、同身位按首封时间</p></div>
   <div className="lu-controls">
    <Select value={date} onChange={setDate} style={{minWidth:150}} placeholder="选择交易日"
     options={dates.map(d=>({value:d,label:d}))} notFoundContent="暂无已采集日期"/>
    <Button onClick={()=>setOpenThemes(allOpen?new Set():new Set(allCodes))} disabled={!data}>
     {allOpen?'全部收起':'全部展开'}</Button>
    <Button onClick={recollect} loading={collecting} disabled={!date}>重新采集</Button>
   </div>
  </header>
  {error&&<p className="lu-error">{error}</p>}
  {loading?<div className="lu-loading"><Spin/></div>:data?<>
   <section className="lu-stats">
    <StatItem label="涨停" value={stats.limit_up??data.themes.reduce((n,t)=>n+t.stock_count,0)}/>
    <StatItem label="跌停" value={stats.limit_down??'—'}/>
    <StatItem label="炸板率" value={stats.broken_ratio??'—'}/>
    <StatItem label="上涨家数" value={stats.up??'—'}/>
    <StatItem label="下跌家数" value={stats.down??'—'}/>
    <StatItem label="采集时间" value={(data.collected_at||'').slice(5,16).replace('T',' ')}/>
   </section>
   <p className="lu-sources">渠道：{Object.entries(data.sources).map(([k,v])=>
    <Tag key={k} color={String(v).startsWith('ok')?'green':String(v).startsWith('skip')?'default':'red'}>
     {SRC_NAMES[k]||k} {String(v).replace(/^(ok|fail|skip):/,'')}</Tag>)}</p>
   <section className="lu-themes">
    {data.themes.map((t,i)=><ThemeRow key={t.theme_code} t={t} rank={i+1}
     open={openThemes.has(t.theme_code)} onToggle={()=>toggleTheme(t.theme_code)}/>)}
   </section>
   <footer className="lu-foot">题材排序取开盘啦强度榜（无强度题材按涨停数排后）；点击题材展开个股，点击个股行展开涨停原因。原因全文开盘啦优先、韭研公社补充、同花顺标签兜底。</footer>
  </>:!error&&<Empty description="暂无数据，请选择交易日或点击重新采集"/>}
 </main>;
}
