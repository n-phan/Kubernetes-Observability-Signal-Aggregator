// ── Status dot ───────────────────────────────────────────────────────────────
// Updates the small coloured dot in the header to reflect current state.
// States: '' (idle) | 'loading' | 'ok' | 'mock' | 'error'
function setStatus(state) {
  $('status-dot').className = 'status-dot ' + state;
}

// ── Store last query params ───────────────────────────────────────────────────
// Kept so the "Analyze with AI" button can re-use them without re-reading
// the form inputs (which the user may have changed since the last query).
let _lastQuery = null;
let _lastResult = null;
let _rcaFollowupHistory = [];
let _pendingDemoWindow = null;

function setDemoQueryWindow(event) {
  if (!event?.query_target || !event?.window_start || !event?.window_end) return;
  _pendingDemoWindow = {
    target: event.query_target,
    start:  event.window_start,
    end:    event.window_end,
  };

  const targetInput = $('inp-target');
  if (targetInput) targetInput.value = event.query_target;
}

function _buildQueryBody({ target, namespace, lookback, includeRca, range }) {
  const body = {
    target,
    namespace,
    include_rca: includeRca,
  };

  if (range?.start && range?.end) {
    body.start = range.start;
    body.end = range.end;
  } else {
    body.lookback_minutes = lookback;
  }

  return body;
}

function _storeLastQuery({ target, namespace, lookback, endpoint, data, requestBody }) {
  _lastQuery = {
    target,
    namespace,
    lookback,
    endpoint,
    start: data?.meta?.window_start || requestBody.start || null,
    end:   data?.meta?.window_end   || requestBody.end   || null,
  };
}

// ── In-flight guard ───────────────────────────────────────────────────────────
// While a /query or RCA request is pending, block starting another one — a late
// RCA response would otherwise overwrite a freshly-rendered query result.
let _busy = false;
function setBusy(busy) {
  _busy = busy;
  ['btn-query', 'btn-mock', 'btn-analyze'].forEach(id => {
    const el = $(id);
    if (el) el.disabled = busy;
  });
}

// ── Query (without RCA) ───────────────────────────────────────────────────────
// Reads the form, sends a POST to /query with include_rca: false, and renders
// the result. RCA is intentionally excluded to keep the response fast — the
// user can trigger it separately with the Analyze button.
async function runQuery() {
  if (_busy) return;
  const target    = $('inp-target').value.trim();
  const namespace = $('inp-namespace').value.trim() || 'default';
  const lookback  = parseInt($('inp-lookback').value) || 30;
  const endpoint  = $('inp-endpoint').value.trim().replace(/\/$/, '');

  if (!target) { alert('Please enter a target service name.'); return; }

  const demoRange =
    _pendingDemoWindow?.target === target
      ? { start: _pendingDemoWindow.start, end: _pendingDemoWindow.end }
      : null;
  const requestBody = _buildQueryBody({
    target,
    namespace,
    lookback,
    includeRca: false,
    range: demoRange,
  });

  setStatus('loading');
  setBusy(true);
  // Switching to the results view — close any open content panel (Manage / Config
  // LLM / History / …) so it doesn't sit on top of the results, and un-hide #main.
  if (typeof Sidebar !== 'undefined') Sidebar.closeOtherPanels(null);
  const mainEl = $('main');
  mainEl.style.display = '';
  mainEl.innerHTML = `
    <div class="empty-state">
      <span class="glyph" style="animation: pulse 1s infinite; display:block">◎</span>
      <p>Querying ${escHtml(target)} · ${escHtml(namespace)} …</p>
    </div>
  `;

  try {
    const resp = await fetch(`${endpoint}/query`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(requestBody),
    });

    if (!resp.ok) {
      const txt = await resp.text();
      throw new Error(`HTTP ${resp.status}: ${txt}`);
    }

    const data = await resp.json();
    _lastResult = data;
    _rcaFollowupHistory = [];
    _storeLastQuery({ target, namespace, lookback, endpoint, data, requestBody });
    if (demoRange) _pendingDemoWindow = null;

    setStatus('ok');
    renderResult(data, false);

  } catch (err) {
    setStatus('error');
    $('main').innerHTML = `
      <div class="error-msg-box animate-in">
        <strong>Query failed</strong><br/>${escHtml(err.message)}
      </div>
    `;
  } finally {
    setBusy(false);
  }
}

