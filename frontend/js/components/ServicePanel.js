// ── ServicePanel Component ────────────────────────────────────────────────────
//
// Two-tab panel for managing registered services.
//
// Tab 1 — Registered: table of current services with inline GitHub-metadata
//          editing and removal (protected services are locked).
// Tab 2 — Add new: four-step wizard (absorbs RegisterPanel functionality).
//
// Usage (called once from index.html after all scripts are loaded):
//   ServicePanel.init();
//   ServicePanel.toggle();   // wired to the ☰ Services button in the header

(function () {

  // ── Protected services (no Remove button, no delete on backend) ───────────
  const PROTECTED = new Set(['service-a', 'service-b']);

  // ── Component styles ──────────────────────────────────────────────────────
  const STYLES = `
    /* ── Container ── */
    #sp-section {
      display: none;
      background: var(--bg);
    }
    #sp-section.visible { display: block; }

    .sp-header {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 24px;
      border-bottom: 1px solid var(--border);
      background: var(--bg-raised);
    }
    .sp-title {
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--accent);
    }
    .sp-tabs { display: flex; gap: 4px; margin-left: 16px; }
    .sp-tab {
      background: none;
      border: 1px solid var(--border);
      border-radius: 4px;
      color: var(--text-dim);
      cursor: pointer;
      font-family: var(--mono);
      font-size: 11px;
      padding: 4px 10px;
      letter-spacing: 0.06em;
      transition: border-color 0.15s, color 0.15s;
    }
    .sp-tab:hover { color: var(--text); border-color: var(--border-lit); }
    .sp-tab.active { border-color: var(--accent); color: var(--accent); }
    .sp-close {
      margin-left: auto;
      background: none;
      border: none;
      color: var(--text-dim);
      cursor: pointer;
      font-size: 16px;
      line-height: 1;
      padding: 0 2px;
    }
    .sp-close:hover { color: var(--text); }

    .sp-pane { padding: 16px 24px; }

    /* ── Registered services table ── */
    .sp-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }
    .sp-table th {
      text-align: left;
      padding: 6px 10px;
      font-size: 9px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--text-dim);
      border-bottom: 1px solid var(--border);
    }
    .sp-table td {
      padding: 8px 10px;
      border-bottom: 1px solid var(--border);
      color: var(--text);
      vertical-align: middle;
    }
    .sp-actions { text-align: right; white-space: nowrap; }
    .sp-actions .reg-btn { margin-left: 4px; }
    .sp-edit-grid { display: flex; flex-direction: column; gap: 8px; padding: 4px 0; }
    .sp-edit-fields { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
    .sp-confirm-text { font-size: 12px; color: var(--text); flex: 1; }

    /* ── Shared wizard styles ── */
    .reg-wizard {
      border: 1px solid var(--border);
      border-radius: 6px;
      overflow: hidden;
      background: var(--bg-panel);
    }
    .reg-steps { padding: 16px; display: flex; flex-direction: column; gap: 10px; }
    .reg-step { border: 1px solid var(--border); border-radius: 4px; overflow: hidden; }
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
    .reg-field .hint { font-size: 10px; color: var(--text-muted); }

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
    .reg-btn:hover:not(:disabled) { border-color: var(--cyan); color: var(--cyan); }
    .reg-btn.primary { border-color: var(--accent-dim); color: var(--accent); }
    .reg-btn.primary:hover:not(:disabled) { background: var(--accent-dim); border-color: var(--accent); }
    .reg-btn.danger  { border-color: var(--error-dim, #3d1a1a); color: var(--error, #ff6b6b); }
    .reg-btn.danger:hover:not(:disabled)  { background: var(--error-dim, #3d1a1a); border-color: var(--error, #ff6b6b); }
    .reg-btn:disabled { opacity: 0.35; cursor: not-allowed; }

    .reg-inline-msg {
      font-size: 12px;
      padding: 6px 10px;
      border-radius: 4px;
      display: none;
    }
    .reg-inline-msg.ok    { display: block; color: var(--accent); background: var(--accent-dim); }
    .reg-inline-msg.error { display: block; color: var(--error);  background: var(--error-dim);  }

    .reg-summary-table { font-size: 12px; border-collapse: collapse; width: 100%; max-width: 480px; }
    .reg-summary-table td { padding: 4px 8px 4px 0; vertical-align: top; }
    .reg-summary-table td:first-child { color: var(--text-dim); white-space: nowrap; width: 120px; }
    .reg-summary-table td:last-child  { color: var(--text); font-family: var(--mono); word-break: break-all; }

    /* ── Checklist (step 1) ── */
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
    .reg-check { color: var(--text-dim); flex-shrink: 0; margin-top: 1px; font-size: 11px; }
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

  // ── Wizard state ──────────────────────────────────────────────────────────
  let _step = 1;
  let _url = '';
  let _testPassed = false;
  let _name = '';
  let _githubRepo = '';
  let _githubBranch = '';
  let _githubPathPrefix = '';

  // ── DOM helpers ───────────────────────────────────────────────────────────
  function _r(id) { return document.getElementById('sp-' + id); }

  // ── Tab navigation ────────────────────────────────────────────────────────

  function _showTab(tab) {
    _r('tab-registered').classList.toggle('active', tab === 'registered');
    _r('tab-add').classList.toggle('active', tab === 'add');
    _r('pane-registered').style.display = tab === 'registered' ? 'block' : 'none';
    _r('pane-add').style.display        = tab === 'add'        ? 'block' : 'none';
    if (tab === 'registered') _loadRegistered();
  }

  // ── Registered tab ────────────────────────────────────────────────────────

  async function _loadRegistered() {
    const pane = _r('pane-registered');
    pane.innerHTML = '<p style="padding:4px 0;color:var(--text-dim);font-size:12px">Loading…</p>';
    const endpoint = $('inp-endpoint').value.trim().replace(/\/$/, '');

    try {
      const [srvResp, regResp] = await Promise.all([
        fetch(`${endpoint}/services`),
        fetch(`${endpoint}/services/registry`),
      ]);
      const services = srvResp.ok ? await srvResp.json() : [];
      const regData  = regResp.ok ? await regResp.json() : {};
      const registry = regData.services || {};

      if (services.length === 0) {
        pane.innerHTML = '<p style="padding:4px 0;color:var(--text-dim);font-size:12px">No services registered yet. Use the <strong>Add new</strong> tab to register one.</p>';
        return;
      }

      pane.innerHTML = `
        <table class="sp-table">
          <thead><tr>
            <th>Service</th><th>GitHub repo</th><th>Branch</th><th>Path prefix</th><th></th>
          </tr></thead>
          <tbody>${services.map(n => _buildRow(n, registry[n] || {})).join('')}</tbody>
        </table>
      `;
    } catch (err) {
      pane.innerHTML = `<p style="padding:4px 0;color:var(--error);font-size:12px">Failed to load: ${escHtml(err.message)}</p>`;
    }
  }

  function _buildRow(name, entry) {
    const lock    = PROTECTED.has(name);
    const nameEsc = escHtml(name);
    const repo    = escHtml(entry.github_repo || '');
    const hasRepo = !!entry.github_repo;
    const branch  = escHtml(entry.github_branch  || (hasRepo ? 'main'   : ''));
    const prefix  = escHtml(entry.github_path_prefix || (hasRepo ? '(root)' : ''));

    return `<tr id="sp-row-${nameEsc}" data-name="${nameEsc}">
      <td>
        ${lock ? '<span style="color:var(--text-dim);margin-right:4px" title="Protected">🔒</span>' : ''}
        <span style="font-family:var(--mono)">${nameEsc}</span>
      </td>
      <td style="font-family:var(--mono);color:var(--text-muted)">${repo}</td>
      <td style="font-family:var(--mono);color:${entry.github_branch  ? 'var(--text-muted)' : 'var(--text-dim)'}">${branch}</td>
      <td style="font-family:var(--mono);color:${entry.github_path_prefix ? 'var(--text-muted)' : 'var(--text-dim)'}">${prefix}</td>
      <td class="sp-actions">
        <button class="reg-btn" onclick="ServicePanel._editRow(this.closest('tr').dataset.name)">Edit</button>
        ${!lock ? `<button class="reg-btn danger" onclick="ServicePanel._confirmRemove(this.closest('tr').dataset.name)">Remove</button>` : ''}
      </td>
    </tr>`;
  }

  function _editRow(name) {
    const row = document.getElementById(`sp-row-${name}`);
    if (!row) return;
    const cells  = row.querySelectorAll('td');
    const repo   = cells[1]?.textContent.trim() || '';
    const branch = cells[2]?.textContent.trim() || '';
    const prefix = cells[3]?.textContent.trim() || '';

    row.innerHTML = `<td colspan="5">
      <div class="sp-edit-grid">
        <div class="sp-edit-fields">
          <div class="reg-field">
            <label>GitHub repo</label>
            <input id="sp-edit-repo-${name}" type="text" value="${escHtml(repo)}" placeholder="owner/repo" />
          </div>
          <div class="reg-field">
            <label>Branch</label>
            <input id="sp-edit-branch-${name}" type="text" value="${escHtml(branch)}" placeholder="main" />
          </div>
          <div class="reg-field">
            <label>Path prefix</label>
            <input id="sp-edit-prefix-${name}" type="text" value="${escHtml(prefix)}" placeholder="src" />
          </div>
        </div>
        <div id="sp-edit-msg-${name}" class="reg-inline-msg"></div>
        <div class="reg-row" style="justify-content:flex-end">
          <button class="reg-btn" onclick="ServicePanel._cancelEdit()">Cancel</button>
          <button class="reg-btn primary" onclick="ServicePanel._saveEdit(this.closest('tr').dataset.name)">Save</button>
        </div>
      </div>
    </td>`;
  }

  async function _saveEdit(name) {
    const repo   = document.getElementById(`sp-edit-repo-${name}`)?.value.trim()   ?? '';
    const branch = document.getElementById(`sp-edit-branch-${name}`)?.value.trim() ?? '';
    const prefix = document.getElementById(`sp-edit-prefix-${name}`)?.value.trim() ?? '';
    const msgEl  = document.getElementById(`sp-edit-msg-${name}`);

    if (msgEl) msgEl.className = 'reg-inline-msg';

    try {
      await updateServiceGithub(name, {
        github_repo:        repo,
        github_branch:      branch,
        github_path_prefix: prefix,
      });
      _loadRegistered();
    } catch (err) {
      if (msgEl) {
        msgEl.className = 'reg-inline-msg error';
        msgEl.textContent = '✗ ' + err.message;
      }
    }
  }

  function _cancelEdit() { _loadRegistered(); }

  function _confirmRemove(name) {
    const row = document.getElementById(`sp-row-${name}`);
    if (!row) return;
    row.innerHTML = `<td colspan="5">
      <div class="reg-row" style="align-items:center;gap:12px;padding:4px 0">
        <span class="sp-confirm-text">Remove <strong>${escHtml(name)}</strong>? This stops Prometheus from scraping it.</span>
        <button class="reg-btn" onclick="ServicePanel._cancelRemove()">Cancel</button>
        <button class="reg-btn danger" onclick="ServicePanel._doRemove(this.closest('tr').dataset.name)">Remove</button>
      </div>
    </td>`;
  }

  async function _doRemove(name) {
    try {
      await deleteService(name);

      // Remove the row directly — do NOT call _loadRegistered() here because
      // Prometheus TSDB retains label values after a scrape target is removed,
      // so GET /services would return the stale entry for several minutes.
      const row = document.getElementById(`sp-row-${name}`);
      if (row) row.remove();

      // Show empty-state message if no rows remain
      const pane = _r('pane-registered');
      if (pane && !pane.querySelector('.sp-table tbody tr')) {
        pane.innerHTML = '<p style="padding:4px 0;color:var(--text-dim);font-size:12px">No services registered yet. Use the <strong>Add new</strong> tab to register one.</p>';
      }

      // Optimistically remove the service from the target dropdown as well
      const sel = document.getElementById('inp-target');
      if (sel) {
        const opt = Array.from(sel.options).find(o => o.value === name);
        if (opt) opt.remove();
      }
      document.dispatchEvent(new CustomEvent('obs:services-changed'));
    } catch (err) {
      const row = document.getElementById(`sp-row-${name}`);
      if (row) {
        row.innerHTML = `<td colspan="5">
          <span style="color:var(--error);font-size:12px;padding:4px 0">✗ ${escHtml(err.message)}</span>
        </td>`;
      }
    }
  }

  function _cancelRemove() { _loadRegistered(); }

  // ── Wizard helpers ─────────────────────────────────────────────────────────

  function _stateClass(n) {
    if (n < _step)   return 'done';
    if (n === _step) return 'active';
    return 'locked';
  }

  function _refreshStepHeaders() {
    [1, 2, 3, 4].forEach(n => {
      const el = _r('step' + n);
      if (el) el.className = 'reg-step ' + _stateClass(n);
    });
    const s2 = _r('summary2'); if (s2) s2.textContent = _step > 2 ? '✓ ' + _url  : '';
    const s3 = _r('summary3'); if (s3) s3.textContent = _step > 3 ? '✓ ' + _name : '';
  }

  // ── Step 1 — Prerequisites ────────────────────────────────────────────────

  function _goStep2() {
    _step = 2;
    _refreshStepHeaders();
  }

  // ── Step 2 — Metrics endpoint ─────────────────────────────────────────────

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
    if (!_r('name').value) {
      try { _r('name').value = new URL(_url).hostname; } catch (_) {}
    }
  }

  // ── Step 3 — Service details ──────────────────────────────────────────────

  function _goStep4() {
    _name             = _r('name').value.trim();
    _githubRepo       = _r('github-repo').value.trim();
    _githubBranch     = _r('github-branch').value.trim();
    _githubPathPrefix = _r('github-prefix').value.trim();

    if (!_name) { _r('name').focus(); return; }

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

  // ── Step 4 — Confirm & register ───────────────────────────────────────────

  async function _register() {
    const btn = _r('register-btn');
    btn.disabled = true;
    btn.textContent = '⟳ Registering…';
    _r('register-msg').className = 'reg-inline-msg';

    const payload = { name: _name, metrics_url: _url };
    if (_githubRepo)       payload.github_repo        = _githubRepo;
    if (_githubBranch)     payload.github_branch      = _githubBranch;
    if (_githubPathPrefix) payload.github_path_prefix = _githubPathPrefix;

    try {
      await registerService(payload);
      _r('register-msg').className = 'reg-inline-msg ok';
      _r('register-msg').textContent = `✓ "${_name}" registered. Prometheus will scrape it within 15 s.`;
      if (typeof loadServices === 'function') loadServices();
      document.dispatchEvent(new CustomEvent('obs:services-changed'));
      setTimeout(() => {
        _resetWizard();
        _showTab('registered');
      }, 1500);
    } catch (err) {
      _r('register-msg').className = 'reg-inline-msg error';
      _r('register-msg').textContent = '✗ ' + err.message;
      btn.disabled = false;
      btn.textContent = 'Register';
    }
  }

  function _resetWizard() {
    _step = 1;
    _testPassed = false;
    _url = '';
    _name = '';
    _githubRepo = '';
    _githubBranch = '';
    _githubPathPrefix = '';
    const fields = ['url', 'name', 'github-repo', 'github-branch', 'github-prefix'];
    fields.forEach(f => { const el = _r(f); if (el) el.value = ''; });
    const msg  = _r('test-msg'); if (msg)  msg.className = 'reg-inline-msg';
    const next = _r('next1');    if (next) next.disabled = true;
    _refreshStepHeaders();
  }

  // ── Build DOM ─────────────────────────────────────────────────────────────

  function _build() {
    const styleEl = document.createElement('style');
    styleEl.textContent = STYLES;
    document.head.appendChild(styleEl);

    const section = document.createElement('section');
    section.id = 'sp-section';
    section.innerHTML = `
      <div class="sp-header">
        <span class="sp-title">☰ Services</span>
        <div class="sp-tabs">
          <button class="sp-tab active" id="sp-tab-registered" onclick="ServicePanel._showTab('registered')">Registered</button>
          <button class="sp-tab"        id="sp-tab-add"        onclick="ServicePanel._showTab('add')">Add new</button>
        </div>
        <button class="sp-close" onclick="ServicePanel.toggle()" title="Close">✕</button>
      </div>

      <!-- Registered pane -->
      <div id="sp-pane-registered" class="sp-pane"></div>

      <!-- Add new pane -->
      <div id="sp-pane-add" class="sp-pane" style="display:none">
        <div class="reg-wizard">
          <div class="reg-steps">

            <!-- Step 1 — Prerequisites -->
            <div class="reg-step active" id="sp-step1">
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
                      <div style="flex:1">
                        <div>Added a Prometheus client library to your service</div>
                        <div class="reg-code-snippet">from prometheus_client import Counter, generate_latest</div>
                      </div>
                    </div>
                    <div class="reg-checklist-item">
                      <span class="reg-check">✓</span>
                      <div style="flex:1">
                        <div>Exposed a <code>GET /metrics</code> endpoint that returns Prometheus-format data</div>
                        <div class="reg-code-snippet">@app.get("/metrics")<br/>def metrics():<br/>    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)</div>
                      </div>
                    </div>
                    <div class="reg-checklist-item">
                      <span class="reg-check">✓</span>
                      <div style="flex:1">
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
                      <div style="flex:1">
                        <div>Added OpenTelemetry SDK to your service</div>
                        <div class="reg-code-snippet">from opentelemetry.sdk.trace import TracerProvider<br/>from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter</div>
                      </div>
                    </div>
                    <div class="reg-checklist-item">
                      <span class="reg-check">✓</span>
                      <div style="flex:1">
                        <div>Set environment variables in docker-compose.yml</div>
                        <div class="reg-code-snippet">OTEL_EXPORTER_OTLP_ENDPOINT: http://jaeger:4317<br/>OTEL_SERVICE_NAME: my-service</div>
                      </div>
                    </div>
                  </div>

                </div>
                <div class="reg-row" style="justify-content:flex-end;margin-top:4px">
                  <button class="reg-btn primary" onclick="ServicePanel._goStep2()">I've completed these steps →</button>
                </div>
              </div>
            </div>

            <!-- Step 2 — Metrics endpoint -->
            <div class="reg-step locked" id="sp-step2">
              <div class="reg-step-header">
                <div class="reg-step-num">2</div>
                <span class="reg-step-label">Metrics endpoint</span>
                <span class="reg-step-summary" id="sp-summary2"></span>
              </div>
              <div class="reg-step-body">
                <div class="reg-field">
                  <label>Metrics URL</label>
                  <input id="sp-url" type="url" placeholder="http://my-service:8003/metrics"
                         oninput="document.getElementById('sp-next1').disabled=true;
                                  document.getElementById('sp-test-msg').className='reg-inline-msg';" />
                  <span class="hint">Must be reachable from inside the aggregator container.</span>
                </div>
                <div class="reg-row">
                  <button class="reg-btn" id="sp-test-btn" onclick="ServicePanel._runTest()">Test connection</button>
                  <div class="reg-inline-msg" id="sp-test-msg"></div>
                </div>
                <div class="reg-row" style="justify-content:flex-end">
                  <button class="reg-btn primary" id="sp-next1" disabled onclick="ServicePanel._goStep3()">Next →</button>
                </div>
              </div>
            </div>

            <!-- Step 3 — Service details -->
            <div class="reg-step locked" id="sp-step3">
              <div class="reg-step-header">
                <div class="reg-step-num">3</div>
                <span class="reg-step-label">Service details</span>
                <span class="reg-step-summary" id="sp-summary3"></span>
              </div>
              <div class="reg-step-body">
                <div class="reg-field">
                  <label>Service name <span style="color:var(--error)">*</span></label>
                  <input id="sp-name" type="text" placeholder="my-service" />
                  <span class="hint">Used as the Prometheus job label and service identifier.</span>
                </div>
                <div class="reg-field">
                  <label>GitHub repo <span style="color:var(--text-muted)">(optional)</span></label>
                  <input id="sp-github-repo" type="text" placeholder="owner/repo" />
                </div>
                <div class="reg-field">
                  <label>Branch <span style="color:var(--text-muted)">(optional)</span></label>
                  <input id="sp-github-branch" type="text" placeholder="main" />
                </div>
                <div class="reg-field">
                  <label>Path prefix <span style="color:var(--text-muted)">(optional)</span></label>
                  <input id="sp-github-prefix" type="text" placeholder="src" />
                </div>
                <div class="reg-row" style="justify-content:space-between">
                  <button class="reg-btn" onclick="ServicePanel._backToStep(2)">← Back</button>
                  <button class="reg-btn primary" onclick="ServicePanel._goStep4()">Next →</button>
                </div>
              </div>
            </div>

            <!-- Step 4 — Confirm -->
            <div class="reg-step locked" id="sp-step4">
              <div class="reg-step-header">
                <div class="reg-step-num">4</div>
                <span class="reg-step-label">Confirm</span>
                <span class="reg-step-summary" id="sp-summary4"></span>
              </div>
              <div class="reg-step-body">
                <table class="reg-summary-table" id="sp-confirm-table"></table>
                <div class="reg-row" style="justify-content:space-between;margin-top:4px">
                  <button class="reg-btn" onclick="ServicePanel._backToStep(3)">← Back</button>
                  <button class="reg-btn primary" id="sp-register-btn" onclick="ServicePanel._register()">Register</button>
                </div>
                <div class="reg-inline-msg" id="sp-register-msg"></div>
              </div>
            </div>

          </div><!-- /.reg-steps -->
        </div><!-- /.reg-wizard -->
      </div>
    `;

    const main = document.querySelector('main');
    document.body.insertBefore(section, main);
  }

  // ── Public API ────────────────────────────────────────────────────────────

  function toggle() {
    const sec = document.getElementById('sp-section');
    const isOpen = sec.classList.toggle('visible');
    const btn = document.getElementById('btn-services');
    if (btn) btn.classList.toggle('active', isOpen);
    // Hide the cluster-status bar while this panel takes over the content area.
    const clusterBar = document.getElementById('cluster-bar');
    if (clusterBar) clusterBar.style.display = isOpen ? 'none' : '';
    if (isOpen) {
      // close Demo panel if open
      const demoSec = document.getElementById('demo-section');
      if (demoSec && demoSec.classList.contains('visible')) {
        window.DemoPanel && window.DemoPanel.toggle();
      }
      _showTab('registered');
    } else {
      _resetWizard();
    }
  }

  function init() {
    _build();
  }

  window.ServicePanel = {
    init, toggle, _showTab,
    _runTest, _goStep2, _goStep3, _goStep4, _backToStep, _register,
    _editRow, _saveEdit, _cancelEdit,
    _confirmRemove, _doRemove, _cancelRemove,
  };

})();
