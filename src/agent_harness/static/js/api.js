// api.js — API helpers, auth, dark mode, and shared utilities

// ─── API helper (with auth) ───
function _apiHeaders() {
  const h = {'Content-Type': 'application/json'};
  if (window.__API_TOKEN__) h['X-API-Key'] = window.__API_TOKEN__;
  return h;
}
function _apiGetHeaders() {
  const h = {};
  if (window.__API_TOKEN__) h['X-API-Key'] = window.__API_TOKEN__;
  return h;
}
const API = {
  async get(path) {
    const r = await fetch(path, {headers: _apiGetHeaders()});
    const d = await r.json(); return d;
  },
  async post(path, body) {
    const r = await fetch(path, {
      method: 'POST', headers: _apiHeaders(),
      body: JSON.stringify(body),
    }); return await r.json();
  },
  async upload(path, formData) {
    const r = await fetch(path, { method: 'POST', body: formData, headers: _apiGetHeaders() });
    return await r.json();
  },
};

// ─── Auth state ───
window.__auth = { token: null, user: null };

// Load saved JWT from localStorage
const _savedToken = localStorage.getItem('lingShu_jwt');
if (_savedToken) window.__auth.token = _savedToken;

// ─── Dark mode ───
function initDarkMode() {
  const saved = localStorage.getItem('lingShu_dark');
  if (saved === 'true') {
    document.body.classList.add('dark-mode');
  } else if (saved === 'false') {
    document.body.classList.remove('dark-mode');
  } else {
    // Auto-detect system preference
    if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
      document.body.classList.add('dark-mode');
    }
  }
}
function toggleDarkMode() {
  document.body.classList.toggle('dark-mode');
  const isDark = document.body.classList.contains('dark-mode');
  localStorage.setItem('lingShu_dark', isDark ? 'true' : 'false');
  toast(isDark ? '🌙 暗色模式' : '☀️ 亮色模式', 'info');
}
initDarkMode();

// ─── Auto-attach auth header to all /v1/* fetch calls ───
const _origFetch = window.fetch;
window.fetch = function(url, opts) {
  opts = opts || {};
  const urlStr = (typeof url === 'string') ? url : url.url;
  if (urlStr && urlStr.startsWith('/v1/')) {
    opts.headers = opts.headers || {};
    if (opts.headers instanceof Headers) {
      // JWT Bearer (preferred)
      if (window.__auth.token && !opts.headers.has('Authorization')) {
        opts.headers.set('Authorization', 'Bearer ' + window.__auth.token);
      }
      // API Key fallback
      if (window.__API_TOKEN__ && !opts.headers.has('X-API-Key')) {
        opts.headers.set('X-API-Key', window.__API_TOKEN__);
      }
    } else {
      // Plain object
      if (window.__auth.token && !opts.headers['Authorization']) {
        opts.headers['Authorization'] = 'Bearer ' + window.__auth.token;
      }
      if (window.__API_TOKEN__ && !opts.headers['X-API-Key']) {
        opts.headers['X-API-Key'] = window.__API_TOKEN__;
      }
    }
  }
  return _origFetch.call(window, url, opts);
};

// ─── Auth helpers ───
async function authLogin(username, password) {
  const r = await _origFetch('/v1/auth/login', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username, password}),
  });
  const data = await r.json();
  if (r.status !== 200) throw new Error(data.error || '登录失败');
  window.__auth.token = data.access_token;
  window.__auth.user = data.user;
  localStorage.setItem('lingShu_jwt', data.access_token);
  localStorage.setItem('lingShu_user', JSON.stringify(data.user));
  return data;
}

async function authLogout() {
  await _origFetch('/v1/auth/logout', {method: 'POST'}).catch(()=>{});
  window.__auth.token = null;
  window.__auth.user = null;
  localStorage.removeItem('lingShu_jwt');
  localStorage.removeItem('lingShu_user');
}

async function authCheck() {
  // Try JWT auth first
  if (window.__auth.token) {
    try {
      const r = await _origFetch('/v1/auth/me', {headers: {'Authorization': 'Bearer ' + window.__auth.token}});
      if (r.status === 200) {
        const data = await r.json();
        window.__auth.user = data.user;
        return 'authenticated';
      }
    } catch(e) {}
  }
  // Try API key fallback (for Open WebUI / CLI)
  try {
    const r = await _origFetch('/v1/auth/me', {headers: {'X-API-Key': window.__API_TOKEN__}});
    if (r.status === 200) {
      const data = await r.json();
      window.__auth.user = data.user;
      return 'authenticated';
    }
  } catch(e) {}
  return 'unauthenticated';
}

// ─── UI utilities ───

// Show skeleton loading in a container
function showSkeleton(containerId, lines=4) {
  const el = typeof containerId === 'string' ? document.getElementById(containerId) : containerId;
  if (!el) return;
  el.innerHTML = '<div class="skeleton-card">' +
    '<div class="skeleton skeleton-title"></div>' +
    Array(lines).fill('<div class="skeleton skeleton-line"></div>').join('') +
    '</div>';
}

// Show error card with retry
function showErrorEl(container, msg, detail, onRetry) {
  const el = typeof container === 'string' ? document.getElementById(container) : container;
  if (!el) return;
  const retryBtn = onRetry ? '<button class="btn btn-primary btn-sm" onclick="(' + onRetry.toString() + ')()">重试</button>' : '';
  el.innerHTML = '<div class="error-card"><div class="error-icon">⚠️</div><div class="error-msg">' + escHtml(msg) + '</div>' +
    (detail ? '<div class="error-detail">' + escHtml(detail) + '</div>' : '') + retryBtn + '</div>';
}

// Safe API call with loading state
async function apiLoad(container, fetchFn) {
  showSkeleton(container);
  try {
    const result = await fetchFn();
    return result;
  } catch(e) {
    showErrorEl(container, '加载失败', e.message, function(){ apiLoad(container, fetchFn); });
    return null;
  }
}

// ─── HTML escape ───
function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// ─── Auth / Logout ───
async function doLogout() {
  await authLogout();
  window.__NEEDS_ADMIN__ = false;
  init();
}
