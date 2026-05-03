// ── Meta bar ─────────────────────────────────────────────────────────────────
// Summary row shown at the top of every result: target, namespace, time window,
// and pill badges for counts of each signal type.
function renderMeta(data) {
  const m           = data.meta         || {};
  const cols        = data.correlations || [];
  const logCount    = data.logs?.total_lines         ?? 0;
  const traceCount  = data.traces?.traces?.length    ?? 0;
  const metricCount = data.metrics?.series?.length   ?? 0;
  const rca         = data.rca || {};

  const el = document.createElement('div');
  el.className = 'meta-bar animate-in';
  el.innerHTML = `
    <span class="target">${m.target || '—'}</span>
    <span class="sep">·</span>
    <span>${m.namespace || 'default'}</span>
    <span class="sep">·</span>
    <span class="window">${fmtTime(m.window_start)} → ${fmtTime(m.window_end)}</span>
    <span class="summary-pill">${metricCount} series</span>
    <span class="summary-pill">${logCount} log lines</span>
    <span class="summary-pill">${traceCount} trace(s)</span>
    <span class="summary-pill">${cols.length} correlation(s)</span>
    ${rca.performed
      ? `<span class="summary-pill" style="color:var(--accent);border-color:var(--accent-dim)">RCA ✓</span>`
      : ''}
    <span class="duration">${(m.query_duration_ms ?? 0).toFixed(0)} ms</span>
  `;
  return el;
}

// ── RCA panel ────────────────────────────────────────────────────────────────
// Full AI analysis panel: summary, root cause, confidence bar, evidence list,
// recommended actions (P1/P2/P3), and GitHub code references.
// Returns null when RCA was not performed (placeholder shown by renderResult).
function renderRCA(rca) {
  if (!rca || !rca.performed) return null;

  const conf  = rca.confidence || 0;
  const pct   = Math.round(conf * 100);
  const color = confidenceColor(conf);

  const actions = (rca.recommended_actions || []).map(a => `
    <div class="action-row p${a.priority}">
      <span class="action-priority">P${a.priority}</span>
      <div class="action-content">
        <div class="action-text">${a.action}</div>
        <div class="action-rationale">${a.rationale}</div>
      </div>
    </div>
  `).join('');

  const evidence = (rca.supporting_evidence || [])
    .map(e => `<li>${e}</li>`)
    .join('');

  const codeRefs = (rca.code_references || []).map(r => `
    <div class="code-ref">
      <span class="code-ref-icon">⌥</span>
      <div class="code-ref-info">
        <div class="code-ref-path">${r.path}${r.line_number ? '#L' + r.line_number : ''}</div>
        <div class="code-ref-relevance">${r.relevance || ''}</div>
      </div>
      ${r.url
        ? `<a class="code-ref-link" href="${r.url}" target="_blank" rel="noopener">↗ GitHub</a>`
        : ''}
    </div>
  `).join('');

  const body = `
    <div class="rca-summary">${rca.summary}</div>
    <div class="rca-root-cause">${rca.root_cause}</div>
    <div class="confidence-row">
      <span class="confidence-label">Confidence</span>
      <div class="confidence-bar">
        <div class="confidence-fill" style="width:${pct}%;background:${color}"></div>
      </div>
      <span class="confidence-pct" style="color:${color}">${pct}%</span>
    </div>
    ${evidence ? `
      <div class="rca-subsection">
        <div class="rca-subsection-title">Supporting evidence</div>
        <ul class="evidence-list">${evidence}</ul>
      </div>` : ''}
    ${actions ? `
      <div class="rca-subsection">
        <div class="rca-subsection-title">Recommended actions</div>
        <div class="actions-list">${actions}</div>
      </div>` : ''}
    ${codeRefs ? `
      <div class="rca-subsection">
        <div class="rca-subsection-title">Code references</div>
        <div class="code-refs">${codeRefs}</div>
      </div>` : ''}
  `;

  const header = `
    <span class="panel-title" style="color:var(--accent)">Root Cause Analysis</span>
    <span style="font-size:10px;color:var(--text-dim);margin-left:8px">${pct}% confidence</span>
    <span class="panel-count">▾</span>
  `;

  return collapsible(header, body);
}

