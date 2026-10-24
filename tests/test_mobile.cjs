const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../remote_bridge/static/app.js'),'utf8');

function mobile(fetch, storage=new Map([['remote-token','valid-token']]), options={}) {
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
    localStorage:{getItem:k=>options.persistent?.get(k)||null,setItem:(k,v)=>options.persistent?.set(k,v),removeItem:k=>options.persistent?.delete(k)},
    location:options.location,history:{replaceState:(...args)=>options.history?.push(args)},
    fetch,AbortController,URL,URLSearchParams,confirm:()=>true,
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

test('QR scan authenticates, removes the key from the URL, and remembers the phone',async()=>{
  const token='scanned_connection_key_'.padEnd(48,'x'), persistent=new Map(), history=[], headers=[];
  const fetch=async(url,options)=>{
    headers.push(options.headers['X-Remote-Token']);
    return url.endsWith('sessions')?ok({items:[]}):status();
  };
  const app=mobile(fetch,new Map(),{persistent,history,
    location:{hash:'#connect='+token,pathname:'/',search:''}});
  await tick();await tick();
  assert.equal(app.node('pairing').hidden,true);
  assert.ok(headers.every(value=>value===token));
  assert.equal(history[0][2],'/');
  assert.equal(persistent.get('remote-token'),token);
  const reopened=mobile(fetch,new Map(),{persistent});
  await tick();await tick();
  assert.equal(reopened.node('pairing').hidden,true);
  assert.equal(reopened.storage.get('remote-token'),token);
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

test('slow health checks do not create overlapping reconnection requests',async()=>{
  let calls=0,resolve;
  const app=mobile(async url=>{
    if(url.endsWith('/sessions'))return ok({items:[]});
    calls++;
    return await new Promise(done=>{resolve=done;});
  });
  app.intervals[0]();app.intervals[0]();
  assert.equal(calls,1);
  resolve(status());await tick();await tick();
  assert.match(app.node('connection').textContent,/노트북 연결됨/);
  app.intervals[0]();
  assert.equal(calls,2);
  resolve(status());await tick();
});
