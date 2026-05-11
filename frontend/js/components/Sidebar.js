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
      </nav>
    `;
    document.getElementById('sb-head-service').addEventListener('click', () => this.toggleItem('service'));
    document.getElementById('sb-head-setting').addEventListener('click', () => this.toggleItem('setting'));
    document.getElementById('sb-collapse').addEventListener('click', () => this.toggleCollapsed());
    document.getElementById('sb-conn-config').addEventListener('click', () => {
      if (window.ConnectionPanel) ConnectionPanel.toggle();
    });
    document.getElementById('sb-llm-config').addEventListener('click', () => {
      if (window.LlmConfigPanel) LlmConfigPanel.toggle();
    });
    document.getElementById('sb-head-history').addEventListener('click', () => {
      const bar = document.getElementById('sidebar');
      if (bar && bar.classList.contains('collapsed')) this.toggleCollapsed();
      if (window.HistoryListPanel) HistoryListPanel.toggle();
    });

    // Keep the Service list in sync when services are added/removed elsewhere.
    document.addEventListener('obs:services-changed', () => {
      const item = document.querySelector('.sb-item[data-key="service"]');
      if (item && item.classList.contains('open')) this.loadServices();
    });
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

  // True if a content-area panel (Manage services / Connection / Config LLM / History) is open.
  _panelOpen() {
    const sp = document.getElementById('sp-section');
    return !!((sp && sp.classList.contains('visible'))
           || (window.ConnectionPanel && ConnectionPanel.isOpen())
           || (window.LlmConfigPanel && LlmConfigPanel.isOpen())
           || (window.HistoryListPanel && HistoryListPanel.isOpen()));
  },
  _closePanels() {
    const sp = document.getElementById('sp-section');
    if (sp && sp.classList.contains('visible') && window.ServicePanel) ServicePanel.toggle();
    if (window.ConnectionPanel && ConnectionPanel.isOpen()) ConnectionPanel.toggle();
    if (window.LlmConfigPanel && LlmConfigPanel.isOpen()) LlmConfigPanel.toggle();
    if (window.HistoryListPanel && HistoryListPanel.isOpen()) HistoryListPanel.toggle();
  },

  toggleItem(key) {
    const bar = document.getElementById('sidebar');
    // If the rail is collapsed, expand it first so the submenu has room.
    if (bar && bar.classList.contains('collapsed')) this.toggleCollapsed();
    // Interacting with the sidebar nav backs out of any open content panel.
    const wasPanelOpen = this._panelOpen();
    this._closePanels();
    const item = document.querySelector(`.sb-item[data-key="${key}"]`);
    if (!item) return;
    // If we just dismissed a panel, leave the item expanded instead of toggling shut.
    const opening = wasPanelOpen ? true : !item.classList.contains('open');
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
    if (mgr) mgr.addEventListener('click', () => { if (window.ServicePanel) ServicePanel.toggle(); });
  },

  // Select a service as the active query target.
  selectService(name) {
    this._closePanels();
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
  },
};
