/* Pebble Health — self-hosted health dashboard for a Pebble watch.
   No dependencies. Every server-provided string reaches the page through
   textContent / setAttribute only; nothing is assigned to innerHTML. */
'use strict';

/* ================================================================== *
 * DOM helpers
 * ================================================================== */
const SVGNS = 'http://www.w3.org/2000/svg';

function build(el, attrs, kids) {
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === 'class') el.setAttribute('class', v);
      else if (k === 'text') el.textContent = String(v);
      else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : String(v));
    }
  }
  const add = c => {
    if (c == null || c === false) return;
    if (Array.isArray(c)) { c.forEach(add); return; }
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  };
  add(kids);
  return el;
}
const h = (tag, attrs, ...kids) => build(document.createElement(tag), attrs, kids);
const s = (tag, attrs, ...kids) => build(document.createElementNS(SVGNS, tag), attrs, kids);

const ICON = {
  today: 'M12 3v2M12 19v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M3 12h2M19 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z',
  sleep: 'M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z',
  recovery: 'M3 12h4l2-5 3 10 2-5h7',
  training: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  more: 'M5 12h.01M12 12h.01M19 12h.01',
  refresh: 'M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7',
  sun: 'M12 3v2M12 19v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M3 12h2M19 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z',
  moon: 'M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z',
  chev: 'M9 6l6 6-6 6',
  back: 'M15 6l-6 6 6 6',
};
const icon = (name, cls) => s('svg', { viewBox: '0 0 24 24', class: cls, 'aria-hidden': 'true' }, s('path', { d: ICON[name] }));

/* ================================================================== *
 * Formatting
 * ================================================================== */
