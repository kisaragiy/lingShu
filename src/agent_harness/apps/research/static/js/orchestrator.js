// orchestrator.js — 编排可视化（真实 API 驱动）
// 展示灵枢 Supervisor-Worker 架构 + 实时服务状态

const ORCH_NODES = [
  { id: 'supervisor', x: 50, y: 6,  name: 'Supervisor', role: '主控 · LangGraph 状态机', color: '#5e6ad2', dot: 'dot-ok' },
  { id: 'search',     x: 12, y: 38, name: 'Search Worker', role: '多引擎搜索 · 抓取 · 缓存', color: '#0ea5e9', dot: 'dot-ok' },
  { id: 'analyze',    x: 50, y: 38, name: 'Analyze Worker', role: '分析 · 总结 · 代码', color: '#14b8a6', dot: 'dot-ok' },
  { id: 'execute',    x: 88, y: 38, name: 'Execute Worker', role: '桌面 · 浏览器 · 绘画', color: '#f59e0b', dot: 'dot-ok' },
  { id: 'tools',      x: 12, y: 72, name: '工具集', role: '45+ 工具 · 三级权限', color: '#8b5cf6', dot: 'dot-warn' },
  { id: 'knowledge',  x: 50, y: 72, name: 'RAG 知识库', role: '向量 + BM25 降级', color: '#64748b', dot: 'dot-warn' },
  { id: 'llm',        x: 88, y: 72, name: '推理后端', role: 'Ollama / llama.cpp / API', color: '#10b981', dot: 'dot-warn' },
];
const ORCH_EDGES = [
  ['supervisor', 'search'], ['supervisor', 'analyze'], ['supervisor', 'execute'],
  ['search', 'tools'], ['analyze', 'tools'], ['execute', 'tools'],
  ['search', 'knowledge'], ['analyze', 'knowledge'],
  ['supervisor', 'llm'], ['search', 'llm'], ['analyze', 'llm'], ['execute', 'llm'],
];

let orchTimers = [];

function renderOrchestrate() {
  const el = document.getElementById('dash-content');
  document.title = '灵枢 — 编排';
  el.innerHTML = `
    <div class="flex-between mb-16">
      <div>
        <span class="card-title" style="font-size:18px;margin:0">🕸️ Agent 编排拓扑</span>
        <div class="text-muted text-sm" style="margin-top:4px">Supervisor-Worker · LangGraph 状态机 · <span id="orch-health">连接中…</span></div>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <span class="text-muted text-sm" id="orch-last">-</span>
        <button class="btn btn-secondary btn-sm" onclick="renderOrchestrate()">🔄 刷新</button>
      </div>
    </div>
    <div class="card" style="padding:0;overflow:hidden">
      <div id="orch-graph" style="position:relative;height:420px;background:var(--bg,#fff)">
        <svg id="orch-svg" style="position:absolute;inset:0;width:100%;height:100%;z-index:1"></svg>
      </div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px">
      <div class="card" style="margin:0">
        <div class="card-title" style="margin-bottom:10px">⚡ 运行中任务 <span class="badge" id="orch-task-count" style="margin-left:6px">0</span></div>
        <div id="orch-tasks" class="text-muted text-sm">加载中…</div>
      </div>
      <div class="card" style="margin:0">
        <div class="card-title" style="margin-bottom:10px">🔧 工具注册表 <span class="badge" id="orch-tool-count" style="margin-left:6px">-</span></div>
        <div id="orch-tools" class="text-muted text-sm">加载中…</div>
      </div>
    </div>`;

  _orchDrawGraph();
  _orchPoll();
  clearInterval(orchTimers['main']);
  orchTimers['main'] = setInterval(_orchPoll, 5000);
}

