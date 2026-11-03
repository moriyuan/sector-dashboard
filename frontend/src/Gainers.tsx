import {Button,Checkbox,Input,Select} from 'antd';
import {useEffect,useState} from 'react';
import {Pager,usePages} from './ResearchLayout';
import {useLhbDay,LhbBoard,badge,type OpenStock} from './Lhb';

type Board={code:string;name:string};
type Row={code:string;name:string;price:number;change:number;amount:number;is_st:boolean;boards:number|null;limit_status:string;industry:Board[];concepts:Board[]};
type Ranking={concept_source?:string;input_refs?:{industry:number|null;concept:number|null};meta:{trade_date:string;source_time:string|null;source?:string;revision:number|null};rows:Row[];total:number;membership_counts:{industry:number;concept:number}|null};
const money=(n:number)=>(n/1e8).toFixed(2)+'亿';
export default function Gainers({date,refresh,kind,conceptSource,boards,onNews,onBoard,onStock}:{date:string;refresh:number;kind:string;conceptSource:string;boards:Board[];onNews:()=>void;onStock:OpenStock;onBoard:(code:string,kind:string,revision:number|null,source:string)=>void}){
 const lhb=useLhbDay(date,refresh);
 const [data,setData]=useState<Ranking|null>(null);const [error,setError]=useState('');const [query,setQuery]=useState('');const [includeST,setIncludeST]=useState(false);const [sector,setSector]=useState('');
 useEffect(()=>setSector(''),[kind,conceptSource,date]);
 useEffect(()=>{const controller=new AbortController();setData(null);setError('');const params=new URLSearchParams({limit:'100',include_st:String(includeST),q:query,...(date?{date}:{}),...(sector&&(kind!=='concept'||(conceptSource==='ths'?sector.startsWith('THS'):sector.startsWith('BK')))?{sector}:{})});fetch('api/gainers?'+params,{signal:controller.signal}).then(r=>{if(!r.ok)throw Error();return r.json();}).then(d=>{if(!controller.signal.aborted)setData(d);}).catch(e=>{if(e.name!=='AbortError')setError('个股榜暂不可用，请刷新重试。');});return()=>controller.abort();},[date,refresh,query,includeST,sector,kind,conceptSource]);
 const pages=usePages(data?.rows||[],10,[date,query,includeST,sector,kind,conceptSource].join('|'));
 const chip=(b:Board,k:string)=><button className="sector-chip" key={b.code} onClick={()=>onBoard(b.code,k,data?.input_refs?.[k as 'industry'|'concept']??null,data?.concept_source||'eastmoney')}>{b.name}</button>;
 return <><section id="gainers" className="panel gainers"><div className="section-head"><div><h2>个股涨幅榜</h2><p>沪深有效交易股票 · 按收盘涨幅排序 · 点击股票查看龙虎榜与新闻</p></div><Button onClick={onNews}>按本榜找新闻 ↗</Button></div>
 <div className="gainer-controls"><Input allowClear className="stock-search" aria-label="搜索个股" placeholder="股票名称或代码" value={query} onChange={e=>setQuery(e.target.value)}/>
 <Select className="stock-sector-filter" showSearch={{optionFilterProp:'label'}} disabled={kind==='concept'&&data?.concept_source!==conceptSource} aria-label="个股榜板块筛选"
   value={sector} onChange={setSector} options={[{value:'',label:kind==='industry'?'全部行业':'全部概念'},...boards.map(b=>({value:b.code,label:b.name}))]}/>
 <Checkbox checked={includeST} onChange={e=>setIncludeST(e.target.checked)}>包含 ST</Checkbox><span className="muted">{data?`${data.total} 只 · 按涨幅取前 100 只`:'读取收盘快照…'}</span></div>
 <p className="footnote">{data?.meta.source||'公开行情'} · 源 {data?.meta.source_time?.replace('T',' ').slice(0,19)||'未知'} · 修订 {data?.meta.revision??'—'}。行业使用东方财富 F10，概念使用{data?.concept_source==='ths'?'同花顺':'历史东方财富'}归属（行业 {data?.membership_counts?.industry??0} / 概念 {data?.membership_counts?.concept??0}），不完整归属留空；“第几板”仅取同日涨停池的连续涨停天数。</p>
 <div className="table-wrap"><table><thead><tr><th>股票 / 代码</th><th>龙虎榜</th><th>涨幅</th><th>收盘价</th><th>成交额</th><th>所属行业</th><th>{data?.concept_source==='ths'?'同花顺概念':'历史东财概念'}</th><th>第几板</th></tr></thead><tbody>{pages.visible.map((r,i)=><tr key={r.code}><td><button className="lhb-stock-link" onClick={()=>onStock({code:r.code,name:r.name,date:data!.meta.trade_date})}><span className="gainer-rank">{pages.offset+i+1}</span>{r.name}</button><small>{r.code}{r.is_st?' · ST':''}</small></td><td className="lhb-badge">{badge(lhb.data,r.code)}</td><td className={r.change>0?'up':r.change<0?'down':'muted'}>{r.change>0?'+':''}{r.change.toFixed(2)}%</td><td>{r.price.toFixed(2)}</td><td>{money(r.amount)}</td><td className="board-links">{r.industry.length?r.industry.map(b=>chip(b,'industry')):'归属未存档'}</td><td className="board-links concepts-cell">{r.concepts.slice(0,3).map(b=>chip(b,'concept'))}{r.concepts.length>3&&<details><summary>全部 {r.concepts.length} 个</summary><div>{r.concepts.slice(3).map(b=>chip(b,'concept'))}</div></details>}{!r.concepts.length&&'归属未存档'}</td><td><span className={r.limit_status==='sealed'?'up':''}>{r.boards!=null&&r.boards>0?r.boards===1?'首板':`第 ${r.boards} 板`:r.limit_status==='source_conflict'?'来源冲突':r.limit_status==='not_sealed'?'未涨停':r.limit_status==='sealed_height_unknown'?'涨停 · 高度未知':'未确认'}</span></td></tr>)}</tbody></table>{(!data?.rows.length)&&<div className="empty"><strong>{error||(!data?'正在读取个股榜':data.meta.revision?'没有符合筛选条件的股票':'该日个股行情未存档')}</strong></div>}</div>
 <Pager {...pages} label="个股涨幅榜"/>
 <p className="footnote">涨停价由行情源提供，不按统一涨幅阈值猜测。未进入涨停池的股票不填“0 板”；科创板、ST、未开板新股等可能缺少连板记录。概念归属可重叠。</p>
 </section><LhbBoard data={lhb.data} error={lhb.error} onStock={onStock}/></>;
}
