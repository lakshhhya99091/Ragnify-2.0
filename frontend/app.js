/**
 * Ragnify — Frontend Application Logic
 * Handles: upload, polling, chat, streaming SSE, UI state
 */

// Auto-detect backend URL:
//   - On Render (production), FastAPI serves the frontend, so use same origin.
//   - In local dev, fall back to localhost:8000.
const API_BASE = (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')
  ? 'http://localhost:8000'
  : window.location.origin;

// ── State ─────────────────────────────────────────────────────────────────
let state = {
  currentDocId: null,
  documents: {},         // doc_id → doc info
  chatHistory: {},       // doc_id → [ {role, content, sources} ]
  isStreaming: false,
  pollingIntervals: {},  // temp_token → intervalId
};

// ── DOM References ────────────────────────────────────────────────────────
const $  = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const uploadZone       = $('#uploadZone');
const fileInput        = $('#fileInput');
const uploadProgress   = $('#uploadProgress');
const progressBar      = $('#progressBar');
const progressStatus   = $('#progressStatus');
const docList          = $('#docList');
const docListEmpty     = $('#docListEmpty');
const docCount         = $('#docCount');
const welcomeScreen    = $('#welcomeScreen');
const chatScreen       = $('#chatScreen');
const chatDocName      = $('#chatDocName');
const chatDocMeta      = $('#chatDocMeta');
const chatDocIcon      = $('#chatDocIcon');
const messagesInner    = $('#messagesInner');
const messagesContainer = $('#messagesContainer');
const questionInput    = $('#questionInput');
const sendBtn          = $('#sendBtn');
const sourcesPanel     = $('#sourcesPanel');
const sourcesList      = $('#sourcesList');
const suggestions      = $('#suggestions');
const processingOverlay = $('#processingOverlay');
const processingMsg    = $('#processingMsg');
const statusDot        = $('#statusDot');
const statusText       = $('#statusText');
const toastContainer   = $('#toastContainer');
const clearChatBtn     = $('#clearChatBtn');
const newDocBtn        = $('#newDocBtn');
const welcomeUploadBtn = $('#welcomeUploadBtn');
const sidebarToggle    = $('#sidebarToggle');
const sidebar          = $('#sidebar');
const settingsBtn      = $('#settingsBtn');
const settingsModal    = $('#settingsModal');
const settingsClose    = $('#settingsClose');
const settingsCancel   = $('#settingsCancel');
const settingsSave     = $('#settingsSave');
const newApiKey        = $('#newApiKey');
const currentKeyDisplay = $('#currentKeyDisplay');
const settingsAlert    = $('#settingsAlert');

// ── Initialization ────────────────────────────────────────────────────────
async function init() {
  bindEvents();
  await checkHealth();
  await loadDocuments();
  autoResizeTextarea();
}

// ── Health Check ─────────────────────────────────────────────────────────
async function checkHealth() {
  try {
    const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(3000) });
    if (res.ok) {
      statusDot.className = 'status-dot online';
      statusText.textContent = 'Connected';
    } else {
      throw new Error('API error');
    }
  } catch {
    statusDot.className = 'status-dot offline';
    statusText.textContent = 'Offline';
    showToast('Cannot connect to Ragnify backend. Is it running?', 'error');
  }
}

