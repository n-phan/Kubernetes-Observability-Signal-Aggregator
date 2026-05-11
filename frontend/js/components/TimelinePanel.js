// ── TimelinePanel Component ──────────────────────────────────────────────────
//
// Displays causal ordering of incident events (metric/log/trace) with
// severity, timestamp, and key details.

class TimelinePanel {
  constructor(timeline) {
    this.element = this._build(timeline);
  }

  static create(timeline) {
    const events = timeline?.events || [];
    if (!events.length) return null;
    return new TimelinePanel(timeline);
  }

  _build(timeline) {
    const events = timeline.events || [];
    const rowsHtml = events.map((evt) => {
      const severityClass = `event-severity-${escHtml(evt.severity || 'info')}`;
      const icon = this._getEventIcon(evt.event_type);
      const details = Object.entries(evt.details || {})
        .map(([key, val]) => `
          <div class="event-detail">
            <span class="detail-key">${escHtml(key)}:</span>
            <span class="detail-value">${escHtml(String(val))}</span>
          </div>
        `).join('');

      return `
        <div class="timeline-event ${severityClass}">
          <div class="event-header">
            <span class="event-icon">${icon}</span>
            <span class="event-type">${escHtml((evt.event_type || '').replace(/_/g, ' ').toUpperCase())}</span>
            <span class="event-time">${escHtml(fmtTime(evt.timestamp))}</span>
          </div>
          <div class="event-summary">${escHtml(evt.summary || '')}</div>
          ${details}
        </div>
      `;
    }).join('');

    const dominantCause = timeline.dominant_cause
      ? `<div class="dominant-cause">Likely root cause: ${escHtml(String(timeline.dominant_cause).toUpperCase())}</div>`
      : '';

    const span = (timeline.total_span_seconds !== null && timeline.total_span_seconds !== undefined)
      ? `<div class="timeline-span">Total incident duration: ${Number(timeline.total_span_seconds).toFixed(1)}s</div>`
      : '';

    const body = `
      <div class="timeline-container">
        ${dominantCause}
        ${span}
        <div class="timeline-events">${rowsHtml}</div>
      </div>
    `;

    const header = `
      <span class="panel-title">Timeline</span>
      <span class="panel-count">${events.length} event(s) ▾</span>
    `;

    return collapsible(header, body);
  }

  _getEventIcon(eventType) {
    const icons = {
      metric_spike: '📊',
      log_burst: '📝',
      trace_error: '⚠️',
      latency_spike: '🐢',
    };
    return icons[eventType] || '•';
  }
}
