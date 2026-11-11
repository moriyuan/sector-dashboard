import {useEffect,useRef,useState} from 'react';
import {Alert, Button, Segmented, Select, Spin, Table} from 'antd';
import * as echarts from 'echarts/core';
import {LineChart} from 'echarts/charts';
import {GridComponent,LegendComponent,MarkLineComponent,TooltipComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
import './resonance.css';

echarts.use([LineChart,GridComponent,LegendComponent,MarkLineComponent,TooltipComponent,CanvasRenderer]);
type Stock={code:string;name:string;at:number;level:number;gain:number;hold:string;rank_from:number;rank_to:number;leader_known:boolean};
type Board={code:string;kind:string;name:string;at:number;lead:number;excess:number;gain:number;up:number;valid:number;eligible:number;held:number;stocks:number;response:number;response_up:number;response_n:number};
type Event={id:number;pivot:number;confirm:number;count:number;leading:number};
type Wave={pivot:number;confirm:number;end:number;pullback:{status:string;peak:number;pull:number|null;again:number|null}};
type Replay={version:string;day:string|null;dates:string[];reason?:string;clock:string[];events:Event[];selected?:number;wave?:Wave;boards?:Board[];board?:string;members?:Stock[];stock?:string;
  summary?:{prior:string;observed:number;universe:number;industry:number;concept:number};chart?:{start:number;index:(number|null)[];sector?:(number|null)[];stock?:(number|null)[]}};
type Choice={day:string;benchmark:keyof typeof benchmarkName;turn?:number;board?:string;stock?:string};
const benchmarkName={sh000001:'上证指数',sz399006:'创业板指',sh000680:'科创综指'};
const pct=(v:number)=> (v>0?'+':'')+v.toFixed(2)+'%';
function rank(s:Stock){
  if(s.rank_to!==s.rank_from)return `第 ${s.rank_from}–${s.rank_to} 只 · 同分钟`;
  if(s.rank_from===1)return s.leader_known?'领涨候选':'首个观察到';
  return '第 '+s.rank_from+' 只'+(s.leader_known?'跟随':'触发');
}
function timing(lead:number){return lead>0?`先动 ${lead} 分钟`:lead===0?'与低点同分钟':`低点后 ${-lead} 分钟`;}

function MinuteChart({data,bench,board,stock}:{data:Replay;bench:string;board?:Board;stock?:Stock}){
  const ref=useRef<HTMLDivElement>(null);
  useEffect(()=>{
    const part=data.chart,w=data.wave;if(!ref.current||!part||!w)return;
    const chart=echarts.init(ref.current,undefined,{renderer:'canvas'});
    const clocks=data.clock.slice(part.start,part.start+part.index.length);
    const marks=[{name:'低点',xAxis:data.clock[w.pivot]},{name:'确认',xAxis:data.clock[w.confirm]}];
    if(w.pullback.pull!=null)marks.push({name:'回踩',xAxis:data.clock[w.pullback.pull]});
    if(w.pullback.again!=null)marks.push({name:'再攻',xAxis:data.clock[w.pullback.again]});
    const line=(name:string,values:(number|null)[],color:string)=>({name,data:values,type:'line' as const,
      symbol:'none',connectNulls:false,itemStyle:{color},lineStyle:{width:2}});
    chart.setOption({animation:false,tooltip:{trigger:'axis',confine:true,renderMode:'richText'},legend:{top:0},
      grid:{top:46,left:55,right:20,bottom:32},xAxis:{type:'category',data:clocks,boundaryGap:false},
      yAxis:{type:'value',scale:true,axisLabel:{formatter:'{value}%'}},series:[
        {...line(bench,part.index,'#354c52'),markLine:{symbol:'none',lineStyle:{color:'#929a90',type:'dashed'},label:{formatter:'{b}',position:'insideEndTop'},data:marks}},
        ...(part.sector&&board?[line(board.name,part.sector,'#b25441')]:[]),
        ...(part.stock&&stock?[line(stock.name,part.stock,'#357eaa')]:[])
      ]});
    const ro=new ResizeObserver(()=>chart.resize());ro.observe(ref.current);
    return ()=>{ro.disconnect();chart.dispose();};
  },[data,bench,board,stock]);
  return <div ref={ref} className="wave-chart" role="img" aria-label="指数、板块与个股分钟涨跌对照"/>;
}

export default function Resonance(){
  const [choice,select]=useState<Choice>({day:'',benchmark:'sh000001'});
  const [data,setData]=useState<Replay|null>(null),[dates,setDates]=useState<string[]>([]),[error,setError]=useState('');
  const [refresh,reload]=useState(0),[onlyHeld,setOnlyHeld]=useState(false);
  useEffect(()=>{
    const abort=new AbortController(),q=new URLSearchParams({benchmark:choice.benchmark});
    if(choice.day)q.set('day',choice.day);
    if(choice.turn!=null)q.set('turn',String(choice.turn));
    if(choice.board)q.set('board',choice.board);
    if(choice.stock)q.set('stock',choice.stock);
    setData(null);setError('');
    fetch('api/resonance?'+q,{signal:abort.signal}).then(async r=>{if(!r.ok)throw Error('读取失败 '+r.status);return r.json() as Promise<Replay>;})
      .then(d=>{if(!abort.signal.aborted){setData(d);setDates(d.dates);}})
      .catch(e=>{if(!abort.signal.aborted)setError(e.message);});
    return ()=>abort.abort();
  },[choice,refresh]);
  const change=(updates:Partial<Choice>)=>{setOnlyHeld(false);select({...choice,...updates});};
  const b=data?.boards?.find(v=>v.code===data.board),s=data?.members?.find(v=>v.code===data.stock),w=data?.wave,summary=data?.summary;
  const time=(i:number|null|undefined)=>i==null?'—':data?.clock[i]||'—';
  return <main className="wave-page" id="resonance">
    <header className="section-head"><div><h1>指数共振</h1><p>盘后回放 · 1 分钟</p></div>
      <div className="wave-tools"><Select aria-label="共振交易日" value={choice.day} options={[{value:'',label:'最近归档'},...dates.map(d=>({value:d,label:d}))]}
        onChange={day=>change({day,turn:undefined,board:undefined,stock:undefined})}/>
        <Segmented aria-label="共振基准" value={choice.benchmark} options={Object.entries(benchmarkName).map(([value,label])=>({value,label}))}
          onChange={v=>change({benchmark:v as Choice['benchmark'],turn:undefined,board:undefined,stock:undefined})}/>
        <Button onClick={()=>reload(v=>v+1)}>刷新</Button></div></header>
    {error?<Alert type="error" title={error}/>:!data?<Spin/>:<>
    {summary&&<div className="wave-meta"><strong>{data.day}</strong><span>分钟行情 {summary.observed} / {summary.universe} 只</span>
      <span>行业 {summary.industry} 个 · 同花顺概念 {summary.concept} 个</span><span>成分日 {summary.prior}</span></div>}
    {data.reason&&<Alert type="info" showIcon title={data.reason}/>}
    {w&&<><section className="panel"><div className="section-head"><h2>指数转折</h2><Select aria-label="选择指数波段" value={data.selected} className="wave-control"
      options={data.events.map(e=>({value:e.id,label:`${time(e.pivot)} 低点 · ${time(e.confirm)} 确认 · ${e.leading} 个先动板块`}))}
      onChange={turn=>change({turn,board:undefined,stock:undefined})}/></div>
      <div className="wave-steps">{[['转折低点',time(w.pivot)],['反弹确认',time(w.confirm)],['回踩确认',time(w.pullback.pull)],['再次突破',time(w.pullback.again)]].map(([k,v])=><div key={k}><small>{k}</small><strong>{v}</strong></div>)}</div>
      <MinuteChart data={data} bench={benchmarkName[choice.benchmark]} board={b} stock={s}/><p className="footnote">曲线从图中首分钟价格归零。板块为已核验成分等权分时。波段状态 · {w.pullback.status}</p></section>
    <section className="panel"><div className="section-head"><h2>板块响应</h2>{b&&<Select showSearch optionFilterProp="label" className="wave-control" aria-label="选择共振板块" value={b.code}
      options={data.boards?.map(v=>({value:v.code,label:`${v.name} · ${time(v.at)} · ${timing(v.lead)}`}))} onChange={board=>change({turn:data.selected,board,stock:undefined})}/>}</div>
      {b?<><h3>{b.name} <small>{b.kind} · {timing(b.lead)}</small></h3><p>{time(b.at)} 板块突破，近 3 分钟涨幅 <b>{pct(b.gain)}</b>，超出指数 {b.excess.toFixed(2)} 个百分点，上涨 {b.up} / {b.valid} 只。
      从指数低点到反弹确认，板块涨幅 {pct(b.response)}，上涨 {b.response_up} / {b.response_n} 只。</p>
      <div className="wave-tools"><h3>个股启动顺序</h3><Button size="small" type={onlyHeld?'primary':'default'} aria-pressed={onlyHeld} onClick={()=>setOnlyHeld(!onlyHeld)}>只看守住（{b.held}）</Button></div>
      <Table<Stock> size="small" rowKey="code" scroll={{x:580}} dataSource={(data.members||[]).filter(r=>!onlyHeld||r.hold==='守住')} pagination={{pageSize:12,showSizeChanger:false}}
        locale={{emptyText:onlyHeld?'该波段暂无已确认守住的股票':'该波段暂无达到启动门槛的个股'}} columns={[
        {title:'顺序',render:(_,r)=>rank(r)},{title:'股票',render:(_,r)=><Button type="link" onClick={()=>select({...choice,turn:data.selected,board:b.code,stock:r.code})}>{r.name} {r.code}</Button>},
        {title:'启动',render:(_,r)=>time(r.at)},{title:'3 分钟涨幅',render:(_,r)=>pct(r.gain)},{title:'突破位',dataIndex:'level'},
        {title:'回踩',render:(_,r)=><span className={r.hold==='守住'?'wave-held':''}>{r.hold}</span>}]}/>
      </>:<p>本段指数反弹期间，没有板块同时达到启动和同步上涨门槛。</p>}</section></>}
    <details className="panel"><summary>识别规则与覆盖范围</summary>
      <p>指数在上午或下午独立找低点。近 12 分钟低点相对此前 10 分钟高点至少回落 0.18%，至少两分钟后反弹 0.12% 并突破前三分钟高点时确认。记录的低点时间早于可确认时间，确认后间隔至少 20 分钟寻找下一段。</p>
      <p>从低点前 8 分钟到确认分钟，板块首次突破前 5 分钟高点、近 3 分钟上涨至少 0.20%、跑赢指数至少 0.10 个百分点，且至少 55% 有效成分上涨，记为启动。低点至确认还需上涨至少 0.04%、至少半数有效成分上涨。至少 5 只有效股票并覆盖期初合格成分的 70%。</p>
      <p>股票突破前 5 分钟最高分钟价，近 3 分钟上涨至少 0.45%、跑赢板块 0.15 和指数 0.10 个百分点，且有成交。观察起点取板块启动前 5 分钟与指数低点前 10 分钟中的较晚者，每只股票只记首次触发。同分钟并列；首只须在板块启动当时或更早触发才标为领涨候选。</p>
      <p>指数从反弹高点回落至少 0.08% 且回吐涨幅至少 30% 后构成回踩。再次越过该高点时，已在高点前启动、且期间每个分钟价都守在自身突破位之上的股票（含等于）标为守住。最多跟踪 30 分钟，至再攻、跌破原低点或该场交易结束。</p>
      <p>分钟价和成交额来自腾讯财经。股票池取前一交易日有效交易的沪深非 ST 股票，板块取本次开盘前已完整核验的东方财富行业和同花顺概念。排序受成分与分钟采集覆盖限制。阈值为复盘观察规则，版本 {data.version}。</p>
    </details></>}
  </main>;
}