// ── Event Binding ─────────────────────────────────────────────────────────
function bindEvents() {
  // Upload zone
  uploadZone.addEventListener('click', () => fileInput.click());
  uploadZone.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); }
  });
  uploadZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadZone.classList.add('dragging');
  });
  uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('dragging'));
  uploadZone.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadZone.classList.remove('dragging');
    const file = e.dataTransfer.files[0];
    if (file) handleFileUpload(file);
  });
  fileInput.addEventListener('change', () => {
    if (fileInput.files[0]) handleFileUpload(fileInput.files[0]);
  });

  // Welcome CTA
  welcomeUploadBtn.addEventListener('click', () => fileInput.click());

  // Chat input
  questionInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (!sendBtn.disabled) sendQuestion();
    }
  });
  questionInput.addEventListener('input', () => {
    autoResizeTextarea();
    sendBtn.disabled = !questionInput.value.trim() || state.isStreaming;
  });

  sendBtn.addEventListener('click', sendQuestion);
  clearChatBtn.addEventListener('click', clearChat);
  newDocBtn.addEventListener('click', () => fileInput.click());
  sidebarToggle.addEventListener('click', () => sidebar.classList.toggle('open'));

  // Close sidebar on mobile when clicking outside
  document.addEventListener('click', (e) => {
    if (window.innerWidth < 768 && sidebar.classList.contains('open')) {
      if (!sidebar.contains(e.target)) sidebar.classList.remove('open');
    }
  });

  // Settings modal
  settingsBtn.addEventListener('click', openSettings);
  settingsClose.addEventListener('click', closeSettings);
  settingsCancel.addEventListener('click', closeSettings);
  settingsSave.addEventListener('click', saveSettings);
  newApiKey.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') saveSettings();
    if (e.key === 'Escape') closeSettings();
  });
  settingsModal.addEventListener('click', (e) => {
    if (e.target === settingsModal) closeSettings();
  });
}

// ── Settings Modal ────────────────────────────────────────────────────────
async function openSettings() {
  settingsModal.style.display = 'flex';
  settingsAlert.style.display = 'none';
  settingsAlert.className = 'settings-alert';
  newApiKey.value = '';
  currentKeyDisplay.textContent = 'Loading...';

  try {
    const res = await fetch(`${API_BASE}/settings`);
    const data = await res.json();
    currentKeyDisplay.textContent = data.api_key_masked || 'not set';
  } catch {
    currentKeyDisplay.textContent = 'Could not load';
  }
  setTimeout(() => newApiKey.focus(), 100);
}

function closeSettings() {
  settingsModal.style.display = 'none';
  newApiKey.value = '';
  settingsAlert.style.display = 'none';
}

