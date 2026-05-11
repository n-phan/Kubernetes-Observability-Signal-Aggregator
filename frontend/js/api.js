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

// ── Query (without RCA) ───────────────────────────────────────────────────────
// Reads the form, sends a POST to /query with include_rca: false, and renders
// the result. RCA is intentionally excluded to keep the response fast — the
// user can trigger it separately with the Analyze button.
async function runQuery() {
  const target    = $('inp-target').value.trim();
  const namespace = $('inp-namespace').value.trim() || 'default';
  const lookback  = parseInt($('inp-lookback').value) || 30;
  const endpoint  = $('inp-endpoint').value.trim().replace(/\/$/, '');

  if (!target) { alert('Please enter a target service name.'); return; }

  _lastQuery = { target, namespace, lookback, endpoint };

  setStatus('loading');
  $('btn-query').disabled = true;
  $('main').innerHTML = `
    <div class="empty-state">
      <span class="glyph" style="animation: pulse 1s infinite; display:block">◎</span>
      <p>Querying ${target} · ${namespace} …</p>
    </div>
  `;

  try {
    const resp = await fetch(`${endpoint}/query`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        target,
        namespace,
        lookback_minutes: lookback,
        include_rca:      false,   // fast path — RCA triggered separately
      }),
    });

    if (!resp.ok) {
      const txt = await resp.text();
      throw new Error(`HTTP ${resp.status}: ${txt}`);
    }

    const data = await resp.json();
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
    $('btn-query').disabled = false;
  }
}

// ── Analyze with AI (RCA) ─────────────────────────────────────────────────────
// Re-runs the last query with include_rca: true. Called from the placeholder
// panel button that appears after a normal query.
async function runAnalyze() {
  if (!_lastQuery) {
    alert('Run a Query first so the analyzer knows which service and time window to investigate.');
    return;
  }
  const { target, namespace, lookback, endpoint } = _lastQuery;

  const btn = $('btn-analyze');
  if (btn) { btn.disabled = true; btn.textContent = '⟳ Analyzing…'; }
  setStatus('loading');

  try {
    const resp = await fetch(`${endpoint}/query`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        target,
        namespace,
        lookback_minutes: lookback,
        include_rca:      true,
      }),
    });

    if (!resp.ok) {
      const txt = await resp.text();
      throw new Error(`HTTP ${resp.status}: ${txt}`);
    }

    const data = await resp.json();

    // Surface RCA errors to the console so they're easy to inspect.
    // The RcaPanel._buildFailed() view handles displaying them to the user.
    if (data.rca?.error) {
      console.error('[RCA] analysis failed:', data.rca.error);
    }

    setStatus('ok');
    renderResult(data, true);

  } catch (err) {
    setStatus('error');
    if (btn) { btn.disabled = false; btn.innerHTML = '⚡ Analyze with AI'; }
    alert('RCA failed: ' + err.message);
  }
}

// ── Mock toggle ───────────────────────────────────────────────────────────────
// Loads MOCK_DATA (from config.js) without hitting the API.
// Toggling off clears all results and resets to the idle state.
function runMock() {
  const btn      = $('btn-mock');
  const isActive = btn.classList.toggle('active');

  if (isActive) {
    setStatus('mock');
    renderResult(MOCK_DATA);
  } else {
    $('main').innerHTML = '';
    setStatus('');
  }
}

// ── Render full result ────────────────────────────────────────────────────────
// Instantiates the component for each panel and appends them to <main>.
// Each component's static factory method returns null when there is no data
// to show, so empty sections are never displayed.
function renderResult(data, showRca = true) {
  const main = $('main');
  main.innerHTML = '';

  // Determine whether error signals are present (used by the RCA placeholder).
  const hasErrors = (data.correlations || [])
    .some(c => c.severity === 'error' || c.severity === 'warn');

  // Build all panels. Each is either a component instance (with .element)
  // or null (no data to show).
  const rcaPanel     = new RcaPanel({ rca: data.rca, showRca, hasErrors });
  const metricsPanel = MetricsPanel.create(data.metrics);
  const logsPanel    = LogsPanel.create(data.logs);
  const tracesPanel  = TracesPanel.create(data.traces);

  const panels = [
    new MetaBar(data),
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
