const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const SOURCE = path.join(__dirname, '..', 'today-watch', 'src', 'pkjs', 'index.js');

function setup(seed) {
  const store = new Map(Object.entries(seed || {})), events = {}, sent = [], requests = [], timers = [];
  const sandbox = {
    localStorage: {getItem: k => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k)},
    Pebble: {addEventListener: (k, f) => { events[k] = f; }, sendAppMessage: (m, ok, fail) => sent.push({m, ok, fail}), openURL: u => { sandbox.url = u; }},
    XMLHttpRequest: function () {
      this.headers = {};
      this.open = (method, url) => { this.method = method; this.url = url; };
      this.setRequestHeader = (k, v) => { this.headers[k] = v; };
      this.send = body => { this.body = body; requests.push(this); };
    },
    setTimeout: f => timers.push(f), Date, JSON, Math, Number, String, isFinite
  };
  vm.runInNewContext(fs.readFileSync(SOURCE, 'utf8'), sandbox);
  return {store, events, sent, requests, timers, sandbox, last: () => JSON.parse(JSON.stringify(sent.at(-1).m))};
}
function respond(req, status, body) { req.status = status; req.responseText = body === undefined ? '' : JSON.stringify(body); req.onload(); }

/* No token: no network request, watch told to pair. Settings opens the LAN pairing page. */
let t = setup();
t.events.ready();
assert.equal(t.requests.length, 0);
assert.deepEqual(t.last(), {status: 2});
t.events.showConfiguration();
assert.equal(t.sandbox.url, 'https://pebble-health.example.lan/pair?kind=today');

/* Pairing close protocol stores token + expiry and immediately fetches with the bearer token. */
const future = Math.floor(Date.now() / 1000) + 30 * 86400;
t.events.webviewclosed({response: encodeURIComponent(JSON.stringify({token: 'runtime-today-token', expires: future}))});
assert.equal(t.store.get('today_token'), 'runtime-today-token');
assert.equal(t.store.get('today_token_expires'), String(future));
assert.equal(t.requests.length, 1);
assert.equal(t.requests[0].method, 'GET');
assert.equal(t.requests[0].url, 'https://pebble-health.example.lan/fitness/today');
assert.equal(t.requests[0].headers.Authorization, 'Bearer runtime-today-token');

/* Cancelled / malformed pairing leaves storage untouched. */
t = setup();
t.events.webviewclosed({response: 'CANCELLED'});
assert.equal(t.store.has('today_token'), false); assert.equal(t.sent.length, 0);
t.events.webviewclosed({response: 'not-json'});
assert.equal(t.store.has('today_token'), false); assert.deepEqual(t.last(), {status: 2});
t.events.webviewclosed({response: encodeURIComponent(JSON.stringify({expires: future}))});
assert.equal(t.store.has('today_token'), false); assert.deepEqual(t.last(), {status: 2});

/* 401 -> unpaired message (status 3); token is kept so a transient server fault cannot unpair. */
t = setup({today_token: 'runtime-today-token'});
t.events.ready();
respond(t.requests[0], 401, {error: 'unauthorized'});
assert.deepEqual(t.last(), {status: 3});

/* A locally stale expiry never blocks: the server extends tokens on use and answers 401 when truly expired. */
t = setup({today_token: 'old', today_token_expires: String(Math.floor(Date.now() / 1000) - 10)});
t.events.ready();
assert.equal(t.requests.length, 1);

/* Full payload: x10 scaling for HRV/breathing, rounding, enum mapping, string sanitising. */
t = setup({today_token: 'runtime-today-token'});
t.events.ready();
respond(t.requests[0], 200, {
  ts: 1760770000, readiness: 72, readiness_status: 'moderately_ready', sleep_score: 81, sleep_min: 452, restful_min: 110,
  hrv_status: 'balanced', hrv_ms: 45.24, weekly_hrv_ms: 48.06, baseline_nights: 14, rhr: 52.4, breathing: 14.25,
  load_acute: 312.6, load_chronic: 280.2, steps: 8412
});
assert.deepEqual(t.last(), {
  status: 1, ts: 1760770000, readiness: 72, readiness_status: 'moderately ready', sleep_score: 81, sleep_min: 452, restful_min: 110,
  hrv_status: 1, hrv_ms10: 452, weekly_hrv_ms10: 481, baseline_nights: 14, rhr: 52, breathing10: 143,
  load_acute: 313, load_chronic: 280, steps: 8412
});
Object.values(t.last()).forEach(v => { if (typeof v === 'number') assert.ok(Number.isInteger(v)); });

/* Nulls -> -1 sentinel; baseline_building enum; unknown enum -> 0. */
t = setup({today_token: 'runtime-today-token'});
t.events.appmessage({payload: {command: 1}});
respond(t.requests[0], 200, {
  ts: 1760770000, readiness: null, readiness_status: null, sleep_score: null, sleep_min: null, restful_min: null,
  hrv_status: 'baseline_building', hrv_ms: null, weekly_hrv_ms: null, baseline_nights: 3, rhr: null, breathing: null,
  load_acute: null, load_chronic: null, steps: null
});
let m = t.last();
assert.equal(m.status, 1); assert.equal(m.hrv_status, 6); assert.equal(m.baseline_nights, 3); assert.equal(m.readiness_status, '');
['readiness', 'sleep_score', 'sleep_min', 'restful_min', 'hrv_ms10', 'weekly_hrv_ms10', 'rhr', 'breathing10', 'load_acute', 'load_chronic', 'steps']
  .forEach(k => assert.equal(m[k], -1, k));
t.events.appmessage({payload: {command: 1}});
respond(t.requests[1], 200, {ts: null, hrv_status: null, baseline_nights: 0});
m = t.last();
assert.equal(m.ts, -1); assert.equal(m.hrv_status, -1); assert.equal(m.readiness, -1);
t.events.appmessage({payload: {command: 1}});
respond(t.requests[2], 200, {ts: 5, hrv_status: 'something_new'});
assert.equal(t.last().hrv_status, 0);

/* Network/timeout/other HTTP/bad JSON codes; in-flight requests are not duplicated. */
t = setup({today_token: 'runtime-today-token'});
t.events.ready(); t.events.appmessage({payload: {command: 1}});
assert.equal(t.requests.length, 1);
t.requests[0].onerror(); assert.deepEqual(t.last(), {status: 4});
t.events.appmessage({payload: {command: 1}}); t.requests[1].ontimeout(); assert.deepEqual(t.last(), {status: 5});
t.events.appmessage({payload: {command: 1}}); respond(t.requests[2], 503); assert.deepEqual(t.last(), {status: 1503});
t.events.appmessage({payload: {command: 1}}); t.requests[3].status = 200; t.requests[3].responseText = '<html>'; t.requests[3].onload();
assert.deepEqual(t.last(), {status: 6});
t.events.appmessage({payload: {}});
assert.equal(t.requests.length, 4);

/* A NACKed AppMessage is retried once, not forever. */
const before = t.sent.length;
t.sent.at(-1).fail(); assert.equal(t.timers.length, 1); t.timers[0]();
assert.equal(t.sent.length, before + 1); t.sent.at(-1).fail(); assert.equal(t.timers.length, 1);

/* No credentials are embedded in the bundled source. */
assert.doesNotMatch(fs.readFileSync(SOURCE, 'utf8'), /Bearer [A-Za-z0-9]{8,}|AUTH_TOKEN\s*=\s*['"][^'"]/);
console.log('Today transport passed: pairing storage, expiry, 401 unpaired, null sentinels, x10 scaling, error codes, single retry');
