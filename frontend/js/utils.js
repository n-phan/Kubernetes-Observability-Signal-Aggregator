// ── DOM shorthand ────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

// ── Number formatter ─────────────────────────────────────────────────────────
// Abbreviates large numbers (1.23M, 4.56K) and falls back to 4 sig-figs.
function fmt(v) {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number') {
    if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(2) + 'M';
    if (Math.abs(v) >= 1e3) return (v / 1e3).toFixed(2) + 'K';
    if (Number.isNaN(v))    return 'nan';
    return v.toPrecision(4);
  }
  return String(v);
}

// ── Timezone helpers ─────────────────────────────────────────────────────────
// Timestamps from the API are UTC (ISO with a +00:00 offset); we render them in
// the browser's local zone. `_tzLabel` produces a compact, locale-independent
// suffix like "UTC+8" / "UTC-5" / "UTC+5:30" so the displayed time is unambiguous.
const _pad2 = n => String(n).padStart(2, '0');
function _tzLabel(d) {
  const off = -d.getTimezoneOffset();              // minutes east of UTC
  const sign = off >= 0 ? '+' : '-';
  const h = Math.floor(Math.abs(off) / 60);
  const m = Math.abs(off) % 60;
  return `UTC${sign}${h}${m ? ':' + _pad2(m) : ''}`;
}

// ── ISO timestamp → local "HH:MM:SS UTC±N" ──────────────────────────────────
function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return `${_pad2(d.getHours())}:${_pad2(d.getMinutes())}:${_pad2(d.getSeconds())} ${_tzLabel(d)}`;
}

// ── ISO timestamp → local "YYYY-MM-DD HH:MM UTC±N" ──────────────────────────
function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return `${d.getFullYear()}-${_pad2(d.getMonth() + 1)}-${_pad2(d.getDate())} `
       + `${_pad2(d.getHours())}:${_pad2(d.getMinutes())} ${_tzLabel(d)}`;
}

// ── Log severity classifier ──────────────────────────────────────────────────
// Maps raw severity strings to one of: error | warn | info | unknown.
// Used both for CSS class names and filter logic.
function severityClass(sev) {
  if (!sev) return 'unknown';
  const s = sev.toLowerCase();
  if (s === 'error' || s === 'critical') return 'error';
  if (s === 'warn'  || s === 'warning')  return 'warn';
  if (s === 'info')                      return 'info';
  return 'unknown';
}

// ── RCA confidence → colour ───────────────────────────────────────────────────
// Green ≥ 80%, amber ≥ 50%, red below that.
function confidenceColor(c) {
  if (c >= 0.8) return '#3dffa0';
  if (c >= 0.5) return '#ffb347';
  return '#ff4e6a';
}

// ── Resource usage % → colour ────────────────────────────────────────────────
// Green < 70%, amber < 85%, red at/above that. Used by the cluster gauges.
function usageColor(pct) {
  if (pct == null || isNaN(pct)) return '#5a7a9a';
  if (pct < 70) return '#3dffa0';
  if (pct < 85) return '#ffb347';
  return '#ff4e6a';
}

// ── Byte / rate formatters ───────────────────────────────────────────────────
function fmtBytes(n) {
  if (n == null || isNaN(n)) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let v = n, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return (i === 0 ? v.toFixed(0) : v.toFixed(2)) + ' ' + units[i];
}

function fmtRate(bytesPerSec) {
  if (bytesPerSec == null || isNaN(bytesPerSec)) return '—';
  return fmtBytes(bytesPerSec) + '/s';
}

// ── Sparkline ────────────────────────────────────────────────────────────────
// Builds a tiny inline SVG line+area chart from an array of numbers.
// Returns an <svg> string; renders an empty (placeholder) svg for < 2 points.
// Long series are down-sampled so the path stays small.
function sparklineSvg(values, w = 84, h = 22) {
  const clean = (values || []).filter(x => typeof x === 'number' && isFinite(x));
  if (clean.length < 2) {
    return `<svg class="spark-svg spark-empty" width="${w}" height="${h}" aria-hidden="true"></svg>`;
  }
  // Down-sample to ~100 points max.
  let v = clean;
  if (v.length > 100) {
    const stride = Math.ceil(v.length / 100);
    v = v.filter((_, i) => i % stride === 0);
    if (v[v.length - 1] !== clean[clean.length - 1]) v.push(clean[clean.length - 1]);
  }
  const min = v.reduce((a, b) => Math.min(a, b), Infinity);
  const max = v.reduce((a, b) => Math.max(a, b), -Infinity);
  const range = (max - min) || 1;
  const pad = 1.5;
  const innerH = h - pad * 2;
  const xAt = i => (i / (v.length - 1)) * (w - pad * 2) + pad;
  const yAt = val => pad + innerH - ((val - min) / range) * innerH;
  const pts = v.map((val, i) => `${xAt(i).toFixed(2)},${yAt(val).toFixed(2)}`);
  const line = 'M' + pts.join(' L');
  const x0 = pad.toFixed(2), xN = (w - pad).toFixed(2), yB = (h - pad).toFixed(2);
  const area = `${line} L${xN},${yB} L${x0},${yB} Z`;
  const lx = xAt(v.length - 1).toFixed(2), ly = yAt(v[v.length - 1]).toFixed(2);
  return `<svg class="spark-svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">`
    + `<path class="spark-area" d="${area}"></path>`
    + `<path class="spark-line" d="${line}"></path>`
    + `<circle class="spark-dot" cx="${lx}" cy="${ly}" r="1.6"></circle>`
    + `</svg>`;
}

// ── HTML escaper ─────────────────────────────────────────────────────────────
// Prevents raw user / API data from being interpreted as HTML.
function escHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

// ── Collapsible panel factory ────────────────────────────────────────────────
// Returns a <div class="panel"> element with a clickable header that
// toggles the .collapsed class to show / hide the body.
function collapsible(header, body) {
  const panel = document.createElement('div');
  panel.className = 'panel animate-in';
  panel.innerHTML = `
    <div class="panel-header">${header}</div>
    <div class="panel-body">${body}</div>
  `;
  panel.querySelector('.panel-header').addEventListener('click', () => {
    panel.classList.toggle('collapsed');
  });
  return panel;
}
