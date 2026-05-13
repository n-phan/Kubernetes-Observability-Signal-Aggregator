// ── Sidebar Component ─────────────────────────────────────────────────────────
//
// Left-hand navigation rail. For now it holds two expandable items:
//   • Service — expands to list every registered service (from /services).
//               Clicking a service selects it as the query target; a "Manage"
//               entry opens the existing ServicePanel.
//   • Setting — placeholder, not yet implemented.
//
// Usage (called once from index.html after the other components load):
//   Sidebar.init();

const Sidebar = {

  init() {
    const host = document.getElementById('sidebar');
    if (!host) return;
    host.innerHTML = `
      <div class="sb-brand">
        <span class="sb-brand-icon">◎</span>
        <span class="sb-brand-text">OBS</span>
        <button class="sb-collapse" id="sb-collapse" title="Collapse / expand">«</button>
      </div>
      <nav class="sb-nav">
        <div class="sb-item" data-key="service">
          <button class="sb-item-head" id="sb-head-service">
            <span class="sb-item-icon">☰</span>
            <span class="sb-item-label">Service</span>
            <span class="sb-item-chevron">▾</span>
          </button>
          <div class="sb-sub" id="sb-sub-service">
            <div class="sb-sub-empty">Click to load services…</div>
          </div>
        </div>
        <div class="sb-item sb-item-leaf" data-key="history">
          <button class="sb-item-head" id="sb-head-history">
            <span class="sb-item-icon">↺</span>
            <span class="sb-item-label">History</span>
          </button>
        </div>
        <div class="sb-item" data-key="setting">
          <button class="sb-item-head" id="sb-head-setting">
            <span class="sb-item-icon">⚙</span>
            <span class="sb-item-label">Setting</span>
            <span class="sb-item-chevron">▾</span>
          </button>
          <div class="sb-sub" id="sb-sub-setting">
            <button class="sb-svc" id="sb-conn-config">API Endpoint &amp; Namespace</button>
            <button class="sb-svc" id="sb-llm-config">Config LLM</button>
          </div>
        </div>
        <div class="sb-cta-wrap">
          <button class="sb-watchdog-cta" id="sb-watchdog-cta">
            <span class="sb-watchdog-cta-label">Watchdog</span>
            <span class="sb-watchdog-cta-pill" id="sb-watchdog-pill">OFF</span>
          </button>
        </div>
      </nav>
    `;
    document.getElementById('sb-head-service').addEventListener('click', () => this.toggleItem('service'));
    document.getElementById('sb-head-setting').addEventListener('click', () => this.toggleItem('setting'));
    document.getElementById('sb-collapse').addEventListener('click', () => this.toggleCollapsed());
    // Sidebar nav items act like "go to page" rather than toggle: clicking the
    // currently-open page is a no-op (don't close the right-hand view from
    // under the user). Switching from one open panel to another still works
    // because each panel's toggle() calls closeOtherPanels() on open.
    document.getElementById('sb-conn-config').addEventListener('click', () => {
      if (window.ConnectionPanel && !ConnectionPanel.isOpen()) ConnectionPanel.toggle();
    });
    document.getElementById('sb-llm-config').addEventListener('click', () => {
      if (window.LlmConfigPanel && !LlmConfigPanel.isOpen()) LlmConfigPanel.toggle();
    });
    document.getElementById('sb-watchdog-cta').addEventListener('click', () => {
      if (window.WatchdogPanel && !WatchdogPanel.isOpen()) WatchdogPanel.toggle();
    });
    document.getElementById('sb-head-history').addEventListener('click', () => {
      if (window.HistoryListPanel && HistoryListPanel.isOpen()) return;
      const bar = document.getElementById('sidebar');
      if (bar && bar.classList.contains('collapsed')) this.toggleCollapsed();
      document.querySelectorAll('#sidebar .sb-item.open').forEach(el => el.classList.remove('open'));
      if (window.HistoryListPanel) HistoryListPanel.toggle();
    });

    // Keep the Service list in sync when services are added/removed elsewhere.
    document.addEventListener('obs:services-changed', () => {
      const item = document.querySelector('.sb-item[data-key="service"]');
      if (item && item.classList.contains('open')) this.loadServices();
    });

    document.addEventListener('obs:watchdog-status', (event) => {
      this.setWatchdogIndicator(!!event?.detail?.enabled);
    });

    this.refreshWatchdogIndicator();
    if (this._watchdogPoll) clearInterval(this._watchdogPoll);
    this._watchdogPoll = setInterval(() => this.refreshWatchdogIndicator(), 10000);
  },

  _watchdogPoll: null,

  setWatchdogIndicator(enabled) {
    const cta = document.getElementById('sb-watchdog-cta');
    const pill = document.getElementById('sb-watchdog-pill');
    if (!cta || !pill) return;
    cta.classList.toggle('on', enabled);
    pill.textContent = enabled ? 'ON' : 'OFF';
  },

  async refreshWatchdogIndicator() {
    const endpoint = ($('inp-endpoint')?.value || 'http://localhost:8080').trim().replace(/\/$/, '');
    try {
      const resp = await fetch(`${endpoint}/api/watchdog`);
      if (!resp.ok) return;
      const status = await resp.json();
      this.setWatchdogIndicator(!!status.enabled);
    } catch (_) {
      // Keep last-known indicator state when backend is temporarily unreachable.
    }
  },

  // Collapse the rail to an icon-only strip, or expand it back.
  toggleCollapsed() {
    const bar = document.getElementById('sidebar');
    if (!bar) return;
    const collapsed = bar.classList.toggle('collapsed');
    document.documentElement.style.setProperty('--sidebar-w', collapsed ? '56px' : '200px');
    const btn = document.getElementById('sb-collapse');
    if (btn) btn.textContent = collapsed ? '»' : '«';
  },

  // The content-area panels that take over the right-hand side. Only one should
  // ever be visible at a time — each panel's toggle() calls closeOtherPanels()
  // when opening, and the sidebar nav calls it (with no exception) to back out.
  _CONTENT_PANELS: [
    { name: 'service',    isOpen: () => { const s = document.getElementById('sp-section'); return !!(s && s.classList.contains('visible')); }, close: () => window.ServicePanel    && ServicePanel.toggle()    },
    { name: 'demo',       isOpen: () => { const s = document.getElementById('demo-section'); return !!(s && s.classList.contains('visible')); }, close: () => window.DemoPanel       && DemoPanel.toggle()       },
    { name: 'connection', isOpen: () => !!(window.ConnectionPanel  && ConnectionPanel.isOpen()),  close: () => ConnectionPanel.toggle()  },
    { name: 'environment', isOpen: () => !!(window.EnvironmentPanel && EnvironmentPanel.isOpen()), close: () => EnvironmentPanel.toggle() },
    { name: 'llm',        isOpen: () => !!(window.LlmConfigPanel   && LlmConfigPanel.isOpen()),   close: () => LlmConfigPanel.toggle()   },
    { name: 'watchdog',   isOpen: () => !!(window.WatchdogPanel    && WatchdogPanel.isOpen()),    close: () => WatchdogPanel.toggle()    },
    { name: 'history',    isOpen: () => !!(window.HistoryListPanel && HistoryListPanel.isOpen()), close: () => HistoryListPanel.toggle() },
  ],

  // True if any content-area panel is open.
  _panelOpen() {
    return this._CONTENT_PANELS.some(p => p.isOpen());
  },
  // Close every content panel except the named one (pass null to close all).
  closeOtherPanels(except) {
    this._CONTENT_PANELS.forEach(p => { if (p.name !== except && p.isOpen()) p.close(); });
  },
  _closePanels() { this.closeOtherPanels(null); },
  // When a content panel takes over the right-hand side, hide the Cluster Status
  // bar AND the query-results area (#main) so only the panel shows — not the
  // panel stacked on top of the previous query. Every panel's toggle() calls
  // this, so the state is always consistent regardless of toggle order.
  syncClusterBar() {
    const open = this._panelOpen();
    const cb = document.getElementById('cluster-bar');
    if (cb) cb.style.display = open ? 'none' : '';
    const main = document.getElementById('main');
    if (main) main.style.display = open ? 'none' : '';
  },

  // Toggle a sidebar accordion section (Service / Setting). This only shows/hides
  // the submenu — it does NOT touch the content panels or the results area, so
  // expanding Setting while (say) Config LLM is open leaves Config LLM open.
  toggleItem(key) {
    const bar = document.getElementById('sidebar');
    // If the rail is collapsed, expand it first so the submenu has room.
    if (bar && bar.classList.contains('collapsed')) this.toggleCollapsed();
    const item = document.querySelector(`.sb-item[data-key="${key}"]`);
    if (!item) return;
    const opening = !item.classList.contains('open');
    // Accordion: expanding one section collapses the others.
    if (opening) {
      document.querySelectorAll('#sidebar .sb-item.open').forEach(el => { if (el !== item) el.classList.remove('open'); });
    }
    item.classList.toggle('open', opening);
    if (key === 'service' && opening) this.loadServices();
  },

  async loadServices() {
    const sub = document.getElementById('sb-sub-service');
    if (!sub) return;
    sub.innerHTML = `<div class="sb-sub-empty">Loading…</div>`;

    const endpoint = ($('inp-endpoint').value || '').trim().replace(/\/$/, '');
    let services = [];
    try {
      const resp = await fetch(`${endpoint}/services`);
      if (resp.ok) services = await resp.json();
    } catch (_) { /* leave empty */ }

    const current = $('inp-target') ? $('inp-target').value : '';
    const rows = services.length
      ? services.map(s =>
          `<button class="sb-svc${s === current ? ' active' : ''}" data-svc="${escHtml(s)}">${escHtml(s)}</button>`
        ).join('')
      : `<div class="sb-sub-empty">No services registered</div>`;

    sub.innerHTML = rows + `<button class="sb-svc sb-manage" id="sb-manage">+ Manage services…</button>`;

    sub.querySelectorAll('.sb-svc[data-svc]').forEach(btn =>
      btn.addEventListener('click', () => this.selectService(btn.dataset.svc))
    );
    const mgr = document.getElementById('sb-manage');
    if (mgr) mgr.addEventListener('click', () => {
      const sp = document.getElementById('sp-section');
      if (window.ServicePanel && !(sp && sp.classList.contains('visible'))) ServicePanel.toggle();
    });
  },

  // Select a service as the active query target and immediately run a query
  // against it (one click = pick + query). RCA stays opt-in via the Analyze button.
  selectService(name) {
    const sel = $('inp-target');
    if (sel) {
      if (![...sel.options].some(o => o.value === name)) {
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name;
        sel.appendChild(opt);
      }
      sel.value = name;
    }
    const lbl = $('target-label');
    if (lbl) { lbl.textContent = name; lbl.classList.add('set'); }
    document.querySelectorAll('#sb-sub-service .sb-svc[data-svc]').forEach(b =>
      b.classList.toggle('active', b.dataset.svc === name)
    );
    // Refresh the cluster panel so its "Pods of <target>" section updates.
    if (typeof ClusterStatusPanel !== 'undefined') ClusterStatusPanel.refresh();
    // Auto-run a query for the picked service (no-op if one is already in flight).
    if (typeof runQuery === 'function') runQuery();
  },
};
