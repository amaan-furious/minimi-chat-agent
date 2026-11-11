/**
 * app.js — minimi Chat Agent Frontend
 *
 * Architecture:
 *   State  → single source of truth object
 *   Render → pure functions that read state and update DOM
 *   Events → update state then call render
 *   SSE    → streams server events, updates message in real-time
 */

'use strict';

// ── Config ────────────────────────────────────────────────────────────────────
const API_BASE = 'http://localhost:8000';

// ── State ─────────────────────────────────────────────────────────────────────
const state = {
  sessions: [],
  activeSessionId: null,
  messages: [],         // {id, role, content, created_at}
  streaming: false,
  currentEventSource: null,
};

// ── DOM refs ──────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const sessionsList     = $('sessionsList');
const messagesEl       = $('messages');
const messagesContainer = $('messagesContainer');
const emptyState       = $('emptyState');
const messageInput     = $('messageInput');
const sendBtn          = $('sendBtn');
const sendIcon         = $('sendIcon');
const stopIcon         = $('stopIcon');
const charCount        = $('charCount');
const toast            = $('toast');
const newChatBtn       = $('newChatBtn');
const sidebarToggle    = $('sidebarToggle');
const sidebar          = $('sidebar');
const suggestionChips  = $('suggestionChips');

// ── Markdown renderer (lightweight) ──────────────────────────────────────────
function renderMarkdown(text) {
  // Escape HTML first
  let html = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  // Code blocks (``` ... ```)
  html = html.replace(/```(\w+)?\n([\s\S]*?)```/g, (_, lang, code) =>
    `<pre><code class="lang-${lang || ''}">${code.trim()}</code></pre>`
  );

  // Inline code
  html = html.replace(/`([^`\n]+)`/g, '<code>$1</code>');

  // Bold **text**
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

  // Italic *text*
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

  // Links [text](url)
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener">$1</a>'
  );

  // Headings
  html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');

  // Horizontal rule
  html = html.replace(/^---+$/gm, '<hr>');

  // Blockquote
  html = html.replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>');

  // Unordered lists
  html = html.replace(/((?:^- .+\n?)+)/gm, match => {
    const items = match.trim().split('\n').map(l => `<li>${l.replace(/^- /, '')}</li>`).join('');
    return `<ul>${items}</ul>`;
  });

  // Ordered lists
  html = html.replace(/((?:^\d+\. .+\n?)+)/gm, match => {
    const items = match.trim().split('\n').map(l => `<li>${l.replace(/^\d+\. /, '')}</li>`).join('');
    return `<ol>${items}</ol>`;
  });

  // Paragraphs — split by double newlines, wrap non-block content
  const blocks = html.split(/\n\n+/);
  html = blocks.map(block => {
    block = block.trim();
    if (!block) return '';
    if (/^<(h[1-3]|ul|ol|pre|blockquote|hr)/.test(block)) return block;
    return `<p>${block.replace(/\n/g, '<br>')}</p>`;
  }).join('\n');

  return html;
}

// ── Time formatting ───────────────────────────────────────────────────────────
function formatTime(isoString) {
  const d = new Date(isoString);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatDate(isoString) {
  const d = new Date(isoString);
  const now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60)      return 'just now';
  if (diff < 3600)    return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400)   return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800)  return `${Math.floor(diff / 86400)}d ago`;
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

// ── API helpers ───────────────────────────────────────────────────────────────
async function api(path, opts = {}) {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...opts.headers },
    ...opts,
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.json();
}

// ── Sessions ──────────────────────────────────────────────────────────────────
async function loadSessions() {
  try {
    state.sessions = await api('/sessions');
    renderSessions();
  } catch {
    sessionsList.innerHTML = '<div class="sessions-empty">Could not load sessions</div>';
  }
}

function renderSessions() {
  if (!state.sessions.length) {
    sessionsList.innerHTML = '<div class="sessions-empty">No chats yet</div>';
    return;
  }
  sessionsList.innerHTML = state.sessions.map(s => `
    <div class="session-item ${s.id === state.activeSessionId ? 'active' : ''}"
         data-id="${s.id}" role="button" tabindex="0">
      <svg class="session-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
      </svg>
      <div class="session-info">
        <div class="session-title">${escHtml(s.title || 'New chat')}</div>
        <div class="session-date">${formatDate(s.updated_at)}</div>
      </div>
    </div>
  `).join('');

  // Attach click handlers
  sessionsList.querySelectorAll('.session-item').forEach(el => {
    el.addEventListener('click', () => loadSession(el.dataset.id));
    el.addEventListener('keydown', e => e.key === 'Enter' && loadSession(el.dataset.id));
  });
}

async function createNewSession() {
  try {
    const session = await api('/sessions', { method: 'POST', body: JSON.stringify({}) });
    state.sessions.unshift(session);
    await loadSession(session.id);
  } catch (e) {
    showToast('Failed to create session', 'error');
  }
}

async function loadSession(sessionId) {
  try {
    state.activeSessionId = sessionId;
    const data = await api(`/sessions/${sessionId}`);
    state.messages = data.messages;
    renderSessions();
    renderMessages();
    closeSidebar();
  } catch {
    showToast('Failed to load session');
  }
}

// ── Messages ──────────────────────────────────────────────────────────────────
function renderMessages() {
  const hasMessages = state.messages.some(m => m.role === 'user' || m.role === 'assistant');

  if (hasMessages) {
    emptyState.style.display = 'none';
    messagesContainer.style.display = 'flex';
  } else {
    emptyState.style.display = 'flex';
    messagesContainer.style.display = 'none';
    return;
  }

  messagesEl.innerHTML = state.messages
    .filter(m => m.role === 'user' || m.role === 'assistant')
    .map(m => buildMessageHTML(m))
    .join('');

  scrollToBottom();
}

function buildMessageHTML(msg) {
  const isUser = msg.role === 'user';
  const avatar = isUser ? '👤' : 'm';
  const time   = formatTime(msg.created_at);

  return `
    <div class="message ${msg.role}" data-id="${msg.id ?? ''}">
      <div class="msg-avatar">${avatar}</div>
      <div class="msg-body">
        <div class="msg-meta">
          <span>${isUser ? 'You' : 'minimi'}</span>
          <span>·</span>
          <span>${time}</span>
        </div>
        <div class="msg-bubble">${isUser ? escHtml(msg.content) : renderMarkdown(msg.content)}</div>
      </div>
    </div>
  `;
}

function appendUserMessage(content) {
  const msg = {
    id: Date.now(),
    role: 'user',
    content,
    created_at: new Date().toISOString(),
  };
  state.messages.push(msg);

  emptyState.style.display = 'none';
  messagesContainer.style.display = 'flex';

  const el = document.createElement('div');
  el.innerHTML = buildMessageHTML(msg);
  messagesEl.appendChild(el.firstElementChild);
  scrollToBottom();
}

function createAssistantMessageEl() {
  const wrapper = document.createElement('div');
  wrapper.className = 'message assistant';
  wrapper.innerHTML = `
    <div class="msg-avatar">m</div>
    <div class="msg-body">
      <div class="msg-meta">
        <span>minimi</span>
        <span>·</span>
        <span>${formatTime(new Date().toISOString())}</span>
      </div>
      <div class="msg-tools" id="msgTools"></div>
      <div class="msg-bubble" id="streamBubble"><span class="cursor"></span></div>
    </div>
  `;
  messagesEl.appendChild(wrapper);
  scrollToBottom();
  return wrapper;
}

// ── Sending a message ─────────────────────────────────────────────────────────
async function sendMessage() {
  const text = messageInput.value.trim();
  if (!text || state.streaming) return;

  // Ensure we have a session
  if (!state.activeSessionId) {
    await createNewSession();
  }

  messageInput.value = '';
  updateCharCount();
  resizeInput();

  appendUserMessage(text);
  setStreaming(true);

  const assistantEl = createAssistantMessageEl();
  const streamBubble = assistantEl.querySelector('#streamBubble');
  const msgTools     = assistantEl.querySelector('#msgTools');

  let accumulatedText = '';
  let hasStartedText  = false;

  try {
    const response = await fetch(`${API_BASE}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: state.activeSessionId,
        message: text,
      }),
    });

    if (!response.ok) throw new Error(`Server error ${response.status}`);

    const reader = response.body.getReader();
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
        if (!raw || raw === '[DONE]') continue;

        try {
          const event = JSON.parse(raw);
          handleStreamEvent(event, streamBubble, msgTools);

          if (event.type === 'token') {
            accumulatedText += event.data;
            hasStartedText = true;
          }
          if (event.type === 'done') break;
        } catch { /* skip malformed JSON */ }
      }
    }

    // Final render of accumulated text
    if (accumulatedText) {
      streamBubble.innerHTML = renderMarkdown(accumulatedText);
    } else if (!hasStartedText) {
      streamBubble.innerHTML = '<em style="color:var(--text-2)">No response generated.</em>';
    }

    // Update sessions list (title may have changed)
    await loadSessions();

  } catch (e) {
    streamBubble.innerHTML = `<span style="color:var(--red)">Error: ${escHtml(e.message)}</span>`;
  } finally {
    setStreaming(false);
    scrollToBottom();
  }
}

