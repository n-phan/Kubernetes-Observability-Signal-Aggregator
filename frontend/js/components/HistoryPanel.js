// ── History components ────────────────────────────────────────────────────────
//
// HistoryPanel      — inline "Recurrence" banner shown with a query result:
//                     answers "has this happened before?" for the current
//                     incident signature. (Built per-result by api.js.)
// HistoryListPanel  — a browsable content-area panel (opened from the sidebar
//                     "History" item) listing recent query-history records.
//
// Backend: GET /history?target=&limit= , plus the `history` block on /query.

// ── 1. Recurrence banner (per query result) ────────────────────────────────

class HistoryPanel {
  // history: the `history` object from a /query response, or null/undefined.
  static create(history) {
    if (!history || !history.recurrence) return null;
    return new HistoryPanel(history);
  }

  constructor(history) {
    const rec = history.recurrence || {};
    const count = rec.count || 0;
    const recurring = count > 0;
    const ongoing = !!rec.ongoing;
    // Three states: recurring (prior occurrences exist) > ongoing (folded into
    // the current open occurrence) > new (no history at all). Color and copy
    // diverge so the user doesn't see "new failure mode" when there clearly is
    // a matching open record.
    let state, tone, label, headline;
    const last = fmtDateTime(rec.last_seen);
    const first = fmtDateTime(rec.first_seen);
    const started = fmtDateTime(rec.current_started_at);
    if (recurring) {
      state = 'recurring';
      tone = 'var(--warn)';
      label = `seen ${count}× before`;
      headline = `⚠ A similar incident was recorded <strong>${count}</strong> time${count === 1 ? '' : 's'} before for this service — `
        + `last on <strong>${escHtml(last)}</strong>${count > 1 ? `, first on ${escHtml(first)}` : ''}. `
        + `This looks like a <strong>recurring</strong> issue, not a new failure mode.`;
    } else if (ongoing) {
      state = 'ongoing';
      tone = 'var(--cyan)';
      label = 'same ongoing incident';
      headline = `🔄 This is the <strong>same ongoing incident</strong> as your earlier query — folded into the open occurrence that started <strong>${escHtml(started)}</strong>. `
        + `A genuinely new occurrence will only be logged once activity goes quiet for 10 minutes.`;
    } else {
      state = 'new';
      tone = 'var(--cyan)';
      label = 'first occurrence';
      headline = `🆕 No prior record of this incident signature for this service — this appears to be a <strong>new</strong> failure mode.`;
    }

    const occHtml = (rec.occurrences || []).filter(o => o && typeof o === 'object').map(o => `
      <div class="hist-row">
        <span class="hist-when">${escHtml(fmtDateTime(o.created_at))}</span>
        <span class="hist-conf">${o.rca_confidence != null ? Math.round(o.rca_confidence * 100) + '%' : '—'}</span>
        <span class="hist-sum">${escHtml(o.rca_summary || o.rca_root_cause || '(no AI summary recorded)')}</span>
      </div>
    `).join('');

    const header = `
      <span class="panel-title" style="color:${tone}">Recurrence</span>
      <span class="panel-count">${escHtml(label)} ▾</span>
    `;
    const body = `
      <div class="hist-headline">${headline}</div>
      ${occHtml ? `<div class="hist-sub-title">Recent prior occurrences</div><div class="hist-list">${occHtml}</div>` : ''}
      <div class="hist-sig">signature ${escHtml(history.signature || '?')}</div>
    `;
    this.element = collapsible(header, body);
  }
}

// ── 2. Browsable history panel (sidebar → History) ─────────────────────────

// ISO timestamp → compact relative string ("3m ago", "5h ago", or a date).
function histAgo(iso) {
  if (!iso) return '—';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '—';
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 45)        return 'just now';
  if (s < 3600)      return `${Math.round(s / 60)}m ago`;
  if (s < 86400)     return `${Math.round(s / 3600)}h ago`;
  if (s < 7 * 86400) return `${Math.round(s / 86400)}d ago`;
  return fmtDateTime(iso);
}

