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

// ── ISO timestamp → "HH:MM:SS UTC" ──────────────────────────────────────────
function fmtTime(iso) {
  if (!iso) return '—';
  try { return new Date(iso).toISOString().slice(11, 19) + ' UTC'; }
  catch { return iso; }
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
