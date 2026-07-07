# 灵枢 (LingShu) — Super Agent

> 灵枢者，智之枢也。以 Supervisor 为枢，Worker 为四肢，调度万端。
> 一个基于 LangGraph Supervisor-Worker 架构的通用 AI Agent 产品。
> 通过 Open WebUI 交互，由本地 LLM 驱动，连接搜索、代码、桌面、AIGC 等丰富工具生态。

---

## 一、核心身份

**Super Agent** 不是一个聊天机器人，而是一个**自主智能体系统**：

| 维度 | 定义 |
|------|------|
| **本质** | 基于 LangGraph 的有状态多 Agent 编排引擎 |
| **交互层** | Open WebUI（用户前端） |
| **推理引擎** | Qwen3.6-35B（本地主力）+ DeepSeek Flash（云端编排）+ Ollama 模型群 |
| **工具数量** | 45+ 已注册工具（含 RAG 检索 + A 股数据 + AIGC 绘画） |
| **架构模式** | Supervisor-Worker（主管-工人） |
| **产品形态** | pip 包 + API 服务 + Open WebUI 集成 |

### 一句话定义

> 用户用自然语言下达指令 → Supervisor 理解意图、拆解任务 → 并行派给多个 Worker 执行 → 收集结果、多轮迭代直到完成 → 返回最终回复。

---

## 二、系统架构

```
┌─────────────────────────────────────────────────────┐
│                  用户界面层                          │
│  Open WebUI (:3000)  ←→  Agent Harness API (:8788)  │
│       (Chat UI / 会话管理)  (OpenAI兼容API)          │
└────────────────────────┬────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────┐
│                  编排层 (LangGraph)                   │
│                                                      │
│  ┌──────────────────────────────────────┐            │
│  │          Supervisor Agent             │            │
│  │  (分析→分配→收集→验收→重规划→最终)     │            │
│  └────┬─────────┬─────────┬─────────────┘            │
│       │         │         │                          │
│  ┌────▼──┐ ┌───▼────┐ ┌──▼────────┐                 │
│  │Search │ │Analyze │ │ Execute   │                 │
│  │Worker │ │Worker  │ │ Worker    │                 │
│  └───┬───┘ └───┬────┘ └───┬───────┘                 │
│      │         │          │                          │
│      ▼         ▼          ▼                          │
│  ┌─────┐ ┌───────┐ ┌────────────┐                   │
│  │搜索  │ │代码   │ │桌面/ComfyUI│                   │
│  │抓取  │ │分析   │ │浏览器/聊天 │                   │
│  │RAG   │ │总结   │ │文件操作    │                   │
│  └─────┘ └───────┘ └────────────┘                   │
│                                                      │
│  ├── 熔断器 (Circuit Breaker)                        │
│  ├── 全链路追踪 (Tracing)                            │
│  └── 评估套件 (Eval Suite)                           │
└──────────────────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────┐
│                  基础设施层                           │
│                                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐             │
│  │llama.cpp │ │ Ollama   │ │ DeepSeek │             │
│  │:8080     │ │ :11434   │ │ Flash    │             │
│  │Qwen3.6-  │ │coder/    │ │ 云端API  │             │
│  │35B(主力) │ │r1/qwen3 │ │ (备用)   │             │
│  └──────────┘ └──────────┘ └──────────┘             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐             │
│  │SearXNG   │ │ Model    │ │ ComfyUI  │             │
│  │:4000     │ │Proxy:8081│ │ :8188    │             │
│  │私有搜索  │ │路由网关  │ │ AI 绘画  │             │
│  └──────────┘ └──────────┘ └──────────┘             │
└──────────────────────────────────────────────────────┘
```

### 关键设计原则

1. **Supervisor 不干活，只做决策** — 分析任务、分配 Worker、验收结果，所有具体操作由 Worker 完成
2. **Worker 只有专用工具集** — Search Worker 只有搜索工具，不能执行代码
3. **多轮迭代** — 一轮不够可以再规划再执行，最多 3 轮（SUPERVISOR_MAX_ROUNDS）
4. **Worker 并行执行** — ThreadPoolExecutor 同时跑多个 Worker，互不等待
5. **安全熔断** — token 预算、时间预算、无进展检测三重保护，防止无限循环

---

## 三、Worker 能力矩阵

