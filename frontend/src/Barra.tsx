import {useEffect, useState} from 'react';
import {Alert, Button, Select, Spin, Table} from 'antd';
import './barra.css';

type Factor = {name:string; group:'市场'|'行业'|'风格'; value:number; n?:number};
type Result = {day:string|null; previous?:string; dates:string[]; version:string; reason?:string;
  n?:number; universe?:number; coverage?:number|null; r2?:number|null; factors:Factor[];
  benchmark?:string; window_start?:string; industry_date?:string; input_digest?:string;
  revisions?:{exposure:number; returns:number; raw:number[]}};
const definitions:Record<string,string> = {
  '规模':'流通市值的对数', '账面市值比':'市净率的倒数，仅使用正市净率',
  '换手活跃度':'上一交易日换手率的对数', '短期反转':'上一交易日涨跌幅取负值',
  'Beta':'过去 252 日相对沪深 300 的收益敏感度', '中期动量':'跳过最近 21 日的 126 日累计收益',
  '残差波动率':'个股对沪深 300 回归后的日残差标准差', '多周期流动性':'20、60、252 日平均换手率取对数后等权合成'
};
const percent = (value:number) => (value>0?'+':'') + value.toFixed(3) + '%';
function Reading({value}:{value:number}) {
  return <span className={value>0?'barra-pos':value<0?'barra-neg':''}>{percent(value)}</span>;
}

export default function Barra() {
  const [model,setModel] = useState('price_volume');
  const [date,setDate] = useState(''), [refresh,setRefresh] = useState(0);
  const [result,setResult] = useState<Result|null>(null), [error,setError] = useState('');
  const [loading,setLoading] = useState(true), [dates,setDates] = useState<string[]>([]);
  useEffect(()=>{
    const controller=new AbortController();
    setLoading(true);setError('');setResult(null);
    fetch('api/barra?model='+model+(date?'&day='+encodeURIComponent(date):''),{signal:controller.signal})
      .then(async response=>{if(!response.ok)throw Error('读取失败（'+response.status+'）');return response.json() as Promise<Result>;})
      .then(data=>{if(!controller.signal.aborted){setResult(data);setDates(data.dates);}})
      .catch(e=>{if(!controller.signal.aborted)setError(String(e.message));})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return ()=>controller.abort();
  },[date,refresh,model]);
  const styles=result?.factors.filter(f=>f.group==='风格')||[];
  const industries=result?.factors.filter(f=>f.group==='行业').sort((a,b)=>b.value-a.value)||[];
  const market=result?.factors.find(f=>f.group==='市场');
  const scale=Math.max(...styles.map(f=>Math.abs(f.value)),0.00001);
  return <main id="barra" className="barra-page">
    <header className="section-head"><div><h1>日度因子收益</h1><p>A 股市场、行业与风格的收盘横截面回归 · 自建 Barra 风格模型</p></div>
    <div className="daily-tools"><Select aria-label="因子模型" value={model}
      options={[{value:'price_volume',label:'八因子 · 历史量价'},{value:'archive',label:'四因子 · 复盘归档'}]}
      onChange={m=>{setDate('');setDates([]);setModel(m);}}/><Select showSearch aria-label="因子收益交易日" value={date} onChange={setDate}
      options={[{value:'',label:'最近收盘'},...dates.map(d=>({value:d,label:d}))]}/><Button onClick={()=>setRefresh(v=>v+1)} loading={loading}>刷新</Button></div></header>
    {error&&<Alert type="error" title={error} showIcon/>}
    {loading&&<Spin aria-label="正在读取收盘数据"/>}
    {result&&<><div className="barra-meta"><span>收益日 <b>{result.day||'—'}</b></span><span>暴露日 <b>{result.previous||'—'}</b></span>
      <span>入模 / 期初合格股票 <b>{result.n??'—'} / {result.universe??'—'}</b></span><span>覆盖率 <b>{result.coverage==null?'—':(result.coverage*100).toFixed(1)+'%'}</b></span></div>
      {result.reason?<Alert showIcon type="info" title={result.reason}/>:<>
      <section className="panel"><div className="section-head"><div><h2>市场与风格</h2><p>风格收益表示暴露高一个截面标准差所对应的当日收益差，其他暴露相同。</p></div></div>
      <p className="barra-market">市场因子 <b>{market&&<Reading value={market.value}/>}</b><span>加权 R² {result.r2==null?'—':result.r2.toFixed(3)}</span></p>
      {styles.map(f=><div className="barra-style" key={f.name}><div><b>{f.name}</b><small>{definitions[f.name]}</small></div>
        <div className="barra-track" aria-hidden="true"><i className={f.value>=0?'positive':'negative'} style={{width:(48*Math.abs(f.value)/scale)+'%'}}/></div>
        <b><Reading value={f.value}/></b></div>)}</section>
      <section className="panel"><h2>行业因子收益</h2><p>剔除市场和风格暴露后的行业收益，按流通市值加权之和为零。</p>
        <Table<Factor> size="small" dataSource={industries} rowKey="name" pagination={{pageSize:12,showSizeChanger:false}} columns={[
          {title:'行业',dataIndex:'name'},{title:'有效股票',dataIndex:'n',align:'right'},
          {title:'当日因子收益',align:'right',sorter:(a,b)=>a.value-b.value,render:(_,r)=><Reading value={r.value}/>}
        ]}/></section></>}
      <details className="panel"><summary>计算口径与输入版本</summary>
        {result.version==='cn-price-volume-v2'?<><p>沪深 A 股，期初排除 ST 及无有效交易股票。个股和沪深 300 均采用 BaoStock 收盘相对昨收参考价的日涨跌幅，流通市值、总市值和市净率取东方财富历史截面，行业按 BaoStock 证监会行业历史更新日匹配。</p><p>收益日只使用上一交易日及以前的暴露。Beta 和残差波动率使用带截距的 252 日回归，至少需 202 对有效收益。动量需要完整 126 日，流动性每个窗口至少 80% 有效。数据为事后回补的供应商历史版本。</p>
        {result.window_start&&<p>估计窗口 {result.window_start} 至 {result.previous} · 行业版本 {result.industry_date}</p>}</>:<p>使用上一交易日收盘暴露及当日供应商收盘涨跌幅。期初样本为有效交易的沪深非 ST 股票，行业归属取当时归档的东方财富行业。估值和换手数据取腾讯收盘行情。</p>}
        <p>各项风格暴露按样本的 1% 和 99% 分位缩尾，减去流通市值加权均值后除以等权标准差。回归权重为流通市值平方根。市场因子取行业截距的流通市值加权平均。</p>
        <p>缺失必要字段、行业归属不唯一或行业有效样本少于 5 只的股票排除。样本至少 100 只、覆盖至少 60%、至少两个行业，且样本数达到参数数的三倍，才发布估计。</p>
        <small>{result.version}{result.revisions&&' · 暴露归档 #'+result.revisions.exposure+' · 收益归档 #'+result.revisions.returns+' · 历史原始报价批次 '+result.revisions.raw.length}</small>
      </details></>}
  </main>;
}
