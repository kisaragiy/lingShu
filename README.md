<div align="center">

# 🧠 灵枢 (LingShu)

**AI 调研助手 — 多 Agent 编排 · 搜索分析报告全链路自动化**

[![Python](https://img.shields.io/badge/Python-3.11%2B-2b5b84?logo=python&logoColor=white)](https://python.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2%2B-1c3d5a?logo=langchain)](https://langchain-ai.github.io/langgraph/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-MIT-f5de17)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/kisaragiy/lingShu?style=flat&logo=github)](https://github.com/kisaragiy/lingShu)

> **灵枢者，智之枢也。** Supervisor-Worker 多智能体架构，搜索→分析→报告全链路自动化。
>
> 本地优先、开箱即用。不是聊天机器人，是帮你干活出报告的 AI。

</div>

---

## 🖥️ 效果

<p align="center">
  <img src="docs/screenshots/lingShu-dashboard.png" alt="灵枢主界面" width="700">
  <br>
  <em>搜索→分析→报告全链路，多 Agent 并行执行</em>
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

### 源码运行

```bash
git clone https://github.com/kisaragiy/lingShu.git
cd lingShu
pip install -e .

# 启动 Web 服务
python -m agent_harness.main
# → http://127.0.0.1:8788
```

### 使用 pip 安装

```bash
pip install git+https://github.com/kisaragiy/lingShu.git

# 启动
agent-harness serve
# → http://127.0.0.1:8788
```

> 需要自行配置 LLM 后端（OpenAI 兼容 API 即可，详见下方文档）。

---

## ✨ 核心特性

### 🤖 LLM 集成

| 能力 | 说明 |
|------|------|
| **多 Agent 编排** | Supervisor-Worker 架构，Search/Analyze/Execute 三 Worker 并行执行，支持多轮 Replan |
| **全链路自动化** | 搜索 → 分析 → 排版精美的 HTML 报告（A4 打印优化），一条指令完成 |
| **OpenAI 兼容 API** | `/v1/chat/completions` + `/v1/models`，支持 SSE 流式 |
| **三模型群回退** | 本地 llama.cpp → WSL Ollama → DeepSeek Flash API 自动降级 |
| **45+ 工具** | 搜索/代码/桌面/浏览器/绘画/RAG/股票 6 大类 |
| **三重熔断** | Token 预算 / 超时 / 无进展 自动熔断保护 |
| **RAG 知识库** | 上传 PDF/DOCX/TXT → 向量搜索 + BM25 关键词降级 |
| **JWT 认证** | Access+Refresh 双 token + RBAC 权限 + 审计日志 |
| **MCP 协议** | JSON-RPC 2.0 标准暴露工具，外部 Agent 可调用 |

---

## 🛠️ 技术栈

| 领域 | 技术 |
|------|------|
| **编排框架** | LangGraph · FastAPI · Uvicorn |
| **推理引擎** | Qwen3.6-35B (llama.cpp) · DeepSeek Flash · Ollama 模型群 |
| **前端** | pywebview (WebView2) · 原生 HTML/CSS/JS · SSE 流式 |
| **工具层** | Playwright · PyAutoGUI · ComfyUI REST API · SearXNG |
| **安全** | JWT (HMAC-SHA256) · RBAC · CSP · 速率限制 · 审计日志 |
| **数据** | SQLite · JSON 文件 · NPY 向量索引 |
| **部署** | PyInstaller exe · Docker · Open WebUI |

---

## 📁 项目结构

```
src/agent_harness/
├── core/                        # 共享基础设施
│   ├── agents/                  # Supervisor / Worker 编排
│   ├── tools/                   # 搜索、RAG、桌面自动化、注册中心
│   ├── pipeline/                # LLM 调用、熔断器、会话存储、状态
│   ├── auth/                    # JWT 认证、RBAC、API 安全
│   └── graph/                   # 单 Agent / 多 Agent 图定义
├── apps/
│   └── research/                # 调研助手（主应用）
│       ├── api.py               # 调研 API 端点
│       ├── run.py               # 启动脚本
│       ├── static/              # 前端 HTML/CSS/JS
│       ├── pipeline/            # 报告模板、报告存储
│       └── tools/               # ComfyUI 绘画
├── main.py                      # 入口 + 路由
├── plugin_loader.py             # 插件系统
└── api_fastapi.py               # OpenAI 兼容 API 入口
```

> 另有独立 Demo: [cs-agent-demo](https://github.com/kisaragiy/cs-agent-demo) — LLM 驱动客服系统（基于 lingShu-core）

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