// ── Analyze with AI (RCA) ─────────────────────────────────────────────────────
// Re-runs the last query with include_rca: true. Called from the placeholder
// panel button that appears after a normal query.
async function runAnalyze() {
  if (_busy) return;
  if (!_lastQuery) {
    alert('Run a Query first so the analyzer knows which service and time window to investigate.');
    return;
  }
  const { target, namespace, lookback, endpoint, start, end } = _lastQuery;
  const requestBody = _buildQueryBody({
    target,
    namespace,
    lookback,
    includeRca: true,
    range: start && end ? { start, end } : null,
  });
  // Per-request LLM override from the Config LLM panel (if the user saved one).
  const llmCfg = (window.LlmConfigPanel && LlmConfigPanel.getConfig) ? LlmConfigPanel.getConfig() : null;
  if (llmCfg) {
    requestBody.llm = {
      provider: llmCfg.provider,
      endpoint: llmCfg.endpoint || null,
      model:    llmCfg.model || null,
      api_key:  llmCfg.key || null,
    };
  }

  setStatus('loading');
  setBusy(true);
  // Swap the RCA panel for a loading view while the LLM is thinking.
  const rcaEl = document.getElementById('rca-panel');
  if (rcaEl) rcaEl.replaceWith(RcaPanel.loadingElement());

  try {
    const resp = await fetch(`${endpoint}/query`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(requestBody),
    });

    if (!resp.ok) {
      const txt = await resp.text();
      throw new Error(`HTTP ${resp.status}: ${txt}`);
    }

    const data = await resp.json();
    _lastResult = data;
    _rcaFollowupHistory = [];
    _storeLastQuery({ target, namespace, lookback, endpoint, data, requestBody });
    // The RcaPanel._buildFailed() view shows RCA errors to the user; also log them.
    if (data.rca?.error) console.error('[RCA] analysis failed:', data.rca.error);

    setStatus('ok');
    renderResult(data, true);

  } catch (err) {
    setStatus('error');
    // Replace the loading panel with a failed view (shows the error + Retry).
    const loadingEl = document.getElementById('rca-panel');
    if (loadingEl) {
      loadingEl.replaceWith(new RcaPanel({ rca: { performed: false, error: err.message }, showRca: true }).element);
    }
    alert('RCA failed: ' + err.message);
  } finally {
    setBusy(false);
  }
}

async function runRcaFollowup(questionOverride) {
  const input = $('rca-followup-input');
  const question = (questionOverride || input?.value || '').trim();
  if (!question) return;
  if (!_lastQuery || !_lastResult?.rca?.performed) {
    alert('Run RCA first so the assistant has an incident to discuss.');
    return;
  }

  const endpoint = _lastQuery.endpoint;
  const payload = {
    incident: _lastResult,
    question,
    history: _rcaFollowupHistory
      .slice(-12)
      .map(item => ({ role: item.role, content: item.content })),
  };

  _rcaFollowupHistory.push({ role: 'user', content: question });
  if (input) input.value = '';
  renderResult(_lastResult, true);
  _setFollowupLoading(true);
  const form = $('rca-followup-form');
  const sendBtn = $('rca-followup-send');
  const starterBtns = document.querySelectorAll('.rca-followup-suggestion');
  if (form) form.dataset.loading = 'true';
  if (sendBtn) sendBtn.disabled = true;
  starterBtns.forEach(btn => { btn.disabled = true; });
  setStatus('loading');

  try {
    const resp = await fetch(`${endpoint}/rca/followup`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(payload),
    });

    if (!resp.ok) {
      const txt = await resp.text();
      throw new Error(`HTTP ${resp.status}: ${txt}`);
    }

    const data = await resp.json();
    if (!data.answer) throw new Error(data.error || 'No follow-up answer returned');
    _rcaFollowupHistory.push({
      role: 'assistant',
      content: data.answer,
      provider: data.provider,
      fallback_used: data.fallback_used,
    });
    setStatus('ok');
    renderResult(_lastResult, true);
  } catch (err) {
    _rcaFollowupHistory.push({
      role: 'assistant',
      content: `Follow-up failed: ${err.message}`,
    });
    setStatus('error');
    renderResult(_lastResult, true);
  }
}

function _setFollowupLoading(loading) {
  const status = $('rca-followup-status');
  if (status) status.textContent = loading ? 'thinking…' : '';
}

