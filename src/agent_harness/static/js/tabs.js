// tabs.js — All tab rendering functions (setup wizard, dashboard, status, reports, knowledge, sessions, scheduler, settings, MCP, skills, admin)

// ═══════════════════════════════════
// LOGIN / ADMIN SETUP
// ═══════════════════════════════════

function renderSetupAdmin() {
  document.title = '灵枢 — 创建管理员';
  document.getElementById('app').innerHTML = `
    <div class="container" style="max-width:480px;margin-top:120px">
      <div class="card text-center" style="padding:48px">
        <div style="font-size:48px;margin-bottom:16px">⚡</div>
        <h2 style="margin-bottom:4px">首次启动设置</h2>
        <p class="text-muted text-sm" style="margin-bottom:24px">创建管理员账号以开始使用灵枢</p>
        <div class="form-group text-left">
          <label class="form-label">管理员用户名</label>
          <input class="form-input" id="setup-admin-username" placeholder="admin" value="admin">
        </div>
        <div class="form-group text-left">
          <label class="form-label">密码（至少 6 位）</label>
          <input class="form-input" id="setup-admin-password" type="password" placeholder="••••••">
        </div>
        <div class="form-group text-left">
          <label class="form-label">确认密码</label>
          <input class="form-input" id="setup-admin-password2" type="password" placeholder="••••••">
        </div>
        <div id="setup-admin-error" class="text-sm text-left" style="color:var(--danger);margin-bottom:12px;display:none"></div>
        <button class="btn btn-primary" onclick="doSetupAdmin()" id="setup-admin-btn" style="width:100%;font-size:16px;padding:14px">
          创建管理员 →
        </button>
      </div>
    </div>`;
}

async function doSetupAdmin() {
  const username = document.getElementById('setup-admin-username').value.trim();
  const password = document.getElementById('setup-admin-password').value;
  const password2 = document.getElementById('setup-admin-password2').value;
  const errEl = document.getElementById('setup-admin-error');

  if (!username) { errEl.textContent = '请输入用户名'; errEl.style.display = 'block'; return; }
  if (password.length < 6) { errEl.textContent = '密码至少 6 位'; errEl.style.display = 'block'; return; }
  if (password !== password2) { errEl.textContent = '两次密码不一致'; errEl.style.display = 'block'; return; }

  const btn = document.getElementById('setup-admin-btn');
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> 创建中...';
  errEl.style.display = 'none';

  try {
    const r = await _origFetch('/v1/auth/setup-admin', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username, password}),
    });
    const data = await r.json();
    if (r.status !== 200) throw new Error(data.error || '创建失败');

    // Auto login after creation
    await authLogin(username, password);
    toast('管理员已创建，欢迎使用灵枢！', 'ok');
    init();  // Re-init (will go to setup wizard now)
  } catch(e) {
    errEl.textContent = e.message;
    errEl.style.display = 'block';
    btn.disabled = false; btn.innerHTML = '创建管理员 →';
  }
}

function renderLogin() {
  document.title = '灵枢 — 登录';
  document.getElementById('splash').classList.add('hidden');
  document.getElementById('app').innerHTML = `
    <div class="container" style="max-width:420px;margin-top:100px">
      <div class="card text-center" style="padding:48px">
        <div style="font-size:48px;margin-bottom:8px">⚡</div>
        <h2 style="margin-bottom:4px">灵枢</h2>
        <div style="color:var(--accent);font-size:13px;margin-bottom:24px">LingShu Agent · AI 调研助手</div>
        <div id="login-error" class="text-sm text-left" style="color:var(--danger);margin-bottom:12px;display:none"></div>
        <div class="form-group text-left">
          <label class="form-label">用户名</label>
          <input class="form-input" id="login-username" placeholder="admin" autocomplete="username"
            onkeydown="if(event.key==='Enter') document.getElementById('login-password').focus()">
        </div>
        <div class="form-group text-left">
          <label class="form-label">密码</label>
          <input class="form-input" id="login-password" type="password" placeholder="••••••" autocomplete="current-password"
            onkeydown="if(event.key==='Enter') doLogin()">
        </div>
        <button class="btn btn-primary" onclick="doLogin()" id="login-btn" style="width:100%;font-size:16px;padding:14px">
          登录
        </button>
      </div>
    </div>`;
  // Focus username
  setTimeout(() => document.getElementById('login-username').focus(), 100);
}

async function doLogin() {
  const username = document.getElementById('login-username').value.trim();
  const password = document.getElementById('login-password').value;
  const errEl = document.getElementById('login-error');

  if (!username || !password) {
    errEl.textContent = '请输入用户名和密码';
    errEl.style.display = 'block'; return;
  }

  const btn = document.getElementById('login-btn');
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> 登录中...';
  errEl.style.display = 'none';

  try {
    await authLogin(username, password);
    // Redirect to dashboard
    init();
  } catch(e) {
    errEl.textContent = e.message;
    errEl.style.display = 'block';
    btn.disabled = false; btn.innerHTML = '登录';
    document.getElementById('login-password').value = '';
    document.getElementById('login-password').focus();
  }
}

// ═══════════════════════════════════
// SETUP WIZARD
// ═══════════════════════════════════
let setupStep = 1;
let setupData = { paths: [], llm: {}, env: {} };

function renderSetup() {
  document.title = '灵枢 — 初始化配置';
  document.getElementById('app').innerHTML = `
    <div class="container" style="max-width:720px">
      <div class="welcome" id="setup-welcome">
        <h1 style="font-size:42px; letter-spacing:2px">⚡ 灵枢</h1>
        <div style="color:var(--accent); font-size:14px; margin-bottom:4px">LingShu Agent</div>
        <p class="subtitle">AI 调研助手 — 64 秒出正式报告</p>
        <div style="display:flex; gap:12px; justify-content:center; flex-wrap:wrap">
          <button class="btn btn-primary" onclick="startSetup()" style="font-size:16px; padding:14px 36px">
            开始配置 →
          </button>
          <button class="btn btn-success" onclick="quickStart()" style="font-size:16px; padding:14px 36px">
            ⚡ 快速开始 — 一键自动配置
          </button>
        </div>
        <div class="feature-list" style="margin-top:48px">
          <div class="feature-item"><div class="icon">🔍</div><div class="name">深度调研</div><div class="desc">多角度搜索 × AI 分析</div></div>
          <div class="feature-item"><div class="icon">📄</div><div class="name">正式报告</div><div class="desc">A4 排版 · 一键生成</div></div>
          <div class="feature-item"><div class="icon">📚</div><div class="name">知识库</div><div class="desc">文件上传 RAG 检索</div></div>
          <div class="feature-item"><div class="icon">💬</div><div class="name">对话式交互</div><div class="desc">智能体全程驱动</div></div>
        </div>
      </div>
      <div id="setup-content" class="hidden"></div>
    </div>`;
}

function startSetup() {
  document.getElementById('setup-welcome').classList.add('hidden');
  document.getElementById('setup-content').classList.remove('hidden');
  setupStep = 1;
  renderStep1();
}

// ─── Welcome / Onboarding (shown after first setup) ───
function renderWelcome() {
  document.title = '灵枢 — 设置完成';
  document.getElementById('app').innerHTML = `
    <div class="container" style="max-width:640px;margin-top:60px">
      <div class="card text-center" style="padding:48px">
        <div style="font-size:56px;margin-bottom:12px">⚡</div>
        <h2 style="margin-bottom:8px">欢迎使用灵枢</h2>
        <p class="text-muted" style="margin-bottom:32px">你的 AI 调研助手已准备就绪</p>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;text-align:left;margin-bottom:32px">
          <div class="service-card" style="cursor:pointer" onclick="renderDashboard();switchTab('chat')">
            <div class="service-info">
              <div class="service-name">💬 开始对话</div>
              <div class="service-detail">输入关键词，灵枢自动搜索和分析</div>
            </div>
          </div>
          <div class="service-card" style="cursor:pointer" onclick="renderDashboard();switchTab('knowledge')">
            <div class="service-info">
              <div class="service-name">📚 上传知识库</div>
              <div class="service-detail">PDF/Word/TXT → RAG 检索</div>
            </div>
          </div>
          <div class="service-card" style="cursor:pointer" onclick="renderDashboard();switchTab('status')">
            <div class="service-info">
              <div class="service-name">📊 服务状态</div>
              <div class="service-detail">检查 LLM 和辅助服务运行情况</div>
            </div>
          </div>
          <div class="service-card" style="cursor:pointer" onclick="renderDashboard();switchTab('admin-users')">
            <div class="service-info">
              <div class="service-name">👥 管理用户</div>
              <div class="service-detail">添加用户、分配角色</div>
            </div>
          </div>
        </div>

        <button class="btn btn-primary" onclick="renderDashboard()" style="font-size:16px;padding:14px 36px">
          进入控制台 →
        </button>
      </div>
    </div>`;
}

