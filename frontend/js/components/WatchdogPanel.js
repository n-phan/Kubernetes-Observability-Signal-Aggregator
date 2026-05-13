const WatchdogPanel = {
  _built: false,

  init() {
    if (this._built) return;
    const section = document.createElement('section');
    section.id = 'watchdog-section';
    section.innerHTML = `
      <div class="conn-bar">
        <span class="conn-title">WATCHDOG</span>
        <button class="conn-close" onclick="WatchdogPanel.toggle()" title="Close">✕</button>
      </div>
      <div class="conn-body">
        <div class="conn-note">Run continuous anomaly scans across registered services and send email alerts when SMTP is configured.</div>
        <div class="conn-form">
          <div class="field">
            <label for="wd-enabled">Enabled</label>
            <select id="wd-enabled">
              <option value="true">On</option>
              <option value="false">Off</option>
            </select>
          </div>
          <div class="field">
            <label for="wd-interval">Interval (sec)</label>
            <input id="wd-interval" type="number" min="15" value="60" />
          </div>
          <div class="field">
            <label for="wd-lookback">Lookback (min)</label>
            <input id="wd-lookback" type="number" min="1" value="15" />
          </div>
          <div class="field">
            <label for="wd-threshold">Threshold (0-1)</label>
            <input id="wd-threshold" type="number" step="0.05" min="0" max="1" value="0.7" />
          </div>
        </div>
        <div class="conn-actions">
          <button class="btn-query" id="wd-apply">Apply</button>
          <button class="btn-mock" id="wd-clear">Clear alerts</button>
          <span class="conn-msg" id="wd-msg"></span>
        </div>
        <div id="wd-status" class="env-current" style="margin-top:8px">—</div>
        <div id="wd-alerts" class="watchdog-alerts"></div>
      </div>
    `;
    const main = document.querySelector('main');
    document.body.insertBefore(section, main);

    $('wd-apply').addEventListener('click', () => this.apply());
    $('wd-clear').addEventListener('click', () => this.clearAlerts());

    this._built = true;
  },

  isOpen() {
    const s = $('watchdog-section');
    return !!(s && s.classList.contains('visible'));
  },

  async refresh() {
    this.init();
    const endpoint = ($('inp-endpoint')?.value || 'http://localhost:8080').replace(/\/$/, '');
    const msg = $('wd-msg');
    try {
      const statusResp = await fetch(`${endpoint}/api/watchdog`);
      if (!statusResp.ok) throw new Error(`HTTP ${statusResp.status}`);
      const status = await statusResp.json();
      $('wd-enabled').value = status.enabled ? 'true' : 'false';
      $('wd-interval').value = status.interval_seconds ?? 60;
      $('wd-lookback').value = status.lookback_minutes ?? 15;
      $('wd-threshold').value = status.anomaly_threshold ?? 0.7;
      $('wd-status').textContent = `Status: ${status.enabled ? 'running' : 'stopped'} · alerts: ${status.alerts ?? 0}`;
      document.dispatchEvent(new CustomEvent('obs:watchdog-status', { detail: { enabled: !!status.enabled } }));

      const alertsResp = await fetch(`${endpoint}/api/watchdog/alerts`);
      if (!alertsResp.ok) throw new Error(`HTTP ${alertsResp.status}`);
      const data = await alertsResp.json();
      this._renderAlerts(data.alerts || []);
      if (msg) msg.textContent = '';
    } catch (err) {
      if (msg) {
        msg.className = 'conn-msg';
        msg.textContent = `Refresh failed: ${err.message}`;
      }
    }
  },

  _renderAlerts(alerts) {
    const host = $('wd-alerts');
    if (!host) return;
    if (!alerts.length) {
      host.innerHTML = '<div class="sb-sub-empty">No alerts yet</div>';
      return;
    }
    host.innerHTML = alerts.slice(0, 25).map(a => `
      <div class="watchdog-alert ${a.severity || 'info'}">
        <div><strong>${escHtml(a.service || 'service')}</strong> · score ${Number(a.score || 0).toFixed(2)} · ${escHtml(a.severity || 'info')}</div>
        <div>${escHtml(a.summary || '')}</div>
        <div class="watchdog-alert-time">${fmtDateTime(a.created_at)}</div>
      </div>
    `).join('');
  },

  async apply() {
    const endpoint = ($('inp-endpoint')?.value || 'http://localhost:8080').replace(/\/$/, '');
    const payload = {
      enabled: $('wd-enabled').value === 'true',
      interval_seconds: parseInt($('wd-interval').value, 10) || 60,
      lookback_minutes: parseInt($('wd-lookback').value, 10) || 15,
      anomaly_threshold: parseFloat($('wd-threshold').value) || 0.7,
    };
    try {
      const resp = await fetch(`${endpoint}/api/watchdog`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const txt = await resp.text();
        throw new Error(txt || `HTTP ${resp.status}`);
      }
      $('wd-msg').className = 'conn-msg ok';
      $('wd-msg').textContent = payload.enabled ? 'Watchdog started' : 'Watchdog stopped';
      await this.refresh();
    } catch (err) {
      $('wd-msg').className = 'conn-msg';
      $('wd-msg').textContent = `Apply failed: ${err.message}`;
    }
  },

  async clearAlerts() {
    const endpoint = ($('inp-endpoint')?.value || 'http://localhost:8080').replace(/\/$/, '');
    try {
      await fetch(`${endpoint}/api/watchdog/alerts`, { method: 'DELETE' });
      await this.refresh();
    } catch (_) {}
  },

  toggle() {
    this.init();
    const open = $('watchdog-section').classList.toggle('visible');
    if (open) {
      if (typeof Sidebar !== 'undefined') Sidebar.closeOtherPanels('watchdog');
      this.refresh();
    }
    if (typeof Sidebar !== 'undefined') Sidebar.syncClusterBar();
  },
};

window.WatchdogPanel = WatchdogPanel;
