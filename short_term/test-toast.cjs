const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
async function setup(){
 const nodes=new Map(),storage=new Map();
 function element(tag='div'){
  return {tag,dataset:{},children:[],textContent:'',innerHTML:'',hidden:false,scrollTop:0,checked:false,
   classList:{toggle(){},remove(){}},setAttribute(){},addEventListener(){},scrollIntoView(){},getBoundingClientRect(){return {top:0,bottom:20};},
   append(...items){for(const item of items){this.children.push(item);}this.firstChild=this.children[0];this.lastChild=this.children.at(-1);},
   replaceChildren(fragment){this.children=fragment.children;},
   querySelector(selector){if(!nodes.has(selector))nodes.set(selector,element());return nodes.get(selector);},
   querySelectorAll(selector){return selector==='.bc-item'?this.children.filter(n=>n.className==='bc-item'):[];}};
 }
 const root=element(),context={document:{querySelector:()=>root,createElement:element,createDocumentFragment:()=>element(),addEventListener(){}},
  localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},Intl,Date,Map,Set,
  window:{addEventListener(){},isSecureContext:true},navigator:{locks:{request:async(k,fn)=>fn()}},
  fetch:()=>new Promise(()=>{}),setInterval(){return 1;},clearInterval(){}};
 vm.createContext(context);
 const script=fs.readFileSync('broadcast-card.js','utf8').replace(/\}\)\(\);\s*$/, 'globalThis.testAPI={receive,changeDay,toast,day:()=>day,queue:()=>queue};})();');
 vm.runInContext(script,context);
 const api=context.testAPI,day=api.day();let revision=0;
 const row=(id,time)=>({id:String(id),published_at:day+'T'+time+'+08:00',received_at:day+'T'+time+'+08:00',text:'测试 '+time,version:1});
 const feed=async rows=>{api.receive({day,revision:++revision,rows,status:{state:'ready'}});await new Promise(setImmediate);};
 const title=()=>nodes.get('[data-toast-title]').textContent;
 const close=()=>nodes.get('[data-toast-close]').onclick();
 return {api,row,feed,title,close,nodes};
}
test('cached first payload does not pop historical messages',async()=>{
 const h=await setup();await h.feed([h.row(1,'09:30:00')]);assert.equal(h.api.toast.hidden,true);assert.equal(h.api.queue().length,0);
});
test('newer batch replaces old pending toast; closing shows older next',async()=>{
 const h=await setup();await h.feed([]);const old=h.row(1,'09:30:00'),newest=h.row(2,'09:31:00');
 await h.feed([old]);assert.match(h.title(),/09:30:00/);
 await h.feed([old,newest]);assert.match(h.title(),/09:31:00/);
 assert.match(h.nodes.get('[data-toast-count]').textContent,/2 条待查看 · 最新优先/);
 h.close();assert.match(h.title(),/09:30:00/);h.close();assert.equal(h.api.toast.hidden,true);
});
test('unordered multi-message batch is read newest to oldest',async()=>{
 const h=await setup();await h.feed([]);await h.feed([h.row(1,'09:30:00'),h.row(3,'09:32:00'),h.row(2,'09:31:00')]);
 for(const time of ['09:32:00','09:31:00','09:30:00']){assert.ok(h.title().includes(time));h.close();}
});
test('late older publication never displaces the newer pending message',async()=>{
 const h=await setup();await h.feed([]);const recent=h.row(3,'09:32:00');await h.feed([recent]);
 const late=h.row(1,'09:30:00');late.received_at=h.row(1,'09:35:00').received_at;
 await h.feed([recent,late]);assert.match(h.title(),/09:32:00/);h.close();assert.match(h.title(),/09:30:00.*补到/);
});
test('same-time IDs break ties numerically and replays do not duplicate alerts',async()=>{
 const h=await setup();await h.feed([]);const rows=[h.row(99,'09:32:00'),h.row(100,'09:32:00')];await h.feed(rows);await h.feed(rows);
 assert.equal(h.api.queue().length,2);assert.equal(h.api.queue()[0].id,'100');
});
test('new top message resets long-text scroll, unchanged top preserves it',async()=>{
 const h=await setup();await h.feed([]);const newest=h.row(3,'09:32:00');await h.feed([newest]);
 h.nodes.get('[data-toast-text]').scrollTop=80;await h.feed([newest,h.row(1,'09:30:00')]);assert.equal(h.nodes.get('[data-toast-text]').scrollTop,80);
 await h.feed([newest,h.row(1,'09:30:00'),h.row(4,'09:33:00')]);assert.equal(h.nodes.get('[data-toast-text]').scrollTop,0);
});