async function quickStart() {
  const el = document.getElementById('setup-content');
  el.classList.remove('hidden');
  el.innerHTML = '<div class="card text-center" style="padding:40px"><span class="spinner"></span><div class="mt-16">自动配置中...<br><span class="text-sm text-muted">启动服务、配置 LLM、检测环境</span></div></div>';

  try {
    const result = await API.post('/v1/setup/auto-configure', {});
    const steps = (result.steps || []).map(s =>
      `<div class="service-card"><span class="dot ${s.success ? 'dot-ok' : 'dot-fail'}"></span><div class="service-info"><div class="service-name">${s.action}</div><div class="service-detail">${s.message || s.error || ''}</div></div></div>`
    ).join('');
    const ok = result.llm_configured;
    el.innerHTML = `
      <div class="card text-center" style="padding:40px">
        <div style="font-size:48px; margin-bottom:16px">${ok ? '🎉' : '⚠️'}</div>
        <h2 style="margin-bottom:8px">${ok ? '配置完成！' : '部分完成'}</h2>
        <p class="text-muted" style="margin-bottom:24px">${result.warning || '灵枢已就绪，可进行 AI 调研'}</p>
        <div style="text-align:left">${steps}</div>
        <div class="mt-16">
          <button class="btn btn-primary" onclick="renderWelcome()" style="font-size:16px; padding:14px 36px">
            进入灵枢 →
          </button>
          <button class="btn btn-secondary btn-sm" onclick="startSetup()" style="margin-left:8px">
            手动配置
          </button>
        </div>
      </div>`;
  } catch(e) {
    el.innerHTML = `<div class="card text-center" style="padding:40px">
      <div style="font-size:48px; margin-bottom:16px">❌</div>
      <h2>配置失败</h2>
      <p class="text-muted">${e.message}</p>
      <button class="btn btn-primary mt-16" onclick="startSetup()">手动配置</button>
    </div>`;
  }
}

async function fixService(name) {
  const btn = event.target; btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>';
  try {
    const result = await API.post('/v1/setup/fix', {action: 'start_' + name});
    if (result.success) { toast(name + ' 启动成功 ✓', 'ok'); renderStep3(); }
    else { toast(name + ' 启动失败: ' + (result.error || '未知错误'), 'err'); }
  } catch(e) { toast('操作失败: ' + e.message, 'err'); }
  btn.disabled = false; btn.innerHTML = '启动';
}

function renderSteps() {
  const steps = ['环境检测', 'LLM 配置', '服务检查', '完成'];
  return `<div class="steps">${
    steps.map((s, i) => {
      const n = i + 1;
      let cls = 'step';
      if (n === setupStep) cls += ' active';
      else if (n < setupStep) cls += ' done';
      return `<div class="${cls}"><span class="num">${n < setupStep ? '✓' : n}</span>${s}</div>`;
    }).join('')
  }</div>`;
}

async function renderStep1() {
  const el = document.getElementById('setup-content');
  el.innerHTML = renderSteps() + `
    <div class="card">
      <div class="card-title">🔍 环境路径检测</div>
      <p class="text-muted text-sm mb-16">检查关键路径是否存在中文字符等问题</p>
      <div id="path-list"><div class="text-center"><span class="spinner"></span> 扫描中...</div></div>
      <div class="flex gap-8 mt-16">
        <button class="btn btn-secondary btn-sm" onclick="renderStep1()">重新检测</button>
        <button class="btn btn-primary btn-sm hidden" id="step1-next" onclick="setupStep=2; renderStep2()">下一步 →</button>
      </div>
    </div>`;
  const data = await API.get('/v1/setup/check-paths');
  setupData.paths = data;
  const list = document.getElementById('path-list');
  list.innerHTML = data.map(p => {
    const ok = p.exists && !p.has_chinese;
    const warn = p.has_chinese ? '⚠️ 路径含中文，建议修改' : (p.exists ? '✅ 正常' : '⏸️ 未找到');
    const warnCls = p.has_chinese ? 'path-warn' : (p.exists ? 'path-ok' : 'text-muted');
    return `<div class="path-item">
      <span class="path-label">${p.label}</span>
      <span class="path-value">${p.path || '—'}</span>
      <span class="${warnCls}">${warn}</span>
    </div>`;
  }).join('');
  document.getElementById('step1-next').classList.remove('hidden');
}

async function renderStep2() {
  const el = document.getElementById('setup-content');
  el.innerHTML = renderSteps() + `
    <div class="card">
      <div class="card-title">🤖 LLM 配置</div>
      <p class="text-muted text-sm mb-16">选择推理后端，或使用自动检测</p>
      <div id="llm-backends"><div class="text-center"><span class="spinner"></span> 检测可用后端...</div></div>
      <div class="mt-16 hidden" id="llm-custom">
        <div class="form-group">
          <label class="form-label">API 地址</label>
          <input class="form-input" id="llm-url" placeholder="http://127.0.0.1:8081/v1/chat/completions">
        </div>
        <div class="form-group">
          <label class="form-label">API Key（可选）</label>
          <input class="form-input" id="llm-key" placeholder="sk-...">
        </div>
        <div class="form-group">
          <label class="form-label">模型名称</label>
          <input class="form-input" id="llm-model" placeholder="deepseek-v4">
        </div>
        <button class="btn btn-secondary btn-sm" onclick="testLLM()">测试连接</button>
        <div id="llm-test-result" class="mt-16"></div>
      </div>
      <div class="flex gap-8 mt-16">
        <button class="btn btn-secondary btn-sm" onclick="setupStep=1; renderStep1()">← 上一步</button>
        <button class="btn btn-success btn-sm" onclick="quickStartLLM()">⚡ 自动配置</button>
        <button class="btn btn-primary btn-sm" onclick="setupStep=3; renderStep3()">下一步 →</button>
      </div>
    </div>`;
  const backends = await API.get('/v1/setup/llm-backends');
  setupData.llm = backends;
  const container = document.getElementById('llm-backends');
  container.innerHTML = Object.entries(backends).map(([k, v]) => `
    <div class="service-card" style="cursor:pointer" onclick="selectLLM('${k}')">
      <span class="dot ${v.available ? 'dot-ok' : 'dot-fail'}"></span>
      <div class="service-info">
        <div class="service-name">${v.label}</div>
        <div class="service-detail">${v.available ? '可用' : '未运行'} · ${v.endpoint || ''}</div>
      </div>
      <span class="text-sm" style="color:var(--accent)" id="llm-sel-${k}">${k === 'model_proxy' ? '✓ 推荐' : ''}</span>
    </div>
  `).join('');
}

function selectLLM(key) {
  const v = setupData.llm[key];
  document.querySelectorAll('.service-card').forEach(el => el.style.borderColor = '');
  event.currentTarget.style.borderColor = 'var(--accent)';
  setupData.selectedLLM = key;
  setupData.llmEndpoint = v.endpoint;
  if (key === 'deepseek') {
    document.getElementById('llm-custom').classList.remove('hidden');
    document.getElementById('llm-url').value = v.endpoint;
  } else {
    document.getElementById('llm-custom').classList.add('hidden');
  }
}

async function quickStartLLM() {
  const btn = event.target; btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>';
  try {
    const result = await API.post('/v1/setup/auto-configure', {});
    if (result.llm_configured) { toast('LLM 自动配置成功 ✓', 'ok'); setupStep = 3; renderStep3(); }
    else { toast('自动配置失败: ' + (result.warning || '请手动选择'), 'err'); }
  } catch(e) { toast('自动配置出错: ' + e.message, 'err'); }
  btn.disabled = false; btn.innerHTML = '⚡ 自动配置';
}

