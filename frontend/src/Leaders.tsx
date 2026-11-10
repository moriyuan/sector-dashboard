import {useEffect,useRef,useState} from 'react';
import {Button,Input,Popconfirm,message} from 'antd';
import {DetailDrawer} from './DeskUI';
import './leaders.css';

type Row={code:string;name:string;note:string;added_at:string};
type Pool={pool:'emotion'|'trend';name:string;rows:Row[]};
type Board={pools:Pool[]};
type Perf={d1:number|null;d10:number|null;d20:number|null};
type PerfBoard={date:string|null;pools:Record<'emotion'|'trend',(Row&Perf)[]>};
type Hit={code:string;name:string};
type UniRow={code:string;name:string;py:string};

const POOL_HINT:Record<string,string>={
 emotion:'情绪核心：冰点分/高潮分的龙头维度取这里的股票，每日收盘后按池内涨跌幅计算。池空时用昨日连板股兜底。',
 trend:'趋势核心：仅做记录，暂未参与评分（预留趋势维度）。',
};

const pctCls=(v:number|null)=>v==null?'muted':v>0?'up':v<0?'down':'muted';
const pctText=(v:number|null)=>v==null?'—':`${v>0?'+':''}${v.toFixed(2)}%`;

export default function Leaders({entry}:{entry?:React.ReactNode}){
 const [show,setShow]=useState(false);
 const [board,setBoard]=useState<Board|null>(null);
 const [perf,setPerf]=useState<PerfBoard|null>(null);
 const [failed,setFailed]=useState(false);
 const [tab,setTab]=useState<'emotion'|'trend'>('emotion');
 const [query,setQuery]=useState('');
 const [hits,setHits]=useState<Hit[]>([]);
 const [sel,setSel]=useState<Hit|null>(null);
 const [note,setNote]=useState('');
 const [editCode,setEditCode]=useState<string|null>(null);
 const [editVal,setEditVal]=useState('');
 const [busy,setBusy]=useState(false);
 const [msg,ctx]=message.useMessage();
 const uniRef=useRef<UniRow[]|null>(null);
 const load=()=>{
   fetch('api/leaders').then(r=>{if(!r.ok)throw Error(String(r.status));return r.json();})
     .then((b:Board)=>{setBoard(b);setFailed(false);}).catch(()=>{setBoard(null);setFailed(true);});
   fetch('api/leaders/perf').then(r=>{if(!r.ok)throw Error(String(r.status));return r.json();})
     .then((p:PerfBoard)=>setPerf(p)).catch(()=>setPerf(null));
 };
 useEffect(()=>{load();},[]);
 // 模糊搜索：优先走服务端；静态预览下退化为本地名单检索
 useEffect(()=>{
   const q=query.trim();
   if(!q||sel){setHits([]);return;}
   const t=setTimeout(async()=>{
     try{
       const r=await fetch('api/stocks/search?q='+encodeURIComponent(q));
       if(!r.ok)throw Error(String(r.status));
       const d=await r.json();
       setHits(d.rows||[]);
     }catch{
       try{
         if(!uniRef.current){
           const r=await fetch('demo-data/stock-universe.json');
           uniRef.current=await r.json();
         }
         const uni=uniRef.current||[],lq=q.toLowerCase();
         const cm=uni.filter(s=>s.code.startsWith(lq));
         const pm=uni.filter(s=>!cm.includes(s)&&s.py.startsWith(lq));
         const nm=uni.filter(s=>!cm.includes(s)&&!pm.includes(s)&&s.name.includes(q));
         setHits([...cm,...pm,...nm].slice(0,10).map(s=>({code:s.code,name:s.name})));
       }catch{setHits([]);}
     }
   },250);
   return ()=>clearTimeout(t);
 },[query,sel]);
 const mutate=async(url:string,body?:object,method='POST')=>{
   setBusy(true);
   try{
     const r=await fetch(url,{method,headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
     if(!r.ok){
       const detail=(await r.json().catch(()=>null))?.detail;
       if(r.status===422&&detail){msg.warning(String(detail));return false;}
       throw Error(String(r.status));
     }
     await load();
     return true;
   }catch{
     msg.warning('当前为静态预览，修改不会保存；本地启动服务后可正常编辑。');
     return false;
   }finally{setBusy(false);}
 };
 const add=async()=>{
   if(!sel)return;
   if(await mutate('api/leaders',{pool:tab,code:sel.code,name:sel.name,note:note.trim()})){
     setSel(null);setQuery('');setNote('');
   }
 };
 const saveNote=async(r:Row)=>{
   if(await mutate(`api/leaders/${tab}/${r.code}/note`,{note:editVal.trim()},'PUT'))setEditCode(null);
 };
 const pool=board?.pools.find(p=>p.pool===tab);
 const perfRows=perf?.pools?.[tab]||[];
 const perfOf=(c:string)=>perfRows.find(r=>r.code===c);
 const emoCount=board?.pools.find(p=>p.pool==='emotion')?.rows.length||0;
 const d1Label=perf?.date?`${perf.date.slice(5)} 收盘`:'收盘';
 return <>{ctx}
  <span className="leaders-entry-wrap" onClick={e=>{e.stopPropagation();setShow(true);load();}}>
   {entry??<Button className="leaders-entry">龙头池{emoCount?` ${emoCount}`:''}</Button>}
  </span>
  {show&&<DetailDrawer title="核心龙头股票池" label="核心龙头股票池" closeLabel="关闭龙头池" size={520} onClose={()=>setShow(false)}>
   <div className="ld-tabs">
    {(['emotion','trend'] as const).map(p=><button key={p} className={`ld-tab${tab===p?' on':''}`} onClick={()=>setTab(p)}>
      {p==='emotion'?'情绪核心':'趋势核心'}<small>{board?.pools.find(q=>q.pool===p)?.rows.length??0}</small>
    </button>)}
   </div>
   <p className="ld-hint">{POOL_HINT[tab]}</p>
   <div className="ld-add">
    {sel?<span className="ld-sel"><b>{sel.name}</b><em>{sel.code}</em>
      <a onClick={()=>{setSel(null);setQuery('');}} aria-label="取消选择">×</a></span>
     :<div className="ld-search">
      <Input placeholder="搜股票：拼音首字母/名称/代码，如 jygf" value={query} disabled={busy}
        onChange={e=>setQuery(e.target.value)} allowClear/>
      {!!hits.length&&<ul className="ld-hits">
       {hits.map(h=><li key={h.code} onClick={()=>{setSel(h);setHits([]);}}>
         <b>{h.name}</b><em>{h.code}</em></li>)}
      </ul>}
     </div>}
    <Input placeholder="个股属性（选填），如 玄学抱团总龙头" value={note} maxLength={30} disabled={busy}
      onChange={e=>setNote(e.target.value)} onPressEnter={add} style={{width:190}}/>
    <Button type="primary" onClick={add} loading={busy} disabled={!sel}>添加</Button>
   </div>
   {failed&&<div className="empty"><strong>股票池读取失败</strong><p>本地服务暂不可用；静态预览中列表为演示数据，修改不会保存。</p></div>}
   {!failed&&pool&&<>
    {!pool.rows.length&&<p className="ld-empty">{tab==='emotion'?'池子是空的，评分暂用昨日连板股兜底。':'池子是空的。'}</p>}
    {!!pool.rows.length&&<div className="ld-head">
     <span className="ld-h-id">个股</span>
     <span className="ld-h-perf"><i>{d1Label}</i><i>近10日</i><i>近20日</i></span>
    </div>}
    <ul className="ld-list">
     {pool.rows.map(r=>{const p=perfOf(r.code);return <li key={r.code} className={`ld-row ${tab}`}>
      <div className="ld-id"><code>{r.code}</code>
       <div className="ld-nn"><span className="ld-name">{r.name}</span>
        {editCode===r.code
         ?<span className="ld-note-edit"><Input size="small" value={editVal} maxLength={30} autoFocus
             placeholder="个股属性，留空清除"
             onChange={e=>setEditVal(e.target.value)} onPressEnter={()=>saveNote(r)}/>
           <Button size="small" type="link" onClick={()=>saveNote(r)} loading={busy}>存</Button>
           <Button size="small" type="text" onClick={()=>setEditCode(null)}>取消</Button></span>
         :<a className={`ld-note${r.note?'':' empty'}`} title="点击修改个股属性"
             onClick={()=>{setEditCode(r.code);setEditVal(r.note||'');}}>{r.note||'＋属性'}</a>}
       </div>
       <small>{r.added_at?.slice(0,10)} 加入</small></div>
      <div className="ld-perf">
       {[p?.d1,p?.d10,p?.d20].map((v,i)=><span key={i} className={pctCls(v??null)}>{pctText(v??null)}</span>)}
      </div>
      <Popconfirm title={`移出 ${r.name}？`} okText="移出" cancelText="取消" okButtonProps={{danger:true,loading:busy}}
        onConfirm={()=>mutate(`api/leaders/${tab}/${r.code}`,undefined,'DELETE')}>
       <Button size="small" danger type="text" loading={busy}>移出</Button>
      </Popconfirm>
     </li>;})}
    </ul>
   </>}
  </DetailDrawer>}
 </>;
}
