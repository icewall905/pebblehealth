/* PebbleFit Today: credential-free PKJS bridge. Pairing supplies a scoped read token
 * stored only in this phone's localStorage; the PBW never contains a credential. */
(function () {
  'use strict';
  // Set at build time by deploy/build-watch-apps.sh (PEBBLE_HEALTH_URL).
  var SERVER = 'https://pebble-health.example.lan';
  var ENDPOINT = SERVER + '/fitness/today';
  var PAIR = SERVER + '/pair?kind=today';
  var TOKEN_KEY = 'today_token';
  var EXPIRES_KEY = 'today_token_expires';
  var NULL_VALUE = -1;
  /* Status codes match the HRV/history apps: 1 ok, 2 not paired, 3 unauthorized,
   * 4 network error, 5 timeout, 6 bad response, 1000+HTTP status otherwise. */
  var STATUS_OK = 1, STATUS_UNPAIRED = 2, STATUS_UNAUTHORIZED = 3, STATUS_NETWORK = 4,
    STATUS_TIMEOUT = 5, STATUS_BAD_RESPONSE = 6;
  var HRV_STATUS = {balanced: 1, unbalanced_low: 2, unbalanced_high: 3, low: 4, insufficient: 5, baseline_building: 6};
  var INT32_MAX = 2147483647;
  var fetching = false;

  function token() { return localStorage.getItem(TOKEN_KEY) || ''; }

  /* expires may be epoch seconds, epoch milliseconds or an ISO string. Unknown formats never block. */
  function expired() {
    var raw = localStorage.getItem(EXPIRES_KEY), ms;
    if (!raw) return false;
    if (/^\d+(\.\d+)?$/.test(raw)) { ms = Number(raw); if (ms < 1e12) ms *= 1000; }
    else ms = Date.parse(raw);
    return isFinite(ms) && ms > 0 && ms <= Date.now();
  }

  function send(msg, retried) {
    Pebble.sendAppMessage(msg, function () {}, function () {
      if (!retried) setTimeout(function () { send(msg, true); }, 1000);
    });
  }
  function status(code) { send({status: code}); }

  /* Integer field, or NULL_VALUE for null/missing/non-numeric/negative values. */
  function intOrNull(v, scale) {
    var n;
    if (v === null || v === undefined || v === '' || typeof v === 'boolean') return NULL_VALUE;
    n = Number(v);
    if (!isFinite(n) || n < 0) return NULL_VALUE;
    n = Math.round(n * (scale || 1));
    return n > INT32_MAX ? INT32_MAX : n;
  }
  function shortText(v) {
    if (typeof v !== 'string') return '';
    return v.replace(/[^\x20-\x7e]/g, '').replace(/_/g, ' ').slice(0, 20);
  }
  function hrvStatus(v) {
    if (v === null || v === undefined) return NULL_VALUE;
    return HRV_STATUS.hasOwnProperty(v) ? HRV_STATUS[v] : 0;
  }

  function toMessage(d) {
    return {
      status: STATUS_OK,
      ts: intOrNull(d.ts),
      readiness: intOrNull(d.readiness),
      readiness_status: shortText(d.readiness_status),
      sleep_score: intOrNull(d.sleep_score),
      sleep_min: intOrNull(d.sleep_min),
      restful_min: intOrNull(d.restful_min),
      hrv_status: hrvStatus(d.hrv_status),
      hrv_ms10: intOrNull(d.hrv_ms, 10),
      weekly_hrv_ms10: intOrNull(d.weekly_hrv_ms, 10),
      baseline_nights: intOrNull(d.baseline_nights),
      rhr: intOrNull(d.rhr),
      breathing10: intOrNull(d.breathing, 10),
      load_acute: intOrNull(d.load_acute),
      load_chronic: intOrNull(d.load_chronic),
      steps: intOrNull(d.steps)
    };
  }

  function fetchToday() {
    var t = token(), req;
    if (fetching) return;
    /* No local expiry check: the server extends the token on every use, so a 401 is the only signal. */
    if (!t) { status(STATUS_UNPAIRED); return; }
    fetching = true;
    req = new XMLHttpRequest();
    req.open('GET', ENDPOINT, true);
    req.timeout = 15000;
    req.setRequestHeader('Authorization', 'Bearer ' + t);
    req.onload = function () {
      var data;
      fetching = false;
      if (req.status === 200) {
        try { data = JSON.parse(req.responseText); } catch (e) { status(STATUS_BAD_RESPONSE); return; }
        if (!data || typeof data !== 'object') { status(STATUS_BAD_RESPONSE); return; }
        send(toMessage(data));
      } else status(req.status === 401 ? STATUS_UNAUTHORIZED : 1000 + req.status);
    };
    req.onerror = function () { fetching = false; status(STATUS_NETWORK); };
    req.ontimeout = function () { fetching = false; status(STATUS_TIMEOUT); };
    req.send();
  }

  function parseClose(response) {
    try { return JSON.parse(decodeURIComponent(response)); }
    catch (e) { return JSON.parse(response); }
  }

  Pebble.addEventListener('showConfiguration', function () { Pebble.openURL(PAIR); });
  Pebble.addEventListener('webviewclosed', function (e) {
    var d;
    if (!e || !e.response || e.response === 'CANCELLED') return;
    try { d = parseClose(e.response); } catch (err) { status(STATUS_UNPAIRED); return; }
    if (!d || typeof d.token !== 'string' || !d.token) { status(STATUS_UNPAIRED); return; }
    localStorage.setItem(TOKEN_KEY, d.token);
    if (d.expires !== undefined && d.expires !== null) localStorage.setItem(EXPIRES_KEY, String(d.expires));
    else localStorage.removeItem(EXPIRES_KEY);
    fetchToday();
  });
  Pebble.addEventListener('ready', function () { fetchToday(); });
  Pebble.addEventListener('appmessage', function (e) {
    var p = (e && e.payload) || {};
    if (p.command) fetchToday();
  });
}());