async function testLLM() {
  const btn = event.target; btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> 测试中...';
  const result = await API.post('/v1/setup/test-llm', {
    endpoint: document.getElementById('llm-url').value || setupData.llmEndpoint,
    api_key: document.getElementById('llm-key').value || '',
    model: document.getElementById('llm-model').value || '',
  });
  const el = document.getElementById('llm-test-result');
  el.innerHTML = result.reachable
    ? `<span class="dot dot-ok"></span> 连接成功！`
    : `<span class="dot dot-fail"></span> 连接失败: ${result.error}`;
  btn.disabled = false; btn.textContent = '测试连接';
}

async function renderStep3() {
  const el = document.getElementById('setup-content');
  el.innerHTML = renderSteps() + `
    <div class="card">
      <div class="card-title">🛡️ 环境检查</div>
      <p class="text-muted text-sm mb-16">检测所有服务运行状态</p>
      <div id="env-results"><div class="text-center"><span class="spinner"></span> 检测中...</div></div>
      <div class="flex gap-8 mt-16">
        <button class="btn btn-secondary btn-sm" onclick="setupStep=2; renderStep2()">← 上一步</button>
        <button class="btn btn-primary btn-sm" onclick="setupStep=4; renderStep4()">下一步 →</button>
      </div>
    </div>`;
  const env = await API.get('/v1/setup/env-check');
  setupData.env = env;
  const html = [];
  html.push('<div class="card-title" style="font-size:13px;margin-top:8px">推理后端</div>');
  for (const [k, v] of Object.entries(env.llm_backends || {})) {
    const fixable = !v.available && ['model_proxy', 'ollama', 'llamacpp'].includes(k);
    html.push(`<div class="service-card">
      <span class="dot ${v.available ? 'dot-ok' : 'dot-fail'}"></span>
      <div class="service-info">
        <div class="service-name">${v.label}</div>
        <div class="service-detail">${v.available ? '运行中' : '离线'}</div>
      </div>
      ${fixable ? `<button class="btn btn-secondary btn-sm" onclick="fixService('${k}')">启动</button>` : ''}
    </div>`);
  }
  html.push('<div class="card-title" style="font-size:13px;margin-top:16px">辅助服务</div>');
  for (const [k, v] of Object.entries(env.services || {})) {
    const fixable = !v.available && ['searxng', 'comfyui'].includes(k);
    html.push(`<div class="service-card">
      <span class="dot ${v.available ? 'dot-ok' : 'dot-fail'}"></span>
      <div class="service-info">
        <div class="service-name">${v.label}</div>
        <div class="service-detail">${v.available ? '运行中' : '离线'} ${v.endpoint || ''}</div>
      </div>
      ${fixable ? `<button class="btn btn-secondary btn-sm" onclick="fixService('${k}')">启动</button>` : ''}
    </div>`);
  }
  html.push('<div class="card-title" style="font-size:13px;margin-top:16px">路径</div>');
  for (const p of env.paths || []) {
    const ok = p.exists && !p.has_chinese;
    html.push(`<div class="service-card"><span class="dot ${ok ? 'dot-ok' : 'dot-fail'}"></span><div class="service-info"><div class="service-name">${p.label}</div><div class="service-detail">${p.path || '未找到'}</div></div></div>`);
  }
  document.getElementById('env-results').innerHTML = html.join('');
}

async function renderStep4() {
  const llmKey = setupData.selectedLLM || 'model_proxy';
  const llmInfo = setupData.llm[llmKey] || {};
  await API.post('/v1/setup/config', {
    setup_complete: true,
    llm: { backend: llmKey, api_url: llmInfo.endpoint || '', api_key: '', model: 'deepseek-v4' },
  });
  const el = document.getElementById('setup-content');
  el.innerHTML = renderSteps() + `
    <div class="card text-center" style="padding:60px">
      <div style="font-size:48px; margin-bottom:16px">🎉</div>
      <h2 style="margin-bottom:8px">配置完成！</h2>
      <p class="text-muted" style="margin-bottom:24px">灵枢已就绪，可进行 AI 调研</p>
      <button class="btn btn-primary" onclick="renderDashboard()" style="font-size:16px; padding:14px 36px">
        进入控制台 →
      </button>
    </div>`;
}

// ═══════════════════════════════════
// DASHBOARD
// ═══════════════════════════════════
const DASH_TABS = [
  { id: 'chat',    icon: '💬',  label: '对话' },
  { id: 'reports', icon: '📄',  label: '报告' },
  { id: 'status',  icon: '📊',  label: '服务' },
  { id: 'knowledge', icon: '📚', label: '知识库' },
  { id: 'mcp',     icon: '🔌',  label: '工具' },
  { id: 'sessions', icon: '🗂️', label: '会话管理' },
  { id: 'admin-users', icon: '👥', label: '用户管理', adminOnly: true },
  { id: 'scheduler', icon: '⏰', label: '定时任务', adminOnly: true },
  { id: 'settings', icon: '⚙️', label: '设置', adminOnly: true },
  { id: 'skills',  icon: '🧠',  label: '技能', adminOnly: true },
];

function _visibleTabs() {
  const isAdmin = window.__auth.user && window.__auth.user.role === 'admin';
  return DASH_TABS.filter(t => !t.adminOnly || isAdmin);
}

let dashTab = 'chat';
let dashData = {};

function renderDashboard() {
  document.title = '灵枢 — AI 调研助手';
  dashTab = 'chat';

  const app = document.getElementById('app');
  const isAdmin = window.__auth.user && window.__auth.user.role === 'admin';
  const userName = (window.__auth.user && window.__auth.user.display_name) || '用户';
  const userBadge = isAdmin ? 'admin' : 'user';

  app.innerHTML = `
    <!-- Header -->
    <div class="app-header">
      <div class="app-header-left">
        <span class="app-header-logo">⚡ 灵枢</span>
        <span class="app-header-sub">AI 调研助手</span>
        <span class="header-user-badge" style="font-size:11px;padding:2px 8px;border-radius:10px;background:${isAdmin?'var(--accent-light)':'#f0f0f0'};color:${isAdmin?'var(--accent)':'#666'};margin-left:8px">${userName} · ${userBadge}</span>
      </div>
      <div style="display:flex;gap:4px;align-items:center">
        <div style="position:relative" id="settings-menu-container">
          <button class="btn btn-secondary btn-sm" onclick="toggleSettingsMenu()">☰ 菜单 ▾</button>
          <div id="settings-dropdown" class="hidden" style="position:absolute;top:100%;right:0;margin-top:4px;background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);min-width:180px;z-index:100;box-shadow:0 4px 20px rgba(0,0,0,0.15)">
            <div class="dropdown-item text-sm" style="cursor:default;color:var(--text-secondary);border-bottom:1px solid var(--border);margin-bottom:4px">
              ${userName} · ${isAdmin ? '管理员' : '普通用户'}
            </div>
            ${isAdmin ? '<div class="dropdown-item" onclick="switchTab(\'settings\'); closeSettingsMenu()">⚙️ 设置</div>' : ''}
            ${isAdmin ? '<div class="dropdown-item" onclick="switchTab(\'admin-users\'); closeSettingsMenu()">👥 用户管理</div>' : ''}
            <div class="dropdown-item" onclick="switchTab('reports'); closeSettingsMenu()">📄 报告列表</div>
            <div class="dropdown-item" onclick="switchTab('mcp'); closeSettingsMenu()">🔌 工具列表</div>
            <div class="dropdown-item" onclick="switchTab('sessions'); closeSettingsMenu()">🗂️ 会话管理</div>
            ${isAdmin ? '<div class="dropdown-item" onclick="switchTab(\'skills\'); closeSettingsMenu()">🧠 技能</div>' : ''}
            <div class="dropdown-divider"></div>
            <div class="dropdown-item" onclick="checkUpdate(); closeSettingsMenu()">📦 检查更新</div>
            <div class="dropdown-item" onclick="closeSettingsMenu(); toggleDarkMode()">🌙 暗色模式</div>
            ${isAdmin ? '<div class="dropdown-item" onclick="renderSetup(); closeSettingsMenu()">🔄 重新配置</div>' : ''}
            <div class="dropdown-divider"></div>
            <div class="dropdown-item" onclick="doLogout(); closeSettingsMenu()" style="color:var(--danger)">🚪 退出登录</div>
          </div>
        </div>
      </div>
    </div>

    <!-- Body: Sidebar + Main -->
    <div class="app-body">
      <!-- Sidebar -->
      <div class="app-sidebar" id="sidebar">
        <div class="sidebar-search">
          <input id="sidebar-search-input" type="text" placeholder="搜索会话..."
            oninput="onSidebarFilterChange()" onfocus="showSearchSuggestions()"
            onblur="setTimeout(hideSearchSuggestions,200)" autocomplete="off">
          <div id="sidebar-search-suggestions" class="search-suggestions hidden"></div>
        </div>
        <div class="sidebar-actions">
          <button class="sidebar-new-btn" onclick="switchToNewSession()">
            <span>✕</span> 新会话
          </button>
          <button class="btn btn-sm" style="padding:4px 8px;font-size:11px" onclick="toggleMessageSearch()" title="搜索消息内容">🔍</button>
        </div>
        <div class="sidebar-list" id="sidebar-list"></div>
        <div class="sidebar-footer" id="sidebar-footer">
          <span class="sidebar-version">v0.42.0</span>
        </div>
        <div class="status-bar" id="status-bar">
          <span class="status-dot" id="health-dot"></span>
          <span id="health-text">检查中...</span>
        </div>
      </div>

      <!-- Main content -->
      <div class="app-main">
        <div class="main-tabs" id="main-tabs"></div>
        <div class="main-content" id="dash-content"></div>
      </div>
    </div>
  `;

  // Build tab bar
  renderTabBar();
  // Open chat tab
  switchTab('chat');
  // Load session list
  loadSidebarSessions();
  // Update health bar
  updateHealthBar();
}