/* ─── 编排图（DOM 测量画线）─── */
function _orchDrawGraph() {
  const area = document.getElementById('orch-graph');
  const svg = document.getElementById('orch-svg');
  area.innerHTML = '<svg id="orch-svg" style="position:absolute;inset:0;width:100%;height:100%;z-index:1"></svg>';
  ORCH_NODES.forEach(n => {
    const d = document.createElement('div');
    d.id = 'on-' + n.id;
    d.style.cssText = `position:absolute;left:${n.x}%;top:${n.y}%;transform:translate(-50%,-50%);min-width:180px;text-align:center;z-index:2;cursor:default;transition:transform .2s ease, box-shadow .2s ease`;
    d.innerHTML = `<div style="display:inline-block;background:var(--bg-card,#fff);border:1px solid var(--border,rgba(15,23,42,.07));border-left:3px solid ${n.color};border-radius:14px;padding:12px 16px;box-shadow:var(--shadow,0 1px 2px rgba(16,24,40,.04));backdrop-filter:blur(10px);min-width:180px;transition:box-shadow .2s, transform .2s">
      <div style="display:flex;align-items:center;gap:8px;justify-content:flex-start">
        <span class="dot ${n.dot}" id="od-${n.id}" style="width:9px;height:9px;flex-shrink:0"></span>
        <b style="font-size:13px;font-weight:700">${n.name}</b>
        <span style="margin-left:auto;font-size:10px;color:var(--text-muted,#94a3b8)">${n.dot==='dot-ok'?'● 在线':'○ 待命'}</span>
      </div>
      <div style="font-size:11px;color:var(--text-muted,#888);margin-top:4px;text-align:left">${n.role}</div>
      <div style="font-size:10px;color:var(--text-muted,#aaa);margin-top:2px;text-align:left" id="om-${n.id}">-</div>
    </div>`;
    d.onmouseenter = () => { d.firstChild.style.transform = 'translateY(-3px)'; d.firstChild.style.boxShadow = 'var(--shadow-hover,0 8px 32px rgba(16,24,40,.10))'; };
    d.onmouseleave = () => { d.firstChild.style.transform = ''; d.firstChild.style.boxShadow = 'var(--shadow,0 1px 2px rgba(16,24,40,.04))'; };
    area.appendChild(d);
  });
  _orchDrawEdges();
}

function _orchDrawEdges() {
  const area = document.getElementById('orch-graph');
  const svg = document.getElementById('orch-svg');
  const ar = area.getBoundingClientRect();
  svg.setAttribute('viewBox', `0 0 ${ar.width} ${ar.height}`);
  const NS = 'http://www.w3.org/2000/svg';
  // 为每个节点生成渐变色 id 供连线引用
  const colorFor = { supervisor:'#6366f1', search:'#0ea5e9', analyze:'#14b8a6', execute:'#f59e0b', tools:'#8b5cf6', knowledge:'#64748b', llm:'#10b981' };
  ORCH_NODES.forEach(n => {
    if (svg.querySelector(`#grad-${n.id}`)) return;
    const defs = document.createElementNS(NS, 'defs');
    const g = document.createElementNS(NS, 'linearGradient');
    g.setAttribute('id', `grad-${n.id}`);
    const s1 = document.createElementNS(NS, 'stop'); s1.setAttribute('offset', '0%'); s1.setAttribute('stop-color', colorFor[n.id] || n.color); s1.setAttribute('stop-opacity', '0.5');
    const s2 = document.createElementNS(NS, 'stop'); s2.setAttribute('offset', '100%'); s2.setAttribute('stop-color', colorFor[n.id] || n.color); s2.setAttribute('stop-opacity', '0.15');
    g.appendChild(s1); g.appendChild(s2); defs.appendChild(g); svg.appendChild(defs);
  });
  ORCH_EDGES.forEach(([a, b]) => {
    const na = document.getElementById('on-' + a), nb = document.getElementById('on-' + b);
    if (!na || !nb) return;
    const ra = na.getBoundingClientRect(), rb = nb.getBoundingClientRect();
    const x1 = ra.left - ar.left + ra.width / 2, y1 = ra.top - ar.top + ra.height / 2;
    const x2 = rb.left - ar.left + rb.width / 2, y2 = rb.top - ar.top + rb.height / 2;
    // 贝塞尔曲线连接（SaaS 感）
    const mx = (x1 + x2) / 2, my = Math.max(y1, y2) - 40;
    const path = document.createElementNS(NS, 'path');
    path.setAttribute('d', `M ${x1} ${y1} C ${mx} ${my}, ${mx} ${my + 24}, ${x2} ${y2}`);
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke', `url(#grad-${a})`);
    path.setAttribute('stroke-width', '2');
    path.setAttribute('stroke-linecap', 'round');
    path.setAttribute('opacity', '0.4');
    svg.appendChild(path);
  });
}