async function saveSettings() {
  const key = newApiKey.value.trim();
  if (!key) {
    showSettingsAlert('Please enter an API key.', 'error');
    return;
  }
  if (!key.startsWith('sk-') && !key.startsWith('AIza')) {
    showSettingsAlert('Invalid key. Gemini keys start with "AIza", OpenAI keys with "sk-".', 'error');
    return;
  }

  settingsSave.textContent = 'Saving...';
  settingsSave.disabled = true;

  try {
    const res = await fetch(`${API_BASE}/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ gemini_api_key: key }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to update');
    }

    const data = await res.json();
    currentKeyDisplay.textContent = data.api_key_masked;
    showSettingsAlert(`✅ API key updated: ${data.api_key_masked}`, 'success');
    newApiKey.value = '';
    showToast('API key updated! You can now upload documents.', 'success');

    // Re-check health
    setTimeout(() => {
      checkHealth();
      closeSettings();
    }, 1500);

  } catch (err) {
    showSettingsAlert(`❌ ${err.message}`, 'error');
  } finally {
    settingsSave.textContent = 'Update Key';
    settingsSave.disabled = false;
  }
}

function showSettingsAlert(msg, type) {
  settingsAlert.textContent = msg;
  settingsAlert.className = `settings-alert ${type}`;
  settingsAlert.style.display = 'block';
}


// ── Auto-resize textarea ──────────────────────────────────────────────────
function autoResizeTextarea() {
  questionInput.style.height = 'auto';
  questionInput.style.height = Math.min(questionInput.scrollHeight, 150) + 'px';
}

// ── File Upload ───────────────────────────────────────────────────────────
async function handleFileUpload(file) {
  const allowedTypes = [
    'application/pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/msword',
    'image/jpeg', 'image/png', 'image/bmp', 'image/tiff',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'application/vnd.ms-powerpoint',
  ];
  const allowedExts = ['.pdf','.docx','.doc','.jpg','.jpeg','.png','.bmp','.tiff','.pptx','.ppt'];
  const ext = '.' + file.name.split('.').pop().toLowerCase();

  if (!allowedExts.includes(ext)) {
    showToast(`Unsupported file type "${ext}". Supported: ${allowedExts.join(', ')}`, 'error');
    return;
  }

  // Show progress UI
  uploadProgress.style.display = 'block';
  progressBar.style.width = '5%';
  progressStatus.textContent = '📤 Uploading...';
  uploadZone.style.pointerEvents = 'none';

  const formData = new FormData();
  formData.append('file', file);

  try {
    // Simulate progress during upload
    let progress = 5;
    const progressTimer = setInterval(() => {
      progress = Math.min(progress + 3, 40);
      progressBar.style.width = `${progress}%`;
    }, 200);

    const res = await fetch(`${API_BASE}/upload`, {
      method: 'POST',
      body: formData,
    });

    clearInterval(progressTimer);

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Upload failed');
    }

    const data = await res.json();
    progressBar.style.width = '50%';
    progressStatus.textContent = '⚙️ Processing document...';

    // Show processing overlay
    showProcessingOverlay(file.name);

    // Start polling for this document
    startPolling(data.temp_token, file.name);

  } catch (err) {
    uploadProgress.style.display = 'none';
    uploadZone.style.pointerEvents = 'auto';
    showToast(`Upload failed: ${err.message}`, 'error');
  } finally {
    fileInput.value = '';
  }
}

function showProcessingOverlay(filename) {
  processingMsg.textContent = `Preparing "${filename}"...`;
  processingOverlay.style.display = 'flex';
  state._processingStartTime = Date.now();
  resetSteps();
  updateElapsedTime();
}

function hideProcessingOverlay() {
  processingOverlay.style.display = 'none';
  uploadProgress.style.display = 'none';
  uploadZone.style.pointerEvents = 'auto';
  progressBar.style.width = '0%';
  if (state._elapsedTimer) {
    clearInterval(state._elapsedTimer);
    state._elapsedTimer = null;
  }
}

function updateElapsedTime() {
  if (state._elapsedTimer) clearInterval(state._elapsedTimer);
  const elapsedEl = document.getElementById('processingElapsed');
  const pctEl = document.getElementById('processingPct');
  const pBarEl = document.getElementById('processingProgressBar');
  state._elapsedTimer = setInterval(() => {
    if (!state._processingStartTime) return;
    const secs = Math.floor((Date.now() - state._processingStartTime) / 1000);
    if (elapsedEl) {
      const mins = Math.floor(secs / 60);
      const s = secs % 60;
      elapsedEl.textContent = mins > 0 ? `${mins}m ${s}s elapsed` : `${s}s elapsed`;
    }
  }, 1000);
}

function resetSteps() {
  ['parsing','crawling','embedding','indexing'].forEach(s => {
    const el = $(`#step-${s}`);
    if (el) {
      el.classList.remove('active','done');
      el.querySelector('.step-status').className = 'step-status pending';
    }
  });
}

function setStepActive(stepId) {
  const el = $(`#step-${stepId}`);
  if (!el) return;
  el.classList.add('active');
  el.classList.remove('done');
  el.querySelector('.step-status').className = 'step-status active';
}

function setStepDone(stepId) {
  const el = $(`#step-${stepId}`);
  if (!el) return;
  el.classList.remove('active');
  el.classList.add('done');
  el.querySelector('.step-status').className = 'step-status done';
}

function updateStepFromStatus(status, message) {
  const statusMap = {
    'parsing':   () => { setStepActive('parsing'); },
    'crawling':  () => { setStepDone('parsing'); setStepActive('crawling'); },
    'indexing':  () => { setStepDone('parsing'); setStepDone('crawling'); setStepActive('indexing'); },
    'embedding': () => { setStepDone('parsing'); setStepDone('crawling'); setStepActive('embedding'); },
    'ready':     () => { setStepDone('parsing'); setStepDone('crawling'); setStepDone('embedding'); setStepDone('indexing'); },
  };
  const fn = statusMap[status];
  if (fn) fn();
  if (message) processingMsg.textContent = message;

  // Update progress bar and percentage
  const pct = getProgressPct(status);
  const pBarEl = document.getElementById('processingProgressBar');
  const pctEl = document.getElementById('processingPct');
  if (pBarEl) pBarEl.style.width = pct + '%';
  if (pctEl) pctEl.textContent = pct + '%';
}

