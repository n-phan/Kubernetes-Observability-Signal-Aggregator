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

        <div class="wd-notify-section">
          <div class="wd-notify-title">Notifications</div>
          <div class="wd-notify-note">Channels fired when an anomaly exceeds the threshold. Empty fields disable that channel.</div>

          <div class="wd-notify-block">
            <div class="wd-notify-head">
              <span class="wd-notify-label">Email (SMTP)</span>
              <label class="wd-toggle"><input id="wd-email-enabled" type="checkbox" /><span class="wd-toggle-slider"></span></label>
            </div>
            <div class="wd-notify-fields">
              <div class="field"><label>SMTP host</label><input id="wd-email-host" type="text" placeholder="smtp.gmail.com" autocomplete="off" /></div>
              <div class="field"><label>Port</label><input id="wd-email-port" type="number" min="1" value="587" /></div>
              <div class="field"><label>Username</label><input id="wd-email-user" type="text" autocomplete="off" /></div>
              <div class="field"><label>Password</label><input id="wd-email-pass" type="password" autocomplete="off" /></div>
              <div class="field"><label>From</label><input id="wd-email-from" type="email" autocomplete="off" /></div>
              <div class="field"><label>To (alert)</label><input id="wd-email-to" type="email" autocomplete="off" /></div>
              <div class="field"><label>STARTTLS</label><select id="wd-email-starttls"><option value="true">On</option><option value="false">Off</option></select></div>
            </div>
          </div>

          <div class="wd-notify-block">
            <div class="wd-notify-head">
              <span class="wd-notify-label">Bark (iOS push)</span>
              <span class="wd-notify-hint">Server: https://api.day.app</span>
              <label class="wd-toggle"><input id="wd-bark-enabled" type="checkbox" /><span class="wd-toggle-slider"></span></label>
            </div>
            <div class="wd-notify-fields">
              <div class="field wd-notify-wide"><label>Device key</label><input id="wd-bark-key" type="password" placeholder="from the Bark app" autocomplete="off" /></div>
            </div>
          </div>

          <div class="conn-actions">
            <button class="btn-query" id="wd-notify-save">Save notifications</button>
            <button class="btn-mock"  id="wd-notify-test">Send test</button>
            <span class="conn-msg" id="wd-notify-msg"></span>
          </div>
        </div>

        <div id="wd-alerts" class="watchdog-alerts"></div>
      </div>
    `;
    const main = document.querySelector('main');
    document.body.insertBefore(section, main);

    $('wd-apply').addEventListener('click', () => this.apply());
    $('wd-clear').addEventListener('click', () => this.clearAlerts());

    // Same dirty-tracking pattern as the notification form: Apply is dim until
    // any watchdog field is edited, then lights up.
    ['wd-enabled','wd-interval','wd-lookback','wd-threshold'].forEach(id => {
      $(id).addEventListener('input',  () => this._updateApplyDirty());
      $(id).addEventListener('change', () => this._updateApplyDirty());
    });
    $('wd-notify-save').addEventListener('click', () => this.saveNotifications());
    $('wd-notify-test').addEventListener('click', () => this.testNotifications());
    // Enable/disable toggles auto-apply — flipping them is a single boolean
    // change, no point requiring an extra Save click. Other fields still need
    // Save (they're not committed until the form is filled out completely).
    $('wd-email-enabled').addEventListener('change', () => this.saveNotifications());
    $('wd-bark-enabled').addEventListener('change',  () => this.saveNotifications());

    // Dirty tracking: Save button is dim until any text field is edited. The
    // baseline is reset after every load/save so the button reflects "unsaved
    // changes vs server" rather than "any input at all".
    [
      'wd-email-host','wd-email-port','wd-email-user','wd-email-pass',
      'wd-email-from','wd-email-to','wd-email-starttls','wd-bark-key',
    ].forEach(id => $(id).addEventListener('input', () => this._updateDirty()));
    $('wd-email-starttls').addEventListener('change', () => this._updateDirty());
    this._updateDirty();

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
      this._cleanWatchdogSnapshot = this._watchdogSnapshot();
      this._updateApplyDirty();
      document.dispatchEvent(new CustomEvent('obs:watchdog-status', { detail: { enabled: !!status.enabled } }));

      const alertsResp = await fetch(`${endpoint}/api/watchdog/alerts`);
      if (!alertsResp.ok) throw new Error(`HTTP ${alertsResp.status}`);
      const data = await alertsResp.json();
      this._renderAlerts(data.alerts || []);
      await this._refreshNotifications(endpoint);
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

  // ── Notifications ────────────────────────────────────────────────────────
  _formSnapshot() {
    return JSON.stringify(this._collectNotifications());
  },

  _watchdogSnapshot() {
    return JSON.stringify({
      enabled:  $('wd-enabled').value,
      interval: $('wd-interval').value,
      lookback: $('wd-lookback').value,
      threshold: $('wd-threshold').value,
    });
  },

  _updateDirty() {
    const btn = $('wd-notify-save');
    if (!btn) return;
    const dirty = this._cleanSnapshot !== undefined && this._formSnapshot() !== this._cleanSnapshot;
    btn.classList.toggle('btn-dim', !dirty);
    btn.disabled = !dirty;
  },

  _updateApplyDirty() {
    const btn = $('wd-apply');
    if (!btn) return;
    const dirty = this._cleanWatchdogSnapshot !== undefined && this._watchdogSnapshot() !== this._cleanWatchdogSnapshot;
    btn.classList.toggle('btn-dim', !dirty);
    btn.disabled = !dirty;
  },

  async _refreshNotifications(endpoint) {
    try {
      const resp = await fetch(`${endpoint}/api/watchdog/notifications`);
      if (!resp.ok) return;
      const cfg = await resp.json();
      const e = cfg.email || {};
      $('wd-email-enabled').checked = !!e.enabled;
      $('wd-email-host').value      = e.smtp_host       || '';
      $('wd-email-port').value      = e.smtp_port       ?? 587;
      $('wd-email-user').value      = e.smtp_username   || '';
      $('wd-email-pass').value      = e.smtp_password   || '';  // server returns ******** if set
      $('wd-email-from').value      = e.smtp_from_email || '';
      $('wd-email-to').value        = e.alert_email     || '';
      $('wd-email-starttls').value  = e.smtp_use_starttls === false ? 'false' : 'true';
      const b = cfg.bark || {};
      $('wd-bark-enabled').checked = !!b.enabled;
      $('wd-bark-key').value       = b.device_key || '';
      this._cleanSnapshot = this._formSnapshot();
      this._updateDirty();
    } catch (_) { /* leave fields as-is */ }
  },

  _collectNotifications() {
    // Empty password / device-key fields mean "no change" — the server masks
    // stored secrets as "********" in GET, and we send back whatever's in the
    // box. The server's merge_incoming() treats the mask as "keep existing".
    return {
      email: {
        enabled:          $('wd-email-enabled').checked,
        smtp_host:        $('wd-email-host').value.trim() || null,
        smtp_port:        parseInt($('wd-email-port').value, 10) || 587,
        smtp_username:    $('wd-email-user').value.trim() || null,
        smtp_password:    $('wd-email-pass').value,
        smtp_from_email:  $('wd-email-from').value.trim() || null,
        smtp_use_starttls: $('wd-email-starttls').value === 'true',
        alert_email:      $('wd-email-to').value.trim() || null,
      },
      bark: {
        enabled:    $('wd-bark-enabled').checked,
        device_key: $('wd-bark-key').value,
      },
    };
  },

  async saveNotifications() {
    const endpoint = ($('inp-endpoint')?.value || 'http://localhost:8080').replace(/\/$/, '');
    const msg = $('wd-notify-msg');
    try {
      const resp = await fetch(`${endpoint}/api/watchdog/notifications`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(this._collectNotifications()),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      msg.className = 'conn-msg ok';
      msg.textContent = 'Saved ✓';
      await this._refreshNotifications(endpoint);   // refresh also re-snapshots
    } catch (err) {
      msg.className = 'conn-msg';
      msg.textContent = `Save failed: ${err.message}`;
    }
  },

  async testNotifications() {
    const endpoint = ($('inp-endpoint')?.value || 'http://localhost:8080').replace(/\/$/, '');
    const msg = $('wd-notify-msg');
    msg.className = 'conn-msg';
    msg.textContent = 'Sending test…';
    try {
      // Persist whatever's in the form first so the test uses current input.
      await fetch(`${endpoint}/api/watchdog/notifications`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(this._collectNotifications()),
      });
      const resp = await fetch(`${endpoint}/api/watchdog/notifications/test`, { method: 'POST' });
      const data = await resp.json();
      const sent = (data.sent || []);
      msg.className = sent.length ? 'conn-msg ok' : 'conn-msg';
      msg.textContent = sent.length
        ? `Sent via: ${sent.join(', ')}`
        : 'No channels fired — check enable toggles and required fields. See aggregator logs for details.';
    } catch (err) {
      msg.textContent = `Test failed: ${err.message}`;
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