| Worker | 工具 | 能力描述 |
|--------|------|----------|
| **Search** | search, fetch, web_scrape, agent_browser, rag_query, datetime | 搜索、抓取、浏览网页、RAG 检索 |
| **Analyze** | think, code_execute, summarize, file_read, file_write | 思考、编程、总结、文件操作 |
| **Execute** | desktop_gui, browser_automation, app_launch, comfyui_text2img, comfyui_img2img, chat_send, file_write | 桌面自动化、浏览器操作、AI 绘画、发消息 |

### 工具全集（45+）

| 类别 | 工具 | 说明 |
|------|------|------|
| 🧠 思考 | think | LLM 自有推理能力 |
| 🔍 搜索 | search, fetch, web_scrape, agent_browser | 网页搜索 + 内容抓取 |
| 💻 代码 | code_execute | Python 代码执行 |
| 📁 文件 | file_read, file_write | 工作区内文件读写 |
| 🖥️ 桌面 | desktop_gui | 截图、按键、鼠标、窗口操作 |
| 🌐 浏览器 | browser_automation | Playwright 浏览器自动化 |
| 🎨 绘画 | comfyui_text2img, comfyui_img2img, character_sheet, scene_grid | ComfyUI 图像生成 |
| 💬 消息 | chat_send, wechat_send, qq_send | 社交消息发送 |
| 🚀 启动 | app_launch | Windows 应用启动 |
| 📊 股票 | stock_realtime, stock_history, stock_indicator, stock_financial, stock_search, stock_compare, stock_market_index, stock_alert_condition | A 股数据 |
| 📚 RAG | rag_query, rag_index | 本地向量检索 |
| ⏰ 时间 | datetime | 当前时间 |
| 📝 总结 | summarize | 长文本压缩 |
| 🔒 权限 | permission_gate | 高风险操作确认 |
| 🐙 GitHub | github_issues | Issue 管理 |

---

## 四、核心工作流

### 4.1 标准多 Agent 流程

```
用户: "帮我看一下今天AI Agent有什么重大新闻，然后总结给我"

1. Analyze (Supervisor)
   → 识别为 mixed 类型 → 分配 search + analyze 两个 Worker
   
2. Dispatch (并行)
   ├→ Search Worker: 搜索"AI Agent 2025 新闻" → 返回3条结果
   └→ Analyze Worker: 准备接收结果并总结

3. Collect (Supervisor)
   → search 成功 → analyze 还没开始（等待数据）
   → 验收：结果不够完整，需要重新规划

4. Replan → Dispatch (第二轮)
   └→ Analyze Worker: 基于搜索结果写总结

5. Collect → Finalize
   → 生成回复，包含新闻要点+总结
```

### 4.2 搜索 + 分析链

```
用户: "最近有什么新的开源LLM？"

Analyze → Search Worker (搜索新闻)
       → Analyze Worker (总结结果)
       → Finalize (给出答案)
```

### 4.3 桌面自动化链

```
用户: "打开浏览器帮我查一下天气"

Analyze → Execute Worker (启动浏览器→搜索天气→截图)
       → Finalize (展示结果)
```

### 4.4 AIGC 视频管线（ComicAgent）

```
用户: "猫娘在咖啡馆打工的一天"

Script → LLM 生成剧本 + 分镜
Images → ComfyUI 批量出图（IPAdapter 角色一致性）
Voice  → edge-tts 中文配音
Video  → ffmpeg 合成（缩放+拼接+字幕）→ 输出 MP4
```

### 4.5 多轮迭代场景

当一轮 Worker 结果不够时，Supervisor 自动进入 Replan：

```
Round 1: "搜索AI新闻" → search 返回5条标题（太简略）
Round 2: "展开第一条和第三条的详细内容" → fetch 抓取详情
Round 3: "用Python提取关键数据" → code_execute 整理
→ Finalize: 给出结构化报告
```

---

## 五、产品功能清单

### P0 — 核心可用（已全部完成 ✅）

- [x] Supervisor-Worker 多 Agent 编排
- [x] 3 个 Worker（Search / Analyze / Execute）
- [x] 45+ 工具注册
- [x] CLI 入口（`agent-harness run`）
- [x] OpenAI 兼容 API（`agent-harness serve` → :8788）
- [x] 熔断器（token/时间/无进展）
- [x] 全链路追踪
- [x] 评估套件
- [x] AIGC 视频管线（ComicAgent）
- [x] MCP 服务器

