// ── TracesPanel Component ─────────────────────────────────────────────────────
//
// Displays a paginated, filterable list of Jaeger traces from the API response.
// Each trace can be expanded to show its individual spans in call-tree order.
// Manages its own state: the full trace list, active filters, and current page.
//
// Returns null from the static factory method when there are no traces,
// so the caller can skip rendering it entirely.
//
// Usage:
//   const panel = TracesPanel.create(data.traces);
//   if (panel) document.querySelector('#main').appendChild(panel.element);
//
// After appending to the DOM, the panel renders its first page automatically.

class TracesPanel {
  // traces: the traces object from the API response ({ traces: [...] })
  constructor(traces) {
    // ── State ───────────────────────────────────────────────────────────────
    this._allTraces = traces.traces || [];
    this._page      = 0;
    this._filters   = { errorsOnly: false, minDuration: 0 };

    // ── Build DOM ────────────────────────────────────────────────────────────
    this.element = this._buildShell();

    // Render the first page once the element exists
    setTimeout(() => this._renderPage(), 0);
  }

  // Factory method — returns null if there is nothing to show.
  static create(traces) {
    if (!traces?.traces?.length) return null;
    return new TracesPanel(traces);
  }

  // ── Shell builder ─────────────────────────────────────────────────────────

  _buildShell() {
    const header = `
      <span class="panel-title">Traces</span>
      <span class="panel-count" id="trace-header-count">${this._allTraces.length} trace(s) ▾</span>
    `;

    const body = `
      <div class="trace-filters">
        <span class="filter-label">Filter</span>
        <button class="filter-toggle" id="trace-errors-btn">● Errors only</button>
        <span class="filter-label" style="margin-left:4px">Min duration</span>
        <input class="filter-input" type="number" placeholder="0 ms" min="0" step="0.1"
               style="width:70px" id="trace-dur-input" />
      </div>
      <div id="trace-rows-container"></div>
      <div class="pagination">
        <button class="page-btn" id="trace-prev-btn">◀ Prev</button>
        <span class="page-info">Page</span>
        <input class="filter-input" id="trace-page-input" type="number" min="1" value="1"
               style="width:52px;text-align:center;padding:3px 6px" />
        <span class="page-info" id="trace-page-total">of 1</span>
        <button class="page-btn" id="trace-next-btn">Next ▶</button>
      </div>
    `;

    const panel = collapsible(header, body);
    panel.id = 'traces-panel';

    // Cache element references
    this._els = {
      headerCount: panel.querySelector('#trace-header-count'),
      errBtn:      panel.querySelector('#trace-errors-btn'),
      durInput:    panel.querySelector('#trace-dur-input'),
      rowsCont:    panel.querySelector('#trace-rows-container'),
      pageInput:   panel.querySelector('#trace-page-input'),
      pageTotal:   panel.querySelector('#trace-page-total'),
      prevBtn:     panel.querySelector('#trace-prev-btn'),
      nextBtn:     panel.querySelector('#trace-next-btn'),
    };

    // Attach event handlers
    this._els.errBtn.addEventListener('click', () => this._toggleErrorFilter());
    this._els.durInput.addEventListener('input', e => this._updateDurationFilter(e.target.value));
    this._els.prevBtn.addEventListener('click', () => this._goToPage(this._page - 1));
    this._els.nextBtn.addEventListener('click', () => this._goToPage(this._page + 1));
    this._els.pageInput.addEventListener('change', e => this._goToPage(parseInt(e.target.value) - 1));
    this._els.pageInput.addEventListener('keydown', e => { if (e.key === 'Enter') this._goToPage(parseInt(e.target.value) - 1); });

    return panel;
  }

  // ── Filter helpers ────────────────────────────────────────────────────────

  _toggleErrorFilter() {
    this._filters.errorsOnly = !this._filters.errorsOnly;
    this._page = 0;
    this._renderPage();
  }

  _updateDurationFilter(val) {
    this._filters.minDuration = parseFloat(val) || 0;
    this._page = 0;
    this._renderPage();
  }

  // ── Pagination helpers ────────────────────────────────────────────────────

  _goToPage(pageIndex) {
    if (!isNaN(pageIndex)) {
      this._page = Math.max(0, pageIndex);
      this._renderPage();
    }
  }

  // ── Filter application ────────────────────────────────────────────────────

  _applyFilters() {
    const { errorsOnly, minDuration } = this._filters;
    return this._allTraces.filter(t => {
      const hasError = (t.spans || []).some(s => s.is_error);
      const dur      = t.duration_ms ?? 0;
      return (!errorsOnly || hasError) && (dur >= minDuration);
    });
  }

