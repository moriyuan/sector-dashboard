import {useState, type ReactNode} from 'react';
import {Segmented} from 'antd';
import './capital-flow.css';

type Period = 'today' | 'five' | 'ten';
type Direction = 'in' | 'out' | 'flat' | 'missing';
type Filter = 'all' | 'in' | 'out';
type Amount = number | null | undefined;
export type Flow = Partial<Record<Period | `${Period}_ratio`, Amount>> & {source_time?: string|null};
type SectorFlow = {code:string; name:string; flow:Flow|null};
const periods: {label:string; value:Period}[] = [
  {label:'今日', value:'today'}, {label:'5日', value:'five'}, {label:'10日', value:'ten'},
];
const labels = {in:'净流入', out:'净流出', flat:'净额为零', missing:'缺少数据'};
const amountFormat = new Intl.NumberFormat('zh-CN', {maximumFractionDigits:2});

function direction(value: Amount): Direction {
  if (value == null || !Number.isFinite(value)) return 'missing';
  if (value === 0) return 'flat';
  return value > 0 ? 'in' : 'out';
}
function signedAmount(value: Amount) {
  if (value == null || !Number.isFinite(value)) return '—';
  if (value === 0) return '0 元';
  const abs = Math.abs(value);
  const [unit, divisor] = abs >= 1e8 ? ['亿', 1e8] as const : abs >= 1e4 ? ['万', 1e4] as const : ['元', 1] as const;
  return (value > 0 ? '+' : '−') + amountFormat.format(abs / divisor) + ' ' + unit;
}

// Three streamlines form a single direction symbol; only the adjacent bar encodes size.
function FlowMark({state}:{state:Direction}) {
  const inward = state === 'in';
  const trail = inward ? 'M13 3 Q12.5 9 17.5 13.7' : 'M17.5 13.7 Q12.5 9 13 3';
  const tip = inward ? 'M13.7 13 L17.5 13.7 L17.2 9.8' : 'M10.5 6 L13 3 L15 6.3';
  return <svg viewBox="0 0 40 40" className={'fund-mark fund-'+state} aria-hidden="true" focusable="false">
    {state === 'in' || state === 'out' ? <>
      <circle className="fund-core-halo" cx="20" cy="20" r="5.8"/>
      <circle cx="20" cy="20" r="2.8" fill={inward ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.3"/>
      {[0,120,240].map(angle => <g key={angle} transform={`rotate(${angle} 20 20)`} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d={trail} opacity=".7"/><path d={tip}/><path className="fund-particle" d={trail} pathLength="1"/>
      </g>)}
    </> : <>
      <circle cx="20" cy="20" r="11" fill="none" stroke="currentColor" strokeWidth="1.65" strokeDasharray={state==='missing'?'2.5 3':undefined}/>
      <path d={state==='missing'?'M16 20h8':'M16 17.8h8 M16 22.2h8'} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
    </>}
  </svg>;
}

export function FlowPeriods({flow}:{flow:Flow|null}) {
  return <div className="fund-periods" aria-label="各周期主力资金方向">
    {periods.map(period => {
      const value = flow?.[period.value];
      const state = direction(value);
      return <div key={period.value} className={'fund-reading fund-'+state}>
        <FlowMark state={state}/>
        <span><small>{period.label} · {labels[state]}</small><b>{signedAmount(value)}</b></span>
      </div>;
    })}
  </div>;
}

export function CapitalFlow({rows,sourceTime,ths,filterHint,onSelect}:{
  rows:SectorFlow[]; sourceTime:string; ths:boolean; filterHint:ReactNode; onSelect:(code:string)=>void;
}) {
  const [period,setPeriod] = useState<Period>('today');
  const [filter,setFilter] = useState<Filter>('all');
  const ranked = rows.flatMap(row => {
    const value = row.flow?.[period];
    if (typeof value !== 'number' || !Number.isFinite(value)) return [];
    return [{...row, value, state:direction(value), ratio:row.flow?.[`${period}_ratio`]}];
  }).sort((a,b)=>Math.abs(b.value)-Math.abs(a.value) || a.code.localeCompare(b.code));
  const scale = Math.abs(ranked[0]?.value ?? 0);
  const list = ranked.filter(row=>filter==='all' || filter===row.state).slice(0,7);
  const missing = rows.length-ranked.length;
  return <section className="panel fund-panel" id="capital">
    <div className="section-head"><div><h3>资金动向</h3><p>{ths?'同花顺概念 · 资金流尚无独立日期核验':'东方财富统计口径 · 板块主力净额'}</p></div>
      <Segmented<Period> size="small" aria-label="资金统计周期" options={periods} value={period} onChange={setPeriod}/>
    </div>
    <div className="fund-filters" role="group" aria-label="资金方向筛选">
      <button aria-pressed={filter==='all'} onClick={()=>setFilter('all')}>全部</button>
      {(['in','out'] as const).map(side=><button className={'fund-'+side} key={side} aria-pressed={filter===side} onClick={()=>setFilter(side)}>
        <FlowMark state={side}/>{labels[side]}
      </button>)}
    </div>
    {list.length>0 ? <>
      <div className="fund-columns fund-layout" aria-hidden="true"><span>板块</span><span className="fund-axis-key"><span>出</span><span>0</span><span>入</span></span><span>净额 / 净占比</span></div>
      {list.map(row=><button className={'fund-row fund-layout fund-'+row.state} key={row.code} onClick={()=>onSelect(row.code)}>
        <span className="fund-name"><FlowMark state={row.state}/><span><strong>{row.name}</strong><small>{labels[row.state]}</small></span></span>
        <span className="fund-axis" aria-hidden="true"><i style={{width:scale ? 50*Math.abs(row.value)/scale+'%' : 0}}/></span>
        <span className="fund-number"><b>{signedAmount(row.value)}</b><small>{row.ratio==null?'净占比未知':(row.ratio>0?'+':'')+row.ratio.toFixed(2)+'%'}</small></span>
      </button>)}
      <p className="fund-note">箭头向心为流入，向外为流出。按净额绝对值取前 7 个，条长共用刻度。</p>
    </> : filterHint || <div className="fund-empty">
      {ranked.length===0 && <FlowMark state="missing"/>}
      <strong>{ranked.length ? '当前没有'+(filter==='out'?'净流出':'净流入')+'的板块' : ths ? '同花顺概念资金流暂不可用' : '所选日期暂无该周期资金数据'}</strong>
      {ranked.length>0 && <button onClick={()=>setFilter('all')}>查看全部方向</button>}
    </div>}
    <p className="fund-source">资金源 {sourceTime}{rows.length>0 && <> · 覆盖 {ranked.length}/{rows.length} 个板块{missing>0 && <> · 缺失 {missing} 个</>}</>}</p>
  </section>;
}
