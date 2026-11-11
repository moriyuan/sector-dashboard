import {DetailDrawer} from './DeskUI';
import {useEffect,useRef,useState} from 'react';
import type {Opening} from './GainerNews';
import './lhb.css';
import {MoveReasons,causeStatus,EvidenceLink,type Profile,type Peer,type Period} from './MoveEvidence';
import {Pager,usePages} from './ResearchLayout';

export type StockPick={date:string;code:string;name:string;asof?:string};
export type OpenStock=(s:StockPick)=>void;
type SeatAssociation={name:string;level:string;confidence:string;reviewed_at:string;match:string;sources:{url:string;title:string;basis:string}[]};
type Seat={name:string;buy:number|null;sell:number|null;net:number|null;association?:SeatAssociation|null};
type KnownSeat={name:string;level:string;seat:string;sides:('buy'|'sell')[];states:Record<string,string>;net:number|null};
type SummarySeat={name:string;seat:string;net:number|null;conflict:boolean;previous:boolean;disclosure_ids:string[]};
type SeatStock={code:string;name:string;change:number|null;periods:Partial<Record<Period,SummarySeat[]>>};
type RecordRow={id:string;code:string;name:string;reason:string;period:Period;net:number|null;
  known_seats?:KnownSeat[];buy_total:number|null;sell_total:number|null;buy:Seat[];sell:Seat[];buy_state:string;sell_state:string};
type Day={trade_date:string;status:string;revision:number|null;collected_at:string|null;checked_at:string|null;issues:string[];seat_research?:{note:string;reviewed_at:string;available:boolean};rows:RecordRow[]};
type Listing=Omit<Day,'rows'>&{rows:(RecordRow&{change:number|null;three_day_change:number|null})[];seat_stocks?:SeatStock[];three_day_window:string[];related_by_code:Record<string,Profile>};
type Hit={id:string;title:string;source:string;published_at:string;basis:string;timing:string;url:string};
type Detail=Day&{stock:{code:string;name:string;rank:number|null;change:number|null;direct:Hit[];sector:Hit[]};
                 related:Profile;news_asof:string;gainers_revision:number|null};
const groups:Record<Period,string>={single:'单日榜',three:'三日累计榜',other:'其他周期／口径未明'};
const states:Record<string,string>={published:'已有披露',pending:'待披露',partial:'披露待补齐',error:'获取异常',stale:'显示上次快照',unarchived:'该日未存档'};
const when=(s:string|null)=>s?new Date(s).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'—';
const wan=(v:number|null)=>v==null?'—':(v/10000).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
const sign=(v:number|null)=>v==null?'':v>0?'up':v<0?'down':'';
const change=(v:number|null)=>v==null?'—':`${v>0?'+':''}${v.toFixed(2)}%`;

function useReport<T>(path:string,refresh=0) {
 const [state,setState]=useState<{path:string;data?:T;error?:string}>({path:''});
 useEffect(()=>{
   if(!path)return;
   const c=new AbortController();
   const load=async()=>{try{
     const r=await fetch(path,{signal:c.signal});if(!r.ok)throw Error();
     const data=await r.json();if(!c.signal.aborted)setState({path,data});
   }catch{if(!c.signal.aborted)setState(previous=>({path,data:previous.path===path?previous.data:undefined,error:'本地龙虎榜接口暂不可用'}));}};
   void load();const t=setInterval(load,60000);return()=>{clearInterval(t);c.abort();};
 },[path,refresh]);
 return state.path===path?state:{path};
}

export function useLhbDay(date:string,refresh:number){
 return useReport<Listing>(date?'api/lhb?'+new URLSearchParams({date}):'',refresh);
}

export function badge(day:Day|undefined,code:string){
 if(!day)return '—';
 const values=new Set(day.rows.filter(r=>r.code===code).map(r=>groups[r.period]));
 return [...values].join(' / ')||(['published','partial'].includes(day.status)?'暂无记录':states[day.status]);
}