// ── Polling ───────────────────────────────────────────────────────────────
function startPolling(tempToken, filename) {
  let attempts = 0;
  const maxAttempts = 200; // 10 min max (at 3s intervals)

  const interval = setInterval(async () => {
    attempts++;
    if (attempts > maxAttempts) {
      clearInterval(interval);
      hideProcessingOverlay();
      showToast('Processing timed out. Please try again.', 'error');
      return;
    }

    try {
      const docs = await (await fetch(`${API_BASE}/documents`)).json();
      const allDocs = docs.documents || [];

      // Find the newest ready doc that matches our file
      let found = null;
      for (const doc of allDocs) {
        if (doc.filename === filename && doc.status !== 'error') {
          found = doc;
        }
      }

      // Also look for temp token
      const pending = allDocs.find(d => d.doc_id === tempToken);
      if (pending && pending.status !== 'ready') {
        updateStepFromStatus(pending.status, pending.status_message);
        progressBar.style.width = getProgressPct(pending.status) + '%';
      }

      // Find any ready doc for this file
      const readyDoc = allDocs.find(d => d.filename === filename && d.status === 'ready');

      if (readyDoc) {
        clearInterval(interval);
        delete state.pollingIntervals[tempToken];

        // Update progress bar
        progressBar.style.width = '100%';
        updateStepFromStatus('ready', `✅ "${filename}" is ready!`);

        setTimeout(() => {
          hideProcessingOverlay();
          loadDocuments().then(() => {
            selectDocument(readyDoc.doc_id);
          });
          showToast(`"${filename}" indexed successfully!`, 'success');
        }, 1200);
      }

      // Handle error
      if (found && found.status === 'error') {
        clearInterval(interval);
        hideProcessingOverlay();
        showToast(`Error processing "${filename}": ${found.status_message}`, 'error');
        loadDocuments();
      }

    } catch (err) {
      console.warn('Polling error:', err);
    }
  }, 3000); // Poll every 3 seconds (was 1s — too aggressive)

  state.pollingIntervals[tempToken] = interval;
}

function getProgressPct(status) {
  const map = { uploading: 10, parsing: 25, crawling: 50, embedding: 75, indexing: 90, ready: 100, error: 100 };
  return map[status] || 20;
}

// ── Document Management ───────────────────────────────────────────────────
async function loadDocuments() {
  try {
    const res = await fetch(`${API_BASE}/documents`);
    const data = await res.json();
    const docs = (data.documents || []).filter(d => d.status !== 'uploading');

    state.documents = {};
    docs.forEach(d => { state.documents[d.doc_id] = d; });

    renderDocList(docs);
    return docs;
  } catch (err) {
    console.error('Failed to load documents:', err);
    return [];
  }
}

function renderDocList(docs) {
  const ready = docs.filter(d => d.status === 'ready');
  const processing = docs.filter(d => d.status !== 'ready' && d.status !== 'error');
  const errors = docs.filter(d => d.status === 'error');
  const all = [...ready, ...processing, ...errors];

  docCount.textContent = ready.length;

  if (all.length === 0) {
    docListEmpty.style.display = 'flex';
    const items = docList.querySelectorAll('.doc-item');
    items.forEach(el => el.remove());
    return;
  }

  docListEmpty.style.display = 'none';

  // Remove stale items
  docList.querySelectorAll('.doc-item').forEach(el => {
    if (!state.documents[el.dataset.docId]) el.remove();
  });

  // Render / update each doc
  all.forEach(doc => {
    let existing = docList.querySelector(`[data-doc-id="${doc.doc_id}"]`);
    if (!existing) {
      existing = createDocItem(doc);
      docList.appendChild(existing);
    } else {
      updateDocItem(existing, doc);
    }
    if (doc.doc_id === state.currentDocId) {
      existing.classList.add('active');
    }
  });
}

