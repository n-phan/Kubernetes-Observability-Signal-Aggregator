// ── MetaBar Component ─────────────────────────────────────────────────────────
//
// Displays a summary row at the top of every query result.
// Shows: target service, namespace, time window, and pill badges for
// signal counts (metrics, logs, traces, correlations).
//
// Usage:
//   const bar = new MetaBar(data);
//   document.querySelector('#main').appendChild(bar.element);

class MetaBar {
  // data: the full API response object
  constructor(data) {
    this.element = this._build(data);
  }

  _build(data) {
    const meta        = data.meta         || {};
    const corrs       = data.correlations || [];
    const logCount    = data.logs?.total_lines      ?? 0;
    const traceCount  = data.traces?.traces?.length ?? 0;
    const metricCount = data.metrics?.series?.length ?? 0;
    const rca         = data.rca || {};

    const el = document.createElement('div');
    el.className = 'meta-bar animate-in';
    el.innerHTML = `
      <span class="target">${meta.target || '—'}</span>
      <span class="sep">·</span>
      <span>${meta.namespace || 'default'}</span>
      <span class="sep">·</span>
      <span class="window">${fmtTime(meta.window_start)} → ${fmtTime(meta.window_end)}</span>
      <span class="summary-pill">${metricCount} series</span>
      <span class="summary-pill">${logCount} log lines</span>
      <span class="summary-pill">${traceCount} trace(s)</span>
      <span class="summary-pill">${corrs.length} correlation(s)</span>
      ${rca.performed
        ? `<span class="summary-pill" style="color:var(--accent);border-color:var(--accent-dim)">RCA ✓</span>`
        : ''}
      <span class="duration">${(meta.query_duration_ms ?? 0).toFixed(0)} ms</span>
    `;
    return el;
  }
}
