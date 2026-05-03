// ── Log filter + pagination state ────────────────────────────────────────────
let _logFilters  = { errorsOnly: false, search: '' };
let _logPage     = 0;
let _allLogLines = [];   // full set of lines from the API response

// ── Render current log page ───────────────────────────────────────────────────
// Applies active filters to _allLogLines, slices to the current page,
// builds row HTML, and updates all pagination controls.
function renderLogPage() {
  const { errorsOnly, search } = _logFilters;

  const filtered = _allLogLines.filter(l => {
    const lvl   = (l.severity || 'unknown').toLowerCase();
    const isErr = severityClass(lvl) === 'error';
    const msg   = (fmtTime(l.timestamp) + ' ' + (l.message || '')).toLowerCase();
    return (!errorsOnly || isErr) && (!search || msg.includes(search));
  });

  const totalPages = Math.max(1, Math.ceil(filtered.length / LOG_PAGE_SIZE));
  _logPage = Math.min(_logPage, totalPages - 1);

  const pageStart = _logPage * LOG_PAGE_SIZE;

  // Attach global index (position in the unfiltered _allLogLines) to each
  // filtered line so line numbers stay stable regardless of active filters.
  const filteredWithIdx = filtered.map(l => ({
    ...l,
    _globalIdx: _allLogLines.indexOf(l) + 1,  // 1-based
  }));

  const pageLines = filteredWithIdx.slice(pageStart, pageStart + LOG_PAGE_SIZE);

  const rows = pageLines.map((l, idx) => {
    const globalIdx = l._globalIdx;
    const lvl       = (l.severity || 'unknown').toLowerCase();
    const isErr     = severityClass(lvl) === 'error';
    const fullMsg   = l.message || '';
    const firstLine = fullMsg.split('\n')[0];
    const multiline = fullMsg.indexOf('\n') !== -1;
    const id        = `${_logPage}-${idx}`;

    const expandBtn = multiline
      ? `<span class="log-expand-btn" id="log-btn-${id}" onclick="toggleLogEntry('${id}')">▸</span>`
      : `<span style="width:18px;flex-shrink:0"></span>`;

    return `
      <div class="log-row"
           data-is-error="${isErr}"
           data-msg="${escHtml((fmtTime(l.timestamp) + ' ' + fullMsg).toLowerCase().slice(0, 300))}">
        <span class="log-line-num">#${globalIdx}</span>
        <span class="log-ts">${fmtTime(l.timestamp)}</span>
        <span class="log-level ${lvl}">${(l.severity || 'unknown').toUpperCase()}</span>
        ${expandBtn}
        <span class="log-msg-preview ${isErr ? 'error-msg' : ''}" id="log-preview-${id}"
              style="cursor:${multiline ? 'pointer' : 'default'}"
              onclick="${multiline ? `toggleLogEntry('${id}')` : ''}">${escHtml(firstLine)}</span>
        <span class="log-msg-full ${isErr ? 'error-msg' : ''}" id="log-full-${id}"
              style="display:none;cursor:pointer"
              onclick="toggleLogEntry('${id}')">${escHtml(fullMsg)}</span>
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
  const container = document.getElementById('log-rows-container');
  if (container) container.innerHTML = rows;

  // Update header count (reflects filters)
  const countEl = document.getElementById('log-header-count');
  if (countEl) countEl.textContent = `${filtered.length} of ${_allLogLines.length} ▾`;

  // Update pagination controls
  const pageInput = document.getElementById('log-page-input');
  const pageTotal = document.getElementById('log-page-total');
  const prevBtn   = document.getElementById('log-prev-btn');
  const nextBtn   = document.getElementById('log-next-btn');
  const errBtn    = document.getElementById('log-errors-btn');

  if (pageInput) pageInput.value       = _logPage + 1;
  if (pageTotal) pageTotal.textContent = `of ${totalPages}`;
  if (prevBtn)   prevBtn.disabled      = _logPage === 0;
  if (nextBtn)   nextBtn.disabled      = _logPage >= totalPages - 1;
  if (errBtn)    errBtn.classList.toggle('active', errorsOnly);
}

// ── Log filter helpers ────────────────────────────────────────────────────────
function applyLogFilters()       { _logPage = 0; renderLogPage(); }
function toggleLogErrorFilter()  { _logFilters.errorsOnly = !_logFilters.errorsOnly; applyLogFilters(); }
function updateLogSearch(val)    { _logFilters.search = val.trim().toLowerCase(); applyLogFilters(); }

// ── Log pagination helpers ────────────────────────────────────────────────────
function logPagePrev()     { _logPage = Math.max(0, _logPage - 1); renderLogPage(); }
function logPageNext()     { _logPage++; renderLogPage(); }
function logJumpToPage(val) {
  const p = parseInt(val) - 1;
  if (!isNaN(p)) { _logPage = Math.max(0, p); renderLogPage(); }
}

// Jump to the page containing a specific global line number.
// If the target line is hidden by active filters, clears all filters first.
function logJumpToLine(val) {
  const lineNum = parseInt(val);
  if (isNaN(lineNum) || lineNum < 1) return;

  const { errorsOnly, search } = _logFilters;
  const filtered = _allLogLines.filter(l => {
    const lvl   = (l.severity || 'unknown').toLowerCase();
    const isErr = severityClass(lvl) === 'error';
    const msg   = (fmtTime(l.timestamp) + ' ' + (l.message || '')).toLowerCase();
    return (!errorsOnly || isErr) && (!search || msg.includes(search));
  });

  const pos = filtered.findIndex(l => _allLogLines.indexOf(l) + 1 === lineNum);

  if (pos === -1) {
    // Target line is not visible under current filters — clear them and jump
    _logFilters = { errorsOnly: false, search: '' };
    const errBtn     = document.getElementById('log-errors-btn');
    const searchInput = document.querySelector('.log-filters .filter-input');
    if (errBtn)     errBtn.classList.remove('active');
    if (searchInput) searchInput.value = '';
    _logPage = Math.floor((lineNum - 1) / LOG_PAGE_SIZE);
  } else {
    _logPage = Math.floor(pos / LOG_PAGE_SIZE);
  }

  renderLogPage();

  // Clear the jump input after navigating
  const inp = document.getElementById('log-line-input');
  if (inp) inp.value = '';
}

// ── Trace filter + pagination state ──────────────────────────────────────────
let _traceFilters = { errorsOnly: false, minDuration: 0 };
let _tracePage    = 0;
let _allTraceData = [];   // full set of traces from the API response

// ── Render current trace page ─────────────────────────────────────────────────
// Applies active filters to _allTraceData, slices to the current page,
// and rebuilds the trace-rows-container using buildTraceBlock() from render.js.
function renderTracePage() {
  const { errorsOnly, minDuration } = _traceFilters;

  const filtered = _allTraceData.filter(t => {
    const hasError = (t.spans || []).some(s => s.is_error);
    const dur      = t.duration_ms ?? 0;
    return (!errorsOnly || hasError) && (dur >= minDuration);
  });

  const totalPages = Math.max(1, Math.ceil(filtered.length / TRACE_PAGE_SIZE));
  _tracePage = Math.min(_tracePage, totalPages - 1);

  const pageData  = filtered.slice(_tracePage * TRACE_PAGE_SIZE, (_tracePage + 1) * TRACE_PAGE_SIZE);
  const container = document.getElementById('trace-rows-container');

  if (container) {
    container.innerHTML = pageData.length
      ? pageData.map(buildTraceBlock).join('')
      : '<div style="padding:16px;color:var(--text-muted);font-size:12px">No traces match the current filters.</div>';
  }

  // Update header count (reflects filters)
  const headerCount = document.getElementById('trace-header-count');
  if (headerCount) headerCount.textContent = `${filtered.length} of ${_allTraceData.length} trace(s) ▾`;

  // Update pagination controls
  const pageInput = document.getElementById('trace-page-input');
  const pageTotal = document.getElementById('trace-page-total');
  const prevBtn   = document.getElementById('trace-prev-btn');
  const nextBtn   = document.getElementById('trace-next-btn');
  const errBtn    = document.getElementById('filter-errors-btn');

  if (pageInput) pageInput.value       = _tracePage + 1;
  if (pageTotal) pageTotal.textContent = `of ${totalPages}`;
  if (prevBtn)   prevBtn.disabled      = _tracePage === 0;
  if (nextBtn)   nextBtn.disabled      = _tracePage >= totalPages - 1;
  if (errBtn)    errBtn.classList.toggle('active', errorsOnly);
}

// ── Trace filter helpers ──────────────────────────────────────────────────────
function applyTraceFilters()      { _tracePage = 0; renderTracePage(); }
function toggleErrorFilter()      { _traceFilters.errorsOnly = !_traceFilters.errorsOnly; applyTraceFilters(); }
function updateDurationFilter(val){ _traceFilters.minDuration = parseFloat(val) || 0; applyTraceFilters(); }

// ── Trace pagination helpers ──────────────────────────────────────────────────
function tracePagePrev()     { _tracePage = Math.max(0, _tracePage - 1); renderTracePage(); }
function tracePageNext()     { _tracePage++; renderTracePage(); }
function traceJumpToPage(val) {
  const p = parseInt(val) - 1;
  if (!isNaN(p)) { _tracePage = Math.max(0, p); renderTracePage(); }
}