function renderTabBar() {
  const bar = document.getElementById('main-tabs');
  bar.innerHTML = _visibleTabs().map(t =>
    `<a class="main-tab" data-tab="${t.id}" href="#" onclick="return switchTab('${t.id}')">${t.icon} ${t.label}</a>`
  ).join('');
}

// ═══════════════════════════════════
// STATUS TAB
// ═══════════════════════════════════
async function renderDashStatus() {
  const el = document.getElementById('dash-content');
  el.innerHTML = '<div class="skeleton-card"><div class="skeleton skeleton-title"></div><div class="skeleton skeleton-line"></div><div class="skeleton skeleton-line"></div><div class="skeleton skeleton-line"></div></div>';

  const env = await API.get('/v1/setup/env-check').catch(() => null);
  const health = await API.get('/health').catch(() => null);
  if (!env) { showErrorEl(el, '无法获取服务状态', '请检查 API 是否正常运行', renderDashStatus); return; }

  let html = '<div class="flex-between mb-16"><div><span class="card-title" style="font-size:18px;margin:0">📊 服务状态</span></div>' +
    '<button class="btn btn-secondary btn-sm" onclick="renderDashStatus()">🔄 刷新</button></div>';

  html += '<div class="card"><div class="card-title">🤖 推理后端</div><div class="service-grid">';
  for (const [k, v] of Object.entries(env.llm_backends || {})) {
    html += '<div class="service-card"><span class="dot ' + (v.available ? 'dot-ok' : 'dot-fail') + '"></span>' +
      '<div class="service-info"><div class="service-name">' + escHtml(v.label) + '</div>' +
      '<div class="service-detail">' + (v.available ? '运行中' : '离线') + (v.port ? ' · port ' + v.port : '') + '</div></div></div>';
  }
  html += '</div></div>';

  html += '<div class="card"><div class="card-title">🔧 辅助服务</div><div class="service-grid">';
  for (const [k, v] of Object.entries(env.services || {})) {
    html += '<div class="service-card"><span class="dot ' + (v.available ? 'dot-ok' : 'dot-fail') + '"></span>' +
      '<div class="service-info"><div class="service-name">' + escHtml(v.label) + '</div>' +
      '<div class="service-detail">' + (v.available ? '运行中' : '离线') + (v.port ? ' · port ' + v.port : '') + '</div></div></div>';
  }
  html += '</div></div>';

  html += '<div class="card"><div class="card-title">📊 概览</div><div class="service-grid">';
  const sessions = health ? health.active_sessions : '?';
  html += '<div class="service-card"><div class="service-info"><div class="service-name">活跃会话</div><div class="service-detail">' + sessions + '</div></div></div>';
  html += '<div class="service-card"><div class="service-info"><div class="service-name">API 版本</div><div class="service-detail">v' + (window.__VERSION__ || '0.41.0') + '</div></div></div>';
  // Auth status
  const isAdmin = window.__auth.user && window.__auth.user.role === 'admin';
  html += '<div class="service-card"><div class="service-info"><div class="service-name">认证方式</div><div class="service-detail">JWT · 当前: ' + (window.__auth.user ? escHtml(window.__auth.user.username) + ' (' + (isAdmin ? '管理员' : '用户') + ')' : 'API Key') + '</div></div></div>';

  // Try to fetch additional status data (silent on failure)
  Promise.all([
    API.get('/v1/scheduler/tasks').catch(() => null),
    API.get('/v1/plugins/loaded').catch(() => null),
    API.get('/v1/diag/search').catch(() => null),
  ]).then(([sched, plugs, sdiag]) => {
    const taskCount = sched && sched.tasks ? sched.tasks.length : 0;
    const pluginCount = plugs && plugs.plugins ? plugs.plugins.length : 0;
    const pluginOk = plugs && plugs.plugins ? plugs.plugins.filter(p => p.success).length : 0;
    // Append to existing overview
    const grid = document.querySelector('.service-grid');
    if (grid) {
      let extra = '<div class="service-card"><div class="service-info"><div class="service-name">定时任务</div><div class="service-detail">' + taskCount + ' 个</div></div></div>' +
        '<div class="service-card"><div class="service-info"><div class="service-name">插件</div><div class="service-detail">' + pluginCount + ' 个（' + pluginOk + ' 正常）</div></div></div>';
      // Search diagnostics summary
      if (sdiag && sdiag.diag && sdiag.diag.length > 0) {
        const last = sdiag.diag[0];
        const failed = sdiag.diag.filter(d => d.status === 'failed' || d.status === 'error').length;
        extra += '<div class="service-card"><div class="service-info"><div class="service-name">搜索诊断</div><div class="service-detail">最近: ' + last.engine + ' → ' + last.status + ' · ' + failed + ' 次失败</div></div></div>';
      }
      grid.insertAdjacentHTML('beforeend', extra);
    }
  }).catch(() => {});

  html += '</div></div>';

  try {
    const tasksData = await API.get('/v1/tasks');
    if (tasksData.tasks && tasksData.tasks.length > 0) {
      html += '<div class="card"><div class="card-title">⚡ 运行中的任务</div>';
      html += tasksData.tasks.map(t => `
        <div class="service-card">
          <span class="dot dot-ok" style="animation:pulse 1s infinite"></span>
          <div class="service-info">
            <div class="service-name">${t.session_id.slice(0,12)}...</div>
            <div class="service-detail">运行中</div>
          </div>
          <button class="btn btn-danger btn-sm" onclick="cancelTask('${t.session_id}')">取消</button>
        </div>
      `).join('');
      html += '</div>';
    }
  } catch(e) {}
  el.innerHTML = html;
}

// ═══════════════════════════════════
// REPORTS TAB
// ═══════════════════════════════════
async function renderReports() {
  const el = document.getElementById('dash-content');
  el.innerHTML = '<div class="card"><div class="flex-between"><div class="card-title">📄 报告列表</div><button class="btn btn-secondary btn-sm" onclick="renderReports()">🔄 刷新</button></div>' +
    '<div style="display:flex;gap:8px;margin-bottom:12px">' +
    '<input class="form-input" id="report-search-input" placeholder="搜索报告标题或标签..." onkeydown="if(event.key===\'Enter\') searchReports()">' +
    '<button class="btn btn-primary btn-sm" onclick="searchReports()">搜索</button>' +
    '</div><div id="report-list"><div class="text-center"><span class="spinner"></span> 加载中...</div></div></div>';
  await loadReports();
}

