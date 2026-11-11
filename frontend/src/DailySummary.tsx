import {useEffect,useState} from 'react';
import {Alert,Button,DatePicker,Empty,Input,Select,Spin,Table,Tabs,Tag} from 'antd';
import dayjs from 'dayjs';
import {DetailDrawer} from './DeskUI';
import type {StockPick} from './Lhb';
import './daily-summary.css';
import RegulationPanel,{type Regulation} from './RegulationPanel';

type Metric={id:string;label:string;value:number|null;unit:string;currency?:string|null;n?:number};
type Industry={name:string;count:number;period:string;metrics:Metric[];url:string;cutoff_notice_date:string};
type Notice={id:string;code:string;name:string;title:string;kind:string;period:string|null;day:string;notice_date:string;published_at:string|null;date_basis:string;url:string;captured_at:string;status:string;industry:Industry|null;metrics:Metric[]};
type Event={time:string;event:string;detail:string;source_date:string|null};
type Anomaly={source:string;code:string;name:string;first:string;last:string;count:number;types:{name:string;count:number}[];events:Event[]};
type Source={ok:boolean;last_success?:string;last_attempt?:string;coverage?:string;count?:number;error?:string};
type Listing={day:string;regulation?:Regulation;notices:Notice[];anomalies:Anomaly[];sources:Record<string,Source>;budget:{spent_or_reserved:number;limit_usd:number};model:{model_configured:boolean;heartbeat:string}|null};
type Detail={notice:Notice;captured_at:string;status:string;financials_captured_at?:string;financials:{metrics:Metric[];industry:Industry|null;basis:string;backfilled:boolean;currency:string|null;url:string}|null;analysis:{model:string;prompt_version:string;review:string;sections:{key:string;kind:string;text:string;evidence_ids:string[]}[];input_evidence:{id:string;label?:string;text?:string;value?:number;unit?:string;currency?:string;url:string}[]}|null;analyzed_at?:string};
const statusNames:Record<string,string>={pending:'等待分析',awaiting_financials:'等待财务指标入库',collection_failed:'财务采集待重试',non_report:'预告 / 快报，未作正式财报分析',inflight:'正在分析',analyzed:'分析已归档',budget_paused:'今日预算用尽',analysis_failed:'分析失败 / 未通过核验',insufficient_evidence:'证据不足'};
const names:Record<string,string>={overview:'核心判断',growth:'增长与盈利',quality:'现金流与盈利质量',industry:'行业比较',risks:'风险与不确定性',watch:'后续核验重点'};
const format=(m:Metric)=>{if(m.value==null)return '—';const money=m.unit==='元';const v=money&&Math.abs(m.value)>=1e8?m.value/1e8:m.value;return v.toLocaleString('zh-CN',{maximumFractionDigits:2})+(money&&Math.abs(m.value)>=1e8?'亿':m.unit)+(m.currency&&m.currency!=='CNY'?` (${m.currency})`:'');};
const stamp=(v?:string|null)=>v?v.replace('T',' ').slice(0,19):'未知';
const today=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
async function read<T>(url:string,signal:AbortSignal):Promise<T>{const res=await fetch(url,{signal});if(!res.ok)throw Error(`读取失败（${res.status}）`);return res.json();}
function Metrics({rows}:{rows:Metric[]}){return <div className="daily-metrics">{rows.map(m=><div key={m.id}><span>{m.label}</span><strong>{format(m)}</strong>{m.n!=null&&<small>有效同业样本 {m.n} 家</small>}</div>)}</div>;}

