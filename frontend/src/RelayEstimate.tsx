import {Popover} from 'antd';
import './relay.css';
export type Relay={status:string;probability:number|null;retrospective:boolean;version:string;group?:string;n?:number;hits?:number;days?:number;interval?:number[];audit_n?:number;audit_actual?:number;audit_mean?:number;audit_brier?:number};
export type RelayModel={version:string;label_end:string;train_start:string;train_end:string;expires:string;released_at:string;source:string;source_url:string;method:string;target:string;interval_method:string;audit_window:string[]};
const pct=(v:number|undefined)=>v==null?'—':`${(100*v).toFixed(1)}%`;
const labels:Record<string,string>={close_only:'收盘后估计',excluded_st:'ST 暂不覆盖',not_sealed:'未涨停',source_conflict:'来源冲突',unconfirmed:'封板未核实',unknown_height:'高度未知',historical_unavailable:'历史预测未存档',model_expired:'模型待更新',unsupported_regime:'制度未覆盖',insufficient_samples:'样本不足',insufficient_validation:'检验样本不足'};
export function RelayCell({value}:{value?:Relay}){
 if(!value||value.probability==null)return <span className="muted relay-empty">{value?labels[value.status]||'未估计':'未估计'}</span>;
 const proof=<div className="relay-proof"><strong>分组历史概率估计</strong><p>{value.group?.startsWith('20')?'创业板／科创板':'主板'} · {value.group?.endsWith(':4')?'4 板及以上':`${value.group?.split(':')[1]} 板`}；同组股票使用同一估计。</p>
 <p>历史 {value.n?.toLocaleString()} 个样本，次日再封板 {value.hits?.toLocaleString()} 次，覆盖 {value.days} 个交易日。</p>
 <p>95% 区块重采样区间：{value.interval?.map(pct).join('–')}。反映样本估计误差，不包含突发事件与模型误设。</p>
 <p>独立检验 {value.audit_n} 个样本：平均预测 {pct(value.audit_mean)}，实际再封板 {pct(value.audit_actual)}，Brier {value.audit_brier?.toFixed(3)}（越低越好）。</p>
 <p>未做个股专属校准。{value.retrospective?'此日为事后估算，非当时保存的预测。':''}版本 {value.version}。</p></div>;
 return <Popover content={proof} trigger={['click']} placement="bottomRight"><button className="relay-estimate" aria-label={`查看连板概率依据 ${pct(value.probability)}`}><b>≈{pct(value.probability)}</b><small>{value.retrospective?'事后估算':'分组估计'} · 查看依据</small></button></Popover>;
}
export function RelayMethod({model}:{model?:RelayModel}){
 if(!model)return null;
 return <details className="relay-method"><summary>连板概率口径与历史检验 · 模型标签截至 {model.label_end}</summary><p>目标：今日收盘封板后，下一交易日再次收盘封板。仅适用于已核实涨停价、连板高度和制度的沪深非 ST 股票；午间不估计。</p><p>{model.method}；训练 {model.train_start} 至 {model.train_end}，标签截至 {model.label_end}。滚动独立检验 {model.audit_window.join(' 至 ')}。这是分组统计估计，尚未做个股专属校准；点击数值查看同组实际命中率。</p><p>每组至少 100 个训练样本、20 个交易日及 50 个独立检验样本才显示数值；缺失与未涨停均不填 0%。模型固定版本 {model.version}，适用至 {model.expires}，到期显示待更新。历史事后估算单独标注。</p><p>来源：<a href={model.source_url} target="_blank" rel="noreferrer">{model.source}</a>。{model.interval_method}。</p></details>;
}
