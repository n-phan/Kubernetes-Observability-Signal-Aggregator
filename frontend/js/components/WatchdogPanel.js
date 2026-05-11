/**
 * WatchdogPanel — Auto-watchdog mode control and alert display.
 */

const WatchdogPanel = {
  _built: false,
  enabled: false,
  alerts: [],

  _getEndpoint() {
    const input = document.getElementById('inp-endpoint');
    const raw = input ? input.value.trim() : 'http://localhost:8080';
    return (raw || 'http://localhost:8080').replace(/\/$/, '');
  },

  async _loadServices() {
    try {
      const resp = await fetch(`${this._getEndpoint()}/services`);
      if (!resp.ok) return [];
      const services = await resp.json();
      return Array.isArray(services) ? services : [];
    } catch (_) {
      return [];
    }
  },

  async toggleWatchdog(enabled) {
    const services = await this._loadServices();
    const fallback = document.getElementById('inp-target')?.value?.trim();
    const selectedServices = services.length ? services : (fallback ? [fallback] : []);

    if (enabled && !selectedServices.length) {
      throw new Error('No services available to monitor. Register/select a service first.');
    }

    const response = await fetch(`${this._getEndpoint()}/api/watchdog`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        enabled,
        services: selectedServices,
        check_interval_seconds: 60,
        lookback_minutes: 15,
        anomaly_threshold: 0.7,
      }),
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const data = await response.json();
    this.enabled = !!data.enabled;
    return data;
  },

  async pollAlerts(limit = 15) {
    const response = await fetch(`${this._getEndpoint()}/api/watchdog/alerts?limit=${limit}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    this.alerts = data.alerts || [];
    return this.alerts;
  },

  async clearAlerts() {
    const response = await fetch(`${this._getEndpoint()}/api/watchdog/alerts`, {
      method: 'DELETE',
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    this.alerts = [];
  },

  _build() {
    if (this._built) return;
    if (document.getElementById('watchdog-section')) {
      this._built = true;
      return;
    }

    const section = document.createElement('section');
    section.id = 'watchdog-section';
    section.innerHTML = `
      <div class="llm-bar">
        <span class="llm-title">WATCHDOG</span>
        <button class="llm-close" onclick="WatchdogPanel.toggle()" title="Close">✕</button>
      </div>
      <div class="llm-body">
        <div class="llm-note">
          Monitor services continuously and surface anomalies without manual queries.
        </div>
        <div class="watchdog-panel">
          <div style="display:flex; gap:8px; flex-wrap:wrap;">
            <button class="btn-watchdog-toggle" id="btn-watchdog-toggle">▶ Start Watchdog</button>
            <button class="btn-secondary" id="btn-watchdog-refresh">Refresh Alerts</button>
            <button class="btn-secondary" id="btn-watchdog-clear">Clear Alerts</button>
          </div>
          <div class="watchdog-alerts" id="watchdog-alerts"></div>
          <span class="llm-msg" id="watchdog-msg"></span>
        </div>
      </div>
    `;

    const main = document.querySelector('main');
    document.body.insertBefore(section, main);

    const toggleBtn = document.getElementById('btn-watchdog-toggle');
    const refreshBtn = document.getElementById('btn-watchdog-refresh');
    const clearBtn = document.getElementById('btn-watchdog-clear');

    toggleBtn.addEventListener('click', async () => {
      try {
        const next = !this.enabled;
        await this.toggleWatchdog(next);
        this._renderToggle();
        this._flash(this.enabled ? 'Watchdog started' : 'Watchdog stopped');
      } catch (err) {
        this._flash(`Failed: ${err.message}`, true);
      }
    });

    refreshBtn.addEventListener('click', async () => {
      try {
        await this.pollAlerts();
        this._renderAlerts();
      } catch (err) {
        this._flash(`Failed: ${err.message}`, true);
      }
    });

    clearBtn.addEventListener('click', async () => {
      try {
        await this.clearAlerts();
        this._renderAlerts();
        this._flash('Alerts cleared');
      } catch (err) {
        this._flash(`Failed: ${err.message}`, true);
      }
    });

    this._built = true;
    this._renderToggle();
    this._renderAlerts();
  },

  toggle() {
    this._build();
    const open = document.getElementById('watchdog-section').classList.toggle('visible');
    if (open && typeof Sidebar !== 'undefined') Sidebar.closeOtherPanels('watchdog');
    if (typeof Sidebar !== 'undefined') Sidebar.syncClusterBar();
  },

  isOpen() {
    const sec = document.getElementById('watchdog-section');
    return !!(sec && sec.classList.contains('visible'));
  },

  _renderToggle() {
    const toggleBtn = document.getElementById('btn-watchdog-toggle');
    if (!toggleBtn) return;
    toggleBtn.textContent = this.enabled ? '⏹ Stop Watchdog' : '▶ Start Watchdog';
    toggleBtn.classList.toggle('active', this.enabled);
  },

  _renderAlerts() {
    const container = document.getElementById('watchdog-alerts');
    if (!container) return;

    if (!this.alerts.length) {
      container.innerHTML = '<div class="alerts-empty">No anomalies detected</div>';
      return;
    }

    container.innerHTML = `<div class="alerts-list">${this.alerts.map((alert) => `
      <div class="alert-item alert-severity-${escHtml(alert.severity || 'info')}">
        <div class="alert-header">
          <span class="alert-service">${escHtml(alert.service || '')}</span>
          <span class="alert-type">${escHtml(String(alert.anomaly_type || '').replace(/_/g, ' '))}</span>
          <span class="alert-time">${escHtml(fmtTime(alert.detected_at))}</span>
        </div>
        <div class="alert-summary">${escHtml(alert.summary || '')}</div>
        <div class="alert-confidence">Confidence: ${Math.round((alert.confidence || 0) * 100)}%</div>
      </div>
    `).join('')}</div>`;
  },

  _flash(msg, isErr = false) {
    const el = document.getElementById('watchdog-msg');
    if (!el) return;
    el.textContent = msg;
    el.className = `llm-msg ${isErr ? 'err' : 'ok'}`;
    clearTimeout(this._msgTimer);
    this._msgTimer = setTimeout(() => {
      el.textContent = '';
      el.className = 'llm-msg';
    }, 2500);
  },
};

window.WatchdogPanel = WatchdogPanel;
