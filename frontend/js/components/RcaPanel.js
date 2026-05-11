// ── RcaPanel Component ────────────────────────────────────────────────────────
//
// Displays the AI root cause analysis result, or a placeholder panel with an
// "Analyze with AI" button when RCA has not yet been run.
//
// Usage — show full RCA results:
//   const panel = new RcaPanel({ rca: data.rca, showRca: true });
//   document.querySelector('#main').appendChild(panel.element);
//
// Usage — show placeholder with Analyze button:
//   const panel = new RcaPanel({ rca: null, showRca: false, hasErrors: true });
//   document.querySelector('#main').appendChild(panel.element);

class RcaPanel {
  // options.rca        – the rca object from the API response (may be null)
  // options.showRca    – if false, renders the "not yet performed" placeholder
  // options.hasErrors  – used by the placeholder to customize its message
  // options.followups  – stateless browser-side follow-up chat history
  constructor({ rca, showRca, hasErrors, followups }) {
    if (showRca && rca?.performed) {
      this.element = this._buildResults(rca, followups || []);
    } else if (showRca && rca && rca.error) {
      this.element = this._buildFailed(rca);
    } else {
      this.element = this._buildPlaceholder(hasErrors);
    }
    this.element.id = 'rca-panel';   // stable handle so api.js can swap in a loading view
  }

  // ── Loading view (LLM request in flight) ──────────────────────────────────
  static loadingElement() {
    const panel = document.createElement('div');
    panel.className = 'panel animate-in';
    panel.id = 'rca-panel';
    panel.innerHTML = `
      <div class="panel-header">
        <span class="panel-title" style="color:var(--accent)">Root Cause Analysis</span>
        <span class="panel-count">analyzing… ▾</span>
      </div>
      <div class="panel-body">
        <div class="rca-loading">
          <span class="rca-spinner">⟳</span>
          <span>Analyzing signals with AI — this can take a few seconds…</span>
        </div>
      </div>
    `;
    return panel;
  }
  // ── Failed view (RCA ran but errored — bad/missing key, unsupported provider, API error) ──
  _buildFailed(rca) {
    const panel = document.createElement('div');
    panel.className = 'panel animate-in';
    panel.innerHTML = `
      <div class="panel-header">
        <span class="panel-title" style="color:var(--error)">Root Cause Analysis</span>
        <span class="panel-count">failed ▾</span>
      </div>
      <div class="panel-body">
        <div class="rca-placeholder">
          <div style="color:var(--error);margin-bottom:12px">⚠ ${escHtml(rca.error || 'Analysis failed')}</div>
          <button class="btn-analyze" id="btn-analyze" onclick="runAnalyze()">⚡ Retry analysis</button>
        </div>
      </div>
    `;
    return panel;
  }

  // ── Full results view ───────────────────────────────────────────────────────

