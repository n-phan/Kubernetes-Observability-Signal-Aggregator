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
    const tone = recurring ? 'var(--warn)' : 'var(--cyan)';

    const last = fmtDateTime(rec.last_seen);
    const first = fmtDateTime(rec.first_seen);

    const headline = recurring
      ? `⚠ A similar incident was recorded <strong>${count}</strong> time${count === 1 ? '' : 's'} before for this service — `
        + `last on <strong>${escHtml(last)}</strong>${count > 1 ? `, first on ${escHtml(first)}` : ''}. `
        + `This looks like a <strong>recurring</strong> issue, not a new failure mode.`
      : `🆕 No prior record of this incident signature for this service — this appears to be a <strong>new</strong> failure mode.`;

    const occHtml = (rec.occurrences || []).map(o => `
      <div class="hist-row">
        <span class="hist-when">${escHtml(fmtDateTime(o.created_at))}</span>
        <span class="hist-conf">${o.rca_confidence != null ? Math.round(o.rca_confidence * 100) + '%' : '—'}</span>
        <span class="hist-sum">${escHtml(o.rca_summary || '(no AI summary recorded)')}</span>
      </div>
    `).join('');

    const header = `
      <span class="panel-title" style="color:${tone}">Recurrence</span>
      <span class="panel-count">${recurring ? `seen ${count}× before` : 'first occurrence'} ▾</span>
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
    const sec = document.getElementById('history-section');
    const open = sec.classList.toggle('visible');
    const cb = document.getElementById('cluster-bar');
    if (cb) cb.style.display = open ? 'none' : '';
    if (open) {
      // Close the other content-area panels.
      const sp = document.getElementById('sp-section');
      if (sp && sp.classList.contains('visible') && window.ServicePanel) ServicePanel.toggle();
      const demo = document.getElementById('demo-section');
      if (demo && demo.classList.contains('visible') && window.DemoPanel) DemoPanel.toggle();
      if (window.LlmConfigPanel && LlmConfigPanel.isOpen()) LlmConfigPanel.toggle();
      this._syncTargetOptions();
      this.refresh();
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
    body.innerHTML = '<div class="hl-empty">Loading…</div>';
    const endpoint = ($('inp-endpoint').value || '').trim().replace(/\/$/, '');
    const target = document.getElementById('hl-target').value;
    let rows = [];
    try {
      const url = `${endpoint}/history?limit=100` + (target ? `&target=${encodeURIComponent(target)}` : '');
      const resp = await fetch(url);
      if (resp.ok) rows = await resp.json();
    } catch (_) { /* leave empty */ }

    if (!rows.length) {
      body.innerHTML = `<div class="hl-empty">No history yet${target ? ` for ${escHtml(target)}` : ''}. Run a query that produces error signals — it gets recorded automatically.</div>`;
      return;
    }
    const rowsHtml = rows.map(r => {
      const kinds = (r.correlation_kinds || '').split(',').filter(Boolean);
      const kindsHtml = kinds.length ? kinds.map(k => `<span class="hl-chip">${escHtml(k)}</span>`).join(' ') : '—';
      const rca = r.rca_performed
        ? `<span class="hl-conf">${r.rca_confidence != null ? Math.round(r.rca_confidence * 100) + '%' : '—'}</span> ${escHtml(r.rca_summary || '')}`
        : '<span class="hl-muted">not run</span>';
      return `
        <tr>
          <td class="hl-when">${escHtml(fmtDateTime(r.created_at))}</td>
          <td>${escHtml(r.target || '—')}</td>
          <td class="num">${r.error_count ?? 0} / ${r.error_trace_count ?? 0}</td>
          <td>${kindsHtml}</td>
          <td class="hl-rca">${rca}</td>
        </tr>`;
    }).join('');
    body.innerHTML = `
      <table class="data-table hl-table">
        <thead><tr>
          <th>When</th><th>Target</th><th>Errors&nbsp;(log/trace)</th><th>Correlations</th><th>RCA</th>
        </tr></thead>
        <tbody>${rowsHtml}</tbody>
      </table>`;
  },
};

// Expose on window so the Sidebar can reach it — top-level `const`s aren't
// added to the global object.
window.HistoryListPanel = HistoryListPanel;
