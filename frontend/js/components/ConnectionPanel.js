// ── ConnectionPanel Component ─────────────────────────────────────────────────
//
// Settings → "API Endpoint & Namespace": where the aggregator API lives, and
// (in Kubernetes) which namespace to query. Persisted in localStorage.
//
// Unlike LlmConfigPanel/HistoryListPanel this one is built EAGERLY (init() is
// called from index.html's bootstrap before anything else) because its
// #inp-endpoint / #inp-namespace inputs are read by the rest of the app from
// page load. The <section> is display:none until toggle()'d open, and toggling
// it hides the cluster bar / closes the other content panels — same as the rest.

const ConnectionPanel = {
  ENDPOINT_KEY: 'obs_api_endpoint',
  NAMESPACE_KEY: 'obs_namespace',
  _built: false,
  _msgTimer: null,

  init() {
    if (this._built) return;
    let endpoint = 'http://localhost:8080', namespace = 'default';
    try {
      endpoint  = localStorage.getItem(this.ENDPOINT_KEY)  || endpoint;
      namespace = localStorage.getItem(this.NAMESPACE_KEY) || namespace;
    } catch (_) { /* private mode etc. */ }

    const section = document.createElement('section');
    section.id = 'connection-section';
    section.innerHTML = `
      <div class="conn-bar">
        <span class="conn-title">CONNECTION</span>
        <button class="conn-close" onclick="ConnectionPanel.toggle()" title="Close">✕</button>
      </div>
      <div class="conn-body">
        <div class="conn-note">
          Where the aggregator API lives, and — when querying a Kubernetes cluster — which namespace to scope to.
          Stored locally in your browser.
        </div>
        <div class="conn-form">
          <div class="field conn-wide">
            <label for="inp-endpoint">API Endpoint</label>
            <input id="inp-endpoint" type="text" value="${escHtml(endpoint)}"
                   autocomplete="off" spellcheck="false" placeholder="http://host:port" />
          </div>
          <div class="field">
            <label for="inp-namespace">Namespace</label>
            <input id="inp-namespace" type="text" value="${escHtml(namespace)}"
                   autocomplete="off" spellcheck="false" placeholder="default" />
          </div>
        </div>
        <div class="conn-actions">
          <button class="btn-query" id="conn-save">Apply</button>
          <span class="conn-msg" id="conn-msg"></span>
          <span class="conn-hint">Apply also saves these to your browser so they persist across reloads.</span>
        </div>
      </div>
    `;
    const main = document.querySelector('main');
    document.body.insertBefore(section, main);

    // Live-apply the field values for the current session (no persistence) —
    // editing + Enter takes effect immediately so you can try a new endpoint.
    const applyLive = () => {
      if (typeof loadServices === 'function') loadServices();
      if (typeof ClusterStatusPanel !== 'undefined') ClusterStatusPanel.refresh();
    };
    document.getElementById('inp-endpoint').addEventListener('change', applyLive);
    document.getElementById('inp-namespace').addEventListener('change', () => {
      if (typeof ClusterStatusPanel !== 'undefined') ClusterStatusPanel.refresh();
    });
    // "Apply" — persist to localStorage so it survives a refresh, and re-apply.
    document.getElementById('conn-save').addEventListener('click', () => {
      this._persist();
      applyLive();
      this._flash('Applied & saved ✓');
    });

    this._built = true;
  },

  _persist() {
    try {
      localStorage.setItem(this.ENDPOINT_KEY, document.getElementById('inp-endpoint').value.trim());
      localStorage.setItem(this.NAMESPACE_KEY, document.getElementById('inp-namespace').value.trim());
    } catch (_) {}
  },

  _flash(msg) {
    const m = document.getElementById('conn-msg');
    if (!m) return;
    m.textContent = msg;
    m.className = 'conn-msg ok';
    clearTimeout(this._msgTimer);
    this._msgTimer = setTimeout(() => { m.textContent = ''; m.className = 'conn-msg'; }, 3000);
  },

  isOpen() {
    const s = document.getElementById('connection-section');
    return !!(s && s.classList.contains('visible'));
  },

  toggle() {
    this.init();
    const sec = document.getElementById('connection-section');
    const open = sec.classList.toggle('visible');
    const cb = document.getElementById('cluster-bar');
    if (cb) cb.style.display = open ? 'none' : '';
    if (open) {
      const sp = document.getElementById('sp-section');
      if (sp && sp.classList.contains('visible') && window.ServicePanel) ServicePanel.toggle();
      const demo = document.getElementById('demo-section');
      if (demo && demo.classList.contains('visible') && window.DemoPanel) DemoPanel.toggle();
      if (window.LlmConfigPanel && LlmConfigPanel.isOpen()) LlmConfigPanel.toggle();
      if (window.HistoryListPanel && HistoryListPanel.isOpen()) HistoryListPanel.toggle();
    }
  },
};

window.ConnectionPanel = ConnectionPanel;
