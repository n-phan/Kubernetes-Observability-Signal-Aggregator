// ── RegisterPanel Component ───────────────────────────────────────────────────
//
// A four-step wizard for registering a new service with the aggregator.
//
// Step 1 — Prerequisites checklist (informational, no inputs).
// Step 2 — Enter metrics URL and test connectivity.
// Step 3 — Confirm service name and optional GitHub metadata.
// Step 4 — Review summary and submit registration.
//
// Usage (called once from index.html after all scripts are loaded):
//   RegisterPanel.init();
//   RegisterPanel.toggle();   // wired to the ⊕ Register button in the header

(function () {

  // ── Component styles ──────────────────────────────────────────────────────
  const STYLES = `
    #reg-section {
      display: none;
      padding: 0 24px 12px;
      background: var(--bg);
    }
    #reg-section.visible { display: block; }

    .reg-wizard {
      border: 1px solid var(--border);
      border-radius: 6px;
      overflow: hidden;
      background: var(--bg-panel);
    }

    .reg-wizard-header {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 16px;
      border-bottom: 1px solid var(--border);
      background: var(--bg-raised);
    }
    .reg-wizard-title {
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--accent);
    }
    .reg-wizard-close {
      margin-left: auto;
      background: none;
      border: none;
      color: var(--text-dim);
      cursor: pointer;
      font-size: 16px;
      line-height: 1;
      padding: 0 2px;
    }
    .reg-wizard-close:hover { color: var(--text); }

    .reg-steps { padding: 16px; display: flex; flex-direction: column; gap: 10px; }

    /* Individual step card */
    .reg-step {
      border: 1px solid var(--border);
      border-radius: 4px;
      overflow: hidden;
    }
    .reg-step-header {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 9px 14px;
      background: var(--bg-raised);
      font-size: 11px;
      letter-spacing: 0.08em;
    }
    .reg-step-num {
      width: 18px; height: 18px;
      border-radius: 50%;
      border: 1px solid var(--border-lit);
      display: flex; align-items: center; justify-content: center;
      font-size: 10px;
      color: var(--text-dim);
      flex-shrink: 0;
    }
    .reg-step.active   .reg-step-num { border-color: var(--accent); color: var(--accent); }
    .reg-step.done     .reg-step-num { border-color: var(--accent); background: var(--accent); color: #070b12; }
    .reg-step.locked   .reg-step-num { opacity: 0.4; }
    .reg-step-label { font-weight: 600; color: var(--text-dim); }
    .reg-step.active  .reg-step-label { color: var(--text); }
    .reg-step.locked  .reg-step-label { opacity: 0.4; }
    .reg-step-summary { margin-left: auto; font-size: 10px; color: var(--text-muted); font-family: var(--mono); }

    .reg-step-body { padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; }
    .reg-step.done   .reg-step-body { display: none; }
    .reg-step.locked .reg-step-body { display: none; }

    /* Form rows inside steps */
    .reg-field { display: flex; flex-direction: column; gap: 4px; }
    .reg-field label {
      font-size: 9px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--text-dim);
    }
    .reg-field input {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 4px;
      color: var(--text);
      font-family: var(--mono);
      font-size: 13px;
      padding: 7px 10px;
      outline: none;
      transition: border-color 0.2s;
      width: 100%;
      max-width: 480px;
    }
    .reg-field input:focus { border-color: var(--accent); }
    .reg-field .hint {
      font-size: 10px;
      color: var(--text-muted);
    }

    .reg-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }

    .reg-btn {
      background: transparent;
      border: 1px solid var(--border-lit);
      border-radius: 4px;
      color: var(--text-dim);
      cursor: pointer;
      font-family: var(--mono);
      font-size: 12px;
      letter-spacing: 0.06em;
      padding: 7px 14px;
      transition: border-color 0.15s, color 0.15s;
      white-space: nowrap;
    }
    .reg-btn:hover:not(:disabled)  { border-color: var(--cyan); color: var(--cyan); }
    .reg-btn.primary { border-color: var(--accent-dim); color: var(--accent); }
    .reg-btn.primary:hover:not(:disabled) { background: var(--accent-dim); border-color: var(--accent); }
    .reg-btn:disabled { opacity: 0.35; cursor: not-allowed; }

    .reg-inline-msg {
      font-size: 12px;
      padding: 6px 10px;
      border-radius: 4px;
      display: none;
    }
    .reg-inline-msg.ok    { display: block; color: var(--accent); background: var(--accent-dim); }
    .reg-inline-msg.error { display: block; color: var(--error);  background: var(--error-dim);  }

    .reg-summary-table {
      font-size: 12px;
      border-collapse: collapse;
      width: 100%;
      max-width: 480px;
    }
    .reg-summary-table td { padding: 4px 8px 4px 0; vertical-align: top; }
    .reg-summary-table td:first-child { color: var(--text-dim); white-space: nowrap; width: 120px; }
    .reg-summary-table td:last-child  { color: var(--text); font-family: var(--mono); word-break: break-all; }

    /* Checklist — step 1 prerequisites */
    .reg-checklist { display: flex; flex-direction: column; gap: 14px; }
    .reg-checklist-group { display: flex; flex-direction: column; gap: 6px; }
    .reg-checklist-group-label {
      font-size: 9px;
      font-weight: 600;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      padding-bottom: 4px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 2px;
    }
    .reg-checklist-group-label.required { color: var(--accent); }
    .reg-checklist-group-label.auto     { color: var(--text-dim); }
    .reg-checklist-group-label.optional { color: var(--cyan); }
    .reg-checklist-item {
      display: flex;
      gap: 8px;
      font-size: 12px;
      color: var(--text);
      line-height: 1.5;
      align-items: flex-start;
    }
    .reg-check {
      color: var(--text-dim);
      flex-shrink: 0;
      margin-top: 1px;
      font-size: 11px;
    }
    .reg-code-snippet {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 3px;
      font-family: var(--mono);
      font-size: 11px;
      padding: 6px 8px;
      margin: 4px 0;
      color: var(--text-muted);
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-word;
    }
  `;

  // ── State ─────────────────────────────────────────────────────────────────
  let _step = 1;
  let _url        = '';
  let _testPassed = false;
  let _name       = '';
  let _githubRepo = '';
  let _githubBranch = '';
  let _githubPathPrefix = '';

  // ── DOM helpers ───────────────────────────────────────────────────────────
  function _r(id) { return document.getElementById('reg-' + id); }

  // ── Step rendering ────────────────────────────────────────────────────────

  function _stateClass(n) {
    if (n < _step)  return 'done';
    if (n === _step) return 'active';
    return 'locked';
  }

  function _refreshStepHeaders() {
    [1, 2, 3, 4].forEach(n => {
      const el = _r('step' + n);
      el.className = 'reg-step ' + _stateClass(n);
    });
    // Step 1 (checklist) has no summary
    _r('summary2').textContent = _step > 2 ? '✓ ' + _url : '';
    _r('summary3').textContent = _step > 3 ? '✓ ' + _name : '';
  }

  // ── Step 1 logic (checklist) ──────────────────────────────────────────────

  function _goStep2() {
    _step = 2;
    _refreshStepHeaders();
  }

  // ── Step 2 logic (metrics endpoint) ──────────────────────────────────────

  async function _runTest() {
    const url = _r('url').value.trim();
    if (!url) return;
    _url = url;
    _testPassed = false;
    _r('test-btn').disabled = true;
    _r('test-btn').textContent = '⟳ Testing…';
    _r('test-msg').className = 'reg-inline-msg';
    _r('next1').disabled = true;

    try {
      const result = await testMetricsEndpoint(url);
      if (result.ok) {
        _testPassed = true;
        _r('test-msg').className = 'reg-inline-msg ok';
        _r('test-msg').textContent = '✓ Endpoint reachable and responding';
        _r('next1').disabled = false;
        // Pre-fill name from hostname
        try {
          const host = new URL(url).hostname;
          if (!_r('name').value) _r('name').value = host;
        } catch (_) {}
      } else {
        _r('test-msg').className = 'reg-inline-msg error';
        _r('test-msg').textContent = '✗ ' + (result.error || 'Unreachable');
      }
    } catch (err) {
      _r('test-msg').className = 'reg-inline-msg error';
      _r('test-msg').textContent = '✗ ' + err.message;
    } finally {
      _r('test-btn').disabled = false;
      _r('test-btn').textContent = 'Test connection';
    }
  }

  function _goStep3() {
    if (!_testPassed) return;
    _url = _r('url').value.trim();
    _step = 3;
    _refreshStepHeaders();
    // Pre-fill name from hostname if still empty
    if (!_r('name').value) {
      try { _r('name').value = new URL(_url).hostname; } catch (_) {}
    }
  }

  // ── Step 3 logic (service details) ───────────────────────────────────────

  function _goStep4() {
    _name             = _r('name').value.trim();
    _githubRepo       = _r('github-repo').value.trim();
    _githubBranch     = _r('github-branch').value.trim();
    _githubPathPrefix = _r('github-prefix').value.trim();

    if (!_name) { _r('name').focus(); return; }

    // Populate confirm table
    const rows = [
      ['Service name', _name],
      ['Metrics URL',  _url],
      ...(_githubRepo ? [
        ['GitHub repo',   _githubRepo],
        ['Branch',        _githubBranch || '(default)'],
        ['Path prefix',   _githubPathPrefix || '(none)'],
      ] : []),
    ];
    _r('confirm-table').innerHTML = rows
      .map(([k, v]) => `<tr><td>${escHtml(k)}</td><td>${escHtml(v)}</td></tr>`)
      .join('');

    _r('register-msg').className = 'reg-inline-msg';
    _step = 4;
    _refreshStepHeaders();
  }

  function _backToStep(n) {
    _step = n;
    _refreshStepHeaders();
  }

  // ── Step 4 logic (confirm) ────────────────────────────────────────────────

  async function _register() {
    const btn = _r('register-btn');
    btn.disabled = true;
    btn.textContent = '⟳ Registering…';
    _r('register-msg').className = 'reg-inline-msg';

    const payload = {
      name:        _name,
      metrics_url: _url,
    };
    if (_githubRepo)       payload.github_repo         = _githubRepo;
    if (_githubBranch)     payload.github_branch       = _githubBranch;
    if (_githubPathPrefix) payload.github_path_prefix  = _githubPathPrefix;

    try {
      await registerService(payload);
      _r('register-msg').className = 'reg-inline-msg ok';
      _r('register-msg').textContent = `✓ "${_name}" registered. Prometheus will scrape it within 15 s.`;
      // Refresh dropdown so the new service appears immediately
      if (typeof loadServices === 'function') loadServices();
      // Collapse wizard after short delay so user sees confirmation
      setTimeout(() => toggle(), 2200);
    } catch (err) {
      _r('register-msg').className = 'reg-inline-msg error';
      _r('register-msg').textContent = '✗ ' + err.message;
      btn.disabled = false;
      btn.textContent = 'Register';
    }
  }

  // ── Build DOM ─────────────────────────────────────────────────────────────

  function _build() {
    const styleEl = document.createElement('style');
    styleEl.textContent = STYLES;
    document.head.appendChild(styleEl);

    const section = document.createElement('section');
    section.id = 'reg-section';
    section.innerHTML = `
      <div class="reg-wizard">
        <div class="reg-wizard-header">
          <span class="reg-wizard-title">⊕ Register a service</span>
          <button class="reg-wizard-close" onclick="RegisterPanel.toggle()" title="Close">✕</button>
        </div>

        <div class="reg-steps">

          <!-- Step 1 — Prerequisites -->
          <div class="reg-step active" id="reg-step1">
            <div class="reg-step-header">
              <div class="reg-step-num">1</div>
              <span class="reg-step-label">Prerequisites</span>
            </div>
            <div class="reg-step-body">
              <div class="reg-checklist">

                <div class="reg-checklist-group">
                  <div class="reg-checklist-group-label required">Metrics (required)</div>
                  <div class="reg-checklist-item">
                    <span class="reg-check">✓</span>
                    <div style="flex: 1">
                      <div>Added a Prometheus client library to your service</div>
                      <div class="reg-code-snippet">from prometheus_client import Counter, generate_latest</div>
                    </div>
                  </div>
                  <div class="reg-checklist-item">
                    <span class="reg-check">✓</span>
                    <div style="flex: 1">
                      <div>Exposed a <code>GET /metrics</code> endpoint that returns Prometheus-format data</div>
                      <div class="reg-code-snippet">@app.get("/metrics")<br/>def metrics():<br/>    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)</div>
                    </div>
                  </div>
                  <div class="reg-checklist-item">
                    <span class="reg-check">✓</span>
                    <div style="flex: 1">
                      <div>Service is running and reachable from inside the aggregator container</div>
                      <div class="reg-code-snippet"># Use Docker service name: http://my-service:8003/metrics<br/># NOT localhost — services are on a bridge network</div>
                    </div>
                  </div>
                </div>

                <div class="reg-checklist-group">
                  <div class="reg-checklist-group-label auto">Logs (automatic)</div>
                  <div class="reg-checklist-item">
                    <span class="reg-check">✓</span>
                    <span>No action needed — Promtail reads stdout from all Docker containers automatically</span>
                  </div>
                </div>

                <div class="reg-checklist-group">
                  <div class="reg-checklist-group-label optional">Traces (optional — required for distributed trace links in RCA)</div>
                  <div class="reg-checklist-item">
                    <span class="reg-check">✓</span>
                    <div style="flex: 1">
                      <div>Added OpenTelemetry SDK to your service</div>
                      <div class="reg-code-snippet">from opentelemetry.sdk.trace import TracerProvider<br/>from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter</div>
                    </div>
                  </div>
                  <div class="reg-checklist-item">
                    <span class="reg-check">✓</span>
                    <div style="flex: 1">
                      <div>Set environment variables in docker-compose.yml</div>
                      <div class="reg-code-snippet">OTEL_EXPORTER_OTLP_ENDPOINT: http://jaeger:4317<br/>OTEL_SERVICE_NAME: my-service</div>
                    </div>
                  </div>
                </div>

              </div>
              <div class="reg-row" style="justify-content:flex-end; margin-top:4px">
                <button class="reg-btn primary" onclick="RegisterPanel._goStep2()">I've completed these steps →</button>
              </div>
            </div>
          </div>

          <!-- Step 2 — Metrics endpoint -->
          <div class="reg-step locked" id="reg-step2">
            <div class="reg-step-header">
              <div class="reg-step-num">2</div>
              <span class="reg-step-label">Metrics endpoint</span>
              <span class="reg-step-summary" id="reg-summary2"></span>
            </div>
            <div class="reg-step-body">
              <div class="reg-field">
                <label>Metrics URL</label>
                <input id="reg-url" type="url" placeholder="http://my-service:8003/metrics"
                       oninput="document.getElementById('reg-next1').disabled=true;
                                document.getElementById('reg-test-msg').className='reg-inline-msg';" />
                <span class="hint">Must be reachable from inside the aggregator container.</span>
              </div>
              <div class="reg-row">
                <button class="reg-btn" id="reg-test-btn" onclick="RegisterPanel._runTest()">Test connection</button>
                <div class="reg-inline-msg" id="reg-test-msg"></div>
              </div>
              <div class="reg-row" style="justify-content:flex-end">
                <button class="reg-btn primary" id="reg-next1" disabled onclick="RegisterPanel._goStep3()">Next →</button>
              </div>
            </div>
          </div>

          <!-- Step 3 — Service details -->
          <div class="reg-step locked" id="reg-step3">
            <div class="reg-step-header">
              <div class="reg-step-num">3</div>
              <span class="reg-step-label">Service details</span>
              <span class="reg-step-summary" id="reg-summary3"></span>
            </div>
            <div class="reg-step-body">
              <div class="reg-field">
                <label>Service name <span style="color:var(--error)">*</span></label>
                <input id="reg-name" type="text" placeholder="my-service" />
                <span class="hint">Used as the Prometheus job label and service identifier.</span>
              </div>
              <div class="reg-field">
                <label>GitHub repo <span style="color:var(--text-muted)">(optional)</span></label>
                <input id="reg-github-repo" type="text" placeholder="owner/repo" />
              </div>
              <div class="reg-field">
                <label>Branch <span style="color:var(--text-muted)">(optional)</span></label>
                <input id="reg-github-branch" type="text" placeholder="main" />
              </div>
              <div class="reg-field">
                <label>Path prefix <span style="color:var(--text-muted)">(optional)</span></label>
                <input id="reg-github-prefix" type="text" placeholder="src" />
              </div>
              <div class="reg-row" style="justify-content:space-between">
                <button class="reg-btn" onclick="RegisterPanel._backToStep(2)">← Back</button>
                <button class="reg-btn primary" onclick="RegisterPanel._goStep4()">Next →</button>
              </div>
            </div>
          </div>

          <!-- Step 4 — Confirm -->
          <div class="reg-step locked" id="reg-step4">
            <div class="reg-step-header">
              <div class="reg-step-num">4</div>
              <span class="reg-step-label">Confirm</span>
              <span class="reg-step-summary" id="reg-summary4"></span>
            </div>
            <div class="reg-step-body">
              <table class="reg-summary-table" id="reg-confirm-table"></table>
              <div class="reg-row" style="justify-content:space-between; margin-top:4px">
                <button class="reg-btn" onclick="RegisterPanel._backToStep(3)">← Back</button>
                <button class="reg-btn primary" id="reg-register-btn" onclick="RegisterPanel._register()">Register</button>
              </div>
              <div class="reg-inline-msg" id="reg-register-msg"></div>
            </div>
          </div>

        </div><!-- /.reg-steps -->
      </div><!-- /.reg-wizard -->
    `;

    // Insert between <header> and <main>
    const main = document.querySelector('main');
    document.body.insertBefore(section, main);
  }

  // ── Public API ────────────────────────────────────────────────────────────

  function toggle() {
    const sec = document.getElementById('reg-section');
    const isOpen = sec.classList.toggle('visible');
    if (!isOpen) {
      // Reset wizard state when closing so next open starts fresh
      _step = 1;
      _testPassed = false;
      _r('url').value = '';
      _r('test-msg').className = 'reg-inline-msg';
      _r('next1').disabled = true;
      _refreshStepHeaders();
    }
  }

  function init() {
    _build();
  }

  window.RegisterPanel = { init, toggle, _runTest, _goStep2, _goStep3, _goStep4, _backToStep, _register };

})();
