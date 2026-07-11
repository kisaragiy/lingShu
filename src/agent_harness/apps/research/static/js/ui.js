// ui.js — Keyboard shortcuts, toast queue, router, and UI framework

// ─── Keyboard shortcuts ───
document.addEventListener('keydown', function(e) {
  // Ctrl+Shift+S: focus search
  if (e.ctrlKey && e.shiftKey && e.key === 'S') {
    e.preventDefault();
    const input = document.getElementById('sidebar-search-input');
    if (input) { input.focus(); input.select(); }
  }
  // ? : show help
  if (e.key === '?' && !e.ctrlKey && !e.metaKey && !e.target.matches('input,textarea')) {
    e.preventDefault();
    showHelpModal();
  }
  // Ctrl+Shift+F: message search
  if (e.ctrlKey && e.shiftKey && e.key === 'F') {
    e.preventDefault();
    toggleMessageSearch();
  }
  // Ctrl+K: focus sidebar search
  if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
    e.preventDefault();
    const input = document.getElementById('sidebar-search-input');
    if (input) { input.focus(); input.select(); }
  }
  // Ctrl+Shift+N: new session
  if (e.ctrlKey && e.shiftKey && e.key === 'N') {
    e.preventDefault();
    switchToNewSession();
    const tab = document.querySelector('.main-tab[data-tab="chat"]');
    if (tab) switchTab('chat');
  }
});

function showHelpModal() {
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.4);z-index:9999;display:flex;align-items:center;justify-content:center';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = '<div class="card" style="max-width:480px;width:90%;padding:32px;max-height:80vh;overflow:auto">' +
    '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px"><h2 style="margin:0">⌨️ 快捷键</h2><button class="btn btn-sm" onclick="this.closest(\'div[style]\').remove()" style="font-size:18px">✕</button></div>' +
    '<table style="width:100%"><tr><td style="padding:8px 0"><kbd>Enter</kbd></td><td>发送消息</td></tr>' +
    '<tr><td style="padding:8px 0"><kbd>Ctrl+Enter</kbd></td><td>换行</td></tr>' +
    '<tr><td style="padding:8px 0"><kbd>Esc</kbd></td><td>取消/关闭</td></tr>' +
    '<tr><td style="padding:8px 0"><kbd>Ctrl+Shift+S</kbd></td><td>搜索会话</td></tr>' +
    '<tr><td style="padding:8px 0"><kbd>Ctrl+Shift+F</kbd></td><td>搜索消息内容</td></tr>' +
    '<tr><td style="padding:8px 0"><kbd>Ctrl+K</kbd></td><td>聚焦会话搜索</td></tr>' +
    '<tr><td style="padding:8px 0"><kbd>Ctrl+Shift+N</kbd></td><td>新建对话</td></tr>' +
    '<tr><td style="padding:8px 0"><kbd>Ctrl+P</kbd></td><td>打印报告（打开报告时）</td></tr>' +
    '<tr><td style="padding:8px 0"><kbd>?</kbd></td><td>显示此帮助</td></tr>' +
    '</table></div>';
  document.body.appendChild(overlay);
  // Close on Escape
  const closeHandler = (ev) => { if (ev.key === 'Escape') { overlay.remove(); document.removeEventListener('keydown', closeHandler); } };
  document.addEventListener('keydown', closeHandler);
}

// ─── Toast upgrade ───
const TOAST_QUEUE = [];
let _toastShowing = false;

function toast(msg, type='info') {
  TOAST_QUEUE.push({msg, type});
  if (!_toastShowing) _showNextToast();
}
function _showNextToast() {
  if (TOAST_QUEUE.length === 0) { _toastShowing = false; return; }
  _toastShowing = true;
  const {msg, type} = TOAST_QUEUE.shift();
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast toast-' + type + ' show';
  setTimeout(() => {
    el.classList.remove('show');
    setTimeout(_showNextToast, 300);
  }, 3000);
}

// ─── Router ───
async function init() {
  // Check first-boot (admin not created yet)
  if (window.__NEEDS_ADMIN__) {
    renderSetupAdmin();
    return;
  }

  // Check authentication
  const status = await authCheck();
  if (status !== 'authenticated') {
    renderLogin();
    return;
  }

  // Authenticated — check setup status
  try {
    const config = await API.get('/v1/setup/config');
    if (config.setup_complete) {
      renderDashboard();
    } else {
      renderSetup();
    }
  } catch(e) {
    renderDashboard();
  }
  // Hide splash
  setTimeout(() => document.getElementById('splash').classList.add('hidden'), 200);
}

// ── Settings dropdown ──
function toggleSettingsMenu() {
  document.getElementById('settings-dropdown').classList.toggle('hidden');
}
function closeSettingsMenu() {
  document.getElementById('settings-dropdown').classList.add('hidden');
}

