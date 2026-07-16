// sidebar.js — Sidebar state, session list, search, session actions

// ─── Sidebar state ───
let sidebarSessions = [];
let chatSessionId = '';
let chatAbortController = null;
let chatHistory = [];
let sidebarFilter = '';

// ═══════════════════════════════════
// SIDEBAR
// ═══════════════════════════════════
async function loadSidebarSessions() {
  try {
    const data = await API.get('/v1/sessions');
    sidebarSessions = data.sessions || [];
  } catch(e) {
    sidebarSessions = [];
  }
  renderSidebarList();
}

function renderSidebarList() {
  const list = document.getElementById('sidebar-list');
  if (!list) return;
  const filter = sidebarFilter.toLowerCase();
  const filtered = sidebarSessions.filter(s =>
    !filter || (s.preview || '').toLowerCase().includes(filter) || s.id.toLowerCase().includes(filter)
  );
  if (filtered.length === 0) {
    list.innerHTML = '<div class="text-center text-muted text-sm" style="padding:20px">暂无会话</div>';
    return;
  }
  list.innerHTML = filtered.map(s => {
    const isActive = s.id === chatSessionId;
    const displayName = (s.title || s.preview || '新会话').slice(0, 35);
    const pinIcon = s.pinned ? '📌 ' : '';
    const time = formatSidebarTime(s.last_active);
    return `<div class="session-item ${isActive ? 'active' : ''}" onclick="switchToSession('${s.id}')" data-sid="${s.id}">
      <div class="session-item-title">${pinIcon}${escHtml(displayName)}</div>
      <div class="session-item-preview">${s.exchanges || 0} 条消息 · ${time}</div>
      <button class="btn btn-sm" style="padding:1px 6px;font-size:10px;margin-right:2px" onclick="event.stopPropagation();renameSession('${s.id}')">✏️</button>
      <button class="btn btn-sm" style="padding:1px 6px;font-size:10px;margin-right:2px" onclick="event.stopPropagation();togglePinSession('${s.id}',${!s.pinned})">${s.pinned ? '📌' : '📍'}</button>
      <button class="btn btn-sm" style="padding:1px 6px;font-size:10px" onclick="event.stopPropagation();downloadExport('/v1/sessions/${s.id}/export','session_${s.id}.json')">⬇</button>
    </div>`;
  }).join('');
}

function formatSidebarTime(seconds) {
  if (seconds == null) return '';
  if (seconds < 60) return '刚刚';
  if (seconds < 3600) return Math.floor(seconds/60) + '分前';
  if (seconds < 86400) return Math.floor(seconds/3600) + '时前';
  return Math.floor(seconds/86400) + '天前';
}

// ─── Session actions ───
function renameSession(sessionId) {
  const newTitle = prompt('输入新的会话名称:', '');
  if (!newTitle || newTitle.trim() === '') return;
  _origFetch('/v1/sessions/' + sessionId + '/meta', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({title: newTitle.trim()}),
  }).then(() => { loadSidebarSessions(); }).catch(() => {});
}

function togglePinSession(sessionId, pin) {
  _origFetch('/v1/sessions/' + sessionId + '/meta', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({pinned: pin}),
  }).then(() => { loadSidebarSessions(); }).catch(() => {});
}

// ─── Sidebar toggle ───
let _sidebarOpen = true;

function toggleSidebar() {
  _sidebarOpen = !_sidebarOpen;
  document.body.classList.toggle('sidebar-collapsed', !_sidebarOpen);
  localStorage.setItem('lingShu_sidebar_open', _sidebarOpen ? '1' : '0');
}

function initSidebarState() {
  const saved = localStorage.getItem('lingShu_sidebar_open');
  if (saved === '0') {
    _sidebarOpen = false;
    document.body.classList.add('sidebar-collapsed');
  } else {
    _sidebarOpen = true;
    document.body.classList.remove('sidebar-collapsed');
  }
}
// Initialize on load
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initSidebarState);
} else {
  initSidebarState();
}

function onSidebarFilterChange() {
  sidebarFilter = document.getElementById('sidebar-search-input').value;
  renderSidebarList();
  // Save to search history
  saveSearchHistory(sidebarFilter);
}