async function loadReports(q) {
  const list = document.getElementById('report-list');
  if (!list) return;
  try {
    const url = q ? '/v1/reports/search?q=' + encodeURIComponent(q) : '/v1/reports';
    const data = await API.get(url);
    if (!data.reports || !data.reports.length) {
      list.innerHTML = '<div class="text-center text-muted" style="padding:20px">暂无报告。在「💬 对话」中聊天后保存即可。</div>';
      return;
    }
    list.innerHTML = data.reports.map(r => {
      const date = new Date(r.created_at*1000).toLocaleString('zh-CN');
      const isDraft = (r.tags||[]).includes('draft');
      const draftBadge = isDraft ? '<span style="display:inline-block;padding:2px 8px;border-radius:4px;background:#fef3c7;color:#92400e;font-size:11px;margin-right:4px">📝 草稿</span>' : '';
      const tags = (r.tags||[]).map(t => '<span style="display:inline-block;padding:2px 8px;border-radius:4px;background:var(--accent-light);color:var(--accent);font-size:11px;margin-right:4px">'+escHtml(t)+'</span>').join('');
      return '<div class="service-card"><div class="service-info"><div class="service-name">'+escHtml(r.title)+draftBadge+'</div><div class="service-detail">'+date+' · '+r.chars+' 字 '+tags+'</div></div><button class="btn btn-secondary btn-sm" onclick="viewReport(\''+r.id+'\')" style="margin-right:4px">查看</button><button class="btn btn-success btn-sm" onclick="downloadReport(\''+r.id+'\',\''+escHtml(r.title).replace(/'/g,"\\'")+'\')" style="margin-right:4px">⬇</button><button class="btn btn-danger btn-sm" onclick="deleteReport(\''+r.id+'\')">删除</button></div>';
    }).join('');
  } catch(e) {}
}

async function viewReport(reportId) { window.open('/v1/reports/' + reportId, '_blank'); }
async function searchReports() {
  const q = document.getElementById('report-search-input').value.trim();
  document.getElementById('report-list').innerHTML = '<div class="text-center"><span class="spinner"></span> 搜索中...</div>';
  await loadReports(q || null);
}
async function downloadReport(reportId, title) {
  const a = document.createElement('a');
  a.href = '/v1/reports/' + reportId;
  a.download = (title || 'report').replace(/[^a-zA-Z0-9\u4e00-\u9fa5_-]/g, '_') + '.html';
  document.body.appendChild(a); a.click(); a.remove();
  toast('下载中...', 'info');
}
async function deleteReport(reportId) {
  if (!confirm('删除此报告？')) return;
  try { await fetch('/v1/reports/'+reportId,{method:'DELETE'}); toast('已删除','ok'); renderReports(); }
  catch(e) { toast('删除失败','err'); }
}

// ═══════════════════════════════════
// KNOWLEDGE TAB
// ═══════════════════════════════════
async function renderDashKnowledge() {
  const el = document.getElementById('dash-content');
  el.innerHTML = '<div class="card"><div class="card-title">📚 知识库</div>' +
    // Upload zone
    '<div class="upload-zone" id="upload-zone" onclick="document.getElementById(\'file-input\').click()">' +
    '<div class="upload-zone-icon">📄</div><div>点击上传文件（PDF/TXT/DOCX/MD）</div>' +
    '<div class="text-sm text-muted">文件将自动分块索引到向量存储</div></div>' +
    '<input type="file" id="file-input" hidden accept=".pdf,.txt,.docx,.md" onchange="uploadFile(this)">' +
    '<div id="upload-progress" class="hidden mt-16 text-center"><span class="spinner"></span> 索引中...</div>' +
    // Search section
    '<div class="mt-16" style="display:flex;gap:8px">' +
    '<input class="form-input" id="kb-query" placeholder="搜索知识库内容..." onkeydown="if(event.key===\'Enter\') kbSearch()">' +
    '<button class="btn btn-primary btn-sm" onclick="kbSearch()">搜索</button>' +
    '</div><div id="kb-results" class="mt-16"></div>' +
    // Collections
    '<div id="collections-list" class="mt-16"><div class="text-center text-muted"><span class="spinner"></span> 加载中...</div></div></div>';
  renderCollections();
}

async function kbSearch() {
  const q = document.getElementById('kb-query').value.trim();
  const resultsEl = document.getElementById('kb-results');
  if (!q) { resultsEl.innerHTML = ''; return; }
  resultsEl.innerHTML = '<div class="text-center text-sm"><span class="spinner"></span> 搜索中...</div>';
  try {
    const data = await API.get('/v1/knowledge/collections');
    const cols = data.collections || [];
    let allResults = [];
    for (const col of cols) {
      const r = await fetch('/v1/knowledge/query?q=' + encodeURIComponent(q) + '&collection=' + encodeURIComponent(col.name) + '&top_k=3').catch(() => null);
      if (r) {
        const d = await r.json();
        if (d.results) allResults = allResults.concat(d.results.map(r => ({...r, collection: col.name})));
      }
    }
    if (allResults.length === 0) {
      resultsEl.innerHTML = '<div class="text-center text-muted text-sm" style="padding:16px">未找到匹配内容</div>';
      return;
    }
    resultsEl.innerHTML = '<div class="card-title" style="font-size:13px;margin-bottom:8px">搜索结果 (' + allResults.length + ')</div>' +
      allResults.map(r => {
        const method = r.method === 'vector' ? '🔵 向量' : '🟡 关键词';
        const source = escHtml(r.source || '');
        const text = escHtml((r.text || '').slice(0, 300));
        return '<div class="service-card"><div class="service-info">' +
          '<div class="service-name">' + method + ' · 评分 ' + (r.score || 0) + ' · ' + source + '</div>' +
          '<div class="service-detail">' + text + '</div>' +
          (r.collection ? '<div class="text-sm text-muted">集合: ' + escHtml(r.collection) + '</div>' : '') +
          '</div></div>';
      }).join('');
  } catch(e) {
    resultsEl.innerHTML = '<div class="text-center text-muted text-sm" style="padding:16px">搜索失败: ' + e.message + '</div>';
  }
}

async function renderCollections() {
  try {
    const data = await API.get('/v1/knowledge/collections');
    const el = document.getElementById('collections-list');
    if (!data.collections || data.collections.length === 0) {
      el.innerHTML = '<div class="text-center text-muted" style="padding:20px">暂无知识库，上传文件开始</div>';
      return;
    }
    el.innerHTML = '<h4 style="font-size:13px;color:var(--text-secondary);margin-bottom:8px">已有知识库</h4>' +
    data.collections.map(c => {
      const embStatus = c.embedding_available ? '<span class="dot dot-ok" title="向量搜索可用"></span>' : '<span class="dot dot-fail" title="仅关键词搜索"></span>';
      return `<div class="service-card">
        <div class="service-info">
          <div class="service-name">${escHtml(c.name)} ${embStatus}</div>
          <div class="service-detail">${c.chunks} 个片段 · ${c.doc_count} 个文件 · ${(c.sources||[]).join(', ')}</div>
        </div>
        <button class="btn btn-danger btn-sm" onclick="deleteCollection('${c.name}')">删除</button>
      </div>`;
    }).join('');
  } catch(e) {}
}

async function uploadFile(input) {
  const file = input.files[0];
  if (!file) return;
  document.getElementById('upload-progress').classList.remove('hidden');
  const fd = new FormData();
  fd.append('file', file);
  fd.append('collection', 'default');
  try {
    const data = await API.upload('/v1/knowledge/upload', fd);
    const chunks = data.chunks_indexed || 0;
    const emb = data.embedding_status || 'unknown';
    const warn = data.warning || '';
    let msg = '✅ 已索引 ' + chunks + ' 个片段';
    if (emb === 'online') msg += '（向量搜索）';
    else if (emb === 'offline') msg += '（关键词降级）';
    toast(msg, emb === 'offline' ? 'warn' : 'ok');
    if (warn) setTimeout(() => toast(warn, 'info'), 1500);
    renderCollections();
  } catch(e) { toast('上传失败: ' + e, 'err'); }
  document.getElementById('upload-progress').classList.add('hidden');
  input.value = '';
}

async function deleteCollection(name) {
  if (!confirm(`确定删除知识库「${name}」？`)) return;
  try { await fetch('/v1/knowledge/collections/' + name, { method: 'DELETE' }); toast('已删除', 'ok'); renderCollections(); }
  catch(e) { toast('删除失败', 'err'); }
}

// ═══════════════════════════════════
// SESSIONS TAB
// ═══════════════════════════════════
async function renderDashSessions() {
  const el = document.getElementById('dash-content');
  el.innerHTML = `<div class="card">
    <div class="flex-between"><div class="card-title">🗂️ 会话历史</div><button class="btn btn-secondary btn-sm" onclick="renderDashSessions()">🔄 刷新</button></div>
    <div id="session-list"><div class="text-center"><span class="spinner"></span> 加载中...</div></div>
  </div>`;
  try {
    const data = await API.get('/v1/sessions');
    const list = document.getElementById('session-list');
    if (!data.sessions || data.sessions.length === 0) {
      list.innerHTML = '<div class="text-center text-muted" style="padding:20px">暂无会话记录</div>';
      return;
    }
    list.innerHTML = '<table><tr><th>会话 ID</th><th>轮次</th><th>最后活跃</th><th>预览</th><th></th></tr>' +
    data.sessions.map(s => `<tr>
      <td><code>${escHtml(s.id)}</code></td>
      <td>${s.exchanges}</td>
      <td>${s.last_active < 60 ? s.last_active + '秒前' : Math.floor(s.last_active/60) + '分钟前'}</td>
      <td class="text-muted text-sm">${escHtml((s.preview || '').slice(0, 40))}</td>
      <td><button class="btn btn-danger btn-sm" onclick="deleteSessionFromList('${s.id}')">删除</button></td>
    </tr>`).join('') + '</table>';
  } catch(e) {}
}

async function deleteSessionFromList(sessionId) {
  if (!confirm('删除此会话？')) return;
  try { await fetch('/v1/sessions/' + sessionId, { method: 'DELETE' }); toast('已删除', 'ok'); renderDashSessions(); loadSidebarSessions(); }
  catch(e) { toast('删除失败', 'err'); }
}

// ═══════════════════════════════════
// SCHEDULER TAB
// ═══════════════════════════════════
async function renderScheduler() {
  const el = document.getElementById('dash-content');
  el.innerHTML = '<div class="skeleton-card"><div class="skeleton skeleton-title"></div><div class="skeleton skeleton-line"></div><div class="skeleton skeleton-line"></div></div>';
  const data = await API.get('/v1/scheduler/tasks').catch(() => null);

  let html = '<div class="flex-between mb-16"><div class="card-title" style="margin:0">⏰ 定时任务</div><button class="btn btn-secondary btn-sm" onclick="renderScheduler()">🔄 刷新</button></div>';

  // Task list
  html += '<div class="card"><div class="card-title" style="font-size:14px">已创建的任务</div><div id="scheduler-list">';
  const tasks = data && data.tasks;
  if (!tasks || tasks.length === 0) {
    html += '<div class="text-center text-muted text-sm" style="padding:20px">暂无定时任务</div>';
  } else {
    html += '<table><tr><th>ID</th><th>调度</th><th>Prompt</th><th>状态</th><th>运行次数</th><th></th></tr>' +
      tasks.map(t => {
        const enabled = t.enabled !== false;
        const status = enabled
          ? '<span class="dot dot-ok"></span> 启用'
          : '<span class="dot dot-fail"></span> 禁用';
        const toggleBtn = enabled
          ? '<button class="btn btn-warning btn-sm" onclick="schedulerToggle(\'' + t.id + '\', false)">禁用</button>'
          : '<button class="btn btn-success btn-sm" onclick="schedulerToggle(\'' + t.id + '\', true)">启用</button>';
        return '<tr><td><code>' + escHtml(t.id) + '</code></td>' +
          '<td class="text-sm">' + escHtml(t.schedule) + '</td>' +
          '<td class="text-sm text-muted">' + escHtml((t.prompt || '').slice(0, 40)) + '</td>' +
          '<td>' + status + '</td>' +
          '<td class="text-sm">' + (t.run_count || 0) + '</td>' +
          '<td style="text-align:right">' + toggleBtn + ' <button class="btn btn-danger btn-sm" onclick="schedulerDelete(\'' + t.id + '\')">删除</button></td></tr>';
      }).join('') + '</table>';
  }
  html += '</div></div>';

  // Create form
  html += '<div class="card"><div class="card-title" style="font-size:14px">新建定时任务</div>' +
    '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:end">' +
    '<div class="form-group" style="flex:1;min-width:100px"><label class="form-label">任务 ID</label><input class="form-input" id="sched-id" placeholder="daily_report"></div>' +
    '<div class="form-group" style="flex:1;min-width:120px"><label class="form-label">调度表达式</label><input class="form-input" id="sched-schedule" placeholder="0 9 * * * 或 every 30m"></div>' +
    '<div class="form-group" style="flex:2;min-width:200px"><label class="form-label">Prompt</label><input class="form-input" id="sched-prompt" placeholder="生成昨日行业简报"></div>' +
    '<button class="btn btn-primary btn-sm" onclick="schedulerCreate()" style="margin-bottom:4px">创建</button></div>' +
    '<div id="sched-error" class="text-sm" style="color:var(--danger);margin-top:8px;display:none"></div>' +
    '<div class="text-sm text-muted mt-8">调度表达式示例: <code>0 9 * * *</code> (每天9点), <code>*/15 * * * *</code> (每15分), <code>every 30m</code></div>' +
    '</div>';

  el.innerHTML = html;
}

async function schedulerToggle(taskId, enable) {
  const r = await _origFetch('/v1/scheduler/tasks/' + taskId, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({enabled: enable}),
  });
  const d = await r.json();
  if (r.status !== 200) { toast('操作失败: ' + (d.error || ''), 'err'); return; }
  toast(enable ? '✅ 任务已启动' : '⏹️ 任务已暂停', enable ? 'ok' : 'info');
  renderScheduler();
}

