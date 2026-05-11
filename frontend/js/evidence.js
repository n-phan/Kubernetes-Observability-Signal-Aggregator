// ── Evidence drill-down ───────────────────────────────────────────────────────
//
// Makes each "Supporting evidence" item in the RCA panel clickable when its text
// can be matched to a concrete signal in the same query result. Clicking jumps to
// the matching row in the Metrics / Logs / Traces panel and flashes it.
//
// Matching is heuristic and operates on the raw API response (no backend changes):
//   1. A quoted fragment in the evidence text that appears in a log message
//   2. An identifier-like token (contains "_" or ".") that appears in a log message
//   3. A Prometheus metric series name that appears in the evidence text
//   4. A Jaeger span operation name that appears in the evidence text
// The first rule that matches wins. Evidence that matches nothing stays plain text.

const EvidenceLinker = {

  // Resolve an evidence string against the raw result `data`.
  // Returns a target descriptor ({kind, ...}) or null.
  resolve(text, data) {
    if (!text) return null;
    const lower = text.toLowerCase();
    const logLines = (data.logs && data.logs.lines) || [];

    const logHit = (needle) => {
      const n = needle.toLowerCase();
      return logLines.some(l => (l.message || '').toLowerCase().includes(n));
    };

    // 1. Quoted fragments: 'xxx' / "xxx" / `xxx`, at least 3 chars.
    const quoted = [...text.matchAll(/['"`]([^'"`\n]{3,})['"`]/g)].map(m => m[1]);
    for (const frag of quoted.slice(0, 10)) {
      if (logHit(frag)) return { kind: 'log', needle: frag };
    }

    // 2. Identifier-like tokens (function names, file names): contain "_" or ".".
    const tokens = [...text.matchAll(/\b[A-Za-z_][A-Za-z0-9_.]{3,}\b/g)]
      .map(m => m[0])
      .filter(t => /[._]/.test(t));
    for (const tok of tokens.slice(0, 10)) {
      if (logHit(tok)) return { kind: 'log', needle: tok };
    }

    // 3. Metric series names mentioned in the text (longest first so a specific
    //    name like "http_error_rate" beats a generic prefix).
    const metricNames = [...new Set((data.metrics && data.metrics.series || [])
      .map(s => s.name).filter(Boolean))].sort((a, b) => b.length - a.length);
    for (const name of metricNames) {
      if (lower.includes(name.toLowerCase())) return { kind: 'metric', name };
    }

    // 4. Span operation names mentioned in the text.
    const ops = new Set();
    ((data.traces && data.traces.traces) || []).forEach(t =>
      (t.spans || []).forEach(s => { if (s.operation_name) ops.add(s.operation_name); }));
    const opList = [...ops].sort((a, b) => b.length - a.length);
    for (const op of opList) {
      if (op.length >= 4 && lower.includes(op.toLowerCase())) return { kind: 'trace', op };
    }

    return null;
  },

  // Attach click handlers to the evidence items inside an already-rendered RCA panel.
  // `panels` carries the LogsPanel/MetricsPanel/TracesPanel instances (any may be undefined).
  wire(rcaEl, data, panels) {
    if (!rcaEl || !data || !data.rca) return;
    const evidence = data.rca.supporting_evidence || [];
    rcaEl.querySelectorAll('.evidence-item').forEach(li => {
      const idx = Number(li.dataset.evidenceIdx);
      const text = evidence[idx];
      const target = this.resolve(text, data);
      if (!target) return;
      li.classList.add('evidence-link');
      li.title = target.kind === 'log'    ? 'Jump to the related log line'
               : target.kind === 'metric' ? 'Jump to the related metric'
               : 'Jump to the related trace span';
      li.addEventListener('click', () => this.jump(target, panels || {}));
    });
  },

  jump(target, panels) {
    let el = null;
    if (target.kind === 'metric') {
      el = document.querySelector(`#main tr[data-metric="${(window.CSS && CSS.escape) ? CSS.escape(target.name) : target.name}"]`);
    } else if (target.kind === 'log' && panels.logsPanel) {
      el = panels.logsPanel.revealByContent(target.needle);
    } else if (target.kind === 'trace') {
      const blocks = document.querySelectorAll('#main .trace-block');
      for (const block of blocks) {
        const opSpan = [...block.querySelectorAll('.span-op')]
          .find(s => s.textContent.trim() === target.op);
        if (opSpan) {
          block.classList.remove('collapsed');
          el = opSpan.closest('.span-row') || block;
          break;
        }
      }
    }
    this._reveal(el);
  },

  _reveal(el) {
    if (!el) return;
    const panel = el.closest('.panel');
    if (panel) panel.classList.remove('collapsed');
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.remove('flash-highlight');
    void el.offsetWidth;            // force reflow so the animation restarts
    el.classList.add('flash-highlight');
    setTimeout(() => el.classList.remove('flash-highlight'), 2000);
  },
};