function getDocIcon(filename) {
  const ext = (filename || '').split('.').pop().toLowerCase();
  const icons = {
    pdf: '📄', docx: '📝', doc: '📝',
    jpg: '🖼️', jpeg: '🖼️', png: '🖼️', bmp: '🖼️', tiff: '🖼️',
    pptx: '📊', ppt: '📊',
  };
  return icons[ext] || '📁';
}

function createDocItem(doc) {
  const el = document.createElement('div');
  el.className = 'doc-item';
  el.dataset.docId = doc.doc_id;
  el.setAttribute('role', 'listitem');
  el.setAttribute('tabindex', '0');
  el.setAttribute('aria-label', `Document: ${doc.filename}`);
  el.addEventListener('click', () => selectDocument(doc.doc_id));
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') selectDocument(doc.doc_id);
  });
  updateDocItem(el, doc);
  return el;
}

function updateDocItem(el, doc) {
  const statusLabel = { ready: 'Ready', processing: 'Processing…', error: 'Error' }[doc.status] || doc.status;
  const metaText = doc.status === 'ready'
    ? `${doc.num_chunks || 0} chunks · ${doc.num_links || 0} links`
    : (doc.status_message || statusLabel);

  el.innerHTML = `
    <div class="doc-icon">${getDocIcon(doc.filename)}</div>
    <div class="doc-info">
      <div class="doc-name" title="${escapeHtml(doc.filename)}">${escapeHtml(truncate(doc.filename, 28))}</div>
      <div class="doc-meta">
        <div class="doc-status-dot ${doc.status}" aria-hidden="true"></div>
        <span>${escapeHtml(truncate(metaText, 36))}</span>
      </div>
    </div>
    <button class="doc-delete" aria-label="Delete ${escapeHtml(doc.filename)}" onclick="deleteDocument(event, '${doc.doc_id}')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
      </svg>
    </button>
  `;
}

function selectDocument(docId) {
  const doc = state.documents[docId];
  if (!doc || doc.status !== 'ready') {
    showToast('Document is not ready yet.', 'info');
    return;
  }

  state.currentDocId = docId;

  // Update sidebar selection
  $$('.doc-item').forEach(el => el.classList.toggle('active', el.dataset.docId === docId));

  // Update chat header
  chatDocName.textContent = doc.filename;
  chatDocMeta.textContent = `${doc.num_chunks || 0} chunks indexed · ${doc.num_links || 0} hyperlinks · ${doc.num_crawled || 0} pages crawled`;
  chatDocIcon.textContent = getDocIcon(doc.filename);

  // Show chat screen
  welcomeScreen.style.display = 'none';
  chatScreen.style.display = 'flex';

  // Load chat history
  const history = state.chatHistory[docId] || [];
  renderMessages(history);
  sendBtn.disabled = !questionInput.value.trim();

  // Close sidebar on mobile
  if (window.innerWidth < 768) sidebar.classList.remove('open');
}

