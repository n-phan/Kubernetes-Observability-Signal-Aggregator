// ── RcaPanel Component ────────────────────────────────────────────────────────
//
// Displays the AI root cause analysis result, or a placeholder panel with
// Hermes and LLM analysis buttons when RCA has not yet been run.
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
  // options.meta       – query metadata used to name and annotate exports
  // options.result     – full query result used for incident report export
  // options.rcaBackend – selected backend for loading/failure retry context
  constructor({ rca, showRca, hasErrors, followups, meta, result, rcaBackend }) {
    if (showRca && rca?.performed) {
      this.element = this._buildResults(rca, followups || [], meta || {}, result || null);
    } else if (showRca && rca && rca.error) {
      this.element = this._buildFailed(rca, rcaBackend);
    } else {
      this.element = this._buildPlaceholder(hasErrors);
    }
    this.element.id = 'rca-panel';   // stable handle so api.js can swap in a loading view
  }

  // ── Loading view (LLM request in flight) ──────────────────────────────────
  static loadingElement(rcaBackend) {
    const backend = rcaBackend === 'llm' ? 'LLM' : 'Hermes';
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
          <span>Analyzing signals with ${backend} — this can take a few seconds…</span>
        </div>
      </div>
    `;
    return panel;
  }
  // ── Failed view (RCA ran but errored — bad/missing key, unsupported provider, API error) ──
  _buildFailed(rca, rcaBackend) {
    const backend = rcaBackend === 'llm' ? 'llm' : 'hermes';
    const label = backend === 'llm' ? 'LLM' : 'Hermes';
    const altBackend = backend === 'llm' ? 'hermes' : 'llm';
    const altLabel = altBackend === 'llm' ? 'LLM' : 'Hermes';
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
          <div class="rca-actions">
            <button class="btn-analyze" onclick="runAnalyze('${backend}')">Retry ${label}</button>
            <button class="btn-analyze secondary" onclick="runAnalyze('${altBackend}')">Try ${altLabel}</button>
          </div>
        </div>
      </div>
    `;
    return panel;
  }

  // ── Full results view ───────────────────────────────────────────────────────

  _buildResults(rca, followups, meta, result) {
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
      <div class="rca-export-row">
        <a class="rca-export-btn" href="#" download title="Download RCA Markdown">Export .md</a>
        <a class="rca-export-btn rca-incident-export-btn" href="#" download title="Download Incident Report Markdown">Export Incident Report</a>
        <span class="rca-export-status" role="status" aria-live="polite"></span>
      </div>
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
    const exportLink = el.querySelector('.rca-export-btn');
    const exportStatus = el.querySelector('.rca-export-status');
    if (exportLink) {
      this._prepareMarkdownDownload(rca, meta, exportStatus, exportLink);
    }
    const incidentExportLink = el.querySelector('.rca-incident-export-btn');
    if (incidentExportLink) {
      this._prepareIncidentReportDownload(result || { meta, rca }, followups, exportStatus, incidentExportLink);
    }
    return el;
  }

  _prepareMarkdownDownload(rca, meta, statusEl, linkEl) {
    try {
      const markdown = this._buildMarkdown(rca, meta);
      const filename = this._markdownFilename(meta);
      const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
      const url = URL.createObjectURL(blob);

      linkEl.href = url;
      linkEl.download = filename;
      linkEl.addEventListener('click', () => {
        this._setExportStatus(statusEl, `downloading: ${filename}`, 'ok');
        window.setTimeout(() => URL.revokeObjectURL(url), 120000);
      }, { once: true });
    } catch (err) {
      const message = err?.message || 'download failed';
      linkEl.removeAttribute('href');
      linkEl.removeAttribute('download');
      this._setExportStatus(statusEl, message, 'error');
    }
  }

  _buildMarkdown(rca, meta) {
    const target = this._mdText(meta?.target || '');
    const namespace = this._mdText(meta?.namespace || '');
    const windowStart = this._mdText(meta?.window_start || '');
    const windowEnd = this._mdText(meta?.window_end || '');
    const confidencePct = Math.round((rca.confidence || 0) * 100);
    const lines = [
      target ? `# RCA: ${target}` : '# Root Cause Analysis',
      '',
      target ? `- Target: \`${target}\`` : null,
      namespace ? `- Namespace: \`${namespace}\`` : null,
      windowStart || windowEnd ? `- Window: ${windowStart || 'unknown'} to ${windowEnd || 'unknown'}` : null,
      `- Confidence: ${confidencePct}%`,
      '',
      '## Summary',
      this._sectionText(rca.summary),
      '',
      '## Root Cause',
      this._sectionText(rca.root_cause),
      '',
      '## Supporting Evidence',
      ...this._orderedTextList(rca.supporting_evidence || []),
      '',
      '## Log Evidence',
      ...this._logEvidenceLines(rca.log_evidence || []),
      '',
      '## Recommended Actions',
      ...this._actionLines(rca.recommended_actions || []),
      '',
      '## Code References',
      ...this._codeReferenceLines(rca.code_references || []),
      '',
    ];

    return lines.filter(line => line !== null).join('\n');
  }

  _prepareIncidentReportDownload(result, followups, statusEl, linkEl) {
    try {
      const markdown = this._buildIncidentReport(result, followups);
      const filename = this._incidentReportFilename(result?.meta);
      const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
      const url = URL.createObjectURL(blob);

      linkEl.href = url;
      linkEl.download = filename;
      linkEl.addEventListener('click', () => {
        this._setExportStatus(statusEl, `downloading: ${filename}`, 'ok');
        window.setTimeout(() => URL.revokeObjectURL(url), 120000);
      }, { once: true });
    } catch (err) {
      const message = err?.message || 'incident report download failed';
      linkEl.removeAttribute('href');
      linkEl.removeAttribute('download');
      this._setExportStatus(statusEl, message, 'error');
    }
  }

  _buildIncidentReport(result, followups) {
    const rca = result?.rca || {};
    const meta = result?.meta || {};
    const target = this._mdText(meta.target || 'TBD');
    const namespace = this._mdText(meta.namespace || 'TBD');
    const windowStart = this._mdText(meta.window_start || 'TBD');
    const windowEnd = this._mdText(meta.window_end || 'TBD');
    const generatedAt = new Date().toISOString();
    const confidencePct = Math.round((rca.confidence || 0) * 100);
    const impact = this._impactLines(result);
    const lines = [
      `# Incident Report: ${target}`,
      '',
      '## Incident Metadata',
      `- Target service: \`${target}\``,
      `- Namespace: \`${namespace}\``,
      `- Query window: ${windowStart} to ${windowEnd}`,
      `- Generated at: ${generatedAt}`,
      `- RCA confidence: ${confidencePct}%`,
      '',
      '## Executive Summary',
      this._sectionText(rca.summary),
      '',
      '## Root Cause',
      this._sectionText(rca.root_cause),
      '',
      '## Impact',
      ...impact,
      '',
      '## Incident Timeline',
      this._timelineMermaid(result?.timeline),
      '',
      '## Signal Snapshot',
      '### Metrics',
      ...this._metricSummaryTable(result?.metrics),
      '',
      '### Logs',
      ...this._logSummaryTable(result?.logs),
      '',
      '### Traces',
      ...this._traceSummaryTable(result?.traces),
      '',
      '### Correlations',
      ...this._correlationTable(result?.correlations || []),
      '',
      '## Root Cause Analysis',
      '### Supporting Evidence',
      ...this._orderedTextList(rca.supporting_evidence || []),
      '',
      '### Structured Log Evidence',
      ...this._logEvidenceLines(rca.log_evidence || []),
      '',
      '### Recommended Actions',
      ...this._actionLines(rca.recommended_actions || []),
      '',
      '### Code References',
      ...this._codeReferenceLines(rca.code_references || []),
      '',
      '## Follow-up Context',
      ...this._followupTranscript(followups || []),
      '',
      '## Unknowns / TODOs',
      '- Owner: TBD',
      '- Mitigation status: TBD',
      '- Customer impact: TBD',
      '- Remediation started at: TBD',
      '- Remediation completed at: TBD',
      '- Verification steps: TBD',
      '',
    ];

    return lines.join('\n');
  }

  _impactLines(result) {
    const logs = result?.logs || {};
    const traces = result?.traces || {};
    const correlations = result?.correlations || [];
    const meta = result?.meta || {};
    const highestSeverity = this._highestSeverity(correlations.map(c => c.severity));
    const p99 = traces.p99_duration_ms !== null && traces.p99_duration_ms !== undefined
      ? `${this._formatNumber(traces.p99_duration_ms)} ms`
      : 'TBD';
    return [
      `- Affected service: \`${this._mdText(meta.target || 'TBD')}\``,
      `- Error logs: ${Number(logs.error_count || 0)}`,
      `- Warning logs: ${Number(logs.warn_count || 0)}`,
      `- Trace p99 duration: ${p99}`,
      `- Error traces: ${Number(traces.error_trace_count || 0)}`,
      `- Highest correlation severity: ${highestSeverity || 'TBD'}`,
      '- Customer impact: TBD',
    ];
  }

  _timelineMermaid(timeline) {
    const events = timeline?.events || [];
    if (!events.length) return 'TBD - no timeline events detected';

    const lines = ['```mermaid', 'flowchart TD'];
    events.slice(0, 12).forEach((evt, idx) => {
      const node = `E${idx + 1}`;
      const label = this._mermaidLabel([
        `${idx + 1}. ${this._eventTypeLabel(evt.event_type)}`,
        fmtTime(evt.timestamp),
        evt.summary || '',
      ].filter(Boolean).join(' - '));
      lines.push(`  ${node}["${label}"]`);
      if (idx > 0) lines.push(`  E${idx} --> ${node}`);
    });
    if (events.length > 12) {
      lines.push(`  E12 --> MORE["${events.length - 12} more event${events.length - 12 === 1 ? '' : 's'}"]`);
    }
    lines.push('```');
    return lines.join('\n');
  }

  _metricSummaryTable(metrics) {
    const series = (metrics?.series || [])
      .slice()
      .sort((a, b) => Number(b.peak_value ?? b.latest_value ?? 0) - Number(a.peak_value ?? a.latest_value ?? 0))
      .slice(0, 8);
    if (!series.length) return ['_None provided._'];

    const rows = [
      '| Metric | Latest | Peak | Labels |',
      '|---|---:|---:|---|',
    ];
    series.forEach(item => {
      rows.push(this._mdTableRow([
        this._mdCell(item.name || 'unknown'),
        this._mdCell(this._formatMaybeNumber(item.latest_value)),
        this._mdCell(this._formatMaybeNumber(item.peak_value)),
        this._mdCell(this._labelsText(item.labels || {})),
      ]));
    });
    return rows;
  }

  _logSummaryTable(logs) {
    const lines = (logs?.lines || [])
      .filter(line => ['error', 'critical', 'warn', 'warning'].includes(String(line.severity || '').toLowerCase()))
      .slice(0, 8);
    if (!lines.length) return ['_None provided._'];

    const rows = [
      '| Time | Severity | Message |',
      '|---|---|---|',
    ];
    lines.forEach(line => {
      rows.push(this._mdTableRow([
        this._mdCell(line.timestamp || 'unknown'),
        this._mdCell(String(line.severity || 'unknown').toUpperCase()),
        this._mdCell(this._truncateText(line.message || '', 220)),
      ]));
    });
    return rows;
  }

  _traceSummaryTable(traces) {
    const traceRows = (traces?.traces || [])
      .slice()
      .sort((a, b) => Number(b.duration_ms || 0) - Number(a.duration_ms || 0))
      .slice(0, 8);
    if (!traceRows.length) return ['_None provided._'];

    const rows = [
      '| Trace ID | Duration | Error | Root service | Top span |',
      '|---|---:|---|---|---|',
    ];
    traceRows.forEach(trace => {
      const spans = trace.spans || [];
      const topSpan = spans.slice().sort((a, b) => Number(b.duration_ms || 0) - Number(a.duration_ms || 0))[0];
      rows.push(this._mdTableRow([
        this._mdCell(trace.trace_id || 'unknown'),
        this._mdCell(`${this._formatMaybeNumber(trace.duration_ms)} ms`),
        this._mdCell(trace.has_errors || spans.some(span => span.is_error) ? 'yes' : 'no'),
        this._mdCell(trace.root_service || topSpan?.service_name || 'TBD'),
        this._mdCell(topSpan ? `${topSpan.operation_name || 'unknown'} (${this._formatMaybeNumber(topSpan.duration_ms)} ms)` : 'TBD'),
      ]));
    });
    return rows;
  }

  _correlationTable(correlations) {
    const rows = (correlations || []).slice(0, 10);
    if (!rows.length) return ['_None provided._'];

    const lines = [
      '| Time | Severity | Kind | Confidence | Description |',
      '|---|---|---|---:|---|',
    ];
    rows.forEach(item => {
      lines.push(this._mdTableRow([
        this._mdCell(item.timestamp || 'unknown'),
        this._mdCell(item.severity || 'info'),
        this._mdCell(item.kind || 'unknown'),
        this._mdCell(`${Math.round(Number(item.confidence || 0) * 100)}%`),
        this._mdCell(this._truncateText(item.description || '', 220)),
      ]));
    });
    return lines;
  }

  _followupTranscript(followups) {
    const visible = (followups || []).slice(-12);
    if (!visible.length) return ['_None provided._'];

    const lines = [];
    visible.forEach((item, idx) => {
      const role = item.role === 'user' ? 'User' : 'Assistant';
      const provider = item.provider ? ` (${this._mdText(item.provider)}${item.fallback_used ? ' fallback' : ''})` : '';
      lines.push(`${idx + 1}. **${role}${provider}:** ${this._indentContinuation(this._mdText(item.content || ''), '   ')}`);
    });
    return lines;
  }

  _sectionText(value) {
    return this._mdText(value) || '_None provided._';
  }

  _orderedTextList(items) {
    if (!items.length) return ['_None provided._'];
    return items.map((item, idx) => `${idx + 1}. ${this._indentContinuation(this._mdText(item))}`);
  }

  _logEvidenceLines(logs) {
    if (!logs.length) return ['_None provided._'];

    const visible = logs.slice(0, 5);
    const lines = [];
    visible.forEach((log, idx) => {
      const timestamp = this._mdText(log.timestamp || 'unknown time');
      const severity = this._mdText((log.severity || 'unknown').toUpperCase());
      const relevance = this._mdText(log.relevance || '');
      const label = relevance
        ? `${timestamp} \`${severity}\` - ${relevance}`
        : `${timestamp} \`${severity}\``;
      const message = this._truncateText(log.message || '', 900);

      lines.push(`${idx + 1}. ${this._indentContinuation(label)}`);
      if (message) {
        lines.push('');
        lines.push(this._indentBlock(this._codeFence(message, 'text'), '   '));
      }
    });

    if (logs.length > visible.length) {
      lines.push('');
      lines.push(`...and ${logs.length - visible.length} more log evidence entr${logs.length - visible.length === 1 ? 'y' : 'ies'}.`);
    }
    return lines;
  }

  _actionLines(actions) {
    if (!actions.length) return ['_None provided._'];

    const lines = [];
    [1, 2, 3].forEach(priority => {
      const group = actions.filter(action => Number(action.priority) === priority);
      if (!group.length) return;

      lines.push(`### P${priority}`);
      group.forEach(action => {
        lines.push(`- ${this._indentContinuation(this._mdText(action.action || 'Action not provided'))}`);
        if (action.rationale) {
          lines.push(`  Rationale: ${this._indentContinuation(this._mdText(action.rationale), '  ')}`);
        }
      });
      lines.push('');
    });

    const grouped = new Set([1, 2, 3]);
    const other = actions.filter(action => !grouped.has(Number(action.priority)));
    if (other.length) {
      lines.push('### Other');
      other.forEach(action => {
        lines.push(`- ${this._indentContinuation(this._mdText(action.action || 'Action not provided'))}`);
        if (action.rationale) {
          lines.push(`  Rationale: ${this._indentContinuation(this._mdText(action.rationale), '  ')}`);
        }
      });
      lines.push('');
    }

    while (lines[lines.length - 1] === '') lines.pop();
    return lines;
  }

  _codeReferenceLines(references) {
    if (!references.length) return ['_None provided._'];

    const visible = references.slice(0, 10);
    const lines = [];
    visible.forEach((ref, idx) => {
      const path = this._mdText(ref.path || 'unknown path');
      const lineNumber = ref.line_number ? `#L${ref.line_number}` : '';
      const relevance = this._mdText(ref.relevance || '');
      const url = this._codeReferenceUrl(ref);
      const label = relevance
        ? `\`${path}${lineNumber}\` - ${relevance}`
        : `\`${path}${lineNumber}\``;

      lines.push(`${idx + 1}. ${this._indentContinuation(label)}`);
      if (url) lines.push(`   URL: ${url}`);
      if (ref.snippet) {
        lines.push('   Snippet:');
        lines.push(this._indentBlock(this._codeFence(this._truncateText(ref.snippet, 600), 'text'), '   '));
      }
    });

    if (references.length > visible.length) {
      lines.push('');
      lines.push(`...and ${references.length - visible.length} more code reference${references.length - visible.length === 1 ? '' : 's'}.`);
    }
    return lines;
  }

  _codeReferenceUrl(ref) {
    const base = this._mdText(ref.url || '');
    if (!base) return '';
    if (!ref.line_number || base.includes('#')) return base;
    return `${base}#L${ref.line_number}`;
  }

  _markdownFilename(meta) {
    const target = this._slug(meta?.target || 'incident');
    const stamp = this._timestampForFilename(meta?.window_start || meta?.window_end || '');
    return `rca-${target}-${stamp}.md`;
  }

  _incidentReportFilename(meta) {
    const target = this._slug(meta?.target || 'incident');
    const stamp = this._timestampForFilename(meta?.window_start || meta?.window_end || '');
    return `incident-report-${target}-${stamp}.md`;
  }

  _timestampForFilename(value) {
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.getTime())) {
      return parsed.toISOString()
        .replace(/[-:]/g, '')
        .replace(/\.\d{3}Z$/, 'Z');
    }
    return this._slug(value || 'unknown-window');
  }

  _slug(value) {
    return String(value)
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 80) || 'incident';
  }

  _mdText(value) {
    return String(value ?? '')
      .replace(/\r\n/g, '\n')
      .replace(/\r/g, '\n')
      .trim();
  }

  _truncateText(value, maxLength) {
    const text = this._mdText(value);
    if (text.length <= maxLength) return text;
    return text.slice(0, Math.max(0, maxLength - 16)).trimEnd() + '\n...[truncated]';
  }

  _mdCell(value) {
    const text = this._mdText(value || 'TBD')
      .replace(/\n+/g, ' ')
      .replace(/\|/g, '\\|');
    return ` ${text || 'TBD'} `;
  }

  _mdTableRow(cells) {
    return `|${cells.join('|')}|`;
  }

  _labelsText(labels) {
    const entries = Object.entries(labels || {});
    if (!entries.length) return 'TBD';
    return entries
      .slice(0, 6)
      .map(([key, value]) => `${key}=${value}`)
      .join(', ');
  }

  _formatMaybeNumber(value) {
    if (value === null || value === undefined || value === '') return 'TBD';
    const num = Number(value);
    if (!Number.isFinite(num)) return this._mdText(value);
    return this._formatNumber(num);
  }

  _formatNumber(value) {
    const num = Number(value);
    if (!Number.isFinite(num)) return 'TBD';
    if (Math.abs(num) >= 100) return num.toFixed(0);
    if (Math.abs(num) >= 10) return num.toFixed(1);
    return num.toFixed(3).replace(/0+$/, '').replace(/\.$/, '');
  }

  _highestSeverity(severities) {
    const rank = { critical: 5, error: 4, warn: 3, warning: 3, info: 2, debug: 1 };
    return (severities || [])
      .map(value => this._mdText(value).toLowerCase())
      .filter(Boolean)
      .sort((a, b) => (rank[b] || 0) - (rank[a] || 0))[0] || '';
  }

  _eventTypeLabel(eventType) {
    return this._mdText(eventType || 'event').replace(/_/g, ' ');
  }

  _mermaidLabel(value) {
    return this._truncateText(value, 120)
      .replace(/\n+/g, ' ')
      .replace(/["\\]/g, '')
      .replace(/[{}[\]<>|]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim() || 'event';
  }

  _indentContinuation(value, indent = '   ') {
    return this._mdText(value).replace(/\n/g, `\n${indent}`);
  }

  _indentBlock(value, indent) {
    return this._mdText(value)
      .split('\n')
      .map(line => indent + line)
      .join('\n');
  }

  _codeFence(value, lang = '') {
    const text = this._mdText(value);
    const runs = text.match(/`+/g) || [];
    const fenceLength = Math.max(3, ...runs.map(run => run.length + 1));
    const fence = '`'.repeat(fenceLength);
    return `${fence}${lang}\n${text}\n${fence}`;
  }

  _setExportStatus(statusEl, message, tone) {
    if (!statusEl) return;
    statusEl.textContent = message;
    statusEl.className = `rca-export-status ${tone || ''}`.trim();
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
          <div class="rca-actions">
            <button class="btn-analyze" onclick="runAnalyze('hermes')">Analyze with Hermes</button>
            <button class="btn-analyze secondary" onclick="runAnalyze('llm')">Analyze with LLM</button>
          </div>
        </div>
      </div>
    `;
    return panel;
  }
}
