import {useEffect,useRef,useState,type ReactNode} from 'react';
import gsap from 'gsap';
import {ScrollTrigger} from 'gsap/ScrollTrigger';
import {useGSAP} from '@gsap/react';
import {Pagination} from 'antd';

gsap.registerPlugin(useGSAP,ScrollTrigger);

const destinations=[['overview','市场'],['sectors','板块'],['rotation','轮动'],['capital','资金'],['national-team','宽基ETF'],
  ['gainers','个股'],['lhb','龙虎榜'],['seat-backtest','游资回测'],['resonance','指数共振'],['barra','因子收益'],['daily','每日汇总'],['news','新闻']] as const;

export function DeskNav({news,seatBacktest=false,daily=false,barra=false,resonance=false,midday=false,children,onNews}:{news:boolean;seatBacktest?:boolean;daily?:boolean;barra?:boolean;resonance?:boolean;midday?:boolean;children:ReactNode;onNews:()=>void}){
 const [active,setActive]=useState('overview');
 useEffect(()=>{
   if(news||seatBacktest||daily||barra||resonance)return;
   const sync=()=>{
     const headings=destinations.filter(([id])=>id!=='news').map(([id])=>document.getElementById(id)).filter((el):el is HTMLElement=>!!el);
     const before=headings.filter(el=>el.getBoundingClientRect().top<=135);
     if(before.length) {
       const nearest=Math.max(...before.map(el=>el.getBoundingClientRect().top));
       const equal=before.filter(el=>Math.abs(el.getBoundingClientRect().top-nearest)<2);
       setActive(equal.find(el=>'#'+el.id===location.hash)?.id||equal[0].id);
     } else setActive('overview');
   };
   let raf=0;
   const schedule=()=>{if(!raf)raf=requestAnimationFrame(()=>{raf=0;sync();});};
   let anchorId='',layoutFrame=0;
   const release=()=>{anchorId='';cancelAnimationFrame(layoutFrame);};
   const align=()=>{
     if(!anchorId)return;
     cancelAnimationFrame(layoutFrame);
     layoutFrame=requestAnimationFrame(()=>{
       // Loading the archive date can remount the rankings beneath the same ID.
       document.getElementById(anchorId)?.scrollIntoView({block:'start'});
       schedule();
     });
   };
   const follow=()=>{
     const id=location.hash.slice(1);
     anchorId=id==='news'?'':id;
     align();
     schedule();
   };
   // Async market sections above a deep link can grow after the initial jump.
   const layout=new ResizeObserver(align);
   layout.observe(document.getElementById('top')!);
   const inputs=['wheel','touchstart','pointerdown','keydown'];
   inputs.forEach(event=>window.addEventListener(event,release,{passive:true}));
   follow();window.addEventListener('hashchange',follow);window.addEventListener('scroll',schedule,{passive:true});
   return()=>{layout.disconnect();release();inputs.forEach(event=>window.removeEventListener(event,release));
     window.removeEventListener('hashchange',follow);window.removeEventListener('scroll',schedule);cancelAnimationFrame(raf);};
 },[news,seatBacktest,daily,barra,resonance,midday]);
 return <header className="desk-nav"><div className="nav-inner">
 <a className="desk-brand" href="#top" aria-label="复盘研究台首页"><svg viewBox="0 0 34 34" aria-hidden="true"><rect width="34" height="34" rx="7"/><path d="M8 22h4v5H8zm7-9h4v14h-4zm7-6h4v20h-4z" fill="currentColor"/></svg><b>复盘研究台</b></a>
 <nav className="desk-links" aria-label="复盘导航">{destinations.filter(([id])=>!midday||(id!=='national-team'&&id!=='lhb')).map(([id,label])=><a key={id} href={'#'+id}
 aria-current={(seatBacktest?id==='seat-backtest':resonance?id==='resonance':barra?id==='barra':daily?id==='daily':news?id==='news':id===active)?'location':undefined} onClick={id==='news'?onNews:undefined}>{label}</a>)}</nav>
 <div className="nav-tools">{children}</div></div></header>;
}

type Action={label:string;href?:string;run?:()=>void};
function ActionLink({action,secondary=false}:{action:Action;secondary?:boolean}){
 const cls='cover-action'+(secondary?' secondary':'');
 return action.href?<a className={cls} href={action.href} onClick={action.run}>{action.label}<span aria-hidden="true">↗</span></a>:
 <button className={cls} onClick={action.run}>{action.label}<span aria-hidden="true">↗</span></button>;
}

export function Cover({title,description,actions}:{title:string;description:string;actions:[Action,Action]}){
 return <header className="research-cover"><div className="cover-content"><h1 className="max-w-5xl">{title}</h1>
 <p>{description}</p><div className="cover-actions"><ActionLink action={actions[0]}/><ActionLink secondary action={actions[1]}/></div></div></header>;
}

export function Chapter({id,title,copy,aside}:{id?:string;title:string;copy:string[];aside?:ReactNode}){
 const ref=useRef<HTMLDivElement>(null);
 useGSAP(()=>{
   const media=gsap.matchMedia();media.add('(min-width: 801px) and (prefers-reduced-motion: no-preference)',()=>{
     gsap.fromTo('.chapter-copy span',{opacity:.72},{opacity:1,ease:'none',stagger:.3,scrollTrigger:{
       trigger:ref.current,start:'top 94%',end:'top 65%',scrub:true}});
   });return()=>media.revert();
 },{scope:ref});
 return <div id={id} ref={ref} className="chapter-head"><div><h2>{title}</h2><p className="chapter-copy">{copy.map(t=><span key={t}>{t}</span>)}</p></div>{aside}</div>;
}

