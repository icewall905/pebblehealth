const vm = require('node:vm');
const fs = require('node:fs');
const assert = require('node:assert/strict');

function setup(token) {
  const store = new Map(token ? [['history_token', token]] : []), events = {}, sent = [], requests = [];
  const sandbox = {
    localStorage: {getItem: k => store.get(k) || null, setItem: (k, v) => store.set(k, v), removeItem: k => store.delete(k)},
    Pebble: {addEventListener: (k, f) => { events[k] = f; }, sendAppMessage: m => sent.push(m), openURL: u => { sandbox.url = u; }},
    XMLHttpRequest: function () { this.open = (method, url) => { this.method = method; this.url = url; }; this.setRequestHeader = () => {}; this.send = body => { this.body = body; requests.push(this); }; }
  };
  vm.runInNewContext(fs.readFileSync('pebblefit/history-watch/src/pkjs/index.js', 'utf8'), sandbox);
  return {store, events, sent, requests, sandbox};
}

let t = setup();
t.events.ready();
assert.equal(t.sent.at(-1).ack, 2);
t.events.showConfiguration();
assert.equal(t.sandbox.url, 'https://pebble-health.example.lan/pair?kind=history');

t = setup('runtime-history-token');
t.events.ready();
assert.equal(t.requests[0].method, 'GET');
t.requests[0].status = 401; t.requests[0].onload();
assert.equal(t.sent.at(-1).ack, 3);

t = setup('runtime-history-token');
const bytes = [0xe8, 0x03, 0, 0, 0x3c, 0, 0x2a, 0, 0x07, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
t.events.appmessage({payload: {run: 7, seq: 4, start: 1000, records: bytes, records_count: 2}});
assert.equal(t.requests.length, 1);
let payload = JSON.parse(t.requests[0].body);
assert.equal(payload.source, 'watch-history');
assert.equal(payload.run, 7);
assert.deepEqual(payload.records[0], {ts: 1000, hr: 60, steps: 42, active: 0, vmc: 7});
assert.equal(payload.records[1].ts, 0);
t.requests[0].status = 201; t.requests[0].onload();
assert.equal(t.sent.at(-1).ack, 1); assert.equal(t.sent.at(-1).ack_seq, 4); assert.equal(t.sent.at(-1).ack_run, 7);
assert.equal(JSON.parse(t.store.get('history_queue')).length, 0);

t = setup('runtime-history-token');
t.events.appmessage({payload: {run: 8, seq: 9, start: 2000, records: bytes, records_count: 2}});
t.requests[0].status = 503; t.requests[0].onload();
assert.equal(t.sent.at(-1).ack, 1503);
assert.equal(JSON.parse(t.store.get('history_queue')).length, 1);
t.events.appmessage({payload: {run: 8, seq: 9, start: 2000, records: bytes, records_count: 2}});
assert.equal(JSON.parse(t.store.get('history_queue')).length, 1);

/* Native packets omit invalid minutes before encoding; decoder keeps the valid row only. */
t = setup('runtime-history-token');
t.events.appmessage({payload: {run: 10, seq: 1, start: 3000, records: bytes.slice(0, 10), records_count: 1}});
payload = JSON.parse(t.requests[0].body);
assert.equal(payload.records.length, 1);
assert.equal(payload.records[0].ts, 1000);

/* Same sequence in a new run is a distinct durable queue item. */
t.events.appmessage({payload: {run: 11, seq: 1, start: 3000, records: bytes.slice(0, 10), records_count: 1}});
assert.equal(JSON.parse(t.store.get('history_queue')).length, 2);

t = setup('runtime-history-token');
t.store.set('history_queue', JSON.stringify(Array.from({length: 256}, (_, i) => ({id: String(i), seq: i, run: 1, payload: {}}))));
t.events.appmessage({payload: {run: 12, seq: 99, start: 4000, records: bytes.slice(0, 10), records_count: 1}});
assert.equal(t.sent.at(-1).ack, 7);
assert.equal(t.sent.at(-1).ack_seq, 99);
assert.equal(t.sent.at(-1).ack_run, 12);
console.log('History transport passed: pairing URL, unauthorized preflight, little-endian decode, ACK-gated queue and retry retention');

/* Sleep types 4 and 5 map to sleep and restful_sleep. */
t = setup('runtime-history-token');
const sb = [];
[[100, 4], [200, 5], [300, 3]].forEach(([st, ty]) => sb.push(st & 255, 0, 0, 0, 60, 0, 0, 0, ty));
t.events.appmessage({payload: {run: 20, session_seq: 0, session_start: 100, session_data: sb, session_count: 3}});
payload = JSON.parse(t.requests[0].body);
assert.deepEqual(payload.sessions.map(x => x.type), ['sleep', 'restful_sleep', 'workout']);
