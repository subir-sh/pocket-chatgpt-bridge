const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../remote_bridge/static/app.js'),'utf8');

function mobile(fetch, storage=new Map([['remote-token','valid-token']])) {
  const nodes=new Map(), intervals=[];
  function node(id){
    if(!nodes.has(id))nodes.set(id,{
      value:'',hidden:false,checked:true,style:{},events:{},
      classList:{add(){},remove(){},toggle(){}},
      addEventListener(name,fn){this.events[name]=fn;},replaceChildren(){},append(){},
    });
    return nodes.get(id);
  }
  vm.runInNewContext(source,{
    document:{getElementById:node,createElement:()=>node('child'),hidden:false},
    sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    fetch,AbortController,URL,confirm:()=>true,
    setInterval:fn=>intervals.push(fn),setTimeout:()=>1,clearTimeout(){},
  });
  return {node,storage,intervals};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const ok=data=>({ok:true,status:200,json:async()=>data});
const status=()=>ok({connected:true,foreground:true,server_instance:'server-1'});

test('offline startup retains pairing and reconnects automatically',async()=>{
  let offline=true,sessionReads=0;
  const app=mobile(async url=>{
    if(offline)throw Error('offline');
    if(url.endsWith('sessions')){sessionReads++;return ok({items:[]});}
    return status();
  });
  await tick();
  assert.equal(app.storage.get('remote-token'),'valid-token');
  assert.equal(app.node('pairing').hidden,true);
  offline=false;app.intervals[0]();await tick();await tick();
  assert.ok(sessionReads>0);
  assert.match(app.node('connection').textContent,/●/);
});

test('only an authentication rejection removes pairing',async()=>{
  const app=mobile(async()=>({ok:false,status:401,json:async()=>({detail:'invalid'})}));
  await tick();await tick();
  assert.equal(app.storage.has('remote-token'),false);
  assert.equal(app.node('pairing').hidden,false);
});

test('manual retry after a lost response preserves the request ID',async()=>{
  const sends=[];
  const app=mobile(async(url,options)=>{
    if(url.endsWith('/send')){
      sends.push(JSON.parse(options.body));
      if(sends.length===1)throw Error('response lost after acceptance');
      return ok({input_sent:true});
    }
    if(url.endsWith('/sessions'))return ok({items:[]});
    return status();
  });
  await tick();await tick();
  app.node('message').value='밖에서 보내는 한글';
  await app.node('composer').events.submit({preventDefault(){}});
  assert.ok(app.storage.get('remote-pending'));
  await app.node('composer').events.submit({preventDefault(){}});
  assert.equal(sends.length,2);
  assert.equal(sends[0].request_id,sends[1].request_id);
  assert.equal(sends[0].server_instance,'server-1');
  assert.equal(app.storage.has('remote-pending'),false);
});

test('page reload restores the unconfirmed message and can query its outcome',async()=>{
  const storage=new Map([
    ['remote-token','valid-token'],
    ['remote-pending',JSON.stringify({text:'not lost',request_id:'pending_01',server_instance:'server-1'})],
  ]);
  let sends=0;
  const app=mobile(async url=>{
    if(url.includes('/sends/'))return ok({state:'completed',input_sent:true});
    if(url.endsWith('/send'))sends++;
    if(url.endsWith('/sessions'))return ok({items:[]});
    return status();
  },storage);
  await tick();await tick();
  assert.equal(app.node('message').value,'not lost');
  await app.node('checkPending').events.click();
  assert.equal(storage.has('remote-pending'),false);
  assert.equal(app.node('message').value,'');
  assert.equal(sends,0);
});
