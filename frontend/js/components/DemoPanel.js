// ── DemoPanel Component ───────────────────────────────────────────────────────
//
// Renders an interactive panel for triggering demo scenarios and viewing
// their streamed output without leaving the UI.
//
// Usage (called once from api.js on page load):
//   DemoPanel.init();
//
// The panel is hidden by default and toggled with the "Demo" button in the
// header. It reads the API endpoint from #inp-endpoint, the same as the rest
// of the UI, so it works against any running aggregator.
//
// SSE event types received from POST /demo/run/{scenario}:
//   {type: "status",  message: "..."}
//   {type: "config",  failure_rate: 0.7, latency_ms: 0}
//   {type: "request", index: 1, total: 30, status_code: 200, elapsed_ms: 45, ok: true}
//   {type: "done",    success: 9, failed: 21, total: 30, query_target: "service-a"}
//   {type: "error",   message: "..."}

(function () {

  // ── Inject component styles ─────────────────────────────────────────────
  // Prefixed with `demo-` to avoid collisions with existing panel styles.

  const STYLES = `
    #demo-section {
      display: none;
      padding: 0 24px 8px;
    }
    #demo-section.visible {
      display: block;
    }
    .demo-panel {
      background: var(--surface, #0f1117);
      border: 1px solid var(--border, #1e2330);
      border-radius: 4px;
      overflow: hidden;
    }
    .demo-panel-header {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 10px 16px;
      border-bottom: 1px solid var(--border, #1e2330);
      background: var(--surface-raised, #13161f);
    }
    .demo-panel-title {
      font-family: 'IBM Plex Mono', monospace;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.12em;
      color: var(--accent, #3dffa0);
      text-transform: uppercase;
    }
    .demo-config-badges {
      display: flex;
      gap: 8px;
      margin-left: 4px;
      flex: 1;
    }
    .demo-badge {
      font-family: 'IBM Plex Mono', monospace;
      font-size: 10px;
      padding: 2px 8px;
      border-radius: 2px;
      background: var(--bg, #080b12);
      border: 1px solid var(--border, #1e2330);
      color: var(--text-dim, #5a6175);
      white-space: nowrap;
      transition: color 0.2s, border-color 0.2s;
    }
    .demo-badge.active {
      color: var(--accent, #3dffa0);
      border-color: var(--accent, #3dffa0);
    }
    .demo-btn-reset {
      font-family: 'IBM Plex Mono', monospace;
      font-size: 10px;
      padding: 4px 12px;
      background: transparent;
      border: 1px solid var(--border, #1e2330);
      color: var(--text-dim, #5a6175);
      border-radius: 2px;
      cursor: pointer;
      transition: color 0.15s, border-color 0.15s;
      white-space: nowrap;
    }
    .demo-btn-reset:hover:not(:disabled) {
      color: var(--text, #c8cdd8);
      border-color: var(--text-dim, #5a6175);
    }
    .demo-btn-reset:disabled {
      opacity: 0.4;
      cursor: not-allowed;
    }
    .demo-scenarios {
      display: grid;
      grid-template-columns: repeat(6, 1fr);
      gap: 1px;
      background: var(--border, #1e2330);
    }
    .demo-scenario-card {
      background: var(--surface, #0f1117);
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .demo-scenario-letter {
      font-family: 'IBM Plex Mono', monospace;
      font-size: 18px;
      font-weight: 600;
      color: var(--text-dim, #5a6175);
      line-height: 1;
    }
    .demo-scenario-name {
      font-family: 'IBM Plex Mono', monospace;
      font-size: 11px;
      font-weight: 600;
      color: var(--text, #c8cdd8);
    }
    .demo-scenario-desc {
      font-size: 11px;
      color: var(--text-dim, #5a6175);
      line-height: 1.5;
      flex: 1;
    }
    .demo-scenario-meta {
      font-family: 'IBM Plex Mono', monospace;
      font-size: 10px;
      color: var(--text-dim, #5a6175);
      padding: 4px 0;
      border-top: 1px solid var(--border, #1e2330);
    }
    .demo-scenario-meta span {
      color: var(--blue, #38d4f5);
    }
    .demo-btn-run {
      font-family: 'IBM Plex Mono', monospace;
      font-size: 11px;
      padding: 6px 14px;
      background: transparent;
      border: 1px solid var(--border, #1e2330);
      color: var(--accent, #3dffa0);
      border-radius: 2px;
      cursor: pointer;
      align-self: flex-start;
      transition: background 0.15s, border-color 0.15s;
    }
    .demo-btn-run:hover:not(:disabled) {
      background: rgba(61,255,160,0.06);
      border-color: var(--accent, #3dffa0);
    }
    .demo-btn-run:disabled {
      opacity: 0.4;
      cursor: not-allowed;
    }
    .demo-btn-run.running {
      color: var(--text-dim, #5a6175);
      border-color: var(--text-dim, #5a6175);
    }
    .demo-output {
      display: none;
      border-top: 1px solid var(--border, #1e2330);
    }
    .demo-output.visible {
      display: block;
    }
    .demo-output-header {
      font-family: 'IBM Plex Mono', monospace;
      font-size: 10px;
      color: var(--text-dim, #5a6175);
      padding: 8px 16px 4px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .demo-output-log {
      font-family: 'IBM Plex Mono', monospace;
      font-size: 11px;
      line-height: 1.7;
      padding: 4px 16px 12px;
      max-height: 220px;
      overflow-y: auto;
      background: var(--bg, #080b12);
    }
    .demo-log-line {
      display: flex;
      gap: 10px;
      white-space: pre;
    }
    .demo-log-line .ts {
      color: var(--text-dim, #5a6175);
      flex-shrink: 0;
      user-select: none;
    }
    .demo-log-line .msg { color: var(--text-dim, #5a6175); }
    .demo-log-line.ok   .msg { color: var(--accent, #3dffa0); }
    .demo-log-line.err  .msg { color: #ff4e6a; }
    .demo-log-line.info .msg { color: var(--text, #c8cdd8); }
    .demo-log-line.done .msg { color: var(--blue, #38d4f5); }
    .demo-hint {
      font-family: 'IBM Plex Mono', monospace;
      font-size: 11px;
      color: var(--blue, #38d4f5);
      padding: 10px 16px;
      border-top: 1px solid var(--border, #1e2330);
      background: rgba(56,212,245,0.04);
    }
  `;

  function injectStyles() {
    if (document.getElementById('demo-panel-styles')) return;
    const style = document.createElement('style');
    style.id = 'demo-panel-styles';
    style.textContent = STYLES;
    document.head.appendChild(style);
  }

  // ── Scenario definitions (mirrors aggregator/demo.py) ──────────────────

  const SCENARIOS = [
    {
      id:     'healthy',
      letter: 'A',
      name:   'Normal operation',
      desc:   'Resets service-b to clean defaults, then fires 20 requests through service-a. Use this as a baseline before running failure scenarios.',
      meta:   { 'FAILURE_RATE': '0.00', 'LATENCY_MS': '0ms', 'requests': '20 × /api/data' },
    },
    {
      id:     'errors',
      letter: 'B',
      name:   'High error rate',
      desc:   'Sets 70% failure rate on service-b, then fires 30 requests through service-a.',
      meta:   { 'FAILURE_RATE': '0.70', 'requests': '30 × /api/data' },
    },
    {
      id:     'slow',
      letter: 'C',
      name:   'Latency spike',
      desc:   'Adds 2 second delay to service-b /data, then fires 10 direct requests.',
      meta:   { 'LATENCY_MS': '2000', 'requests': '10 × /data' },
    },
    {
      id:     'crash',
      letter: 'D',
      name:   'Payment crash',
      desc:   'Fires 15 requests to /crash — triggers a full Python traceback each time.',
      meta:   { 'behavior': 'always crashes', 'exception': 'ValueError', 'requests': '15 × /crash' },
    },
    {
      id:     'payment_crash',
      letter: 'E',
      name:   'Gateway timeout',
      desc:   'Sets GATEWAY_FAIL=1 on service-c and fires 15 POST /pay requests. Every call raises GatewayTimeoutError inside charge_gateway() — the full three-frame traceback (pay → process_payment → charge_gateway) is logged to stdout. Configure the service-c GitHub repo in settings so RCA links directly to charge_gateway() in main.py.',
      meta:   { 'GATEWAY_FAIL': '1', 'exception': 'GatewayTimeoutError', 'requests': '15 × /pay' },
    },
    {
      id:     'inventory_crash',
      letter: 'F',
      name:   'DB connection lost',
      desc:   'Sets DB_FAIL=1 on service-d and fires 15 GET /stock/widget-001 requests. Every call raises DatabaseConnectionError inside query_database() — the full traceback (get_stock → lookup_inventory → query_database) is logged to stdout. Configure the service-d GitHub repo in settings so RCA links directly to query_database() in main.py.',
      meta:   { 'DB_FAIL': '1', 'exception': 'DatabaseConnectionError', 'requests': '15 × /stock/{item_id}' },
    },
  ];

  // ── State ───────────────────────────────────────────────────────────────

  let _running = false;   // true while a scenario stream is active
  let _hint    = null;    // last query_target from a completed scenario

  // ── Helpers ─────────────────────────────────────────────────────────────

  function _endpoint() {
    return ($('inp-endpoint').value || 'http://localhost:8080').replace(/\/$/, '');
  }

  function _now() {
    return new Date().toISOString().slice(11, 19);
  }

  // ── Build DOM ───────────────────────────────────────────────────────────

  function _buildPanel() {
    const section = document.createElement('section');
    section.id = 'demo-section';

    // Scenario cards HTML
    const cardsHtml = SCENARIOS.map(s => {
      const metaHtml = Object.entries(s.meta)
        .map(([k, v]) => `${k}: <span>${v}</span>`)
        .join('  ');
      return `
        <div class="demo-scenario-card">
          <div class="demo-scenario-letter">${s.letter}</div>
          <div class="demo-scenario-name">${s.name}</div>
          <div class="demo-scenario-desc">${s.desc}</div>
          <div class="demo-scenario-meta">${metaHtml}</div>
          <button class="demo-btn-run"
                  id="demo-btn-${s.id}"
                  onclick="DemoPanel.runScenario('${s.id}')">
            ▶ Run
          </button>
        </div>
      `;
    }).join('');

    section.innerHTML = `
      <div class="demo-panel">
        <div class="demo-panel-header">
          <span class="demo-panel-title">Demo Scenarios</span>
          <div class="demo-config-badges">
            <span class="demo-badge" id="demo-badge-rate">FAILURE_RATE: 0.00</span>
            <span class="demo-badge" id="demo-badge-ms">LATENCY_MS: 0ms</span>
          </div>
          <button class="demo-btn-reset" id="demo-btn-reset" onclick="DemoPanel.reset()">
            ↺ Reset
          </button>
        </div>

        <div class="demo-scenarios">${cardsHtml}</div>

        <div class="demo-output" id="demo-output">
          <div class="demo-output-header">Output</div>
          <div class="demo-output-log" id="demo-log"></div>
        </div>

        <div class="demo-hint" id="demo-hint" style="display:none"></div>
      </div>
    `;

    return section;
  }

  // ── Log helpers ─────────────────────────────────────────────────────────

  function _appendLog(text, cls = '') {
    const log = $('demo-log');
    if (!log) return;
    const line = document.createElement('div');
    line.className = `demo-log-line ${cls}`;
    line.innerHTML = `<span class="ts">${_now()}</span><span class="msg">${escHtml(text)}</span>`;
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
  }

  function _clearLog() {
    const log = $('demo-log');
    const output = $('demo-output');
    if (log) log.innerHTML = '';
    if (output) output.classList.remove('visible');
    const hint = $('demo-hint');
    if (hint) { hint.style.display = 'none'; hint.textContent = ''; }
  }

  function _showOutput() {
    $('demo-output').classList.add('visible');
  }

  // ── Config badge updater ─────────────────────────────────────────────────

  function _updateBadges(failure_rate, latency_ms) {
    const rateEl = $('demo-badge-rate');
    const msEl   = $('demo-badge-ms');
    if (!rateEl || !msEl) return;

    const rate = failure_rate ?? 0;
    const ms   = latency_ms ?? 0;

    rateEl.textContent = `FAILURE_RATE: ${rate.toFixed(2)}`;
    rateEl.classList.toggle('active', rate > 0);

    msEl.textContent = `LATENCY_MS: ${ms}ms`;
    msEl.classList.toggle('active', ms > 0);
  }

  // ── Lock/unlock all run buttons ──────────────────────────────────────────

  function _setRunning(state) {
    _running = state;
    SCENARIOS.forEach(s => {
      const btn = $(`demo-btn-${s.id}`);
      if (!btn) return;
      btn.disabled = state;
      btn.classList.toggle('running', state);
    });
    const resetBtn = $('demo-btn-reset');
    if (resetBtn) resetBtn.disabled = state;
  }

  // ── SSE event handler ────────────────────────────────────────────────────

  function _handleEvent(event) {
    switch (event.type) {

      case 'status':
        _appendLog(`→ ${event.message}`, 'info');
        break;

      case 'config':
        _updateBadges(event.failure_rate, event.latency_ms);
        _appendLog(
          `✓ Configured: FAILURE_RATE=${event.failure_rate.toFixed(2)}  LATENCY_MS=${event.latency_ms}ms`,
          'ok',
        );
        break;

      case 'request': {
        const status = event.status_code === 0 ? 'timeout' : String(event.status_code);
        const text   = `[${String(event.index).padStart(2)}/${event.total}]  ${status}  ${event.elapsed_ms}ms`;
        _appendLog(text, event.ok ? 'ok' : 'err');
        break;
      }

      case 'done': {
        const summary = `Done — ${event.success} ok, ${event.failed} failed out of ${event.total} requests`;
        _appendLog(summary, 'done');

        // Show a hint pointing at the correct query target
        const hint = $('demo-hint');
        if (hint) {
          hint.style.display = 'block';
          hint.textContent =
            `→ Query "${event.query_target}" in the main UI above to see the signals.`;
        }
        break;
      }

      case 'error':
        _appendLog(`✗ ${event.message}`, 'err');
        break;
    }
  }

  // ── Public API ───────────────────────────────────────────────────────────

  window.DemoPanel = {

    init() {
      injectStyles();

      // Insert the demo section between <header> and <main>
      const header = document.querySelector('header');
      const section = _buildPanel();
      header.after(section);

      // Fetch the current service-b config to initialize the badges
      this.refreshConfig();
    },

    toggle() {
      const section = document.getElementById('demo-section');
      if (!section) return;
      const visible = section.classList.toggle('visible');
      const btn = $('btn-demo');
      if (btn) btn.classList.toggle('active', visible);
      if (visible) this.refreshConfig();
    },

    async refreshConfig() {
      try {
        const resp = await fetch(`${_endpoint()}/demo/config`);
        if (!resp.ok) return;
        const data = await resp.json();
        _updateBadges(data.failure_rate, data.latency_ms);
      } catch {
        // Aggregator may not be running yet — silently ignore
      }
    },

    async runScenario(scenario) {
      if (_running) return;
      _setRunning(true);
      _clearLog();
      _showOutput();

      try {
        const resp = await fetch(`${_endpoint()}/demo/run/${scenario}`, {
          method: 'POST',
        });

        if (!resp.ok) {
          _appendLog(`Request failed: HTTP ${resp.status}`, 'err');
          return;
        }

        // Read the SSE stream chunk by chunk
        const reader  = resp.body.getReader();
        const decoder = new TextDecoder();
        let   buffer  = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          // SSE events are separated by \n\n
          let idx;
          while ((idx = buffer.indexOf('\n\n')) !== -1) {
            const chunk = buffer.slice(0, idx).trim();
            buffer = buffer.slice(idx + 2);
            if (chunk.startsWith('data: ')) {
              try {
                _handleEvent(JSON.parse(chunk.slice(6)));
              } catch {
                // Malformed event — skip
              }
            }
          }
        }

      } catch (err) {
        _appendLog(`Stream error: ${err.message}`, 'err');
      } finally {
        _setRunning(false);
      }
    },

    async reset() {
      if (_running) return;
      const btn = $('demo-btn-reset');
      if (btn) { btn.disabled = true; btn.textContent = '↺ Resetting…'; }

      try {
        const resp = await fetch(`${_endpoint()}/demo/reset`, { method: 'POST' });
        const data = await resp.json();
        _updateBadges(data.failure_rate ?? 0, data.latency_ms ?? 0);
        _clearLog();
        _showOutput();
        _appendLog('✓ service-b reset to defaults (FAILURE_RATE=0.00  LATENCY_MS=0ms)', 'ok');
      } catch (err) {
        _appendLog(`Reset failed: ${err.message}`, 'err');
        _showOutput();
      } finally {
        if (btn) { btn.disabled = false; btn.textContent = '↺ Reset'; }
      }
    },
  };

})();
