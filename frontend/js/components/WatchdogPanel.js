/**
 * WatchdogPanel — Auto-watchdog mode control and alert display.
 *
 * Allows developers to enable background monitoring of services
 * and displays detected anomalies without manual queries.
 */

class WatchdogPanel {
  constructor() {
    this.enabled = false;
    this.alerts = [];
    this.pollInterval = null;
  }

  static instance = new WatchdogPanel();

  /**
   * Toggle watchdog on/off for specified services.
   */
  async toggleWatchdog(services, enabled) {
    try {
      const response = await fetch('/api/watchdog', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          enabled: enabled,
          services: services,
          check_interval_seconds: 60,
          lookback_minutes: 15,
          anomaly_threshold: 0.7,
        }),
      });

      const data = await response.json();
      this.enabled = data.enabled;
      console.log('Watchdog', data.enabled ? 'started' : 'stopped');
      return data;
    } catch (err) {
      console.error('Watchdog toggle failed:', err);
      return null;
    }
  }

  /**
   * Poll for new watchdog alerts.
   */
  async pollAlerts(limit = 10) {
    try {
      const response = await fetch(`/api/watchdog/alerts?limit=${limit}`);
      const data = await response.json();
      this.alerts = data.alerts || [];
      return this.alerts;
    } catch (err) {
      console.error('Failed to fetch watchdog alerts:', err);
      return [];
    }
  }

  /**
   * Clear all stored watchdog alerts.
   */
  async clearAlerts() {
    try {
      await fetch('/api/watchdog/alerts', { method: 'DELETE' });
      this.alerts = [];
    } catch (err) {
      console.error('Failed to clear alerts:', err);
    }
  }

  /**
   * Render watchdog control panel.
   */
  static renderControlPanel(services) {
    const panel = document.createElement('div');
    panel.className = 'panel watchdog-panel';

    const toggleBtn = document.createElement('button');
    toggleBtn.className = 'btn-watchdog-toggle';
    toggleBtn.textContent = WatchdogPanel.instance.enabled ? '⏹ Stop Watchdog' : '▶ Start Watchdog';
    toggleBtn.onclick = async () => {
      const newState = !WatchdogPanel.instance.enabled;
      await WatchdogPanel.instance.toggleWatchdog(services, newState);
      toggleBtn.textContent = newState ? '⏹ Stop Watchdog' : '▶ Start Watchdog';
      toggleBtn.classList.toggle('active', newState);
    };

    const alertsDiv = document.createElement('div');
    alertsDiv.className = 'watchdog-alerts';
    alertsDiv.textContent = 'No alerts yet';

    const refreshBtn = document.createElement('button');
    refreshBtn.className = 'btn-secondary';
    refreshBtn.textContent = 'Refresh Alerts';
    refreshBtn.onclick = async () => {
      await WatchdogPanel.instance.pollAlerts();
      WatchdogPanel.instance._renderAlerts(alertsDiv);
    };

    const clearBtn = document.createElement('button');
    clearBtn.className = 'btn-secondary';
    clearBtn.textContent = 'Clear All';
    clearBtn.onclick = async () => {
      await WatchdogPanel.instance.clearAlerts();
      alertsDiv.textContent = 'All alerts cleared';
    };

    panel.appendChild(toggleBtn);
    panel.appendChild(document.createElement('br'));
    panel.appendChild(refreshBtn);
    panel.appendChild(clearBtn);
    panel.appendChild(document.createElement('br'));
    panel.appendChild(alertsDiv);

    return panel;
  }

  /**
   * Render alert list in the given container.
   */
  _renderAlerts(container) {
    if (this.alerts.length === 0) {
      container.innerHTML = '<div class="alerts-empty">No anomalies detected</div>';
      return;
    }

    const alertsHtml = this.alerts.map((alert) => {
      const severityClass = `alert-severity-${alert.severity}`;
      const timestamp = new Date(alert.detected_at).toLocaleTimeString();
      return `
        <div class="alert-item ${severityClass}">
          <div class="alert-header">
            <span class="alert-service">${alert.service}</span>
            <span class="alert-type">${alert.anomaly_type.replace(/_/g, ' ')}</span>
            <span class="alert-time">${timestamp}</span>
          </div>
          <div class="alert-summary">${alert.summary}</div>
          <div class="alert-confidence">Confidence: ${(alert.confidence * 100).toFixed(0)}%</div>
        </div>
      `;
    }).join('');

    container.innerHTML = `<div class="alerts-list">${alertsHtml}</div>`;
  }
}

// Register in global panel registry
if (window.PANEL_REGISTRY) {
  window.PANEL_REGISTRY.set('watchdog', WatchdogPanel.renderControlPanel);
}