async function schedulerDelete(taskId) {
  if (!confirm('删除任务「' + taskId + '」？')) return;
  const r = await _origFetch('/v1/scheduler/tasks/' + taskId, {method: 'DELETE'});
  if (r.status !== 200) { toast('删除失败', 'err'); return; }
  toast('已删除', 'ok');
  renderScheduler();
}

async function schedulerCreate() {
  const id = document.getElementById('sched-id').value.trim();
  const schedule = document.getElementById('sched-schedule').value.trim();
  const prompt = document.getElementById('sched-prompt').value.trim();
  const errEl = document.getElementById('sched-error');
  if (!id || !schedule) { errEl.textContent = '请填写任务 ID 和调度表达式'; errEl.style.display = 'block'; return; }
  errEl.style.display = 'none';
  const r = await _origFetch('/v1/scheduler/tasks', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id, schedule, prompt}),
  });
  const d = await r.json();
  if (r.status !== 200) { errEl.textContent = d.error || '创建失败'; errEl.style.display = 'block'; return; }
  toast('✅ 任务已创建', 'ok');
  document.getElementById('sched-id').value = '';
  document.getElementById('sched-schedule').value = '';
  document.getElementById('sched-prompt').value = '';
  renderScheduler();
}

// ═══════════════════════════════════
// SETTINGS TAB
// ═══════════════════════════════════
async function renderDashSettings() {
  const el = document.getElementById('dash-content');
  const config = await API.get('/v1/setup/config').catch(() => null);
  const health = await API.get('/health').catch(() => null);

  let html = '<div class="card"><div class="card-title">⚙️ 设置</div>';

  // LLM config section
  html += '<div class="card-title" style="font-size:14px;margin-top:16px">LLM 配置</div>';
  const llm = config && config.llm;
  html += '<div class="form-group"><label class="form-label">LLM 后端</label>' +
    '<input class="form-input" id="set-llm-backend" value="' + escHtml(llm?.backend || 'auto') + '">' +
    '<div class="form-hint">当前: ' + escHtml(llm?.backend || '未配置') + '</div></div>';
  html += '<div class="form-group"><label class="form-label">API 地址</label>' +
    '<input class="form-input" id="set-llm-url" value="' + escHtml(llm?.api_url || '') + '"></div>';
  html += '<div class="form-group"><label class="form-label">模型</label>' +
    '<input class="form-input" id="set-llm-model" value="' + escHtml(llm?.model || '') + '"></div>';
  html += '<button class="btn btn-primary btn-sm" onclick="saveSettings()">保存设置</button>';

  // About section
  html += '<div class="card-title" style="font-size:14px;margin-top:24px">📦 关于灵枢</div>' +
    '<table><tr><td style="width:100px;padding:6px 0">版本</td><td><code>v' + (window.__VERSION__ || '0.41.0') + '</code></td></tr>' +
    '<tr><td style="padding:6px 0">API 版本</td><td>' + (health ? escHtml(health.version || '1.0.0') : '—') + '</td></tr>' +
    '<tr><td style="padding:6px 0">活跃会话</td><td>' + (health ? health.active_sessions + ' 个' : '—') + '</td></tr>' +
    '<tr><td style="padding:6px 0">数据目录</td><td><code>~/.agent-harness/</code></td></tr>' +
    '<tr><td style="padding:6px 0">GitHub</td><td><a href="https://github.com/kisaragiy/lingShu" target="_blank" rel="noopener">kisaragiy/lingShu</a></td></tr>' +
    '<tr><td style="padding:6px 0">CS Demo</td><td><a href="/cs-demo" target="_blank" rel="noopener">🎧 智能客服 Demo</a></td></tr></table>' +
    '<div class="btn-group mt-16" style="display:flex;gap:8px;flex-wrap:wrap">' +
    '<button class="btn btn-secondary btn-sm" onclick="renderSetup()">🔄 重新配置向导</button>' +
    '<button class="btn btn-secondary btn-sm" onclick="checkUpdate()">📦 检查更新</button>' +
    '</div>' +

    // Data export section
    '<div class="card-title" style="font-size:14px;margin-top:24px">💾 数据导出</div>' +
    '<p class="text-sm text-muted" style="margin-bottom:12px">导出会话、报告或完整备份，用于数据迁移或存档</p>' +
    '<div class="btn-group" style="display:flex;gap:8px;flex-wrap:wrap">' +
    '<button class="btn btn-secondary btn-sm" onclick="downloadExport(\'/v1/export/sessions\',\'sessions.json\')">💬 导出会话</button>' +
    '<button class="btn btn-secondary btn-sm" onclick="downloadExport(\'/v1/export/reports\',\'reports.json\')">📄 导出报告</button>' +
    '<button class="btn btn-primary btn-sm" onclick="downloadExport(\'/v1/export/backup\',\'backup.zip\')">⬇ 完整备份</button>' +
    '</div>';

  html += '</div>';
  el.innerHTML = html;
}