function handleStreamEvent(event, streamBubble, msgTools) {
  switch (event.type) {
    case 'token': {
      // Remove cursor if present, append token
      const cursor = streamBubble.querySelector('.cursor');
      if (cursor) cursor.remove();
      // For streaming, show raw text with cursor (full re-render at end)
      const textNode = document.createTextNode(event.data);
      streamBubble.appendChild(textNode);
      const cur = document.createElement('span');
      cur.className = 'cursor';
      streamBubble.appendChild(cur);
      scrollToBottom();
      break;
    }

    case 'tool_call': {
      const ind = document.createElement('div');
      ind.className = 'tool-indicator';
      ind.id = `tool-${event.data.tool}`;
      ind.innerHTML = `
        <div class="tool-spinner"></div>
        <span>Searching: <strong>${escHtml(event.data.args?.query || '')}</strong></span>
      `;
      msgTools.appendChild(ind);
      scrollToBottom();
      break;
    }

    case 'tool_result': {
      const ind = msgTools.querySelector(`#tool-${event.data.tool}`);
      if (ind) {
        ind.classList.add('done');
        ind.innerHTML = `
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
            <polyline points="20 6 9 17 4 12"/>
          </svg>
          <span>Found ${event.data.result_count} results via <strong>${event.data.source}</strong></span>
        `;
      }
      break;
    }

    case 'error': {
      streamBubble.innerHTML = `<span style="color:var(--red)">⚠ ${escHtml(event.data)}</span>`;
      break;
    }
  }
}

