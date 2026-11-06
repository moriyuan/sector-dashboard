import {useState} from 'react';
import {Alert,Button,Empty,Select,Table,Tag} from 'antd';
import type {StockPick} from './Lhb';
export type RiskEvent={id:string;code:string;name:string;kind:string;kind_name:string;action:string;title:string;url:string;published_day:string;source:string;source_name:string;tier:string;start:string|null;end:string|null;excerpt:string;captured_at:string;status:string;backfilled:boolean;events:RiskEvent[]};
export type Regulation={rows:RiskEvent[];documents:{source:string;latest:string;documents:number}[];coverage:string;day:string};
const sourceNames:Record<string,string>={'risk:sse':'上交所','risk:szse':'深交所','risk:bse':'北交所','risk:hx':'华西证券转述','risk:issuer':'上市公司公告'};
export default function RegulationPanel({data,sources,day,query,onStock}:{data?:Regulation;sources:Record<string,{ok:boolean;last_success?:string;last_attempt?:string;error?:string}>;day:string;query:string;onStock:(s:StockPick)=>void}){
 const [kind,setKind]=useState('all');const rows=(data?.rows||[]).filter(r=>(kind==='all'||r.kind===kind)&&(!query||`${r.code} ${r.name}`.includes(query.trim())));
 return <section className="panel"><div className="section-head"><div><h3>交易所风险监控 · 买入前核对</h3><p>“小黑屋”是市场俗称。重点监控、严重异常波动、停牌核查分别列示，不互相替代。</p></div><Select aria-label="监管风险类型" value={kind} onChange={setKind} options={[{value:'all',label:'全部风险类型'},{value:'monitor',label:'重点监控'},{value:'severe',label:'严重异常波动'},{value:'suspension',label:'停牌核查'},{value:'stricter',label:'从严交易监管提示'},{value:'discipline',label:'交易监管措施'}]}/></div>
 <Alert type="warning" showIcon title="未列入不等于无风险；到期不等于已解除。" description={data?.coverage||'尚未采集监管披露，不能判断是否存在监控股票。'}/>
 <div className="daily-risk-sources">{Object.entries(sourceNames).map(([key,name])=>{const state=sources[key];const doc=data?.documents.find(d=>'risk:'+d.source===key);return <div key={key}><b>{name}</b><span>{!state?'未检查':state.ok?'本次索引已读取':'更新异常 / 覆盖缺口'}</span><small>最近披露 {doc?.latest||'未知'} · 已读 {doc?.documents??0} 份</small></div>;})}</div>
 <p className="footnote">按截至 {day} 的公开披露汇总。仅记录来源明示的监控起止日，不推算“出小黑屋”日期。券商转述不冒充交易所原文；事后采集的历史记录会单独标记。</p>
 <Table<RiskEvent> rowKey={r=>r.code+':'+r.kind} dataSource={rows} size="small" scroll={{x:1100}} pagination={{pageSize:15,showSizeChanger:false}} locale={{emptyText:<Empty description="已收录范围内暂无匹配记录；不代表交易所完整名单为空"/>}} columns={[
  {title:'股票',key:'stock',render:(_,r)=><Button type="link" onClick={()=>onStock({date:day,code:r.code,name:r.name})}>{r.name}<small>{r.code}</small></Button>},
  {title:'风险类别',dataIndex:'kind_name',render:v=><Tag color="volcano">{v}</Tag>},
  {title:'最近披露',dataIndex:'published_day'},
  {title:'披露监控期间',key:'period',render:(_,r)=><span>{r.start||'未披露'} → {r.end||'未披露'}</span>},
  {title:'状态与边界',key:'status',render:(_,r)=><div className="daily-risk-status"><strong>{r.status}</strong>{r.backfilled&&<Tag>事后采集</Tag>}</div>},
  {title:'证据来源',key:'source',render:(_,r)=><div><a href={r.url} target="_blank" rel="noreferrer">{r.source_name} ↗</a><small className="muted">{r.tier==='exchange'?'交易所原文':r.tier==='broker'?'券商官方转述':'上市公司披露索引'}</small></div>}
 ]} expandable={{expandedRowRender:r=><div className="daily-risk-evidence">{r.events.map(e=><article key={e.id}><b>{e.published_day} · {e.source_name}</b><p>{e.excerpt}</p><a href={e.url} target="_blank" rel="noreferrer">{e.title} ↗</a><small>采集 {e.captured_at.replace('T',' ').slice(0,19)}</small></article>)}</div>}}/>
 </section>;
}
