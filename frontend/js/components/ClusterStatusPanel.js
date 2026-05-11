// ── ClusterStatusPanel Component ──────────────────────────────────────────────
//
// Homepage status panel: CPU / memory / load / per-disk usage gauges plus
// network throughput, sourced from the aggregator's GET /cluster/status
// endpoint (which queries node-exporter metrics in Prometheus).
//
// Lives in #cluster-bar (outside #main) so it survives query re-renders, and
// refreshes itself on an interval.
//
// Usage (called once from index.html after api.js loads):
//   ClusterStatusPanel.init();

const ClusterStatusPanel = {
  REFRESH_MS: 10000,
  _timer: null,

  init() {
    const host = document.getElementById('cluster-bar');
    if (!host) return;
    host.innerHTML = `
      <div class="panel cluster-panel" id="cluster-panel">
        <div class="panel-header" id="cluster-panel-header">
          <span class="panel-title">Cluster Status</span>
          <span class="panel-count" id="cluster-panel-meta">loading…</span>
          <span class="panel-chevron">▾</span>
        </div>
        <div class="panel-body" id="cluster-panel-body">
          <div class="cluster-loading">Querying node-exporter via Prometheus…</div>
        </div>
      </div>
    `;
    document.getElementById('cluster-panel-header')
      .addEventListener('click', () => document.getElementById('cluster-panel').classList.toggle('collapsed'));

    this.refresh();
    if (this._timer) clearInterval(this._timer);
    this._timer = setInterval(() => this.refresh(), this.REFRESH_MS);
  },

  async refresh() {
    const endpoint = ($('inp-endpoint').value || '').trim().replace(/\/$/, '');
    const target = ($('inp-target') ? $('inp-target').value : '').trim();
    const ns = ($('inp-namespace') ? $('inp-namespace').value : '').trim() || 'default';
    const url = `${endpoint}/cluster/status`
      + (target ? `?target=${encodeURIComponent(target)}&namespace=${encodeURIComponent(ns)}` : '');
    let data;
    try {
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      data = await resp.json();
    } catch (err) {
      this._renderUnavailable(`Could not reach the aggregator: ${err.message}`);
      return;
    }
    if (!data || !data.available) {
      this._renderUnavailable(
        'No host metrics available. Make sure the <code>node-exporter</code> container is running and ' +
        'scraped by Prometheus (job <code>node</code> in <code>infra/prometheus.yml</code>).'
      );
      return;
    }
    this._render(data);
  },

  _renderUnavailable(msg) {
    const meta = document.getElementById('cluster-panel-meta');
    const body = document.getElementById('cluster-panel-body');
    if (meta) meta.textContent = 'unavailable';
    if (body) body.innerHTML = `<div class="cluster-loading">${msg}</div>`;
  },

  _render(data) {
    const meta = document.getElementById('cluster-panel-meta');
    const body = document.getElementById('cluster-panel-body');
    if (!body) return;

    const gauges = [];

    // CPU
    const cpu = data.cpu || {};
    gauges.push(this._gauge(
      cpu.pct, 'CPU',
      cpu.cores != null ? `${cpu.used_cores != null ? cpu.used_cores : '?'} / ${cpu.cores} cores` : ''
    ));

    // Memory
    const mem = data.memory || {};
    gauges.push(this._gauge(
      mem.pct, 'Memory',
      (mem.used != null && mem.total != null) ? `${fmtBytes(mem.used)} / ${fmtBytes(mem.total)}` : ''
    ));

    // Load (as a fraction of core count)
    const load = data.load || {};
    gauges.push(this._gauge(
      load.pct, 'Load',
      load.load1 != null ? `load1 ${load.load1.toFixed(2)}${load.cores != null ? ` · ${load.cores} cores` : ''}` : ''
    ));

    // Disks — one gauge per mount
    (data.disks || []).forEach(d => {
      gauges.push(this._gauge(
        d.pct, this._shortMount(d.mount),
        (d.used != null && d.total != null) ? `${fmtBytes(d.used)} / ${fmtBytes(d.total)}` : ''
      ));
    });

    const net = data.network || {};
    const netChips = `
      <div class="cluster-net">
        <span class="cluster-chip">▲ Up ${fmtRate(net.sent_bps)}</span>
        <span class="cluster-chip">▼ Down ${fmtRate(net.recv_bps)}</span>
      </div>
    `;

    // Pod status for the selected target (Kubernetes only — empty otherwise).
    let podsSection = '';
    const pods = data.pods || [];
    if (pods.length) {
      const rows = pods.map(p => {
        const waiting = (p.waiting_reasons || []).join(', ');
        const bad = (p.restarts > 0) || !!waiting || (p.phase && p.phase !== 'Running');
        return `
          <div class="cs-pod${bad ? ' bad' : ''}">
            <span class="cs-pod-name">${escHtml(p.name)}</span>
            <span class="cs-pod-node">node ${escHtml(p.node || '?')}</span>
            <span class="cs-pod-phase">${escHtml(p.phase || '?')}</span>
            <span class="cs-pod-restarts">↻ ${p.restarts}</span>
            ${waiting ? `<span class="cs-pod-warn">${escHtml(waiting)}</span>` : ''}
          </div>`;
      }).join('');
      podsSection = `
        <div class="cs-pods-title">Pods of ${escHtml(data.target || '')}${data.namespace ? ` · ns ${escHtml(data.namespace)}` : ''}</div>
        <div class="cs-pods">${rows}</div>`;
    }

    body.innerHTML = `<div class="cluster-gauges">${gauges.join('')}</div>${netChips}${podsSection}`;

    if (meta) {
      const host = (data.prometheus_url || '').replace(/^https?:\/\//, '');
      const podBit = pods.length ? ` · ${pods.length} pod(s)` : '';
      meta.textContent = `${(data.disks || []).length} disk(s)${podBit} · ${host} ▾`;
    }
  },

  // Build one SVG donut gauge.
  _gauge(pct, label, sub) {
    const valid = pct != null && !isNaN(pct);
    const clamped = valid ? Math.max(0, Math.min(100, pct)) : 0;
    const r = 38;
    const circ = 2 * Math.PI * r;
    const offset = circ * (1 - clamped / 100);
    const color = usageColor(valid ? pct : null);
    return `
      <div class="gauge" title="${escHtml(label)}: ${valid ? pct.toFixed(2) + '%' : 'no data'}">
        <svg class="gauge-svg" viewBox="0 0 100 100">
          <circle class="gauge-track" cx="50" cy="50" r="${r}"></circle>
          <circle class="gauge-arc" cx="50" cy="50" r="${r}"
                  stroke="${color}"
                  stroke-dasharray="${circ.toFixed(2)}"
                  stroke-dashoffset="${offset.toFixed(2)}"
                  transform="rotate(-90 50 50)"></circle>
        </svg>
        <div class="gauge-center">
          <span class="gauge-pct" style="color:${color}">${valid ? pct.toFixed(1) + '%' : '—'}</span>
          <span class="gauge-label">${escHtml(label)}</span>
        </div>
        <div class="gauge-sub">${escHtml(sub || '')}</div>
      </div>
    `;
  },

  // "/mnt/disk0" stays as-is; long device-mapper paths get trimmed for the label.
  _shortMount(mount) {
    if (!mount) return '—';
    if (mount.length <= 16) return mount;
    return '…' + mount.slice(-15);
  },
};
