import {useMemo,useState,type ReactNode} from 'react';
import {Button,ConfigProvider,DatePicker,Drawer,type ThemeConfig} from 'antd';
import zhCN from 'antd/locale/zh_CN';
import dayjs from 'dayjs';
import 'dayjs/locale/zh-cn';

dayjs.locale('zh-cn');
const theme:ThemeConfig={
 token:{colorPrimary:'#36583e',colorLink:'#36583e',colorLinkHover:'#527553',colorText:'#1f2d27',colorTextSecondary:'#58675d',
   colorBorder:'#cbd4c5',colorBgContainer:'#fffef9',colorBgElevated:'#fffef9',borderRadius:5,
   controlHeight:36,fontSize:13,fontFamily:'-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif'},
 components:{Segmented:{trackBg:'#e8eddf',itemSelectedBg:'#294534',itemSelectedColor:'#fff'},
   Pagination:{itemActiveBg:'#e8efdf'},DatePicker:{cellWidth:34},Drawer:{colorBgElevated:'#fffef9'}}
};
export function DeskProvider({children}:{children:ReactNode}){
 return <ConfigProvider locale={zhCN} theme={theme} button={{autoInsertSpace:false}}>{children}</ConfigProvider>;
}

type ArchivePickerProps={
 label:string;value:string;dates:string[];snapshots?:string[];emptyText?:string;onChange:(value:string)=>void;
 shortcut:{label:string;value:string};onShortcut?:()=>void;
};
export function ArchivePicker({label,value,dates,snapshots,emptyText='暂无归档',onChange,shortcut,onShortcut}:ArchivePickerProps){
 const [open,setOpen]=useState(false);
 const days=useMemo(()=>Array.from(new Set(dates)).sort(),[dates]);
 const available=new Set(days),captured=new Set(snapshots);
 return <DatePicker aria-label={label} className="archive-picker" open={open} onOpenChange={setOpen}
   popupAlign={{overflow:{adjustX:true,shiftX:true,adjustY:true}}}
   value={value?dayjs(value):null} format="YYYY-MM-DD" placeholder={days.length?shortcut.label:emptyText}
   inputReadOnly allowClear={false} showNow={false} disabled={!days.length}
   minDate={days.length?dayjs(days[0]):undefined} maxDate={days.length?dayjs(days[days.length-1]):undefined}
   disabledDate={(d,info)=>info.type==='date'&&!available.has(d.format('YYYY-MM-DD'))}
   onChange={d=>{if(d)onChange(d.format('YYYY-MM-DD'));}}
   cellRender={(d,info)=>info.type==='date'&&dayjs.isDayjs(d)&&captured.has(d.format('YYYY-MM-DD'))
     ?<span className="archive-day">{info.originNode}</span>:info.originNode}
   renderExtraFooter={()=> <div className="archive-footer">{snapshots&&<span>绿点表示有归档快照</span>}
     <Button size="small" type="link" onClick={()=>{if(onShortcut)onShortcut();else onChange(shortcut.value);setOpen(false);}}>{shortcut.label}</Button></div>}/>;
}

export function DetailDrawer({title,label,closeLabel,onClose,size,children}:{title:string;label:string;
 closeLabel:string;onClose:()=>void;size:number;children:ReactNode}){
 const [open,setOpen]=useState(true);
 return <Drawer open={open} title={title} aria-label={label} onClose={()=>setOpen(false)}
   afterOpenChange={visible=>{if(!visible)onClose();}}
   closable={{'aria-label':closeLabel,placement:'end'}} size={`min(${size}px, 100vw)`}
   rootClassName="desk-detail-drawer" classNames={{body:'desk-detail-body'}}>
   {children}</Drawer>;
}
