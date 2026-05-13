/**
 * EnvironmentPanel — Multi-environment support.
 *
 * Allows developers to switch between local, staging, and production
 * observability backends without restarting the application.
 */

class EnvironmentPanel {
  constructor() {
    this.currentEnvironment = localStorage.getItem('observability-env') || 'local';
  }

  static instance = new EnvironmentPanel();

  /**
   * Set the current environment and store in localStorage.
   */
  setEnvironment(env) {
    const validEnvs = ['local', 'staging', 'production'];
    if (!validEnvs.includes(env)) {
      console.error('Invalid environment:', env);
      return false;
    }

    this.currentEnvironment = env;
    localStorage.setItem('observability-env', env);
    console.log('Environment switched to:', env);

    // Notify backend (optional — for auditing)
    fetch('/api/environment', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ environment: env }),
    }).catch((err) => console.warn('Failed to notify backend of env change:', err));

    return true;
  }

  /**
   * Get the current environment.
   */
  getEnvironment() {
    return this.currentEnvironment;
  }

  /**
   * Render environment selector panel.
   */
  static renderSelector() {
    const panel = document.createElement('div');
    panel.className = 'panel environment-panel';

    const label = document.createElement('label');
    label.textContent = 'Environment:';

    const select = document.createElement('select');
    select.className = 'env-select';
    const envs = ['local', 'staging', 'production'];
    envs.forEach((env) => {
      const option = document.createElement('option');
      option.value = env;
      option.textContent = env.charAt(0).toUpperCase() + env.slice(1);
      option.selected = EnvironmentPanel.instance.currentEnvironment === env;
      select.appendChild(option);
    });

    select.onchange = (e) => {
      EnvironmentPanel.instance.setEnvironment(e.target.value);
      // Show confirmation
      const badge = document.createElement('div');
      badge.className = 'env-badge';
      badge.textContent = `✓ Switched to ${e.target.value}`;
      panel.appendChild(badge);
      setTimeout(() => badge.remove(), 2000);
    };

    const info = document.createElement('div');
    info.className = 'env-info';
    info.innerHTML = `
      <small>
        Current: <strong>${EnvironmentPanel.instance.currentEnvironment.toUpperCase()}</strong>
        <br/>
        (Switch to view metrics, logs, and traces from different clusters)
      </small>
    `;

    panel.appendChild(label);
    panel.appendChild(select);
    panel.appendChild(info);

    return panel;
  }
}

// Register in global panel registry
if (window.PANEL_REGISTRY) {
  window.PANEL_REGISTRY.set('environment', EnvironmentPanel.renderSelector);
}
