const EnvironmentPanel = {
  _built: false,

  init() {
    if (this._built) return;
    const section = document.createElement('section');
    section.id = 'environment-section';
    section.innerHTML = `
      <div class="conn-bar">
        <span class="conn-title">ENVIRONMENT</span>
        <button class="conn-close" onclick="EnvironmentPanel.toggle()" title="Close">✕</button>
      </div>
      <div class="conn-body">
        <div class="conn-note">Switch between local, staging, and production observability backends.</div>
        <div class="conn-form">
          <div class="field conn-wide">
            <label for="env-name">Environment</label>
            <select id="env-name">
              <option value="local">local</option>
              <option value="staging">staging</option>
              <option value="production">production</option>
            </select>
          </div>
          <div class="field conn-wide">
            <label>Current backend URLs</label>
            <div id="env-current" class="env-current">—</div>
          </div>
        </div>
        <div class="conn-actions">
          <button class="btn-query" id="env-apply">Apply environment</button>
          <span class="conn-msg" id="env-msg"></span>
        </div>
      </div>
    `;

    const main = document.querySelector('main');
    document.body.insertBefore(section, main);

    document.getElementById('env-apply').addEventListener('click', () => this.apply());
    this._built = true;
  },

  isOpen() {
    const s = document.getElementById('environment-section');
    return !!(s && s.classList.contains('visible'));
  },

  async refresh() {
    this.init();
    const endpoint = ($('inp-endpoint')?.value || 'http://localhost:8080').replace(/\/$/, '');
    const msg = $('env-msg');
    try {
      const resp = await fetch(`${endpoint}/api/environment`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      $('env-name').value = data.current || 'local';
      const urls = data.current_urls || {};
      $('env-current').textContent = `${urls.prometheus_url || '—'} | ${urls.loki_url || '—'} | ${urls.jaeger_url || '—'}`;
      if (msg) msg.textContent = '';
    } catch (err) {
      if (msg) {
        msg.className = 'conn-msg';
        msg.textContent = `Failed to load environment: ${err.message}`;
      }
    }
  },

  async apply() {
    const endpoint = ($('inp-endpoint')?.value || 'http://localhost:8080').replace(/\/$/, '');
    const env = $('env-name').value;
    const msg = $('env-msg');
    try {
      const resp = await fetch(`${endpoint}/api/environment`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: env }),
      });
      if (!resp.ok) {
        const txt = await resp.text();
        throw new Error(txt || `HTTP ${resp.status}`);
      }
      await this.refresh();
      if (msg) {
        msg.className = 'conn-msg ok';
        msg.textContent = `Switched to ${env}`;
      }
      if (typeof loadServices === 'function') loadServices();
      if (typeof ClusterStatusPanel !== 'undefined') ClusterStatusPanel.refresh();
    } catch (err) {
      if (msg) {
        msg.className = 'conn-msg';
        msg.textContent = `Apply failed: ${err.message}`;
      }
    }
  },

  toggle() {
    this.init();
    const open = document.getElementById('environment-section').classList.toggle('visible');
    if (open) {
      if (typeof Sidebar !== 'undefined') Sidebar.closeOtherPanels('environment');
      this.refresh();
    }
    if (typeof Sidebar !== 'undefined') Sidebar.syncClusterBar();
  },
};

window.EnvironmentPanel = EnvironmentPanel;