export default function DailySummary({onStock}:{onStock:(stock:StockPick)=>void}){
 const [day,setDay]=useState(today),[data,setData]=useState<Listing|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(false),[refresh,setRefresh]=useState(0);
 const [source,setSource]=useState('eastmoney'),[query,setQuery]=useState(''),[selected,setSelected]=useState<string|null>(null),[detail,setDetail]=useState<Detail|null>(null),[detailError,setDetailError]=useState('');
 useEffect(()=>{const abort=new AbortController();setLoading(true);setData(null);setError('');
 read<Listing>(`api/daily?day=${day}`,abort.signal).then(setData).catch(e=>{if(!abort.signal.aborted)setError(e.message);}).finally(()=>{if(!abort.signal.aborted)setLoading(false);});return()=>abort.abort();},[day,refresh]);
 useEffect(()=>{setSelected(null);},[day]);
 useEffect(()=>{const timer=setInterval(()=>setRefresh(v=>v+1),60000);return()=>clearInterval(timer);},[]);
 useEffect(()=>{setDetail(null);setDetailError('');if(!selected)return;const abort=new AbortController();read<Detail>(`api/daily/reports/${encodeURIComponent(selected)}`,abort.signal).then(setDetail).catch(e=>{if(!abort.signal.aborted)setDetailError(e.message);});return()=>abort.abort();},[selected]);
 const match=(r:{code:string;name:string})=>!query||`${r.name} ${r.code}`.includes(query.trim());
 const anomalies=(data?.anomalies||[]).filter(r=>r.source===source&&match(r));const notices=(data?.notices||[]).filter(match);
 const stock=(r:{code:string;name:string})=><Button type="link" onClick={()=>{setSelected(null);onStock({date:day,code:r.code,name:r.name});}}>{r.name}<small>{r.code}</small></Button>;
 return <main id="daily" className="research-page daily-page">
  <div className="section-head"><div><span className="eyebrow">每日观察与披露档案</span><h1>每日汇总</h1><p>从盘中被监测到的异动，看到当日发布的财报与财务研究。</p></div><div className="daily-tools"><DatePicker aria-label="汇总日期" value={dayjs(day)} allowClear={false} onChange={v=>v&&setDay(v.format('YYYY-MM-DD'))}/><Button onClick={()=>setDay(today())}>今天</Button><Button loading={loading} onClick={()=>setRefresh(v=>v+1)}>刷新归档</Button></div></div>
  <div className="daily-status"><span>北京时间 · {day}</span><span>异动 {new Set(anomalies.map(r=>r.code)).size} 只</span><span>财报 / 业绩披露 {new Set(notices.map(r=>r.code)).size} 家 · {notices.length} 份</span><span>共享模型预算 ${data?.budget.spent_or_reserved.toFixed(4)??'—'} / ${data?.budget.limit_usd??1}</span><span>{data?.model?.heartbeat&&Date.now()-Date.parse(data.model.heartbeat)<180000?(data.model.model_configured?'DeepSeek 已配置':'模型未配置'):'后台暂停或状态过期 · 可读存档'}</span></div>
  <p className="footnote">刷新只读取归档，不触发采集或模型付费请求。股票详情使用所选日期行情；采集失败保留已存档内容。</p>
  <Input.Search className="daily-search" aria-label="查找汇总股票" placeholder="搜索股票名称或代码" value={query} onChange={e=>setQuery(e.target.value)} allowClear/>
  {error&&<Alert type="error" title={error} showIcon/>}
  <Spin spinning={loading}><Tabs items={[
   {key:'regulation',label:'交易所风险监控',children:<RegulationPanel data={data?.regulation} sources={data?.sources||{}} day={day} query={query} onStock={onStock}/>},
   {key:'anomalies',label:'异动股票',children:<section className="panel"><div className="section-head"><div><h3>被监测到的股票</h3><p>按股票去重，保留首末时间、事件类型和完整已采集记录。异动次数不代表独立资金或买入信号。</p></div><Select aria-label="异动来源" value={source} onChange={setSource} options={[{value:'eastmoney',label:'东方财富 · 自动采集'},{value:'ths',label:'同花顺 · 实录导入'}]}/></div>
    <Alert type="info" showIcon title={source==='eastmoney'?'源接口未提供交易日期：以下按采集日归档，不能保证全部属于当日或覆盖全天。':'同花顺实录独立展示，不与东方财富口径合并；尚未导入时留空。'}/>
    <p className="footnote">最近成功：{stamp(data?.sources[source]?.last_success)} · {data?.sources[source]?.coverage||'尚无采集覆盖信息'}{data?.sources[source]?.ok===false&&' · 更新失败，显示旧归档'}</p>
    <Table<Anomaly> rowKey={r=>r.source+r.code} dataSource={anomalies} size="small" pagination={{pageSize:20,showSizeChanger:false}} scroll={{x:850}} locale={{emptyText:<Empty description="所选日期尚无该来源的异动实录"/>}} columns={[
     {title:'股票',key:'stock',render:(_,r)=>stock(r)}, {title:'首次记录',dataIndex:'first'}, {title:'最后记录',dataIndex:'last'}, {title:'记录数',dataIndex:'count',sorter:(a,b)=>a.count-b.count},
     {title:'异动类型',key:'types',render:(_,r)=><div className="daily-tags">{r.types.map(t=><Tag key={t.name}>{t.name} {t.count}</Tag>)}</div>}
    ]} expandable={{expandedRowRender:r=><div className="daily-events">{r.events.map((e,i)=><div key={i}><time>{e.time}</time><b>{e.event}</b><code>{e.detail||'未提供附加信息'}</code></div>)}<p className="footnote">附加信息为供应商原始字段；未核验单位的数值不作金额或涨幅解释。</p></div>}}/>
   </section>},
   {key:'reports',label:'财报与研究',children:<section className="panel"><div className="section-head"><div><h3>当日发布的财报与业绩公告</h3><p>优先按实际发布时间归档。财报、快报和预告分开标记；分红公告与董事会决议不计入。</p></div></div>
    <p className="footnote">公告最近成功：{stamp(data?.sources.announcements?.last_success)}{data?.sources.announcements?.ok===false?' · 更新失败，可能漏采':data?.sources.announcements?.ok?' · 已完成本次分页扫描':' · 尚未采集'}</p>
    <Table<Notice> rowKey="id" dataSource={notices} size="small" pagination={{pageSize:15,showSizeChanger:false}} scroll={{x:1100}} locale={{emptyText:<Empty description={data?.sources.announcements?.ok?'本次扫描未发现所选日期发布的财报或业绩公告':'公告尚未采集，不能判断今日是否有财报'}/>}} columns={[
     {title:'股票',key:'stock',render:(_,r)=>stock(r)}, {title:'公告 / 报告期',key:'title',render:(_,r)=><div className="daily-report-title"><Button type="link" onClick={()=>setSelected(r.id)}>{r.title}</Button><small>{r.period||'报告期待核验'} · {r.kind==='report'?'正式报告':r.kind==='forecast'?'业绩预告':'业绩快报'}</small></div>},
     {title:'行业',key:'industry',render:(_,r)=>r.industry?.name||'未核验'}, {title:'营业总收入',key:'revenue',render:(_,r)=>format(r.metrics.find(m=>m.id==='TOTALOPERATEREVE')||{id:'',label:'',value:null,unit:'元'})},
     {title:'归母净利润',key:'profit',render:(_,r)=>format(r.metrics.find(m=>m.id==='PARENTNETPROFIT')||{id:'',label:'',value:null,unit:'元'})},
     {title:'研究状态',key:'status',render:(_,r)=><Button onClick={()=>setSelected(r.id)}>{statusNames[r.status]||r.status}</Button>}
    ]}/>
   </section>}
  ]}/></Spin>
  {selected&&<DetailDrawer title="财报研究" label="财报研究详情" closeLabel="关闭财报研究" size={960} onClose={()=>setSelected(null)}>
   {detailError?<Alert type="error" title={detailError}/>:!detail?<Spin/>:<><h2>{detail.notice.title}</h2><div className="daily-tools">{stock(detail.notice)}<a href={detail.notice.url} target="_blank" rel="noreferrer">查看公告原文 ↗</a></div>
    <p className="footnote">报告期 {detail.notice.period||'未知'} · 实际发布 {stamp(detail.notice.published_at)} · 公告日期 {detail.notice.notice_date}<br/>首次采集 {stamp(detail.captured_at)} · {detail.notice.date_basis}</p>
    <h3>财务数据</h3><p className="footnote">收入、利润、现金流为报告期年初至今累计值；资产负债为期末值；单季指标单独标明。空值保留“—”。币种 {detail.financials?.currency||'未知'}。</p>
    {detail.financials?<><Metrics rows={detail.financials.metrics}/><p className="footnote">{detail.financials.basis} 数据采集 {stamp(detail.financials_captured_at)} · <a href={detail.financials.url} target="_blank" rel="noreferrer">指标来源 ↗</a></p>
    {detail.financials.industry&&<><h3>同报告期行业对照 · {detail.financials.industry.name}</h3><p className="footnote">不含本公司，共 {detail.financials.industry.count} 家已披露样本，非全行业。各指标有效样本少于三家时不算中位数；公告日期截止 {detail.financials.industry.cutoff_notice_date}。</p><Metrics rows={detail.financials.industry.metrics.map(m=>({...m,label:m.label+'中位数'}))}/></>}</>:<Empty description="同报告期财务指标尚不可用；不会用上一期数据替代"/>}
    <h3>DeepSeek 研究</h3>{detail.analysis?<><p className="footnote">{detail.analysis.model} · {detail.analysis.prompt_version} · {stamp(detail.analyzed_at)}<br/>{detail.analysis.review}</p>{detail.analysis.sections.map(s=><section className="daily-analysis" key={s.key}><h4>{names[s.key]} <Tag>{s.kind==='fact'?'数据观察':s.kind==='inference'?'研究推导':'证据限制'}</Tag></h4><p>{s.text}</p><div className="daily-evidence">{s.evidence_ids.map(id=>{const e=detail.analysis!.input_evidence.find(e=>e.id===id);return e?<a key={id} href={e.url} target="_blank" rel="noreferrer">{e.label||e.text||id}{e.value!=null?' · '+format({id,label:'',value:e.value,unit:e.unit||'',currency:e.currency}):''} ↗</a>:null;})}</div></section>)}</>:<Alert type="info" showIcon title={statusNames[detail.status]||detail.status} description="分析由后台串行完成，与新闻共用每日一美元预算。缺少指标、未配置模型或输出核验失败时不发布结论。"/>}
   </>}
  </DetailDrawer>}
 </main>;
}
