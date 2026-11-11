import {useEffect,useState} from 'react';
import {Alert,Button,Select,Spin,Table} from 'antd';
import {DetailDrawer} from './DeskUI';
import './seat-backtest.css';

type Stat={profile:string;month?:string;rank?:number;n:number;days:number;signals?:number;entry_skipped?:number;
 win_rate:number|null;mean_return:number|null;median_return:number|null;ci_low:number|null;ci_high:number|null;
 mean_excess:number|null;delayed:number;states:Record<string,number>;active_months?:number;last_entry?:string|null};
type Meta={run_id:string;frozen:boolean;signal_start:string;data_end:string;profiles:string[];months:string[];
 audit:{raw_rows:number;positive_events:number;entry_states:Record<string,number>;trade_states:Record<string,number>};
 validation:{daily_count:number;trade_count:number};protocol:{version:string}};
type Ranking={meta:Meta;rows:Stat[];min_trades:number;min_days:number;interval:string};
type MonthData={run_id:string;rows:Stat[];nonoverlap:Stat|null};
type Trade={profile:string;name:string;code:string;signal_date:string;entry_date?:string;exit_date?:string;planned_exit?:string;
 month?:string;period:string;horizon:number;state:string;entry_state:string;net:number;disclosures:number;
 delay?:number;gross_return?:number;net_return?:number;benchmark_return?:number;excess_return?:number};
type Trades={run_id:string;rows:Trade[];total:number;page:number};
const windows=[{value:'recent_12_full_months',label:'近12个完整月 · 2025-11 至 2026-10'},
 {value:'all',label:'全样本 · 2024-11 至 2026-11（末月不完整）'},
 {value:'early',label:'早期 · 2024-11 至 2026-04'}, {value:'holdout',label:'时间留后 · 2026-05 至 2026-10'}];
const periods=[{value:'single',label:'单日榜'},{value:'three',label:'三日累计榜'},{value:'other',label:'其他周期'}];
const states:Record<string,string>={closed:'已模拟退出',entry_skipped:'未入场',missing_path:'持有路径缺失',right_censored:'尚未到期',unresolved_exit:'尚无法退出',
 one_price_up:'全天一字涨停',open_limit_up:'涨停价开盘',suspended:'停牌',st:'入场日 ST',signal_st_or_unknown:'信号日 ST 或状态未知',
 one_price_other:'全天同一价格',ipo_first_five:'上市前五个交易日',missing_entry:'入场报价缺失',missing_signal_quote:'信号日报价缺失',
 missing_signal_day:'信号日不在日线档案',unknown_st:'ST 状态未知',unknown_limit_regime:'涨跌停制度无法核实',eligible:'满足日线入场条件'};
const pct=(v:number|null|undefined)=>v==null?'—':(v*100).toFixed(2)+'%';
const amount=(v:number)=> (v/10000).toLocaleString('zh-CN',{maximumFractionDigits:2});
const signed=(v:number|null|undefined)=><span className={v==null?'muted':v>0?'up':v<0?'down':''}>{v!=null&&v>0?'+':''}{pct(v)}</span>;
function useRead<T>(path:string){
 const [state,set]=useState<{path:string;data?:T;error?:string}>({path:''});
 useEffect(()=>{const c=new AbortController();fetch(path,{signal:c.signal}).then(async r=>{
  if(!r.ok){const d=await r.json().catch(()=>({}));throw Error(typeof d.detail==='string'?d.detail:'读取回测版本失败');}return r.json();
 }).then(data=>{if(!c.signal.aborted)set({path,data});}).catch(e=>{if(!c.signal.aborted)set({path,error:e.message});});return()=>c.abort();},[path]);
 return state.path===path?state:{path};
}
const query=(p:Record<string,string|number>)=>new URLSearchParams(Object.entries(p).map(([k,v])=>[k,String(v)])).toString();

