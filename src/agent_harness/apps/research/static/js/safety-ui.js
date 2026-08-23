// safety-ui.js — 安全护栏前端交互（模式指示器 + 确认按钮 + 设置面板）
// 依赖: api.js(API), ui.js(toast), chat.js(escHtml)

// ─── 安全模式颜色映射 ───
const SAFETY_COLORS = {
  default: {bg: '#dbeafe', text: '#1e40af', label: '🔒 安全模式'},
  full:    {bg: '#fef3c7', text: '#92400e', label: '🔓 全权模式'},
};

// ─── 更新状态栏安全徽标 ───
async function updateSafetyBadge() {
  const badge = document.getElementById('safety-badge');
  if (!badge) return;
  try {
    const resp = await fetch('/v1/safety/mode');
    const data = await resp.json();
    const mode = data.mode || 'default';
    const c = SAFETY_COLORS[mode] || SAFETY_COLORS.default;
    const pending = (data.pending || []).length;
    badge.innerHTML = `<span style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:4px;
      font-size:10px;font-weight:600;cursor:pointer;background:${c.bg};color:${c.text}"
      onclick="toggleSafetyMode()" title="点击切换安全模式">${c.label}${pending ? ` <span style="background:#ef4444;color:#fff;border-radius:50%;padding:0 5px;font-size:9px">${pending}</span>` : ''}</span>`;
  } catch(e) {
    // badge const is try-block scoped; re-query
    const b = document.getElementById('safety-badge');
    if (b) b.textContent = '';
  }
}

// ─── 切换安全模式 ───
async function toggleSafetyMode() {
  try {
    const resp = await fetch('/v1/safety/mode');
    const data = await resp.json();
    const current = data.mode || 'default';
    const next = current === 'default' ? 'full' : 'default';
    const r = await fetch('/v1/safety/mode', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode: next}),
    });
    const result = await r.json();
    if (result.ok || result.mode) {
      toast(`安全模式已切换为: ${next === 'full' ? '🔓 全权模式' : '🔒 安全模式'}`, 'ok');
      updateSafetyBadge();
    } else {
      toast(result.error || '切换失败', 'err');
    }
  } catch(e) {
    toast('切换失败: ' + e.message, 'err');
  }
}

// ─── 从聊天回复中检测确认码 ───
// 格式: "[需要确认] ... (确认码: ab12cd34)"
function extractConfirmCode(text) {
  const m = (text || '').match(/确认码:\s*([0-9a-f]{8})/i);
  return m ? m[1] : null;
}

// ─── 确认操作 ───
async function confirmOperation(code) {
  try {
    const r = await fetch('/v1/safety/confirm', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({code}),
    });
    const result = await r.json();
    if (result.ok) {
      toast('✅ 操作已确认，请重新发送相同请求', 'ok');
      updateSafetyBadge();
      return true;
    } else {
      toast(result.error || '确认失败', 'err');
      return false;
    }
  } catch(e) {
    toast('确认失败: ' + e.message, 'err');
    return false;
  }
}

// ─── 在聊天气泡底部注入确认/拒绝按钮 ───
function injectConfirmButtons(containerEl, text) {
  if (!containerEl || !text) return;
  const code = extractConfirmCode(text);
  if (!code) return;
  const row = document.createElement('div');
  row.style.cssText = 'display:flex;gap:8px;margin-top:8px';
  row.innerHTML =
    `<button class="btn btn-sm" style="background:#22c55e;color:#fff;border:none;padding:4px 16px;border-radius:6px;cursor:pointer"
      onclick="confirmOperation('${code}')">✅ 确认操作</button>
    <button class="btn btn-sm" style="background:#ef4444;color:#fff;border:none;padding:4px 16px;border-radius:6px;cursor:pointer"
      onclick="this.closest('.chat-bubble')?.remove()">❌ 拒绝</button>
    <span class="text-muted text-sm" style="align-self:center;font-size:10px">确认码: ${code}</span>`;
  containerEl.appendChild(row);
}

// ─── 安全面板（设置页中嵌入） ───
function renderSafetyPanel() {
  const el = document.getElementById('dash-content') || document.getElementById('app');
  let html =
    '<div class="card">' +
    '<div class="card-title">🔒 安全护栏</div>' +
    '<div class="text-sm text-muted" style="margin-bottom:12px">' +
    '灵枢权限双层架构（灵感来自 WorkBuddy 腾讯云桌面 AI agent）。' +
    '<ul style="margin:4px 0;padding-left:20px">' +
    '<li><strong>🔒 安全模式 (default)</strong> — 沙箱优先。写敏感路径/批量删/脚本/外发/网络访问需确认</li>' +
    '<li><strong>🔓 全权模式 (full)</strong> — 放行所有操作，全量审计留痕</li>' +
    '<li>写前自动备份（覆盖写存 .bak）、删除走回收站</li>' +
    '</ul></div>' +
    '<div id="safety-status" class="text-sm" style="margin-bottom:8px">加载中...</div>' +
    '<div id="safety-pending"></div>' +
    '</div>';
  el.innerHTML = html;
  refreshSafetyPanel();
}

async function refreshSafetyPanel() {
  const statusEl = document.getElementById('safety-status');
  const pendingEl = document.getElementById('safety-pending');
  if (!statusEl) return;
  try {
    const resp = await fetch('/v1/safety/mode');
    const data = await resp.json();
    const mode = data.mode || 'default';
    const pending = data.pending || [];
    const c = SAFETY_COLORS[mode] || SAFETY_COLORS.default;
    statusEl.innerHTML =
      `<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px">
        <span style="display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:6px;background:${c.bg};color:${c.text};font-weight:600;font-size:14px">${c.label}</span>
        <button class="btn btn-sm" onclick="toggleSafetyMode();setTimeout(refreshSafetyPanel,500)"
          style="${mode === 'default' ? 'background:#fef3c7;color:#92400e' : 'background:#dbeafe;color:#1e40af'};border:1px solid var(--border);padding:4px 12px;border-radius:6px;cursor:pointer">
          切换到 ${mode === 'default' ? '🔓 全权模式' : '🔒 安全模式'}
        </button>
      </div>`;
    if (pending.length === 0) {
      pendingEl.innerHTML = '<div class="text-center text-muted text-sm" style="padding:16px">✅ 无待确认操作</div>';
    } else {
      pendingEl.innerHTML =
        '<div class="card-title" style="font-size:13px;margin-top:12px">⏳ 待确认操作</div>' +
        pending.map(p =>
          `<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border)">
            <div><span class="text-sm">${escHtml(p.tool || '')}</span><br><span class="text-xs text-muted">${escHtml(p.reason || '')}</span></div>
            <div style="display:flex;gap:4px;align-items:center">
              <button class="btn btn-sm" style="background:#22c55e;color:#fff;border:none;padding:2px 10px;border-radius:4px;cursor:pointer"
                onclick="confirmOperation('${p.code}');setTimeout(refreshSafetyPanel,500)">✅ 确认</button>
              <span class="text-xs text-muted">${p.code}</span>
            </div>
          </div>`
        ).join('');
    }
  } catch(e) {
    statusEl.innerHTML = `<div class="text-sm" style="color:var(--danger)">❌ 无法获取安全状态: ${escHtml(e.message)}</div>`;
    pendingEl.innerHTML = '';
  }
}