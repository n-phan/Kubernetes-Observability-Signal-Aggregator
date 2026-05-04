// ── LogsPanel Component ───────────────────────────────────────────────────────
//
// Displays a paginated, filterable list of log lines from Loki.
// Manages its own state: the full line list, active filters, and current page.
// Multi-line entries (e.g. tracebacks) are collapsed by default and can be
// expanded inline.
//
// Returns null from the static factory method when there are no lines,
// so the caller can skip rendering it entirely.
//
// Usage:
//   const panel = LogsPanel.create(data.logs);
//   if (panel) document.querySelector('#main').appendChild(panel.element);
//
// After appending to the DOM, the panel renders its first page automatically.

class LogsPanel {
  // logs: the logs object from the API response ({ lines: [...], total_lines: N })
  constructor(logs) {
    // ── State ───────────────────────────────────────────────────────────────
    this._allLines = logs.lines || [];
    this._total    = logs.total_lines ?? 0;
    this._page     = 0;
    this._filters  = { errorsOnly: false, search: '' };

    // ── Build DOM ────────────────────────────────────────────────────────────
    this.element = this._buildShell();

    // Render the first page once the element exists
    // (even before it is appended to the document)
    setTimeout(() => this._renderPage(), 0);
  }

  // Factory method — returns null if there is nothing to show.
  static create(logs) {
    if (!logs?.lines?.length) return null;
    return new LogsPanel(logs);
  }

  // ── Shell builder ─────────────────────────────────────────────────────────
  // Creates the collapsible panel with filter bar, row container, and pager.
  // The row container starts empty; _renderPage() fills it.

  _buildShell() {
    // We need stable references to interactive elements, so we build with
    // a temp container and then pull real element references after parsing.
    const wrapper = document.createElement('div');
    wrapper.innerHTML = `
      <div class="log-filters">
        <span class="filter-label">Filter</span>
        <button class="filter-toggle" id="log-errors-btn">● Errors only</button>
        <span class="filter-label" style="margin-left:4px">Search</span>
        <input class="filter-input" placeholder="keyword…" style="width:140px" />
      </div>
      <div class="log-container">
        <div id="log-rows-container"></div>
      </div>
      <div class="pagination">
        <button class="page-btn" id="log-prev-btn">◀ Prev</button>
        <span class="page-info">Page</span>
        <input class="filter-input" id="log-page-input" type="number" min="1" value="1"
               style="width:46px;text-align:center;padding:3px 6px" />
        <span class="page-info" id="log-page-total">of 1</span>
        <button class="page-btn" id="log-next-btn">Next ▶</button>
        <span style="width:1px;height:16px;background:var(--border);margin:0 8px;flex-shrink:0"></span>
        <span class="page-info">Line #</span>
        <input class="filter-input" id="log-line-input" type="number" min="1" placeholder="go to line…"
               style="width:110px;text-align:center;padding:3px 6px" />
      </div>
    `;

    // Cache element references so event handlers can reach them without
    // using global IDs (which would break if two panels existed at once).
    this._els = {
      errBtn:    wrapper.querySelector('#log-errors-btn'),
      searchInp: wrapper.querySelector('.log-filters .filter-input'),
      rowsCont:  wrapper.querySelector('#log-rows-container'),
      pageInput: wrapper.querySelector('#log-page-input'),
      pageTotal: wrapper.querySelector('#log-page-total'),
      prevBtn:   wrapper.querySelector('#log-prev-btn'),
      nextBtn:   wrapper.querySelector('#log-next-btn'),
      lineInput: wrapper.querySelector('#log-line-input'),
    };

    // Attach event handlers
    this._els.errBtn.addEventListener('click', () => this._toggleErrorFilter());
    this._els.searchInp.addEventListener('input', e => this._updateSearch(e.target.value));
    this._els.prevBtn.addEventListener('click', () => this._goToPage(this._page - 1));
    this._els.nextBtn.addEventListener('click', () => this._goToPage(this._page + 1));
    this._els.pageInput.addEventListener('change', e => this._goToPage(parseInt(e.target.value) - 1));
    this._els.pageInput.addEventListener('keydown', e => { if (e.key === 'Enter') this._goToPage(parseInt(e.target.value) - 1); });
    this._els.lineInput.addEventListener('change', e => this._jumpToLine(parseInt(e.target.value)));
    this._els.lineInput.addEventListener('keydown', e => { if (e.key === 'Enter') this._jumpToLine(parseInt(e.target.value)); });

    const body = wrapper.innerHTML;
    const headerHtml = `
      <span class="panel-title">Logs</span>
      <span class="panel-count" id="log-header-count">${this._allLines.length} of ${this._total} ▾</span>
    `;

    const panel = collapsible(headerHtml, body);

    // Re-acquire references now that collapsible() has wrapped the content
    this._els.headerCount = panel.querySelector('#log-header-count');
    this._els.errBtn      = panel.querySelector('#log-errors-btn');
    this._els.searchInp   = panel.querySelector('.log-filters .filter-input');
    this._els.rowsCont    = panel.querySelector('#log-rows-container');
    this._els.pageInput   = panel.querySelector('#log-page-input');
    this._els.pageTotal   = panel.querySelector('#log-page-total');
    this._els.prevBtn     = panel.querySelector('#log-prev-btn');
    this._els.nextBtn     = panel.querySelector('#log-next-btn');
    this._els.lineInput   = panel.querySelector('#log-line-input');

    // Re-attach handlers to the final elements
    this._els.errBtn.addEventListener('click', () => this._toggleErrorFilter());
    this._els.searchInp.addEventListener('input', e => this._updateSearch(e.target.value));
    this._els.prevBtn.addEventListener('click', () => this._goToPage(this._page - 1));
    this._els.nextBtn.addEventListener('click', () => this._goToPage(this._page + 1));
    this._els.pageInput.addEventListener('change', e => this._goToPage(parseInt(e.target.value) - 1));
    this._els.pageInput.addEventListener('keydown', e => { if (e.key === 'Enter') this._goToPage(parseInt(e.target.value) - 1); });
    this._els.lineInput.addEventListener('change', e => this._jumpToLine(parseInt(e.target.value)));
    this._els.lineInput.addEventListener('keydown', e => { if (e.key === 'Enter') this._jumpToLine(parseInt(e.target.value)); });

    return panel;
  }

