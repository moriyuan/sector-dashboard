(()=>{
  const root=document.getElementById('kpl-today');
  const button=root.querySelector('[data-refresh]');
  const status=root.querySelector('[data-refresh-status]');
  const token='__REFRESH_TOKEN__';
  const cadence=document.createElement('span');cadence.dataset.refreshCadence='';
  cadence.textContent='自动刷新：每1分钟';button.before(cadence);
  let displayedDay=JSON.parse(document.getElementById('kpl-today-data').textContent).date;
  let revision=0, busy=false, failures=0, lastJob=null, pollTimer=null, polling=false, lastStorageAt=0;
  let noData=JSON.parse(document.getElementById('kpl-today-data').textContent).noData;
  const storage=document.createElement('p');
  storage.dataset.storage='';
  root.querySelector('.kt-notes').append(storage);
  async function json(url,options={}){
    const response=await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(4000),...options});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    return response.json();
  }
  function show(state){
    cadence.textContent=`自动刷新：每${state.automatic.interval_seconds/60}分钟${state.automatic.active?'':' · '+(state.automatic.pause_reason||'已暂停')}`;
    root.querySelector('.kt-snapshot').textContent=noData?'30日窗口内暂无有效行情 · 待获取':displayedDay!==state.server_day?'历史快照 · '+displayedDay:state.job.state==='running'?'后台逐项更新中':'今日真实数据';
    if(state.analysis)root.dispatchEvent(new CustomEvent('kpl-analysis-status',{detail:state.analysis}));
    if(state.analysis_error){const label=root.querySelector('[data-analysis-state]');if(label)label.textContent=state.analysis_error;}
    busy=state.job.state==='running';
    button.disabled=busy;
    button.textContent=busy?'正在后台更新':'手动实时更新';
    button.setAttribute('aria-busy',String(busy));
    const job=state.job;
    if(busy){
      status.textContent=`${job.phase} ${job.completed}/${job.total} · ${(job.elapsed_ms/1000).toFixed(1)} 秒`;
    }else if(job.state==='failed'){
      status.textContent='更新失败，保留当前画面';
    }else if(job.state==='degraded'){
      status.textContent=`部分模块未更新 · ${(job.elapsed_ms/1000).toFixed(1)} 秒`;
    }else if(job.metrics){
      const core=job.metrics.core_ms;
      status.textContent=`${core==null?'主要数据待核验':'主要数据 '+(core/1000).toFixed(2)+' 秒'} · 全轮 ${(job.metrics.total_ms/1000).toFixed(2)} 秒`;
    }else{
      status.textContent=state.automatic.active?'每 1 分钟自动更新':(state.automatic.pause_reason||'自动更新暂停')+' · 可手动更新';
    }
    const existing=root.querySelector('[data-refresh-errors]');
    if(existing)existing.remove();
    if(job.errors?.length){
      const note=document.createElement('p');note.dataset.refreshErrors='';
      note.textContent='本轮未更新模块：'+job.errors.join('；');
      root.querySelector('.kt-notes').append(note);
    }
  }
  async function loadData(state){
    if(state.revision===revision)return;
    const result=await json('./live');
    const d=result.data;
    // Keep the dated previous screen while a new trading day is still missing its base modules.
    if(!d?.market||!Number.isFinite(d.market.SZJS)||!Number.isFinite(d.emotion?.highest)
       ||!Number.isFinite(d.riseFall?.break_rate)||!Array.isArray(d.stocks))return;
    root.dispatchEvent(new CustomEvent('kpl-refresh-data',{detail:d}));
    revision=result.revision;
    displayedDay=d.date;
    noData=Boolean(d.noData);
    root.querySelector('.kt-snapshot').textContent=displayedDay!==state.server_day?'历史快照 · '+displayedDay:state.job.state==='running'?'后台逐项更新中':'今日真实数据';
  }
  async function loadStorage(){
    const s=await json('./storage');
    lastStorageAt=Date.now();
    const m=s.maintenance;if(!m){storage.textContent='存储状态暂未取得';return;}
    const mb=n=>((n||0)/1024/1024).toFixed(2)+' MB';
    const states={pending:'待执行',running:'后台清理中',complete:'完成',partial:'部分完成，待重试',failed:'失败，待重试'};
    const at=m.finished_at?new Date(m.finished_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'尚未执行';
    const reusable=Object.values(m.databases||{}).reduce((n,r)=>n+(r.reusable_bytes||0),0);
    storage.textContent=`行情快照与报价保留最近30个自然日（含今天，当前从 ${m.cutoff} 起）。每日16:10后台清理，错过后择时补做。当前 ${s.frames} 轮快照；两库及WAL占用 ${mb(m.database_wal_bytes)}。维护：${states[m.state]||'待执行'}；上次完成尝试 ${at}；本次累计删除快照 ${m.deleted_frames??'—'} 轮、报价 ${m.deleted_quotes??'—'} 条，实际回收 ${m.reclaimed_bytes==null?'—':mb(m.reclaimed_bytes)}，空闲页可复用 ${mb(reusable)}。${m.waiting_reason||''}。直播原文、当前核心及人工增删记录继续保留。`;
    storage.title=Object.entries(m.databases||{}).map(([name,r])=>`${name}：清理 ${r.logical}，磁盘回收 ${r.physical}${r.error?'；'+r.error:''}`).join('\n');
  }
  button.addEventListener('click',async()=>{
    if(busy)return;
    busy=true;button.disabled=true;button.textContent='正在发起更新';status.textContent='保留当前画面，正在发起后台任务';
    try{
      const selected=root.querySelector('.kt-stock.is-active')?.dataset.stock||null;
      const state=await json('./refresh',{method:'POST',headers:{'Content-Type':'application/json','X-Preview-Token':token},body:JSON.stringify({selected})});
      show(state);
      if(state.reused_recent_result)status.textContent='刚刚已更新，已返回最近结果';
      await loadData(state);
      clearTimeout(pollTimer);pollTimer=setTimeout(poll,100);
    }catch(error){busy=false;button.disabled=false;button.textContent='重试更新';status.textContent='连接失败，原数据仍可查看';}
  });
  async function poll(){
    if(polling)return;
    polling=true;
    try{
      const state=await json('./status');
      show(state);await loadData(state);failures=0;
      if((state.job.state!=='running'&&state.job.id!==lastJob)||Date.now()-lastStorageAt>30000){lastJob=state.job.id;await loadStorage();}
    }catch(error){
      failures++;
      if(failures>=3){busy=false;button.disabled=false;button.textContent='重试连接';status.textContent='本地服务暂时无法连接，保留当前画面';}
    }finally{polling=false;clearTimeout(pollTimer);pollTimer=setTimeout(poll,busy?300:2000);}
  }
  loadStorage().catch(()=>{});poll();
})();