  _buildResults(rca, followups) {
    const confidence    = rca.confidence || 0;
    const confidencePct = Math.round(confidence * 100);
    const barColor      = confidenceColor(confidence);

    const actionsHtml = (rca.recommended_actions || []).map(a => `
      <div class="action-row p${a.priority}">
        <span class="action-priority">P${a.priority}</span>
        <div class="action-content">
          <div class="action-text">${escHtml(a.action)}</div>
          <div class="action-rationale">${escHtml(a.rationale)}</div>
        </div>
      </div>
    `).join('');

    const evidenceHtml = (rca.supporting_evidence || [])
      .map((e, idx) => {
        // data-evidence-idx lets evidence.js make the item clickable (jump to the signal row).
        const text = escHtml(e);
        const separator = text.indexOf(' — ');
        const hasDetail = separator >= 0;
        const lead = hasDetail ? text.slice(0, separator).trim() : text;
        const detail = hasDetail ? text.slice(separator + 3).trim() : '';
        return `
        <li class="evidence-item" data-evidence-idx="${idx}">
          <span class="evidence-index">${idx + 1}</span>
          <div class="evidence-content">
            <div class="evidence-lead">${lead}</div>
            ${detail ? `<div class="evidence-detail">${detail}</div>` : ''}
          </div>
        </li>
      `;
      })
      .join('');

    const logEvidenceHtml = (rca.log_evidence || []).map(l => {
      const level = (l.severity || 'unknown').toLowerCase();
      const cls   = severityClass(level);
      return `
      <div class="rca-log-row ${cls}">
        <div class="rca-log-meta">
          <span class="rca-log-time">${fmtTime(l.timestamp)}</span>
          <span class="rca-log-level ${cls}">${escHtml((l.severity || 'unknown').toUpperCase())}</span>
        </div>
        <pre class="rca-log-message">${escHtml(l.message || '')}</pre>
        ${l.relevance ? `<div class="rca-log-relevance">${escHtml(l.relevance)}</div>` : ''}
      </div>
    `}).join('');

    const codeRefsHtml = (rca.code_references || []).map(r => {
      const base = r.url || '';
      const href = base + (r.line_number && base && !base.includes('#') ? '#L' + r.line_number : '');
      return `
      <div class="code-ref">
        <span class="code-ref-icon">⌥</span>
        <div class="code-ref-info">
          <div class="code-ref-path">${escHtml(r.path)}${r.line_number ? '#L' + r.line_number : ''}</div>
          <div class="code-ref-relevance">${escHtml(r.relevance || '')}</div>
        </div>
        ${href ? `<a class="code-ref-link" href="${href}">↗ GitHub</a>` : ''}
      </div>
    `}).join('');

    const body = `
      <div class="rca-summary">${escHtml(rca.summary)}</div>
      <div class="rca-root-cause">${escHtml(rca.root_cause)}</div>
      <div class="confidence-row">
        <span class="confidence-label">Confidence</span>
        <div class="confidence-bar">
          <div class="confidence-fill" style="width:${confidencePct}%;background:${barColor}"></div>
        </div>
        <span class="confidence-pct" style="color:${barColor}">${confidencePct}%</span>
      </div>
      ${evidenceHtml ? `
        <div class="rca-subsection">
          <div class="rca-subsection-title">Supporting evidence</div>
          <ol class="evidence-list">${evidenceHtml}</ol>
        </div>` : ''}
      ${logEvidenceHtml ? `
        <div class="rca-subsection">
          <div class="rca-subsection-title">Logs</div>
          <div class="rca-log-list">${logEvidenceHtml}</div>
        </div>` : ''}
      ${actionsHtml ? `
        <div class="rca-subsection">
          <div class="rca-subsection-title">Recommended actions</div>
          <div class="actions-list">${actionsHtml}</div>
        </div>` : ''}
      ${codeRefsHtml ? `
        <div class="rca-subsection">
          <div class="rca-subsection-title">Code references</div>
          <div class="code-refs">${codeRefsHtml}</div>
        </div>` : ''}
      ${this._buildFollowupChat(followups)}
    `;

    const header = `
      <span class="panel-title" style="color:var(--accent)">Root Cause Analysis</span>
      <span style="font-size:10px;color:var(--text-dim);margin-left:8px">${confidencePct}% confidence</span>
      <span class="panel-count">▾</span>
    `;

    const el = collapsible(header, body);
    return el;
  }

  _buildFollowupChat(followups) {
    const starters = [
      'What’s the blast radius?',
      'What should I check first?',
      'What evidence supports this?',
    ];
    const messagesHtml = (followups || []).map(item => {
      const role = item.role === 'user' ? 'user' : 'assistant';
      const source = role === 'assistant' && item.provider
        ? `<div class="rca-followup-source">${escHtml(item.provider)}${item.fallback_used ? ' fallback' : ''}</div>`
        : '';
      return `
        <div class="rca-followup-message ${role}">
          <div class="rca-followup-role">${role === 'user' ? 'You' : 'Assistant'}</div>
          <div class="rca-followup-text">${escHtml(item.content || '')}</div>
          ${source}
        </div>
      `;
    }).join('');
    const startersHtml = starters.map(text => `
      <button
        type="button"
        class="rca-followup-suggestion"
        onclick="runRcaFollowup('${escHtml(text)}')"
      >${escHtml(text)}</button>
    `).join('');

    return `
      <div class="rca-subsection rca-followup">
        <div class="rca-subsection-title">Follow-up</div>
        <div class="rca-followup-suggestions">${startersHtml}</div>
        <div class="rca-followup-messages">
          ${messagesHtml || '<div class="rca-followup-empty">Ask a scoped follow-up about this RCA.</div>'}
        </div>
        <form
          class="rca-followup-form"
          id="rca-followup-form"
          onsubmit="event.preventDefault(); runRcaFollowup();"
        >
          <textarea
            id="rca-followup-input"
            class="rca-followup-input"
            rows="2"
            placeholder="Ask about blast radius, first checks, or evidence…"
          ></textarea>
          <button type="submit" id="rca-followup-send" class="rca-followup-send">Ask</button>
        </form>
        <div id="rca-followup-status" class="rca-followup-status"></div>
      </div>
    `;
  }

  // ── Placeholder view (RCA not yet run) ────────────────────────────────────

  _buildPlaceholder(hasErrors) {
    const message = hasErrors
      ? 'Error signals detected. Run AI analysis to get root cause, recommended actions, and code references.'
      : 'No error signals detected in this time window. Run analysis anyway to confirm.';

    const panel = document.createElement('div');
    panel.className = 'panel animate-in';
    panel.innerHTML = `
      <div class="panel-header">
        <span class="panel-title" style="color:var(--text-muted)">Root Cause Analysis</span>
        <span class="panel-count">not yet performed ▾</span>
      </div>
      <div class="panel-body">
        <div class="rca-placeholder">
          ${message}
          <br/>
          <button class="btn-analyze" id="btn-analyze" onclick="runAnalyze()">
            ⚡ Analyze with AI
          </button>
        </div>
      </div>
    `;
    return panel;
  }
}
