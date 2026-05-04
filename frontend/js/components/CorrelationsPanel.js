// ── CorrelationsPanel Component ───────────────────────────────────────────────
//
// Displays rule-based cross-signal events detected by the aggregator backend.
// Each correlation has a severity badge, kind label, description, and
// confidence score.
//
// Returns null from the static factory method when there are no correlations,
// so the caller can skip rendering it entirely.
//
// Usage:
//   const panel = CorrelationsPanel.create(data.correlations);
//   if (panel) document.querySelector('#main').appendChild(panel.element);

class CorrelationsPanel {
  // correlations: array of correlation objects from the API response
  constructor(correlations) {
    this.element = this._build(correlations);
  }

  // Factory method — returns null if there is nothing to show.
  static create(correlations) {
    if (!correlations || correlations.length === 0) return null;
    return new CorrelationsPanel(correlations);
  }

  _build(correlations) {
    const rowsHtml = correlations.map(c => {
      const sc = severityClass(c.severity);
      return `
        <div class="corr-row">
          <span class="severity-badge ${sc}">${c.severity || 'unknown'}</span>
          <span class="corr-kind">${c.kind || ''}</span>
          <span class="corr-desc">${c.description || ''}</span>
          <span class="corr-conf">${Math.round((c.confidence || 0) * 100)}%</span>
        </div>
      `;
    }).join('');

    const header = `
      <span class="panel-title">Correlations</span>
      <span class="panel-count">${correlations.length} event(s) ▾</span>
    `;

    return collapsible(header, `<div class="corr-list">${rowsHtml}</div>`);
  }
}
