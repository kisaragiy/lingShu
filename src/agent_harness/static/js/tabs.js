// tabs.js — All tab render functions (status, knowledge, sessions, skills, settings, MCP, reports, scheduler, admin)
// Loaded after chat.js, sidebar.js, api.js, ui.js
// ─── Settings dropdown ───

// ═══════════════════════════════════
// STATUS TAB
// ═══════════════════════════════════
async function renderDashStatus() {
  const el = document.getElementById('dash-content');
  el.innerHTML = '<h2>📊 服务状态</h2><div id="status-content"><p class="text-muted">加载中...</p></div>';
  try {
    const data = await API.get('/v1/health');
    const statusEl = document.getElementById('status-content');
    // Build status from health data
    let html = '<div class="status-grid">';
    const items = [
      {label: 'API 状态', value: data.status === 'ok' ? '✅ 运行中' : '❌ 异常'},
      {label: '版本', value: data.version || __VERSION__ || '未知'},
      {label: '运行时间', value: data.uptime ? Math.floor(data.uptime / 60) + ' 分钟' : '—'},
    ];
    for (const item of items) {
      html += '<div class="status-card"><div class="status-label">' + item.label + '</div><div class="status-value">' + item.value + '</div></div>';
    }
    html += '</div>';
    statusEl.innerHTML = html;
  } catch(e) {
    document.getElementById('status-content').innerHTML = '<div class="error-card">❌ 获取状态失败: ' + escHtml(e.message) + '</div>';
  }
}

// ═══════════════════════════════════
// KNOWLEDGE TAB
// ═══════════════════════════════════
async function renderDashKnowledge() {
  const el = document.getElementById('dash-content');
  // Core knowledge tab renders upload + search
  let html = '<h2>📚 知识库</h2>';
  html += '<div class="kb-section"><h3>上传文档</h3>';
  html += '<div class="kb-upload"><input type="file" id="kb-file-input" multiple accept=".pdf,.docx,.txt,.md" style="display:none" onchange="uploadKnowledgeFiles(this)">';
  html += '<button class="btn" onclick="document.getElementById(\'kb-file-input\').click()">📁 选择文件</button>';
  html += '<span id="kb-upload-status" class="text-muted" style="margin-left:12px;font-size:12px"></span></div></div>';
  html += '<div class="kb-section"><h3>知识库列表</h3><div id="kb-list"><p class="text-muted">加载中...</p></div></div>';
  html += '<div class="kb-section"><h3>搜索知识库</h3>';
  html += '<div class="kb-search"><input id="kb-search-input" class="form-input" placeholder="搜索知识库内容..." style="flex:1" onkeydown="if(event.key===\'Enter\') searchKnowledge()">';
  html += '<button class="btn btn-primary" onclick="searchKnowledge()">搜索</button></div>';
  html += '<div id="kb-search-results"></div></div>';
  el.innerHTML = html;
  loadKnowledgeBase();
}

let _kbData = null;
async function loadKnowledgeBase() {
  const list = document.getElementById('kb-list');
  if (!list) return;
  try {
    const data = await API.get('/v1/knowledge/list');
    _kbData = data;
    const sources = data.sources || [];
    if (sources.length === 0) {
      list.innerHTML = '<p class="text-muted text-sm">暂无文档，上传后即可检索</p>';
    } else {
      list.innerHTML = sources.map(s =>
        '<div class="kb-item"><span class="kb-icon">📄</span><span>' + escHtml(s.name || s) + '</span></div>'
      ).join('');
    }
  } catch(e) {
    list.innerHTML = '<p class="text-muted text-sm">加载失败: ' + escHtml(e.message) + '</p>';
  }
}

async function uploadKnowledgeFiles(input) {
  const status = document.getElementById('kb-upload-status');
  const files = input.files;
  if (!files || files.length === 0) return;
  status.textContent = '上传中 ' + files.length + ' 个文件...';
  for (const f of files) {
    const fd = new FormData();
    fd.append('file', f);
    try {
      await API.upload('/v1/knowledge/upload', fd);
    } catch(e) { status.textContent = '上传失败: ' + f.name; }
  }
  status.textContent = '✅ 上传完成';
  input.value = '';
  loadKnowledgeBase();
}

async function searchKnowledge() {
  const q = document.getElementById('kb-search-input')?.value?.trim();
  const results = document.getElementById('kb-search-results');
  if (!q || !results) return;
  results.innerHTML = '<p class="text-muted text-sm"><span class="spinner"></span> 搜索中...</p>';
  try {
    const data = await API.get('/v1/knowledge/query?q=' + encodeURIComponent(q));
    const chunks = data.results || [];
    if (chunks.length === 0) {
      results.innerHTML = '<p class="text-muted text-sm">未找到匹配内容</p>';
      return;
    }
    results.innerHTML = chunks.map(c =>
      '<div class="kb-result"><div class="kb-result-source">📄 ' + escHtml(c.source || '') + '</div><div class="kb-result-text">' + escHtml(c.text || '').slice(0, 200) + '</div></div>'
    ).join('');
  } catch(e) {
    results.innerHTML = '<p class="text-muted text-sm">搜索失败: ' + escHtml(e.message) + '</p>';
  }
}