// ── Correlations panel ───────────────────────────────────────────────────────
// Rule-based cross-signal events detected by the aggregator backend.
function renderCorrelations(cols) {
  if (!cols || cols.length === 0) return null;

  const rows = cols.map(c => {
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
    <span class="panel-count">${cols.length} event(s) ▾</span>
  `;
  return collapsible(header, `<div class="corr-list">${rows}</div>`);
}

// ── Metrics panel ────────────────────────────────────────────────────────────
// Table of Prometheus metric series: name, latest value, peak, and label set.
function renderMetrics(metrics) {
  const series = metrics?.series || [];
  if (!series.length) return null;

  const rows = series.map(s => {
    const labels = Object.entries(s.labels || {})
      .map(([k, v]) => `${k}=<span style="color:var(--cyan)">${v}</span>`)
      .join(', ');
    return `
      <tr>
        <td>${s.name || '—'}</td>
        <td class="num">${fmt(s.latest_value)}</td>
        <td class="num">${fmt(s.peak_value)}</td>
        <td class="labels">${labels || '—'}</td>
      </tr>
    `;
  }).join('');

  const body = `
    <table class="data-table">
      <thead><tr>
        <th>Metric</th><th>Latest</th><th>Peak</th><th>Labels</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;

  const header = `
    <span class="panel-title">Metrics</span>
    <span class="panel-count">${series.length} series ▾</span>
  `;
  return collapsible(header, body);
}

// ── Logs panel ───────────────────────────────────────────────────────────────
// Initialises log state and builds the panel shell (filter bar + empty
// container + pagination). Actual rows are rendered by renderLogPage()
// in filters.js after the DOM is attached.
function renderLogs(logs) {
  const lines = logs?.lines || [];
  const total = logs?.total_lines ?? 0;
  if (!lines.length) return null;

  // Reset state (defined in filters.js)
  _allLogLines = lines;
  _logPage     = 0;
  _logFilters  = { errorsOnly: false, search: '' };

  const filterBar = `
    <div class="log-filters">
      <span class="filter-label">Filter</span>
      <button class="filter-toggle" id="log-errors-btn" onclick="toggleLogErrorFilter()">
        ● Errors only
      </button>
      <span class="filter-label" style="margin-left:4px">Search</span>
      <input class="filter-input" placeholder="keyword…" style="width:140px"
        oninput="updateLogSearch(this.value)" />
    </div>
  `;

  const logBody = `<div class="log-container"><div id="log-rows-container"></div></div>`;

  const pager = `
    <div class="pagination">
      <button class="page-btn" id="log-prev-btn" onclick="logPagePrev()">◀ Prev</button>
      <span class="page-info">Page</span>
      <input class="filter-input" id="log-page-input" type="number" min="1" value="1"
        style="width:46px;text-align:center;padding:3px 6px"
        onchange="logJumpToPage(this.value)"
        onkeydown="if(event.key==='Enter') logJumpToPage(this.value)" />
      <span class="page-info" id="log-page-total">of 1</span>
      <button class="page-btn" id="log-next-btn" onclick="logPageNext()">Next ▶</button>
      <span style="width:1px;height:16px;background:var(--border);margin:0 8px;flex-shrink:0"></span>
      <span class="page-info">Line #</span>
      <input class="filter-input" id="log-line-input" type="number" min="1" placeholder="go to line…"
        style="width:110px;text-align:center;padding:3px 6px"
        onkeydown="if(event.key==='Enter') logJumpToLine(this.value)"
        onchange="logJumpToLine(this.value)" />
    </div>
  `;

  const header = `
    <span class="panel-title">Logs</span>
    <span class="panel-count" id="log-header-count">${lines.length} of ${total} ▾</span>
  `;
  const panel = collapsible(header, filterBar + logBody + pager);

  // Render the first page once the panel is in the DOM
  setTimeout(() => renderLogPage(), 0);
  return panel;
}

// ── Log entry expand / collapse ───────────────────────────────────────────────
// Toggles between the single-line preview and the full multiline traceback
// for a given log row identified by its page-local id string.
function toggleLogEntry(id) {
  const preview = document.getElementById('log-preview-' + id);
  const full    = document.getElementById('log-full-'    + id);
  const btn     = document.getElementById('log-btn-'     + id);
  if (!preview || !full) return;
  const isExpanded = full.style.display !== 'none';
  preview.style.display = isExpanded ? ''     : 'none';
  full.style.display    = isExpanded ? 'none' : '';
  if (btn) btn.textContent = isExpanded ? '▸' : '▾';
}

// ── Traces panel ─────────────────────────────────────────────────────────────
// Initialises trace state and builds the panel shell (filter bar + empty
// container + pagination). Actual blocks are rendered by renderTracePage()
// in filters.js after the DOM is attached.
function renderTraces(traces) {
  const list = traces?.traces || [];
  if (!list.length) return null;

  // Reset state (defined in filters.js)
  _allTraceData  = list;
  _tracePage     = 0;
  _traceFilters  = { errorsOnly: false, minDuration: 0 };

  const filterBar = `
    <div class="trace-filters">
      <span class="filter-label">Filter</span>
      <button class="filter-toggle" id="filter-errors-btn" onclick="toggleErrorFilter()">
        ● Errors only
      </button>
      <span class="filter-label" style="margin-left:4px">Min duration</span>
      <input class="filter-input" type="number" placeholder="0 ms" min="0" step="0.1"
        style="width:70px" oninput="updateDurationFilter(this.value)" />
    </div>
  `;

  const tracePager = `
    <div class="pagination">
      <button class="page-btn" id="trace-prev-btn" onclick="tracePagePrev()">◀ Prev</button>
      <span class="page-info">Page</span>
      <input class="filter-input" id="trace-page-input" type="number" min="1" value="1"
        style="width:52px;text-align:center;padding:3px 6px"
        onchange="traceJumpToPage(this.value)"
        onkeydown="if(event.key==='Enter') traceJumpToPage(this.value)" />
      <span class="page-info" id="trace-page-total">of 1</span>
      <button class="page-btn" id="trace-next-btn" onclick="tracePageNext()">Next ▶</button>
    </div>
  `;

  const header = `
    <span class="panel-title">Traces</span>
    <span class="panel-count" id="trace-header-count">${list.length} trace(s) ▾</span>
  `;
  const panel = collapsible(header, filterBar + '<div id="trace-rows-container"></div>' + tracePager);
  panel.id = 'traces-panel';

  // Render the first page once the panel is in the DOM
  setTimeout(() => renderTracePage(), 0);
  return panel;
}

// ── Single trace block ────────────────────────────────────────────────────────
// Builds the HTML for one trace (header + sorted span rows).
// Called by renderTracePage() for each trace on the current page.
function buildTraceBlock(t) {
  // Sort spans into call-tree order (root first, children indented by depth).
  function sortSpans(spans) {
    const parentOf = s =>
      s.references && s.references.length > 0 ? s.references[0].span_id : null;

    const children = {};
    spans.forEach(s => {
      const p = parentOf(s);
      if (p) { children[p] = children[p] || []; children[p].push(s); }
    });

    const result = [];
    function walk(s, depth) {
      result.push({ ...s, _depth: depth });
      (children[s.span_id] || [])
        .sort((a, b) => new Date(a.start_time) - new Date(b.start_time))
        .forEach(c => walk(c, depth + 1));
    }

    spans
      .filter(s => !parentOf(s))
      .sort((a, b) => new Date(a.start_time) - new Date(b.start_time))
      .forEach(s => walk(s, 0));

    return result;
  }

  const spans = sortSpans(t.spans || []).map(s => `
    <div class="span-row">
      <span style="width:${(s._depth || 0) * 16}px;flex-shrink:0;display:inline-block"></span>
      <span class="${s.is_error ? 'span-error-dot' : 'span-ok-dot'}"></span>
      <span class="span-svc">${s.service_name    || '—'}</span>
      <span class="span-op">${s.operation_name  || '—'}</span>
      <span class="span-dur">${(s.duration_ms ?? 0).toFixed(1)} ms</span>
    </div>
  `).join('');

  const errCount = (t.spans || []).filter(s => s.is_error).length;

  return `
    <div class="trace-block collapsed" id="trace-${t.trace_id}"
         data-has-error="${errCount > 0}"
         data-duration="${(t.duration_ms ?? 0).toFixed(1)}">
      <div class="trace-header" onclick="toggleTrace('${t.trace_id}')">
        <span class="trace-id">${(t.trace_id || '').slice(0, 16)}…</span>
        <span class="trace-svc">${t.root_service || '—'}</span>
        ${errCount ? `<span class="severity-badge error">${errCount} error(s)</span>` : ''}
        <span class="trace-dur">${(t.duration_ms ?? 0).toFixed(1)} ms</span>
        <span class="trace-chevron">▾</span>
      </div>
      <div class="trace-spans">${spans}</div>
    </div>
  `;
}

// ── Trace expand / collapse ───────────────────────────────────────────────────
// Toggles the .collapsed class on a trace block to show / hide its spans.
function toggleTrace(traceId) {
  const block = document.getElementById('trace-' + traceId);
  if (block) block.classList.toggle('collapsed');
}