/* ─── 真实 API 轮询 ─── */
async function _orchPoll() {
  const last = document.getElementById('orch-last');
  if (last) last.textContent = '更新于 ' + new Date().toLocaleTimeString();
  // /health
  const health = await API.get('/health').catch(() => null);
  if (health) {
    const el = document.getElementById('orch-health');
    if (el) el.textContent = `服务 ${health.status} · v${health.version} · ${health.active_sessions ?? 0} 活跃会话`;
    const dots = {
      supervisor: 'dot-ok', search: 'dot-ok', analyze: 'dot-ok', execute: 'dot-ok',
      tools: 'dot-warn', knowledge: 'dot-warn', llm: 'dot-warn',
    };
    if (health.status !== 'ok') Object.keys(dots).forEach(k => dots[k] = 'dot-fail');
    Object.entries(dots).forEach(([id, cls]) => {
      const d = document.getElementById('od-' + id);
      if (d) d.className = 'dot ' + cls;
    });
    const m = document.getElementById('om-supervisor');
    if (m) m.textContent = `sessions: ${health.active_sessions ?? 0}`;
  }
  // /v1/tasks（真实运行中任务）
  const tasks = await API.get('/v1/tasks').catch(() => null);
  const tEl = document.getElementById('orch-tasks');
  const tCount = document.getElementById('orch-task-count');
  if (tasks) {
    const list = tasks.tasks || [];
    if (tCount) tCount.textContent = list.length;
    if (list.length === 0) {
      tEl.innerHTML = '<span class="text-muted">暂无运行中任务 — 在对话页发起调研即在此显示</span>';
      ['search','analyze','execute'].forEach(id => {
        const m = document.getElementById('om-' + id);
        if (m) m.textContent = 'idle';
        const d = document.getElementById('od-' + id);
        if (d) d.className = 'dot dot-ok';
      });
    } else {
      tEl.innerHTML = list.map(t => {
        const sid = t.session_id || '';
        const short = sid.length > 28 ? sid.slice(0, 12) + '…' + sid.slice(-8) : sid;
        return `<div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--border,#eee)">
          <span style="font-family:monospace;font-size:12px">${short}</span>
          <span class="dot dot-ok" style="animation:pulse 1.5s infinite"></span>
        </div>`;
      }).join('');
      const active = list.filter(t => t.running).length;
      ['search','analyze','execute'].forEach(id => {
        const m = document.getElementById('om-' + id);
        if (m) m.textContent = `active: ${active}`;
        const d = document.getElementById('od-' + id);
        if (d) d.className = 'dot dot-ok';
      });
    }
  } else {
    if (tEl) tEl.innerHTML = '<span style="color:var(--danger,#e5484d)">无法连接 /v1/tasks — 认证或服务异常</span>';
  }
  // /v1/tools（真实工具注册表）
  const tools = await API.get('/v1/tools').catch(() => null);
  const tlEl = document.getElementById('orch-tools');
  const tlCount = document.getElementById('orch-tool-count');
  if (tools && tools.tools) {
    const entries = Object.entries(tools.tools);
    if (tlCount) tlCount.textContent = entries.length;
    if (entries.length > 0) {
      const top = entries.slice(0, 8);
      const rest = entries.length - top.length;
      tlEl.innerHTML = top.map(([k, v]) => {
        const on = v.enabled;
        return `<div style="display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid var(--border,#eee);font-size:12.5px">
          <span><span class="dot ${on ? 'dot-ok' : 'dot-fail'}" style="width:7px;height:7px;margin-right:6px"></span>${k}</span>
          <span class="text-muted" style="font-size:11px">${v.privilege || ''}</span>
        </div>`;
      }).join('') + (rest > 0 ? `<div class="text-muted text-sm" style="padding-top:6px">… 等共 ${entries.length} 个工具</div>` : '');
    }
    const m = document.getElementById('om-tools');
    if (m) m.textContent = `${entries.length} 已注册`;
  } else {
    if (tlEl) tlEl.innerHTML = '<span style="color:var(--danger,#e5484d)">无法获取工具列表</span>';
  }
  // /v1/models
  const models = await API.get('/v1/models').catch(() => null);
  if (models && models.data) {
    const m = document.getElementById('om-llm');
    if (m) m.textContent = models.data.map(x => x.id).join(' / ');
  }
}

// 页面离开时清理定时器（ui.js switchTab 会覆盖 dash-content，定时器由页面重建自然失效）
