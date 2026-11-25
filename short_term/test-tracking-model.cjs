const {test}=require('node:test');
const assert=require('node:assert/strict');
const m=require('./tracking-model.js');
const stock=(i,boards=1)=>({code:String(600000+i),name:'样本'+i,boards,limit_timestamp:i,theme:i%2?'芯片':'算力'});
const view={date:'2026-11-23',stocks:Array.from({length:12},(_,i)=>stock(i,i<2?3:1)),tenDay:{end:'2026-11-23',rows:[{...stock(20),rank:1,return_10d:30}]}};
test('front is full market top8 plus ten-day; themed table reuses same function',()=>{
 assert.equal(m.front(view).length,9);assert.equal(m.front(view)[0].code,'600000');
 assert.equal(m.front(view,'芯片').filter(r=>r.theme!=='芯片').length,0);
});
test('union covers all rows, merges tags and does not reintroduce excluded recent members',()=>{
 const recent={rows:[{...stock(0),sources:[{kind:'wind',label:'风向标'}]},{...stock(30),sources:[{kind:'manual',label:'人工添加'}]}]};
 const rows=m.union(view,recent);assert.equal(rows.length,10);
 assert.deepEqual(rows[0].sources.map(s=>s.kind),['intraday','wind']);
 assert.equal(rows.at(-1).code,'600030');
 assert.deepEqual(m.union(view,{rows:[]})[0].sources.map(s=>s.kind),['intraday']);
});
test('stable queue keeps position after source reordering; only new entries append',()=>{
 const rows=m.union(view,{rows:[]});const before=rows.map(m.key);
 assert.deepEqual(m.reconcile(before,rows.slice().reverse()),before);
 assert.deepEqual(m.reconcile(before,[...rows.slice(1),stock(30)]),[...before.slice(1),'SH:600030']);
 assert.deepEqual(m.reconcile(before,[]),[]);
});
test('identity includes market so identical code cannot merge different securities',()=>{
 assert.notEqual(m.key({code:'000001',market:'SH'}),m.key({code:'000001',market:'SZ'}));
 assert.equal(m.key({code:'920690'}),'BJ:920690');
});
test('newer dated quote wins; supplementary fields retain their own old date',()=>{
 const row=stock(0);const v={...view,stocks:[{...row,quote:{price:10,change:2,high:11,turnover:5,detail_source_time:'2026-11-23T15:00:00+08:00'}}]};
 const r={rows:[{...row,quote:{code:row.code,price:12,change:3,quote_day:'2026-11-24',source_time:null}}]};
 const q=m.quote(v,r,row);assert.equal(q.price.value,12);assert.equal(q.price.day,'2026-11-24');assert.equal(q.price.stamp,null);
 assert.equal(q.high.day,'2026-11-23');assert.equal(q.high.stamp,'2026-11-23T15:00:00+08:00');assert.equal(q.amount,undefined);
});
test('quote result cannot be applied to a different stock; missing is not zero',()=>{
 const row=stock(0);const q=m.quote({date:view.date,stocks:[]},{rows:[{...row,quote:{code:'600099',price:100,change:0,quote_day:view.date}}]},row);
 assert.deepEqual(q,{});
});
test('same-day timestamped cached quote beats untimed pool snapshot, preserves valid zero',()=>{
 const row=stock(0);const v={date:view.date,stocks:[{...row,quote:{price:9,change:3}}]};
 const q=m.quote(v,{tracking_quotes:{'600000':{code:'600000',price:10,change:0,quote_day:view.date,source_time:view.date+'T15:00:00+08:00'}}},row);
 assert.equal(q.price.value,10);assert.equal(q.change.value,0);
});
test('all pools empty and missing ranking are valid',()=>{
 assert.deepEqual(m.front({stocks:[]}),[]);assert.deepEqual(m.union({stocks:[]},null),[]);
});
