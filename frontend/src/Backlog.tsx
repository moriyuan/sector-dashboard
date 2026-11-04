import {useEffect,useState} from 'react';
import {Badge,Button,Input,Modal,Popconfirm,message} from 'antd';
import {DetailDrawer} from './DeskUI';
import './backlog.css';

type Item={id:number;title:string;detail:string;created_at:string;done:number;done_at:string|null};
type Board={rows:Item[];open:number;done:number};

const day=(v:string)=>v?v.slice(0,10):'';

// 卡片配色：与整站绿调协调，但每张卡有自己的性格
const PALETTE=[
 {accent:'#3f7a52',tint:'#eef5ef',soft:'#dcebdd'},
 {accent:'#b87a1e',tint:'#faf3e2',soft:'#f1e3c2'},
 {accent:'#4c5fae',tint:'#eef1fa',soft:'#dbe1f3'},
 {accent:'#b0523c',tint:'#faecea',soft:'#f2d7d1'},
 {accent:'#2e7d74',tint:'#e8f4f2',soft:'#d2e9e5'},
 {accent:'#7d4a72',tint:'#f6ecf4',soft:'#ecd9e8'},
];
const tone=(id:number)=>PALETTE[id%PALETTE.length];

export default function Backlog(){
 const [show,setShow]=useState(false);
 const [board,setBoard]=useState<Board|null>(null);
 const [failed,setFailed]=useState(false);
 const [title,setTitle]=useState('');
 const [detail,setDetail]=useState('');
 const [busy,setBusy]=useState(false);
 const [editing,setEditing]=useState<Item|null>(null);
 const [editTitle,setEditTitle]=useState('');
 const [editDetail,setEditDetail]=useState('');
 const [msg,ctx]=message.useMessage();
 const load=()=>fetch('api/backlog').then(r=>{if(!r.ok)throw Error(String(r.status));return r.json();})
   .then((b:Board)=>{setBoard(b);setFailed(false);}).catch(()=>{setBoard(null);setFailed(true);});
 useEffect(()=>{load();},[]);
 const mutate=async(url:string,body?:object,method='POST')=>{
   setBusy(true);
   try{
     const r=await fetch(url,{method,headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
     if(!r.ok)throw Error(String(r.status));
     await load();
     return true;
   }catch{
     msg.warning('当前为静态预览，修改不会保存；本地启动服务后可正常编辑。');
     return false;
   }finally{setBusy(false);}
 };
 const add=async()=>{
   const text=title.trim();
   if(!text)return;
   if(await mutate('api/backlog',{title:text,detail:detail.trim()})){setTitle('');setDetail('');}
 };
 const openEdit=(r:Item)=>{setEditing(r);setEditTitle(r.title);setEditDetail(r.detail);};
 const saveEdit=async()=>{
   if(!editing||!editTitle.trim())return;
   if(await mutate(`api/backlog/${editing.id}`,{title:editTitle,detail:editDetail},'PATCH'))setEditing(null);
 };
 const removeEdit=async()=>{
   if(!editing)return;
   if(await mutate(`api/backlog/${editing.id}`,undefined,'DELETE'))setEditing(null);
 };
 const openItems=board?.rows.filter(r=>!r.done)||[];
 const doneItems=board?.rows.filter(r=>r.done)||[];
 return <>{ctx}
  <Badge count={board?.open||0} size="small" offset={[-4,4]}>
   <Button className="backlog-entry" onClick={()=>{setShow(true);load();}}>待办事项</Button>
  </Badge>
  {show&&<DetailDrawer title="待开发事项" label="待开发事项" closeLabel="关闭待开发事项" size={560} onClose={()=>setShow(false)}>
   <div className="bl-add">
    <Input placeholder="事项主题，例如：板块拥挤度指标" value={title} maxLength={40} showCount
      onChange={e=>setTitle(e.target.value)} onPressEnter={add} disabled={busy}/>
    <Input.TextArea placeholder="补充描述：要做什么、为什么做、做到什么程度（可选）" value={detail}
      maxLength={300} showCount autoSize={{minRows:2,maxRows:4}}
      onChange={e=>setDetail(e.target.value)} disabled={busy}/>
    <Button type="primary" block onClick={add} loading={busy} disabled={!title.trim()}>添加事项</Button>
   </div>
   {failed&&<div className="empty"><strong>清单读取失败</strong><p>本地服务暂不可用；静态预览中列表为演示数据，修改不会保存。</p></div>}
   {!failed&&board&&<>
    <h4 className="bl-group"><span className="bl-dot open"/>进行中 · {board.open}</h4>
    {!openItems.length&&<p className="bl-empty">没有待开发事项，添加一个吧。</p>}
    <ul className="bl-list">
     {openItems.map(r=>{const c=tone(r.id);return <li key={r.id} className="bl-card"
       style={{'--ac':c.accent,'--tint':c.tint,'--soft':c.soft} as React.CSSProperties}
       onClick={()=>openEdit(r)} title="点击编辑">
      <div className="bl-card-head">
       <span className="bl-card-mark"/>
       <span className="bl-card-title">{r.title}</span>
      </div>
      {r.detail&&<p className="bl-card-detail">{r.detail}</p>}
      <div className="bl-card-foot">
       <small>创建于 {day(r.created_at)}</small>
       <Button size="small" loading={busy} onClick={e=>{e.stopPropagation();mutate(`api/backlog/${r.id}/done`);}}>完成</Button>
      </div>
     </li>;})}
    </ul>
    {!!doneItems.length&&<><h4 className="bl-group"><span className="bl-dot done"/>已完成 · {board.done}</h4>
    <ul className="bl-list">
     {doneItems.map(r=><li key={r.id} className="bl-card done" onClick={()=>openEdit(r)} title="点击编辑">
      <div className="bl-card-head">
       <span className="bl-card-check"/>
       <span className="bl-card-title">{r.title}</span>
      </div>
      {r.detail&&<p className="bl-card-detail">{r.detail}</p>}
      <div className="bl-card-foot"><small>{day(r.done_at||'')} 完成</small></div>
     </li>)}
    </ul></>}
   </>}
  </DetailDrawer>}
  <Modal open={!!editing} title={editing?.done?'编辑事项（已完成）':'编辑事项'} width={440}
    onCancel={()=>setEditing(null)} onOk={saveEdit} okText="保存" cancelText="取消"
    okButtonProps={{loading:busy,disabled:!editTitle.trim()}}>
   <div className="bl-edit">
    <label>主题</label>
    <Input value={editTitle} maxLength={40} showCount onChange={e=>setEditTitle(e.target.value)}/>
    <label>描述</label>
    <Input.TextArea value={editDetail} maxLength={300} showCount autoSize={{minRows:3,maxRows:6}}
      placeholder="补充描述（可选）" onChange={e=>setEditDetail(e.target.value)}/>
    <Popconfirm title="删除该事项？" description="删除后不可恢复。" okText="删除" cancelText="再想想"
      okButtonProps={{danger:true,loading:busy}} onConfirm={removeEdit}>
     <Button danger size="small" className="bl-delete">删除此事项</Button>
    </Popconfirm>
   </div>
  </Modal>
 </>;
}