  // ── Filter helpers ────────────────────────────────────────────────────────

  _toggleErrorFilter() {
    this._filters.errorsOnly = !this._filters.errorsOnly;
    this._page = 0;
    this._renderPage();
  }

  _updateSearch(val) {
    this._filters.search = val.trim().toLowerCase();
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

  // Jump to the page containing a specific 1-based global line number.
  // If the target line is hidden by active filters, clears all filters first.
  _jumpToLine(lineNum) {
    if (isNaN(lineNum) || lineNum < 1) return;

    const filtered = this._applyFilters();
    const pos = filtered.findIndex(l => this._allLines.indexOf(l) + 1 === lineNum);

    if (pos === -1) {
      // Target is hidden by current filters — clear them and jump
      this._filters = { errorsOnly: false, search: '' };
      if (this._els.searchInp) this._els.searchInp.value = '';
      this._page = Math.floor((lineNum - 1) / LOG_PAGE_SIZE);
    } else {
      this._page = Math.floor(pos / LOG_PAGE_SIZE);
    }

    this._renderPage();
    if (this._els.lineInput) this._els.lineInput.value = '';
  }

  // ── Filter application ────────────────────────────────────────────────────

  // Returns the subset of _allLines that pass the active filters.
  _applyFilters() {
    const { errorsOnly, search } = this._filters;
    return this._allLines.filter(l => {
      const lvl   = (l.severity || 'unknown').toLowerCase();
      const isErr = severityClass(lvl) === 'error';
      const msg   = (fmtTime(l.timestamp) + ' ' + (l.message || '')).toLowerCase();
      return (!errorsOnly || isErr) && (!search || msg.includes(search));
    });
  }

  // ── Page renderer ─────────────────────────────────────────────────────────
  // Applies filters, slices to the current page, builds row HTML,
  // and updates all pagination controls.

  _renderPage() {
    const filtered   = this._applyFilters();
    const totalPages = Math.max(1, Math.ceil(filtered.length / LOG_PAGE_SIZE));

    // Keep page index in bounds
    this._page = Math.min(this._page, totalPages - 1);

    const pageStart = this._page * LOG_PAGE_SIZE;

    // Attach global index (position in unfiltered _allLines) to each
    // filtered line so line numbers stay stable regardless of active filters.
    const filteredWithIdx = filtered.map(l => ({
      ...l,
      _globalIdx: this._allLines.indexOf(l) + 1,  // 1-based
    }));

    const pageLines = filteredWithIdx.slice(pageStart, pageStart + LOG_PAGE_SIZE);

    const rowsHtml = pageLines.map((l, idx) => {
      const globalIdx = l._globalIdx;
      const lvl       = (l.severity || 'unknown').toLowerCase();
      const isErr     = severityClass(lvl) === 'error';
      const fullMsg   = l.message || '';
      const firstLine = fullMsg.split('\n')[0];
      const multiline = fullMsg.indexOf('\n') !== -1;
      const id        = `${this._page}-${idx}`;

      const expandBtn = multiline
        ? `<span class="log-expand-btn" id="log-btn-${id}" onclick="LogsPanel._toggleEntry('${id}')">▸</span>`
        : `<span style="width:18px;flex-shrink:0"></span>`;

      return `
        <div class="log-row" data-is-error="${isErr}"
             data-msg="${escHtml((fmtTime(l.timestamp) + ' ' + fullMsg).toLowerCase().slice(0, 300))}">
          <span class="log-line-num">#${globalIdx}</span>
          <span class="log-ts">${fmtTime(l.timestamp)}</span>
          <span class="log-level ${lvl}">${(l.severity || 'unknown').toUpperCase()}</span>
          ${expandBtn}
          <span class="log-msg-preview ${isErr ? 'error-msg' : ''}" id="log-preview-${id}"
                style="cursor:${multiline ? 'pointer' : 'default'}"
                onclick="${multiline ? `LogsPanel._toggleEntry('${id}')` : ''}">${escHtml(firstLine)}</span>
          <span class="log-msg-full ${isErr ? 'error-msg' : ''}" id="log-full-${id}"
                style="display:none;cursor:pointer"
                onclick="LogsPanel._toggleEntry('${id}')">${escHtml(fullMsg)}</span>
        </div>
      `;
    }).join('') || `
      <div class="log-row">
        <span class="log-msg-preview" style="color:var(--text-muted)">
          No log lines match the current filters.
        </span>
      </div>
    `;

    // Write rows into the container
    if (this._els.rowsCont) this._els.rowsCont.innerHTML = rowsHtml;

    // Update header count (reflects current filter state)
    if (this._els.headerCount) {
      this._els.headerCount.textContent = `${filtered.length} of ${this._allLines.length} ▾`;
    }

    // Update pagination controls
    const { pageInput, pageTotal, prevBtn, nextBtn, errBtn } = this._els;
    if (pageInput) pageInput.value       = this._page + 1;
    if (pageTotal) pageTotal.textContent = `of ${totalPages}`;
    if (prevBtn)   prevBtn.disabled      = this._page === 0;
    if (nextBtn)   nextBtn.disabled      = this._page >= totalPages - 1;
    if (errBtn)    errBtn.classList.toggle('active', this._filters.errorsOnly);
  }

  // ── Log entry expand / collapse ───────────────────────────────────────────
  // Toggling a multi-line log entry between single-line preview and full text.
  // This is a static method because it's called from inline onclick handlers
  // in the row HTML where we don't have a reference to the instance.

  static _toggleEntry(id) {
    const preview = document.getElementById('log-preview-' + id);
    const full    = document.getElementById('log-full-'    + id);
    const btn     = document.getElementById('log-btn-'     + id);
    if (!preview || !full) return;

    const isExpanded = full.style.display !== 'none';
    preview.style.display = isExpanded ? ''     : 'none';
    full.style.display    = isExpanded ? 'none' : '';
    if (btn) btn.textContent = isExpanded ? '▸' : '▾';
  }
}