// ─── Search history suggestions ───
function getSearchHistory() {
  try { return JSON.parse(localStorage.getItem('lingShu_search_history') || '[]'); } catch(e) { return []; }
}
function saveSearchHistory(q) {
  if (!q || q.length < 2) return;
  let history = getSearchHistory();
  history = history.filter(h => h !== q);
  history.unshift(q);
  if (history.length > 10) history = history.slice(0, 10);
  localStorage.setItem('lingShu_search_history', JSON.stringify(history));
}
function showSearchSuggestions() {
  const el = document.getElementById('sidebar-search-suggestions');
  const input = document.getElementById('sidebar-search-input');
  if (!el || !input) return;
  const history = getSearchHistory().filter(h => h !== input.value);
  if (history.length === 0) { el.classList.add('hidden'); return; }
  el.innerHTML = history.map(h =>
    '<div class="search-suggestion-item" onmousedown="event.preventDefault(); document.getElementById(\'sidebar-search-input\').value=\'' +
    escHtml(h.replace(/'/g, "\\'")) + '\'; onSidebarFilterChange(); hideSearchSuggestions()">' +
    '<span class="search-suggestion-icon">🕐</span>' + escHtml(h) + '</div>'
  ).join('');
  el.classList.remove('hidden');
}
function hideSearchSuggestions() {
  const el = document.getElementById('sidebar-search-suggestions');
  if (el) el.classList.add('hidden');
}

// ─── Message search (cross-session content search) ───
let _msgSearchVisible = false;

function toggleMessageSearch() {
  _msgSearchVisible = !_msgSearchVisible;
  const list = document.getElementById('sidebar-list');
  if (!_msgSearchVisible) {
    renderSidebarList();
    return;
  }
  list.innerHTML = '<div style="padding:12px"><div class="form-group"><input class="form-input" id="msg-search-input" placeholder="搜索所有会话的消息内容..." autofocus onkeydown="if(event.key===\'Enter\') doMsgSearch()" style="font-size:12px"></div><div id="msg-search-results"></div></div>';
  setTimeout(() => document.getElementById('msg-search-input').focus(), 100);
}

async function doMsgSearch() {
  const q = document.getElementById('msg-search-input').value.trim();
  const resultsEl = document.getElementById('msg-search-results');
  if (!q) { resultsEl.innerHTML = ''; return; }
  resultsEl.innerHTML = '<div class="text-center text-sm"><span class="spinner"></span> 搜索中...</div>';
  try {
    const data = await API.get('/v1/search/messages?q=' + encodeURIComponent(q));
    const results = data.results || [];
    if (results.length === 0) {
      resultsEl.innerHTML = '<div class="text-center text-muted text-sm" style="padding:12px">未找到匹配消息</div>';
      return;
    }
    resultsEl.innerHTML = results.map(r => {
      const icon = r.role === 'user' ? '🧑' : '🤖';
      const title = escHtml((r.session_title || '').slice(0, 30));
      const preview = escHtml((r.content_preview || '').slice(0, 120));
      return '<div class="session-item" onclick="switchToSession(\'' + r.session_id + '\'); toggleMessageSearch()" style="cursor:pointer">' +
        '<div class="session-item-title">' + icon + ' ' + title + ' <span class="text-muted" style="font-size:10px">' + r.time + '</span></div>' +
        '<div class="session-item-preview">' + preview + '</div></div>';
    }).join('');
  } catch(e) {
    resultsEl.innerHTML = '<div class="text-center text-muted text-sm" style="padding:12px">搜索失败</div>';
  }
}

async function switchToSession(sessionId) {
  if (sessionId === chatSessionId) return;
  chatSessionId = sessionId;
  renderSidebarList();
  await restoreChatSession(sessionId);
}

function switchToNewSession() {
  chatSessionId = 'chat-' + Date.now() + '-' + Math.random().toString(36).slice(2,6);
  chatHistory = [];
  renderSidebarList();
  const container = document.getElementById('chat-messages');
  if (container) {
    container.innerHTML = '<div class="text-center text-muted text-sm" style="padding:40px">输入消息开始与灵枢对话</div>';
  }
  const count = document.getElementById('chat-msg-count');
  if (count) count.textContent = '';
}
