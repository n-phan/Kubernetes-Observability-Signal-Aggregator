/**
 * EnvironmentPanel — Multi-environment support.
 *
 * Allows developers to switch between local, staging, and production
 * observability backends without restarting the application.
 */

const EnvironmentPanel = {
  STORAGE_KEY: 'observability-env',
  _built: false,
  _msgTimer: null,

  /**
   * Set the current environment and store in localStorage.
   */
  _getCurrentEnvironment() {
    return localStorage.getItem(this.STORAGE_KEY) || 'local';
  },

  _getEndpoint() {
    const input = document.getElementById('inp-endpoint');
    const raw = input ? input.value.trim() : 'http://localhost:8080';
    return (raw || 'http://localhost:8080').replace(/\/$/, '');
  },

  setEnvironment(env) {
    const validEnvs = ['local', 'staging', 'production'];
    if (!validEnvs.includes(env)) {
      console.error('Invalid environment:', env);
      return false;
    }

    localStorage.setItem(this.STORAGE_KEY, env);
    console.log('Environment switched to:', env);

    // Notify backend via configured aggregator endpoint.
    fetch(`${this._getEndpoint()}/api/environment`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ environment: env }),
    }).catch((err) => console.warn('Failed to notify backend of env change:', err));

    return true;
  },

  /**
   * Get the current environment.
   */
  getEnvironment() {
    return this._getCurrentEnvironment();
  },

  _build() {
    if (this._built) return;
    const existing = document.getElementById('env-section');
    if (existing) {
      this._built = true;
      return;
    }
    const section = document.createElement('section');
    section.id = 'env-section';
    section.innerHTML = `
      <div class="llm-bar">
        <span class="llm-title">ENVIRONMENT</span>
        <button class="llm-close" onclick="EnvironmentPanel.toggle()" title="Close">✕</button>
      </div>
      <div class="llm-body">
        <div class="llm-note">
          Switch the active observability environment for queries.
          Selection is stored in your browser and sent to the backend.
        </div>
        <div id="env-panel-host"></div>
        <div class="llm-actions">
          <span class="llm-msg" id="env-msg"></span>
        </div>
      </div>
    `;

    const main = document.querySelector('main');
    document.body.insertBefore(section, main);

    const host = document.getElementById('env-panel-host');
    host.appendChild(this.renderSelector());

    this._built = true;
  },

  toggle() {
    this._build();
    const open = document.getElementById('env-section').classList.toggle('visible');
    if (open && typeof Sidebar !== 'undefined') Sidebar.closeOtherPanels('environment');
    if (typeof Sidebar !== 'undefined') Sidebar.syncClusterBar();
  },

  isOpen() {
    const sec = document.getElementById('env-section');
    return !!(sec && sec.classList.contains('visible'));
  },

  _flash(msg) {
    const el = document.getElementById('env-msg');
    if (!el) return;
    el.textContent = msg;
    el.className = 'llm-msg ok';
    clearTimeout(this._msgTimer);
    this._msgTimer = setTimeout(() => {
      el.textContent = '';
      el.className = 'llm-msg';
    }, 2500);
  },

  /**
   * Render environment selector panel.
   */
  renderSelector() {
    const panel = document.createElement('div');
    panel.className = 'panel environment-panel';

    const label = document.createElement('label');
    label.textContent = 'Environment:';

    const select = document.createElement('select');
    select.className = 'env-select';
    const envs = ['local', 'staging', 'production'];
    const currentEnvironment = this._getCurrentEnvironment();
    envs.forEach((env) => {
      const option = document.createElement('option');
      option.value = env;
      option.textContent = env.charAt(0).toUpperCase() + env.slice(1);
      option.selected = currentEnvironment === env;
      select.appendChild(option);
    });

    select.onchange = (e) => {
      this.setEnvironment(e.target.value);
      const badge = document.createElement('div');
      badge.className = 'env-badge';
      badge.textContent = `✓ Switched to ${e.target.value}`;
      panel.appendChild(badge);
      setTimeout(() => badge.remove(), 2000);
      this._flash(`Environment set to ${e.target.value}`);
    };

    const info = document.createElement('div');
    info.className = 'env-info';
    info.innerHTML = `
      <small>
        Current: <strong>${currentEnvironment.toUpperCase()}</strong>
        <br/>
        (Switch to view metrics, logs, and traces from different clusters)
      </small>
    `;

    panel.appendChild(label);
    panel.appendChild(select);
    panel.appendChild(info);

    return panel;
  },
};

window.EnvironmentPanel = EnvironmentPanel;