async function deleteDocument(e, docId) {
  e.stopPropagation();
  const doc = state.documents[docId];
  if (!doc) return;

  if (!confirm(`Delete "${doc.filename}"? This cannot be undone.`)) return;

  try {
    const res = await fetch(`${API_BASE}/documents/${docId}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('Delete failed');

    delete state.documents[docId];
    delete state.chatHistory[docId];

    if (state.currentDocId === docId) {
      state.currentDocId = null;
      welcomeScreen.style.display = 'flex';
      chatScreen.style.display = 'none';
    }

    loadDocuments();
    showToast(`Deleted "${doc.filename}"`, 'success');
  } catch (err) {
    showToast(`Failed to delete: ${err.message}`, 'error');
  }
}

// ── Chat ─────────────────────────────────────────────────────────────────
async function sendQuestion() {
  const question = questionInput.value.trim();
  if (!question || !state.currentDocId || state.isStreaming) return;

  // Hide suggestions after first message
  suggestions.style.display = 'none';
  sourcesPanel.style.display = 'none';

  // Add user message
  addMessage('user', question);
  questionInput.value = '';
  autoResizeTextarea();
  sendBtn.disabled = true;
  state.isStreaming = true;

  // Save to history
  const docId = state.currentDocId;
  if (!state.chatHistory[docId]) state.chatHistory[docId] = [];
  state.chatHistory[docId].push({ role: 'user', content: question });

  // Add typing indicator
  const typingEl = addTypingIndicator();

  try {
    const res = await fetch(`${API_BASE}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ doc_id: docId, question }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Chat request failed');
    }

    // Stream SSE
    typingEl.remove();
    const assistantEl = addMessage('assistant', '', true);
    const bubbleEl = assistantEl.querySelector('.msg-bubble');

    let fullAnswer = '';
    let sources = [];

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop(); // keep incomplete line

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const raw = line.slice(6).trim();
        if (!raw) continue;

        try {
          const event = JSON.parse(raw);

          if (event.type === 'sources') {
            sources = event.sources || [];
          } else if (event.type === 'token') {
            fullAnswer += event.content;
            bubbleEl.innerHTML = renderMarkdown(sanitizeAnswer(fullAnswer));
            scrollToBottom();
          } else if (event.type === 'done') {
            // Show source citations as a clean separate card
            if (sources.length > 0) {
              const capsules = buildSourcesCapsules(sources);
              assistantEl.querySelector('.msg-body').appendChild(capsules);
            }
            break;
          } else if (event.type === 'error') {
            bubbleEl.innerHTML = `<div class="error-msg">❌ ${escapeHtml(event.content)}</div>`;
            break;
          }
        } catch (parseErr) {
          // ignore malformed SSE line
        }
      }
    }

    // Save to history (sanitized)
    state.chatHistory[docId].push({ role: 'assistant', content: sanitizeAnswer(fullAnswer), sources });
    scrollToBottom();

  } catch (err) {
    typingEl.remove();
    addMessage('assistant', `❌ Error: ${err.message}`);
    showToast(`Chat error: ${err.message}`, 'error');
  } finally {
    state.isStreaming = false;
    sendBtn.disabled = !questionInput.value.trim();
  }
}

// SVG logo reused for avatars (matches sidebar Ragnify logo)
const RAGNIFY_LOGO_SVG = `<svg viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg" width="22" height="22">
  <rect width="40" height="40" rx="10" fill="url(#av-logo-grad)"/>
  <path d="M8 28L20 12L32 28" stroke="white" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="20" cy="22" r="3" fill="white"/>
  <defs><linearGradient id="av-logo-grad" x1="0" y1="0" x2="40" y2="40">
    <stop offset="0%" stop-color="#1a56db"/><stop offset="100%" stop-color="#0e4bbd"/>
  </linearGradient></defs>
</svg>`;

function addMessage(role, content, streaming = false) {
  const el = document.createElement('div');
  el.className = `message ${role}`;

  const avatar = role === 'user'
    ? `<div class="msg-avatar user-avatar">${RAGNIFY_LOGO_SVG}</div>`
    : `<div class="msg-avatar assistant-avatar">${RAGNIFY_LOGO_SVG}</div>`;

  const roleLabel = role === 'user' ? 'You' : 'Ragnify';

  el.innerHTML = `
    ${avatar}
    <div class="msg-body">
      <div class="msg-role">${roleLabel}</div>
      <div class="msg-bubble">${streaming ? '' : renderMarkdown(content)}</div>
    </div>
  `;

  messagesInner.appendChild(el);
  scrollToBottom();
  return el;
}