// ── UI state helpers ──────────────────────────────────────────────────────────
function setStreaming(active) {
  state.streaming = active;
  sendBtn.disabled = active ? false : !messageInput.value.trim();

  if (active) {
    sendIcon.style.display = 'none';
    stopIcon.style.display = 'block';
    sendBtn.title = 'Stop';
    sendBtn.onclick = stopStreaming;
  } else {
    sendIcon.style.display = 'block';
    stopIcon.style.display = 'none';
    sendBtn.title = 'Send';
    sendBtn.onclick = sendMessage;
    updateSendBtn();
  }
}

function stopStreaming() {
  // Simply flag as not streaming — the SSE reader will naturally abort
  setStreaming(false);
  showToast('Stopped');
}

function scrollToBottom() {
  messagesContainer.scrollTo({ top: messagesContainer.scrollHeight, behavior: 'smooth' });
}

function updateCharCount() {
  const len = messageInput.value.length;
  charCount.textContent = `${len} / 4000`;
  charCount.style.color = len > 3800 ? 'var(--red)' : 'var(--text-2)';
}

function updateSendBtn() {
  sendBtn.disabled = !messageInput.value.trim() && !state.streaming;
}

function resizeInput() {
  messageInput.style.height = 'auto';
  messageInput.style.height = Math.min(messageInput.scrollHeight, 200) + 'px';
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function showToast(msg, type = 'info') {
  toast.textContent = msg;
  toast.style.borderColor = type === 'error' ? 'var(--red)' : 'var(--border)';
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 2500);
}

function closeSidebar() {
  sidebar.classList.remove('open');
}

// ── Event listeners ───────────────────────────────────────────────────────────
messageInput.addEventListener('input', () => {
  updateCharCount();
  updateSendBtn();
  resizeInput();
});

messageInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

sendBtn.addEventListener('click', sendMessage);
newChatBtn.addEventListener('click', createNewSession);

sidebarToggle.addEventListener('click', () => {
  sidebar.classList.toggle('open');
});

// Suggestion chips
suggestionChips.querySelectorAll('.chip').forEach(chip => {
  chip.addEventListener('click', () => {
    messageInput.value = chip.dataset.query;
    updateCharCount();
    updateSendBtn();
    resizeInput();
    messageInput.focus();
    sendMessage();
  });
});

// Close sidebar on outside click (mobile)
document.addEventListener('click', e => {
  if (window.innerWidth <= 768 &&
      sidebar.classList.contains('open') &&
      !sidebar.contains(e.target) &&
      e.target !== sidebarToggle) {
    closeSidebar();
  }
});

// ── Init ──────────────────────────────────────────────────────────────────────
(async function init() {
  await loadSessions();

  // Auto-select most recent session if exists
  if (state.sessions.length > 0) {
    await loadSession(state.sessions[0].id);
  }

  messageInput.focus();
})();
