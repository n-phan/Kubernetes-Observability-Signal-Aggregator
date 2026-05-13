class TimelinePanel {
  constructor(events = []) {
    this.element = this._build(events);
  }

  static create(events = []) {
    return new TimelinePanel(events || []);
  }

  _build(events) {
    if (!events.length) {
      return collapsible('Incident Timeline', `
        <div class="sb-sub-empty">No timeline events found for this query window.</div>
      `);
    }

    const minOffset = Math.min(...events.map(e => Number(e.offset_seconds || 0)));
    const maxOffset = Math.max(...events.map(e => Number(e.offset_seconds || 0)));
    const span = Math.max(1, maxOffset - minOffset);
    const minMarker = 2;
    const maxMarker = 98;
    const minGapPct = 2.2;

    const rawPositions = events.map(event => {
      const offset = Number(event.offset_seconds || 0);
      const left = ((offset - minOffset) / span) * 100;
      return Math.min(maxMarker, Math.max(minMarker, left));
    });

    const markerPositions = [...rawPositions];

    // Forward pass: ensure each marker stays at least minGapPct after the previous one.
    for (let i = 1; i < markerPositions.length; i += 1) {
      markerPositions[i] = Math.max(markerPositions[i], markerPositions[i - 1] + minGapPct);
    }

    // If spacing pushed the tail off the track, shift the whole set left.
    const overflow = markerPositions.length
      ? Math.max(0, markerPositions[markerPositions.length - 1] - maxMarker)
      : 0;
    if (overflow > 0) {
      for (let i = 0; i < markerPositions.length; i += 1) {
        markerPositions[i] = Math.max(minMarker, markerPositions[i] - overflow);
      }
    }

    // Backward pass: re-apply min spacing after clamping to the left edge.
    for (let i = markerPositions.length - 2; i >= 0; i -= 1) {
      markerPositions[i] = Math.min(markerPositions[i], markerPositions[i + 1] - minGapPct);
    }

    const markers = events.map((event, i) => {
      const offset = Number(event.offset_seconds || 0);
      const markerLeft = markerPositions[i] ?? minMarker;
      const sev = (event.severity || 'info').toLowerCase();
      const cls = sev === 'error' ? 'error' : (sev === 'warn' ? 'warn' : 'info');
      return `
        <button class="timeline-marker ${cls}" style="left:${markerLeft.toFixed(2)}%" data-idx="${i}" title="${escHtml(event.title || '')}">
          <span class="timeline-dot"></span>
          <span class="timeline-label">T+${Math.round(offset)}s</span>
        </button>
      `;
    }).join('');

    const details = events.map((event, i) => `
      <div class="timeline-item" data-timeline-item="${i}">
        <div class="timeline-item-head">
          <span class="timeline-pill ${event.severity || 'info'}">${escHtml((event.source || 'signal').toUpperCase())}</span>
          <span class="timeline-time">${fmtTime(event.timestamp)} · T+${Math.round(Number(event.offset_seconds || 0))}s</span>
        </div>
        <div class="timeline-title">${escHtml(event.title || 'Event')}</div>
        ${event.detail ? `<div class="timeline-detail">${escHtml(event.detail)}</div>` : ''}
      </div>
    `).join('');

    const panel = collapsible('Incident Timeline', `
      <div class="timeline-track-wrap">
        <div class="timeline-track"></div>
        ${markers}
      </div>
      <div class="timeline-list">${details}</div>
    `);

    panel.querySelectorAll('.timeline-marker').forEach(btn => {
      btn.addEventListener('click', (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        const idx = btn.dataset.idx;
        const item = panel.querySelector(`[data-timeline-item="${idx}"]`);
        if (item) {
          item.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
          item.classList.remove('flash-highlight');
          void item.offsetWidth;
          item.classList.add('flash-highlight');
          setTimeout(() => item.classList.remove('flash-highlight'), 2000);
        }
      });
    });

    return panel;
  }
}
