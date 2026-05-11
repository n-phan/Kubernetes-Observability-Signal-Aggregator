// ── LlmConfigPanel Component ──────────────────────────────────────────────────
//
// Settings → "Config LLM": pick an LLM provider (ChatGPT / Gemini / Claude /
// Ollama / Custom) and enter its endpoint, API key, and secret. Stored in
// localStorage. NOTE: this is a configuration UI only — the RCA backend still
// uses the server-side ANTHROPIC_API_KEY; wiring this config through to the
// analyzer is a follow-up.
//
// Opened from the sidebar Setting item. Lazily builds its DOM on first toggle.

const LlmConfigPanel = {
  STORAGE_KEY: 'obs_llm_config',

  PRESETS: {
    chatgpt: { label: 'ChatGPT (OpenAI)',   endpoint: 'https://api.openai.com/v1/chat/completions', model: 'gpt-4o',           note: 'API key required.' },
    gemini:  { label: 'Gemini (Google)',    endpoint: 'https://generativelanguage.googleapis.com/v1beta/models', model: 'gemini-2.0-flash', note: 'API key required.' },
    claude:  { label: 'Claude (Anthropic)', endpoint: 'https://api.anthropic.com/v1/messages',       model: 'claude-sonnet-4-6', note: 'API key required.' },
    ollama:  { label: 'Ollama (local)',     endpoint: 'http://localhost:11434/api/chat',             model: 'llama3.1',         note: 'Local — no key needed.' },
    custom:  { label: 'Custom',              endpoint: '',                                            model: '',                 note: 'Enter everything manually.' },
  },

  _built: false,

  // ── Build ───────────────────────────────────────────────────────────────
  _build() {
    if (this._built) return;
    const section = document.createElement('section');
    section.id = 'llm-section';
    const providerOpts = Object.entries(this.PRESETS)
      .map(([k, v]) => `<option value="${k}">${escHtml(v.label)}</option>`).join('');
    section.innerHTML = `
      <div class="llm-bar">
        <span class="llm-title">CONFIG LLM</span>
        <button class="llm-close" onclick="LlmConfigPanel.toggle()" title="Close">✕</button>
      </div>
      <div class="llm-body">
        <div class="llm-note">
          Configure the LLM used for AI root-cause analysis. Stored locally in your browser
          and sent with each "Analyze with AI" request.
          <em>Only the Anthropic (Claude) provider is functional right now — the others are placeholders.</em>
        </div>
        <div class="llm-form">
          <div class="field">
            <label>Provider</label>
            <select id="llm-provider">${providerOpts}</select>
          </div>
          <div class="field llm-wide">
            <label>Address / Endpoint</label>
            <input id="llm-endpoint" type="text" placeholder="https://…" autocomplete="off" />
          </div>
          <div class="field">
            <label>Model</label>
            <input id="llm-model" type="text" placeholder="model name" autocomplete="off" />
          </div>
          <div class="field">
            <label>API Key</label>
            <input id="llm-key" type="password" placeholder="sk-…" autocomplete="off" />
          </div>
          <div class="field">
            <label>Secret</label>
            <input id="llm-secret" type="password" placeholder="(if required)" autocomplete="off" />
          </div>
        </div>
        <div class="llm-hint" id="llm-hint"></div>
        <div class="llm-actions">
          <button class="btn-query" id="llm-save">Save</button>
          <button class="btn-mock"  id="llm-clear">Clear</button>
          <span class="llm-msg" id="llm-msg"></span>
        </div>
      </div>
    `;
    const main = document.querySelector('main');
    document.body.insertBefore(section, main);

    document.getElementById('llm-provider').addEventListener('change', e => this._applyPreset(e.target.value, true));
    document.getElementById('llm-save').addEventListener('click', () => this._save());
    document.getElementById('llm-clear').addEventListener('click', () => this._clear());

    this._built = true;
    this._loadFromStorage();
  },

  // ── Open / close ────────────────────────────────────────────────────────
  toggle() {
    this._build();
    const open = document.getElementById('llm-section').classList.toggle('visible');
    if (open && typeof Sidebar !== 'undefined') Sidebar.closeOtherPanels('llm');
    if (typeof Sidebar !== 'undefined') Sidebar.syncClusterBar();
  },

  isOpen() {
    const sec = document.getElementById('llm-section');
    return !!(sec && sec.classList.contains('visible'));
  },

  // ── Form helpers ────────────────────────────────────────────────────────
  _applyPreset(key, overwrite) {
    const p = this.PRESETS[key] || this.PRESETS.custom;
    const ep = document.getElementById('llm-endpoint');
    const md = document.getElementById('llm-model');
    if (overwrite || !ep.value) ep.value = p.endpoint;
    if (overwrite || !md.value) md.value = p.model;
    const hint = document.getElementById('llm-hint');
    if (hint) hint.textContent = p.note || '';
  },

  // Returns the saved config ({provider, endpoint, model, key, secret}) or null.
  // Used by the RCA call (api.js) to pass per-request LLM settings to the backend.
  getConfig() {
    try { return JSON.parse(localStorage.getItem(this.STORAGE_KEY) || 'null'); }
    catch (_) { return null; }
  },

  _loadFromStorage() {
    let cfg = null;
    try { cfg = JSON.parse(localStorage.getItem(this.STORAGE_KEY) || 'null'); } catch (_) {}
    const provider = (cfg && this.PRESETS[cfg.provider]) ? cfg.provider : 'claude';
    document.getElementById('llm-provider').value = provider;
    if (cfg) {
      document.getElementById('llm-endpoint').value = cfg.endpoint || '';
      document.getElementById('llm-model').value    = cfg.model    || '';
      document.getElementById('llm-key').value       = cfg.key      || '';
      document.getElementById('llm-secret').value    = cfg.secret   || '';
      const hint = document.getElementById('llm-hint');
      if (hint) hint.textContent = (this.PRESETS[provider] || {}).note || '';
    } else {
      this._applyPreset(provider, true);
    }
  },

  _save() {
    const cfg = {
      provider: document.getElementById('llm-provider').value,
      endpoint: document.getElementById('llm-endpoint').value.trim(),
      model:    document.getElementById('llm-model').value.trim(),
      key:      document.getElementById('llm-key').value,
      secret:   document.getElementById('llm-secret').value,
    };
    try {
      localStorage.setItem(this.STORAGE_KEY, JSON.stringify(cfg));
      this._flash('Saved ✓ — will be used on the next "Analyze with AI"');
    } catch (e) {
      this._flash('Could not save: ' + e.message, true);
    }
  },

  _clear() {
    try { localStorage.removeItem(this.STORAGE_KEY); } catch (_) {}
    ['llm-endpoint', 'llm-model', 'llm-key', 'llm-secret'].forEach(id => { document.getElementById(id).value = ''; });
    document.getElementById('llm-provider').value = 'claude';
    this._applyPreset('claude', true);
    this._flash('Cleared');
  },

  _flash(msg, isErr) {
    const el = document.getElementById('llm-msg');
    if (!el) return;
    el.textContent = msg;
    el.className = 'llm-msg' + (isErr ? ' err' : ' ok');
    clearTimeout(this._msgTimer);
    this._msgTimer = setTimeout(() => { el.textContent = ''; el.className = 'llm-msg'; }, 4000);
  },
};

// Expose on window so other scripts (e.g. Sidebar) can reach it — top-level
// `const` declarations are not added to the global object.
window.LlmConfigPanel = LlmConfigPanel;
