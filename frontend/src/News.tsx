import {Badge,Button,DatePicker,Input,Segmented,Select} from 'antd';
import dayjs from 'dayjs';
import React, {useEffect, useRef, useState} from 'react';
import './news.css';
import {Cover,EndAction,Pager,SourceStrip} from './ResearchLayout';
import type {OpenStock} from './Lhb';
import GainerNews, {type Opening} from './GainerNews';
import NewsResearch, {type Research, type ResearchContext} from './NewsResearch';
type Board={code:string;name:string};
type Stock={code:string;name:string;change:number|null;amount:number|null;boards:number|null;industry:Board[];concepts:Board[]};
type Link={code:string;type:string;reason:string;evidence_ids:string[]};
type Event=ResearchContext & {id:string;title:string;summary:string;published_at:string;first_seen:string;status:string;themes?:string[];stocks?:Stock[];allowed_links?:Link[];analysis?:{impact:string;chain:string[];links:Link[];research?:Research};reports:{source:string;url:string;title:string;summary:string;published_at:string;first_seen:string}[];evidence?:Record<string,{kind:string;text:string;url:string;date?:string;verified_at?:string}>;leaders?:Record<string,{boards:Stock[];gainers:Stock[];turnover:Stock[];scope:string}>;industry_representatives?:{id:string;code:string;name:string;business:string;url:string;role:string;verified_at:string;source_date:string|null}[];market_date?:string;market_revision?:number;market_refs?:Record<string,number>;concept_source?:string;analysis_time?:string;analysis_revision?:number;mapping_version?:string;prompt_version?:string;model?:string;late_capture:boolean};
type Listing={rows:Event[];total:number;page:number;asof:string;historical:boolean};
type Health={rows:{source:string;name:string;region:string;ok:boolean;stale:boolean;last_success?:string;latest_published?:string;error?:string;count?:number}[];pending:number;worker?:{heartbeat:string;running?:boolean;model_configured:boolean;analysis_alive:boolean};themes:{id:string;name:string}[];budget:{day:string;spent_or_reserved:number;requests:number;limit_usd:number};gaps:{id:number;source:string;start:string;end:string;reason:string}[]};
const statusNames:Record<string,string>={upgrade_pending:'旧版摘要／研究待升级',pending:'待整理',analyzed:'已分析',unrelated:'未命中重点题材',model_unconfigured:'模型未配置',budget_paused:'预算已暂停',inflight:'分析中',analysis_failed:'分析失败／待重试'};
const relations:Record<string,string>={direct:'直接提及',business:'业务相关',supply_chain:'产业链推导'};
const impacts:Record<string,string>={positive:'潜在正面',negative:'潜在负面',mixed:'影响混合',unknown:'无法判断'};
const time=(s?:string)=>s?new Date(s).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'未知';
const pct=(v:number|null)=>v==null?'涨幅未知':`${v>0?'+':''}${v.toFixed(2)}%`;
function Original({url,children}:{url:string;children:React.ReactNode}){return /^https?:\/\//.test(url)?<a href={url} target="_blank" rel="noopener noreferrer">{children} ↗</a>:<span>{children}</span>;}
async function get<T>(url:string,signal:AbortSignal):Promise<T>{const r=await fetch(url,{signal});if(!r.ok)throw new Error('新闻接口暂不可用');return r.json();}
type NewsProps={onStock:OpenStock;onBoard:(b:Board,kind:'industry'|'concept',date:string,revision:number|null,source:'ths'|'eastmoney')=>void};
export default function News({onBoard,onStock,initialDate='',initialOpening=null}:NewsProps & {initialDate?:string;initialOpening?:Opening|null}){
 const [mode,setMode]=useState<'rank'|'feed'>(initialOpening?'feed':'rank'),[day,setDay]=useState(initialDate),[code,setCode]=useState('');
 const [opening,setOpening]=useState<Opening|null>(initialOpening);
 const showMode=(next:'rank'|'feed')=>{
   setMode(next);setOpening(null);
   requestAnimationFrame(()=>document.getElementById('news-workspace')?.scrollIntoView({block:'start'}));
 };
 useEffect(()=>{
   if(!initialOpening)window.scrollTo({top:0});
 },[initialOpening]);
 return <main id="news" className="news-page research-page overflow-x-hidden w-full max-w-full">
 <Cover title="新闻研究" description="从领涨个股找报道，核对事件事实、发布时间与公司关联。" actions={[
   {label:'按涨幅榜查新闻',run:()=>showMode('rank')},{label:'浏览全部事件',run:()=>showMode('feed')}]}/>
 <section id="news-workspace" className="chapter-block"><Segmented<'rank'|'feed'> className="news-mode-switch" aria-label="新闻视角" value={mode}
   options={[{value:'rank',label:'涨幅榜找新闻'},{value:'feed',label:'全部新闻'}]}
   onChange={next=>{setOpening(null);setMode(next);}}/>
 {mode==='rank'?<GainerNews onStock={onStock} day={day} setDay={setDay} code={code} setCode={setCode} onOpen={v=>{setOpening(v);setMode('feed');}}/>:
 <NewsFeed key={opening?.id||'all'} onBoard={onBoard} onStock={onStock} opening={opening}/>}</section>
 <EndAction title="回到市场核对表现" description="在盘后快照中查看指数、板块和个股行情。" action={{label:'返回市场环境',href:'#overview'}}/><SourceStrip/></main>;
}
function NewsFeed({onBoard,onStock,opening}:NewsProps & {opening:Opening|null}){
 const [filters,setFilters]=useState({date:'',window:'24h',region:'',source:'',theme:'',stock:'',relation:'',q:'',level:''});const [page,setPage]=useState(1);const [refresh,setRefresh]=useState(0);const [list,setList]=useState<Listing|null>(null);const [health,setHealth]=useState<Health|null>(null);const [selected,setSelected]=useState<Event|null>(null);const [loading,setLoading]=useState(false);const [error,setError]=useState('');const [expanded,setExpanded]=useState(false);const [eventId,setEventId]=useState(opening?.id||'');
 const eventTrigger=useRef<HTMLButtonElement|null>(null);
 const set=(key:string,value:string)=>{setFilters(x=>({...x,[key]:value}));setPage(1);setSelected(null);setEventId('');};
 useEffect(()=>{
   if(!eventId)return;
   const frame=requestAnimationFrame(()=>{
     if(opening||window.innerWidth<=800)document.querySelector('.news-detail')?.scrollIntoView({block:'start'});
     else eventTrigger.current?.scrollIntoView({block:'nearest'});
   });
   return()=>cancelAnimationFrame(frame);
 },[eventId,opening,selected?.id]);
 useEffect(()=>{const c=new AbortController();setLoading(true);const qs=new URLSearchParams({page:String(page),page_size:'24'});Object.entries(filters).forEach(([k,v])=>{if(v&&(k!=='stock'||v.length===6))qs.set(k,v);});
 const timer=setTimeout(()=>Promise.all([get<Listing>(`api/news?${qs}`,c.signal),get<Health>('api/news/sources',c.signal)]).then(([l,h])=>{setList(l);setHealth(h);setError('');}).catch(e=>{if(e.name!=='AbortError'){setError(e.message);setList(null);}}).finally(()=>{if(!c.signal.aborted)setLoading(false);}),180);return()=>{clearTimeout(timer);c.abort();};},[filters,page,refresh]);
 useEffect(()=>{const timer=setInterval(()=>setRefresh(x=>x+1),60000);return()=>clearInterval(timer);},[]);
 useEffect(()=>{if(!eventId)return;const c=new AbortController();const qs=opening?.id===eventId?'?'+new URLSearchParams({asof:opening.asof}):list?.historical?`?${new URLSearchParams({asof:list.asof})}`:'';get<Event>(`api/news/${eventId}${qs}`,c.signal).then(setSelected).catch(e=>{if(e.name!=='AbortError')setError(e.message);});return()=>c.abort();},[eventId,list?.asof,list?.historical]);
 const closeDetail=()=>{
   setSelected(null);setEventId('');
   const trigger=eventTrigger.current;
   requestAnimationFrame(()=>{
     if(trigger?.isConnected){trigger.focus({preventScroll:true});trigger.scrollIntoView({block:'nearest'});}
     else document.getElementById('news-workspace')?.scrollIntoView({block:'start'});
   });
 };
 const themeName=(id:string)=>health?.themes.find(x=>x.id===id)?.name||id;
 const stockCard=(s:Stock,e:Event)=><div className="news-stock" key={s.code}><button className="stock-name" onClick={()=>set('stock',s.code)} title="筛选这只股票的关联新闻">{s.name} <small>{s.code}</small></button>{e.market_date&&<button className="sector-chip" onClick={()=>onStock({code:s.code,name:s.name,date:e.market_date!,asof:opening?.id===e.id?opening.asof:list?.historical?list.asof:undefined})}>龙虎榜 · {e.market_date}</button>}<div className="stock-numbers"><b className={s.change==null?'muted':s.change>0?'up':s.change<0?'down':''}>{pct(s.change)}</b><span>{s.amount==null?'成交额未知':`${(s.amount/1e8).toFixed(2)} 亿`}</span><span>{s.boards==null?'连板未知':s.boards===0?'未连板':`${s.boards} 板`}</span></div><div className="news-board-links">{(['industry','concepts'] as const).flatMap(k=>(s[k]||[]).slice(0,3).map(b=><button key={b.code} className="sector-chip" onClick={()=>{if(e.market_date)onBoard(b,k==='industry'?'industry':'concept',e.market_date,e.market_refs?.[k==='industry'?'industry':'concept']??null,e.concept_source==='ths'?'ths':'eastmoney');}}>{b.name}</button>))}</div></div>;
 const activeSources=health?.rows.filter(s=>s.ok&&!s.stale).length;
 const workerStale=!health?.worker?.running||Date.now()-new Date(health.worker.heartbeat).getTime()>300000;
 return <><header className="news-work-head"><div><h2>全部新闻事件</h2><p>筛选来源、题材或股票，再打开事件核对原文。</p></div>
 <div className="market-clock"><div className="status-line"><Badge status={!health?'default':workerStale?'warning':'success'}/>{!health?'采集状态待核验':workerStale?'采集进程未运行或心跳滞后':'全天采集运行中'}</div><strong>{filters.date||(filters.window==='all'?'全部已采集':'最近 24 小时')}</strong><span>新闻时间 · 北京时间</span></div></header>
 {opening&&eventId===opening.id&&<p className="notice">来自 {opening.day} {opening.origin==='lhb'?'龙虎榜':'涨幅榜'} · 此事件详情截至 {time(opening.asof)}。</p>}
 <div className="news-health"><button onClick={()=>setExpanded(x=>!x)}>来源正常 <b>{activeSources??'—'} / 6</b><span>查看采集状态</span></button><div>待分析 <b>{health?.pending??'—'}</b></div><div>模型 <b>{!health?.worker?'—':health.worker.model_configured?'DeepSeek Flash':'未配置'}</b></div><div>当日估算与预留 <b>${health?.budget.spent_or_reserved.toFixed(4)??'—'} / $1</b><span>{health?.budget.requests??'—'} 次请求 · 北京时间重置</span></div></div>
 {expanded&&<section className="panel news-source-panel"><h3>来源与采集缺口</h3>{health?.rows.map(s=><div key={s.source}><b>{s.name}</b><span className={s.ok&&!s.stale?'down':'warning'}>{s.error|| (s.stale?'更新滞后':'正常')}</span><span>最近成功 {time(s.last_success)}</span><small>源最新发布时间 {time(s.latest_published)} · 本轮 {s.count??'—'} 条</small></div>)}{!health?.rows.length&&<p>尚无采集记录。页面仅读取服务端保存的数据。</p>}{health?.gaps.map(g=><p key={g.id} className="warning">{g.source} · {time(g.start)} 至 {time(g.end)}：{g.reason}</p>)}<p className="footnote">近期条数受来源限制；休眠、断网或窗口无重叠可能漏采。费用按峰值价格保守估算，未知用量继续占用预算。</p></section>}
 <section className="news-filters">
 <label>时间范围<Select aria-label="新闻时间范围" value={filters.window} onChange={v=>{set('date','');set('window',v);}} options={[{value:'24h',label:'最近 24 小时'},{value:'all',label:'全部已采集'}]}/></label>
 <label>新闻日期<DatePicker aria-label="新闻日期" popupAlign={{overflow:{adjustX:true,shiftX:true,adjustY:true}}} inputReadOnly placeholder="不限日期" value={filters.date?dayjs(filters.date):null} onChange={d=>set('date',d?.format('YYYY-MM-DD')||'')}/></label>
 {[
   {key:'region',label:'来源地区',all:'国内与海外',choices:[{value:'domestic',label:'国内'},{value:'overseas',label:'海外'}]},
   {key:'source',label:'新闻来源',all:'全部来源',choices:health?.rows.map(s=>({value:s.source,label:s.name}))||[]},
   {key:'theme',label:'新闻题材',all:'全部题材',choices:health?.themes.map(t=>({value:t.id,label:t.name}))||[]},
   {key:'level',label:'研究层级',all:'全部事件',choices:[{value:'focus',label:'重点事件'},{value:'brief',label:'普通快讯'}]},
   {key:'relation',label:'关联类型',all:'全部关联',choices:Object.entries(relations).map(([value,label])=>({value,label}))}
 ].map(f=><label key={f.key}>{f.label}<Select aria-label={f.label} value={filters[f.key as keyof typeof filters]} onChange={v=>set(f.key,v)} options={[{value:'',label:f.all},...f.choices]}/></label>)}
 <label>股票代码<Input aria-label="新闻股票代码" allowClear placeholder="6 位代码" value={filters.stock} maxLength={6} onChange={e=>set('stock',e.target.value.replace(/\D/g,''))}/></label>
 <label className="news-search">关键词<Input aria-label="搜索新闻" allowClear placeholder="公司、事件或关键词" value={filters.q} onChange={e=>set('q',e.target.value)}/></label>
 <div className="news-filter-actions"><Button type="primary" loading={loading} onClick={()=>setRefresh(x=>x+1)}>刷新</Button><Button onClick={()=>{setFilters({date:'',window:'24h',region:'',source:'',theme:'',stock:'',relation:'',q:'',level:''});setPage(1);setSelected(null);setEventId('');}}>重置</Button></div></section>
 {list?.historical&&<p className="notice">历史回看截止 {time(list.asof)}；仅展示当时已采集、已保存的关系和分析，事后补采不会加入当时视图。</p>}
 {error&&<p className="notice">{error}</p>}
 <div className={eventId?'news-layout':'news-layout feed-only'}><section className="news-feed"><div className="news-feed-heading"><h2>事件流</h2><span>{loading?'读取中…':`${list?.total??0} 个事件`} · 点击展开证据</span></div>{list?.rows.map(e=><article className={`news-event ${eventId===e.id?'selected':''}`} key={e.id}><div className="news-event-meta"><span>{[...new Set(e.reports.map(r=>health?.rows.find(s=>s.source===r.source)?.name||r.source))].join(' / ')}</span><time>{time(e.published_at)}</time></div><button className="event-title" onClick={click=>{eventTrigger.current=click.currentTarget;setSelected(null);setEventId(e.id);}}>{e.title}</button><p className="news-summary">{e.summary}</p><div className="news-tags">{e.themes?.map(t=><button key={t} onClick={()=>set('theme',t)}>{themeName(t)}</button>)}<span>{statusNames[e.status]||e.status}</span>{e.analysis&&<span>{impacts[e.analysis.impact]}</span>}{e.late_capture&&<span>事后补采</span>}<span>{e.reports.length} 个出处</span>{e.priority?.level==='focus'&&<span className="news-focus-tag">重点研究</span>}{!!e.conflicts?.length&&<span className="warning">来源分歧</span>}</div>{!!e.stocks?.length&&<div className="news-stock-list">{e.stocks.slice(0,4).map(s=>stockCard(s,e))}</div>}{!!e.stocks?.length&&<p className="footnote">行情 {e.market_date||'未知'} · 修订 {e.market_revision??'—'}{e.stocks.length>4&&` · 共 ${e.stocks.length} 只关联股票`}</p>}</article>)}{!loading&&!list?.rows.length&&<div className="empty"><h3>暂无匹配新闻</h3><p>可重置筛选，或查看来源状态。缺少证据的股票关系保留空白。</p></div>}<Pager label="新闻事件" page={page-1} count={Math.ceil((list?.total||0)/24)} select={p=>{setPage(p+1);setSelected(null);setEventId('');}}/></section>
 {eventId&&<aside className="news-detail panel" aria-label="新闻事件详情">{selected?<><div className="news-feed-heading"><span>事件证据</span><button onClick={closeDetail} aria-label="关闭新闻详情">×</button></div><h2>{selected.title}</h2><p className="footnote">发布 {time(selected.published_at)}<br/>首次采集 {time(selected.first_seen)}<br/>分析生成 {time(selected.analysis_time)}</p><h3>事件事实与可能影响</h3><p>{selected.summary}</p>{selected.analysis?<><span className="news-impact">{impacts[selected.analysis.impact]}</span>{selected.analysis.chain.map((x,i)=><p className="news-chain" key={i}>{i+1}. {x}</p>)}</>:<p className="notice">{statusNames[selected.status]}。以下为明确提及与已核验业务匹配，尚无模型影响分析。</p>}
 <NewsResearch event={selected} research={selected.analysis?.research}/><h3>相关公司与关联依据</h3>{(selected.analysis?.links||selected.allowed_links||[]).map((l,i)=><div className="news-relation" key={`${l.code}-${i}`}><b>{selected.stocks?.find(s=>s.code===l.code)?.name||l.code} · {relations[l.type]}</b><p>{l.reason}</p><small>证据 {l.evidence_ids.join('、')}</small></div>)}{selected.stocks?.map(s=>stockCard(s,selected))}{!selected.stocks?.length&&<p className="muted">没有可发布的股票关联。</p>}<p className="footnote">行情 {selected.market_date||'暂无行情'} · 修订 {selected.market_revision??'—'}。未知连板不按首板填充。</p>
 <h3>市场领涨候选</h3>{Object.entries(selected.leaders||{}).map(([theme,board])=><div key={theme} className="news-leaders"><b>{themeName(theme)}</b>{(['boards','gainers','turnover'] as const).map(k=><p key={k}><span>{k==='boards'?'连板最高':k==='gainers'?'正涨幅最高':'成交核心'}</span>{board[k].length?board[k].map(s=><button key={s.code} onClick={()=>set('stock',s.code)}>{s.name} {k==='boards'?`${s.boards}板`:k==='gainers'?pct(s.change):`${((s.amount||0)/1e8).toFixed(2)}亿`}</button>):'暂无有效样本'}</p>)}<small>{board.scope}</small></div>)}<h3>产业地位与代表公司</h3>{selected.industry_representatives?.map(b=><p key={b.id}><Original url={b.url}>{b.name} · {b.role}</Original><br/>{b.business}<small className="news-dates">资料日期 {b.source_date||'来源未标注'} · 核验 {time(b.verified_at)}</small></p>)}{!selected.industry_representatives?.length&&<p className="muted">暂无足够业务证据，不推定产业龙头。</p>}
 <h3>证据与不确定性</h3><p className="footnote">业务相关与产业链推导均不等于实际订单。以下证据可核对；模型无法判断的影响保留未知。</p>{Object.entries(selected.evidence||{}).map(([id,e])=><details key={id}><summary>{id} · {e.kind==='news'?'新闻':e.kind==='article'?'原文补证':e.kind==='prior'?'此前报道':e.kind==='supply_chain'?'产业链证据':'业务证据'}</summary><p>{e.text}</p><Original url={e.url}>查看来源</Original></details>)}<h3>各来源报道</h3>{selected.reports.map((r,i)=><details key={i}><summary>{r.source} · {time(r.published_at)}</summary><Original url={r.url}>{r.title}</Original><p>{r.summary}</p><small>首次采集 {time(r.first_seen)}</small></details>)}<p className="footnote">分析修订 {selected.analysis_revision??'—'} · {selected.model||'未分析'} · {selected.prompt_version||'—'}<br/>映射版本 {selected.mapping_version?.slice(0,12)||'—'}</p></>:<div className="news-detail-empty"><div className="news-feed-heading"><span>{error?'事件详情暂不可用':'正在读取事件…'}</span><button onClick={closeDetail} aria-label="关闭新闻详情">×</button></div></div>}</aside>}</div><footer>公开新闻研究 · 保留一年 · 原始响应保留 30 天 · 无价格预测或交易执行</footer></>;
}
