import {useEffect,useState} from 'react';
import {Select} from 'antd';
import './etf-tracker.css';
type Row={code:string;index:string;manager:string;shares:number|null;prev_shares:number|null;share_delta:number|null;nav:number|null;net:number|null;amount:number|null;mean20:number|null;multiple:number|null;heavy:boolean|null;provisional:boolean;retained:string[];adjustment:boolean};
type Summary={net:number|null;net_count:number;count:number;inflow:number|null;outflow:number|null;heavy_count:number|null;volume_count:number};
type Result={day:string;previous?:string;revision:number|null;collected_at:string|null;status:string;rows:Row[];summary:Summary|null;trend:({day:string}&Summary)[];issues:Record<string,string>;disclosures:{fund:string;report_date:string;published:string;url:string;report:string;holders:{name:string;shares:number;pct:number}[]}[]};
const amt=(v:number|null|undefined,signed=false)=>v==null?'—':(signed&&v>0?'+':'')+(v/1e8).toLocaleString('zh-CN',{maximumFractionDigits:2,minimumFractionDigits:2});
const tone=(v:number|null|undefined)=>v==null?'muted':v>0?'up':v<0?'down':'muted';

export default function EtfTracker({date,refresh}:{date:string;refresh:number}){
 const [state,setState]=useState<{day:string;data?:Result;error?:string}>({day:''});
 const [index,setIndex]=useState('all'),[sort,setSort]=useState('net');
 useEffect(()=>{
  if(!date)return;
  const control=new AbortController();
  fetch('api/etf-tracker?'+new URLSearchParams({date}),{signal:control.signal})
   .then(r=>{if(!r.ok)throw Error();return r.json();})
   .then(data=>{if(!control.signal.aborted)setState({day:date,data});})
   .catch(()=>{if(!control.signal.aborted)setState(previous=>({day:date,data:previous.day===date?previous.data:undefined,error:'宽基 ETF 更新失败；保留已有存档，稍后自动重试。'}));});
  return()=>control.abort();
 },[date,refresh]);
 const data=state.day===date?state.data:undefined;
 const error=state.day===date?state.error:undefined;
 const filtered=(data?.rows||[]).filter(r=>index==='all'||r.index===index);
 const rows=[...filtered].sort((a,b)=>{
  const x=sort==='net'?a.net:a.multiple,y=sort==='net'?b.net:b.multiple;
  return x==null?y==null?0:1:y==null?-1:y-x;
 });
 const s=data?.summary;
 const max=Math.max(...(data?.trend||[]).map(t=>Math.abs(t.net||0)),1);
 return <section className="panel etf-panel" id="national-team">
 <div className="section-head"><div><h3>国家队资金观察</h3><p>宽基 ETF 申赎与放量 · {date||'读取复盘日期…'}</p></div><span className="etf-scope">10 只代表产品 · 7 类宽基</span></div>
 <p className="etf-explain">跟踪市场整体申赎的代理指标。买方身份需以机构公告或基金定期报告核实，日频数值无法识别国家队买卖。</p>
 {error&&<p className="notice">{error}</p>}
 {!data&&!error&&<p className="footnote">读取宽基 ETF 归档…</p>}
 {data&&data.rows.length===0&&<p className="etf-empty">所选日期暂无 ETF 归档。盘后采集，晚间及次晨复核。</p>}
 {data&&s&&<>
 <div className="etf-metrics">
 <div><span>样本估算净申赎</span><strong className={tone(s.net)}>{amt(s.net,true)}<small>亿</small></strong><small>可算 {s.net_count}/{s.count} 只 · 含实物申赎折价</small></div>
 <div><span>净申购 / 净赎回</span><strong><b className="up">{amt(s.inflow)}</b> / <b className="down">{amt(s.outflow)}</b></strong><small>亿元 · 按产品轧差后分别汇总</small></div>
 <div><span>成交额放量产品</span><strong>{s.heavy_count??'—'}<small> / {s.count} 只</small></strong><small>≥ 前 20 日均额的 1.5 倍 · 可比 {s.volume_count} 只</small></div>
 </div>
 <div className="etf-trend"><h4>最近 20 个归档交易日 · 样本净申赎</h4>
 <div className="etf-spark" aria-label="每日估算净申赎，零线以上红色为净申购，以下绿色为净赎回">{data.trend.map(t=><div key={t.day} className="etf-spark-day" title={t.day+' '+amt(t.net,true)+' 亿 · 覆盖 '+t.net_count+'/'+t.count}>
 <div className="etf-spark-track">{t.net!=null?<i className={t.net>=0?'positive':'negative'} style={{height:Math.abs(t.net)/max*45+'%',...(t.net>=0?{bottom:'50%'}:{top:'50%'})}}/>:<span>—</span>}</div>
 <small>{t.day.slice(5)}</small></div>)}</div></div>
 <div className="etf-toolbar"><Select aria-label="宽基指数筛选" value={index} onChange={setIndex} options={[{value:'all',label:'全部指数'},...[...new Set(data.rows.map(r=>r.index))].map(v=>({value:v,label:v}))]}/>
 <Select aria-label="ETF排序" value={sort} onChange={setSort} options={[{value:'net',label:'按净申赎'},{value:'volume',label:'按成交倍数'}]}/></div>
 <div className="table-wrap"><table className="etf-table"><thead><tr><th>指数 / ETF</th><th>估算净申赎（亿）</th><th>份额变化（亿份）</th><th>成交额（亿）</th><th>前20日均额（亿）</th><th>成交倍数 / 状态</th></tr></thead><tbody>
 {rows.map(r=><tr key={r.code}><td><strong>{r.index}</strong><small>{r.manager} · {r.code}</small></td>
 <td className={tone(r.net)}>{amt(r.net,true)}{r.adjustment&&<small>净值或份额调整待核对</small>}{r.provisional&&<small>深市盘后初值</small>}{r.retained.length>0&&<small>部分输入沿用同日存档</small>}</td>
 <td className={tone(r.share_delta)}>{amt(r.share_delta,true)}</td><td>{amt(r.amount)}</td><td>{amt(r.mean20)}</td>
 <td><b>{r.multiple==null?'—':r.multiple.toFixed(2)+'×'}</b><small className={r.heavy?'up':''}>{r.heavy==null?'基准不足':r.heavy?'放量':'未达放量阈值'}</small></td></tr>)}
 </tbody></table></div>
 <details className="etf-evidence"><summary>计算口径、数据来源与披露依据</summary>
 <p>估算净申赎 =（当日收盘份额 − 上一交易日收盘份额）× 当日单位净值。当前对应 {data.previous} → {data.day}。拆分、折算或异常净值变化暂停估算。</p>
 <p>成交倍数 = 当日成交额 ÷ 此前 20 个完整交易日成交额均值。日期或必要字段缺失保留为空。放量仅描述场内成交活跃程度。</p>
 <p>份额来自 <a href="https://www.sse.com.cn/market/funddata/volumn/etfvolumn/" target="_blank" rel="noreferrer">上交所</a> / <a href="https://www.szse.cn/market/fund/volume/etf/index.html" target="_blank" rel="noreferrer">深交所</a>，净值来自天天基金，成交量额来自腾讯财经。沪深300的多个产品分别统计，汇总覆盖当前固定样本。</p>
 <h4>已核验的历史持仓披露示例</h4>
 {data.disclosures.map(d=><div key={d.fund}><a href={d.url} target="_blank" rel="noreferrer">{d.fund} · {d.report}</a><p>持仓截至 {d.report_date} · 公告 {d.published} · 仅作为历史披露</p>{d.holders.map(h=><p key={h.name}>{h.name} · {amt(h.shares)} 亿份 · {h.pct}%</p>)}</div>)}
 {!data.disclosures.length&&<p>所选日期暂无已核验的机构持仓披露。</p>}
 </details>
 <p className="footnote">采集 {data.collected_at?.replace('T',' ').slice(0,19)} · 修订 {data.revision} · {data.status==='complete'?'样本完整':data.status==='provisional'?'盘后初值待确认':'部分数据待补'}。历史份额为事后回补，使用来源标记日期。</p>
 {Object.keys(data.issues).length>0&&<details className="etf-evidence"><summary>采集缺口 · {Object.keys(data.issues).length} 项</summary>{Object.entries(data.issues).map(([k,v])=><p key={k}>{k} · {v}</p>)}</details>}
 </>}
 </section>;
}
