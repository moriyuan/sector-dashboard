import React, {useEffect, useMemo, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import * as echarts from 'echarts/core';
import type {EChartsOption} from 'echarts';
import {BarChart,HeatmapChart,LineChart,TreemapChart} from 'echarts/charts';
import {GridComponent,TooltipComponent,VisualMapContinuousComponent,DataZoomInsideComponent,DataZoomSliderComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
import {Button,Input,Popover,Segmented,Select} from 'antd';
import {ArchivePicker,DeskProvider,DetailDrawer} from './DeskUI';
import './style.css';
import {DeskNav,Cover,Chapter,NotesDeck,Pager,SignalTab,usePages,SourceStrip,EndAction,useDeskRefresh} from './ResearchLayout';
import Gainers from './Gainers';
import EtfTracker from './EtfTracker';
import {CapitalFlow, FlowPeriods, type Flow} from './CapitalFlow';
import News from './News';
import Backlog from './Backlog';
import Leaders from './Leaders';
import {LhbDrawer,type StockPick} from './Lhb';
import type {Opening} from './GainerNews';
import './research.css';

echarts.use([BarChart,HeatmapChart,LineChart,TreemapChart,GridComponent,TooltipComponent,VisualMapContinuousComponent,DataZoomInsideComponent,DataZoomSliderComponent,CanvasRenderer]);

type Nullable = number | null | undefined;
type Meta = {trade_date:string;mode:string;source_time:string|null;collected_at:string|null;revision:number|null;rule_version:string;status:string;coverage:number|null;ingestion_method?:string;source?:string};
type Sector = {membership_verified?:boolean;vendor_up:Nullable;vendor_down:Nullable;vendor_flat:Nullable;code:string;name:string;change:Nullable;amount:Nullable;price:Nullable;return_5:Nullable;return_10:Nullable;return_20:Nullable;heat:Nullable;up_ratio:Nullable;coverage:Nullable;member_count:Nullable;eligible_count:Nullable;amount_share:Nullable;share_change:Nullable;drawdown:Nullable;streak:Nullable;top_days_10:Nullable;rank:Nullable;rank_change:Nullable;flow:Flow|null;labels:string[];rule_evidence:string[];heat_inputs:{daily:Nullable;prior_5:Nullable;breadth:Nullable};matrix:{date:string;change:Nullable;percentile:Nullable}[]};
type Listing = {meta:Meta;rows:Sector[];scope:string;quote_coverage?:number;history_coverage?:number;components?:{stocks:Meta;market:Meta;flow:Meta}};
type Stats = {quote_coverage:Nullable;quote_limit_up:Nullable;quote_limit_down:Nullable;limit_price_coverage:Nullable;up:number;down:number;flat:number;up_ratio:Nullable;eligible:number;amount:Nullable;coverage:Nullable;total:number;st:{up:number;down:number;flat:number;eligible:number};distribution:number[]};
type SentimentSub={key:string;name:string;score:number;weight:number;inputs:Record<string,number|null>};
type Sentiment={score:number;date:string;subs:SentimentSub[];missing:string[];note:string};
type Market = {meta:Meta;indices_meta:Meta;stats:Stats|null;indices:{code:string;name:string;price:Nullable;change:Nullable}[];emotion:{limit_up:Nullable;limit_down:Nullable;broken:Nullable;broken_ratio:Nullable;max_boards:Nullable}|null;emotion_meta:Meta;sentiment?:Sentiment|null};
type Stock={code:string;name:string;change:Nullable;amount:Nullable;price:Nullable;boards:Nullable};
type Detail={membership_meta?:{source:string;source_time:string|null;captured_at:string;verified:boolean;reported_count:number;expected_count:number|null};components?:{stocks:Meta;market:Meta;flow:Meta};meta:Meta;sector:Sector;bars:{date:string;close:number;amount:number}[];members:Stock[];members_available:boolean;flow_history:({date:string}&Flow)[];limit_up:Stock[]|null};
type Status={phase:string;server_time:string;collecting:boolean;events:{job:string;ok:number;time:string;message:string;stale:boolean}[]};
type Dates={snapshots:{trade_date:string;mode:string;revision:number}[];history_dates:string[]};
const fmt=(v:Nullable,d=2)=>v==null?'—':v.toLocaleString('zh-CN',{maximumFractionDigits:d,minimumFractionDigits:d});
const pct=(v:Nullable)=>v==null?'—':`${v>0?'+':''}${fmt(v)}%`;
const amt=(v:Nullable)=>v==null?'—':Math.abs(v)>=1e12?`${fmt(v/1e12)}万亿`:`${fmt(v/1e8)}亿`;

const sentimentColor=(s:number)=>s>=70?'#c8544a':s>=55?'#d08a1d':s>=45?'#8b8d87':s>=30?'#5f8a68':'#4a7a9a';
function SentimentStrip({data,open,onToggle}:{data:Sentiment|null;open:boolean;onToggle:()=>void}){
  if(!data)return <div className="sentiment-strip"><span className="sentiment-label">综合强度<em className="sentiment-tag">自研</em></span><strong>—</strong><small>等待市场快照</small></div>;
  const detail=<div className="sentiment-detail">
    {data.subs.map(s=><div key={s.key} className="sentiment-row">
      <span className="sentiment-name">{s.name}<em>{Math.round(s.weight*100)}%</em></span>
      <div className="sentiment-mini"><i style={{width:`${s.score}%`}}/></div>
      <b>{fmt(s.score,1)}</b>
    </div>)}
    {data.missing.length>0&&<p className="sentiment-missing">数据不足留空：{data.missing.join('、')}</p>}
    <p className="sentiment-note">{data.note}</p>
  </div>;
  return <div className="sentiment-strip" onClick={onToggle} title={open?'收起历史曲线':'点击展开历史曲线'}>
   <span className="sentiment-label">综合强度<em className="sentiment-tag">自研</em></span>
   <strong style={{color:sentimentColor(data.score)}}>{fmt(data.score,1)}</strong>
   <div className="sentiment-track"><b style={{left:`${data.score}%`}}/></div>
   <Popover content={detail} placement="bottom"><span className="sentiment-more" onClick={e=>e.stopPropagation()}>五维明细</span></Popover>
   <span className="sentiment-hint">{open?'收起 ↑':'历史曲线 ↓'}</span>
  </div>;
}
function Spark({values,color}:{values:(number|null)[];color:string}){
  const pts=values.filter((v):v is number=>v!=null);
  if(pts.length<2)return <div className="spark spark-empty"/>;
  const max=Math.max(...pts,1),W=120,H=34,step=W/(pts.length-1);
  const line=pts.map((v,i)=>`${(i*step).toFixed(1)},${(H-3-(v/max)*(H-8)).toFixed(1)}`).join(' ');
  return <svg className="spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
   <polygon points={`0,${H} ${line} ${W},${H}`} fill={color} opacity={0.10}/>
   <polyline points={line} fill="none" stroke={color} strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round"/>
  </svg>;
}
function EmotionCard({ice,climax,day,iceHist,cxHist,open,onToggle}:{ice:number|null;climax:number|null;day:string;iceHist:(number|null)[];cxHist:(number|null)[];open:boolean;onToggle:()=>void}){
  return <div className="emo-card" onClick={onToggle} title={open?'收起历史曲线':'点击展开历史曲线'}>
   <div className="emo-card-head">
    <span className="sentiment-label">情绪打分<em className="sentiment-tag">自研</em></span>
    <span className="emo-card-meta">{day?`${day.slice(5)} 收盘`:''}</span>
    <span className="sentiment-hint">{open?'收起 ↑':'历史曲线 ↓'}</span>
   </div>
   <div className="emo-card-body">
    <div className="emo-block"><small>冰点分 · 越大约冰</small><strong style={{color:'#4a7a9a'}}>{ice==null?'—':fmt(ice,1)}</strong><Spark values={iceHist} color="#4a7a9a"/></div>
    <div className="emo-block"><small>高潮分 · 越大约热</small><strong style={{color:'#c8842a'}}>{climax==null?'—':fmt(climax,1)}</strong><Spark values={cxHist} color="#c8842a"/></div>
   </div>
  </div>;
}
const color=(v:Nullable)=>v==null?'muted':v>0?'up':v<0?'down':'muted';
const indexStrip = [['000001','上证指数'],['399001','深证成指'],['399006','创业板指'],['000300','沪深300'],['000680','科创综指']] as const;
const labelNames:Record<string,string>={hot:'热点',cold:'冷门低关注',ice:'退潮冰点候选'};
const phases:Record<string,string>={trading:'盘后复盘 · 等待收盘',lunch:'盘后复盘 · 等待收盘',holiday:'非交易日 · 查看归档',closed:'等待盘后归档',archive_1530:'收盘归档',archive_1630:'收盘复核',calendar_unknown:'交易日历待核验'};
const timeText=(v:string|null|undefined)=>v?`${v.slice(5,10)} ${v.slice(11,19)}`:'源时间未知';
async function get<T>(url:string,signal?:AbortSignal):Promise<T>{const r=await fetch(url,{signal});if(!r.ok)throw Error(`请求失败 ${r.status}`);return r.json();}

function Chart({option,height=300,onClick}:{option:EChartsOption;height?:number;onClick?:(name:string)=>void}){
 const ref=useRef<HTMLDivElement>(null);
 useEffect(()=>{
   const el=ref.current;
   if(!el)return;
   let chart:echarts.ECharts|undefined;
   const render=()=>{
     if(!el.clientWidth||!el.clientHeight)return;
     if(chart){chart.resize();return;}
     chart=echarts.init(el,undefined,{renderer:'canvas'});
     chart.setOption(option);
     if(onClick)chart.on('click',p=>onClick(p.name));
   };
   const observer=new ResizeObserver(render);
   observer.observe(el);render();
   return()=>{observer.disconnect();chart?.dispose();};
 },[option,onClick]);
 return <div ref={ref} style={{height,width:'100%'}} role="img" aria-label="板块分析图表"/>;
}
function Empty({title='暂无可验证数据',text='采集成功后会自动展示。缺失值保留为空，不使用演示数据。'}:{title?:string;text?:string}){return <div className="empty"><div className="empty-mark">∅</div><strong>{title}</strong><p>{text}</p></div>;}
function Heading({title,subtitle,children}:{title:string;subtitle?:string;children?:React.ReactNode}){
 return <div className="section-head"><div><h3>{title}</h3>{subtitle&&<p>{subtitle}</p>}</div>{children&&<div>{children}</div>}</div>;
}
function Toggle({options,value,onChange}:{options:[string,string][];value:string;onChange:(v:string)=>void}){
 return <Segmented size="small" aria-label={options.map(([,name])=>name).join(' / ')} value={value}
   options={options.map(([value,label])=>({value,label}))} onChange={onChange}/>;
}

function App(){
 useDeskRefresh();
 const [stockPick,setStockPick]=useState<StockPick|null>(null);
 const [newsJump,setNewsJump]=useState<Opening|null>(null);
 const openEvent=(event:Opening)=>{setStockPick(null);setNewsJump(event);setNewsDate(event.day);location.hash='news';};
 const [newsDate,setNewsDate]=useState('');
 const [newsView,setNewsView]=useState(location.hash==='#news');
 useEffect(()=>{const onHash=()=>setNewsView(location.hash==='#news');window.addEventListener('hashchange',onHash);return()=>window.removeEventListener('hashchange',onHash);},[]);
 const [kind,setKind]=useState('industry');const [conceptSource,setConceptSource]=useState('ths');const mode='close';const [date,setDate]=useState('');const [dates,setDates]=useState<Dates>({snapshots:[],history_dates:[]});
 const [data,setData]=useState<Listing|null>(null);const [market,setMarket]=useState<Market|null>(null);const [status,setStatus]=useState<Status|null>(null);const [error,setError]=useState('');const [loading,setLoading]=useState(true);const [refresh,setRefresh]=useState(0);const [sentiCurve,setSentiCurve]=useState<{date:string;score:number}[]>([]);
 const [boardsCurve,setBoardsCurve]=useState<{date:string;tiers:{boards:number;names:string[]}[]}[]>([]);const [showCurve,setShowCurve]=useState(false);const [curveSel,setCurveSel]=useState({senti:true,boards:true});
 const [emoCurve,setEmoCurve]=useState<{date:string;ice:number|null;climax:number|null;source:string}[]>([]);const [showEmo,setShowEmo]=useState(false);
 const [leaderPerf,setLeaderPerf]=useState<{date:string|null;pools:{emotion:{code:string;name:string;d1:number|null;d10:number|null;d20:number|null}[]}}|null>(null);
 const toggleCurve=()=>{const nv=!showCurve;setShowCurve(nv);if(nv)setTimeout(()=>document.getElementById('curve')?.scrollIntoView({behavior:'smooth',block:'center'}),80);};
 const toggleEmo=()=>{const nv=!showEmo;setShowEmo(nv);if(nv)setTimeout(()=>document.getElementById('emotion-curve')?.scrollIntoView({behavior:'smooth',block:'center'}),80);};
 const toggleSel=(k:'senti'|'boards')=>setCurveSel(s=>{const n={...s,[k]:!s[k]};return n.senti||n.boards?n:s;});
 const [query,setQuery]=useState('');const [period,setPeriod]=useState('1');const [ranking,setRanking]=useState('hot');const [sort,setSort]=useState('heat');const [selected,setSelectedRaw]=useState<string|null>(null);const [detailRevision,setDetailRevision]=useState<number|null>(null);const setSelected=(code:string|null)=>{setDetailRevision(null);setSelectedRaw(code);};const [detail,setDetail]=useState<Detail|null>(null);const [detailError,setDetailError]=useState('');const [stockSort,setStockSort]=useState('change');const [showHealth,setShowHealth]=useState(false);const [showRules,setShowRules]=useState(false);
 const [dateHint,setDateHint]=useState('读取日期…');
 useEffect(()=>{
   const controller=new AbortController();
   get<Dates>('api/dates',controller.signal).then(d=>{
     if(controller.signal.aborted)return;
     setDates(d);setDateHint('暂无归档');
     if(d.snapshots.length)setDate(current=>current||d.snapshots[0].trade_date);
   }).catch(()=>{if(!controller.signal.aborted)setDateHint('日期未能读取');});
   return()=>controller.abort();
 },[refresh]);
 useEffect(()=>{const controller=new AbortController();setLoading(true);const qs=new URLSearchParams({kind,mode,concept_source:conceptSource,...(date?{date}:{})});Promise.all([get<Listing>(`api/sectors?${qs}`,controller.signal),get<Market>(`api/market?${qs}`,controller.signal),get<Status>('api/status',controller.signal),get<{rows:{date:string;score:number}[]}>('api/sentiment?limit=500',controller.signal),get<{rows:{date:string;tiers:{boards:number;names:string[]}[]}[]}>('api/boards-history?limit=250',controller.signal),get<{rows:{date:string;ice:number|null;climax:number|null;source:string}[]}>('api/emotion-score?limit=250',controller.signal),get<{date:string|null;pools:{emotion:{code:string;name:string;d1:number|null;d10:number|null;d20:number|null}[]}}>('api/leaders/perf',controller.signal).catch(()=>null)]).then(([d,m,s,c,bd,em,lp])=>{if(controller.signal.aborted)return;setData(d);setMarket(m);setStatus(s);setSentiCurve(c.rows||[]);setBoardsCurve(bd.rows||[]);setEmoCurve(em.rows||[]);setLeaderPerf(lp);setError('');}).catch(e=>{if(e.name!=='AbortError'){setError('本地服务暂不可用，请检查启动终端。');setData(null);setMarket(null);}}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});return()=>controller.abort();},[kind,conceptSource,mode,date,refresh]);
 useEffect(()=>{if(!selected){setDetail(null);return;}const controller=new AbortController();setDetail(null);setDetailError('');const qs=new URLSearchParams({kind,mode,concept_source:conceptSource,...(date?{date}:{}),...((detailRevision??data?.meta.revision)?{revision:String(detailRevision??data?.meta.revision)}:{})});get<Detail>(`api/sectors/${selected}?${qs}`,controller.signal).then(setDetail).catch(e=>{if(e.name!=='AbortError')setDetailError('所选日期没有该板块的完整详情。');});return()=>controller.abort();},[selected,kind,conceptSource,mode,date,data?.meta.revision,detailRevision]);
 const rows=useMemo(()=>data?.rows.filter(r=>r.name.includes(query)||r.code.toLowerCase().includes(query.toLowerCase()))||[],[data,query]);
 const filtered=useMemo(()=>[...rows].sort((a,b)=>{const field=sort as keyof Sector;const av=a[field] as Nullable;const bv=b[field] as Nullable;return av==null?bv==null?0:1:bv==null?-1:bv-av;}),[rows,sort]);
 const ranked=rows.filter(r=>r.labels.includes(ranking)).sort((a,b)=>ranking==='ice'?(a.change??0)-(b.change??0):(b.heat??0)-(a.heat??0));
 const focusPage=usePages(ranked,6,[date,kind,conceptSource,query,ranking].join('|'));
 const changes=(r:Sector):Nullable=>period==='1'?r.change:r[`return_${period}` as keyof Sector] as Nullable;
 const heatOption=useMemo<EChartsOption>(()=>({tooltip:{confine:true,formatter:(p:any)=>`${echarts.format.encodeHTML(p.name)}<br/>涨跌幅 ${pct(p.data.change)}<br/>成交额 ${amt(p.value)}`},series:[{type:'treemap',roam:false,nodeClick:false,breadcrumb:{show:false},left:0,right:0,top:0,bottom:0,label:{show:true,formatter:(p:any)=>`${p.name}\n${pct(p.data.change)}`,color:'#303433',fontSize:12,lineHeight:22},itemStyle:{borderColor:'#fff',borderWidth:3,gapWidth:2},data:rows.filter(r=>r.amount!=null&&r.amount>0).map(r=>({name:r.name,value:r.amount!,change:changes(r),itemStyle:{color:changes(r)==null||changes(r)===0?'#eeefec':changes(r)!>0?`rgba(192,71,62,${Math.min(.65,.12+Math.abs(changes(r)!)*.07)})`:`rgba(44,125,100,${Math.min(.65,.12+Math.abs(changes(r)!)*.07)})`}}))}]}),[rows,period]);
 const matrixRows=filtered.slice(0,14);const matrixDates=matrixRows[0]?.matrix.map(m=>m.date)||[];
 const matrixOption=useMemo<EChartsOption>(()=>({grid:{top:10,bottom:30,left:100,right:10},tooltip:{confine:true,formatter:(p:any)=>`${echarts.format.encodeHTML(matrixRows[p.value[1]]?.name||'')} · ${matrixDates[p.value[0]]}<br/>${pct(p.value[2])}`},xAxis:{type:'category',data:matrixDates.map(d=>d.slice(5)),axisLine:{show:false},axisTick:{show:false},axisLabel:{color:'#8b8d87',fontSize:10}},yAxis:{type:'category',data:matrixRows.map(r=>r.name),inverse:true,axisLine:{show:false},axisTick:{show:false},axisLabel:{color:'#4d524b',width:90,overflow:'truncate'}},visualMap:{show:false,min:-5,max:5,inRange:{color:['#68a994','#faf9f5','#d58178']}},series:[{type:'heatmap',data:matrixRows.flatMap((r,y)=>matrixDates.flatMap((d,x)=>{const v=r.matrix.find(m=>m.date===d)?.change;return v==null?[]:[{name:r.name,value:[x,y,v]}];})),itemStyle:{borderWidth:3,borderColor:'#fff'}}]}),[matrixRows,matrixDates]);
 const amountLeaders=[...rows].filter(r=>r.amount_share!=null).sort((a,b)=>(b.amount_share||0)-(a.amount_share||0)).slice(0,8);
 const failures=status?.events.filter(e=>!e.ok||e.stale)||[];
 const hasIndexOnly=data?.meta.status==='index_only';
 const hasData=rows.length>0;
 const noMatch=!!(query&&data?.rows.length&&!hasData);
 const filterHint=noMatch?<p className="filter-hint">没有符合当前搜索的板块。<button className="text-button" onClick={()=>setQuery('')}>清除搜索</button></p>:null;
 const stats=market?.stats;const emotion=market?.emotion;const fullLimits=stats?.quote_limit_up!=null&&stats?.quote_limit_down!=null;
 const options=[...new Set([...dates.snapshots.map(d=>d.trade_date),...dates.history_dates,...(date?[date]:[])])].sort().reverse();
 const pick=(name:string)=>{const row=rows.find(r=>r.name===name);if(row)setSelected(row.code);};
 const boardPage=usePages(filtered,10,[date,kind,conceptSource,query,sort].join('|'));
 const openNews=()=>{setNewsJump(null);setNewsDate(date);};
 const historical=!!date&&!!status?.server_time&&date<status.server_time.slice(0,10);
 const hot=rows.filter(r=>r.labels.includes('hot')).sort((a,b)=>(b.heat??0)-(a.heat??0));
 const notes=[
   {title:'市场广度',value:stats?.up_ratio==null?'缺少广度':fmt(stats.up_ratio,1)+'%',description:'非 ST 有效报价中上涨股票的比例。',href:'#overview'},
   {title:kind==='industry'?'行业热点':'概念热点',value:hot[0]?.name??'尚无已核实热点',description:hot[0]?'当前筛选下热点强度最高，点击查看同期板块。':'查看评分覆盖和板块分布。',href:'#sectors'},
   {title:'成交额聚焦',value:amountLeaders[0]?.name??'成交占比待核验',description:amountLeaders[0]?'当前筛选下成交额占比居首，可在资金面板核对。':'有效样本不足时留空。',href:'#turnover'}
 ];
 const sentiByDate:Record<string,number>=Object.fromEntries(sentiCurve.map(r=>[r.date,r.score] as [string,number]));
 const boardsByDate:Record<string,{boards:number;names:string[]}>=Object.fromEntries(boardsCurve.map(r=>[r.date,r.tiers[0]] as [string,{boards:number;names:string[]}]));
 const emoByDate:Record<string,{ice:number|null;climax:number|null;source:string}>=Object.fromEntries(emoCurve.map(r=>[r.date,{ice:r.ice,climax:r.climax,source:r.source}] as [string,{ice:number|null;climax:number|null;source:string}]));
 const curveDays=[...new Set([...sentiCurve.map(r=>r.date),...boardsCurve.map(r=>r.date)])].sort();
 const emoDays=emoCurve.map(r=>r.date);
 const emoLatest=[...emoCurve].reverse().find(r=>r.ice!=null||r.climax!=null)||null;
 const poolRows=(leaderPerf?.pools.emotion??[]).slice(0,4);
 return <><DeskNav news={newsView} onNews={openNews}>{!newsView?<><div className="nav-date"><span>复盘日</span>
   <ArchivePicker label="交易日期" value={date} dates={options} emptyText={dateHint} snapshots={dates.snapshots.map(d=>d.trade_date)}
     shortcut={{label:dates.snapshots.length?'最新快照':'最新指数日',value:dates.snapshots[0]?.trade_date||options[0]||''}}
     onChange={next=>{if(next===date)return;setData(null);setMarket(null);setDate(next);setConceptSource('ths');setSelected(null);}}/></div>
   <Button type="primary" className="desk-refresh" loading={loading} onClick={()=>setRefresh(v=>v+1)}>刷新</Button><Backlog/></>:<a className="nav-back" href="#overview">市场总览 ↗</a>}</DeskNav>
 <main id="top" className="research-page overflow-x-hidden w-full max-w-full" style={{display:newsView?'none':undefined}}>
 <Cover title="A 股盘后复盘" description="从市场环境和板块强弱，追踪领涨个股的披露与报道。" actions={[{label:'查看市场环境',href:'#overview'},{label:'定位领涨个股',href:'#gainers'}]}/>
 <section id="overview" className="chapter-block"><Chapter title="市场环境" copy={['先看五项指数与市场广度，','再核对涨跌停、炸板和连板高度。']} aside={<span className="phase-note">{historical?'历史复盘':phases[status?.phase||'']||'连接本地服务'} · {data?.meta.trade_date||date||'日期待定'}</span>}/>
 <div className="index-strip">{indexStrip.map(([code,name])=>{
   const index=market?.indices.find(i=>i.code===code);
   return <div key={code}><span>{name}</span><strong>{fmt(index?.price??null)}</strong><b className={color(index?.change??null)}>{index?pct(index.change):market?'未存档':'等待源数据'}</b></div>;
 })}</div>
 <div className="metrics"><div className="metric wide"><span>市场成交额</span><strong>{amt(stats?.amount)}</strong><small>沪深 A 股 · 含 ST · 有效报价</small></div><div className="metric wide"><span>上涨 / 下跌</span><strong><b className="up">{fmt(stats?.up,0)}</b><em>/</em><b className="down">{fmt(stats?.down,0)}</b></strong><div className="breadth-track"><i style={{width:`${stats?.up_ratio??0}%`}}/></div><small>平盘 {fmt(stats?.flat,0)} · ST 涨 {fmt(stats?.st.up,0)} / 跌 {fmt(stats?.st.down,0)}</small></div><div className="metric"><span>{fullLimits?'涨停 / 跌停':'池中涨停 / 跌停'}</span><strong><b className="up">{fmt(fullLimits?stats?.quote_limit_up:emotion?.limit_up,0)}</b><em>/</em><b className="down">{fmt(fullLimits?stats?.quote_limit_down:emotion?.limit_down,0)}</b></strong><small>{fullLimits?'源涨跌停价判定 · 非ST':'供应商池覆盖 · 非ST'}</small></div><div className="metric"><span>炸板率</span><strong>{emotion?.broken_ratio==null?'—':`${fmt(emotion.broken_ratio,1)}%`}</strong><small>东财池：未封住 / 当日曾涨停</small></div><div className="metric clickable" onClick={toggleCurve} title={showCurve?'收起走势曲线':'点击查看走势'}><span>最高连板<em className="metric-hint">{showCurve?'收起 ↑':'走势 ↓'}</em></span><strong>{fmt(emotion?.max_boards,0)}<small>板</small></strong><small>东财池 · 非ST，科创板可能未收录</small></div></div>
 <SentimentStrip data={market?.sentiment??null} open={showCurve} onToggle={toggleCurve}/>
 <div className="emotion-cards">
 <EmotionCard ice={emoLatest?.ice??null} climax={emoLatest?.climax??null} day={emoLatest?.date||''} iceHist={emoCurve.map(r=>r.ice).slice(-30)} cxHist={emoCurve.map(r=>r.climax).slice(-30)} open={showEmo} onToggle={toggleEmo}/>
 <Leaders entry={<div className="emo-card pool-card" title="管理核心龙头股票池">
  <div className="emo-card-head">
   <span className="sentiment-label">龙头池<em className="sentiment-tag">情绪核心</em></span>
   <span className="emo-card-meta">{leaderPerf?.date?`${leaderPerf.date.slice(5)} 收盘`:'涨幅随收盘归档更新'}</span>
   <span className="sentiment-hint">管理 ↗</span>
  </div>
  {poolRows.length?<div className="pool-rows">{poolRows.map(s=><div key={s.code} className="pool-row">
    <span className="pool-name">{s.name}<em>{s.code}</em></span>
    <span className={`pool-d1 ${color(s.d1)}`}>{pct(s.d1)}</span>
    <span className="pool-sub"><i>10日</i>{pct(s.d10)}</span>
    <span className="pool-sub"><i>20日</i>{pct(s.d20)}</span>
   </div>)}
   {(leaderPerf?.pools.emotion.length??0)>4&&<div className="pool-more">还有 {(leaderPerf?.pools.emotion.length??0)-4} 只，点卡片查看全部</div>}
  </div>:<div className="pool-empty"><strong>池子是空的</strong><span>评分暂用昨日连板股兜底，点我添加龙头股</span></div>}
 </div>}/>
 </div>
 {showCurve&&<section id="curve" className="breadth-card sentiment-curve"><h3>综合强度 × 最高板<em className="sentiment-tag">自研 + 涨停池</em></h3>
 <div className="curve-chips"><button className={`curve-chip${curveSel.senti?'':' off'}`} onClick={()=>toggleSel('senti')}><i className="dot" style={{background:'#b0523c'}}/>综合强度</button><button className={`curve-chip${curveSel.boards?'':' off'}`} onClick={()=>toggleSel('boards')}><i className="dot" style={{background:'#36583e'}}/>最高板</button></div>
 {sentiCurve.length>1||boardsCurve.length>1?<Chart height={300} option={{
   grid:{left:36,right:36,top:16,bottom:48},
   dataZoom:[{type:'inside',startValue:Math.max(0,curveDays.length-60),endValue:curveDays.length-1,filterMode:'none'},
     {type:'slider',height:16,bottom:6,startValue:Math.max(0,curveDays.length-60),endValue:curveDays.length-1,filterMode:'none',
       borderColor:'transparent',backgroundColor:'rgba(54,88,62,.05)',fillerColor:'rgba(54,88,62,.12)',
       handleStyle:{color:'#36583e'},moveHandleStyle:{color:'#36583e'},
       dataBackground:{lineStyle:{color:'#c9cdc2'},areaStyle:{color:'rgba(54,88,62,.08)'}},
       selectedDataBackground:{lineStyle:{color:'#36583e'},areaStyle:{color:'rgba(54,88,62,.14)'}},
       textStyle:{fontSize:9,color:'#8b8d87'}}],
   tooltip:{trigger:'axis',formatter:(ps:any)=>{const day=curveDays[ps[0]?.dataIndex]||'';const lines=ps.filter((p:any)=>p.data&&p.value!=null).map((p:any)=>p.seriesName==='最高板'?`最高板 <b>${p.data.boards}板</b>：${p.data.names.join('、')}`:`综合强度 <b>${p.value}</b>`);return [day,...lines].join('<br/>');}},
   xAxis:{type:'category',data:curveDays.map(d=>d.slice(5)),axisLine:{show:false},axisTick:{show:false},axisLabel:{color:'#8b8d87',fontSize:10}},
   yAxis:[{type:'value',show:curveSel.senti,min:0,max:100,interval:10,axisLabel:{color:'#8b8d87',fontSize:10},splitLine:{lineStyle:{color:'#eef0e8'}}},
     {type:'value',show:curveSel.boards,min:0,minInterval:1,axisLabel:{color:'#8b8d87',fontSize:10},splitLine:{show:curveSel.senti?false:true,lineStyle:{color:'#eef0e8'}}}],
   series:[...(curveSel.senti?[{name:'综合强度',type:'line' as const,smooth:true,symbol:'circle',symbolSize:5,connectNulls:false,
     data:curveDays.map(d=>d in sentiByDate?{value:sentiByDate[d],itemStyle:{color:sentimentColor(sentiByDate[d])}}:null),
     lineStyle:{width:2,color:'#b0523c'},areaStyle:{color:{type:'linear' as const,x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:'rgba(200,84,74,.22)'},{offset:1,color:'rgba(200,84,74,0)'}]}},
     markLine:{silent:true,symbol:'none',label:{fontSize:10,color:'#a3a69e'},data:[{yAxis:75,lineStyle:{color:'#c8544a',type:'dashed' as const},label:{formatter:'过热 75'}},{yAxis:25,lineStyle:{color:'#5f8a68',type:'dashed' as const},label:{formatter:'过冷 25'}}]},
     ...(date&&curveDays.includes(date)?{markPoint:{symbol:'pin',symbolSize:34,itemStyle:{color:'#36583e'},label:{fontSize:9,color:'#fff'},data:[{name:'当前',coord:[curveDays.indexOf(date),sentiByDate[date]]}]}}:{})
   }]:[]),...(curveSel.boards?[{name:'最高板',type:'line' as const,smooth:true,symbol:'circle',symbolSize:6,connectNulls:false,yAxisIndex:1,
     lineStyle:{width:2,color:'#36583e'},itemStyle:{color:'#36583e'},
     label:{show:true,position:'top' as const,fontSize:9,color:'#36583e',lineHeight:12,
       formatter:(p:any)=>p.data?.names?p.data.names.map((n:string)=>`${n}${p.data.boards}`).join('\n'):''},
     data:curveDays.map(d=>{const t=boardsByDate[d];return t?{value:t.boards,boards:t.boards,names:t.names}:null;})
   }]:[])]}}/>:<Empty title="暂无曲线数据" text="等待连续交易日的市场快照与涨停池积累。"/>}
 <p className="footnote">自研近似算法（参考开盘啦五维度），非开盘啦官方数值 · 最高板取自每日涨停池，并列股票全部列出 · 池历史自系统上线起逐日积累</p></section>}
 {showEmo&&<section id="emotion-curve" className="breadth-card sentiment-curve"><h3>冰点分 × 高潮分<em className="sentiment-tag">自研打分</em></h3>
 {emoCurve.length>1?<Chart height={260} option={{
   grid:{left:36,right:36,top:16,bottom:48},
   dataZoom:[{type:'inside',startValue:Math.max(0,emoDays.length-60),endValue:emoDays.length-1,filterMode:'none'},
     {type:'slider',height:16,bottom:6,startValue:Math.max(0,emoDays.length-60),endValue:emoDays.length-1,filterMode:'none',
       borderColor:'transparent',backgroundColor:'rgba(54,88,62,.05)',fillerColor:'rgba(54,88,62,.12)',
       handleStyle:{color:'#36583e'},moveHandleStyle:{color:'#36583e'},
       dataBackground:{lineStyle:{color:'#c9cdc2'},areaStyle:{color:'rgba(54,88,62,.08)'}},
       selectedDataBackground:{lineStyle:{color:'#36583e'},areaStyle:{color:'rgba(54,88,62,.14)'}},
       textStyle:{fontSize:9,color:'#8b8d87'}}],
   tooltip:{trigger:'axis',formatter:(ps:any)=>{const day=emoDays[ps[0]?.dataIndex]||'';const emo=emoByDate[day];const lines=ps.filter((p:any)=>p.data!=null&&p.value!=null).map((p:any)=>`${p.seriesName} <b>${p.value}</b>`);
     return [day+(emo?.source==='kline'?'（日K近似回补）':''),...lines].join('<br/>');}},
   xAxis:{type:'category',data:emoDays.map(d=>d.slice(5)),axisLine:{show:false},axisTick:{show:false},axisLabel:{color:'#8b8d87',fontSize:10}},
   yAxis:{type:'value',min:0,minInterval:1,axisLabel:{color:'#8b8d87',fontSize:10},splitLine:{lineStyle:{color:'#eef0e8'}}},
   series:[{name:'冰点分',type:'line' as const,smooth:true,symbol:'circle',symbolSize:5,connectNulls:false,
     lineStyle:{width:2,color:'#4a7a9a'},itemStyle:{color:'#4a7a9a'},
     areaStyle:{color:{type:'linear' as const,x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:'rgba(74,122,154,.18)'},{offset:1,color:'rgba(74,122,154,0)'}]}},
     label:{show:true,position:'top' as const,fontSize:9,color:'#4a7a9a',lineHeight:12,formatter:(p:any)=>p.value??''},
     data:emoDays.map(d=>{const t=emoByDate[d];return t&&t.ice!=null?t.ice:null;})
   },{name:'高潮分',type:'line' as const,smooth:true,symbol:'circle',symbolSize:5,connectNulls:false,
     lineStyle:{width:2,color:'#c8842a'},itemStyle:{color:'#c8842a'},
     areaStyle:{color:{type:'linear' as const,x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:'rgba(200,132,42,.18)'},{offset:1,color:'rgba(200,132,42,0)'}]}},
     label:{show:true,position:'bottom' as const,fontSize:9,color:'#c8842a',lineHeight:12,formatter:(p:any)=>p.value??''},
     data:emoDays.map(d=>{const t=emoByDate[d];return t&&t.climax!=null?t.climax:null;})
   }]}}/>:<Empty title="暂无打分数据" text="等待连续交易日的市场快照与涨停池积累。"/>}
 <p className="footnote">冰点分越大情绪越冰，高潮分越大越极致火热 · 五维打分：广度/涨跌停/封板率/龙头/大面（冰点）与广度/涨停/龙头/最高板/封板率（高潮）· 龙头取「龙头池」情绪核心池，池空时用昨日连板股兜底 · 标注「日K近似回补」的日期为池数据缺失段（约两周前），由日 K 近似计算</p></section>}
 <div className="market-bento grid-flow-dense"><section className="breadth-card"><h3>涨跌分布</h3> {stats?<Chart height={150} option={{grid:{left:40,right:20,top:18,bottom:30},tooltip:{trigger:'axis'},xAxis:{type:'category',data:['<-7%','-7~-3%','-3~0%','平盘','0~3%','3~7%','≥7%']},yAxis:{type:'value',minInterval:1},series:[{type:'bar',label:{show:true,position:'top',fontSize:10,color:'#58675d'},data:stats.distribution.map((v,i)=>({value:v,itemStyle:{color:i<3?'#6da58f':i>3?'#ce887e':'#b9bcb3'}}))}]}}/>:<Empty title="暂无涨跌分布" text="等待该日市场广度快照。"/>}</section><NotesDeck notes={notes}/></div>
 <p className="footnote">指数 {market?.indices_meta?.source} · {timeText(market?.indices_meta?.source_time)} / 广度 {market?.meta.source} · {timeText(market?.meta.source_time)} · 情绪池源 {timeText(market?.emotion_meta.source_time)} / 采集 {timeText(market?.emotion_meta.collected_at)} · 收盘报价覆盖 {stats?.quote_coverage==null?'未知':`${fmt(stats.quote_coverage*100,1)}%`} · 有效交易占比 {stats?.coverage==null?'未知':`${fmt(stats.coverage*100,1)}%`}</p>
 </section>
 <section id="sectors" className="chapter-block"><Chapter title="板块强弱" copy={['查看成交集中的位置，','用成分广度和历史排名核对热点与退潮。']}/>
 <div className="toolbar"><Toggle options={[["industry","一级行业"],["concept","同花顺概念"]]} value={kind} onChange={v=>{setData(null);setMarket(null);setKind(v);setConceptSource('ths');setSelected(null);}}/><div className="toolbar-right"><Input allowClear className="board-search" aria-label="搜索板块" placeholder="搜索板块或代码" value={query} onChange={e=>setQuery(e.target.value)}/></div></div>
 <div className="source-line">{kind==='concept'&&conceptSource==='eastmoney'&&<span>正在查看历史东财概念 <button className="text-button" onClick={()=>{setConceptSource('ths');setSelected(null);}}>返回同花顺</button></span>}<span>{data?.meta.source||(kind==='concept'?'同花顺概念':'东方财富行业')} {data?.meta.revision&&`/ 修订 ${data.meta.revision}`}</span><span>行情源 {timeText(data?.meta.source_time)}<span className="separator">/</span>采集 {timeText(data?.meta.collected_at)}<button className="text-button" onClick={()=>setShowHealth(v=>!v)}>数据状态 {failures.length>0&&<b className="warning-dot">{failures.length}</b>}</button></span></div>
 {kind==='concept'&&conceptSource==='ths'&&data?.quote_coverage!=null&&data.quote_coverage<1&&<div className="notice">同花顺本轮报价覆盖 {fmt(data.quote_coverage*100,1)}%；缺失或未复核行情已单独标记，成分采集中。</div>}{(error||hasIndexOnly||!data?.rows.length)&&<div className="notice">{error|| (hasIndexOnly?'仅指数历史：此日期未保存成分、资金及情绪快照，相关指标留空。':loading?'正在读取本地数据…':'所选日期暂无收盘快照。可切换历史日期，或展开数据状态查看盘后采集结果。')}</div>}
 {data?.meta.ingestion_method==='manual_browser'&&<div className="notice">当前为浏览器公开响应导入的真实快照，自动更新尚未验证。日线覆盖 {fmt((data.history_coverage??0)*100,1)}%；成分与市场数据缺失时不计算热点强度。<button className="text-button" onClick={()=>setShowHealth(true)}>查看采集状态</button></div>}
 {hasData&&!hasIndexOnly&&<p className="footnote">热点评分可用 {data?.rows.filter(r=>r.heat!=null).length} / {data?.rows.length} 个；成分待核验 {data?.rows.filter(r=>r.membership_verified===false).length} 个。缺失历史或成分的板块不评分；当前标签仅覆盖可评分样本。</p>}
 {showHealth&&<section className="health panel"><h3>数据采集状态</h3><p>仅盘后采集 · 交易日 15:30 归档、16:30 复核 · 服务{status?.collecting?'运行中':'未启动采集'}。失败后按 5 分钟起退避重试；页面手动刷新仅读取本地缓存。</p><div className="health-scroll">{status?.events.length?status.events.map(e=><div className="health-row" key={e.job}><span className={e.ok&&!e.stale?'down':'warning'}>{e.ok?e.stale?'滞后':'完成':'失败'}</span><code>{e.job}</code><time>{timeText(e.time)}</time><span>{e.message}</span></div>):<p>尚无采集记录。</p>}</div></section>}
 <div className="main-grid grid-flow-dense"><section className="panel panorama"><Heading title="板块全景" subtitle="面积 = 成交额 · 颜色 = 区间涨跌幅"><Toggle value={period} onChange={setPeriod} options={['1','5','10','20'].map(p=>[p,`${p}日`])}/></Heading>{rows.some(r=>r.amount!=null&&r.amount>0)?<Chart option={heatOption} height={340} onClick={pick}/>:filterHint||<Empty title="板块热力图等待数据" text="采集到真实板块成交额后生成；点击板块可展开分析。"/>}<div className="legend"><span><i style={{background:'#78ac9d'}}/>弱</span><i className="legend-neutral"/><span><i style={{background:'#d9968e'}}/>强</span><span className="muted">灰色为区间数据不足</span></div></section>
 <section className="panel focus"><Heading title="热点与冷点" subtitle="标签是观察依据，不是走势预测。"/><div className="signal-accordion" aria-label="热点与冷点分类">{[['hot','热点'],['cold','冷门低关注'],['ice','退潮冰点']].map(([id,label])=><div key={id} className={'signal-slice'+(ranking===id?' expanded':'')}>
 <SignalTab id={id} label={label} active={ranking===id} count={data?.rows.length?rows.filter(r=>r.labels.includes(id)).length:null} select={setRanking}/>
 <div id={'signal-'+id} hidden={ranking!==id}>{ranking===id&&<div className="focus-list">{ranked.length?focusPage.visible.map((r,i)=><button className="focus-row" key={r.code} onClick={()=>setSelected(r.code)}><span className="rank-number">{String(focusPage.offset+i+1).padStart(2,'0')}</span><div><strong>{r.name}</strong><small>{ranking==='hot'?`强度 ${fmt(r.heat,1)} · 上涨占比 ${fmt(r.up_ratio,0)}%`:ranking==='cold'?'低成交关注 · 持续落后':`距高点 ${pct(r.drawdown)}`}</small></div><b className={color(r.change)}>{pct(r.change)}</b></button>):filterHint||<Empty title={hasData?(ranking==='hot'&&!rows.some(r=>r.heat!=null)?'热点评分输入待补齐':'暂无满足条件的板块'):'等待真实板块数据'} text={ranking==='cold'?'需要 60 个交易日成交占比与连续排名。数据不足时不贴标签。':'必要输入完整后才计算标签；无候选时不凑数。'}/>}<Pager {...focusPage} label={label}/></div>}</div></div>)}</div><button className="rules-link" onClick={()=>setShowRules(v=>!v)}>查看评分与标签口径 <span>↗</span></button></section></div>
 {showRules&&<section className="panel rule-panel"><h3>可解释的观察规则 · {data?.meta.rule_version||'sector-v1.0'}</h3><div><p><b>热点</b> 同类板块当日涨幅百分位 40% + 此前 5 个完整交易日涨幅百分位 30% + 成分上涨比例百分位 30%；强度前 20%且当日上涨。</p><p><b>冷门</b> 连续 5 个收盘日涨幅后 30%，成交额占比处于自身 60 日后 20%。</p><p><b>冰点候选</b> 当日涨幅后 20%、上涨比例≤20%，且较此前 20 日最高收盘点位回撤≥5%。</p><p><b>缺失与并列</b> 不填零、不调整权重；百分位采用平均名次，并列全相同时为50。连续天数仅使用收盘数据。</p></div></section>}
 <section className="panel board-table"><Heading title="强弱观察列表" subtitle={`${rows.length} 个板块 · 点击行查看完整证据`}><label className="select-label">排序<Select aria-label="板块排序" value={sort} onChange={setSort} options={[{value:'heat',label:'热点强度'},{value:'change',label:'当日涨幅'},{value:'return_5',label:'5 日涨幅'},{value:'amount',label:'成交额'},{value:'up_ratio',label:'上涨比例'}]}/></label></Heading><div className="table-wrap"><table><thead><tr><th>板块 / 状态</th><th>当日</th><th>5日</th><th>10日</th><th>20日</th><th>强度</th><th>上涨比例</th><th>连续强势</th><th>近10日强势</th><th>排名变化</th><th>成分覆盖</th></tr></thead><tbody>{boardPage.visible.map(r=><tr key={r.code} onClick={()=>setSelected(r.code)}><td><button className="board-name" aria-label={'查看'+r.name+'板块详情'} onClick={e=>{e.stopPropagation();setSelected(r.code);}}>{r.name}</button><div className="tags">{r.labels.map(l=><span className={`tag ${l}`} key={l}>{labelNames[l]}</span>)}{!r.labels.length&&<small>{r.code}</small>}</div></td>{[r.change,r.return_5,r.return_10,r.return_20].map((v,i)=><td key={i} className={color(v)}>{pct(v)}</td>)}<td>{fmt(r.heat,1)}</td><td>{r.up_ratio==null?'—':`${fmt(r.up_ratio,0)}%`}</td><td>{fmt(r.streak,0)}</td><td>{fmt(r.top_days_10,0)} / 10</td><td className={color(r.rank_change)}>{r.rank_change==null?'—':`${r.rank_change>0?'↑':r.rank_change<0?'↓':'·'} ${Math.abs(r.rank_change)}`}</td><td>{r.membership_verified===false?'待核验':r.coverage==null?'—':`${fmt(r.coverage*100,0)}%`}</td></tr>)}</tbody></table>{!filtered.length&&(filterHint||<Empty title="所选日期暂无记录"/>)}</div><Pager {...boardPage} label="板块"/></section>

 </section>
 <section className="chapter-block"><Chapter title="持续性与资金" copy={['拉长到二十个交易日看轮动，','再对照成交占比和资金方向。']}/>
 <div className="lower-grid grid-flow-dense"><section className="panel" id="rotation"><Heading title="持续性与轮动" subtitle="最近 20 个交易日 · 当前排序前 14 个板块"/>{matrixRows.some(r=>r.matrix.some(m=>m.change!=null))?<Chart option={matrixOption} height={44+24*matrixRows.length} onClick={pick}/>:filterHint||<Empty title="轮动矩阵等待历史回补" text="指数日线目标回补 250 个交易日；缺少的日期保留空格。"/>}<p className="footnote">强势次数与连续天数依据同类板块每日涨幅前 20%，仅使用已完成的收盘数据。</p></section>
 <CapitalFlow rows={rows} sourceTime={timeText(data?.components?.flow.source_time)} ths={kind==='concept'&&conceptSource==='ths'} filterHint={filterHint} onSelect={setSelected}/>
 <section id="turnover" className="panel turnover-panel"><Heading title="成交额聚焦" subtitle="按市场成交额占比排序 · 变化单位为百分点"/><div className="share-section">{amountLeaders.length?amountLeaders.map(r=><button key={r.code} onClick={()=>setSelected(r.code)}><span>{r.name}</span><b>{fmt(r.amount_share)}%</b><small className={color(r.share_change)}>{r.share_change==null?'—':`${r.share_change>0?'+':''}${fmt(r.share_change)}pp`}</small></button>):filterHint||<p className="muted">等待完整成分与同日市场成交额，计算占比。</p>}</div></section></div>
 <EtfTracker date={date} refresh={refresh}/>
 </section>
 <section className="chapter-block"><Chapter title="个股与披露" copy={['从收盘涨幅榜选股，','按同一日期查上榜原因、席位与相关新闻。']}/>
 <Gainers onStock={setStockPick} onNews={()=>{setNewsJump(null);setNewsDate(date);location.hash='news';}} date={date} refresh={refresh} kind={kind} conceptSource={conceptSource} boards={data?.rows||[]} onBoard={(code,k,revision,source)=>{if(k==='concept')setConceptSource(source);if(k!==kind){setData(null);setMarket(null);setKind(k);}setDetailRevision(revision);setSelectedRaw(code);}}/>
 </section>
 <EndAction title="继续核对新闻" description="先查领涨个股的上涨依据，再核对由同一事件带动的股票。" action={{label:'按本日涨幅榜查新闻',href:'#news',run:openNews}}/>
 <SourceStrip/>
 <footer><p>{kind==='concept'?'概念板块成分重叠，成交额及资金流不可跨板块相加；旧东财归档与同花顺概念分别保存。':'一级行业目录使用东方财富原始口径；历史排名基于当前目录中的指数，成分广度统计沪深非ST有效交易股票。'}</p><span>本地研究 · 无交易执行 · {data?.meta.rule_version||'sector-v1.0'}</span></footer>
 </main>
 {newsView&&<News key={newsJump?.id+':'+newsJump?.asof} initialOpening={newsJump} onStock={setStockPick} initialDate={newsDate} onBoard={(b,k,d,revision,source)=>{setDate(d);setKind(k);if(k==='concept')setConceptSource(source);setDetailRevision(revision);setSelectedRaw(b.code);}}/>}
 {stockPick&&<LhbDrawer pick={stockPick} close={()=>setStockPick(null)} onEvent={openEvent}/>}
 {selected&&<DetailDrawer title="板块详情" label="板块详情" closeLabel="关闭详情" size={750} onClose={()=>setSelected(null)}>{detail?<><div className="detail-title"><div><span className="eyebrow">{detail.sector.code} · {detail.meta.trade_date}</span><h2>{detail.sector.name}</h2><div className="tags">{detail.sector.labels.map(l=><span className={`tag ${l}`} key={l}>{labelNames[l]}</span>)}</div></div><strong className={color(detail.sector.change)}>{pct(detail.sector.change)}</strong></div><p className="footnote">源 {timeText(detail.meta.source_time)} · 修订 {detail.meta.revision??'仅指数历史'}</p><div className="detail-metrics"><div><span>热点强度</span><b>{fmt(detail.sector.heat,1)}</b></div><div><span>上涨比例</span><b>{detail.sector.up_ratio==null?'—':`${fmt(detail.sector.up_ratio,0)}%`}</b></div><div><span>距20日高点</span><b className={color(detail.sector.drawdown)}>{pct(detail.sector.drawdown)}</b></div><div><span>有效 / 成分</span><b>{fmt(detail.sector.eligible_count,0)} / {fmt(detail.sector.member_count,0)}</b></div></div>
 <h3>指数走势 <small>供应商指数 · 不复权</small></h3>{detail.bars.length?<Chart height={230} option={{grid:{left:55,right:15,top:20,bottom:28},tooltip:{trigger:'axis'},xAxis:{type:'category',data:detail.bars.map(b=>b.date),axisLabel:{color:'#8c8f86'}},yAxis:{type:'value',scale:true,splitLine:{lineStyle:{color:'#eeefea'}}},series:[{name:'收盘点位',type:'line',showSymbol:false,data:detail.bars.map(b=>b.close),lineStyle:{color:'#535e50',width:2}}]}}/>:<Empty title="该板块日线尚未回补"/>}
 <p className="footnote">供应商原始成分计数：上涨 {fmt(detail.sector.vendor_up,0)} / 下跌 {fmt(detail.sector.vendor_down,0)} / 平盘 {fmt(detail.sector.vendor_flat,0)}。未经沪深、ST 与有效报价筛选，不代替评分广度。</p><h3>规则与证据</h3><div className="evidence">{detail.sector.rule_evidence.length?detail.sector.rule_evidence.map(t=><p key={t}>{t}</p>):<p>当前未满足标签条件，或必要数据不足。</p>}<p>评分输入百分位：当日 {fmt(detail.sector.heat_inputs.daily,1)} / 前5日 {fmt(detail.sector.heat_inputs.prior_5,1)} / 广度 {fmt(detail.sector.heat_inputs.breadth,1)}</p><p>成分覆盖 {detail.sector.coverage==null?'未知':`${fmt(detail.sector.coverage*100,0)}%`} · 连续强势 {fmt(detail.sector.streak,0)} 日 · 近10日强势 {fmt(detail.sector.top_days_10,0)} 次</p></div>
 <h3>资金趋势 <small>已存档日数据</small></h3><FlowPeriods flow={detail.sector.flow}/>{detail.flow_history.length?<Chart height={190} option={{grid:{left:55,right:15,top:20,bottom:25},tooltip:{trigger:'axis'},xAxis:{type:'category',data:detail.flow_history.map(f=>f.date)},yAxis:{type:'value',name:'亿元'},series:[{name:'主力净流入',type:'bar',data:detail.flow_history.map(f=>({value:f.today==null?null:f.today/1e8,itemStyle:{color:(f.today||0)>0?'#c66b62':'#5e9d88'}}))}]}}/>:<p className="empty-inline">此日期没有可用的资金历史快照。</p>}
 <h3>成分涨跌分布</h3>{detail.membership_meta&&<p className="footnote">归属来源 {detail.membership_meta.source} · 源时间未知 · 采集 {timeText(detail.membership_meta.captured_at)}。{detail.membership_meta.verified?'按当日目录数量核验；归属接口无历史日期，保留采集版本。':`成分待核验：返回 ${detail.membership_meta.reported_count} 只，目录 ${detail.membership_meta.expected_count??'未知'} 只；不参与当日评分和个股归属。`}</p>}<p className="footnote">成分行情源 {timeText(detail.components?.stocks.source_time)} · 仅同日收盘行情参与评分。</p>{detail.members_available&&detail.members.length?<Chart height={160} option={{grid:{left:35,right:15,top:20,bottom:25},xAxis:{type:'category',data:['≤-7','-7~-3','-3~0','平盘','0~3','3~7','≥7']},yAxis:{type:'value',minInterval:1},series:[{type:'bar',label:{show:true,position:'top',fontSize:10,color:'#58675d'},data:[[-100,-7],[-7,-3],[-3,0],[0,0.00000001],[0.00000001,3],[3,7],[7,1000]].map(([a,b],i)=>({value:detail.members.filter(s=>s.change!=null&&s.change>=a&&s.change<b&&s.amount!=null&&s.amount>0&&s.price!=null&&s.price>0&&!s.name.toUpperCase().includes('ST')).length,itemStyle:{color:i<3?'#6da58f':i>3?'#ce887e':'#b9bcb3'}}))}]}}/>:<p className="empty-inline">当日成分与行情未完整存档。</p>}
 <h3>涨停梯队</h3><div className="ladder">{detail.limit_up?.length?[...detail.limit_up].sort((a,b)=>(b.boards||0)-(a.boards||0)).map(r=><span key={r.code}><b>{fmt(r.boards,0)}板</b>{r.name}</span>):<p className="muted">{detail.limit_up===null?'该日涨停梯队数据不可用':'当前板块无收录的涨停股'}</p>}</div>
 <div className="stock-title"><h3>代表股与成分</h3><Select aria-label="成分股排序" value={stockSort} onChange={setStockSort} options={[{value:'change',label:'按涨幅'},{value:'amount',label:'按成交额'},{value:'boards',label:'按连板高度'}]}/></div><div className="table-wrap"><table><thead><tr><th>股票</th><th>涨跌幅</th><th>成交额</th><th>连板</th></tr></thead><tbody>{[...detail.members].sort((a,b)=>{const av=a[stockSort as keyof Stock] as Nullable;const bv=b[stockSort as keyof Stock] as Nullable;return av==null?bv==null?0:1:bv==null?-1:bv-av;}).slice(0,50).map(r=><tr key={r.code}><td><strong>{r.name}</strong><small>{r.code}</small></td><td className={color(r.change)}>{pct(r.change)}</td><td>{amt(r.amount)}</td><td>{fmt(r.boards,0)}</td></tr>)}</tbody></table></div><p className="footnote">展示排序前50只。名单与行情来自当日快照，ST可在名单中查看，未参与广度指标。</p></>:<Empty title={detailError||'正在读取板块详情'} text="仅使用所选日期与修订的数据。"/>}</DetailDrawer>}
 </>;
}
createRoot(document.getElementById('root')!).render(<DeskProvider><App/></DeskProvider>);