// ═══════════════════════════════════
// SESSIONS TAB
// ═══════════════════════════════════
async function renderDashSessions() {
  const el = document.getElementById('dash-content');
  el.innerHTML = '<h2>💬 会话管理</h2><div id="sessions-content"><p class="text-muted">加载中...</p></div>';
  try {
    const data = await API.get('/v1/sessions');
    const sessions = data.sessions || [];
    const container = document.getElementById('sessions-content');
    if (sessions.length === 0) {
      container.innerHTML = '<p class="text-muted">暂无会话记录</p>';
      return;
    }
    container.innerHTML = sessions.map(s =>
      '<div class="session-item" onclick="switchToSession(\'' + s.id + '\')">' +
        '<div class="session-item-title">💬 ' + escHtml((s.title || '对话').slice(0, 40)) + '</div>' +
        '<div class="session-item-preview">' + escHtml((s.preview || '').slice(0, 80)) + '</div>' +
        '<div class="session-item-meta">' + (s.time || '') + ' · ' + (s.count || 0) + ' 条消息</div>' +
      '</div>'
    ).join('');
  } catch(e) {
    document.getElementById('sessions-content').innerHTML = '<div class="error-card">❌ 加载失败: ' + escHtml(e.message) + '</div>';
  }
}

// ═══════════════════════════════════
// SKILLS TAB
// ═══════════════════════════════════
async function renderSkills() {
  const el = document.getElementById('dash-content');
  el.innerHTML = '<h2>🧠 技能</h2><div id="skills-content"><p class="text-muted">加载中...</p></div>';
  try {
    const data = await API.get('/v1/skills/list');
    const skills = data.skills || [];
    let html = '<div class="skills-list">';
    for (const s of skills) {
      const enabled = s.enabled !== false;
      html += '<div class="skill-item"><div class="skill-name">' + escHtml(s.name) + '</div>' +
        '<div class="skill-desc">' + escHtml(s.description || '').slice(0, 100) + '</div>' +
        '<button class="btn btn-sm ' + (enabled ? 'btn-success' : 'btn-secondary') + '" onclick="toggleSkill(\'' + s.name + '\')">' +
        (enabled ? '✅ 已启用' : '⏸️ 已禁用') + '</button></div>';
    }
    html += '</div>';
    document.getElementById('skills-content').innerHTML = html;
  } catch(e) {
    document.getElementById('skills-content').innerHTML = '<div class="error-card">❌ 加载失败</div>';
  }
}

async function toggleSkill(name) {
  try {
    await API.post('/v1/skills/' + encodeURIComponent(name) + '/toggle', {});
    renderSkills();
  } catch(e) { toast('操作失败: ' + e.message, 'err'); }
}

// ═══════════════════════════════════
// SETTINGS TAB
// ═══════════════════════════════════
function renderDashSettings() {
  const el = document.getElementById('dash-content');
  const isDark = document.body.classList.contains('dark-mode');
  el.innerHTML = `
    <h2>⚙️ 设置</h2>
    <div class="settings-section">
      <div class="settings-item">
        <div class="settings-item-label">🌙 暗色模式</div>
        <div class="settings-item-control">
          <label class="switch"><input type="checkbox" ${isDark ? 'checked' : ''} onchange="toggleDarkMode(); renderDashSettings()"><span class="slider"></span></label>
        </div>
      </div>
      <div class="settings-item">
        <div class="settings-item-label">🧹 清理数据</div>
        <div class="settings-item-control"><button class="btn btn-sm btn-danger" onclick="if(confirm('确定清除所有会话？')) clearAllSessions()">清除所有会话</button></div>
      </div>
    </div>
    <div class="settings-section">
      <h3>关于</h3>
      <p class="text-sm">灵枢 (LingShu Agent) v${window.__VERSION__ || 'dev'}</p>
      <p class="text-sm"><a href="/cs-demo" target="_blank">🎧 智能客服 Demo</a></p>
      <p class="text-sm"><a href="https://github.com/kisaragiy/lingShu" target="_blank">GitHub</a></p>
    </div>`;
}

async function clearAllSessions() {
  try {
    await API.post('/v1/sessions/clear', {});
    chatSessionId = 'chat-' + Date.now() + '-' + Math.random().toString(36).slice(2,6);
    chatHistory = [];
    toast('✅ 已清除所有会话', 'ok');
    renderSidebarList();
  } catch(e) { toast('清除失败: ' + e.message, 'err'); }
}

