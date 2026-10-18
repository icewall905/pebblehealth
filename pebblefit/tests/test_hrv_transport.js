const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
function setup(token){
 const store=new Map(token?[['pebblefit_hrv_token',token]]:[]),events={},sent=[],requests=[];
 const sandbox={localStorage:{getItem:k=>store.get(k)||null,setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)},
  Pebble:{addEventListener:(k,f)=>events[k]=f,sendAppMessage:m=>sent.push(m),openURL:()=>{}},
  XMLHttpRequest:function(){this.open=(method,url)=>{this.method=method;this.url=url;};this.setRequestHeader=()=>{};this.send=body=>{this.body=body;requests.push(this);};}};
 vm.runInNewContext(fs.readFileSync('pebblefit/hrv-watch/src/pkjs/index.js','utf8'),sandbox);
 return {store,events,sent,requests};
}
let t=setup();t.events.ready();assert.equal(t.sent.at(-1).ack,2);assert.equal(t.requests.length,0);
t=setup('runtime-token');t.events.ready();assert.equal(t.requests[0].method,'GET');
t.requests[0].status=401;t.requests[0].onload();assert.equal(t.sent.at(-1).ack,3);
t=setup('runtime-token');t.events.appmessage({payload:{ts:123,ppi:[32,3,42,3],offsets:[0,0,0,0,232,3,0,0],motion:12}});
assert.deepEqual(JSON.parse(t.requests[0].body).ppi,[800,810]);
t.requests[0].status=404;t.requests[0].onload();assert.equal(t.sent.at(-1).ack,1404);assert.ok(t.store.get('pebblefit_hrv_queue'));
t.events.appmessage({payload:{command:1}});assert.equal(t.requests.length,2);
t.requests[1].status=201;t.requests[1].onload();assert.equal(t.sent.at(-1).ack,1);assert.equal(JSON.parse(t.store.get('pebblefit_hrv_queue')).length,0);
console.log('HRV transport passed: pairing preflight, byte decoding, exact HTTP errors, retained queue and retry');

t=setup('runtime-token');
const packet=ts=>({payload:{ts,ppi:[32,3],offsets:[232,3,0,0],motion:5,night:1}});
t.events.appmessage(packet(123));t.events.appmessage(packet(124));
assert.equal(JSON.parse(t.store.get('pebblefit_hrv_queue')).length,2);
assert.equal(t.requests.length,1);assert.equal(t.sent.at(-1).ack,11);assert.equal(t.sent.at(-1).ack_ts,124);
t.requests[0].status=201;t.requests[0].onload();
assert.equal(JSON.parse(t.requests[1].body).ts,124);
assert.equal(JSON.parse(t.requests[1].body).scheduled,true);
t.requests[1].status=201;t.requests[1].onload();
assert.equal(JSON.parse(t.store.get('pebblefit_hrv_queue')).length,0);
assert.equal(t.sent.at(-1).ack_ts,124);
console.log('Multiple nightly sessions retain independent acknowledgements and queue order');
t=setup('runtime-token');t.events.appmessage({payload:{command:2,ts:123,collector_version:120,schedule_ok:6,schedule_error:0,next_wakeup:456,night:1}});
assert.equal(JSON.parse(t.requests[0].body).collector,true);
assert.equal(JSON.parse(t.requests[0].body).schedule_ok,6);
assert.equal(t.requests[1].method,'GET');
assert.equal(t.store.has('pebblefit_hrv_queue'),false);
console.log('Collector status stays separate from measurement queues');
t=setup('runtime-token');t.events.appmessage({payload:{ts:123,ppi:[32,3],offsets:[232,3,0,0],motion:7,motion_samples:50,motion_source:1,collector_version:130}});
let raw=JSON.parse(t.requests[0].body);
assert.equal(raw.motion_source,'raw_accelerometer');assert.equal(raw.vibration_status,'unknown');assert.equal(raw.motion_samples,50);
t=setup('runtime-token');t.events.appmessage({payload:{command:2,ts:123,collector_version:130,schedule_ok:1,schedule_error:0,next_wakeup:456,night:1,phase:2,launch:1,schedule_ids:[255,255,255,255,12,0,0,0],schedule_times:[200,1,0,0,232,3,0,0]}});
raw=JSON.parse(t.requests[0].body);assert.deepEqual(raw.schedule_ids,[-1,12]);assert.deepEqual(raw.schedule_times,[456,1000]);assert.equal(raw.phase,2);assert.equal(raw.launch,'scheduled');
console.log('Raw motion source and signed scheduling diagnostics survive phone transport');
t=setup('runtime-token');
const ppi512=[],offset512=[];
for(let i=0;i<512;i++){ppi512.push(32,3);const n=i*350;offset512.push(n&255,(n>>>8)&255,(n>>>16)&255,(n>>>24)&255);}
t.events.appmessage({payload:{ts:123,ppi:ppi512,offsets:offset512,motion:50,motion_samples:4650,motion_source:1,collector_version:140,hrv_events:513,zero_ppi_events:1,hr_events:174,hr_sum:10440,hr_valid:174,firmware:'4.40.0',capacity_limited:1}});
raw=JSON.parse(t.requests[0].body);assert.equal(raw.ppi.length,512);assert.equal(raw.offsets_ms.length,512);assert.equal(raw.hrv_events,513);assert.equal(raw.zero_ppi_events,1);assert.equal(raw.hr_sum,10440);assert.equal(raw.firmware,'4.40.0');assert.equal(raw.capacity_limited,true);
console.log('512-interval capture and firmware/event diagnostics survive transport');
