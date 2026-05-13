/**
 * TimelinePanel — Causal ordering of incident events.
 *
 * Shows which signal type occurred first (metrics spike vs log errors vs trace latency),
 * helping developers understand the causal flow of an incident.
 */

class TimelinePanel {
  static renderTimeline(data) {
    const timeline = data.timeline;
    if (!timeline || !timeline.events || timeline.events.length === 0) {
      return $('div', { class: 'panel-empty' }, 'No timeline events');
    }

    const events = timeline.events.map((evt) => {
      const severityClass = `event-severity-${evt.severity}`;
      const icon = TimelinePanel._getEventIcon(evt.event_type);
      const timestamp = new Date(evt.timestamp).toLocaleTimeString();

      return $('div', { class: `timeline-event ${severityClass}` },
        $('div', { class: 'event-header' },
          $('span', { class: 'event-icon' }, icon),
          $('span', { class: 'event-type' }, evt.event_type.replace(/_/g, ' ').toUpperCase()),
          $('span', { class: 'event-time' }, timestamp),
        ),
        $('div', { class: 'event-summary' }, evt.summary),
        ...Object.entries(evt.details || {}).map(([key, val]) =>
          $('div', { class: 'event-detail' },
            $('span', { class: 'detail-key' }, key + ':'),
            $('span', { class: 'detail-value' }, String(val)),
          ),
        ),
      );
    });

    const dominantCauseInfo = timeline.dominant_cause
      ? $('div', { class: 'dominant-cause' },
          `Likely root cause: ${timeline.dominant_cause.toUpperCase()}`)
      : null;

    const timelineSpan = timeline.total_span_seconds
      ? $('div', { class: 'timeline-span' },
          `Total incident duration: ${(timeline.total_span_seconds).toFixed(1)}s`)
      : null;

    return $('div', { class: 'timeline-container' },
      dominantCauseInfo,
      timelineSpan,
      $('div', { class: 'timeline-events' }, ...events),
    );
  }

  static _getEventIcon(eventType) {
    const icons = {
      metric_spike: '📊',
      log_burst: '📝',
      trace_error: '⚠️',
      latency_spike: '🐢',
    };
    return icons[eventType] || '•';
  }
}

// Register in global panel registry
if (window.PANEL_REGISTRY) {
  window.PANEL_REGISTRY.set('timeline', TimelinePanel.renderTimeline);
}