async function saveSettings() {
  const config = {
    llm: {
      backend: document.getElementById('set-llm-backend').value,
      api_url: document.getElementById('set-llm-url').value,
      model: document.getElementById('set-llm-model').value,
    },
    setup_complete: true,
  };
  await API.post('/v1/setup/config', config);
  toast('设置已保存 ✓', 'ok');
}

// ═══════════════════════════════════
// MCP TOOLS TAB
// ═══════════════════════════════════
async function renderMCP() {
  const el = document.getElementById('dash-content');
  el.innerHTML = '<div class="skeleton-card"><div class="skeleton skeleton-title"></div><div class="skeleton skeleton-line"></div><div class="skeleton skeleton-line"></div></div>';
  const r = await fetch('/v1/tools').catch(() => null);
  if (!r) { showErrorEl(el, '无法加载工具列表', 'API 连接失败', renderMCP); return; }
  const data = await r.json();
  const tools = data.tools || {};
  const entries = Object.entries(tools);
  const enabled = entries.filter(([,v]) => v.enabled !== false);
  const disabled = entries.filter(([,v]) => v.enabled === false);

  let html = '<div class="flex-between mb-16"><div class="card-title" style="margin:0">🔌 工具列表 (' + entries.length + ')</div><button class="btn btn-secondary btn-sm" onclick="renderMCP()">🔄 刷新</button></div>';

  // Enabled tools
  html += '<div class="card"><div class="card-title" style="font-size:14px">已启用 (' + enabled.length + ')</div><div class="service-grid">';
  for (const [k, v] of enabled) {
    html += '<div class="service-card"><div class="service-info"><div class="service-name">' + escHtml(k) + ' <span class="privilege-badge" style="font-size:10px;padding:1px 6px;border-radius:4px;background:#eef2ff;color:#2563eb">' + escHtml(v.privilege || '') + '</span></div><div class="service-detail">' + escHtml((v.description || '').slice(0, 80)) + '</div></div><button class="btn btn-warning btn-sm" onclick="toggleTool(\'' + k + '\')">禁用</button></div>';
  }
  html += '</div></div>';

  // Disabled tools
  if (disabled.length > 0) {
    html += '<div class="card"><div class="card-title" style="font-size:14px">已禁用 (' + disabled.length + ')</div><div class="service-grid">';
    for (const [k, v] of disabled) {
      html += '<div class="service-card" style="opacity:0.5"><div class="service-info"><div class="service-name">' + escHtml(k) + '</div><div class="service-detail">' + escHtml((v.description || '').slice(0, 60)) + '</div></div><button class="btn btn-success btn-sm" onclick="toggleTool(\'' + k + '\')">启用</button></div>';
    }
    html += '</div></div>';
  }

  el.innerHTML = html;
}

async function toggleTool(name) {
  const r = await _origFetch('/v1/tools/' + name + '/toggle', {method: 'POST'});
  const d = await r.json();
  if (r.status !== 200) throw new Error(d.error || '操作失败');
  toast(d.enabled ? '✅ ' + name + ' 已启用' : '⏹️ ' + name + ' 已禁用', d.enabled ? 'ok' : 'info');
  renderMCP();
}

// ═══════════════════════════════════
// SKILLS TAB — marketplace + installed + toggle
// ═══════════════════════════════════
async function renderSkills() {
  const el = document.getElementById('dash-content');
  el.innerHTML = `<div class="skeleton-card"><div class="skeleton skeleton-title"></div><div class="skeleton skeleton-line"></div><div class="skeleton skeleton-line"></div></div>`;

  const skillsData = await API.get('/v1/skills').catch(() => null);
  const marketplaceData = await API.get('/v1/skills/marketplace?q=').catch(() => null);

  let html = '<div class="flex-between mb-16"><div class="card-title" style="margin:0">🧠 我的技能</div><button class="btn btn-secondary btn-sm" onclick="renderSkills()">🔄 刷新</button></div>';

  // ── Installed Skills ──
  html += '<div class="card"><div class="card-title" style="font-size:14px">已安装</div>';
  const skills = skillsData && skillsData.skills;
  if (!skills || skills.length === 0) {
    html += '<div class="text-center text-muted text-sm" style="padding:20px">暂无技能</div>';
  } else {
    html += '<table><tr><th>名称</th><th>状态</th><th></th></tr>' +
      skills.map(s => {
        const status = s.enabled
          ? '<span class="dot dot-ok" title="已启用"></span> 启用'
          : '<span class="dot dot-fail" title="已禁用"></span> 禁用';
        const btn = s.enabled
          ? '<button class="btn btn-warning btn-sm" onclick="toggleSkill(\'' + s.name + '\',false)">禁用</button>'
          : '<button class="btn btn-success btn-sm" onclick="toggleSkill(\'' + s.name + '\',true)">启用</button>';
        return '<tr><td><code>' + escHtml(s.name) + '</code></td><td>' + status + '</td><td style="text-align:right">' + btn + '</td></tr>';
      }).join('') + '</table>';
  }
  html += '</div>';

  // ── Marketplace ──
  html += '<div class="card"><div class="card-title" style="font-size:14px">📦 技能市场</div>' +
    '<div style="display:flex;gap:8px;margin-bottom:12px">' +
    '<input class="form-input" id="marketplace-search" placeholder="搜索技能..." onkeydown="if(event.key===\'Enter\') searchMarketplace()">' +
    '<button class="btn btn-primary btn-sm" onclick="searchMarketplace()">搜索</button>' +
    '</div><div id="marketplace-list">';

  const mk = marketplaceData && marketplaceData.skills;
  if (mk && mk.length > 0) {
    html += '<table><tr><th>名称</th><th>描述</th><th></th></tr>' +
      mk.slice(0, 10).map(s => {
        const btn = s.installed
          ? '<span class="text-muted text-sm">已安装</span>'
          : '<button class="btn btn-primary btn-sm" onclick="installSkill(\'' + s.name + '\')">安装</button>';
        return '<tr><td><code>' + escHtml(s.name) + '</code></td><td class="text-sm text-muted">' + escHtml(s.desc || '') + '</td><td style="text-align:right">' + btn + '</td></tr>';
      }).join('') + '</table>';
  } else {
    html += '<div class="text-center text-muted text-sm" style="padding:16px">输入关键词搜索技能市场</div>';
  }
  html += '</div></div>';

  el.innerHTML = html;
}

