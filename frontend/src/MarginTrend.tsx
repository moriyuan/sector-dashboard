import {useEffect,useMemo,useRef,useState} from 'react';
import {Segmented} from 'antd';
import * as echarts from 'echarts/core';
import type {EChartsOption} from 'echarts';
import {LineChart} from 'echarts/charts';
import {DataZoomComponent,GridComponent,TooltipComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
import './margin-trend.css';

echarts.use([LineChart,DataZoomComponent,GridComponent,TooltipComponent,CanvasRenderer]);

type Week={week_end:string;date:string|null;balance:number|null;sh_balance:number|null;sz_balance:number|null;change:number|null;change_pct:number|null;provisional:boolean;missing:boolean;backfilled:boolean};
export type Financing={status:'available'|'missing'|'stale';source:string;source_url:string;unit:'CNY';scope:string;latest_date:string|null;collected_at:string|null;revision:number|null;backfilled:boolean;publication_note:string;weeks:Week[]};
const amount=(value:number|null|undefined,signed=false)=>value==null?'—':`${signed&&value>0?'+':''}${(value/1e8).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})}`;
const percent=(value:number|null|undefined)=>value==null?'—':`${value>0?'+':''}${value.toFixed(2)}%`;
const tone=(value:number|null|undefined)=>value==null?'muted':value>0?'up':value<0?'down':'muted';
const escape=echarts.format.encodeHTML;

export default function MarginTrend({data,loading=false,error=false}:{data?:Financing;loading?:boolean;error?:boolean}){
 const [period,setPeriod]=useState(104);
 const chartRef=useRef<HTMLDivElement>(null);
 const weeks=useMemo(()=>(data?.weeks||[]).slice(-period),[data,period]);
 const latest=[...(data?.weeks||[])].reverse().find(row=>row.balance!=null);
 const hasData=weeks.some(row=>row.balance!=null);
 const option=useMemo<EChartsOption>(()=>({
  animation:false,
  grid:{left:68,right:24,top:28,bottom:65},
  tooltip:{trigger:'axis',confine:true,formatter:params=>{
   const item=(Array.isArray(params)?params:[params])[0];
   const row=weeks[item.dataIndex];
   if(!row)return '';
   return [`<b>${escape(row.week_end)} 当周${row.provisional?' · 未完周':''}</b>`,
    row.date?`实际取值日 ${escape(row.date)}`:'缺少本周数据',
    `融资余额 ${amount(row.balance)} 亿元`,
    `较上周末 ${amount(row.change,true)} 亿元（${percent(row.change_pct)}）`,
    row.balance!=null?`沪市 ${amount(row.sh_balance)} / 深市 ${amount(row.sz_balance)} 亿元`:'期末交易日未披露时留空，不以前一日替代',
    row.backfilled?'历史事后回补':'',row.provisional?'本周暂定值，等待周末披露':''].filter(Boolean).join('<br/>');
  }},
  xAxis:{type:'category',boundaryGap:false,data:weeks.map(row=>row.week_end),axisLine:{lineStyle:{color:'#c8d0c3'}},axisTick:{show:false},
   axisLabel:{color:'#768171',fontSize:11,hideOverlap:true,formatter:(value:string)=>value.slice(2)}},
  yAxis:{type:'value',name:'亿元',scale:true,axisLabel:{color:'#768171',fontSize:11,formatter:(value:number)=>value.toLocaleString('zh-CN',{maximumFractionDigits:0})},
   nameTextStyle:{color:'#768171'},splitLine:{lineStyle:{color:'#e8ece3'}}},
  dataZoom:[{type:'inside',zoomOnMouseWheel:false,moveOnMouseWheel:false},{type:'slider',height:16,bottom:8,borderColor:'#dce2d5',fillerColor:'rgba(81,112,70,.12)',
   handleStyle:{color:'#7f9774',borderColor:'#647d58'},textStyle:{color:'#768171',fontSize:10},showDetail:false}],
  series:[{name:'沪深融资余额',type:'line',connectNulls:false,smooth:false,showSymbol:true,symbolSize:3,
   lineStyle:{color:'#43664f',width:2},itemStyle:{color:'#43664f'},
   data:weeks.map(row=>({value:row.balance==null?null:row.balance/1e8,symbol:row.provisional?'emptyDiamond':'circle',symbolSize:row.provisional?9:3,
    itemStyle:{color:row.provisional?'#ad7c38':'#43664f',borderWidth:row.provisional?2:0}}))}]
 }),[weeks]);
 useEffect(()=>{
  const element=chartRef.current;
  if(!element||!hasData)return;
  let chart:echarts.ECharts|undefined;
  const render=()=>{
   if(!element.clientWidth||!element.clientHeight)return;
   if(chart){chart.resize();return;}
   chart=echarts.init(element,undefined,{renderer:'canvas'});
   chart.setOption(option);
  };
  const resize=new ResizeObserver(render);
  resize.observe(element);
  render();
  return()=>{resize.disconnect();chart?.dispose();};
 },[option,hasData]);
 return <section className="panel margin-trend" aria-labelledby="margin-trend-title">
  <div className="section-head margin-head"><div><h3 id="margin-trend-title">沪深融资余额 · 周度</h3>
   <p>沪深合计 · 单位：亿元 · 数据截至 {data?.latest_date||'待披露'}</p></div>
   <Segmented aria-label="融资余额观察区间" size="small" value={period} options={[{value:26,label:'近半年'},{value:52,label:'近1年'},{value:104,label:'近2年'}]} onChange={value=>setPeriod(Number(value))}/>
  </div>
  {hasData&&latest&&<div className="margin-summary"><span>最近周度取值 <strong>{amount(latest.balance)}<small> 亿元</small></strong></span>
   <span>{latest.provisional?'较上周末（暂定）':'周环比'} <b className={tone(latest.change)}>{amount(latest.change,true)} 亿元</b> <em className={tone(latest.change_pct)}>（{percent(latest.change_pct)}）</em></span>
   <span className="margin-sample-date">{latest.date} {latest.provisional&&<b className="margin-provisional">未完周</b>}</span></div>}
  {data?.status==='stale'&&<p className="notice">融资余额更新暂有延迟，展示上次已披露存档，实际数据日期见上方。</p>}
  <div key="margin-chart" ref={chartRef} className="margin-chart" hidden={!hasData} role="img" aria-label={`沪深融资余额周度曲线，观察区间最近${period}周，缺失周保留断点`}/>
  {!hasData&&<div key="margin-empty" className="margin-empty" role="status"><strong>{error?'融资余额数据暂不可用':loading?'正在读取融资余额…':'所选日期暂无已披露融资余额'}</strong>
    <span>{error?'请刷新重试，已有其他复盘数据仍可查看。':'仅展示真实披露数据，缺失周保留为空。'}</span></div>}
  <p className="footnote margin-method">{data?.publication_note||'融资余额按 T+1 披露，非实时行情。'} 每周取期末存量，不将周内余额相加；菱形为未完周暂定值，缺失周保留断点。纵轴按余额区间缩放。</p>
  {data?.backfilled&&<p className="footnote">历史为事后回补，不代表当时已知数据。</p>}
  {data&&<p className="footnote margin-source">来源 {data.source_url?<a href={data.source_url} target="_blank" rel="noreferrer">{data.source}</a>:data.source} · {data.scope}
   {data.collected_at&&` · 采集 ${data.collected_at.replace('T',' ').slice(0,19)}`}{data.revision!=null&&` · 修订 ${data.revision}`}</p>}
 </section>;
}