// ═══════════════════════════════════
// MCP TAB
// ═══════════════════════════════════
async function renderMCP() {
  const el = document.getElementById('dash-content');
  el.innerHTML = '<h2>🔌 MCP 工具</h2><div id="mcp-content"><p class="text-muted">加载中...</p></div>';
  try {
    const data = await API.get('/v1/tools/list');
    const tools = data.tools || [];
    let enabled = [], disabled = [];
    for (const t of tools) {
      if (t.enabled !== false) enabled.push(t);
      else disabled.push(t);
    }
    let html = '<h3>已启用 (' + enabled.length + ')</h3><div class="tool-grid">';
    for (const t of enabled) {
      html += '<div class="tool-item"><div class="tool-name">' + escHtml(t.name) + '</div><div class="tool-desc">' + escHtml(t.description || '').slice(0, 80) + '</div>' +
        '<button class="btn btn-sm btn-secondary" onclick="toggleTool(\'' + t.name + '\')">禁用</button></div>';
    }
    html += '</div><h3>已禁用 (' + disabled.length + ')</h3><div class="tool-grid">';
    for (const t of disabled) {
      html += '<div class="tool-item disabled"><div class="tool-name">' + escHtml(t.name) + '</div><div class="tool-desc">' + escHtml(t.description || '').slice(0, 80) + '</div>' +
        '<button class="btn btn-sm btn-primary" onclick="toggleTool(\'' + t.name + '\')">启用</button></div>';
    }
    html += '</div>';
    document.getElementById('mcp-content').innerHTML = html;
  } catch(e) {
    document.getElementById('mcp-content').innerHTML = '<div class="error-card">❌ 加载失败</div>';
  }
}

async function toggleTool(name) {
  try {
    await API.post('/v1/tools/' + encodeURIComponent(name) + '/toggle', {});
    renderMCP();
  } catch(e) { toast('操作失败', 'err'); }
}

// ═══════════════════════════════════
// REPORTS TAB
// ═══════════════════════════════════
async function renderReports() {
  const el = document.getElementById('dash-content');
  el.innerHTML = '<h2>📄 报告</h2><div id="reports-content"><p class="text-muted">加载中...</p></div>';
  try {
    const data = await API.get('/v1/reports/list');
    const reports = data.reports || [];
    const container = document.getElementById('reports-content');
    if (reports.length === 0) {
      container.innerHTML = '<p class="text-muted">暂无报告</p>';
      return;
    }
    container.innerHTML = reports.map(r =>
      '<div class="report-item"><div class="report-title">📄 ' + escHtml(r.title || '报告') + '</div>' +
      '<div class="report-meta">' + (r.created_at || '') + '</div>' +
      '<a class="btn btn-sm" href="/v1/reports/' + r.id + '/download" target="_blank">下载</a></div>'
    ).join('');
  } catch(e) {
    document.getElementById('reports-content').innerHTML = '<div class="error-card">❌ 加载失败</div>';
  }
}

// ═══════════════════════════════════
// SCHEDULER TAB
// ═══════════════════════════════════
async function renderScheduler() {
  const el = document.getElementById('dash-content');
  el.innerHTML = '<h2>⏰ 定时任务</h2><div id="scheduler-content"><p class="text-muted">加载中...</p></div>';
  try {
    const data = await API.get('/v1/scheduler/tasks');
    const tasks = data.tasks || [];
    const container = document.getElementById('scheduler-content');
    if (tasks.length === 0) {
      container.innerHTML = '<p class="text-muted">暂无定时任务</p>';
      return;
    }
    container.innerHTML = tasks.map(t =>
      '<div class="task-item"><div class="task-name">' + escHtml(t.name || '任务') + '</div>' +
      '<div class="task-schedule">' + escHtml(t.schedule || '') + '</div></div>'
    ).join('');
  } catch(e) {
    document.getElementById('scheduler-content').innerHTML = '<div class="error-card">❌ 加载失败</div>';
  }
}

// ═══════════════════════════════════
// ADMIN USERS TAB
// ═══════════════════════════════════
async function renderAdminUsers() {
  const el = document.getElementById('dash-content');
  el.innerHTML = '<h2>👥 用户管理</h2><div id="admin-users-content"><p class="text-muted">加载中...</p></div>';
  try {
    const data = await API.get('/v1/admin/users');
    const users = data.users || [];
    let html = '<table class="table"><tr><th>用户名</th><th>角色</th><th>操作</th></tr>';
    for (const u of users) {
      html += '<tr><td>' + escHtml(u.username) + '</td><td>' + escHtml(u.role) + '</td>' +
        '<td><button class="btn btn-sm btn-danger" onclick="deleteUser(\'' + u.id + '\')">删除</button></td></tr>';
    }
    html += '</table>';
    document.getElementById('admin-users-content').innerHTML = html;
  } catch(e) {
    document.getElementById('admin-users-content').innerHTML = '<div class="error-card">❌ 加载失败</div>';
  }
}

async function deleteUser(userId) {
  if (!confirm('确定删除此用户？')) return;
  try {
    await API.post('/v1/admin/users/' + userId + '/delete', {});
    renderAdminUsers();
    toast('✅ 用户已删除', 'ok');
  } catch(e) { toast('删除失败', 'err'); }
}
