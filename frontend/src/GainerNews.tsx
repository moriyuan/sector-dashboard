import {Button} from 'antd';
import {MoveReasons,type Profile} from './MoveEvidence';
import {ArchivePicker} from './DeskUI';
import {useEffect,useState} from 'react';
import type {OpenStock} from './Lhb';

type Hit={id:string;title:string;url:string;source:string;published_at:string;timing:string;basis:string;excerpt:string;market_report:boolean};
type Board={code:string;name:string};
type Row={rank:number;code:string;name:string;change:number;boards:number|null;industry:Board[];concepts:Board[];
  attribution:Profile;scan:{ok:number;hits:number|null;checked_at:string;error:string|null}|null;
  direct:Hit[];sector:Hit[];direct_count:number;sector_count:number};
type Result={trade_date:string;revision:number|null;source_time:string|null;start:string;asof:string;dates:string[];rows:Row[];top:number};
export type Opening={id:string;asof:string;day:string;origin?:'lhb'};
const time=(v:string)=>new Date(v).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});

function Reports({title,hits,total,onEvent}:{title:string;hits:Hit[];total:number;onEvent:(hit:Hit)=>void}) {
 return <section className="rank-hits"><h3>{title} <small>{total}</small></h3>
 {hits.length?hits.map(h=><article key={h.id}>
   <div className="rank-hit-meta">{h.source} · {time(h.published_at)} · <b>{h.timing}</b>{h.market_report?' · 含行情描述':''}</div>
   <button className="event-title" onClick={()=>onEvent(h)}>{h.title}</button>
   {h.excerpt&&h.excerpt.trim()!==h.title.trim()&&<p className="rank-excerpt">{h.excerpt}</p>}
   <div className="rank-hit-foot"><span>{h.basis}</span><a href={h.url} target="_blank" rel="noopener noreferrer">原文 ↗</a></div>
 </article>):<p className="footnote">此窗口已收录的报道中暂无匹配。</p>}
 {total>hits.length&&<p className="footnote">展示最近 {hits.length} 条，共 {total} 条。</p>}</section>;
}

export default function GainerNews({day,setDay,code,setCode,onOpen,onStock}:{onStock:OpenStock;day:string;setDay:(v:string)=>void;code:string;
 setCode:(v:string)=>void;onOpen:(v:Opening)=>void}) {
 const [data,setData]=useState<Result|null>(null),[error,setError]=useState(''),[refresh,setRefresh]=useState(0);
 useEffect(()=>{const t=setInterval(()=>setRefresh(n=>n+1),60000);return()=>clearInterval(t);},[]);
 useEffect(()=>{const c=new AbortController();setError('');
   fetch('api/news/gainers'+(day?'?'+new URLSearchParams({date:day}):''),{signal:c.signal})
   .then(r=>{if(!r.ok)throw Error();return r.json();}).then(v=>{if(!c.signal.aborted)setData(v);})
   .catch(()=>{if(!c.signal.aborted)setError('涨幅榜新闻暂不可用。');});return()=>c.abort();
 },[day,refresh]);
 const row=data?.rows.find(r=>r.code===code)||data?.rows[0];
 const openStock=(r:Row)=>{if(data)onStock({code:r.code,name:r.name,date:data.trade_date,
   asof:data.trade_date<new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'})?data.asof:undefined});};
 const open=(hit:Hit)=>{if(data)onOpen({id:hit.id,asof:data.asof,day:data.trade_date});};
 return <><header className="news-work-head"><div><h2>涨幅榜找新闻</h2><p>从个股上涨查到具体事件，再找有同一催化依据的股票。</p></div>
 <div className="market-clock"><strong>{data?.trade_date||day||'今日'}</strong><span>行情日期 · 北京时间</span></div></header>
 <div className="rank-toolbar"><div className="rank-date"><span>复盘日期</span><ArchivePicker label="涨幅榜新闻日期" emptyText={data?'暂无归档':error?'日期未能读取':'读取日期…'} value={day||data?.trade_date||''} dates={[...(day?[day]:[]),...(data?.dates||[])]}
   onChange={next=>{if(next!==day){setData(null);setDay(next);setCode('');}}} shortcut={{label:'回到今日',value:''}}/></div>
 <Button onClick={()=>setRefresh(n=>n+1)}>刷新</Button><span>沪深收盘涨幅前 20 · 排除 ST · 仅上涨股</span></div>
 {error&&<p className="notice">{error}</p>}
 {data&&<p className="footnote">报道窗口 {time(data.start)} 至 {time(data.asof)}。{data.revision?'收盘榜修订 '+data.revision+'。':''}同行业或概念词命中仅供查找线索，涨幅原因需要另外核实。</p>}
 {!data&&!error&&<p className="empty">正在读取榜单和报道…</p>}
 {data&&!data.rows.length&&<section className="panel empty"><h3>{data.revision?'榜内无符合条件的上涨股':'该日收盘涨幅榜尚未归档'}</h3><p>可在复盘日期中选择已归档的交易日。</p></section>}
 {data&&row&&<div className="rank-layout"><section className="panel rank-list"><div className="news-feed-heading"><h2>领涨个股</h2><span>{data.rows.length} 只 · 点击查看龙虎榜与报道</span></div>
 {data.rows.map(r=><button key={r.code} aria-pressed={r.code===row.code} className="rank-stock" onClick={()=>{setCode(r.code);openStock(r);}}>
 <span className="rank-num">{r.rank}</span><span><strong>{r.name}</strong><small>{r.code} · 公司 {r.direct_count} / 板块 {r.sector_count}</small></span><b className="up">+{r.change.toFixed(2)}%</b></button>)}</section>
 <section className="panel rank-detail"><p className="rank-context">收盘涨幅第 {row.rank} 名</p><h2>{row.name} <small>{row.code}</small></h2>
 <p><b className="up">+{row.change.toFixed(2)}%</b>　{row.boards?row.boards===1?'首板':'第 '+row.boards+' 板':'连板高度未确认'}　{row.industry.map(b=>b.name).join('、')||'行业归属未存档'}</p>
 <p className="footnote">{row.concepts.length?'已核验概念：'+row.concepts.map(b=>b.name).join('、'):'该快照没有已核验的概念归属。'}</p>
 <MoveReasons profile={row.attribution} open={id=>onOpen({id,asof:data.asof,day:data.trade_date})}/>
 <button className="lhb-rank-button" onClick={()=>openStock(row)}>查看相关股票（{row.attribution?.total??0}）与龙虎榜 ↗</button>
 <p className="rank-scan">新浪个股资讯 · {row.scan?(row.scan.ok?'已检索，找到 '+row.scan.hits+' 条公司标题':'本次检索未成功（'+row.scan.error+'）')+' · '+time(row.scan.checked_at):'暂无该日定向检索记录'}。同时检索已归档的国内外快讯。</p>
 <Reports title="直接提及公司" hits={row.direct} total={row.direct_count} onEvent={open}/>
 <Reports title="板块新闻（不用于归因）" hits={row.sector} total={row.sector_count} onEvent={open}/></section></div>}</>;
}
