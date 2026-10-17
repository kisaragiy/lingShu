<div align="center">

# 🧠 灵枢 (LingShu)

**独立的多 Agent AI 应用工程实践 — Supervisor-Worker 编排 · 搜索分析报告全链路 · 防复现知识管道 · Agent 可靠性工程**

[![Python](https://img.shields.io/badge/Python-3.11%2B-2b5b84?logo=python&logoColor=white)](https://python.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2%2B-1c3d5a?logo=langchain)](https://langchain-ai.github.io/langgraph/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-MIT-f5de17)](LICENSE)
[![tests](https://img.shields.io/badge/tests-769%20passed-brightgreen)]()
[![GitHub stars](https://img.shields.io/github/stars/kisaragiy/lingShu?style=flat&logo=github)](https://github.com/kisaragiy/lingShu)

> **灵枢者，智之枢也。** 以 Supervisor 为枢，Worker 为四肢，调度万端。
> 本地优先、开箱即用。多 Agent 编排 + 知识反哺的完整 AI 应用落地。

</div>

---

## 🖥️ 效果

<p align="center">
  <img src="docs/screenshots/lingShu-orchestrate.png" alt="灵枢 Agent 编排拓扑界面" width="700">
  <br>
  <em>Agent 编排拓扑（Supervisor-Worker · LangGraph 状态机 · 实时工具注册表）</em>
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

> 架构取舍：三个 Worker 各自独立 context，防止并行任务共享同一 LLM 上下文时互相串扰。这是处理并行 Agent 时最常被低估的一个点。

### Harness 工程分层（ETCLOVG）

除模型权重外的一切工程（harness），灵枢按业界七层分类法组织——`core/` 67 个模块**七层全覆盖，无空白层**：

| 层 | 灵枢落位 |
|---|---|
| **E**xecution | 安全模式（沙箱优先 / 全权审计）+ 写前自动备份 + 危险操作确认矩阵 + 幂等 key |
| **T**ool | 工具注册表（37 个工具六大类）+ MCP 协议暴露 + 插件加载 + MCP 健康治理 |
| **C**ontext | Token 预算 + RAG 检索（向量 → BM25 降级）+ 会话持久化 |
| **L**ifecycle-Orchestration ★ | Supervisor-Worker **真并行 fan-out**（只读并行 / 副作用串行）+ 三重熔断 + 降级链 + provider 对冲 + 任务队列 + 可取消 |
| **O**bservability | run 轨迹 + API 审计 + Prometheus 指标 + 轨迹冻结回放 + 失败归因 |
| **V**erification | 判据隔离 + judge 冻结 + 在线采样 + 可复现信封 + 溯源准入 |
| **G**overnance | JWT / RBAC + 准入闸 + 配置 / Prompt 版本戳 |

> 分类法来源：《Agent Harness Engineering: A Survey》(2026)。完整映射、覆盖度分析与演进方向见 [`docs/architecture-harness-layers.md`](docs/architecture-harness-layers.md)。

---

## ⚡ 快速开始

### 源码运行

```bash
git clone https://github.com/kisaragiy/lingShu.git
cd lingShu
cp .env.example .env          # ← 先拷配置模板（不拷会因"未配置 LLM 后端"直接退出）
pip install -e ".[dev]"       # 只跑服务可省去 [dev]（pytest/ruff/mypy 在里面）

# 启动 Web 服务
python -m agent_harness.main
# → http://127.0.0.1:8788
```

### 跑测试

```bash
pytest                        # 默认口径：不跑需要外部服务的集成用例
pytest -m integration         # 集成用例（需本地 cs_demo :8788 / llama.cpp :8080）
pytest --cov=src/agent_harness --cov-report=term
```

### 使用 pip 安装

```bash
pip install git+https://github.com/kisaragiy/lingShu.git

# 启动
agent-harness serve
# → http://127.0.0.1:8788
```

> 需要自行配置 LLM 后端（OpenAI 兼容 API 即可，详见下方文档）。`cp .env.example .env` 后可起服务；
> 后端未就绪时接口会明确返回"后端不可用"，不会静默降级成假回答。

---

## ✨ 核心特性

### 🤖 LLM 集成

| 能力 | 说明 |
|------|------|
| **多 Agent 编排** | Supervisor-Worker 架构，Search/Analyze/Execute 三 Worker 并行执行，支持多轮 Replan |
| **全链路自动化** | 搜索 → 分析 → 排版精美的 HTML 报告（A4 打印优化），一条指令完成 |
| **OpenAI 兼容 API** | `/v1/chat/completions` + `/v1/models`，支持 SSE 流式 |
| **三模型群回退** | 本地 llama.cpp → WSL Ollama → DeepSeek Flash API 自动降级 |
| **37 个工具** | 搜索/代码/桌面/浏览器/绘画/RAG/股票 6 大类（实测注册数） |
| **防复现知识管道** | 把高频技术/求职知识沉淀为结构化规则库（35 条）· 中文 2-gram 检索 · 每日笔记自动抽候选 · 38 测试通过 |
| **三重熔断** | Token 预算 / 超时 / 无进展 自动熔断保护 |
| **RAG 知识库** | 上传 PDF/DOCX/TXT → 向量搜索 + BM25 关键词降级 |
| **JWT 认证** | Access+Refresh 双 token + RBAC 权限 + 审计日志 |
| **MCP 协议** | JSON-RPC 2.0 标准暴露工具，外部 Agent 可调用 |

> 工具数量、测试用例、规则库条目均以实测注册数为准，可在仓库中直接核对。

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

> 灵枢的 core 层解耦场景，apps/ 下已有智能客服、知识库问答、调研助手、模板四个独立场景。

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
agent-harness eval          # 运行回归测试
```

覆盖：搜索、RAG、代码执行、桌面操作、多轮对话、客服场景等。

> 项目共 769 个测试用例通过（`pytest`，2026-10-18 实测 **769 passed / 2 skipped**，收集 779、8 个 integration 默认 deselect），覆盖工具链路、鉴权与安全护栏、架构边界与数据根隔离、HTTP 契约、可观测性、RAG、成本控制、推理缓存等模块；其中 8 个集成用例需本地起服务（`cs_demo :8788` / `llama.cpp :8080`），默认不收集，用 `pytest -m integration` 显式运行。测试数量以仓库实测为准。

---

## 📄 License

MIT — 详见 [LICENSE](LICENSE)

---

<p align="center">
  <sub>Made with ❤️ by <a href="https://github.com/kisaragiy">kisaragiy</a> · 广州 · 2025–2026</sub>
</p>