// 24 hourly buckets covering [now-24h, now]: shows when occurrences happened
// so you can tell flapping (lots of recent bars) from one-shot (single tall bar
// far left/right). Bars are normalized to the card's own max.
function historyOccurrenceSparkline(rows) {
  const N = 24, W = 96, H = 18, gap = 1;
  const barW = (W - gap * (N - 1)) / N;
  const now = Date.now();
  const buckets = new Array(N).fill(0);
  for (const r of rows) {
    const t = new Date(r.created_at).getTime();
    if (!t) continue;
    const hoursAgo = (now - t) / 3600000;
    if (hoursAgo < 0 || hoursAgo >= N) continue;
    buckets[N - 1 - Math.floor(hoursAgo)] += 1;
  }
  const max = Math.max(1, ...buckets);
  const bars = buckets.map((v, i) => {
    if (!v) return '';
    const h = Math.max(2, Math.round((v / max) * H));
    return `<rect x="${(i * (barW + gap)).toFixed(1)}" y="${H - h}" width="${barW.toFixed(1)}" height="${h}" fill="currentColor"/>`;
  }).join('');
  return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" aria-hidden="true">${bars}</svg>`;
}

const HistoryListPanel = {
  _built: false,

  _build() {
    if (this._built) return;
    const section = document.createElement('section');
    section.id = 'history-section';
    section.innerHTML = `
      <div class="hl-bar">
        <span class="hl-title">QUERY HISTORY</span>
        <select id="hl-target" class="hl-select"><option value="">All services</option></select>
        <button class="hl-refresh" id="hl-refresh" title="Refresh">⟳</button>
        <button class="hl-close" onclick="HistoryListPanel.toggle()" title="Close">✕</button>
      </div>
      <div class="hl-body" id="hl-body"><div class="hl-empty">Loading…</div></div>
    `;
    const main = document.querySelector('main');
    document.body.insertBefore(section, main);
    document.getElementById('hl-refresh').addEventListener('click', () => this.refresh());
    document.getElementById('hl-target').addEventListener('change', () => this.refresh());
    this._built = true;
  },

  isOpen() {
    const s = document.getElementById('history-section');
    return !!(s && s.classList.contains('visible'));
  },

  toggle() {
    this._build();
    const open = document.getElementById('history-section').classList.toggle('visible');
    if (open && typeof Sidebar !== 'undefined') Sidebar.closeOtherPanels('history');
    if (typeof Sidebar !== 'undefined') Sidebar.syncClusterBar();
    if (open) {
      this._syncTargetOptions();
      this.refresh();
    } else {
      this.closeDetail();
    }
  },

  // Mirror the header Target dropdown into the filter (preserving current pick).
  _syncTargetOptions() {
    const src = $('inp-target');
    const dst = document.getElementById('hl-target');
    if (!src || !dst) return;
    const keep = dst.value;
    const names = Array.from(src.options).map(o => o.value).filter(Boolean);
    dst.innerHTML = '<option value="">All services</option>'
      + names.map(n => `<option value="${escHtml(n)}">${escHtml(n)}</option>`).join('');
    dst.value = names.includes(keep) ? keep : '';
  },

  async refresh() {
    const body = document.getElementById('hl-body');
    if (!body) return;
    this.closeDetail();
    body.innerHTML = '<div class="hl-empty">Loading…</div>';
    const endpoint = ($('inp-endpoint').value || '').trim().replace(/\/$/, '');
    const target = document.getElementById('hl-target').value;
    let rows = [];
    try {
      const url = `${endpoint}/history?limit=200` + (target ? `&target=${encodeURIComponent(target)}` : '');
      const resp = await fetch(url);
      if (resp.ok) rows = await resp.json();
    } catch (_) { /* leave empty */ }

    if (!rows.length) {
      body.innerHTML = `<div class="hl-empty">No history yet${target ? ` for ${escHtml(target)}` : ''}. Run a query that produces error signals — it gets recorded automatically.</div>`;
      return;
    }

    body.innerHTML = this._renderDashboard(rows);
    body.querySelectorAll('.hl-card.recurring .hl-card-head').forEach(h =>
      h.addEventListener('click', () => h.closest('.hl-card').classList.toggle('open'))
    );
    body.querySelectorAll('[data-occ]').forEach(el =>
      el.addEventListener('click', e => { e.stopPropagation(); this.showDetail(Number(el.dataset.occ)); })
    );
  },

  // Group the raw rows by incident signature (each row = one occurrence, post
  // dedup) and render a dashboard: headline stats + one card per failure mode.
  _renderDashboard(rows) {
    const when = r => new Date(r.last_seen || r.created_at).getTime() || 0;
    this._rowsById = new Map(rows.map(r => [r.id, r]));

    const groups = new Map();
    for (const r of rows) {
      const sig = r.signature || '?';
      if (!groups.has(sig)) groups.set(sig, []);
      groups.get(sig).push(r);
    }

    const cards = [...groups.values()].map(g => {
      g.sort((a, b) => when(b) - when(a));            // newest occurrence first
      const latest = g[0], oldest = g[g.length - 1];
      const rca = g.find(r => r.rca_performed);
      const kinds = (latest.correlation_kinds || '').split(',').filter(Boolean);
      const errors = latest.error_count ?? 0;
      const rcaRan = !!rca;
      const count = g.length;
      // Severity: P1 recurring + correlated, P2 has real signal, P3 light signal,
      // noise = no correlations + no RCA + few errors + single occurrence (these
      // are basically "I queried this service and saw a few errors" — not a
      // failure mode worth surfacing at the top).
      let severity;
      if (count >= 2 && kinds.length > 0) severity = 'p1';
      else if (kinds.length > 0 || rcaRan || errors >= 20) severity = 'p2';
      else if (count >= 2 || errors >= 5) severity = 'p3';
      else severity = 'noise';
      return {
        sig:       latest.signature || '?',
        target:    latest.target || '—',
        latestId:  latest.id,
        count,
        lastSeen:  latest.last_seen || latest.created_at,
        firstSeen: oldest.created_at,
        errors,
        traces:    latest.error_trace_count ?? 0,
        kinds,
        rcaRan,
        rcaConf:   rca && rca.rca_confidence != null ? rca.rca_confidence : null,
        headline:  (rca && (rca.rca_root_cause || rca.rca_summary)) || latest.rca_root_cause || latest.rca_summary || '',
        rows:      g,
        severity,
      };
    });
    // Sort by severity (P1 → P2 → P3 → noise), tie-broken by recurrence then recency.
    const sevRank = { p1: 0, p2: 1, p3: 2, noise: 3 };
    cards.sort((a, b) =>
      (sevRank[a.severity] - sevRank[b.severity])
      || (b.count - a.count)
      || (new Date(b.lastSeen) - new Date(a.lastSeen))
    );
    const primary   = cards.filter(c => c.severity !== 'noise');
    const lowSignal = cards.filter(c => c.severity === 'noise');

    const services  = new Set(rows.map(r => r.target)).size;
    const highSev   = cards.filter(c => c.severity === 'p1' || c.severity === 'p2').length;
    const stat = (n, label, sub, tone) => `
      <div class="hl-stat${tone ? ' ' + tone : ''}">
        <div class="hl-stat-n">${n}</div>
        <div class="hl-stat-label">${escHtml(label)}</div>
        <div class="hl-stat-sub">${escHtml(sub)}</div>
      </div>`;
    const statsHtml = `<div class="hl-stats">
      ${stat(rows.length, 'Occurrences', 'logged')}
      ${stat(primary.length, 'Failure modes', `${lowSignal.length} low-signal hidden`)}
      ${stat(services, services === 1 ? 'Service' : 'Services', 'affected')}
      ${stat(highSev, 'High severity', 'P1 + P2', highSev ? 'warn' : '')}
    </div>`;

    const renderCard = c => {
      const rec = c.count >= 2;
      const occHtml = c.rows.map(r => `
        <div class="hl-occ" data-occ="${r.id}" title="View full details">
          <span class="hl-occ-when">${escHtml(fmtDateTime(r.created_at))}</span>
          <span class="hl-occ-err">${r.error_count ?? 0}/${r.error_trace_count ?? 0}</span>
          <span class="hl-occ-conf">${r.rca_performed && r.rca_confidence != null ? Math.round(r.rca_confidence * 100) + '%' : '—'}</span>
          <span class="hl-occ-sum">${escHtml(r.rca_summary || (r.rca_performed ? '(no summary recorded)' : 'RCA not run'))}</span>
        </div>`).join('');
      const rcaBit = c.rcaRan
        ? `RCA ${c.rcaConf != null ? Math.round(c.rcaConf * 100) + '%' : 'ran'}`
        : `<span class="hl-muted">RCA not run</span>`;
      const sevLabel = { p1: 'P1', p2: 'P2', p3: 'P3', noise: '—' }[c.severity];
      const spark = historyOccurrenceSparkline(c.rows);
      return `
        <div class="hl-card hl-sev-${c.severity}${rec ? ' recurring' : ''}">
          <div class="hl-card-head">
            ${rec ? '<span class="hl-chev">▸</span>' : '<span class="hl-chev hl-chev-spacer"></span>'}
            <span class="hl-sev-badge hl-sev-${c.severity}">${sevLabel}</span>
            <span class="hl-badge">${rec ? '⚠' : '🆕'}&nbsp;×${c.count}</span>
            <span class="hl-card-target">${escHtml(c.target)}</span>
            <span class="hl-spark" title="Occurrences in the last 24h">${spark}</span>
            <span class="hl-card-when">${escHtml(histAgo(c.lastSeen))}</span>
            <button class="hl-detail" data-occ="${c.latestId}" title="View full details">details ↗</button>
          </div>
          <div class="hl-card-headline">${escHtml(c.headline || '(no root-cause text recorded)')}</div>
          <div class="hl-card-meta">
            ${c.kinds.length ? c.kinds.map(k => `<span class="hl-chip">${escHtml(k)}</span>`).join(' ') : '<span class="hl-muted">no correlations</span>'}
            <span class="hl-dot">·</span><span>${c.errors} log errors · ${c.traces} error traces</span>
            <span class="hl-dot">·</span><span>${rcaBit}</span>
            ${rec ? `<span class="hl-dot">·</span><span>first ${escHtml(histAgo(c.firstSeen))}</span>` : ''}
          </div>
          ${rec ? `<div class="hl-occ-list">${occHtml}</div>` : ''}
          <div class="hl-card-sig">signature ${escHtml(c.sig)}</div>
        </div>`;
    };

    const primaryHtml = primary.map(renderCard).join('');
    const lowSignalHtml = lowSignal.map(renderCard).join('');

    return statsHtml
      + `<div class="hl-cards-title">Failure modes <span class="hl-muted">— severity, then recurring, then recent</span></div>`
      + `<div class="hl-cards">${primaryHtml || '<div class="hl-empty">No high-signal failure modes recorded.</div>'}</div>`
      + (lowSignal.length ? `
          <details class="hl-low-signal">
            <summary>Low-signal queries (${lowSignal.length}) <span class="hl-muted">— single-shot queries with no correlations and no RCA; click to expand</span></summary>
            <div class="hl-cards" style="margin-top:10px">${lowSignalHtml}</div>
          </details>` : '');
  },

  // ── Occurrence detail modal ──────────────────────────────────────────────
  // Click an occurrence row (or a card's "details" link) → a dimmed overlay
  // with a card showing every stored attribute of that one record, including
  // the full (un-truncated) RCA root cause and summary.
  showDetail(id) {
    const r = this._rowsById && this._rowsById.get(id);
    if (!r) return;
    this.closeDetail();

    const conf = r.rca_performed && r.rca_confidence != null ? Math.round(r.rca_confidence * 100) + '%' : null;
    const kinds = (r.correlation_kinds || '').split(',').filter(Boolean);
    const win = (r.window_start || r.window_end)
      ? `${escHtml(fmtDateTime(r.window_start))} &nbsp;→&nbsp; ${escHtml(fmtDateTime(r.window_end))}`
      : '—';

    const row = (key, val, opts = {}) => `
      <div class="hl-d-row${opts.block ? ' hl-d-block' : ''}">
        <div class="hl-d-key">${escHtml(key)}</div>
        <div class="hl-d-val${opts.mono ? ' hl-d-mono' : ''}${opts.muted ? ' hl-muted' : ''}">${opts.html ? val : escHtml(val == null || val === '' ? '—' : String(val))}</div>
      </div>`;

    const chips = kinds.length
      ? kinds.map(k => `<span class="hl-chip">${escHtml(k)}</span>`).join(' ')
      : '<span class="hl-muted">none</span>';

    const rcaRows = r.rca_performed
      ? row('Root cause', r.rca_root_cause || '(none recorded)', { block: true })
        + row('AI summary', r.rca_summary || '(none recorded)', { block: true })
        + row('Confidence', conf || '—')
      : row('RCA', 'not run for this occurrence', { muted: true });

    // Frozen evidence — survives Loki/Prom/Jaeger retention windows. NULL on
    // rows recorded before this column was added.
    let snap = null;
    if (r.signals_snapshot) {
      try { snap = JSON.parse(r.signals_snapshot); } catch (_) { snap = null; }
    }
    const snapshotRows = !snap ? row('Frozen evidence', 'not captured for this occurrence', { muted: true }) : (() => {
      const logsHtml = (snap.logs || []).map(l =>
        `<div class="hl-snap-log hl-sev-${escHtml(String(l.severity || '').toLowerCase())}">
           <span class="hl-snap-ts">${escHtml(fmtDateTime(l.ts))}</span>
           <span class="hl-snap-msg">${escHtml(l.message || '')}</span>
         </div>`).join('') || '<span class="hl-muted">none captured</span>';
      const tracesHtml = (snap.traces || []).map(t =>
        `<div class="hl-snap-trace${t.is_error ? ' hl-snap-trace-err' : ''}">
           <span class="hl-snap-trace-id">${escHtml(t.trace_id || '')}</span>
           <span class="hl-snap-trace-op">${escHtml(t.operation || '—')}</span>
           <span class="hl-snap-trace-dur">${escHtml((t.duration_ms ?? 0) + ' ms')}</span>
           <span class="hl-snap-trace-svc">${escHtml(t.root_service || '—')}</span>
           ${t.error_message ? `<div class="hl-snap-trace-err-msg">${escHtml(t.error_message)}</div>` : ''}
         </div>`).join('') || '<span class="hl-muted">none captured</span>';
      const metricsHtml = (snap.metrics || []).map(m =>
        `<div class="hl-snap-metric">
           <span class="hl-snap-metric-name">${escHtml(m.name || '')}</span>
           <span class="hl-snap-metric-vals">latest=${escHtml(String(m.latest ?? '—'))} · peak=${escHtml(String(m.peak ?? '—'))}</span>
         </div>`).join('') || '<span class="hl-muted">none captured</span>';
      const totals = snap.totals || {};
      const totalsLine = `${totals.log_lines ?? 0} log lines (${totals.error_count ?? 0} error, ${totals.warn_count ?? 0} warn) · ${totals.trace_count ?? 0} traces (${totals.error_trace_count ?? 0} error)`;
      return row('Frozen evidence', `<div class="hl-muted hl-snap-totals">${escHtml(totalsLine)}</div>`, { block: true, html: true })
        + row('Logs (top errors)',  `<div class="hl-snap-logs">${logsHtml}</div>`,        { block: true, html: true })
        + row('Traces',             `<div class="hl-snap-traces">${tracesHtml}</div>`,    { block: true, html: true })
        + row('Metrics',            `<div class="hl-snap-metrics">${metricsHtml}</div>`,  { block: true, html: true });
    })();

    const ov = document.createElement('div');
    ov.className = 'hl-overlay';
    ov.innerHTML = `
      <div class="hl-modal" role="dialog" aria-modal="true">
        <div class="hl-modal-head">
          <span class="hl-modal-title">${escHtml(r.target || '—')}<span class="hl-muted"> &nbsp;·&nbsp; occurrence #${escHtml(String(r.id))}</span></span>
          <button class="hl-modal-close" title="Close (Esc)">✕</button>
        </div>
        <div class="hl-modal-body">
          ${row('Recorded at', fmtDateTime(r.created_at))}
          ${row('Last seen', fmtDateTime(r.last_seen || r.created_at))}
          ${row('Service', r.target, { mono: true })}
          ${row('Namespace', r.namespace, { mono: true })}
          ${row('Incident window', win, { html: true })}
          ${row('Errors / error traces', `${r.error_count ?? 0} / ${r.error_trace_count ?? 0}`)}
          ${row('Correlations', chips, { html: true })}
          ${rcaRows}
          ${snapshotRows}
          ${row('Signature', r.signature || '?', { mono: true, muted: true })}
        </div>
      </div>`;
    document.body.appendChild(ov);

    const close = () => this.closeDetail();
    ov.addEventListener('click', e => { if (e.target === ov) close(); });
    ov.querySelector('.hl-modal-close').addEventListener('click', close);
    this._escHandler = e => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', this._escHandler);
    this._overlay = ov;
  },

  closeDetail() {
    if (this._overlay) { this._overlay.remove(); this._overlay = null; }
    if (this._escHandler) { document.removeEventListener('keydown', this._escHandler); this._escHandler = null; }
  },
};

// Expose on window so the Sidebar can reach it — top-level `const`s aren't
// added to the global object.
window.HistoryListPanel = HistoryListPanel;