// ── Mock toggle ───────────────────────────────────────────────────────────────
// Loads MOCK_DATA (from config.js) without hitting the API.
// Toggling off clears all results and resets to the idle state.
function runMock() {
  if (_busy) return;
  const btn      = $('btn-mock');
  const isActive = btn.classList.toggle('active');
  if (typeof Sidebar !== 'undefined') Sidebar.closeOtherPanels(null);

  if (isActive) {
    setStatus('mock');
    _lastResult = MOCK_DATA;
    _rcaFollowupHistory = [];
    renderResult(MOCK_DATA);
  } else {
    const mainEl = $('main');
    mainEl.style.display = '';
    mainEl.innerHTML = '';
    _lastResult = null;
    _rcaFollowupHistory = [];
    setStatus('');
  }
}

// ── Render full result ────────────────────────────────────────────────────────
// Instantiates the component for each panel and appends them to <main>.
// Each component's static factory method returns null when there is no data
// to show, so empty sections are never displayed.
function renderResult(data, showRca = true) {
  const main = $('main');
  main.style.display = '';   // make sure the results area is visible (a content panel may have hidden it)
  main.innerHTML = '';

  // Determine whether error signals are present (used by the RCA placeholder).
  const hasErrors = (data.correlations || [])
    .some(c => c.severity === 'error' || c.severity === 'warn');

  // Build all panels. Each is either a component instance (with .element)
  // or null (no data to show).
  const rcaPanel     = new RcaPanel({ rca: data.rca, showRca, hasErrors, followups: _rcaFollowupHistory });
  const metricsPanel = MetricsPanel.create(data.metrics);
  const logsPanel    = LogsPanel.create(data.logs);
  const tracesPanel  = TracesPanel.create(data.traces);

  const panels = [
    new MetaBar(data),
    HistoryPanel.create(data.history),
    rcaPanel,
    CorrelationsPanel.create(data.correlations),
    metricsPanel,
    logsPanel,
    tracesPanel,
  ].filter(Boolean);  // remove nulls

  panels.forEach((panel, i) => {
    panel.element.style.animationDelay = `${i * 60}ms`;
    main.appendChild(panel.element);
  });

  // Wire RCA evidence items so they jump to the signal row they reference.
  if (typeof EvidenceLinker !== 'undefined') {
    EvidenceLinker.wire(rcaPanel.element, data, { logsPanel, metricsPanel, tracesPanel });
  }
}

// ── Service registration API wrappers ───────────────────────────────────────── ─────────────────────────────────────────

/**
 * Probe a metrics URL. Returns { ok: true } or { ok: false, error: "..." }.
 * Uses the same aggregator endpoint as the rest of the UI.
 */
async function testMetricsEndpoint(url) {
  const endpoint = $('inp-endpoint').value.trim().replace(/\/$/, '');
  const resp = await fetch(`${endpoint}/services/test`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ url }),
  });
  return resp.json();
}

/**
 * Delete a registered service by name.
 * Returns { ok: true, name } or throws on HTTP error.
 */
async function deleteService(name) {
  const endpoint = $('inp-endpoint').value.trim().replace(/\/$/, '');
  const resp = await fetch(`${endpoint}/services/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}

/**
 * Update GitHub metadata for an existing service.
 * payload: { github_repo?, github_branch?, github_path_prefix? }
 * Send "" to clear a field, null/omit to leave unchanged.
 * Returns { ok: true, name, entry } or throws on HTTP error.
 */
async function updateServiceGithub(name, payload) {
  const endpoint = $('inp-endpoint').value.trim().replace(/\/$/, '');
  const resp = await fetch(`${endpoint}/services/${encodeURIComponent(name)}`, {
    method:  'PUT',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(payload),
  });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}

/**
 * Register a new service with the aggregator.
 * payload: { name, metrics_url, github_repo?, github_branch?, github_path_prefix? }
 * Returns { ok: true, name } or throws on HTTP error.
 */
async function registerService(payload) {
  const endpoint = $('inp-endpoint').value.trim().replace(/\/$/, '');
  const resp = await fetch(`${endpoint}/services/register`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(payload),
  });
  if (resp.status === 409) {
    const data = await resp.json();
    throw new Error(data.detail || 'Service already registered');
  }
  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(`HTTP ${resp.status}: ${txt}`);
  }
  return resp.json();
}
