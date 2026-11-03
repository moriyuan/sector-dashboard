export type MoveEvidence={id:string;revision:number;title:string;url:string;source:string;published_at:string;
 timing:string;captured_at?:string|null;fact:string;excerpt:string;origin:string};
export type Cause={key:string;level:'context'|'attributed';topic:string|null;evidence:MoveEvidence};
export type Period='single'|'three'|'other';
export type Peer={code:string;name:string;rank:number;change:number;periods:Period[];matches:{key:string;evidence:MoveEvidence}[]};
export type Profile={state:'ok'|'unavailable'|'missing_quote'|'not_rising'|'unverified';total:number;rows:Peer[];causes:Cause[];leads:MoveEvidence[]};
export function causeStatus(profile?:Profile){
 switch(profile?.state){
 case 'ok':return '有新闻依据';
 case 'not_rising':return '当日未上涨';
 case 'missing_quote':return '缺少个股收盘行情';
 case 'unverified':return '上涨原因待核实';
 default:return '缺少当日涨幅榜快照';
 }
}
export const evidenceTime=(value:string)=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false});
export function EvidenceLink({ev,open}:{ev:MoveEvidence;open:(id:string)=>void}){
 return <div className="move-source"><span>{ev.source} · {evidenceTime(ev.published_at)} · {ev.timing}{ev.captured_at?' · 原文补证 '+evidenceTime(ev.captured_at):''}</span>
 <button type="button" onClick={()=>open(ev.id)}>查看报道</button><a href={ev.url} target="_blank" rel="noopener noreferrer">原文 ↗</a></div>;
}
export function MoveReasons({profile,open}:{profile?:Profile;open:(id:string)=>void}){
 return <section className="move-reasons" aria-label="上涨原因与证据"><h3>上涨原因与证据</h3>
 {profile?.causes.length?profile.causes.map(c=><article key={c.key} className="move-reason">
 <div className="move-label">{c.level==='attributed'?'报道明确归因':'报道列为行情背景'}{c.topic&&<strong>{c.topic}</strong>}</div>
 <p>{c.evidence.fact}</p><blockquote>{c.evidence.excerpt}</blockquote>
 <EvidenceLink ev={c.evidence} open={open}/></article>):<p className="lhb-empty">{causeStatus(profile)}{profile?.state==='unverified'?'：已收录的新闻没有把当日上涨与具体事件明确联系起来。':'。'}</p>}
 {!!profile?.leads?.length&&<details className="move-leads"><summary>待核实的报道线索 · {profile.leads.length} 条</summary>
 <p className="footnote">这些报道同时提到事件和股价，但没有说明因果，不据此匹配其他股票。</p>
 {profile.leads.map(ev=><article key={ev.id}><p>{ev.title}</p><EvidenceLink ev={ev} open={open}/></article>)}</details>}
 </section>;
}
