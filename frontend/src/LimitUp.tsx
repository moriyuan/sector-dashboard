import {useEffect,useState} from 'react';
import {Button,Empty,Select,Spin,Tag,Tooltip} from 'antd';
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

function StockRow({s}:{s:Stock}){
 const reason=s.reason_full||s.reason;
 return <li className="lu-stock">
  <span className={`lu-boards ${(s.boards??1)>1?'multi':''}`}>{boardsLabel(s)}</span>
  <span className="lu-name">{s.name}<small>{s.code}</small></span>
  <span className="lu-time">{s.first_time?.slice(0,5)||'—'}</span>
  <span className={s.change!=null&&s.change>=0?'up':'down'}>{pctText(s.change)}</span>
  <span className="lu-seal">{sealText(s.seal_fund)}</span>
  <span className="lu-reason">
   {reason?<Tooltip title={s.reason_full&&s.reason&&s.reason_full!==s.reason?s.reason:null} placement="topLeft">{reason}</Tooltip>:<i>待补齐</i>}
   {s.tags.length>0&&<span className="lu-tags">{s.tags.map(t=><em key={t}>{t}</em>)}</span>}
  </span>
 </li>;
}

export default function LimitUp(){
 const [dates,setDates]=useState<string[]>([]);
 const [date,setDate]=useState<string>();
 const [data,setData]=useState<Daily|null>(null);
 const [error,setError]=useState<string>();
 const [loading,setLoading]=useState(false);
 const [collecting,setCollecting]=useState(false);

 useEffect(()=>{window.scrollTo({top:0});
  fetch('api/limitup/dates').then(r=>r.json()).then(d=>{
   const list:string[]=d.dates||[];setDates(list);setDate(v=>v||list[0]);
  }).catch(()=>setError('读取日期列表失败'));
 },[]);

 useEffect(()=>{if(!date)return;setLoading(true);setError(undefined);
  fetch('api/limitup/daily?'+new URLSearchParams({date})).then(async r=>{
   if(!r.ok){const d=await r.json().catch(()=>({}));throw Error(typeof d.detail==='string'?d.detail:'读取失败');}
   return r.json();
  }).then(d=>setData(d)).catch(e=>{setData(null);setError(e.message);}).finally(()=>setLoading(false));
 },[date]);

 const recollect=()=>{if(!date)return;setCollecting(true);setError(undefined);
  fetch('api/limitup/collect?'+new URLSearchParams({date}),{method:'POST'}).then(async r=>{
   if(!r.ok){const d=await r.json().catch(()=>({}));throw Error(typeof d.detail==='string'?d.detail:'采集失败');}
   return r.json();
  }).then(d=>{setData(d);if(!dates.includes(d.date))setDates(v=>[d.date,...v]);})
   .catch(e=>setError(e.message)).finally(()=>setCollecting(false));};

 const stats=data?.stats||{};
 return <main className="lu-page">
  <header className="lu-head">
   <div><h1>涨停汇总</h1><p className="muted">题材按涨停数降序 · 题材内按连板数降序、同身位按首封时间从早到晚</p></div>
   <div className="lu-controls">
    <Select value={date} onChange={setDate} style={{minWidth:150}} placeholder="选择交易日"
     options={dates.map(d=>({value:d,label:d}))} notFoundContent="暂无已采集日期"/>
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
    {data.themes.map(t=><article className="lu-theme" key={t.theme_code}>
     <header>
      <div className="lu-theme-title">
       <h2>{t.name}</h2>
       {t.strength!=null&&<span className={`lu-strength ${strengthClass(t.strength)}`} title="开盘啦题材强度">
        强度 {Math.round(t.strength)}{t.strength_rank!=null&&<i>#{t.strength_rank}</i>}</span>}
       <span className="lu-count">{t.stock_count} 只涨停</span>
       {t.change!=null&&<span className={t.change>=0?'up':'down'}>{pctText(t.change)}</span>}
      </div>
      {t.logic&&<p className="lu-logic">{t.logic}</p>}
     </header>
     <ul>
      <li className="lu-stock lu-stock-head">
       <span>身位</span><span>股票</span><span>首封</span><span>涨幅</span><span>封单</span><span>涨停原因</span>
      </li>
      {t.stocks.map(s=><StockRow key={s.code} s={s}/>)}
     </ul>
    </article>)}
   </section>
   <footer className="lu-foot">股票全集以东方财富涨停池为基准；题材分组以开盘啦复盘为准，未分组按同花顺原因首标签归并；原因全文开盘啦优先、韭研公社补充；历史日强度为逐板块回补。</footer>
  </>:!error&&<Empty description="暂无数据，请选择交易日或点击重新采集"/>}
 </main>;
}