function Status({data}:{data:Day}){
 return <><p className="lhb-status"><b>当日榜单 · {states[data.status]}</b> 最近检查 {when(data.checked_at)}
 {data.revision!=null&&<> · 修订 {data.revision} · {data.collected_at && data.collected_at.slice(0,10)>data.trade_date?'事后补采':'采集'} {when(data.collected_at)}</>}</p>
 {data.issues.map(m=><p className="warning" key={m}>{m}</p>)}</>;
}

function peerStatus(peer:Peer,status:string){
 if(peer.periods.length)return peer.periods.map(p=>groups[p]).join(' / ');
 return status==='published'?'当日未上榜':status==='pending'?'待披露':status==='unarchived'?'当日未存档':'上榜状态待核实';
}
function DisclosureGroup({group,data,onStock}:{group:Period;data:Listing;onStock:OpenStock}){
 const rows=data.rows.filter(r=>r.period===group);
 const pages=usePages(rows,10,data.trade_date+group);
 return <details className="lhb-group" key={group} open={group==='single'}><summary>{groups[group]} · {rows.length} 条披露</summary>
 {group==='three'&&<p className="footnote">累计区间：{data.three_day_window?.length===3?`${data.three_day_window[0]} 至 ${data.three_day_window[2]}`:'交易日历不足'} · 按三个交易日收盘价／昨收参考价连乘计算，缺少任一日有效报价显示 —。</p>}
 {rows.length?<><div className="table-wrap"><table><thead><tr><th>股票</th><th className="lhb-change">当日涨跌幅</th>{group==='three'&&<th className="lhb-change" title={data.three_day_window?.join('、')}>三日累计涨跌幅</th>}<th>上榜原因</th><th>净买额（万）</th><th>上涨线索与相关股票</th></tr></thead><tbody>
 {pages.visible.map(r=>{
   const peers=data.related_by_code?.[r.code],open=(code:string,name:string)=>onStock({code,name,date:data.trade_date});
   return <tr key={r.id}><td><button className="lhb-stock-link" onClick={()=>open(r.code,r.name)}>{r.name}</button><small>{r.code}</small>{r.known_seats?.map(k=><small key={k.name+k.seat} className="lhb-seat-label">{k.name} · {k.sides.map(side=>(side==='buy'?'买榜':'卖榜')+(k.states[side]==='previous'?'（旧记录）':'')).join('／')}<br/>{k.net==null?'净额未知':`席位净额 ${wan(k.net)} 万`}</small>)}</td><td className={`lhb-change ${r.change==null?'muted':sign(r.change)}`}>{change(r.change)}</td>{group==='three'&&<td className={`lhb-change ${r.three_day_change==null?'muted':sign(r.three_day_change)}`}>{change(r.three_day_change)}</td>}<td className="lhb-reason">{r.reason}</td><td className={sign(r.net)}>{wan(r.net)}</td>
   <td className="lhb-peer-preview">{peers?.causes?.length?<><button className="move-preview-cause" onClick={()=>open(r.code,r.name)} title={peers.causes[0].evidence.fact}>{peers.causes[0].topic||'个股事件'} · 查看依据</button>
   {peers.rows.map(peer=><button key={peer.code} title={peer.matches[0].evidence.fact} onClick={()=>open(peer.code,peer.name)}>#{peer.rank} {peer.name}</button>)}
   {peers.total>peers.rows.length&&<button className="text-button" onClick={()=>open(r.code,r.name)}>全部 {peers.total} 只</button>}
   {!peers.total&&<small>榜内暂无同一催化的其他股票</small>}</>:<small>{causeStatus(peers)}</small>}</td></tr>;
 })}
 </tbody></table></div><Pager {...pages} label={groups[group]}/></>:<p className="footnote">当前存档无该类记录。</p>}</details>;
}

function SeatStockTable({data,onStock}:{data:Listing;onStock:OpenStock}){
 const rows=data.seat_stocks||[],pages=usePages(rows,10,data.trade_date);
 const periods:Period[]=rows.some(r=>r.periods.other?.length)?['single','three','other']:['single','three'];
 const names=new Set(rows.flatMap(r=>Object.values(r.periods).flatMap(seats=>seats.map(s=>s.name))));
 return <section className="lhb-seat-stocks" aria-label="游资参与股票汇总">
 <a className="lhb-backtest-link" href="#seat-backtest">查看历史跟随回测 ↗</a><h3>游资参与股票汇总 <small>{rows.length} 只股票 · {names.size} 组关联席位</small></h3>
 <p className="footnote">{data.trade_date} · 席位净买额（万元），＋净买入／−净卖出。重复披露去重，单日与三日累计不可相加；点击股票核对席位与关联依据。</p>
 {rows.length?<><div className="table-wrap"><table><thead><tr><th>股票</th><th className="lhb-change">当日涨跌幅</th>{periods.map(p=><th key={p}>{groups[p]} · 关联游资及净买额</th>)}</tr></thead><tbody>
 {pages.visible.map(row=><tr key={row.code}><td><button className="lhb-stock-link" onClick={()=>onStock({code:row.code,name:row.name,date:data.trade_date})}>{row.name}</button><small>{row.code}</small></td><td className={`lhb-change ${sign(row.change)}`}>{change(row.change)}</td>
 {periods.map(p=><td key={p} className="lhb-summary-amounts">{row.periods[p]?.length?row.periods[p]!.map(s=><div key={s.name+s.seat} title={s.seat}>
 <span>{s.name}</span><b className={sign(s.net)}>{s.conflict?'金额不一致 · 查看详情':s.net==null?'净额未知':`${s.net>0?'+':''}${wan(s.net)}`}</b>{s.previous&&<small>含沿用席位</small>}</div>):<span className="muted">—</span>}</td>)}</tr>)}
 </tbody></table></div><Pager {...pages} label="游资参与股票"/></>:<p className="footnote">{data.status==='pending'?'待披露，披露后展示已匹配的席位。':data.seat_research?.available===false?'该截止时间暂无可用的席位关联资料。':'当前存档尚无匹配记录，不代表没有游资参与。'}</p>}
 <p className="footnote">公开报道关联，实际账户归属待核实；— 表示该周期无匹配记录，不代表净额为零。</p></section>;
}

export function LhbBoard({data,error,onStock}:{data?:Listing;error?:string;onStock:OpenStock}){
 return <section id="lhb" className="panel lhb-board"><div className="section-head"><div>
 <h2>龙虎榜</h2><p>沪深 A 股交易公开信息 · {data?.trade_date||'读取日期…'}</p></div></div>
 {error&&<p className="notice">{error}</p>}{!data&&!error&&<p>读取本地存档…</p>}
 {data&&<><Status data={data}/>{data.seat_research&&<p className="footnote">{data.seat_research.note} 核验日 {data.seat_research.reviewed_at.slice(0,10)}。</p>}<SeatStockTable data={data} onStock={onStock}/>{data.rows.length===0?<p className="footnote">
 {data.status==='pending'?'来源尚无该日沪深 A 股披露记录，盘后自动复查。':'该交易日暂无可展示的本地记录。'}</p>:
 (Object.keys(groups) as Period[]).map(group=><DisclosureGroup key={data.trade_date+group} group={group} data={data} onStock={onStock}/>)}</>}
 <p className="footnote">涨跌幅使用所选交易日的收盘报价，三日累计列为截至榜单日的三个交易日复合涨跌幅，非涨幅偏离值，亦不保证与异常交易披露的起止区间相同；缺少报价显示 —。先核对新闻里的上涨线索，再从同日非 ST 涨幅榜前 100 名查找同一催化下被报道的股票，含未上龙虎榜者。每项龙虎榜披露单独列示，不同原因或周期的净买额可能重叠。</p></section>;
}

function Seats({row,side}:{row:RecordRow;side:'buy'|'sell'}){
 const entries=row[side],state=side==='buy'?row.buy_state:row.sell_state;
 return <div className="lhb-seats"><h4>{side==='buy'?'买入席位':'卖出席位'} <small>{state==='previous'?'沿用上次取得的席位':entries.length+' 条'}</small></h4>
 {entries.length?<table><thead><tr><th>席位</th><th>买入</th><th>卖出</th><th>净买额</th></tr></thead><tbody>
 {entries.map((s,i)=><tr key={i}><td>{i+1}. {s.name}{s.association?<details className="lhb-seat-evidence"><summary>{s.association.name} · {s.association.level} · 查看依据</summary><p>{s.association.confidence} · 匹配方式 {s.association.match} · 核验日 {s.association.reviewed_at.slice(0,10)}</p>{s.association.sources.map(source=><p key={source.url}>{source.basis}<br/><a href={source.url} target="_blank" rel="noopener noreferrer">{source.title} ↗</a></p>)}</details>:<small>未匹配已核验游资资料</small>}</td><td>{wan(s.buy)}</td><td>{wan(s.sell)}</td><td className={sign(s.net)}>{wan(s.net)}</td></tr>)}
 </tbody></table>:<p className="footnote">此项披露的该侧席位尚未取得。</p>}</div>;
}

function RelatedStocks({data,onPick,open}:{data:Detail;onPick:(peer:Peer)=>void;open:(id:string)=>void}){
 const peers=data.related,pages=usePages(peers?.rows||[],5,data.trade_date+data.stock.code);
 return <><MoveReasons profile={peers} open={open}/><section className="lhb-peers" aria-label="同一催化的相关股票"><h3>同一催化的相关股票 {peers?.state==='ok'&&<small>共 {peers.total} 只</small>}</h3>
 <p className="footnote">{data.trade_date} · 仅列有新闻异动证据的同日非 ST 涨幅前 100 名，名次仅供对照。报道列为背景的因素不能证明个股的唯一上涨原因。</p>
 {peers?.rows.length?<><div className="lhb-peer-list">{pages.visible.map(peer=><article key={peer.code} className="move-peer-item"><button onClick={()=>onPick(peer)} className="lhb-peer">
 <span className="lhb-peer-rank">#{peer.rank}</span><span><strong>{peer.name} <small>{peer.code}</small></strong></span>
 <span className="lhb-peer-performance"><b className={sign(peer.change)}>+{peer.change.toFixed(2)}%</b><small>{peerStatus(peer,data.status)}</small></span></button>
 {peer.matches.map(m=><details className="move-peer-proof" key={m.key}><summary>{peers.causes.find(c=>c.key===m.key)?.topic||'共同事件'} · 核对这只股票的依据</summary>
 <p>{m.evidence.fact}</p><blockquote>{m.evidence.excerpt}</blockquote><EvidenceLink ev={m.evidence} open={open}/></details>)}</article>)}</div>
 <Pager {...pages} label="同一催化的股票"/></>:<p className="footnote">{peers?.state==='ok'?'已有本股线索，涨幅榜中尚无其他股票满足同一催化的证据条件。':'未建立上涨归因，暂不列出关联股。'}</p>}</section></>;
}

export function LhbDrawer({pick:entry,close,onEvent}:{pick:StockPick;close:()=>void;onEvent:(e:Opening)=>void}){
 const [trail,setTrail]=useState<StockPick[]>([]);
 const pick=trail[trail.length-1]||entry;
 const heading=useRef<HTMLHeadingElement>(null),lastCode=useRef(pick.code);
 useEffect(()=>{
   if(lastCode.current!==pick.code){
     heading.current?.closest('.desk-detail-body')?.scrollTo({top:0});
     heading.current?.focus({preventScroll:true});lastCode.current=pick.code;
   }
 },[pick.code]);
 const qs=new URLSearchParams({date:pick.date,...(pick.asof?{asof:pick.asof}:{})});
 const {data,error}=useReport<Detail>('api/lhb/'+pick.code+'?'+qs);
 return <DetailDrawer title={'交易公开信息 · '+pick.date} label="个股龙虎榜" closeLabel="关闭个股龙虎榜" size={690} onClose={close}>
 <div className="lhb-content">{trail.length>0&&<button className="lhb-back" onClick={()=>setTrail(t=>t.slice(0,-1))}>← 返回{(trail[trail.length-2]||entry).name}</button>}
 <h2 ref={heading} tabIndex={-1}>{pick.name} <small>{pick.code}</small></h2>
 {pick.asof&&<p className="footnote">历史截止 {when(pick.asof)}，只显示当时已存档的披露。</p>}
 {error&&<p className="notice">{error}</p>}{!data&&!error&&<p>正在读取披露和相关新闻…</p>}
 {data&&<><Status data={data}/>{data.seat_research&&<p className="footnote">{data.seat_research.note} 核验日 {data.seat_research.reviewed_at.slice(0,10)}。</p>}
 {data.stock.rank!=null&&<p>当日非 ST 上涨股第 {data.stock.rank} 名 · 收盘涨幅 <b className="up">+{data.stock.change?.toFixed(2)}%</b></p>}
 {!data.rows.length&&<p className="lhb-empty">{data.status==='published'?'该股当日未上龙虎榜。':data.status==='pending'?'待披露 · 来源尚无该日沪深 A 股榜单。':data.status==='partial'?'当前披露仍待补齐，尚未见该股记录。':states[data.status]}</p>}
 <RelatedStocks data={data} open={id=>onEvent({id,asof:data.news_asof,day:pick.date,origin:'lhb'})} onPick={peer=>setTrail(t=>[...t,{...pick,code:peer.code,name:peer.name}])}/>
 {(Object.keys(groups) as Period[]).map(group=>{const rows=data.rows.filter(r=>r.period===group);
 return rows.length>0&&<section key={group} className="lhb-records"><h3>{groups[group]}</h3>
 {rows.map(r=><article key={r.id}><h4>{r.reason}</h4><small>披露编号 {r.id} · 金额单位：万元</small>
 <div className="lhb-totals"><span>榜单买入 <b>{wan(r.buy_total)}</b></span><span>榜单卖出 <b>{wan(r.sell_total)}</b></span><span>榜单净买额 <b className={sign(r.net)}>{wan(r.net)}</b></span></div>
 <Seats row={r} side="buy"/><Seats row={r} side="sell"/></article>)}</section>;})}
 <p className="footnote"><a target="_blank" rel="noopener noreferrer" href={'https://data.eastmoney.com/stock/lhb/'+pick.code+'.html'}>东方财富个股龙虎榜 ↗</a> · 源发布时间未提供。两侧可能有同一席位，净买额使用来源公布值。</p>
 <h3>其他新闻线索</h3><p className="footnote">榜单日及此前两个自然日 · 截至 {when(data.news_asof)}。新闻匹配不代表上榜原因。</p>
 {(['direct','sector'] as const).map(key=><section className="lhb-news" key={key}><h4>{key==='direct'?'直接提及公司':'板块新闻（不用于归因）'}</h4>
 {data.stock[key].map(hit=><div key={hit.id}><small>{hit.source} · {when(hit.published_at)} · {hit.timing}</small>
 <button onClick={()=>onEvent({id:hit.id,asof:data.news_asof,day:pick.date,origin:'lhb'})}>{hit.title}</button><small>{hit.basis} · <a href={hit.url} target="_blank" rel="noopener noreferrer">原文 ↗</a></small></div>)}
 {!data.stock[key].length&&<p className="footnote">该窗口暂无已收录匹配。</p>}</section>)}</>}</div></DetailDrawer>;
}
