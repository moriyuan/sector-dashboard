import {useEffect,useState} from 'react';
import {Alert,Button,Input,Select,Table} from 'antd';
import {DetailDrawer} from './DeskUI';
import './seat-stocks.css';

type Meta={archive_available:boolean;profiles:string[];note:string;stock_count:number;event_count:number;first_date:string|null;last_date:string|null};
type Stock={code:string;name:string;profiles:string[];seats:string[];first_date:string;last_date:string;disclosures:number};
type Event={id:string;code:string;name:string;date:string;period:string;profile:string;seat:string;reason:string;buy:number|null;sell:number|null;net:number|null;source:string;url:string;reviewed_at:string;evidence:{url:string;title:string;basis:string}[]};
type Listing={meta:Meta;rows:Stock[];total:number};
type Detail={meta:Meta;rows:Event[];total:number};
const periods=[{value:'all',label:'全部披露周期'},{value:'single',label:'单日榜'},{value:'three',label:'三日累计榜'},{value:'other',label:'其他周期'}];
const names:Record<string,string>={single:'单日榜',three:'三日累计榜',other:'其他周期'};
const amount=(v:number|null)=>v==null?'—':(v/10000).toLocaleString('zh-CN',{maximumFractionDigits:2});
function useRead<T>(url:string){
 const [state,set]=useState<{url:string;data?:T;error?:string}>({url:''});
 useEffect(()=>{if(!url)return;const control=new AbortController();fetch(url,{signal:control.signal}).then(async r=>{
  const data=await r.json();if(!r.ok)throw Error(data.detail||'读取存档失败');return data;
 }).then(data=>set({url,data})).catch(e=>{if(!control.signal.aborted)set({url,error:e.message});});return()=>control.abort();},[url]);
 return state.url===url?state:{url};
}

export default function SeatStocks(){
 const [profile,setProfile]=useState(''),[period,setPeriod]=useState('all'),[q,setQ]=useState(''),[page,setPage]=useState(1);
 const [pick,setPick]=useState<Stock|null>(null),[detailPage,setDetailPage]=useState(1),[refresh,setRefresh]=useState(0);
 const common=new URLSearchParams({profile,period,v:String(refresh)});
 const data=useRead<Listing>('api/seat-stocks?'+common+'&'+new URLSearchParams({q,page:String(page)}));
 const detail=useRead<Detail>(pick?'api/seat-stocks/'+pick.code+'?'+common+'&page='+detailPage:'');
 useEffect(()=>{window.scrollTo({top:0});},[]);
 const reset=()=>{setPage(1);setPick(null);};
 return <main id="seat-stocks" className="seat-stocks-page">
  <header className="section-head"><div><h1>游资参与股票</h1><p>从已收集的席位披露查股票、上榜日期和关联依据。</p></div><Button onClick={()=>setRefresh(v=>v+1)}>刷新存档</Button></header>
  <Alert type="info" showIcon title="保留完整参与名单" description="不按净买入、ST 状态或能否买入筛选。关联来自公开席位资料，不代表已确认实际账户身份；历史披露采用事后核验的关联资料，不代表当时已知。"/>
  {data.error&&<Alert type="error" title={data.error}/>}
  {data.data&&!data.data.meta.archive_available&&<Alert type="warning" title="历史名单尚未安装，当前仅显示现存龙虎榜记录"/>}
  {data.data&&<p className="footnote">{data.data.meta.first_date||'—'} — {data.data.meta.last_date||'—'} · {data.data.meta.profiles.length} 组关联游资 · {data.data.meta.stock_count.toLocaleString()} 只股票 · {data.data.meta.event_count.toLocaleString()} 条披露记录（含修订）</p>}
  <section className="panel"><div className="seat-stocks-tools">
   <Select aria-label="关联游资" showSearch optionFilterProp="label" value={profile} onChange={v=>{setProfile(v);reset();}} options={[{value:'',label:'全部游资'},...(data.data?.meta.profiles||[]).map(p=>({value:p,label:p}))]}/>
   <Select aria-label="披露周期" value={period} options={periods} onChange={v=>{setPeriod(v);reset();}}/>
   <Input.Search aria-label="搜索游资股票" placeholder="股票名称或代码" allowClear onSearch={v=>{setQ(v.trim());reset();}}/>
  </div><Table<Stock> aria-label="游资股票名单" rowKey="code" size="small" dataSource={data.data?.rows||[]} loading={!data.data&&!data.error} scroll={{x:900}}
   pagination={{current:page,pageSize:20,total:data.data?.total||0,showSizeChanger:false,onChange:setPage,showTotal:n=>`共 ${n} 只股票`}} columns={[
    {title:'股票',width:150,render:(_,r)=><Button type="link" onClick={()=>{setPick(r);setDetailPage(1);}}>{r.name}<small>{r.code}</small></Button>},
    {title:'关联游资',render:(_,r)=>r.profiles.join('、')},
    {title:'关联席位',render:(_,r)=><span title={r.seats.join('；')}>{r.seats.length} 个席位 · 点击股票查看</span>},
    {title:'首次收录披露日',dataIndex:'first_date'}, {title:'最近披露日',dataIndex:'last_date'},
    {title:'披露记录',dataIndex:'disclosures',align:'right'},
   ]}/><p className="footnote">按最近披露日排序，未对参与者做收益排名。名单包含已收集的沪深北股票及 B 股；可转债原始记录另存，不计入股票数量。单日、三日累计及多条上榜原因不能相加。</p></section>
  {pick&&<DetailDrawer title={pick.name+' · '+pick.code} label="游资参与披露详情" closeLabel="关闭游资股票详情" onClose={()=>setPick(null)} size={940}>
   {detail.error&&<Alert type="error" title={detail.error}/>}
   <Table<Event> aria-label="关联席位披露记录" rowKey="id" size="small" loading={!detail.data&&!detail.error} dataSource={detail.data?.rows||[]} scroll={{x:900}}
    pagination={{current:detailPage,pageSize:20,total:detail.data?.total||0,showSizeChanger:false,onChange:setDetailPage}} columns={[
     {title:'披露日',dataIndex:'date'},{title:'周期',render:(_,r)=>names[r.period]||r.period},{title:'关联游资',dataIndex:'profile'},
     {title:'席位',dataIndex:'seat',width:220}, {title:'上榜原因',dataIndex:'reason',width:200},
     {title:'买入（万元）',render:(_,r)=>amount(r.buy)},{title:'卖出（万元）',render:(_,r)=>amount(r.sell)},
     {title:'净买额（万元）',render:(_,r)=><span className={r.net==null?'muted':r.net>0?'up':r.net<0?'down':''}>{amount(r.net)}</span>},
    ]} expandable={{expandedRowRender:r=><div className="seat-stock-evidence"><p><a href={r.url} target="_blank" rel="noreferrer">{r.source} ↗</a> · 关联资料核验于 {r.reviewed_at?.slice(0,10)||'未知'}</p>{r.evidence?.map(e=><p key={e.url}><a href={e.url} target="_blank" rel="noreferrer">{e.title} ↗</a><br/>{e.basis}</p>)}</div>}}/>
   <p className="footnote">披露金额单位为万元，空缺保留为 —。同一记录的后续修订及不同上榜原因分别保留，不合并计算资金总量。</p>
  </DetailDrawer>}
 </main>;
}
