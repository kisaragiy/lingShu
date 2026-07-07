# 灵枢 (LingShu Agent)

> **灵枢者，智之枢也。** 以 Supervisor 为枢，Worker 为四肢，调度万端。
> 基于 LangGraph Supervisor-Worker 架构的多 Agent 超级智能体平台。

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://python.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2%2B-green)](https://langchain-ai.github.io/langgraph/)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-kisaragiy%2FlingShu-181717?logo=github)](https://github.com/kisaragiy/lingShu)

---

## 🚀 快速开始

### 下载即用（推荐）

从 Releases 下载 `lingShu.exe`，双击启动，弹出独立窗口。

> 首次启动会自动检测可用端口并启动服务，稍等片刻即可使用。

### 从源码运行

```bash
# 安装（需要 Python 3.11+）
pip install pywebview  # 独立窗口支持
pip install git+https://github.com/kisaragiy/lingShu.git

# 启动（独立窗口）
python scripts/lingShu_launcher.py

# 或直接启动服务（浏览器访问）
agent-harness serve

# 构建 .exe
pip install pyinstaller pywebview
python scripts/build_exe.py
```

### 首次使用

1. 打开浏览器 → `http://127.0.0.1:8788`
2. Setup Wizard 自动引导：路径检测 → LLM 配置 → 环境检查
3. 进入 Dashboard → 在「💬 对话」标签页开始聊天
4. 多轮对话自动保持上下文，长任务可取消

## 🏗️ 架构

```
用户请求 → Supervisor (任务分析·分配·验收·重规划)
            ├→ Search Worker   (网页搜索·RAG·抓取)
            ├→ Analyze Worker  (代码执行·数据分析·总结)
            └→ Execute Worker  (桌面自动化·ComfyUI·浏览器)
                 ↓
           Supervisor (结果收集·多轮迭代)
                 ↓
           Finalizer (综合回复)
```

## ✨ 特性

- **多 Agent 编排** — Supervisor-Worker 架构，Worker 并行执行，支持多轮 Replan 迭代
- **41 个工具** — 搜索/代码/桌面/浏览器/绘画/RAG/股票/消息 6 大类别
- **OpenAI 兼容 API** — `/v1/chat/completions` + `/v1/models`，支持 SSE 流式
- **会话上下文** — X-Session-Id 自动追踪 2h 对话历史，多轮不失忆
- **Open WebUI 集成** — Docker 容器化前端，自然语言→Agent→结果全链路闭环
- **MCP 协议** — JSON-RPC 2.0 标准暴露工具，外部 Agent 原生调用
- **本地推理** — llama.cpp + Ollama WSL + DeepSeek Flash 三模型群，Model Proxy 路由
- **全链路追踪** — 执行耗时/Tokens/工具调用统计
- **熔断保护** — Token 预算 / 超时 / 无进展 三重熔断
- **AIGC 管线** — ComfyUI + edge-tts + ffmpeg 一句话生成短视频
- **评估套件** — 10 条回归测试任务，60+ 分及格线

## 🚀 快速开始

```bash
# 安装
pip install git+https://github.com/kisaragiy/lingShu.git

# 启动 API 服务
agent-harness serve    # → http://127.0.0.1:8788

# 或直接运行任务
agent-harness run "用 Python 计算 2 的 10 次方"
agent-harness run --trace "搜索 AI Agent 最新进展"  # 带追踪
agent-harness eval                                    # 评估套件
agent-harness comic "猫娘在咖啡馆打工的一天" --no-images  # AIGC 脚本
```

## 🔌 Open WebUI 集成

在 Open WebUI 管理后台添加自定义 OpenAI 连接：

| 字段 | 值 |
|------|-----|
| URL | `http://host.docker.internal:8788/v1` |
| Key | 留空 |
| 模型 | `agent-harness-multi` |

## 🛠️ 技术栈

- **编排框架**: LangGraph · FastAPI · Uvicorn
- **推理引擎**: Qwen3.6-35B (llama.cpp) · DeepSeek Flash · Ollama 模型群
- **工具层**: Playwright · PyAutoGUI · ComfyUI REST API · ChromaDB · SearXNG
- **部署**: Docker · Open WebUI · WSL · Windows 原生

## 📁 项目结构

```
src/agent_harness/
├── agents/
│   ├── supervisor.py       # Supervisor Agent（分析/分配/验收/重规划）
│   ├── workers.py          # Worker Agents（Search/Analyze/Execute）
│   └── comic_agent.py      # AIGC 视频管线
├── pipeline/
│   ├── state.py            # TypedDict 状态定义
│   ├── llm.py              # LLM 调用封装
│   ├── circuit_breaker.py  # 三重熔断器
│   └── tracing.py          # 全链路追踪
├── tools/                  # 41 个工具（注册即用）
│   ├── web.py              # 搜索/抓取/浏览
│   ├── desktop.py          # GUI/浏览器/消息/启动
│   ├── comfyui.py          # 图像/视频生成
│   └── misc.py             # 文件/代码/RAG/股票
├── eval/                   # 评估套件
├── graph.py                # 单 Agent 管线
├── graph_multi.py          # 多 Agent 管线
├── api_fastapi.py          # OpenAI 兼容 API（会话+流式）
├── mcp_server.py           # MCP 服务器
└── run.py                  # CLI 入口
```

## 📄 License

MIT