const LOCALE = undefined; // browser default
const isNum = v => typeof v === 'number' && Number.isFinite(v);
function fmt(v, digits = 0) {
  if (!isNum(v)) return '—';
  return v.toLocaleString(LOCALE, { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}
function fmtDur(sec, short) {
  if (!isNum(sec)) return '—';
  const m = Math.round(sec / 60), hh = Math.floor(m / 60), mm = m % 60;
  if (short) return hh ? `${hh}h ${String(mm).padStart(2, '0')}m` : `${mm}m`;
  return hh ? `${hh} h ${mm} min` : `${mm} min`;
}
const fmtHours = sec => isNum(sec) ? `${fmt(sec / 3600, 1)} h` : '—';
const fmtClock = ts => isNum(ts) ? new Date(ts * 1000).toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' }) : '—';
const fmtDateTime = ts => isNum(ts) ? new Date(ts * 1000).toLocaleString(LOCALE, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—';
function parseISO(iso) { const [y, m, d] = String(iso).split('-').map(Number); return new Date(y, (m || 1) - 1, d || 1); }
function isoOf(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}
const fmtDay = (iso, opts = { weekday: 'short', day: 'numeric', month: 'short' }) => parseISO(iso).toLocaleDateString(LOCALE, opts);
const fmtDayShort = iso => parseISO(iso).toLocaleDateString(LOCALE, { day: 'numeric', month: 'short' });
function addDays(iso, n) { const d = parseISO(iso); d.setDate(d.getDate() + n); return isoOf(d); }
function ago(sec) {
  if (!isNum(sec)) return 'never';
  if (sec < 90) return 'just now';
  if (sec < 3600) return `${Math.round(sec / 60)} min ago`;
  if (sec < 86400 * 2) return `${Math.round(sec / 3600)} h ago`;
  return `${Math.round(sec / 86400)} days ago`;
}
const avg = arr => { const v = arr.filter(isNum); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
const sum = arr => { const v = arr.filter(isNum); return v.length ? v.reduce((a, b) => a + b, 0) : null; };
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const cap = str => { const t = String(str ?? ''); return t.charAt(0).toUpperCase() + t.slice(1); };

/* ================================================================== *
 * State
 * ================================================================== */
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
};
const RANGES = [{ days: 7, label: '7 days' }, { days: 28, label: '4 weeks' }, { days: 365, label: '1 year' }];
const S = {
  data: null, daysLoaded: 0, loading: false, error: null,
  fetchedAt: 0, offlineSince: null, range: Number(store.get('ph-range')) || 7,
};
if (!RANGES.some(r => r.days === S.range)) S.range = 7;
const D = () => S.data || {};
const todayISO = () => (D().recovery && D().recovery.today) || isoOf(new Date());
const nowSec = () => D().now || Math.floor(Date.now() / 1000);

/* ================================================================== *
 * Network
 * ================================================================== */
async function api(path, method, body) {
  const res = await fetch(path, {
    method, headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: body ? JSON.stringify(body) : undefined, cache: 'no-store',
  });
  let json = null;
  try { json = await res.json(); } catch (e) { /* not JSON */ }
  if (!res.ok) throw new Error((json && (json.error || json.detail)) || `The server answered ${res.status}.`);
  return json;
}

async function load(force) {
  if (S.loading) return;
  const want = clamp(Math.max(42, S.range), 7, 365);
  if (!force && S.data && S.daysLoaded >= want && Date.now() - S.fetchedAt < 60000) return;
  S.loading = true; setBusy(true);
  try {
    const res = await fetch(`api/summary?days=${want}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!res.ok) {
      let msg = `The server answered ${res.status}.`;
      try { const j = await res.json(); if (j && j.error) msg = j.error; } catch (e) { /* ignore */ }
      throw new Error(msg);
    }
    const data = await res.json();
    S.offlineSince = res.headers.get('X-Offline-Fallback') ? Number(res.headers.get('X-Cached-At')) || null : null;
    if (S.offlineSince === null && res.headers.get('X-Offline-Fallback')) S.offlineSince = 0;
    S.data = data; S.daysLoaded = want; S.error = null; S.fetchedAt = Date.now();
  } catch (e) {
    S.error = e.message || 'Could not reach the server.';
    if (S.data) S.offlineSince = S.offlineSince ?? S.fetchedAt;
  } finally {
    S.loading = false; setBusy(false); render();
  }
}

function setBusy(on) {
  const b = document.getElementById('refresh-btn');
  b.classList.toggle('spin', on);
  b.setAttribute('aria-busy', on ? 'true' : 'false');
}

/* ================================================================== *
 * Shared UI pieces
 * ================================================================== */
function swatch() { return h('span', { class: 'swatch', 'aria-hidden': 'true' }); }

/* A card whose heading links to its detail view (stretched link). */
function card(m, title, opts, ...body) {
  const o = opts || {};
  const head = h('div', { class: 'card-head' }, swatch(),
    o.link ? h('h2', null, h('a', { class: 'card-link', href: `#d/${o.link}` }, title)) : h('h2', null, title),
    o.chip || null,
    o.link ? icon('chev', 'chev') : null);
  return h('article', { class: `card${o.link ? ' tappable' : ''}${o.cls ? ' ' + o.cls : ''}`, 'data-m': m }, head, body);
}
function chip(text, kind, title) { return h('span', { class: `chip ${kind || ''}`, title }, text); }
const expChip = () => chip('Experimental', 'exp', 'Not a validated medical measure');

function big(value, unit, none) {
  return h('div', { class: 'big' }, h('span', { class: `v${none ? ' none' : ''}` }, value), unit ? h('span', { class: 'u' }, unit) : null);
}
function stats(items) {
  return h('dl', { class: 'stats' }, items.filter(Boolean).map(([k, v, u]) => {
    const none = v === '—' || v == null;
    return h('div', { class: 'stat' }, h('dt', null, k), h('dd', { class: none ? 'none' : '' }, none ? 'No data' : v, u && !none ? h('small', null, ' ' + u) : null));
  }));
}
function pending(title, text, progress) {
  return h('div', { class: 'pending above' }, h('strong', null, title), text ? h('span', null, text) : null,
    progress ? [h('div', { class: 'progress', role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': progress.of, 'aria-valuenow': progress.n, 'aria-label': progress.label || title },
      h('span', { style: { width: `${clamp(100 * progress.n / Math.max(1, progress.of), 0, 100)}%` } })),
    h('p', { class: 'progress-label' }, `${progress.n} of ${progress.of} ${progress.unit || ''}`.trim())] : null);
}
function info(summary, ...paras) {
  return h('details', { class: 'info' }, h('summary', null, summary || 'How this works'), paras.filter(Boolean).map(p => h('p', null, p)));
}
function missingList(items) {
  if (!items || !items.length) return null;
  return h('ul', { class: 'missing above', 'aria-label': 'Missing inputs' }, items.map(t => h('li', null, humanMissing(t))));
}
/* "hrv: no usable overnight measurement" → "HRV: no usable overnight measurement" */
function humanMissing(t) {
  const str = String(t ?? '');
  const m = str.match(/^([a-z_]+):\s*(.*)$/);
  if (!m) return str;
  return `${COMPONENT_NAMES[m[1]] || cap(m[1].replace(/_/g, ' '))}: ${m[2]}`;
}
const COMPONENT_NAMES = { hrv: 'HRV', resting_hr: 'Resting heart rate', sleep: 'Sleep', training_load: 'Training load', duration: 'Duration', regularity: 'Regularity' };

function rangeSwitch() {
  return h('div', { class: 'seg', role: 'group', 'aria-label': 'Time range' }, RANGES.map(r =>
    h('button', { type: 'button', 'aria-pressed': String(S.range === r.days), onclick: () => setRange(r.days) }, r.label)));
}
function setRange(days) {
  S.range = days; store.set('ph-range', String(days));
  const want = Math.max(42, days);
  if (S.daysLoaded < want) load(true); else render();
}

/* ================================================================== *
 * Charts (inline SVG)
 * ================================================================== */
function dateSpan(n, end) {
  const last = end || todayISO();
  const out = [];
  for (let i = n - 1; i >= 0; i--) out.push(addDays(last, -i));
  return out;
}
function niceMax(v) {
  if (!isNum(v) || v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v))), n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}

/* Group daily points into weeks for the 1-year view so bars stay legible. */
function bucket(dates, getter, mode) {
  if (dates.length <= 60) return dates.map(d => ({ key: d, label: d, ...getter(d) }));
  const out = [];
  for (let i = 0; i < dates.length; i += 7) {
    const chunk = dates.slice(i, i + 7), rows = chunk.map(getter), vals = rows.map(r => r.v).filter(isNum);
    const v = !vals.length ? null : mode === 'sum' ? vals.reduce((a, b) => a + b, 0) : vals.reduce((a, b) => a + b, 0) / vals.length;
    const partial = mode === 'sum' && vals.length > 0 && rows.some(r => !isNum(r.v));
    out.push({ key: chunk[0], label: chunk[0], end: chunk[chunk.length - 1], v, lo: avgOrNull(rows.map(r => r.lo)), hi: avgOrNull(rows.map(r => r.hi)), cls: partial ? 'partial' : undefined, n: vals.length, week: true });
  }
  return out;
}
const avgOrNull = a => avg(a);

function xLabels(items, W, x0, bw, H) {
  const g = [];
  if (!items.length) return g;
  const idx = items.length <= 7 ? items.map((_, i) => i) : [0, Math.floor((items.length - 1) / 2), items.length - 1];
  idx.forEach(i => {
    const it = items[i];
    const daily = items.length <= 7 && !it.week;
    const label = daily ? fmtDay(it.label, { weekday: 'short' }) : fmtDayShort(it.label);
    const anchor = items.length <= 7 ? 'middle' : i === 0 ? 'start' : i === items.length - 1 ? 'end' : 'middle';
    const x = anchor === 'start' ? x0 + i * bw : anchor === 'end' ? x0 + (i + 1) * bw : x0 + (i + .5) * bw;
    g.push(s('text', { x, y: H - 4, 'text-anchor': anchor }, label));
  });
  return g;
}
function tipLabel(it) {
  return it.week ? `Week of ${fmtDayShort(it.key)}` : fmtDay(it.key);
}

/* Bar chart. items: [{key,label,v,cls,lo?}] */
function barChart({ items, W = 340, H = 150, fmtV = v => fmt(v), unit = '', ref, label, max, minBar = 0 }) {
  const top = 10, bottom = 22, left = 30, right = 6, ph = H - top - bottom, pw = W - left - right;
  const vals = items.map(i => i.v).filter(isNum);
  const ymax = niceMax(Math.max(max || 0, ...(vals.length ? vals : [1]), ref ? ref.v : 0));
  const bw = pw / Math.max(1, items.length), gap = items.length > 40 ? 0.5 : Math.min(6, bw * .25);
  const y = v => top + ph - (v / ymax) * ph;
  const kids = [];
  for (let i = 0; i <= 2; i++) {
    const v = ymax * i / 2, yy = y(v);
    kids.push(s('line', { class: `grid${i === 0 ? ' zero' : ''}`, x1: left, x2: W - right, y1: yy, y2: yy }));
    kids.push(s('text', { x: left - 6, y: yy + 3.5, 'text-anchor': 'end' }, fmtAxis(v)));
  }
  items.forEach((it, i) => {
    const x = left + i * bw + gap / 2, w = Math.max(1, bw - gap);
    if (isNum(it.v)) {
      const hgt = Math.max(minBar, ph * it.v / ymax);
      const r = s('rect', { class: `bar${it.cls ? ' ' + it.cls : ''}`, x, y: top + ph - hgt, width: w, height: Math.max(hgt, it.v > 0 ? 1.5 : 0), rx: Math.min(3, w / 3) },
        s('title', null, `${tipLabel(it)}: ${fmtV(it.v)}${unit ? ' ' + unit : ''}${it.note ? ' (' + it.note + ')' : ''}`));
      kids.push(r);
      if (isNum(it.inner) && it.inner > 0) {
        const ih = Math.max(1, ph * it.inner / ymax);
        kids.push(s('rect', { class: 'bar', x, y: top + ph - ih, width: w, height: ih, rx: Math.min(3, w / 3) }, s('title', null, `${tipLabel(it)}: ${fmtV(it.v)} ${unit}, restful ${fmtV(it.inner)}`)));
      }
    } else {
      kids.push(s('rect', { class: 'gapmark', x, y: top + ph - 2, width: w, height: 2 }, s('title', null, `${tipLabel(it)}: ${it.cls === 'rest' ? 'rest day' : 'no data'}`)));
    }
  });
  if (ref && isNum(ref.v)) {
    const yy = y(ref.v);
    kids.push(s('line', { class: 'ref', x1: left, x2: W - right, y1: yy, y2: yy }));
    kids.push(s('text', { class: 'ref-label', x: W - right, y: yy - 4, 'text-anchor': 'end' }, ref.label));
  }
  kids.push(...xLabels(items, W, left, bw, H));
  return s('svg', { class: 'chart', viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': label || 'Bar chart' }, kids);
}
function fmtAxis(v) {
  if (v >= 10000) return `${fmt(v / 1000, 0)}k`;
  if (v >= 1000) return `${fmt(v / 1000, 1)}k`;
  return fmt(v, v < 10 && v % 1 ? 1 : 0);
}

/* Line chart with gaps, optional usual-range band and optional second series. */
function lineChart({ items, W = 340, H = 150, fmtV = v => fmt(v), unit = '', band, label, alt, dots = true, range, zeroBase }) {
  const top = 10, bottom = 22, left = 30, right = 6, ph = H - top - bottom, pw = W - left - right;
  const all = [];
  items.forEach(i => { all.push(i.v, i.lo, i.hi); if (alt) all.push(i.alt); });
  if (band) all.push(band.lo, band.hi);
  let vals = all.filter(isNum);
  if (!vals.length) vals = [0, 1];
  let lo = range ? range[0] : Math.min(...vals), hi = range ? range[1] : Math.max(...vals);
  if (zeroBase) lo = Math.min(0, lo);
  if (hi - lo < 1e-6) { lo -= 1; hi += 1; }
  const pad = (hi - lo) * .12; if (!range) { lo = zeroBase ? lo : lo - pad; hi += pad; }
  const step = niceMax((hi - lo) / 2) / 2 || 1;
  lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
  const bw = pw / Math.max(1, items.length);
  const X = i => left + (i + .5) * bw, Y = v => top + ph - ((v - lo) / (hi - lo)) * ph;
  const kids = [];
  const ticks = [lo, (lo + hi) / 2, hi];
  ticks.forEach((v, i) => {
    kids.push(s('line', { class: `grid${i === 0 ? ' zero' : ''}`, x1: left, x2: W - right, y1: Y(v), y2: Y(v) }));
    kids.push(s('text', { x: left - 6, y: Y(v) + 3.5, 'text-anchor': 'end' }, fmtAxis(v)));
  });
  if (band && isNum(band.lo) && isNum(band.hi)) {
    kids.push(s('rect', { class: 'band', x: left, width: pw, y: Y(band.hi), height: Math.max(1, Y(band.lo) - Y(band.hi)) }, s('title', null, `${band.label || 'Usual range'}: ${fmtV(band.lo)}–${fmtV(band.hi)} ${unit}`)));
  }
  const path = key => {
    let d = '', open = false;
    items.forEach((it, i) => {
      if (isNum(it[key])) { d += `${open ? 'L' : 'M'}${X(i).toFixed(1)} ${Y(it[key]).toFixed(1)}`; open = true; } else open = false;
    });
    return d;
  };
  items.forEach((it, i) => {
    if (isNum(it.lo) && isNum(it.hi)) kids.push(s('line', { x1: X(i), x2: X(i), y1: Y(it.lo), y2: Y(it.hi), stroke: 'var(--mc)', 'stroke-opacity': .35, 'stroke-width': Math.max(2, Math.min(8, bw * .6)), 'stroke-linecap': 'round' }));
  });
  if (alt) { const da = path('alt'); if (da) kids.push(s('path', { class: 'line alt', d: da })); }
  const d = path('v');
  if (d) kids.push(s('path', { class: 'line', d }));
  const showDots = dots && items.length <= 60;
  items.forEach((it, i) => {
    if (isNum(it.v)) {
      const isolated = !isNum((items[i - 1] || {}).v) && !isNum((items[i + 1] || {}).v);
      if (showDots || isolated) kids.push(s('circle', { class: `dot${it.hollow ? ' hollow' : ''}`, cx: X(i), cy: Y(it.v), r: items.length > 30 ? 2.6 : 3.6 }, s('title', null, `${tipLabel(it)}: ${fmtV(it.v)} ${unit}${it.note ? ' (' + it.note + ')' : ''}`)));
    } else if (items.length <= 60) {
      kids.push(s('rect', { class: 'gapmark', x: X(i) - Math.min(4, bw * .35), y: top + ph - 2, width: Math.min(8, bw * .7), height: 2 }, s('title', null, `${tipLabel(it)}: no data`)));
    }
  });
  kids.push(...xLabels(items, W, left, bw, H));
  return s('svg', { class: 'chart', viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': label || 'Line chart' }, kids);
}

/* Score ring 0–100. */
function ring(score, { size = 120, stroke = 11, sub, label, draw = true } = {}) {
  const r = (size - stroke) / 2 - 1, c = 2 * Math.PI * r, cx = size / 2;
  const kids = [s('circle', { class: 'ring-track', cx, cy: cx, r, 'stroke-width': stroke })];
  if (isNum(score)) {
    const len = c * clamp(score, 0, 100) / 100;
    kids.push(s('circle', {
      class: `ring-val${draw ? ' draw' : ''}`, cx, cy: cx, r, 'stroke-width': stroke,
      'stroke-dasharray': `${len} ${c}`, transform: `rotate(-90 ${cx} ${cx})`, style: `--len:${len}`,
    }));
    kids.push(s('text', { class: 'ring-text', x: cx, y: cx + size * .11, 'text-anchor': 'middle', 'font-size': size * .32 }, String(Math.round(score))));
  } else {
    kids.push(s('circle', { class: 'ring-empty', cx, cy: cx, r, 'stroke-width': 2 }));
    kids.push(s('text', { class: 'ring-text', x: cx, y: cx + size * .09, 'text-anchor': 'middle', 'font-size': size * .26, opacity: .55 }, '—'));
  }
  if (sub) kids.push(s('text', { class: 'ring-sub', x: cx, y: cx + size * .27, 'text-anchor': 'middle' }, sub));
  return s('svg', { viewBox: `0 0 ${size} ${size}`, role: 'img', 'aria-label': label || (isNum(score) ? `Score ${Math.round(score)} of 100` : 'No score') }, kids);
}

/* HRV status gauge: half circle, usual range band, weekly-average needle. */
function hrvGauge(st, W = 240) {
  const H = W * .62, cx = W / 2, cy = H - 14, R = W * .4, sw = W * .07;
  const lo = st.baseline_low_ms, hi = st.baseline_high_ms, wk = st.weekly_rmssd_ms;
  const ready = isNum(lo) && isNum(hi);
  const vals = [lo, hi, wk, st.last_night_rmssd_ms].filter(isNum);
  const dmin = ready ? Math.max(0, Math.min(...vals) - (hi - lo) * 1.1) : 0;
  const dmax = ready ? Math.max(...vals) + (hi - lo) * 1.1 : 1;
  const ang = v => Math.PI * (1 - clamp((v - dmin) / (dmax - dmin), 0, 1));
  const pt = (a, rr) => [cx + rr * Math.cos(a), cy - rr * Math.sin(a)];
  const arc = (a0, a1, rr) => { const [x0, y0] = pt(a0, rr), [x1, y1] = pt(a1, rr); return `M${x0.toFixed(1)} ${y0.toFixed(1)}A${rr} ${rr} 0 0 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`; };
  const kids = [s('path', { d: arc(Math.PI, 0, R), fill: 'none', stroke: 'var(--card-2)', 'stroke-width': sw, 'stroke-linecap': 'round' })];
  if (ready) {
    kids.push(s('path', { d: arc(ang(lo), ang(hi), R), fill: 'none', stroke: 'var(--mc)', 'stroke-opacity': .45, 'stroke-width': sw }, s('title', null, `Usual range ${fmt(lo)}–${fmt(hi)} ms`)));
    [[lo, 'start'], [hi, 'end']].forEach(([v]) => {
      const [x, y] = pt(ang(v), R + sw * .9);
      kids.push(s('text', { x, y: y + 3, 'text-anchor': 'middle' }, fmt(v)));
    });
    if (isNum(wk)) {
      const a = ang(wk), [x0, y0] = pt(a, R - sw * 1.2), [x1, y1] = pt(a, R + sw * .55);
      kids.push(s('line', { x1: x0, y1: y0, x2: x1, y2: y1, stroke: 'var(--ink)', 'stroke-width': 3.5, 'stroke-linecap': 'round' }));
      kids.push(s('text', { class: 'lbl-strong', x: cx, y: cy - 6, 'text-anchor': 'middle', 'font-size': W * .14, style: 'font-family:var(--font-num);font-size:' + (W * .14) + 'px' }, fmt(wk)));
      kids.push(s('text', { x: cx, y: cy + 10, 'text-anchor': 'middle' }, 'ms, 7-night average'));
    }
  } else {
    kids.push(s('path', { d: arc(Math.PI, 0, R), fill: 'none', stroke: 'var(--ink-3)', 'stroke-width': 2, 'stroke-dasharray': '2 6', 'stroke-linecap': 'round' }));
    kids.push(s('text', { x: cx, y: cy - 4, 'text-anchor': 'middle' }, 'Usual range not set yet'));
  }
  const lab = ready ? `HRV gauge: 7-night average ${fmt(wk)} ms, usual range ${fmt(lo)} to ${fmt(hi)} ms` : 'HRV gauge: usual range still being learned';
  return s('svg', { class: 'chart', viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': lab, style: 'max-width:300px;margin-inline:auto' }, kids);
}

/* Sleep timing: one row per night, bar from onset to wake on an 18:00→14:00 axis. */
function timingChart(nights, W = 340) {
  const rows = nights.filter(n => isNum(n.onset) && isNum(n.wake));
  const rowH = rows.length > 14 ? 9 : 16, top = 6, bottom = 22, left = 50, right = 8, pw = W - left - right;
  const H = top + bottom + rows.length * rowH;
  const START = 18, SPAN = 20; // hours
  const hourOf = ts => { const d = new Date(ts * 1000); let hr = d.getHours() + d.getMinutes() / 60; if (hr < START - 6) hr += 24; return hr; };
  const X = hr => left + clamp((hr - START) / SPAN, 0, 1) * pw;
  const kids = [];
  [18, 22, 26, 30, 34, 38].forEach(hr => {
    kids.push(s('line', { class: 'grid', x1: X(hr), x2: X(hr), y1: top, y2: H - bottom }));
    kids.push(s('text', { x: X(hr), y: H - 6, 'text-anchor': 'middle' }, `${String(hr % 24).padStart(2, '0')}:00`));
  });
  rows.forEach((n, i) => {
    const y = top + i * rowH;
    let a = hourOf(n.onset), b = a + (n.wake - n.onset) / 3600;
    kids.push(s('rect', { class: 'bar', x: X(a), y: y + 2, width: Math.max(2, X(b) - X(a)), height: rowH - 4, rx: (rowH - 4) / 2 },
      s('title', null, `${fmtDay(n.night)}: ${fmtClock(n.onset)} to ${fmtClock(n.wake)}`)));
    if (rows.length <= 14) kids.push(s('text', { x: left - 6, y: y + rowH / 2 + 3.5, 'text-anchor': 'end' }, fmtDay(n.night, { weekday: 'short', day: 'numeric' })));
  });
  return s('svg', { class: 'chart timing', viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'Sleep timing per night' }, kids);
}

/* Intraday chart on a 0–24 h axis. points: [{t (hours), v}], bars: true for hourly bars. */
function dayChart({ points, bars, W = 340, H = 150, unit = '', label, fmtV = v => fmt(v), hollow }) {
  const top = 10, bottom = 22, left = 30, right = 6, ph = H - top - bottom, pw = W - left - right;
  const vals = points.map(p => p.v).filter(isNum);
  let lo = bars ? 0 : Math.min(...vals), hi = Math.max(...vals, 1);
  if (!bars) { const pad = Math.max(4, (hi - lo) * .1); lo = Math.max(0, Math.floor((lo - pad) / 10) * 10); hi = Math.ceil((hi + pad) / 10) * 10; }
  else hi = niceMax(hi);
  const X = t => left + (t / 24) * pw, Y = v => top + ph - ((v - lo) / (hi - lo || 1)) * ph;
  const kids = [];
  [lo, (lo + hi) / 2, hi].forEach((v, i) => {
    kids.push(s('line', { class: `grid${i === 0 ? ' zero' : ''}`, x1: left, x2: W - right, y1: Y(v), y2: Y(v) }));
    kids.push(s('text', { x: left - 6, y: Y(v) + 3.5, 'text-anchor': 'end' }, fmtAxis(v)));
  });
  [0, 6, 12, 18, 24].forEach(t => kids.push(s('text', { x: X(t), y: H - 4, 'text-anchor': t === 0 ? 'start' : t === 24 ? 'end' : 'middle' }, `${String(t % 24).padStart(2, '0')}:00`)));
  if (bars) {
    const bw = pw / 24;
    points.forEach(p => { if (isNum(p.v) && p.v > 0) kids.push(s('rect', { class: 'bar', x: X(p.t) + 1, width: bw - 2, y: Y(p.v), height: Y(0) - Y(p.v), rx: 2 }, s('title', null, `${String(p.t).padStart(2, '0')}:00: ${fmtV(p.v)} ${unit}`))); });
  } else {
    let d = '', prev = null;
    points.filter(p => !p.snapshot).forEach(p => {
      d += `${prev !== null && p.t - prev < 6 / 60 ? 'L' : 'M'}${X(p.t).toFixed(1)} ${Y(p.v).toFixed(1)}`; prev = p.t;
    });
    if (d) kids.push(s('path', { class: 'line', d, 'stroke-width': 1.6 }));
    points.forEach(p => {
      const lone = !p.snapshot && !points.some(q => q !== p && !q.snapshot && Math.abs(q.t - p.t) < 6 / 60);
      if (p.snapshot || lone) kids.push(s('circle', { class: `dot${p.snapshot ? ' hollow' : ''}`, cx: X(p.t), cy: Y(p.v), r: 3.2 }, s('title', null, `${hourLabel(p.t)}: ${fmtV(p.v)} ${unit}${p.snapshot ? ' (hourly snapshot)' : ''}`)));
    });
  }
  return s('svg', { class: 'chart', viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': label || 'Today' }, kids);
}
const hourLabel = t => `${String(Math.floor(t)).padStart(2, '0')}:${String(Math.round((t % 1) * 60)).padStart(2, '0')}`;

function legend(items) {
  return h('div', { class: 'legend' }, items.map(([cls, text]) => h('span', null, h('i', { class: cls }), text)));
}

/* ================================================================== *
 * Data accessors
 * ================================================================== */
const rec = () => D().recovery || {};
const byNight = (arr, key) => { const m = new Map(); (arr || []).forEach(r => { if (r && r[key]) m.set(r[key], r); }); return m; };
function nightItems(n, key) {
  const m = byNight(rec().nights, 'night');
  return dateSpan(n).map(d => ({ key: d, label: d, v: (m.get(d) || {})[key] ?? null }));
}
function lastNight() { const n = rec().nights || []; return n.length ? n[n.length - 1] : null; }
const isLastNight = n => n && n.night === todayISO();

function obsByLocalDay() {
  const out = new Map();
  (D().observations || []).forEach(o => {
    if (!o || !isNum(o.ts)) return;
    const d = isoOf(new Date(o.ts * 1000));
    if (!out.has(d)) out.set(d, []);
    out.get(d).push(o);
  });
  return out;
}
function todayObs() {
  const today = isoOf(new Date());
  return (obsByLocalDay().get(today) || []).slice().sort((a, b) => a.ts - b.ts);
}
const hourOfTs = ts => { const d = new Date(ts * 1000); return d.getHours() + d.getMinutes() / 60; };
const historyMap = () => byNight(D().history || (D().training || {}).history, 'date');
const wearer = () => { const w = D().wearer; return typeof w === 'string' && w.trim() ? w.trim().slice(0, 60) : ''; };

const HRV_LABEL = {
  balanced: ['Balanced', 'ok'], unbalanced_low: ['Unbalanced, low', 'warn'], unbalanced_high: ['Unbalanced, high', 'warn'],
  low: ['Low', 'bad'], insufficient: ['Not enough nights', ''], baseline_building: ['Building baseline', ''],
};
const hrvLabel = st => HRV_LABEL[st] || [cap(String(st || 'No data').replace(/_/g, ' ')), ''];

function readinessWord(score) {
  if (!isNum(score)) return 'Not enough data';
  if (score >= 75) return 'High';
  if (score >= 55) return 'Moderate';
  if (score >= 35) return 'Low';
  return 'Very low';
}
function sleepWord(score) {
  if (!isNum(score)) return 'No score';
  if (score >= 90) return 'Excellent'; if (score >= 80) return 'Good'; if (score >= 60) return 'Fair'; return 'Poor';
}
const TRAIN_STATUS = { ready: 'Tracking', warming: 'Building baseline' };

/* ================================================================== *
 * Cards
 * ================================================================== */
/* Weight: daily points plus the 7-day trend on the same date spine. */
const signedKg = v => isNum(v) ? `${v > 0 ? '+' : v < 0 ? '−' : '±'}${fmt(Math.abs(v), 1)}` : '—';
function weightItems(span) {
  const m = new Map(((D().weight || {}).entries || []).map(e => [e.date, e]));
  const items = bucket(span, d => ({ v: (m.get(d) || {}).kg ?? null, alt: (m.get(d) || {}).trend_kg ?? null }), 'avg');
  // Weekly buckets (1-year view) keep the trend too.
  return span.length > 60 ? items.map((i, k) => ({ ...i, alt: avg(span.slice(k * 7, k * 7 + 7).map(d => (m.get(d) || {}).trend_kg)) })) : items;
}

const CARDS = {
  weight() {
    const w = D().weight;
    if (!w || !w.latest) return null;
    const stale = w.days_since_latest > 0;
    return card('weight', 'Weight', { link: 'weight' },
      big(fmt(w.latest.kg, 1), stale ? `kg · ${fmtDayShort(w.latest.date)}` : 'kg today'),
      stats([['7-day trend', fmt(w.latest.trend_kg, 1), 'kg'], ['Last week', signedKg(w.change_7d_kg), 'kg'], ['Last 4 weeks', signedKg(w.change_28d_kg), 'kg']]),
      lineChart({ items: weightItems(dateSpan(28)), H: 110, alt: true, fmtV: v => fmt(v, 1), unit: 'kg', label: 'Weight, last 4 weeks' }));
  },
  sleep() {
    const sl = rec().sleep || {}, nights = rec().sleep_nights || [];
    const has = isNum(sl.sleep_s);
    const last = nights[nights.length - 1];
    const word = isNum(sl.score) ? sleepWord(sl.score) : null;
    return card('sleep', 'Sleep', { link: 'sleep', chip: sl.status === 'duration_only' ? chip('Duration only', '', sl.reason) : null },
      has ? [
        h('div', { class: 'big' }, h('span', { class: 'v' }, fmtDur(sl.sleep_s, true)), isNum(sl.score) ? h('span', { class: 'u' }, `score ${sl.score}, ${word.toLowerCase()}`) : null),
        stats([['Restful', fmtDur(sl.restful_s, true)], ['Need', fmtDur(sl.need_s, true)],
          last && isNum(last.onset) ? ['Asleep', `${fmtClock(last.onset)}`] : null]),
        barChart({ items: dateSpan(7).map(d => { const n = byNight(nights, 'night').get(d) || {}; return { key: d, label: d, v: isNum(n.sleep_s) ? n.sleep_s / 3600 : null, inner: isNum(n.restful_s) ? n.restful_s / 3600 : null, cls: 'soft' }; }), H: 110, fmtV: v => fmt(v, 1), unit: 'h', ref: isNum(sl.need_s) ? { v: sl.need_s / 3600, label: 'need' } : null, label: 'Sleep per night, last 7 nights' }),
      ] : pending('No sleep recorded for last night', sl.reason || 'Wear the watch to bed; the night appears after the morning sync.'));
  },

  hrv() {
    const st = rec().hrv_status || {};
    const [word, kind] = hrvLabel(st.status);
    const building = st.status === 'baseline_building';
    return card('hrv', 'HRV status', { link: 'hrv', chip: st.provisional ? chip('Provisional', '', st.reason) : expChip() },
      h('p', { class: 'status-word', style: { fontSize: '20px' } }, word),
      building
        ? pending('Learning your usual range', 'HRV is measured by short recordings at night. A usual range needs enough earlier nights.',
          { n: st.baseline_nights || 0, of: st.required_baseline_nights || 14, unit: 'nights', label: 'Baseline nights' })
        : [hrvGauge(st), stats([['Last night', fmt(st.last_night_rmssd_ms), 'ms'], ['Nights this week', isNum(st.nights_this_week) ? `${st.nights_this_week} of 7` : '—']])],
      !building && st.reason ? h('p', { class: 'lede' }, st.reason) : null);
  },

  rhr() {
    const n = lastNight(), items = nightItems(7, 'resting_hr');
    const has = items.some(i => isNum(i.v));
    const wk = avg(items.map(i => i.v));
    return card('rhr', 'Resting heart rate', { link: 'rhr' },
      has ? [
        big(isLastNight(n) ? fmt(n.resting_hr) : '—', isLastNight(n) ? 'bpm last night' : 'no reading last night', !isLastNight(n)),
        stats([['7-night average', fmt(wk), 'bpm']]),
        lineChart({ items, H: 100, unit: 'bpm', label: 'Resting heart rate, last 7 nights' }),
      ] : pending('No overnight readings yet', 'Resting heart rate comes from the scheduled night recordings of the HRV watch app.'));
  },

  breath() {
    const items = nightItems(7, 'breaths_per_min');
    const has = items.some(i => isNum(i.v)), n = lastNight();
    return card('breath', 'Breathing rate', { link: 'breath', chip: expChip() },
      has ? [
        big(isLastNight(n) && isNum(n.breaths_per_min) ? fmt(n.breaths_per_min, 1) : '—', 'breaths/min overnight', !(isLastNight(n) && isNum(n.breaths_per_min))),
        lineChart({ items, H: 100, unit: 'br/min', fmtV: v => fmt(v, 1), label: 'Breathing rate, last 7 nights' }),
      ] : pending('No reliable overnight reading yet', 'Breathing rate is shown only for nights where the signal was clear enough.'));
  },

  stress() {
    const st = D().stress || {};
    const score = st.experimental_score;
    const building = st.status === 'baseline_building' || !isNum(score);
    return card('stress', 'Stress', { link: 'stress', chip: expChip() },
      building
        ? pending(st.status === 'baseline_building' ? 'Building baseline' : 'No score yet', 'Needs two weeks of comparable daytime spot checks before a score means anything.',
          { n: st.baseline_days || 0, of: st.required_days || 14, unit: 'days', label: 'Baseline days' })
        : [big(fmt(score), 'of 100'), h('p', { class: 'lede' }, stressWord(score))]);
  },

  steps() {
    const lt = D().latest || {}, st = lt.status || {};
    const fresh = isNum(lt.ts) && isoOf(new Date(lt.ts * 1000)) === isoOf(new Date());
    const hourly = hourlySteps(todayObs());
    return card('steps', 'Steps and activity', { link: 'steps' },
      fresh && isNum(st.steps) ? [
        big(fmt(st.steps), 'steps'),
        stats([['Distance', isNum(st.distance_meters) ? fmt(st.distance_meters / 1000, 2) : '—', 'km'], ['Active', isNum(st.active_seconds) ? fmtDur(st.active_seconds, true) : '—'],
          ['Calories', isNum(st.active_kcal) ? fmt(st.active_kcal) : '—', 'active kcal']]),
        h('p', { class: 'lede', style: { fontSize: '13px' } }, `Watch total as of ${fmtClock(lt.ts)}`),
        hourly.some(p => p.v > 0) ? [dayChart({ points: hourly, bars: true, H: 100, unit: 'steps', label: 'Steps per hour today' }), h('p', { class: 'lede', style: { fontSize: '13px' } }, 'Hourly bars count synced minute records only.')] : null,
      ] : pending('No activity reported today yet', isNum(lt.ts) ? `The last report from the watch was ${fmtDateTime(lt.ts)}.` : 'The watch has not reported yet.'));
  },

  heart() {
    const pts = todayObs().filter(o => isNum(o.hr)).map(o => ({ t: hourOfTs(o.ts), v: o.hr, snapshot: !!o.snapshot }));
    const lt = D().latest || {}, st = lt.status || {};
    const vals = pts.map(p => p.v);
    return card('heart', 'Heart rate today', { link: 'heart' },
      pts.length ? [
        h('div', { class: 'big' }, h('span', { class: 'v' }, fmt(isNum(st.heart_rate_bpm) ? st.heart_rate_bpm : vals[vals.length - 1])), h('span', { class: 'u' }, `bpm at ${fmtClock(lt.ts || null)}`)),
        stats([['Lowest', fmt(Math.min(...vals)), 'bpm'], ['Highest', fmt(Math.max(...vals)), 'bpm']]),
        dayChart({ points: pts, H: 110, unit: 'bpm', label: 'Heart rate today' }),
      ] : pending('No heart rate recorded today yet', 'Minute heart rate arrives with the history export; hourly snapshots fill in between.'));
  },

  train() {
    const t = D().training || {}, lt = t.latest || {};
    const hist = historyMap();
    const items = dateSpan(7).map(d => loadItem(d, hist.get(d)));
    const warming = lt.status !== 'ready';
    return card('train', 'Training load', { link: 'train', chip: warming ? chip(TRAIN_STATUS.warming) : null },
      stats([['7-day load', fmt(lt.load7)], ['Acute', fmt(lt.acute, 1)], ['Chronic', fmt(lt.chronic, 1)], ['Form', isNum(lt.form) ? signed(lt.form) : '—']]),
      items.some(i => isNum(i.v)) ? [barChart({ items, H: 100, unit: 'load', label: 'Daily training load, last 7 days' }),
        legend([['', 'Recorded'], ['partial', 'Partly recorded'], ['gap', 'Unknown']])] : null,
      warming ? pending('Fitness and fatigue need history', `Needs 42 consecutive confirmed days to estimate chronic load; ${lt.baseline_days || 0} so far.`, { n: lt.baseline_days || 0, of: 42, unit: 'days' }) : null);
  },

  workouts(limit = 3, full) {
    const ws = (D().workouts || (D().training || {}).workouts || []).slice().sort((a, b) => (b.start || 0) - (a.start || 0));
    const shown = full ? ws : ws.slice(0, limit);
    return card('workouts', 'Workouts', { link: full ? null : 'workouts', cls: full ? 'wide' : '' },
      shown.length ? h('ul', { class: 'list above' }, shown.map(workoutRow)) : h('p', { class: 'lede' }, 'No workouts yet. Record one with the watch’s Workout app, or log one here by effort.'),
      h('div', { class: 'btn-row above' }, h('button', { type: 'button', class: 'btn small', onclick: () => openWorkoutForm() }, 'Log a workout'),
        !full && ws.length > limit ? h('a', { class: 'btn small', href: '#d/workouts' }, `All ${ws.length} workouts`) : null));
  },

  calendar() {
    const hist = historyMap(), days = dateSpan(28);
    const known = days.filter(d => ['complete', 'rest'].includes((hist.get(d) || {}).status)).length;
    return card('calendar', 'Recording completeness', { link: 'calendar' },
      h('p', { class: 'lede', style: { marginTop: 0 } }, `${known} of 28 days confirmed. Training load counts only days you confirm.`),
      calendarGrid(days, hist, true));
  },

  device() {
    const d = D(), lt = d.latest || {}, st = lt.status || {}, col = d.collector || {};
    const age = d.sync_age_seconds;
    return card('device', 'Watch and sync', { link: 'device' },
      stats([['Last upload', isNum(age) ? ago(age) : '—'], ['Battery', isNum(st.battery_percent) ? `${st.battery_percent}%` : '—', st.battery_charging ? 'charging' : ''],
        ['HRV recorder', isNum(col.received) ? ago(nowSec() - col.received) : '—']]),
      isNum(age) && age > 3 * 3600 ? h('p', { class: 'lede', style: { color: 'var(--warn)' } }, 'No upload for over three hours. Check that the phone app is running.') : null);
  },
};

function signed(v, digits = 1) { return isNum(v) ? (v > 0 ? '+' : v < 0 ? '−' : '') + fmt(Math.abs(v), digits) : '—'; }
function stressWord(score) {
  if (!isNum(score)) return '';
  if (score < 26) return 'Rest level against your own baseline.';
  if (score < 51) return 'Low arousal against your own baseline.';
  if (score < 76) return 'Medium arousal against your own baseline.';
  return 'High arousal against your own baseline.';
}
function hourlySteps(obs) {
  const out = Array.from({ length: 24 }, (_, t) => ({ t, v: 0 }));
  obs.forEach(o => { if (isNum(o.steps) && !o.snapshot) out[new Date(o.ts * 1000).getHours()].v += o.steps; });
  return out;
}
function loadItem(d, row) {
  const r = row || {};
  if (r.status === 'rest') return { key: d, label: d, v: 0, cls: 'rest', note: 'rest day' };
  if (isNum(r.load)) return { key: d, label: d, v: r.load, cls: r.status === 'partial' ? 'partial' : undefined, note: r.status === 'partial' ? 'recorded sessions only' : undefined };
  return { key: d, label: d, v: null };
}

function workoutRow(w) {
  const mins = isNum(w.seconds) ? Math.round(w.seconds / 60) : null;
  const sub = [fmtDateTime(w.start), mins != null ? `${mins} min` : null,
    isNum(w.hr_load) ? `HR load ${fmt(w.hr_load)}` : isNum(w.hr_coverage) && w.hr_coverage < .8 ? `HR coverage ${fmt(w.hr_coverage * 100)}%` : null,
    w.overlap ? 'overlaps another recording' : null].filter(Boolean).join(', ');
  return h('li', { class: w.ignored ? 'ignored' : '' },
    h('span', { class: 't' }, cap(w.kind || 'Workout')),
    h('span', { class: 's' }, sub),
    h('span', { class: 'acts' },
      h('button', { type: 'button', class: 'btn small', onclick: () => openRpe(w), 'aria-label': isNum(w.rpe) ? `Effort ${w.rpe} of 10, change` : 'Add effort' }, isNum(w.rpe) ? `Effort ${fmt(w.rpe)}` : 'Add effort'),
      w.id ? h('button', { type: 'button', class: 'btn small', onclick: () => toggleIgnore(w) }, w.ignored ? 'Restore' : 'Exclude') : null));
}

/* Month-style grid (Monday first). compact: last 4 weeks with weekday header. */
function calendarGrid(days, hist, compact) {
  const today = todayISO();
  const conf = D().confirmations || {};
  const grid = h('div', { class: 'cal above', role: 'grid', 'aria-label': 'Day recording status' });
  const dows = []; for (let i = 0; i < 7; i++) dows.push(parseISO('2024-01-01').getTime() + i * 86400000); // a Monday
  dows.forEach(t => grid.append(h('div', { class: 'dow', 'aria-hidden': 'true' }, new Date(t).toLocaleDateString(LOCALE, { weekday: 'narrow' }))));
  let lastMonth = null;
  const first = parseISO(days[0]); const lead = (first.getDay() + 6) % 7;
  for (let i = 0; i < lead; i++) grid.append(h('div', { class: 'pad' }));
  days.forEach((d, idx) => {
    const dt = parseISO(d);
    if (!compact && dt.getMonth() !== lastMonth && idx > 0 && dt.getDate() === 1) {
      const rem = (7 - (grid.querySelectorAll('button, .pad').length % 7)) % 7;
      for (let i = 0; i < rem; i++) grid.append(h('div', { class: 'pad' }));
      grid.append(h('p', { class: 'cal-month' }, dt.toLocaleDateString(LOCALE, { month: 'long', year: 'numeric' })));
      const l2 = (dt.getDay() + 6) % 7; for (let i = 0; i < l2; i++) grid.append(h('div', { class: 'pad' }));
    }
    lastMonth = dt.getMonth();
    const row = hist.get(d) || {}, status = row.status || 'unknown';
    const label = { complete: 'complete', rest: 'rest day', partial: 'partly recorded', unknown: 'unknown' }[status] || status;
    grid.append(h('button', {
      type: 'button', class: `${status}${d === today ? ' today' : ''}${conf[d] ? ' confirmed' : ''}`,
      'aria-label': `${fmtDay(d)}: ${label}${conf[d] ? ', confirmed by you' : ''}`, title: `${fmtDay(d)}: ${label}`,
      onclick: () => openDay(d, row),
    }, String(dt.getDate())));
  });
  const wrap = h('div', null, grid, h('div', { class: 'legend cal-legend' },
    [['complete', 'Complete'], ['partial', 'Partly recorded'], ['rest', 'Rest day'], ['unknown', 'Unknown']].map(([c, t]) => h('span', null, h('i', { class: c }), t))));
  return wrap;
}

/* ================================================================== *
 * Today hero — the watch
 * ================================================================== */
function watchHero() {
  const r = rec().readiness || {}, sl = rec().sleep || {}, hs = rec().hrv_status || {}, n = lastNight();
  const hr = new Date().getHours();
  const greet = hr < 5 ? 'Good night' : hr < 12 ? 'Good morning' : hr < 18 ? 'Good afternoon' : 'Good evening';
  const name = wearer();
  const score = r.score;
  return h('article', { class: 'watch', 'data-m': 'ready' },
    h('div', { class: 'watch-top' },
      h('h1', { class: 'watch-hello' }, name ? `${greet}, ${name}` : greet),
      h('p', { class: 'watch-date' }, new Date().toLocaleDateString(LOCALE, { weekday: 'long', day: 'numeric', month: 'long' }))),
    h('div', { class: 'watch-main' },
      h('div', { class: 'watch-ring' }, ring(score, { size: 132, stroke: 12, label: isNum(score) ? `Readiness ${Math.round(score)} of 100` : 'Readiness: not enough data' })),
      h('div', null,
        h('p', { class: 'watch-score-label' }, h('a', { class: 'card-link', href: '#d/ready' }, 'Readiness')),
        h('p', { class: 'watch-score-word' }, readinessWord(score)),
        h('p', { class: 'watch-reason' }, isNum(score) ? 'Compared with your own normal, where 50 is a typical day.' : (r.reason || 'Not enough inputs yet.')))),
    h('dl', { class: 'watch-vitals' },
      vital('sleep', 'Sleep', isNum(sl.sleep_s) ? fmtDur(sl.sleep_s, true) : null),
      vital('heart', 'Resting HR', isLastNight(n) && isNum(n.resting_hr) ? `${fmt(n.resting_hr)} bpm` : null),
      vital('hrv', 'HRV status', hs.status && hs.status !== 'baseline_building' && hs.status !== 'insufficient' ? hrvLabel(hs.status)[0] + (hs.provisional ? ' (provisional)' : '') : null, hs.status === 'baseline_building' ? `Learning, ${hs.baseline_nights || 0}/${hs.required_baseline_nights || 14}` : null)));
}
function vital(m, label, value, fallback) {
  return h('div', { 'data-m': m }, h('dt', null, h('i'), label), h('dd', { class: value ? '' : 'none' }, value || fallback || 'No data'));
}

/* ================================================================== *
 * Detail views
 * ================================================================== */
function chartW() { return window.innerWidth >= 720 ? 640 : 340; }

const DETAIL = {
  weight: {
    title: 'Weight', m: 'weight',
    render() {
      const w = D().weight || {}, span = dateSpan(S.range), items = weightItems(span);
      const inRange = (w.entries || []).filter(e => e.date >= span[0]);
      const kgs = inRange.map(e => e.kg);
      const change = inRange.length > 1 ? inRange[inRange.length - 1].trend_kg - inRange[0].trend_kg : null;
      return [card('weight', 'Weight', { cls: 'wide' },
        items.some(i => isNum(i.v)) ? [lineChart({ items, W: chartW(), H: 200, alt: true, fmtV: v => fmt(v, 1), unit: 'kg', label: 'Weight and 7-day trend' }),
          legend([['line', 'Weigh-ins'], ['dash', '7-day trend']]),
          stats([['Latest', fmt((w.latest || {}).kg, 1), 'kg'], ['Change in range', signedKg(change), 'kg'], ['Lowest', fmt(kgs.length ? Math.min(...kgs) : null, 1), 'kg'], ['Weigh-ins', `${kgs.length}`]])]
          : pending('No weigh-ins in this range', 'Weight comes from your configured weight source and syncs every 30 minutes.'),
        info('About the trend', 'Day-to-day weight swings with water, salt and meal timing. The 7-day trend averages your weigh-ins over the past week and is the better guide to real change.'))];
    },
  },
  ready: {
    title: 'Readiness', m: 'ready', noRange: true,
    render() {
      const r = rec().readiness || {}, comps = r.components || {}, weights = r.weights || {};
      const names = Object.keys(weights).length ? Object.keys(weights) : ['hrv', 'resting_hr', 'sleep', 'training_load'];
      return [
        card('ready', 'Today', { chip: expChip(), cls: 'wide' },
          h('div', { style: { display: 'flex', gap: '18px', alignItems: 'center', flexWrap: 'wrap' } },
            h('div', { style: { width: '128px' } }, ring(r.score, { size: 128, stroke: 12 })),
            h('div', { style: { flex: '1', minWidth: '180px' } },
              h('p', { class: 'status-word', style: { fontSize: '22px' } }, readinessWord(r.score)),
              h('p', { class: 'lede' }, isNum(r.score) ? 'A weighted mix of the inputs below. 50 means a typical day for you.' : (r.reason || 'Not enough inputs yet.')))),
          h('div', { class: 'comps' }, names.map(k => compRow(COMPONENT_NAMES[k] || k, comps[k], weights[k]))),
          missingList(r.missing),
          info('How readiness works',
            'Each input is scored against your own recent history: 50 is your normal, higher is better rested. The score is the weighted mean of the inputs that are available.',
            'Missing inputs are left out rather than guessed. With fewer than two inputs, or without overnight HRV or sleep, no score is shown.',
            r.method ? `Method: ${r.method}.` : null)),
        h('p', { class: 'foot' }, 'Readiness history is not stored yet, so only today is shown.'),
      ];
    },
  },

  sleep: {
    title: 'Sleep', m: 'sleep',
    render() {
      const sl = rec().sleep || {}, nights = rec().sleep_nights || [], map = byNight(nights, 'night');
      const span = dateSpan(S.range);
      const items = bucket(span, d => { const n = map.get(d) || {}; return { v: isNum(n.sleep_s) ? n.sleep_s / 3600 : null, inner: isNum(n.restful_s) ? n.restful_s / 3600 : null }; }, 'avg')
        .map(i => ({ ...i, cls: 'soft', inner: i.week ? null : i.inner }));
      const inRange = span.map(d => map.get(d)).filter(Boolean);
      const timed = inRange.filter(n => isNum(n.onset) && isNum(n.wake)).slice(-14);
      return [
        card('sleep', 'Last night', { chip: sl.status === 'experimental' ? expChip() : sl.status === 'duration_only' ? chip('Duration only') : null, cls: 'wide' },
          isNum(sl.sleep_s) ? [
            h('div', { style: { display: 'flex', gap: '18px', alignItems: 'center', flexWrap: 'wrap' } },
              h('div', { style: { width: '112px' } }, ring(sl.score, { size: 112, stroke: 11, label: isNum(sl.score) ? `Sleep score ${sl.score}` : 'No sleep score' })),
              h('div', null, big(fmtDur(sl.sleep_s, true), 'asleep'), h('p', { class: 'lede' }, isNum(sl.score) ? `${sleepWord(sl.score)}. ${sl.reason || ''}` : sl.reason || ''))),
            stats([['Restful', fmtDur(sl.restful_s, true)], ['Sleep need', fmtDur(sl.need_s, true)], ['Timing spread', isNum(sl.regularity_sd_min) ? `±${fmt(sl.regularity_sd_min)}` : '—', 'min']]),
            h('div', { class: 'comps' }, Object.entries(sl.components || {}).map(([k, v]) => compRow(COMPONENT_NAMES[k] || k, { score: v }))),
          ] : pending('No sleep recorded for last night', sl.reason),
          missingList(sl.missing),
          info('How the sleep score works',
            'Duration is compared with your sleep need. Once bed and wake times are known for three nights in a week, the regularity of your timing counts too.',
            'Restful sleep is the watch’s own estimate of quieter sleep. It is not a sleep-stage measurement.')),
        card('sleep', 'Sleep per night', { cls: 'wide' },
          inRange.length ? [
            barChart({ items, W: chartW(), H: 180, fmtV: v => fmt(v, 1), unit: 'h', ref: isNum(sl.need_s) ? { v: sl.need_s / 3600, label: 'need' } : null, label: 'Sleep duration per night' }),
            legend([['soft', 'Asleep'], ['', 'Restful'], ['dash', 'Sleep need'], ['gap', 'No data']]),
            stats([['Average', fmtDur(avg(inRange.map(n => n.sleep_s)), true)], ['Nights recorded', `${inRange.length} of ${span.length}`]]),
          ] : pending('No nights in this range', 'Nights appear here after the watch syncs in the morning.')),
        card('sleep', 'Bed and wake times', { cls: 'wide' },
          timed.length ? [timingChart(timed, chartW()), h('p', { class: 'lede', style: { fontSize: '13px' } }, `Showing the last ${timed.length} nights with known times.`)]
            : pending('No bed and wake times yet', 'Times come from exported sleep sessions. Nights reported only as a daily total have a duration but no timing.')),
      ];
    },
  },

  hrv: {
    title: 'HRV status', m: 'hrv',
    render() {
      const st = rec().hrv_status || {};
      const [word] = hrvLabel(st.status);
      const span = dateSpan(S.range), m = byNight(rec().nights, 'night');
      const items = bucket(span, d => ({ v: (m.get(d) || {}).rmssd_ms ?? null }), 'avg');
      const has = items.some(i => isNum(i.v));
      const pts = ((D().hrv || {}).analysis || {}).points || [];
      return [
        card('hrv', 'This week', { chip: st.provisional ? chip('Provisional', '', st.reason) : expChip(), cls: 'wide' },
          h('p', { class: 'status-word', style: { fontSize: '22px' } }, word),
          st.status === 'baseline_building'
            ? pending('Learning your usual range', st.reason, { n: st.baseline_nights || 0, of: st.required_baseline_nights || 14, unit: 'nights' })
            : [hrvGauge(st, 280), stats([[st.provisional ? 'Last 3 nights' : '7-night average', fmt(st.weekly_rmssd_ms), 'ms'], ['Usual range', isNum(st.baseline_low_ms) ? `${fmt(st.baseline_low_ms)}–${fmt(st.baseline_high_ms)}` : '—', 'ms'], ['Last night', fmt(st.last_night_rmssd_ms), 'ms'], ['Nights this week', isNum(st.nights_this_week) ? `${st.nights_this_week} of 7` : '—']]), h('p', { class: 'lede' }, st.reason || '')],
          info('How HRV status works',
            'Heart rate variability (RMSSD) is measured in short recordings during the night. The 7-night average is compared with the range that is usual for you.',
            'Balanced means the average sits inside that range. Values outside it are worth noting, not alarming. These recordings are experimental and unvalidated.',
            'Until 14 Pebble nights exist, a provisional status compares your last 3 nights with your earlier Pebble nights, using how much your HRV varied night to night in your earlier Garmin history.')),
        card('hrv', 'Nightly HRV', { cls: 'wide' },
          has ? [lineChart({ items, W: chartW(), H: 180, unit: 'ms', band: isNum(st.baseline_low_ms) ? { lo: st.baseline_low_ms, hi: st.baseline_high_ms } : null, label: 'Nightly HRV' }),
            legend([['line', 'Nightly RMSSD'], ['soft', 'Usual range'], ['gap', 'No usable recording']])]
            : pending('No usable overnight recordings in this range', 'Enable night recordings in the HRV watch app.')),
        spotChecks(pts),
      ];
    },
  },

  rhr: {
    title: 'Resting heart rate', m: 'rhr',
    render() {
      const span = dateSpan(S.range), m = byNight(rec().nights, 'night');
      const items = bucket(span, d => ({ v: (m.get(d) || {}).resting_hr ?? null }), 'avg');
      const vals = span.map(d => (m.get(d) || {}).resting_hr);
      return [card('rhr', 'Overnight resting heart rate', { cls: 'wide' },
        items.some(i => isNum(i.v)) ? [lineChart({ items, W: chartW(), H: 180, unit: 'bpm', label: 'Resting heart rate per night' }),
          stats([['Average', fmt(avg(vals)), 'bpm'], ['Lowest', fmt(Math.min(...vals.filter(isNum))), 'bpm'], ['Nights', `${vals.filter(isNum).length} of ${span.length}`]])]
          : pending('No overnight readings in this range', 'Resting heart rate comes from the scheduled night recordings of the HRV watch app.'),
        info('About resting heart rate', 'Taken from the average beat interval of the night recordings. A rise of several beats over your usual level often follows hard training, illness, alcohol or short sleep.'))];
    },
  },

  breath: {
    title: 'Breathing rate', m: 'breath',
    render() {
      const span = dateSpan(S.range), m = byNight(rec().nights, 'night');
      const items = bucket(span, d => ({ v: (m.get(d) || {}).breaths_per_min ?? null }), 'avg');
      const vals = span.map(d => (m.get(d) || {}).breaths_per_min);
      return [card('breath', 'Overnight breathing rate', { chip: expChip(), cls: 'wide' },
        items.some(i => isNum(i.v)) ? [lineChart({ items, W: chartW(), H: 180, unit: 'br/min', fmtV: v => fmt(v, 1), label: 'Breathing rate per night' }),
          stats([['Average', fmt(avg(vals), 1), 'br/min'], ['Nights', `${vals.filter(isNum).length} of ${span.length}`]])]
          : pending('No reliable readings in this range', 'Breathing rate is estimated from the rhythm of heartbeats and shown only when that rhythm is clear.'),
        info('About breathing rate', 'Estimated from how heart rate rises and falls with each breath during night recordings. Nights with an unclear signal are left blank.'))];
    },
  },

  stress: {
    title: 'Stress', m: 'stress', noRange: true,
    render() {
      const st = D().stress || {};
      const pts = ((D().hrv || {}).analysis || {}).points || [];
      const bp = ((D().hrv || {}).analysis || {}).baseline_progress || {};
      return [
        card('stress', 'Arousal score', { chip: expChip(), cls: 'wide' },
          isNum(st.experimental_score) ? [big(fmt(st.experimental_score), 'of 100'), h('p', { class: 'lede' }, stressWord(st.experimental_score))]
            : pending(st.status === 'baseline_building' ? 'Building baseline' : 'No score yet', st.reason, { n: st.baseline_days || 0, of: st.required_days || 14, unit: 'days' }),
          Object.keys(bp).length ? stats(Object.entries(bp).map(([k, v]) => [`${cap(k)} baseline`, `${v.adequate_baseline_days ?? 0} of ${v.required_days ?? 14}`, 'days'])) : null,
          info('How the stress score works',
            'Each daytime spot check is compared with your own earlier spot checks taken in the same way. Lower HRV than usual maps to a higher score.',
            'Movement during a recording spoils it, so those are skipped. This is an experimental estimate of physiological arousal, not a measure of how you feel.')),
        spotChecks(pts),
      ];
    },
  },

  steps: {
    title: 'Steps and activity', m: 'steps',
    render() {
      const byDay = obsByLocalDay(), span = dateSpan(S.range, isoOf(new Date()));
      const items = bucket(span, d => { const o = byDay.get(d); const v = o ? sum(o.filter(x => !x.snapshot).map(x => x.steps)) : null; return { v }; }, 'avg');
      return [CARDS.steps(), card('steps', 'Steps from synced minutes', { cls: 'wide' },
        items.some(i => isNum(i.v)) ? [barChart({ items, W: chartW(), H: 180, unit: 'steps', label: 'Steps per day from minute records' }),
          h('p', { class: 'lede', style: { fontSize: '13.5px' } }, 'Totals add up minute records that reached the server. Days with gaps in the export will read low.')]
          : pending('No minute records in this range', 'Minute steps arrive with the history export from the watch.'))];
    },
  },

  heart: {
    title: 'Heart rate', m: 'heart',
    render() {
      const byDay = obsByLocalDay(), span = dateSpan(S.range, isoOf(new Date()));
      const items = bucket(span, d => {
        const v = (byDay.get(d) || []).map(o => o.hr).filter(isNum);
        return v.length ? { v: avg(v), lo: Math.min(...v), hi: Math.max(...v) } : { v: null };
      }, 'avg');
      return [CARDS.heart(), card('heart', 'Daily range', { cls: 'wide' },
        items.some(i => isNum(i.v)) ? [lineChart({ items, W: chartW(), H: 180, unit: 'bpm', label: 'Daily heart rate range and average' }), legend([['line', 'Average'], ['soft', 'Lowest to highest']])]
          : pending('No heart rate in this range', 'Minute heart rate arrives with the history export.'))];
    },
  },

  train: {
    title: 'Training load', m: 'train',
    render() {
      const t = D().training || {}, lt = t.latest || {}, prof = D().profile || {}, model = D().model || {};
      const hist = historyMap(), span = dateSpan(S.range);
      const items = bucket(span, d => loadItem(d, hist.get(d)), 'sum');
      const acu = bucket(span, d => ({ v: (hist.get(d) || {}).acute ?? null, alt: (hist.get(d) || {}).chronic ?? null }), 'avg')
        .map((i, k) => ({ ...i, alt: span.length > 60 ? avg(span.slice(k * 7, k * 7 + 7).map(d => (hist.get(d) || {}).chronic)) : i.alt }));
      const zones = t.zones || [], zmax = Math.max(1, ...zones.map(z => z.minutes || 0));
      const weeks = (t.weeks || []).slice(-8);
      return [
        card('train', 'Now', { chip: lt.status !== 'ready' ? chip('Building baseline') : null, cls: 'wide' },
          stats([['7-day load', fmt(lt.load7), lt.known_days7 != null ? `${lt.known_days7}/7 days known` : ''], ['Acute', fmt(lt.acute, 1), 'fatigue'], ['Chronic', fmt(lt.chronic, 1), 'fitness'], ['Form', isNum(lt.form) ? signed(lt.form) : '—']]),
          lt.status !== 'ready' ? pending('Chronic load needs six weeks', `Needs 42 consecutive confirmed days; ${lt.baseline_days || 0} so far. Confirm days in the calendar below.`, { n: lt.baseline_days || 0, of: 42, unit: 'days' }) : null,
          h('p', { class: 'lede' }, `Load method: ${prof.load_method === 'rpe' ? 'session effort' : 'heart rate'}${isNum(prof.hr_max) ? `, max HR ${prof.hr_max} bpm` : ''}. `, h('a', { href: '#more' }, 'Change')),
          info('How training load works',
            'Each workout gets a load from time in heart-rate zones, or from minutes times your effort rating. Acute load is the last week; chronic load is the last six weeks. Form is chronic minus acute.',
            'Days without a confirmation are unknown, not zero. That keeps an unworn day from looking like a rest day.',
            model.hr ? `Heart-rate model: ${model.hr}.` : null, model.rpe ? `Effort model: ${model.rpe}.` : null)),
        card('train', 'Daily load', { cls: 'wide' },
          barChart({ items, W: chartW(), H: 180, unit: 'load', label: 'Training load per day' }),
          legend([['', 'Recorded'], ['partial', 'Partly recorded'], ['rest', 'Rest day'], ['gap', 'Unknown']])),
        card('train', 'Acute and chronic load', { cls: 'wide' },
          acu.some(i => isNum(i.v) || isNum(i.alt)) ? [lineChart({ items: acu, W: chartW(), H: 170, alt: true, zeroBase: true, label: 'Acute and chronic load' }), legend([['line', 'Acute (7 days)'], ['dash', 'Chronic (42 days)']])]
            : pending('Not enough confirmed days yet', 'These lines appear once enough days are confirmed.')),
        card('train', 'Time in heart-rate zones', {},
          zones.length && zones.some(z => z.minutes > 0) ? h('div', { class: 'zones' }, zones.map(z => h('div', { class: 'zone' },
            h('span', null, z.label), h('span', { class: 'bar' }, h('span', { style: { width: `${100 * (z.minutes || 0) / zmax}%` } })), h('span', { class: 'min' }, `${fmt(z.minutes)} min`))))
            : pending('No zone minutes yet', 'Zone minutes come from workouts with at least 80% heart-rate coverage.'),
          isNum(prof.hr_max) ? h('p', { class: 'lede', style: { fontSize: '13px' } }, `Percent of max heart rate (${prof.hr_max} bpm).`) : null),
        card('train', 'Weekly load', {},
          weeks.length ? barChart({ items: weeks.map(w => ({ key: w.start, label: w.start, v: w.load, cls: w.status === 'partial' || w.unknown_days ? 'partial' : undefined, note: `${w.confirmed_days || 0} confirmed days`, week: true })), H: 140, unit: 'load', label: 'Weekly load' }) : pending('No weeks yet'),
          legend([['', 'Fully confirmed week'], ['partial', 'Week with unknown days']])),
        (t.notes || []).length ? h('p', { class: 'foot' }, t.notes.join(' ')) : null,
      ];
    },
  },

  workouts: { title: 'Workouts', m: 'workouts', noRange: true, render() { return [CARDS.workouts(0, true)]; } },

  calendar: {
    title: 'Recording completeness', m: 'calendar', noRange: true,
    render() {
      const hist = historyMap();
      const keys = [...hist.keys()].sort();
      const days = keys.length ? dateSpan(Math.min(366, Math.max(28, Math.round((parseISO(todayISO()) - parseISO(keys[0])) / 86400000) + 1))) : dateSpan(28);
      return [card('calendar', 'Day by day', { cls: 'wide' },
        h('p', { class: 'lede', style: { marginTop: 0 } }, 'Tap a day to mark it complete (every workout was recorded) or a rest day. Unconfirmed days count as unknown.'),
        calendarGrid(days, hist, false),
        info('Why confirm days?', 'The watch cannot tell a rest day from a day you did not wear it. Confirming lets training load treat the day as known.'))];
    },
  },

  device: { title: 'Watch and sync', m: 'device', noRange: true, render() { return devicesView(); } },
};

function compRow(name, comp, weight) {
  const sc = comp && isNum(comp.score) ? comp.score : null;
  return h('div', { class: 'comp-row' },
    h('span', { class: 'name' }, name, isNum(weight) ? h('span', { style: { fontWeight: 400, color: 'var(--ink-3)', fontSize: '13px' } }, ` ${Math.round(weight * 100)}%`) : null),
    h('span', { class: `score${sc == null ? ' none' : ''}` }, sc == null ? 'Missing' : String(Math.round(sc))),
    h('div', { class: `meter${sc == null ? ' empty' : ''}`, 'aria-hidden': 'true' }, sc != null ? h('span', { style: { width: `${clamp(sc, 0, 100)}%` } }) : null, h('i')),
    comp && comp.detail ? h('span', { class: 'detail' }, comp.detail) : null);
}

function spotChecks(pts) {
  const ok = pts.filter(p => isNum(p.experimental_rmssd_ms));
  const q = (D().hrv || {}).quality || {};
  return card('hrv', 'Daytime spot checks', { chip: expChip(), cls: 'wide' },
    pts.length ? [
      lineChart({ items: pts.slice(-30).map(p => ({ key: isoOf(new Date(p.ts * 1000)), label: isoOf(new Date(p.ts * 1000)), v: p.experimental_rmssd_ms ?? null, hollow: p.context !== 'scheduled', note: `${fmtClock(p.ts)}, ${p.context || 'manual'}, ${String(p.quality || '').replace(/_/g, ' ')}` })), W: chartW(), H: 160, unit: 'ms', label: 'Recent HRV recordings' }),
      h('p', { class: 'lede', style: { fontSize: '13.5px' } }, `${ok.length} of ${pts.length} recent recordings were clean enough to use. Gaps are recordings spoilt by movement or noise.`),
      isNum(q.experimental_rmssd_ms) ? stats([['Latest', fmt(q.experimental_rmssd_ms, 1), 'ms'], ['Beats kept', isNum(q.retained_fraction) ? `${fmt(q.retained_fraction * 100)}%` : '—'], ['Taken', fmtClock((D().hrv || {}).ts)]]) : null,
    ] : pending('No spot checks yet', 'Open the HRV app on the watch and keep still for three minutes.'));
}

function devicesView() {
  const d = D(), lt = d.latest || {}, st = lt.status || {}, col = d.collector || {};
  const sched = (d.collector_history || []).find(c => isNum(c.next_wakeup)) || {};
  return [
    card('device', 'Sync', { cls: 'wide' },
      h('dl', { class: 'kv' },
        kv('Last upload', isNum(d.last_received) ? `${fmtDateTime(d.last_received)} (${ago(d.sync_age_seconds)})` : 'Never'),
        kv('Battery', isNum(st.battery_percent) ? `${st.battery_percent}%${st.battery_charging ? ', charging' : ''}` : 'Unknown'),
        kv('Hourly reports stored', fmt(d.reports)),
        kv('Minute records stored', fmt(d.minute_records)))),
    card('device', 'HRV recorder', { cls: 'wide' },
      isNum(col.received) ? h('dl', { class: 'kv' },
        kv('Last recording', fmtDateTime(col.measurement_ts || col.ts)),
        kv('Started', col.launch === 'scheduled' ? 'On schedule' : 'By hand'),
        kv('App version', col.collector_version != null ? String(col.collector_version) : '—'),
        sched.nights_enabled != null ? kv('Night recordings', sched.nights_enabled ? 'On' : 'Off') : null,
        isNum(sched.schedule_ok) ? kv('Scheduled slots', `${sched.schedule_ok} armed${sched.schedule_error ? `, error ${sched.schedule_error}` : ''}`) : null,
        isNum(sched.next_wakeup) && sched.next_wakeup > nowSec() ? kv('Next night recording', fmtDateTime(sched.next_wakeup)) : null)
        : pending('The HRV app has not reported yet', 'Install it on the watch and open it once.')),
    card('device', 'Watch apps', { cls: 'wide' },
      h('p', { class: 'lede', style: { marginTop: 0 } }, 'Install these on the watch, then pair each one in the phone app. HRV and History upload to this server; Today only reads the daily summary.'),
      h('ul', { class: 'list' }, [
        ['hrv', 'HRV', 'Night and daytime HRV, resting heart rate and breathing.'],
        ['history', 'History', 'Minute heart rate and steps, sleep sessions and workouts.'],
        ['today', 'Today', 'Readiness, sleep, HRV, resting pulse and training load on the watch. Read-only.'],
      ].map(([kind, name, what]) => h('li', null, h('span', { class: 't' }, name), h('span', { class: 's' }, what),
        h('span', { class: 'acts' }, h('a', { class: 'btn small', href: `download/${kind}`, download: '' }, 'Download'), h('a', { class: 'btn small', href: `pair?kind=${kind}` }, 'Pair')))))),
  ];
}
const kv = (k, v) => [h('dt', null, k), h('dd', null, v)];

/* ================================================================== *
 * Settings
 * ================================================================== */
function settingsCard() {
  const prof = D().profile || {};
  let method = prof.load_method === 'rpe' ? 'rpe' : 'hr';
  const hrInput = h('input', { type: 'number', inputmode: 'numeric', min: 100, max: 230, step: 1, value: isNum(prof.hr_max) ? prof.hr_max : '', id: 'hrmax', placeholder: 'e.g. 185' });
  const segBtns = [['hr', 'Heart rate'], ['rpe', 'Session effort']].map(([k, t]) =>
    h('button', { type: 'button', 'aria-pressed': String(method === k), onclick: e => { method = k; segBtns.forEach(b => b.setAttribute('aria-pressed', String(b === e.currentTarget))); } }, t));
  const err = h('p', { class: 'sheet-error', role: 'alert', hidden: true });
  const save = h('button', { type: 'submit', class: 'btn primary' }, 'Save profile');
  const form = h('form', { class: 'above', onsubmit: async e => {
    e.preventDefault(); err.hidden = true; save.disabled = true;
    const body = { load_method: method };
    if (hrInput.value.trim()) body.hr_max = Number(hrInput.value);
    try { await api('api/profile', 'PUT', body); toast('Profile saved'); load(true); }
    catch (x) { err.textContent = x.message; err.hidden = false; }
    finally { save.disabled = false; }
  } },
  h('div', { class: 'form-grid two' },
    h('label', { class: 'field', for: 'hrmax' }, 'Maximum heart rate', hrInput, h('span', { class: 'field-hint' }, 'Sets the zones for heart-rate load.')),
    h('div', { class: 'field' }, h('span', { id: 'lm' }, 'Training load from'), h('div', { class: 'seg', role: 'group', 'aria-labelledby': 'lm' }, segBtns))),
  err, h('div', { class: 'btn-row' }, save));
  return card('train', 'Profile', { cls: 'wide' }, form);
}

function themeCard() {
  const cur = store.get('ph-theme') || 'system';
  const btns = [['system', 'Match device'], ['light', 'Light'], ['dark', 'Dark']].map(([k, t]) =>
    h('button', { type: 'button', 'aria-pressed': String(cur === k), onclick: () => { setTheme(k === 'system' ? null : k); render(); } }, t));
  return card('device', 'Appearance', {}, h('div', { class: 'seg above', role: 'group', 'aria-label': 'Theme' }, btns));
}

function setTheme(t) {
  store.set('ph-theme', t);
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  paintThemeButton();
}
const effectiveDark = () => (document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')) === 'dark';
function paintThemeButton() {
  const b = document.getElementById('theme-btn');
  const dark = effectiveDark();
  b.replaceChildren(icon(dark ? 'sun' : 'moon'));
  b.setAttribute('aria-label', dark ? 'Use light theme' : 'Use dark theme');
  b.title = b.getAttribute('aria-label');
}

/* ================================================================== *
 * Dialogs: effort, workout, day status
 * ================================================================== */
const dlg = () => document.getElementById('dialog');
function openSheet(...content) {
  const d = dlg();
  d.replaceChildren(h('div', { class: 'sheet-body' }, content));
  if (typeof d.showModal === 'function') d.showModal(); else d.setAttribute('open', '');
  const f = d.querySelector('input, [aria-pressed="true"], button');
  if (f) f.focus();
}
function closeSheet() { const d = dlg(); if (d.open) d.close(); }

function rpeScale(initial, onPick) {
  let val = isNum(initial) ? Math.round(initial) : null;
  const btns = [];
  for (let i = 1; i <= 10; i++) {
    btns.push(h('button', { type: 'button', 'aria-pressed': String(val === i), 'aria-label': `Effort ${i} of 10`, onclick: e => { val = i; btns.forEach(b => b.setAttribute('aria-pressed', String(b === e.currentTarget))); onPick(i); } }, String(i)));
  }
  return [h('div', { class: 'rpe', role: 'group', 'aria-label': 'Perceived effort from 1 to 10' }, btns), h('div', { class: 'rpe-words', 'aria-hidden': 'true' }, h('span', null, 'Very easy'), h('span', null, 'Maximal'))];
}

function openRpe(w) {
  let val = isNum(w.rpe) ? Math.round(w.rpe) : null;
  const err = h('p', { class: 'sheet-error', role: 'alert', hidden: true });
  const save = h('button', { type: 'button', class: 'btn primary', disabled: val == null, onclick: async () => {
    save.disabled = true; err.hidden = true;
    try { await api('api/workouts', 'POST', { id: w.id, start: w.start, seconds: w.seconds, rpe: val }); closeSheet(); toast('Effort saved'); load(true); }
    catch (x) { err.textContent = x.message; err.hidden = false; save.disabled = false; }
  } }, 'Save effort');
  openSheet(h('h2', null, 'How hard was it?'),
    h('p', null, `${cap(w.kind || 'Workout')}, ${fmtDateTime(w.start)}${isNum(w.seconds) ? `, ${Math.round(w.seconds / 60)} min` : ''}`),
    rpeScale(val, v => { val = v; save.disabled = false; }), err,
    h('div', { class: 'btn-row' }, save, h('button', { type: 'button', class: 'btn', onclick: closeSheet }, 'Cancel')));
}

async function toggleIgnore(w) {
  try { await api('api/workouts', 'POST', { id: w.id, ignored: !w.ignored }); toast(w.ignored ? 'Workout restored' : 'Workout excluded'); load(true); }
  catch (x) { toast(x.message, true); }
}

function openWorkoutForm() {
  const now = new Date(); now.setMinutes(now.getMinutes() - 60, 0, 0);
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  let rpe = null;
  const type = h('input', { id: 'w-type', type: 'text', maxlength: 40, value: '', placeholder: 'e.g. run, strength, climbing', autocomplete: 'off' });
  const start = h('input', { id: 'w-start', type: 'datetime-local', value: local, max: new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16), required: true });
  const mins = h('input', { id: 'w-min', type: 'number', inputmode: 'numeric', min: 1, max: 1440, value: 45, required: true });
  const err = h('p', { class: 'sheet-error', role: 'alert', hidden: true });
  const save = h('button', { type: 'submit', class: 'btn primary' }, 'Log workout');
  const form = h('form', { onsubmit: async e => {
    e.preventDefault(); err.hidden = true;
    const ts = Math.floor(new Date(start.value).getTime() / 1000);
    if (!isNum(ts)) { err.textContent = 'Enter when the workout started.'; err.hidden = false; return; }
    if (rpe == null) { err.textContent = 'Pick an effort from 1 to 10.'; err.hidden = false; return; }
    save.disabled = true;
    try { await api('api/workouts', 'POST', { type: type.value.trim() || 'workout', start: ts, seconds: Math.round(Number(mins.value) * 60), rpe }); closeSheet(); toast('Workout logged'); load(true); }
    catch (x) { err.textContent = x.message; err.hidden = false; save.disabled = false; }
  } },
  h('h2', null, 'Log a workout'),
  h('p', null, 'For sessions the watch did not record. Load is minutes times effort.'),
  h('div', { class: 'form-grid' },
    h('label', { class: 'field', for: 'w-type' }, 'Activity', type),
    h('div', { class: 'form-grid two' }, h('label', { class: 'field', for: 'w-start' }, 'Started', start), h('label', { class: 'field', for: 'w-min' }, 'Minutes', mins)),
    h('div', { class: 'field' }, h('span', null, 'Effort'), rpeScale(null, v => { rpe = v; }))),
  err, h('div', { class: 'btn-row' }, save, h('button', { type: 'button', class: 'btn', onclick: closeSheet }, 'Cancel')));
  openSheet(form);
}

function openDay(day, row) {
  const conf = (D().confirmations || {})[day];
  const err = h('p', { class: 'sheet-error', role: 'alert', hidden: true });
  const choose = async status => {
    err.hidden = true;
    try { await api('api/days', 'POST', { date: day, day, status }); closeSheet(); toast(status === 'unknown' ? 'Confirmation cleared' : status === 'rest' ? 'Marked as rest day' : 'Marked complete'); load(true); }
    catch (x) { err.textContent = x.message; err.hidden = false; }
  };
  const opt = (status, title, sub) => h('button', { type: 'button', class: 'btn', 'aria-pressed': String(conf === status || (!conf && status === 'unknown')), onclick: () => choose(status) }, title, h('span', null, sub));
  openSheet(h('h2', null, fmtDay(day, { weekday: 'long', day: 'numeric', month: 'long' })),
    h('p', null, isNum(row.load) ? `Recorded load ${fmt(row.load)}.` : 'No workout load recorded.'),
    h('div', { class: 'choice' },
      opt('complete', 'Complete', 'Every workout this day was recorded.'),
      opt('rest', 'Rest day', 'No training at all.'),
      opt('unknown', 'Unknown', 'Not sure, or the watch was off.')),
    err, h('div', { class: 'btn-row' }, h('button', { type: 'button', class: 'btn', onclick: closeSheet }, 'Cancel')));
}

let toastTimer = null;
function toast(msg, isErr) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.toggle('err', !!isErr); t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 3200);
}

/* ================================================================== *
 * Routing and pages
 * ================================================================== */
const TABS = [
  { id: 'today', label: 'Today' }, { id: 'sleep', label: 'Sleep' }, { id: 'recovery', label: 'Recovery' },
  { id: 'training', label: 'Training' }, { id: 'more', label: 'More' },
];
const DETAIL_TAB = { weight: 'today', ready: 'recovery', sleep: 'sleep', hrv: 'recovery', rhr: 'recovery', breath: 'recovery', stress: 'recovery', steps: 'today', heart: 'today', train: 'training', workouts: 'training', calendar: 'training', device: 'more' };

function route() {
  const raw = decodeURIComponent(location.hash.replace(/^#\/?/, ''));
  if (raw.startsWith('d/')) { const key = raw.slice(2); if (DETAIL[key]) return { detail: key, tab: DETAIL_TAB[key] }; }
  return { tab: TABS.some(t => t.id === raw) ? raw : 'today' };
}

function renderNav(active) {
  const mk = bottom => TABS.map(t => h('a', { href: `#${t.id}`, 'aria-current': t.id === active ? 'page' : null }, bottom ? icon(t.id) : null, h('span', null, t.label)));
  document.querySelector('.tabs-top').replaceChildren(...mk(false));
  document.querySelector('.tabs-bottom').replaceChildren(...mk(true));
}

function pageHead(title, sub, extra) {
  return h('div', { class: 'page-head' }, h('div', null, h('h1', null, title), sub ? h('p', null, sub) : null), extra || null);
}

const PAGES = {
  today() {
    return [h('div', { class: 'feed' }, watchHero(), CARDS.sleep(), CARDS.hrv(), CARDS.steps(), CARDS.weight(), CARDS.heart(), CARDS.train(), CARDS.rhr(), CARDS.breath(), CARDS.stress(), CARDS.workouts(3), CARDS.device())];
  },
  sleep() {
    return [pageHead('Sleep', null, rangeSwitch()), h('div', { class: 'feed' }, DETAIL.sleep.render(), CARDS.breath())];
  },
  recovery() {
    return [pageHead('Recovery'), h('div', { class: 'feed' }, DETAIL.ready.render().filter(n => n.tagName !== 'P'), CARDS.hrv(), CARDS.rhr(), CARDS.breath(), CARDS.stress())];
  },
  training() {
    return [pageHead('Training', null, rangeSwitch()), h('div', { class: 'feed' }, DETAIL.train.render(), CARDS.workouts(5), CARDS.calendar())];
  },
  more() {
    return [pageHead('More'), h('div', { class: 'feed' }, devicesView(), settingsCard(), themeCard(),
      card('device', 'About', { cls: 'wide' }, h('p', { class: 'lede', style: { marginTop: 0 } }, 'Pebble Health runs on your own server. Your data stays there. Scores marked experimental come from an unvalidated watch sensor and are for personal interest, not medical use.')))];
  },
};

function render() {
  const main = document.getElementById('main');
  const r = route();
  renderNav(r.tab);
  renderStatus();
  if (!S.data) {
    main.replaceChildren(S.error
      ? h('div', { class: 'error-state' }, h('h1', null, 'Could not load your data'), h('p', null, S.error), h('button', { type: 'button', class: 'btn primary', onclick: () => load(true) }, 'Try again'))
      : h('p', { class: 'loading' }, 'Loading your data…'));
    return;
  }
  let content;
  try {
    if (r.detail) {
      const det = DETAIL[r.detail];
      const back = h('a', { class: 'back', href: `#${r.tab}` }, icon('back'), TABS.find(t => t.id === r.tab).label);
      content = [back, pageHead(det.title, null, det.noRange ? null : rangeSwitch()), h('div', { class: 'feed', 'data-m': det.m }, det.render())];
    } else {
      content = PAGES[r.tab]();
    }
  } catch (e) {
    console.error(e);
    content = [h('div', { class: 'error-state' }, h('h1', null, 'This view could not be drawn'), h('p', null, 'The server sent data in an unexpected shape. Other sections may still work.'))];
  }
  const y = window.scrollY, sameView = main.dataset.view === location.hash;
  main.replaceChildren(...[content].flat(Infinity).filter(Boolean));
  main.dataset.view = location.hash;
  if (sameView) window.scrollTo(0, y);
}

function renderStatus() {
  const line = document.getElementById('sync-line'), banner = document.getElementById('banner');
  const d = S.data;
  if (d) {
    const age = isNum(d.last_received) ? Math.floor(Date.now() / 1000) - d.last_received : d.sync_age_seconds;
    line.textContent = isNum(age) ? `Watch synced ${ago(age)}` : 'Waiting for the first sync';
    line.classList.toggle('stale', isNum(age) && age > 3 * 3600);
  } else line.textContent = S.error ? 'Offline' : 'Loading…';
  if (S.offlineSince !== null && d) {
    const when = S.offlineSince ? new Date(S.offlineSince).toLocaleString(LOCALE, { weekday: 'short', hour: '2-digit', minute: '2-digit' }) : 'an earlier visit';
    banner.textContent = `Offline – showing data from ${when}.`;
    banner.hidden = false;
  } else banner.hidden = true;
}

/* ================================================================== *
 * Boot
 * ================================================================== */
function boot() {
  document.getElementById('refresh-btn').replaceChildren(icon('refresh'));
  document.getElementById('refresh-btn').addEventListener('click', () => load(true));
  document.getElementById('theme-btn').addEventListener('click', () => { setTheme(effectiveDark() ? 'light' : 'dark'); if (route().tab === 'more') render(); });
  paintThemeButton();
  try { matchMedia('(prefers-color-scheme: dark)').addEventListener('change', paintThemeButton); } catch (e) { /* old Safari */ }
  dlg().addEventListener('click', e => { if (e.target === dlg()) closeSheet(); });
  window.addEventListener('hashchange', () => { render(); window.scrollTo(0, 0); document.getElementById('main').focus({ preventScroll: true }); });
  let lastW = window.innerWidth, rt = null;
  window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => { if ((lastW >= 720) !== (window.innerWidth >= 720)) render(); lastW = window.innerWidth; }, 150); });
  window.addEventListener('scroll', () => document.querySelector('.topbar').classList.toggle('scrolled', window.scrollY > 4), { passive: true });
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && Date.now() - S.fetchedAt > 60000) load(true); });
  setInterval(() => { if (document.visibilityState === 'visible') load(true); }, 5 * 60000);
  setInterval(renderStatus, 30000);
  window.addEventListener('online', () => load(true));
  render();
  load(true);
  if ('serviceWorker' in navigator && window.isSecureContext) {
    navigator.serviceWorker.register('sw.js', { scope: './' }).catch(e => console.warn('Service worker not registered:', e.message));
  }
}
boot();
