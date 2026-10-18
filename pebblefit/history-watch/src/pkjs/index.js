/* Credential-free PKJS bridge. Pairing supplies only a short-lived upload token. */
(function () {
  'use strict';
  // Set at build time by deploy/build-watch-apps.sh (PEBBLE_HEALTH_URL).
  var SERVER = 'https://pebble-health.example.lan';
  var ENDPOINT = SERVER + '/fitness/records';
  var PAIR = SERVER + '/pair?kind=history';
  var TOKEN_KEY = 'history_token';
  var QUEUE_KEY = 'history_queue';
  var MAX_QUEUE = 256;
  var uploading = false;

  function readQueue() { try { return JSON.parse(localStorage.getItem(QUEUE_KEY) || '[]'); } catch (e) { return []; } }
  function writeQueue(q) { localStorage.setItem(QUEUE_KEY, JSON.stringify(q)); }
  function token() { return localStorage.getItem(TOKEN_KEY) || ''; }
  function reply(code, seq, run) { var p = {ack: code}; if (seq !== undefined) p.ack_seq = seq; if (run !== undefined) p.ack_run = run; Pebble.sendAppMessage(p); }
  function u16(b, i) { return (b[i] & 255) | ((b[i + 1] & 255) << 8); }
  function i16(b, i) { var n = u16(b, i); return n & 0x8000 ? n - 65536 : n; }
  function u32(b, i) { return (b[i] & 255) | ((b[i + 1] & 255) << 8) | ((b[i + 2] & 255) << 16) | ((b[i + 3] & 255) << 24); }
  function decodeRecords(bytes, count, start) {
    var out = [], i;
    for (i = 0; i < count && i * 10 + 9 < bytes.length; i++) {
      out.push({ts: u32(bytes, i * 10), hr: u16(bytes, i * 10 + 4), steps: i16(bytes, i * 10 + 6), active: 0, vmc: u16(bytes, i * 10 + 8)});
    }
    if (out.length !== count) throw new Error('Truncated minute packet');
    return out;
  }
  function decodeSessions(bytes, count) {
    var out = [], i;
    for (i = 0; i < count && i * 9 + 8 < bytes.length; i++) {
      out.push({start: u32(bytes, i * 9), seconds: u32(bytes, i * 9 + 4), type: ({1:'walk',2:'run',3:'workout',4:'sleep',5:'restful_sleep'})[bytes[i * 9 + 8]] || 'workout'});
    }
    if (out.length !== count) throw new Error('Truncated session packet');
    return out;
  }
  function preflight() {
    var t = token(), req;
    if (!t) { reply(2); return; }
    req = new XMLHttpRequest(); req.open('GET', ENDPOINT, true); req.timeout = 10000;
    req.setRequestHeader('Authorization', 'Bearer ' + t);
    req.onload = function () { reply(req.status === 200 ? 10 : req.status === 401 ? 3 : 1000 + req.status); };
    req.onerror = function () { reply(4); }; req.ontimeout = function () { reply(5); }; req.send();
  }
  function upload() {
    var q, item, req;
    if (uploading) return;
    q = readQueue(); if (!q.length) { preflight(); return; }
    if (!token()) { reply(2, q[0].seq, q[0].run); return; }
    item = q[0]; uploading = true; req = new XMLHttpRequest(); req.open('POST', ENDPOINT, true); req.timeout = 20000;
    req.setRequestHeader('Content-Type', 'application/json'); req.setRequestHeader('Authorization', 'Bearer ' + token());
    req.onload = function () {
      uploading = false;
      if (req.status >= 200 && req.status < 300) {
        writeQueue(readQueue().filter(function (x) { return x.id !== item.id; }));
        reply(1, item.seq, item.run); if (readQueue().length) upload();
      } else reply(req.status === 401 ? 3 : 1000 + req.status, item.seq, item.run);
    };
    req.onerror = function () { uploading = false; reply(4, item.seq, item.run); };
    req.ontimeout = function () { uploading = false; reply(5, item.seq, item.run); };
    req.send(JSON.stringify(item.payload));
  }
  function enqueue(payload, seq, run) {
    var q = readQueue(), id = String(run) + ':' + String(seq), i;
    for (i = 0; i < q.length; i++) if (q[i].id === id) { upload(); return; }
    if (q.length >= MAX_QUEUE) { reply(7, seq, run); return; }
    q.push({id: id, seq: seq, run: run, payload: payload}); writeQueue(q); upload();
  }
  Pebble.addEventListener('showConfiguration', function () { Pebble.openURL(PAIR); });
  Pebble.addEventListener('webviewclosed', function (e) {
    if (!e.response || e.response === 'CANCELLED') return;
    try { var d = JSON.parse(decodeURIComponent(e.response)); if (d.token) { localStorage.setItem(TOKEN_KEY, d.token); upload(); } else reply(2); }
    catch (err) { reply(2); }
  });
  Pebble.addEventListener('ready', function () { upload(); });
  Pebble.addEventListener('appmessage', function (e) {
    var p = e.payload, seq = p.seq, run = p.run || 0, payload;
    if (p.command) { preflight(); return; }
    if (p.records) {
      payload = {source: 'watch-history', ts: p.start, run: run, records: decodeRecords(p.records || [], p.records_count || 0, p.start)};
      enqueue(payload, seq, run); return;
    }
    if (p.session_data) {
      payload = {source: 'watch-history', ts: p.session_start, run: run, records: [], sessions: decodeSessions(p.session_data, p.session_count || 0)};
      enqueue(payload, p.session_seq, run); return;
    }
    reply(6, seq, run);
  });
}());
