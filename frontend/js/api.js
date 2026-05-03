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
  if (!_lastQuery) return;
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
// Builds and appends all result panels to <main> in a fixed display order.
// Each panel function returns null when there is no data to show, and those
// are filtered out before rendering so empty sections are never displayed.
function renderResult(data, showRca = true) {
  const main = $('main');
  main.innerHTML = '';

  // RCA section: full analysis, placeholder with button, or nothing
  let rcaSection = null;
  if (showRca && data.rca && data.rca.performed) {
    rcaSection = renderRCA(data.rca);
  } else {
    const hasErrors = (data.correlations || [])
      .some(c => c.severity === 'error' || c.severity === 'warn');

    const panel = document.createElement('div');
    panel.className = 'panel animate-in';
    panel.innerHTML = `
      <div class="panel-header">
        <span class="panel-title" style="color:var(--text-muted)">Root Cause Analysis</span>
        <span class="panel-count">not yet performed ▾</span>
      </div>
      <div class="panel-body">
        <div class="rca-placeholder">
          ${hasErrors
            ? 'Error signals detected. Run AI analysis to get root cause, recommended actions, and code references.'
            : 'No error signals detected in this time window. Run analysis anyway to confirm.'}
          <br/>
          <button class="btn-analyze" id="btn-analyze" onclick="runAnalyze()">
            ⚡ Analyze with AI
          </button>
        </div>
      </div>
    `;
    rcaSection = panel;
  }

  const sections = [
    renderMeta(data),
    rcaSection,
    renderCorrelations(data.correlations),
    renderMetrics(data.metrics),
    renderLogs(data.logs),
    renderTraces(data.traces),
  ].filter(Boolean);

  sections.forEach((el, i) => {
    el.style.animationDelay = `${i * 60}ms`;
    main.appendChild(el);
  });
}

// ── Event listeners ───────────────────────────────────────────────────────────
// Allow pressing Enter in the target field as a shortcut for the Query button.
$('inp-target').addEventListener('keydown', e => {
  if (e.key === 'Enter') runQuery();
});