### P1 — 产品级完善（已完成 ✅）

- [x] **会话上下文** — X-Session-Id header，多轮对话保持 history
- [x] **真正的 SSE 流式** — Worker 进度逐步推送给用户
- [x] **Open WebUI 集成** — 注册为 custom OpenAI provider（`http://host.docker.internal:8788/v1`）
- [x] **知识库上传** — 文件 PDF/DOCX/TXT → RAG 向量索引 → Agent 自动检索注入上下文（v0.7.0）
- [ ] **API 统一** — 单端口 8788 已统一，需验证无残留

### P2 — 体验提升（待做）

- [ ] Worker 执行过程实时展示（类似 Cursor 的"正在思考"）
- [ ] 用户可中断正在执行的任务
- [ ] 任务历史记录 + 重跑
- [ ] 模型选择（本地 vs 云端，快速 vs 深度）
- [ ] 工具权限管理 UI

### P3 — 扩展（待做）

- [ ] 插件系统（第三方工具注册）
- [ ] 自定义 Worker 模板
- [ ] 团队协作（多用户 + 会话共享）
- [ ] 定时任务（cron 触发 Agent 执行）
- [ ] 一键启动脚本集成

---

## 八、版本历史

| 版本 | 日期 | 关键变更 |
|------|------|---------|
| v0.4.0 | 2026-07 | LangGraph 多 Agent 编排框架 |
| v0.5.0 | 2026-07 | **产品化** — 会话上下文 (X-Session-Id) + SSE 流式 + Open WebUI 集成 + 品牌「灵枢」 |
| v0.6.0 | 2026-07 | **质量提升** — Worker 输出修复 + Supervisor 路由优化 + 进度流式 |
| v0.7.0 | 2026-07 | **知识库系统** — RAG 向量存储 (rag_store.py) + 文件上传 API + Agent 自动检索注入 |
| v0.8.0 | 进行中 | — |

## 九、Open WebUI 集成方案

### 连接方式

Open WebUI 作为前端通过 OpenAI 兼容协议调用 Agent Harness API：

1. **Open WebUI 端**（管理员后台 → 外部连接）：
   - URL: `http://host.docker.internal:8788/v1`
   - Key: 任意（不验证）
   - 模型 ID: `agent-harness-multi`

2. **Agent Harness 端**（`agent-harness serve`）：
   - OpenAI 兼容 `/v1/chat/completions`
   - 支持模型选择：`agent-harness`（单 Agent）/ `agent-harness-multi`（多 Agent）
   - 消息格式：`messages[]` 数组含历史对话

### 会话上下文传递

```python
# api_fastapi.py 接收
POST /v1/chat/completions
{
  "model": "agent-harness-multi",
  "messages": [
    {"role": "user", "content": "帮我查一下今天的日期"},
    {"role": "assistant", "content": "今天是2025年7月6日"},
    {"role": "user", "content": "昨天呢？"}
  ]
}

# → Agent Harness 处理
# 1. 提取最后一条 user message 作为 request
# 2. 将历史对话注入 SupervisorState（作为 conversation_history）
# 3. 多 Agent 执行
# 4. 返回最终回复 + 更新后的 history
```

---

## 十、开发指南

### 10.1 项目结构

```
agent-harness/
├── AGENTS.md                    # ← 本文件，超级智能体定义
├── README.md                    # 项目 README
├── pyproject.toml               # 包配置
└── src/agent_harness/
    ├── agents/
    │   ├── supervisor.py        # Supervisor Agent（分析/分配/验收/重规划）
    │   ├── workers.py           # Worker Agents（各 Worker 子图）
    │   └── comic_agent.py       # AIGC 视频管线
    ├── pipeline/
    │   ├── state.py             # TypedDict 状态定义
    │   ├── circuit_breaker.py   # 三重熔断器
    │   ├── tracing.py           # 全链路追踪
    │   └── llm.py               # LLM 调用封装
    ├── tools/
    │   ├── __init__.py          # 导入即注册
    │   ├── registry.py          # TOOL_REGISTRY + call_tool + validate
    │   ├── misc.py              # think/file/code/RAG/stock
    │   ├── web.py               # search/fetch/scrape
    │   ├── desktop.py           # GUI/browser/chat/app_launch
    │   └── comfyui.py           # text2img/img2img/character
    ├── eval/
    │   ├── dataset.py           # 10 条评估任务
    │   ├── scorer.py            # 100 分评分标准
    │   └── runner.py            # 批量跑分
    ├── graph.py                 # 单 Agent 图
    ├── graph_multi.py           # 多 Agent 图
    ├── mcp_server.py            # MCP stdio 服务器
    ├── api_fastapi.py           # OpenAI 兼容 API
    └── run.py                   # CLI 入口
```

