// chat.js — Chat messages, SSE streaming, markdown rendering

// Simple markdown → HTML for chat messages
function mdToHtml(text) {
  if (!text) return '';
  // Escape HTML first
  let h = escHtml(text);
  // Code blocks (```...```) — must be before inline code
  h = h.replace(/```(\w*)\n?([\s\S]*?)```/g, function(m, lang, code) {
    const label = lang ? '<div class="code-lang-label">' + escHtml(lang) + '</div>' : '';
    return '<div class="code-block-wrapper">' + label + '<pre><code class="lang-' + escHtml(lang) + '">' + code.trim() + '</code></pre></div>';
  });
  // Inline code
  h = h.replace(/`([^`]+)`/g, '<code>$1</code>');
  // Bold (**text**)
  h = h.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  // Unordered lists (- item)
  h = h.replace(/^- (.+)$/gm, '<li>$1</li>');
  h = h.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');
  // Headers (## / ###)
  h = h.replace(/^### (.+)$/gm, '<h4>$1</h4>');
  h = h.replace(/^## (.+)$/gm, '<h3>$1</h3>');
  // Newlines → <br>
  h = h.replace(/\n/g, '<br>');
  return h;
}

// Render a chat message (user or assistant)
function renderChatMessage(role, content) {
  if (role === 'user') return escHtml(content).replace(/\n/g, '<br>');
  return mdToHtml(content);
}

// ═══════════════════════════════════
// CHAT TAB
// ═══════════════════════════════════
function renderDashChat() {
  const el = document.getElementById('dash-content');
  el.innerHTML = `
    <div class="chat-card">
      <div class="chat-messages" id="chat-messages">
        <div class="chat-empty">
          <div class="chat-empty-icon">⚡</div>
          <div class="chat-empty-text">灵枢 — AI 调研助手</div>
          <div class="chat-empty-hint">输入关键词开始调研，或上传文档到知识库</div>
        </div>
      </div>
      <div id="chat-thinking" class="chat-thinking hidden"></div>
      <div class="chat-input-bar">
        <input id="chat-input" class="form-input" placeholder="输入消息，Enter 发送 / Ctrl+Enter 换行..."
          onkeydown="if(event.key==='Enter' && !event.shiftKey && !event.ctrlKey) { event.preventDefault(); sendChatMessage() } else if(event.key==='Escape') { cancelChatMessage() }">
        <button class="btn btn-primary" onclick="sendChatMessage()" id="chat-send-btn">发送</button>
        <button class="btn btn-danger hidden" onclick="cancelChatMessage()" id="chat-cancel-btn">取消</button>
      </div>
    </div>`;
  // Restore session messages if we have one
  if (chatSessionId && chatHistory.length > 0) {
    renderMessages(chatHistory.map(h => ({role: h.role, content: h.content})));
    document.getElementById('chat-msg-count')?.remove();
  } else {
    loadDefaultChatSession();
  }
}

async function loadDefaultChatSession() {
  chatAbortController = null;
  chatHistory = [];
  try {
    const data = await API.get('/v1/sessions');
    const sessions = data.sessions || [];
    if (sessions.length > 0 && sessions[0].exchanges > 0) {
      chatSessionId = sessions[0].id;
      await restoreChatSession(sessions[0].id);
      renderSidebarList();
    } else {
      startNewChat();
    }
  } catch(e) {
    startNewChat();
  }
}

async function restoreChatSession(sessionId) {
  try {
    const data = await API.get('/v1/sessions/' + sessionId + '/messages');
    const msgs = data.messages || [];
    chatHistory = msgs.map(m => ({role: m.role, content: m.content}));
    renderMessages(msgs);
  } catch(e) {
    startNewChat();
  }
}

function startNewChat() {
  chatSessionId = 'chat-' + Date.now() + '-' + Math.random().toString(36).slice(2,6);
  chatHistory = [];
  const container = document.getElementById('chat-messages');
  if (container) {
    container.innerHTML = '<div class="text-center text-muted text-sm" style="padding:40px">输入消息开始与灵枢对话</div>';
  }
}

function renderMessages(msgs) {
  const container = document.getElementById('chat-messages');
  if (!container) return;
  container.innerHTML = '';
  if (!msgs || msgs.length === 0) {
    container.innerHTML = '<div class="text-center text-muted text-sm" style="padding:40px">输入消息开始与灵枢对话</div>';
    return;
  }
  msgs.forEach(m => {
    if (m.role === 'user') addChatBubble('user', m.content || '');
    else if (m.role === 'assistant') addChatBubble('assistant', (m.content || '').slice(0, 500));
  });
  container.scrollTop = container.scrollHeight;
}

async function sendChatMessage() {
  const input = document.getElementById('chat-input');
  const msg = input.value.trim();
  if (!msg) return;

  document.querySelector('#chat-messages .text-center')?.remove();

  addChatBubble('user', msg);
  input.value = '';
  input.disabled = true;
  document.getElementById('chat-send-btn').classList.add('hidden');
  document.getElementById('chat-cancel-btn').classList.remove('hidden');

  const thinkingEl = document.getElementById('chat-thinking');
  thinkingEl.classList.remove('hidden');
  thinkingEl.innerHTML = '<div class="thinking-steps"><div class="thinking-step active"><span class="thinking-icon">🤔</span><span>分析请求...</span></div></div>';
  let thinkingText = '';

  const assistantId = 'assistant-' + Date.now();
  addChatBubble('assistant', '<span class="spinner"></span>', assistantId);
  const assistantEl = document.getElementById(assistantId);

  const messages = [...chatHistory, {role: 'user', content: msg}];

  chatAbortController = new AbortController();
  try {
    const resp = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Session-Id': chatSessionId},
      body: JSON.stringify({model: 'lingShu-deep', messages, stream: true}),
      signal: chatAbortController.signal,
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let fullContent = '';
    let currentThinking = '';

    while (true) {
      const {done, value} = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, {stream: true});
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6).trim();
        if (data === '[DONE]') continue;

        try {
          const parsed = JSON.parse(data);
          const content = parsed.choices?.[0]?.delta?.content || '';
          if (content) {
            fullContent += content;
            if (content.startsWith('🤔') || content.startsWith('🔀') || content.startsWith('▶️') ||
                content.startsWith('✅') || content.startsWith('  ✅') || content.startsWith('  ❌') ||
                content.startsWith('🧠') || content.startsWith('⚡') || content.startsWith('📚')) {
              currentThinking += content;
              // Build step display from thinking text
              const steps = currentThinking.split('\n').filter(s => s.trim()).map(s => {
                const trimmed = s.trim();
                const icon = trimmed.match(/^([\u{1F000}-\u{1FFFF}]|[\u2600-\u27BF]|[\u2700-\u27BF]|✅|❌|⚡|▶️)/u);
                const text = icon ? trimmed.slice(icon[0].length).trim() : trimmed;
                const isActive = !trimmed.startsWith('✅') && !trimmed.startsWith('❌') && (trimmed === currentThinking.trim().split('\n').filter(x=>x.trim()).pop());
                return '<div class="thinking-step' + (isActive ? ' active' : ' done') + '"><span class="thinking-icon">' + (icon ? icon[0] : '•') + '</span><span>' + escHtml(text) + '</span></div>';
              }).join('');
              thinkingEl.innerHTML = '<div class="thinking-steps">' + steps + '</div>';
              thinkingEl.scrollTop = thinkingEl.scrollHeight;
            } else {
              if (assistantEl) {
                let body = assistantEl.querySelector('.chat-msg-body');
                if (!body) { body = document.createElement('div'); body.className = 'chat-msg-body'; assistantEl.appendChild(body); }
                body.innerHTML = mdToHtml(fullContent);
              }
            }
          }
        } catch(e) {}
      }
    }

    chatHistory = messages;
    chatHistory.push({role: 'assistant', content: fullContent});
    if (assistantEl) {
      let body = assistantEl.querySelector('.chat-msg-body');
      if (!body) { body = document.createElement('div'); body.className = 'chat-msg-body'; assistantEl.appendChild(body); }
      body.innerHTML = mdToHtml(fullContent);
    }
    thinkingEl.classList.add('hidden');

    if (fullContent.length > 100) {
      const btns = document.createElement('div');
      btns.className = 'chat-bubble';
      btns.style.cssText = 'align-self:flex-start; margin-top:4px; display:flex; gap:4px; max-width:100%; background:none; border:none; padding:0';
      btns.innerHTML = `
        <button class="btn btn-secondary btn-sm" onclick="saveReport(this, '${chatSessionId}')">💾 保存报告</button>
        <button class="btn btn-primary btn-sm" onclick="formalizeAndOpen(this, '${chatSessionId}')">📄 生成正式报告</button>
        <button class="btn btn-secondary btn-sm" onclick="showAgentLog('${chatSessionId}')">🔍 运行详情</button>`;
      document.getElementById('chat-messages').appendChild(btns);

      // Auto-save draft (silent, background)
      if (fullContent.length > 300) {
        const title = '草稿_' + new Date().toISOString().slice(0,10);
        _origFetch('/v1/reports', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({title, content: fullContent.slice(0, 2000), tags:['chat', 'draft'], source_session: chatSessionId}),
        }).then(r => r.json()).then(d => {
          if (d && d.id) { window._lastReportId = d.id; }
        }).catch(() => {});
      }
    }

    // Refresh sidebar (new session may appear)
    loadSidebarSessions();

  } catch(e) {
    if (e.name === 'AbortError') {
      addChatBubble('assistant', '⛔ 任务已取消', null, true);
    } else {
      addChatBubble('assistant', '❌ 错误: ' + e.message, null, true);
    }
  }

  input.disabled = false;
  input.focus();
  document.getElementById('chat-send-btn').classList.remove('hidden');
  document.getElementById('chat-cancel-btn').classList.add('hidden');
  document.getElementById('chat-thinking').classList.add('hidden');
  chatAbortController = null;

  const msgContainer = document.getElementById('chat-messages');
  if (msgContainer) msgContainer.scrollTop = msgContainer.scrollHeight;
}

function cancelChatMessage() {
  if (chatAbortController) {
    chatAbortController.abort();
    fetch('/v1/tasks/' + chatSessionId + '/cancel', {method: 'POST'}).catch(()=>{});
  }
}

function addChatBubble(role, content, id, isSystem) {
  const container = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.id = id || '';
  div.className = 'chat-bubble ' + (isSystem ? 'system' : role);

  // Timestamp
  const now = new Date();
  const ts = now.getHours().toString().padStart(2,'0') + ':' + now.getMinutes().toString().padStart(2,'0');
  const icon = role === 'user' ? '🧑' : '🤖';

  // Copy button
  const copyBtn = isSystem ? '' : '<button class="chat-copy-btn" onclick="copyMsg(this)" title="复制">📋</button>';

  div.innerHTML = '<div class="chat-msg-header"><span class="chat-msg-role">' + icon + ' ' + (role === 'user' ? '你' : '灵枢') + '</span><span class="chat-msg-ts">' + ts + '</span>' + copyBtn + '</div><div class="chat-msg-body">' + renderChatMessage(role, content) + '</div>';
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function copyMsg(btn) {
  const body = btn.parentElement.nextElementSibling;
  const text = body ? body.textContent || body.innerText : '';
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => {
    toast('已复制', 'ok');
  }).catch(() => {
    // Fallback
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
    toast('已复制', 'ok');
  });
}

async function saveReport(btn, sessionId) {
  const container = document.getElementById('chat-messages');
  const bubbles = container.querySelectorAll('.chat-bubble.assistant');
  const last = bubbles[bubbles.length - 1];
  if (!last) return;
  const content = last.textContent || '';
  const title = '调研报告_' + new Date().toISOString().slice(0,10);
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>';
  try {
    await fetch('/v1/reports', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({title, content, tags:['chat'], source_session:sessionId})});
    toast('报告已保存 ✓', 'ok');
    btn.innerHTML = '💾 已保存'; btn.style.opacity = '0.5';
  } catch(e) { toast('保存失败: ' + e.message, 'err'); btn.disabled = false; btn.innerHTML = '💾 保存报告'; }
}

async function formalizeAndOpen(btn, sessionId) {
  const container = document.getElementById('chat-messages');
  const bubbles = container.querySelectorAll('.chat-bubble.assistant');
  const last = bubbles[bubbles.length - 1];
  if (!last) return;
  const content = last.textContent || '';
  const title = '正式报告_' + new Date().toISOString().slice(0,10);
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> 生成中...';

  try {
    const r = await fetch('/v1/reports/formalize', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({title, content, tags:['正式报告'], source_session:sessionId}),
    });
    const data = await r.json();
    if (data.id) {
      toast('正式报告已生成 ✓ 正在打开...', 'ok');
      btn.innerHTML = '📄 已生成';
      // Open report in new tab after a brief delay
      setTimeout(() => window.open('/v1/reports/' + data.id, '_blank'), 500);
    } else {
      toast('生成失败', 'err'); btn.disabled = false; btn.innerHTML = '📄 生成正式报告';
    }
  } catch(e) {
    toast('生成失败: ' + e.message, 'err');
    btn.disabled = false; btn.innerHTML = '📄 生成正式报告';
  }
}
