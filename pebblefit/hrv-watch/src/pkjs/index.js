/* Upload-only pairing and a durable multi-session queue on the phone. */
// Set at build time by deploy/build-watch-apps.sh (PEBBLE_HEALTH_URL).
var SERVER='https://pebble-health.example.lan';
var ENDPOINT=SERVER+'/fitness/hrv';
var AUTH_TOKEN=localStorage.getItem('pebblefit_hrv_token')||'';
var uploading=false;
function queue(){try{return JSON.parse(localStorage.getItem('pebblefit_hrv_queue')||'[]');}catch(e){return [];}}
function store(q){localStorage.setItem('pebblefit_hrv_queue',JSON.stringify(q));}
function reply(code,ts){var msg={ack:code};if(ts)msg.ack_ts=ts;Pebble.sendAppMessage(msg);}
function decode(bytes,width){var result=[];for(var i=0;i<bytes.length;i+=width){var n=0;for(var j=0;j<width;j++)n+=(bytes[i+j]&255)*Math.pow(256,j);result.push(n);}return result;}
function decodeSigned(bytes){return decode(bytes,4).map(function(v){return v>2147483647?v-4294967296:v;});}
function diagnostic(p){
 if(!AUTH_TOKEN)return;
 var req=new XMLHttpRequest();req.open('POST',ENDPOINT,true);req.timeout=10000;
 req.setRequestHeader('Content-Type','application/json');req.setRequestHeader('Authorization','Bearer '+AUTH_TOKEN);
 req.onload=function(){};req.onerror=function(){};req.ontimeout=function(){};
 req.send(JSON.stringify({collector:true,ts:p.ts,collector_version:p.collector_version,schedule_ok:p.schedule_ok,schedule_error:p.schedule_error,next_wakeup:p.next_wakeup,nights_enabled:!!p.night,firmware:p.firmware||'unknown',phase:p.phase||1,launch:p.launch?'scheduled':'manual',schedule_ids:p.schedule_ids?decodeSigned(p.schedule_ids):[],schedule_times:p.schedule_times?decode(p.schedule_times,4):[]}));
}
function preflight(){
 if(!AUTH_TOKEN){reply(2);return;}
 var req=new XMLHttpRequest();req.open('GET',ENDPOINT,true);req.timeout=10000;
 req.setRequestHeader('Authorization','Bearer '+AUTH_TOKEN);
 req.onload=function(){reply(req.status===200?10:req.status===401?3:1000+req.status);};
 req.onerror=function(){reply(4);};req.ontimeout=function(){reply(5);};req.send();
}
function upload(){
 if(uploading)return;
 var q=queue();if(!q.length){preflight();return;}
 if(!AUTH_TOKEN){reply(2,q[0].ts);return;}
 var sample=q[0],req=new XMLHttpRequest();uploading=true;
 req.open('POST',ENDPOINT,true);req.timeout=20000;
 req.setRequestHeader('Content-Type','application/json');req.setRequestHeader('Authorization','Bearer '+AUTH_TOKEN);
 req.onload=function(){
  uploading=false;
  if(req.status>=200&&req.status<300){
   store(queue().filter(function(s){return s.ts!==sample.ts;}));reply(1,sample.ts);
   if(queue().length)upload();
  }else reply(req.status===401?3:1000+req.status,sample.ts);
 };
 req.onerror=function(){uploading=false;reply(4,sample.ts);};
 req.ontimeout=function(){uploading=false;reply(5,sample.ts);};req.send(JSON.stringify(sample));
}
Pebble.addEventListener('showConfiguration',function(){Pebble.openURL(SERVER+'/pair?kind=hrv');});
Pebble.addEventListener('webviewclosed',function(e){
 if(!e.response||e.response==='CANCELLED')return;
 try{var data=JSON.parse(decodeURIComponent(e.response));if(data.token){AUTH_TOKEN=data.token;localStorage.setItem('pebblefit_hrv_token',data.token);upload();}}catch(err){reply(2);}
});
Pebble.addEventListener('ready',function(){
 var old=localStorage.getItem('pebblefit_hrv_pending');
 if(old){try{var s=JSON.parse(old),q=queue();if(!q.some(function(x){return x.ts===s.ts;})){q.push(s);store(q);}localStorage.removeItem('pebblefit_hrv_pending');}catch(e){}}
 upload();
});
Pebble.addEventListener('appmessage',function(e){
 var p=e.payload;if(p.command){if(p.command===2)diagnostic(p);upload();return;}
 if(!p.ppi||!p.offsets){reply(6);return;}
 var sample={ts:p.ts,ppi:decode(p.ppi,2),offsets_ms:decode(p.offsets,4),motion_total:p.motion,scheduled:!!p.night,motion_samples:p.motion_samples||0,collector_version:p.collector_version||0,accel_callbacks:p.accel_callbacks||0,vibration_samples:p.vibration_samples||0,accel_rate_result:p.accel_rate_result||0,motion_source:p.motion_source===1?'raw_accelerometer':'processed_accelerometer',vibration_status:p.motion_source===1?'unknown':'flagged_samples_excluded',firmware:p.firmware||'unknown',hrv_events:p.hrv_events||0,zero_ppi_events:p.zero_ppi_events||0,hr_events:p.hr_events||0,hr_sum:p.hr_sum||0,hr_valid:p.hr_valid||0,capacity_limited:!!p.capacity_limited,stream_semantics:'latest_ppi_updates'};
 var q=queue(),existing=q.some(function(x){return x.ts===sample.ts;});
 if(!existing&&q.length>=128){reply(7,sample.ts);return;}
 try{if(!existing){q.push(sample);store(q);}reply(11,sample.ts);upload();}catch(e){reply(7,sample.ts);}
});