async function toggleSkill(name, enable) {
  try {
    const r = await _origFetch('/v1/skills/' + name + '/toggle', {method: 'POST'});
    const d = await r.json();
    if (r.status !== 200) throw new Error(d.error || '操作失败');
    toast(d.enabled ? '✅ ' + name + ' 已启用' : '⏹️ ' + name + ' 已禁用', d.enabled ? 'ok' : 'info');
    renderSkills();
  } catch(e) { toast('操作失败: ' + e.message, 'err'); }
}

async function searchMarketplace() {
  const q = document.getElementById('marketplace-search').value.trim();
  const list = document.getElementById('marketplace-list');
  if (!list) return;
  list.innerHTML = '<div class="text-center text-sm"><span class="spinner"></span> 搜索中...</div>';
  try {
    const data = await API.get('/v1/skills/marketplace?q=' + encodeURIComponent(q || ''));
    const skills = data.skills || [];
    if (skills.length === 0) {
      list.innerHTML = '<div class="text-center text-muted text-sm" style="padding:16px">未找到匹配的技能</div>';
      return;
    }
    list.innerHTML = '<table><tr><th>名称</th><th>描述</th><th></th></tr>' +
      skills.map(s => {
        const btn = s.installed
          ? '<span class="text-muted text-sm">已安装</span>'
          : '<button class="btn btn-primary btn-sm" onclick="installSkill(\'' + s.name + '\')">安装</button>';
        return '<tr><td><code>' + escHtml(s.name) + '</code></td><td class="text-sm text-muted">' + escHtml(s.desc || '') + '</td><td style="text-align:right">' + btn + '</td></tr>';
      }).join('') + '</table>';
  } catch(e) { list.innerHTML = '<div class="text-center text-muted text-sm" style="padding:16px">搜索失败: ' + e.message + '</div>'; }
}

async function installSkill(slug) {
  try {
    const r = await _origFetch('/v1/skills/marketplace/install', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({slug}),
    });
    const d = await r.json();
    if (r.status !== 200) throw new Error(d.error || '安装失败');
    toast('✅ ' + slug + ' 已安装', 'ok');
    renderSkills();
  } catch(e) { toast('安装失败: ' + e.message, 'err'); }
}

// ═══════════════════════════════════
// ADMIN — USER MANAGEMENT
// ═══════════════════════════════════

async function renderAdminUsers() {
  const el = document.getElementById('dash-content');
  el.innerHTML = `<div class="card">
    <div class="flex-between">
      <div class="card-title">👥 用户管理</div>
      <button class="btn btn-secondary btn-sm" onclick="renderAdminUsers()">🔄 刷新</button>
    </div>
    <div class="mt-16" id="admin-users-list"><div class="text-center"><span class="spinner"></span> 加载中...</div></div>
    <div class="card" style="margin-top:16px">
      <div class="card-title" style="font-size:13px">创建新用户</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:end">
        <div class="form-group" style="flex:2;min-width:140px">
          <label class="form-label">用户名</label>
          <input class="form-input" id="admin-new-username" placeholder="username">
        </div>
        <div class="form-group" style="flex:2;min-width:140px">
          <label class="form-label">密码</label>
          <input class="form-input" id="admin-new-password" type="password" placeholder="••••••">
        </div>
        <div class="form-group" style="flex:1;min-width:100px">
          <label class="form-label">角色</label>
          <select class="form-input" id="admin-new-role">
            <option value="user">普通用户</option>
            <option value="admin">管理员</option>
          </select>
        </div>
        <button class="btn btn-primary btn-sm" onclick="adminCreateUser()" style="margin-bottom:4px">创建</button>
      </div>
      <div id="admin-new-error" class="text-sm" style="color:var(--danger);margin-top:8px;display:none"></div>
    </div>
  </div>`;
  await adminRefreshUsers();
}

async function adminRefreshUsers() {
  try {
    const data = await API.get('/v1/admin/users');
    const list = document.getElementById('admin-users-list');
    const currentUserId = window.__auth.user && window.__auth.user.id;
    if (!data.users || data.users.length === 0) {
      list.innerHTML = '<div class="text-center text-muted">暂无用户</div>'; return;
    }
    list.innerHTML = '<table><tr><th>用户名</th><th>角色</th><th>显示名</th><th>创建时间</th><th>最后登录</th><th></th></tr>' +
    data.users.map(u => {
      const isSelf = u.id === currentUserId;
      const date = u.created_at ? new Date(u.created_at * 1000).toLocaleString('zh-CN') : '—';
      const lastLogin = u.last_login ? new Date(u.last_login * 1000).toLocaleString('zh-CN') : '从未';
      const roleOpts = `<select onchange="adminChangeRole('${u.id}', this.value)" ${isSelf ? 'disabled' : ''}>
        <option value="user" ${u.role === 'user' ? 'selected' : ''}>user</option>
        <option value="admin" ${u.role === 'admin' ? 'selected' : ''}>admin</option>
      </select>`;
      return `<tr>
        <td><code>${escHtml(u.username)}</code></td>
        <td>${roleOpts}</td>
        <td>${escHtml(u.display_name)}</td>
        <td class="text-muted text-sm">${date}</td>
        <td class="text-muted text-sm">${lastLogin}</td>
        <td>${isSelf ? '<span class="text-muted text-sm">当前</span>' : '<button class="btn btn-danger btn-sm" onclick="adminDeleteUser(\''+u.id+'\',\''+escHtml(u.username)+'\')">删除</button>'}</td>
      </tr>`;
    }).join('') + '</table>';
  } catch(e) {}
}

async function adminCreateUser() {
  const username = document.getElementById('admin-new-username').value.trim();
  const password = document.getElementById('admin-new-password').value;
  const role = document.getElementById('admin-new-role').value;
  const errEl = document.getElementById('admin-new-error');

  if (!username || !password) { errEl.textContent = '请填写用户名和密码'; errEl.style.display = 'block'; return; }

  try {
    const r = await _origFetch('/v1/admin/users', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username, password, role}),
    });
    const data = await r.json();
    if (r.status !== 200) throw new Error(data.error || '创建失败');
    document.getElementById('admin-new-username').value = '';
    document.getElementById('admin-new-password').value = '';
    errEl.style.display = 'none';
    toast('用户 "' + username + '" 已创建', 'ok');
    await adminRefreshUsers();
  } catch(e) { errEl.textContent = e.message; errEl.style.display = 'block'; }
}

async function adminChangeRole(userId, newRole) {
  try {
    const r = await _origFetch('/v1/admin/users/' + userId + '/role', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({role: newRole}),
    });
    const data = await r.json();
    if (r.status !== 200) throw new Error(data.error || '修改失败');
    toast('角色已更新 (将强制该用户重新登录)', 'ok');
  } catch(e) { toast('修改失败: ' + e.message, 'err'); }
}

async function adminDeleteUser(userId, username) {
  if (!confirm('确定删除用户「' + username + '」？\n该用户的所有会话将被保留。')) return;
  try {
    const r = await _origFetch('/v1/admin/users/' + userId, {method: 'DELETE'});
    const data = await r.json();
    if (r.status !== 200) throw new Error(data.error || '删除失败');
    toast('用户已删除', 'ok');
    await adminRefreshUsers();
  } catch(e) { toast('删除失败: ' + e.message, 'err'); }
}
