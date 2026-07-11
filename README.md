<div align="center">

# 🧠 灵枢 (LingShu Agent)

**AI 调研助手 · LLM + 工具编排智能体平台**

[![Python](https://img.shields.io/badge/Python-3.11%2B-2b5b84?logo=python&logoColor=white)](https://python.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2%2B-1c3d5a?logo=langchain)](https://langchain-ai.github.io/langgraph/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-MIT-f5de17)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/kisaragiy/lingShu?style=flat&logo=github)](https://github.com/kisaragiy/lingShu)
[![CI](https://img.shields.io/badge/CI-passing-22c55e?logo=githubactions)](https://github.com/kisaragiy/lingShu/actions)

> **灵枢者，智之枢也。** Supervisor-Worker 多智能体架构，搜索→分析→报告全链路自动化。
>
> 👉 [🎧 在线体验客服 Demo](https://github.com/kisaragiy/lingShu#-cs-demo-%E5%AE%A2%E6%9C%8D%E6%99%BA%E8%83%BD%E4%BD%93%E9%AA%8C)
>
> 📦 [下载 CS Demo 独立 exe](https://github.com/kisaragiy/lingShu/releases) — 双击即用，无需配置

</div>

---

## 📸 先看效果

### 🎧 智能客服 Demo

<p align="center">
  <img src="docs/screenshots/cs-demo-chat.png" alt="CS Demo 聊天界面" width="700">
  <br>
  <em>LLM 驱动客服：商品推荐、订单查询、物流追踪、优惠券 —— 9 种场景实时对话</em>
</p>

<p align="center">
  <img src="docs/screenshots/cs-demo-stream.png" alt="CS Demo SSE 流式回复" width="700">
  <br>
  <em>SSE 流式逐 token 回复：思考过程可视化 + 打字机效果</em>
</p>

### 🖥️ 灵枢主界面

<p align="center">
  <img src="docs/screenshots/lingShu-dashboard.png" alt="灵枢主界面" width="700">
  <br>
  <em>搜索→分析→报告全链路，支持多 Agent 并行执行</em>
</p>

---

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    用户请求 (HTTP/SSE)                     │
└────────────────┬────────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────────┐
│  Supervisor (LLM 驱动)  — 意图分析 · 任务拆解 · 结果验收   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │  Search      │  │  Analyze     │  │  Execute     │   │
│  │  Worker      │  │  Worker      │  │  Worker      │   │
│  │ ──────────   │  │ ──────────   │  │ ──────────   │   │
│  │ 网页搜索     │  │ 代码执行     │  │ 桌面自动化   │   │
│  │ RAG 检索     │  │ 数据分析     │  │ 浏览器控制   │   │
│  │ 网页抓取     │  │ 总结推理     │  │ ComfyUI 生图 │   │
│  └──────────────┘  └──────────────┘  └──────────────┘   │
│                            ↻ Replan 多轮迭代              │
└────────────────┬────────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────────┐
│  Finalizer — 综合回复 (自然语言 + 工具结果)               │
└─────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────┐
│  输出:  HTML 格式报告 · Markdown 回复 · 图片 · 文件      │
└─────────────────────────────────────────────────────────┘
```

### 模块职责

| 模块 | 能力 |
|------|------|
| **Supervisor** | 任务分析 → 拆解 → 分配 → 验收 → 重规划（最多 8 轮迭代） |
| **Search Worker** | 网页搜索 (Bing/DuckDuckGo/SearXNG)、RAG 检索、页面抓取 |
| **Analyze Worker** | Python 代码执行、数据分析、多步推理、报告生成 |
| **Execute Worker** | 桌面 GUI 自动化、浏览器控制、ComfyUI 图像/视频生成 |

---

## ⚡ 快速开始

### 方式 1：下载 exe（推荐）

从 [Releases](https://github.com/kisaragiy/lingShu/releases) 下载 `lingShu.exe`，双击启动即可体验完整功能。

### 方式 2：在线体验客服 Demo

```bash
# 一键启动 CS Demo
git clone https://github.com/kisaragiy/lingShu.git
cd lingShu
pip install -e .
python run_cs_demo.py
# → 浏览器自动打开 http://127.0.0.1:8788/cs-demo
```

### 方式 3：源码运行

```bash
pip install git+https://github.com/kisaragiy/lingShu.git

# 启动 Web 服务
agent-harness serve    # → http://127.0.0.1:8788

# 或直接运行单次任务
agent-harness run "用 Python 分析这份销售数据"
agent-harness run --trace "搜索 AI Agent 最新进展"  # 带执行追踪
agent-harness eval                                    # 运行评估套件
```

---

## ✨ 核心特性

### 🎯 AI 调研助手

| 特性 | 描述 |
|------|------|
| **多 Agent 编排** | Supervisor-Worker 架构，Search/Analyze/Execute 三 Worker 并行执行，支持多轮 Replan |
| **全链路自动化** | 搜索 → 分析 → 排版精美的 HTML 报告（A4 打印优化），一条指令完成 |
| **41+ 工具** | 搜索/代码/桌面/浏览器/绘画/RAG/股票/消息 6 大类 |
| **三重熔断** | Token 预算 / 超时 / 无进展 自动熔断保护 |
| **全链路追踪** | 执行耗时、Token 消耗、工具调用可视化 |

### 🤖 LLM 集成

| 能力 | 说明 |
|------|------|
| **OpenAI 兼容 API** | `/v1/chat/completions` + `/v1/models`，支持 SSE 流式 |
| **三模型群回退** | 本地 llama.cpp → WSL Ollama → DeepSeek Flash API 自动降级 |
| **Open WebUI 集成** | 添加自定义 OpenAI 连接即可使用 |
| **MCP 协议** | JSON-RPC 2.0 标准暴露工具，外部 Agent 可调用 |

### 🎧 智能客服 Demo

| 场景 | 说明 |
|------|------|
| 📦 查订单 | 订单号/手机号/姓名查询，12 条真实感 Mock 订单 |
| 🚚 查物流 | 配送进度追踪 + 时效估算 |
| 🛒 售前咨询 | 商品目录查询、对比、分期方案（10 款热销商品） |
| 💰 查优惠 | 优惠券/促销活动实时查询 |
| 📍 改地址 | 收货地址修改（验证规则） |
| 🔄 退换货 | 售后工单创建 |
| 👤 转人工 | 排队模拟 + 热线 |

> Demo 特点：SSE 流式回复、思考过程可视化、打字机光标效果、暗色/亮色模式自适应

---

## 🛠️ 技术栈

| 领域 | 技术 |
|------|------|
| **编排框架** | LangGraph · FastAPI · Uvicorn |
| **推理引擎** | Qwen3.6-35B (llama.cpp) · DeepSeek Flash · Ollama 模型群 |
| **前端** | 原生 HTML/CSS/JS · 暗色模式 · SSE 流式 |
| **工具层** | Playwright · PyAutoGUI · ComfyUI REST API · ChromaDB · SearXNG · Edge-TTS |
| **数据** | ChromaDB · SQLite · Pandas |
| **部署** | Docker · PyInstaller exe · Open WebUI · WSL |

---

## 📁 项目结构

```
src/agent_harness/
├── agents/
│   ├── supervisor.py       # Supervisor Agent（分析/分配/验收/重规划）
│   ├── workers.py          # Search/Analyze/Execute 三 Worker
│   ├── cs_agent.py         # 客服 Agent（规则引擎 + LLM 驱动）
│   └── comic_agent.py      # AIGC 视频管线
├── pipeline/
│   ├── state.py            # TypedDict 状态定义
│   ├── llm.py              # LLM 调用封装（多模型回退）
│   ├── circuit_breaker.py  # 三重熔断器
│   └── tracing.py          # 全链路追踪
├── tools/
│   ├── web.py              # 搜索/抓取/浏览
│   ├── desktop.py          # GUI/浏览器/消息
│   ├── comfyui.py          # 图像/视频生成
│   ├── customer_service.py # 客服 Mock 数据 + 工具
│   └── misc.py             # 文件/代码/RAG/股票
├── static/
│   ├── index.html          # 主应用前端
│   ├── cs-demo.html        # 独立客服 Demo 页面
│   └── css/lingShu.css     # 主应用样式
├── eval/                   # 评估套件（10 条回归测试）
├── graph.py                # 单 Agent 管线
├── graph_multi.py          # 多 Agent 管线
├── api_fastapi.py          # OpenAI 兼容 API + SSE
└── mcp_server.py           # MCP 协议服务器
```

---

## 🔌 Open WebUI 集成

在 Open WebUI 管理后台添加自定义连接：

| 字段 | 值 |
|------|-----|
| URL | `http://host.docker.internal:8788/v1` |
| Key | 留空 |
| 模型 | `agent-harness-multi` |

---

## 📊 评估

```bash
agent-harness eval          # 运行 10 条回归测试
```

覆盖：搜索、RAG、代码执行、桌面操作、多轮对话、客服场景等。

---

## 📄 License

MIT — 详见 [LICENSE](LICENSE)

---

<p align="center">
  <sub>Made with ❤️ by <a href="https://github.com/kisaragiy">kisaragiy</a> · 广州 · 2025–2026</sub>
</p>
