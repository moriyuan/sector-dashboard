const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const model=require('./tracking-model.js');
function harness(reduced=true){
 const nodes=new Map(),animations=[];
 function node(id){
  if(nodes.has(id))return nodes.get(id);
  const n={innerHTML:'',textContent:'',disabled:false,scrollTop:0,attrs:{},events:{},classList:{add(){}},
   closest(){return node('host');},querySelector(){return node('notes');},scrollTo(x,y){this.scrollTop=y;},focus(){},
   setAttribute(k,v){this.attrs[k]=v;},addEventListener(k,v){this.events[k]=v;},getAnimations(){return [];},
   animate(){let resolve;const finished=new Promise(r=>resolve=r);animations.push(resolve);return {finished};}};
  nodes.set(id,n);return n;
 }
 const root=node('root');
 const stocks=Array.from({length:9},(_,i)=>({code:String(600000+i),name:'测试'+i,boards:1,limit_timestamp:i,theme:'样本'}));
 const context={KplTracking:model,root,$:node,D:{date:'2026-11-23',stocks},state:{stock:'600000'},
  esc:s=>String(s??''),pct:v=>v==null?'—':v+'%',money:v=>v==null?'—':String(v),color:()=>'',
  renderCore(){},renderLadder(){},renderTape(){},remember(){},document:{},
  matchMedia:()=>({matches:reduced}),getSelection:()=>({toString:()=>''}),setTimeout:()=>1,clearTimeout(){},fetch:()=>Promise.resolve({})};
 vm.createContext(context);vm.runInContext(fs.readFileSync('tracking-card.js','utf8'),context);
 const run=s=>vm.runInContext(s,context);run('renderDetail()');
 return {run,node,animations,context};
}
test('cycle forward/backward, keyboard, singleton and empty state',()=>{
 const h=harness();assert.equal(h.node('[data-track-count]').textContent,'1 / 8');
 h.run('trackNext(-1)');assert.equal(h.node('[data-track-count]').textContent,'8 / 8');
 h.run('trackNext(1)');assert.equal(h.node('[data-track-count]').textContent,'1 / 8');
 const stage=h.node('[data-track-stage]');stage.events.keydown({target:stage,key:'ArrowRight',preventDefault(){}});
 assert.equal(h.node('[data-track-count]').textContent,'2 / 8');
 h.run('D.stocks=D.stocks.slice(0,1);renderDetail()');assert.equal(h.node('[data-track-next]').disabled,true);
 h.run('D.stocks=[];renderDetail()');assert.equal(h.node('[data-track-count]').textContent,'0 / 0');assert.match(h.node('[data-track-sheet]').innerHTML,/暂无个股/);
});
test('recent-only direct select persists on refresh and source reordering; deletion advances',()=>{
 const h=harness();h.run("tracking.recent={rows:[{code:'002242',name:'近期测试',sources:[{kind:'wind',label:'风向标'}]}]};trackSelect('002242');");
 assert.equal(h.context.state.stock,'002242');h.run('D.stocks.reverse();renderDetail()');assert.equal(h.context.state.stock,'002242');
 h.run('tracking.recent.rows=[];renderDetail()');assert.equal(h.context.state.stock,'600007');assert.match(h.node('[data-track-notice]').textContent,/已退出/);
});
test('external temporary viewing returns to queue without adding a member',()=>{
 const h=harness();h.run("trackSelect('600003');trackSelect('600008')");assert.match(h.node('[data-track-count]').textContent,/临时/);
 h.run('trackNext(1)');assert.equal(h.context.state.stock,'600003');assert.equal(h.node('[data-track-count]').textContent,'4 / 8');
});
test('animation rejects repeated clicks; refresh during animation displays latest target only',async()=>{
 const h=harness(false);h.run('trackNext(1);trackNext(1);trackNext(1)');assert.equal(h.run('tracking.current'),'SH:600001');
 h.run("D.stocks[1].name='刷新后的股票';renderDetail()");assert.equal(h.animations.length,1);
 h.animations.shift()();await new Promise(setImmediate);assert.equal(h.animations.length,1);
 h.animations.shift()();await new Promise(setImmediate);
 assert.equal(h.context.state.stock,'600001');assert.equal(h.run('tracking.busy'),false);assert.match(h.node('[data-track-sheet]').innerHTML,/刷新后的股票/);
});
test('late completion from cancelled selection cannot overwrite newer target',async()=>{
 const h=harness(false);h.run('trackNext(1)');h.run("trackSelect('600004')");
 const old=h.animations.shift();old();await new Promise(setImmediate);
 h.animations.shift()();await new Promise(setImmediate);h.animations.shift()();await new Promise(setImmediate);
 assert.equal(h.context.state.stock,'600004');assert.match(h.node('[data-track-sheet]').innerHTML,/测试4/);
});
test('scroll/text selection and embedded controls do not advance; body does',()=>{
 const h=harness(),click=h.node('host').events.click;
 click({target:{closest:s=>s.includes('.kt-track-notes')?{}:s==='[data-track-stage]'?{}:null}});
 assert.equal(h.context.state.stock,'600000');
 h.context.getSelection=()=>({toString:()=>'选中的文字'});click({target:{closest:s=>s==='[data-track-stage]'?{}:null}});assert.equal(h.context.state.stock,'600000');
 h.context.getSelection=()=>({toString:()=>''});click({target:{closest:s=>s==='[data-track-stage]'?{}:null}});assert.equal(h.context.state.stock,'600001');
});