function addTypingIndicator() {
  const el = document.createElement('div');
  el.className = 'message assistant';
  el.innerHTML = `
    <div class="msg-avatar assistant-avatar">${RAGNIFY_LOGO_SVG}</div>
    <div class="msg-body">
      <div class="msg-role">Ragnify</div>
      <div class="msg-bubble">
        <div class="typing-indicator">
          <div class="typing-dot"></div>
          <div class="typing-dot"></div>
          <div class="typing-dot"></div>
        </div>
      </div>
    </div>
  `;
  messagesInner.appendChild(el);
  scrollToBottom();
  return el;
}

function renderMessages(history) {
  messagesInner.innerHTML = '';
  history.forEach(msg => {
    const el = addMessage(msg.role, msg.content);
    if (msg.sources && msg.sources.length > 0) {
      const capsules = buildSourcesCapsules(msg.sources);
      el.querySelector('.msg-body').appendChild(capsules);
    }
  });

  if (history.length > 0) {
    suggestions.style.display = 'none';
  } else {
    suggestions.style.display = 'flex';
  }
}

// ── Source Citation Capsules Builder ──────────────────────────────────────
function buildSourcesCapsules(sources) {
  const wrapper = document.createElement('div');
  wrapper.className = 'sources-capsules';
  wrapper.setAttribute('aria-label', 'Sources cited');

  const header = document.createElement('div');
  header.className = 'sources-capsules-header';
  header.innerHTML = `<span class="sources-capsules-label">📎 Sources Used</span>`;
  wrapper.appendChild(header);

  const chips = document.createElement('div');
  chips.className = 'sources-chips';

  sources.forEach(src => {
    // Extract URL from various formats:
    //   "https://example.com"
    //   "Source: https://example.com (linked from "file.pdf")"
    //   "Page 1" (no URL)
    const urlMatch = src.match(/(https?:\/\/[^\s")\]]+)/i);

    if (urlMatch) {
      const extractedUrl = urlMatch[1];
      const chip = document.createElement('a');
      chip.className = 'source-chip source-chip-link';
      chip.href = extractedUrl;
      chip.target = '_blank';
      chip.rel = 'noopener noreferrer';
      chip.title = extractedUrl;
      // Clean display: just domain + short path
      try {
        const url = new URL(extractedUrl);
        let display = url.hostname.replace('www.', '');
        if (url.pathname.length > 1) {
          const path = url.pathname.length > 25 ? url.pathname.slice(0, 22) + '…' : url.pathname;
          display += path;
        }
        chip.textContent = display;
      } catch {
        chip.textContent = extractedUrl.length > 40 ? extractedUrl.slice(0, 37) + '…' : extractedUrl;
      }
      chips.appendChild(chip);
    } else {
      // Non-URL source (Page ref, table ref, etc.)
      const chip = document.createElement('span');
      chip.className = 'source-chip source-chip-page';
      chip.title = src;
      // Simplify display: strip 'Source:' prefix if present
      let display = src.replace(/^Source:\s*/i, '').trim();
      chip.textContent = display.length > 45 ? display.slice(0, 42) + '…' : display;
      chips.appendChild(chip);
    }
  });

  wrapper.appendChild(chips);
  return wrapper;
}

function clearChat() {
  if (!state.currentDocId) return;
  if (messagesInner.children.length === 0) return;
  if (!confirm('Clear chat history for this document?')) return;
  state.chatHistory[state.currentDocId] = [];
  messagesInner.innerHTML = '';
  suggestions.style.display = 'flex';
  sourcesPanel.style.display = 'none';
}

function scrollToBottom() {
  messagesContainer.scrollTo({ top: messagesContainer.scrollHeight, behavior: 'smooth' });
}

// ── Suggestions ───────────────────────────────────────────────────────────
function useSuggestion(btn) {
  questionInput.value = btn.textContent;
  autoResizeTextarea();
  sendBtn.disabled = false;
  sendQuestion();
}
window.useSuggestion = useSuggestion;

// ── Delete (exposed globally for inline onclick) ───────────────────────────
window.deleteDocument = deleteDocument;

// ── Answer Sanitizer ──────────────────────────────────────────────────────
// Strips any leaked "Sources Used" footer, raw URLs, and markdown links
// that the LLM might still inject despite prompt instructions.
function sanitizeAnswer(text) {
  if (!text) return '';

  // Remove the entire "📎 Sources Used" or "Sources Used" footer section
  // (matches from the header line to the end of the answer)
  text = text.replace(/\n*📎\s*\*{0,2}Sources\s+Used:?\*{0,2}[\s\S]*$/i, '');
  text = text.replace(/\n*\*{0,2}📎\s*Sources\s+Used:?\*{0,2}[\s\S]*$/i, '');
  text = text.replace(/\n*---\n*\*{0,2}Sources\s+Used:?\*{0,2}[\s\S]*$/i, '');
  text = text.replace(/\n*\*{0,2}Sources:?\*{0,2}\s*\n+(?:[-•*]\s+.*\n?)+$/i, '');
  text = text.replace(/\n*\*{0,2}References:?\*{0,2}\s*\n+(?:[-•*]\s+.*\n?)+$/i, '');

  // Remove [Source: URL] style inline citations
  text = text.replace(/\[Source:\s*https?:\/\/[^\]]+\]/gi, '');
  text = text.replace(/\[https?:\/\/[^\]]+\]/gi, '');

  // Remove standalone raw URLs on their own line
  text = text.replace(/^\s*https?:\/\/\S+\s*$/gm, '');

  // Remove markdown hyperlinks like [text](url) — keep just the text
  text = text.replace(/\[([^\]]+)\]\(https?:\/\/[^)]+\)/g, '$1');

  // Clean up extra trailing whitespace/newlines
  text = text.replace(/\n{3,}/g, '\n\n').trimEnd();

  return text;
}