  // ── Page renderer ─────────────────────────────────────────────────────────

  _renderPage() {
    const filtered   = this._applyFilters();
    const totalPages = Math.max(1, Math.ceil(filtered.length / TRACE_PAGE_SIZE));

    this._page = Math.min(this._page, totalPages - 1);

    const pageData = filtered.slice(
      this._page * TRACE_PAGE_SIZE,
      (this._page + 1) * TRACE_PAGE_SIZE,
    );

    if (this._els.rowsCont) {
      this._els.rowsCont.innerHTML = pageData.length
        ? pageData.map(t => this._buildTraceBlock(t)).join('')
        : '<div style="padding:16px;color:var(--text-muted);font-size:12px">No traces match the current filters.</div>';
    }

    // Update header count
    if (this._els.headerCount) {
      this._els.headerCount.textContent = `${filtered.length} of ${this._allTraces.length} trace(s) ▾`;
    }

    // Update pagination controls
    const { pageInput, pageTotal, prevBtn, nextBtn, errBtn } = this._els;
    if (pageInput) pageInput.value       = this._page + 1;
    if (pageTotal) pageTotal.textContent = `of ${totalPages}`;
    if (prevBtn)   prevBtn.disabled      = this._page === 0;
    if (nextBtn)   nextBtn.disabled      = this._page >= totalPages - 1;
    if (errBtn)    errBtn.classList.toggle('active', this._filters.errorsOnly);
  }

  // ── Trace block builder ───────────────────────────────────────────────────
  // Builds HTML for a single trace: a collapsible header row + sorted spans.

  _buildTraceBlock(trace) {
    const sortedSpans = this._sortSpans(trace.spans || []);

    const spansHtml = sortedSpans.map(s => `
      <div class="span-row">
        <span style="width:${(s._depth || 0) * 16}px;flex-shrink:0;display:inline-block"></span>
        <span class="${s.is_error ? 'span-error-dot' : 'span-ok-dot'}"></span>
        <span class="span-svc">${s.service_name   || '—'}</span>
        <span class="span-op">${s.operation_name  || '—'}</span>
        <span class="span-dur">${(s.duration_ms ?? 0).toFixed(1)} ms</span>
      </div>
    `).join('');

    const errorCount = (trace.spans || []).filter(s => s.is_error).length;
    const traceId    = trace.trace_id || '';

    return `
      <div class="trace-block collapsed" id="trace-${traceId}"
           data-has-error="${errorCount > 0}"
           data-duration="${(trace.duration_ms ?? 0).toFixed(1)}">
        <div class="trace-header" onclick="TracesPanel._toggleTrace('${traceId}')">
          <span class="trace-id">${traceId.slice(0, 16)}…</span>
          <span class="trace-svc">${trace.root_service || '—'}</span>
          ${errorCount ? `<span class="severity-badge error">${errorCount} error(s)</span>` : ''}
          <span class="trace-dur">${(trace.duration_ms ?? 0).toFixed(1)} ms</span>
          <span class="trace-chevron">▾</span>
        </div>
        <div class="trace-spans">${spansHtml}</div>
      </div>
    `;
  }

  // ── Span sorter ───────────────────────────────────────────────────────────
  // Sorts spans into call-tree order (root first, children indented by depth).
  // The _depth property is added to each span for indentation in the UI.

  _sortSpans(spans) {
    // Build a map of spanId → [childSpan, …]
    const getParentId = s =>
      s.references?.length > 0 ? s.references[0].span_id : null;

    const childrenOf = {};
    spans.forEach(s => {
      const parentId = getParentId(s);
      if (parentId) {
        childrenOf[parentId] = childrenOf[parentId] || [];
        childrenOf[parentId].push(s);
      }
    });

    const result = [];

    function walkSpan(span, depth) {
      result.push({ ...span, _depth: depth });
      (childrenOf[span.span_id] || [])
        .sort((a, b) => new Date(a.start_time) - new Date(b.start_time))
        .forEach(child => walkSpan(child, depth + 1));
    }

    // Walk from root spans (those with no parent)
    spans
      .filter(s => !getParentId(s))
      .sort((a, b) => new Date(a.start_time) - new Date(b.start_time))
      .forEach(root => walkSpan(root, 0));

    return result;
  }

  // ── Trace expand / collapse ───────────────────────────────────────────────
  // Static because it is called from inline onclick handlers in the trace HTML.

  static _toggleTrace(traceId) {
    const block = document.getElementById('trace-' + traceId);
    if (block) block.classList.toggle('collapsed');
  }
}