// ═══════════════════════════════════
// TAB SWITCHING
// ═══════════════════════════════════
function switchTab(tab) {
  dashTab = tab;
  document.querySelectorAll('.main-tab').forEach(a => a.classList.remove('active'));
  document.querySelector(`.main-tab[data-tab="${tab}"]`)?.classList.add('active');
  closeSettingsMenu();

  const content = document.getElementById('dash-content');
  if (tab === 'chat') renderDashChat();
  else if (tab === 'status') renderDashStatus();
  else if (tab === 'knowledge') renderDashKnowledge();
  else if (tab === 'sessions') renderDashSessions();
  else if (tab === 'settings') renderDashSettings();
  else if (tab === 'mcp') renderMCP();
  else if (tab === 'reports') renderReports();
  else if (tab === 'skills') renderSkills();
  else if (tab === 'admin-users') renderAdminUsers();
  else if (tab === 'scheduler') renderScheduler();
  return false;
}

// ═══════════════════════════════════
// STATUS TAB + HEALTH BAR
// ═══════════════════════════════════

// Update the status bar in sidebar
async function updateHealthBar() {
  const dot = document.getElementById('health-dot');
  const text = document.getElementById('health-text');
  if (!dot) return;
  try {
    const health = await API.get('/health');
    dot.className = 'status-dot ok';
    text.textContent = '运行中 · ' + (health.active_sessions || 0) + ' 会话';
  } catch(e) {
    dot.className = 'status-dot fail';
    text.textContent = 'API 离线';
  }
}

async function cancelTask(sessionId) {
  try { await fetch('/v1/tasks/' + sessionId + '/cancel', { method: 'POST' }); toast('已发送取消请求', 'info'); renderDashStatus(); }
  catch(e) { toast('取消失败: ' + e, 'err'); }
}

// Download export file
function downloadExport(url, filename) {
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  toast('正在下载 ' + filename + '...', 'info');
}

// Agent execution log viewer
async function showAgentLog(sessionId) {
  const panel = document.getElementById('agent-log-panel');
  if (panel) { panel.remove(); return; }
  const div = document.createElement('div');
  div.id = 'agent-log-panel';
  div.className = 'agent-log-panel';
  div.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;padding:12px 16px;border-bottom:1px solid var(--border)"><strong>🔍 运行详情</strong><button class="btn btn-sm" onclick="this.parentElement.parentElement.remove()" style="font-size:16px">✕</button></div><div id="agent-log-content" class="agent-log-content"><div class="text-center text-sm" style="padding:20px"><span class="spinner"></span> 加载中...</div></div>';
  document.body.appendChild(div);
  try {
    const data = await API.get('/v1/sessions/' + sessionId + '/logs');
    const logs = data.logs || [];
    const content = document.getElementById('agent-log-content');
    if (logs.length === 0) {
      content.innerHTML = '<div class="text-center text-muted text-sm" style="padding:20px">暂无运行记录</div>';
      return;
    }
    content.innerHTML = logs.map(e => {
      const d = e.data || {};
      const ts = new Date(e.ts * 1000).toLocaleTimeString('zh-CN');
      let html = '<div class="log-entry">';
      html += '<span class="log-ts">' + ts + '</span> ';
      if (e.type === 'finalize') html += '🤖 生成回复 · ' + (d.worker_count || '?') + ' 个 Worker';
      else if (e.type === 'search') html += '🔍 搜索: ' + escHtml(d.query || '') + ' → ' + (d.status || '') + ' (' + (d.count || 0) + ' 结果)';
      else if (e.type === 'llm_call') html += '🧠 LLM 调用 · ' + (d.model || '') + ' · ' + (d.duration || '') + 's';
      else if (e.type === 'worker_start') html += '⚡ Worker: ' + escHtml(d.worker || '');
      else if (e.type === 'worker_end') html += '✅ Worker: ' + escHtml(d.worker || '') + ' · ' + (d.duration || '') + 's';
      else html += escHtml(e.type) + ' · ' + JSON.stringify(d).slice(0, 100);
      html += '</div>';
      return html;
    }).join('');
  } catch(e) {
    document.getElementById('agent-log-content').innerHTML = '<div class="text-center text-muted text-sm" style="padding:20px">加载失败: ' + e.message + '</div>';
  }
}

async function checkUpdate() {
  closeSettingsMenu();
  toast('检查更新中...', 'info');
  try {
    const r = await fetch('https://api.github.com/repos/kisaragiy/lingShu/releases/latest', {signal: AbortSignal.timeout(5000)});
    const data = await r.json();
    const latest = (data.tag_name || '').replace('v', '');
    const current = window.__VERSION__ || '0.41.0';
    if (latest && latest !== current) { toast('发现新版本: v' + latest + ' (当前: v' + current + ')', 'info'); }
    else { toast('已是最新版本 v' + current, 'ok'); }
  } catch(e) { toast('检查更新失败: ' + e.message, 'err'); }
}