// ── Markdown Renderer (lightweight) ───────────────────────────────────────
function renderMarkdown(text) {
  if (!text) return '';
  let html = escapeHtml(text);

  // Bold
  html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/__(.*?)__/g, '<strong>$1</strong>');

  // Italic
  html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');
  html = html.replace(/_(.*?)_/g, '<em>$1</em>');

  // Inline code
  html = html.replace(/`(.*?)`/g, '<code>$1</code>');

  // Headings
  html = html.replace(/^### (.*$)/gm, '<h4 style="margin:8px 0 4px;font-size:0.9em;color:var(--white)">$1</h4>');
  html = html.replace(/^## (.*$)/gm,  '<h3 style="margin:10px 0 5px;font-size:1em;color:var(--white)">$1</h3>');
  html = html.replace(/^# (.*$)/gm,   '<h2 style="margin:12px 0 6px;font-size:1.1em;color:var(--white)">$1</h2>');

  // Unordered list
  html = html.replace(/^\s*[-•]\s+(.*$)/gm, '<li>$1</li>');
  html = html.replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>');

  // Ordered list
  html = html.replace(/^\s*\d+\.\s+(.*$)/gm, '<li>$1</li>');

  // Horizontal rule
  html = html.replace(/^---+$/gm, '<hr style="border:none;border-top:1px solid rgba(255,255,255,0.1);margin:12px 0">');

  // Paragraphs (double newlines)
  html = html.replace(/\n\n+/g, '</p><p>');
  html = html.replace(/\n/g, '<br>');

  return `<p>${html}</p>`;
}

// ── Toast ─────────────────────────────────────────────────────────────────
function showToast(message, type = 'info', duration = 4000) {
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  const icons = { success: '✅', error: '❌', info: 'ℹ️' };
  toast.innerHTML = `<span>${icons[type] || 'ℹ️'}</span><span>${escapeHtml(message)}</span>`;
  toastContainer.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(20px)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

// ── Utilities ─────────────────────────────────────────────────────────────
function escapeHtml(str) {
  const map = { '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;' };
  return String(str).replace(/[&<>"']/g, c => map[c]);
}

function truncate(str, n) {
  return str.length > n ? str.slice(0, n - 1) + '…' : str;
}

// ── Kick Off ──────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', init);
