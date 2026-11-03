import React from 'react';

export type Research = {
  level:'brief'|'focus'; new_facts:{text:string;evidence_ids:string[]}[];
  mechanisms:{text:string;evidence_ids:string[];kind:'fact'|'inference'}[];
  baseline:string; counterarguments:string[]; watchpoints:string[]; market_context:string;
};
export type ResearchContext = {
  priority?:{level:string;reason:string};
  conflicts?:{text:string;unit:string;values:Record<string,string[]>}[];
  enrichment?:{id:number;status:string;url:string;captured_at:string;error?:string;excerpts:string[]}[];
  watch_candidates?:{code:string;name:string;business:string;reason:string;url:string}[];
};

export default function NewsResearch({event,research}:{event:ResearchContext;research?:Research}) {
  const sourceNames:Record<string,string>={em:'东方财富',cls:'财联社',sina:'新浪财经'};
  return <section className="news-research">
    <h3>{event.priority?.level==='focus'?'重点事件研究':'简要研究'}</h3>
    <p className="footnote">{event.priority?.reason}</p>
    {event.conflicts?.map((c,i)=><div className="news-conflict" key={i}><b>来源分歧待核对</b><p>{c.text}</p>{Object.entries(c.values).map(([s,v])=><p key={s}>{sourceNames[s]||s}：{v.join('、')} {c.unit}</p>)}</div>)}
    {research?<>
      <h4>本次披露的事实</h4>{research.new_facts.map((x,i)=><p key={i}>{x.text}<small className="news-dates">依据 {x.evidence_ids.join('、')}</small></p>)}
      <h4>此前基准与变化</h4><p>{research.baseline}</p>
      <h4>影响传导与成立条件</h4>{research.mechanisms.length?research.mechanisms.map((x,i)=><p className="news-chain" key={i}><b>{x.kind==='inference'?'条件推导':'证据事实'}</b> · {x.text}<small className="news-dates">依据 {x.evidence_ids.join('、')}</small></p>):<p>现有证据不足以建立影响链条。</p>}
      <h4>反证与不成立条件</h4><ul>{research.counterarguments.map((x,i)=><li key={i}>{x}</li>)}</ul>
      <h4>后续验证节点</h4><ul>{research.watchpoints.map((x,i)=><li key={i}>{x}</li>)}</ul>
      <h4>市场反应的时间边界</h4><p>{research.market_context}</p>
    </>:<p className="notice">研究待生成。先查看已采集事实、来源及证据缺口。</p>}
    <h4>公开原文补证</h4>
    {event.enrichment?.length?event.enrichment.map(a=><div className="news-excerpt-status" key={a.id}><b>{a.status==='ok'?`已提取 ${a.excerpts.length} 段关键证据`:'正文未取得'}</b><small className="news-dates">补采 {new Date(a.captured_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}</small>{a.error&&<p>{a.error}</p>}<a href={a.url} target="_blank" rel="noopener noreferrer">查看原文 ↗</a></div>):<p className="muted">尚无原文补证；普通快讯按需保留摘要，重点事件优先补证。</p>}
    {!!event.watch_candidates?.length&&<details className="news-watch"><summary>同题材观察名单 · {event.watch_candidates.length} 家 · 未建立事件影响关系</summary><p>以下仅有主营业务证据，不作为事件受益公司或领涨排名样本。</p>{event.watch_candidates.map(s=><p key={s.code}><b>{s.name} {s.code}</b><br/>{s.business}<small className="news-dates">{s.reason}</small></p>)}</details>}
  </section>;
}