### 10.2 添加新工具

```python
# 1. 在 tools/ 下新建或编辑模块
def _tool_my_new_thing(param: str) -> str:
    """我的新工具实现"""
    ...

# 2. 注册到 TOOL_REGISTRY
from .registry import register_tool
register_tool("my_new_thing", _tool_my_new_thing, {
    "description": "我的新工具",
    "properties": {"param": "string"},
}, privilege="read-only")

# 3. 如果是新模块，在 tools/__init__.py 添加 import
from . import my_new_module
```

### 10.3 添加新 Worker

```python
# 1. agents/supervisor.py → WORKER_CAPABILITIES 添加
WORKER_CAPABILITIES["my_worker"] = {
    "description": "我的专用 Worker",
    "tools": ["my_new_thing", ...],
}

# 2. agents/workers.py → build_worker 已通用，无需修改
#    只需确保工具已注册到 TOOL_REGISTRY
```

### 10.4 开发规范

- **P0-P4 全部完成再测试** — 不要半路测试
- **每次变更必须更新 CHANGELOG.md** — 版本 + 变更说明
- **main 分支守护** — 合并前需通过 eval 套件
- **工具参数别名** — 在 registry.py 维护参数名兼容映射
- **不要硬编码路径** — 使用 config.py 的环境变量

---

## 八、启动与部署

### 开发模式

```bash
# 一键启动所有服务（llama-server + gateway + model-proxy + webui）
启动OpenClaw服务.bat

# 启动 Agent Harness API
agent-harness serve    # :8788

# 打开 Open WebUI
http://127.0.0.1:3000
# 在管理后台添加自定义 OpenAI 连接指向 :8788
```

### 配置环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `HARNESS_LLAMA_API` | `http://127.0.0.1:8081/v1/chat/completions` | 本地模型代理 |
| `HARNESS_DEEPSEEK_API` | `http://127.0.0.1:9000/v1/chat/completions` | DeepSeek Flash |
| `HARNESS_API_HOST` | `127.0.0.1` | API 监听地址 |
| `HARNESS_API_PORT` | `8788` | API 端口 |
| `HARNESS_SUPERVISOR_ROUNDS` | `3` | 最大重规划轮数 |
| `HARNESS_MAX_WORKERS` | `3` | 最大并行 Worker 数 |

### 部署检查清单

- [ ] llama-server / Ollama / DeepSeek 至少一个可用
- [ ] SearXNG 运行中（或搜索降级可用）
- [ ] Agent Harness API 可访问（`:8788/health` 返回 ok）
- [ ] Open WebUI 运行中，已添加 Agent Harness provider
- [ ] 测试一次完整流程：`agent-harness run "今天日期"`

---

## 九、当前状态与路线图

### v0.4.0 验证结果

| 维度 | 结果 |
|------|------|
| Eval 套件 | 6/8 pass (75%)，均分 61/100 |
| 单任务响应 | 3-5s（本地模型） |
| ComicAgent | 4 分镜 20s 视频脚本 10.3s |
| 工具注册 | 40+ 个 |

### 下一步

**Phase 1 — 产品化（当前 P1 清单）**
1. 重写 api_fastapi.py → 真流式 + 会话上下文
2. Open WebUI 集成配置
3. 启动脚本整合
4. API 端口统一

**Phase 2 — 体验**
1. Worker 执行过程可视化
2. 用户可中断 + 重跑
3. 任务历史

**Phase 3 — 生态**
1. 插件系统
2. 自定义 Worker
3. 多用户
4. 知识库

---

## 十、核心理念

> **Super Agent 不是更聪明的聊天机器人，而是一个能自主行动的智能体系统。**

- **用户说"做什么"**，而不是"怎么做"
- **Agent 决定怎么拆、谁来做、够不够**
- **做得不够就再来一轮，直到用户满意**
- **所有操作可追踪、可回放、可评估**

这就是"超级智能体"的真正含义。
