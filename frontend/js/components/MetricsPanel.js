// ── MetricsPanel Component ────────────────────────────────────────────────────
//
// Displays a table of Prometheus metric series. Each row shows the metric name,
// latest value, peak value, and the label set (key=value pairs).
//
// Returns null from the static factory method when there are no series,
// so the caller can skip rendering it entirely.
//
// Usage:
//   const panel = MetricsPanel.create(data.metrics);
//   if (panel) document.querySelector('#main').appendChild(panel.element);

class MetricsPanel {
  // metrics: the metrics object from the API response ({ series: [...] })
  constructor(metrics) {
    this.element = this._build(metrics);
  }

  // Factory method — returns null if there is nothing to show.
  static create(metrics) {
    const series = metrics?.series || [];
    if (!series.length) return null;
    return new MetricsPanel(metrics);
  }

  _build(metrics) {
    const series = metrics.series || [];

    const rowsHtml = series.map(s => {
      // Render each label as   key=<cyan-value>
      const labelsHtml = Object.entries(s.labels || {})
        .map(([k, v]) => `${k}=<span style="color:var(--cyan)">${v}</span>`)
        .join(', ');

      const trendValues = (s.samples || []).map(p => p && p.value);

      return `
        <tr data-metric="${escHtml(s.name || '')}">
          <td>${s.name || '—'}</td>
          <td class="spark">${sparklineSvg(trendValues)}</td>
          <td class="num">${fmt(s.latest_value)}</td>
          <td class="num">${fmt(s.peak_value)}</td>
          <td class="labels">${labelsHtml || '—'}</td>
        </tr>
      `;
    }).join('');

    const body = `
      <table class="data-table">
        <thead><tr>
          <th>Metric</th><th>Trend</th><th>Latest</th><th>Peak</th><th>Labels</th>
        </tr></thead>
        <tbody>${rowsHtml}</tbody>
      </table>
    `;

    const header = `
      <span class="panel-title">Metrics</span>
      <span class="panel-count">${series.length} series ▾</span>
    `;

    return collapsible(header, body);
  }
}