export type DeskNote={title:string;value:string;description:string;href:string};
export function NotesDeck({notes}:{notes:DeskNote[]}){
 const ref=useRef<HTMLDivElement>(null);const [current,setCurrent]=useState(0);
 useGSAP(()=>{
   const media=gsap.matchMedia();
   media.add('(prefers-reduced-motion: no-preference)',()=>{
     ref.current?.querySelectorAll<HTMLElement>('.note-card').forEach((el,i)=>{
       const depth=(i-current+notes.length)%notes.length;
       gsap.fromTo(el,{y:22+depth*7,scale:1-depth*.04},{y:depth*7,scale:1-depth*.022,
         ease:'none',scrollTrigger:{trigger:ref.current,start:'top bottom',end:'center 64%',scrub:true}});
     });
   });return()=>media.revert();
 },{scope:ref,dependencies:[current,notes.length],revertOnUpdate:true});
 return <aside ref={ref} className="notes-deck" aria-label="复盘摘记"><div className="deck-head"><h3>复盘摘记</h3>
 <div className="deck-controls"><button aria-label="上一条摘记" onClick={()=>setCurrent((current+notes.length-1)%notes.length)}>‹</button>
 <button aria-label="下一条摘记" onClick={()=>setCurrent((current+1)%notes.length)}>›</button></div></div>
 <div className="notes-stack">{notes.map((n,i)=>{const depth=(i-current+notes.length)%notes.length;
 return <article className="note-card" key={n.title} aria-hidden={depth!==0} style={{zIndex:notes.length-depth,transform:'translateY('+depth*7+'px) scale('+(1-depth*.022)+')'}}>
 <p>{n.title}</p><strong>{n.value}</strong><small>{n.description}</small><a href={n.href} tabIndex={depth===0?0:-1}>查看依据 <span>↗</span></a></article>;})}</div>
 <span className="sr-only" aria-live="polite">{notes[current].title}，{notes[current].value}</span></aside>;
}

export function usePages<T>(items:T[],size:number,scope:string){
 const [state,setState]=useState({scope,page:0});
 const count=Math.ceil(items.length/size);
 const page=state.scope===scope?Math.min(state.page,Math.max(0,count-1)):0;
 return {visible:items.slice(page*size,(page+1)*size),offset:page*size,page,count,
         select:(next:number)=>setState({scope,page:Math.max(0,Math.min(count-1,next))})};
}
export function Pager({page,count,select,label}:{page:number;count:number;select:(n:number)=>void;label:string}){
 return count>1?<nav className="desk-pager" aria-label={label+'分页'}>
 <Pagination size="small" current={page+1} total={count} pageSize={1} showSizeChanger={false}
   showLessItems onChange={next=>select(next-1)}/></nav>:null;
}

export function SignalTab({id,label,count,active,select}:{id:string;label:string;count:number|null;active:boolean;select:(id:string)=>void}){
 const hover=useRef<number|undefined>(undefined);
 const cancelHover=()=>window.clearTimeout(hover.current);
 useEffect(()=>cancelHover,[]);
 return <button className="signal-tab" aria-expanded={active} aria-controls={'signal-'+id}
 onPointerEnter={e=>{if(!active&&e.pointerType==='mouse')hover.current=window.setTimeout(()=>select(id),320);}}
 onPointerLeave={cancelHover} onClick={()=>{cancelHover();select(id);}}>
 {label}<small>{count??'—'}</small></button>;
}

const sources=['腾讯财经 / 行情','东方财富 / 行业与龙虎榜','同花顺 / 概念板块','财联社 / 电报',
 '新浪财经 / 个股资讯','CNBC World / 海外报道','美联储 / 公告','NVIDIA / 新闻室'];
export function SourceStrip(){
 const [paused,setPaused]=useState(false);
 return <div className="source-strip"><strong>公开来源</strong><div className={'source-window'+(paused?' paused':'')}>
 <div className="source-track">{[0,1].map(copy=><div key={copy} aria-hidden={copy===1} className="source-set">{sources.map(s=><span key={s}>{s}</span>)}</div>)}</div></div>
 <button className="marquee-control" aria-label={paused?'继续滚动来源':'暂停滚动来源'} aria-pressed={paused} onClick={()=>setPaused(!paused)}>{paused?'继续':'暂停'}</button></div>;
}

export function EndAction({title,description,action}:{title:string;description:string;action:Action}){
 return <div className="end-action"><div><h2>{title}</h2><p>{description}</p></div><ActionLink action={action}/></div>;
}

// Charts and disclosure tables change height after async reads; keep scroll ranges current.
export function useDeskRefresh(){
 useEffect(()=>{
   let frame=0;let height=0;
   const observer=new ResizeObserver(([entry])=>{
     const measured=Math.round(entry.contentRect.height);
     if(height===measured)return;height=measured;cancelAnimationFrame(frame);
     frame=requestAnimationFrame(()=>ScrollTrigger.refresh());
   });
   observer.observe(document.body);
   return()=>{observer.disconnect();cancelAnimationFrame(frame);};
 },[]);
}