export default function SeatBacktest(){
 useEffect(()=>{window.scrollTo({top:0});},[]);
 const [scope,setScope]=useState('recent_12_full_months'),[period,setPeriod]=useState('single'),[horizon,setHorizon]=useState(5);
 const [profile,setProfile]=useState('陈小群'),[month,setMonth]=useState(''),[outcome,setOutcome]=useState('all'),[page,setPage]=useState(1);
 const [pick,setPick]=useState<Trade|null>(null),[refresh,setRefresh]=useState(0);
 const common={period,horizon,v:refresh};
 const ranking=useRead<Ranking>('api/seat-backtest?'+query({...common,scope}));
 const monthly=useRead<MonthData>('api/seat-backtest/monthly?'+query({...common,profile}));
 const trades=useRead<Trades>('api/seat-backtest/trades?'+query({...common,profile,month,outcome,page}));
 useEffect(()=>{setPage(1);setPick(null);},[profile,period,horizon,month,outcome]);
 const meta=ranking.data?.meta;
 const chooseScope=(value:string)=>{setScope(value);setMonth(/^\d{4}-\d{2}$/.test(value)?value:'');};
 const chooseProfile=(value:string)=>{setProfile(value);document.getElementById('seat-monthly')?.scrollIntoView({block:'start'});};
 const showMonth=(value:string)=>{setMonth(value);document.getElementById('seat-trades')?.scrollIntoView({block:'start'});};
 return <main id="seat-backtest" className="seat-backtest-page">
 <header className="section-head"><div><h1>游资席位跟随回测</h1><p>次日入场机会 → 持有期收益 → 逐月稳定性</p></div><Button onClick={()=>setRefresh(v=>v+1)}>刷新归档</Button></header>
 <Alert type="warning" showIcon title="历史事件研究，尚无稳定跟随优势" description="席位身份来自事后核验的公开报道，不代表实际账户归属。排名是历史条件胜率；日线只能筛查成交机会，不能保证实际成交。"/>
 {ranking.error&&<Alert type="error" showIcon title={ranking.error}/>}
 {!ranking.data&&!ranking.error&&<Spin aria-label="读取回测版本"/>}
 {meta&&<><div className="seat-backtest-meta"><span>数据区间 <b>{meta.signal_start} — {meta.data_end}</b></span><span>版本 <b>{meta.run_id}</b></span><span>固定回测 · 不自动滚动更新</span></div>
 <div className="seat-backtest-meta"><span>公开关联席位 <b>{meta.profiles.length} 组</b></span><span>净买入事件 <b>{meta.audit.positive_events.toLocaleString()}</b></span><span>可模拟入场 <b>{meta.audit.entry_states.eligible.toLocaleString()}</b></span><span>涨停开盘跳过 <b>{(meta.audit.entry_states.open_limit_up+meta.audit.entry_states.one_price_up).toLocaleString()}</b></span></div></>}
 <section className="panel"><h2>席位排名</h2><div className="seat-backtest-tools">
 <label>样本窗口／月份<Select showSearch optionFilterProp="label" aria-label="回测窗口" value={scope} onChange={chooseScope} options={[{label:'样本窗口',options:windows},{label:'逐月回看（按入场月）',options:(meta?.months||[]).map(m=>({value:m,label:m+(m===meta?.data_end.slice(0,7)?' · 不完整月':'')}))}]}/></label>
 <label>榜单周期<Select showSearch optionFilterProp="label" aria-label="回测榜单周期" value={period} onChange={setPeriod} options={periods}/></label>
 <label>持有期<Select showSearch optionFilterProp="label" aria-label="回测持有期" value={horizon} onChange={setHorizon} options={[1,3,5,10].map(n=>({value:n,label:n+' 个交易日'}))}/></label></div>
 <p className="footnote">信号 T 日披露，T+1 开盘买入，买入后的第 N 个交易日收盘卖出。排序按胜率区间下界；至少 {ranking.data?.min_trades??30} 笔、{ranking.data?.min_days??15} 个入场日期。{ranking.data?.interval}。月度排名为事后回看，不能用作月初选人依据。</p>
 <Table<Stat> aria-label="回测席位排名" key={scope+period+horizon} size="small" rowKey="profile" dataSource={ranking.data?.rows||[]} loading={!ranking.data&&!ranking.error} scroll={{x:1150}} pagination={{pageSize:10,showSizeChanger:false}} columns={[
 {title:'保守排名',width:105,render:(_,r)=>r.rank??'未达门槛'},
 {title:'关联席位',width:115,render:(_,r)=><Button type="link" className="seat-inline" onClick={()=>chooseProfile(r.profile)}>{r.profile}</Button>},
 {title:'已平仓样本',dataIndex:'n',align:'right'}, {title:'盈利比例',align:'right',render:(_,r)=>pct(r.win_rate)},
 {title:'胜率95%区间',align:'right',render:(_,r)=>r.ci_low==null?'—':`${pct(r.ci_low)} — ${pct(r.ci_high)}`},
 {title:'平均净收益',align:'right',render:(_,r)=>signed(r.mean_return)}, {title:'中位净收益',align:'right',render:(_,r)=>signed(r.median_return)},
 {title:'平均沪深300超额',align:'right',render:(_,r)=>signed(r.mean_excess)},
 {title:'未能入场',dataIndex:'entry_skipped',align:'right'}, {title:'缺失／未到期',align:'right',render:(_,r)=>`${(r.states.missing_path||0)+(r.states.unresolved_exit||0)} / ${r.states.right_censored||0}`}
 ]}/></section>
 <section id="seat-monthly" className="panel"><div className="section-head"><h2>逐月表现</h2><Select showSearch optionFilterProp="label" aria-label="回测关联席位" value={profile} onChange={setProfile} options={(meta?.profiles||[profile]).map(n=>({value:n,label:n}))}/></div>
 <p className="footnote">{profile} · {periods.find(p=>p.value===period)?.label} · 持有 {horizon} 日。按买入月归组，月份无有效结果显示 —；点击月份查看当月逐笔记录。</p>
 {monthly.error&&<Alert type="error" title={monthly.error}/>}
 {monthly.data?.nonoverlap&&<p className="seat-backtest-sensitivity">近12个完整月剔除同股重叠持仓：{monthly.data.nonoverlap.n} 笔，胜率 {pct(monthly.data.nonoverlap.win_rate)}，平均净收益 {signed(monthly.data.nonoverlap.mean_return)}。上一笔退出后才接受新的同股信号。</p>}
 <Table<Stat> aria-label="回测逐月表现" key={profile+period+horizon} size="small" rowKey="month" dataSource={monthly.data?.rows||[]} loading={!monthly.data&&!monthly.error} scroll={{x:1000}} pagination={{pageSize:12,showSizeChanger:false}} columns={[
 {title:'入场月',width:115,render:(_,r)=><Button type="link" className="seat-inline" onClick={()=>showMonth(r.month!)}>{r.month}</Button>},
 {title:'净买入事件',dataIndex:'signals',align:'right'}, {title:'未能入场',dataIndex:'entry_skipped',align:'right'}, {title:'已平仓',dataIndex:'n',align:'right'},
 {title:'盈利比例',align:'right',render:(_,r)=>pct(r.win_rate)}, {title:'平均净收益',align:'right',render:(_,r)=>signed(r.mean_return)},
 {title:'中位净收益',align:'right',render:(_,r)=>signed(r.median_return)}, {title:'平均超额',align:'right',render:(_,r)=>signed(r.mean_excess)},
 {title:'延后退出',dataIndex:'delayed',align:'right'}, {title:'缺失／未到期',align:'right',render:(_,r)=>`${(r.states.missing_path||0)+(r.states.unresolved_exit||0)} / ${r.states.right_censored||0}`}
 ]}/></section>
 <section id="seat-trades" className="panel"><h2>逐笔机会与模拟结果 · {profile}</h2><div className="seat-backtest-tools">
 <label>入场月份<Select showSearch optionFilterProp="label" aria-label="逐笔入场月份" value={month} onChange={setMonth} options={[{value:'',label:'全部月份'},...(meta?.months||[]).map(m=>({value:m,label:m}))]}/></label>
 <label>结果状态<Select showSearch optionFilterProp="label" aria-label="逐笔结果状态" value={outcome} onChange={setOutcome} options={['all','closed','entry_skipped','missing_path','right_censored','unresolved_exit'].map(s=>({value:s,label:s==='all'?'全部状态':states[s]}))}/></label></div>
 <p className="footnote">沿用所选席位、榜单周期和持有期，月份独立筛选。未入场不记零收益；展示股票名称为供应商当前简称，历史 ST 使用当日行情判定。</p>
 {trades.error&&<Alert type="error" title={trades.error}/>}
 <Table<Trade> aria-label="回测逐笔记录" size="small" rowKey={r=>r.profile+r.signal_date+r.code+r.period+r.horizon} dataSource={trades.data?.rows||[]} loading={!trades.data&&!trades.error} scroll={{x:1050}} pagination={{current:page,pageSize:10,total:trades.data?.total||0,showSizeChanger:false,onChange:setPage}} columns={[
 {title:'股票',width:130,render:(_,r)=><><Button type="link" className="seat-inline" onClick={()=>setPick(r)}>{r.name}</Button><small className="seat-code">{r.code}</small></>},
 {title:'信号日',dataIndex:'signal_date'}, {title:'入场日',render:(_,r)=>r.entry_date||'—'}, {title:'退出日',render:(_,r)=>r.exit_date||'—'},
 {title:'席位净买额（万）',align:'right',render:(_,r)=>amount(r.net)}, {title:'结果',render:(_,r)=><>{states[r.state]||r.state}{r.state==='entry_skipped'&&<small className="seat-code">{states[r.entry_state]||r.entry_state}</small>}</>},
 {title:'净收益',align:'right',render:(_,r)=>signed(r.net_return)}, {title:'超额收益',align:'right',render:(_,r)=>signed(r.excess_return)}
 ]}/></section>
 <details className="panel seat-backtest-method"><summary>计算口径、覆盖与下载</summary>
 <p>本次为固定回测版本，数据截至 {meta?.data_end||'—'}，不随复盘日期或后台行情刷新重新计算。单日榜、三日累计榜及其他周期分别统计；同一股票同席位同日同周期重复原因去重，金额冲突不合并。</p>
 <p>次日涨停开盘（含后来开板）、全天同价、停牌、ST、新股前五日或必要报价缺失不入场，也不延后追买。买入后的第 N 个交易日收盘退出，跌停收盘／停牌延后最多20个交易日；路径缺失和未到期分别保留。</p>
 <p>佣金单边万三、过户费单边十万分之一、卖出印花税万五、滑点单边万五，往返约0.212%。用原始开盘价和除权参考昨收串接收益；未模拟整手、资金占用、精确分红与最低佣金账本，不是可执行组合收益。</p>
 <p>席位关系为事后核验，历史营业部和公司名称可能由供应商重写，不等同当时已知身份。置信区间未做多重比较校正，同股信号可能重叠；对照非重叠结果，不能仅凭排名决定交易。</p>
 {meta&&<p>收益交叉核对：{meta.validation.daily_count} 条次日涨跌幅、{meta.validation.trade_count} 条持仓收益；原始披露、报价文件散列及固定协议随版本保留。</p>}
 <div className="seat-backtest-downloads">{[['monthly.csv','逐月回测 CSV'],['ranking.csv','完整排名 CSV'],['report.md','完整研究报告'],['protocol.json','固定回测协议']].map(([file,label])=><a key={file} href={'api/seat-backtest/download/'+file}>{label} ↗</a>)}</div>
 </details>
 {pick&&<DetailDrawer title="回测事件明细" label="回测事件明细" closeLabel="关闭回测事件" onClose={()=>setPick(null)} size={620}><div className="seat-backtest-method">
 <h2>{pick.name} <small>{pick.code}</small></h2><p>{pick.profile} · {periods.find(p=>p.value===pick.period)?.label} · 持有 {pick.horizon} 日</p>
 <p>信号日 {pick.signal_date} · 席位净买额 {amount(pick.net)} 万元 · 合并 {pick.disclosures} 条相同金额披露。</p>
 <p>入场判断：{states[pick.entry_state]||pick.entry_state}；{pick.state==='entry_skipped'?'未执行模拟买入。':`拟入场日 ${pick.entry_date||'—'}。`}</p>
 <p>计划退出 {pick.planned_exit||'—'} · 实际模拟退出 {pick.exit_date||'—'} · 延后 {pick.delay??'—'} 个交易日。</p>
 <p>结果：{states[pick.state]} · 毛收益 {signed(pick.gross_return)} · 费用后收益 {signed(pick.net_return)}。</p>
 <p>同期沪深300 {signed(pick.benchmark_return)} · 超额 {signed(pick.excess_return)}。</p>
 <p className="footnote">历史回测记录不等于实盘成交，未入场／缺失不计算收益。股票名称可能为供应商当前简称。</p>
 <a href={'https://data.eastmoney.com/stock/lhb/'+pick.code+'.html'} target="_blank" rel="noopener noreferrer">东方财富个股龙虎榜原始披露 ↗</a>
 </div></DetailDrawer>}
 </main>;
}
