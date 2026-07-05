# Agent Harness

**LangGraph 多 Agent 工具编排框架** — Supervisor-Worker 架构，41+ 工具，MCP 协议，OpenAI 兼容 API。

## 架构

```
User Request → Supervisor (任务分析+分配)
                 ├→ Search Worker   (网页搜索·RAG·抓取)
                 ├→ Analyze Worker  (数据处理·代码执行·总结)
                 └→ Execute Worker  (桌面自动化·ComfyUI·浏览器)
                      ↓
                 Supervisor (收集·验收·重规划)
                      ↓
                 Finalizer (综合回复)
```

### 双模式

| 模式 | 命令 | 适用场景 |
|------|------|---------|
| **Multi-Agent**（默认） | `agent-harness run "..."` | 复杂任务，需多 Worker 协作 |
| **Single-Agent** | `agent-harness run --single "..."` | 简单任务，5 阶段管线 |

## 安装

```bash
# 基础安装
pip install -e .

# 含桌面自动化
pip install -e ".[desktop]"

# 全部依赖
pip install -e ".[all]"
```

## 配置

通过环境变量覆盖默认值：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `HARNESS_LLAMA_API` | `http://127.0.0.1:8080/v1/chat/completions` | 本地 LLM 端点 |
| `HARNESS_OLLAMA_API` | `http://172.18.9.126:11434/api/generate` | Ollama 端点 |
| `HARNESS_MAX_TOKENS` | `100000` | 单任务 Token 预算 |
| `HARNESS_SUPERVISOR_ROUNDS` | `3` | Supervisor 最大轮次 |

## 使用

```bash
# CLI 多 Agent 模式
agent-harness run "搜索 2026 年 AI Agent 最新进展，总结成 5 个要点"

# CLI 单 Agent 模式
agent-harness run --single "今天 A 股涨跌幅"

# FastAPI 服务器
agent-harness serve
# → http://localhost:8788/v1/chat/completions

# MCP stdio 服务器（供 Hermes/Claude Desktop 调用）
agent-harness mcp
```

## 项目结构

```
src/agent_harness/
├── agents/
│   ├── supervisor.py    # Supervisor Agent（任务分解+调度）
│   └── workers.py       # Worker Agents（Search/Analyze/Execute）
├── pipeline/
│   ├── state.py         # 状态定义
│   ├── circuit_breaker.py  # 三重熔断器
│   └── llm.py           # LLM 调用
├── tools/               # 41+ 工具
│   ├── desktop.py       # 桌面/微信/QQ/浏览器自动化
│   ├── web.py           # 搜索/抓取
│   ├── comfyui.py       # AI 图像/视频生成
│   └── misc.py          # 文件/代码/金融/RAG
├── graph.py             # 单 Agent 图
├── graph_multi.py       # 多 Agent 图
├── mcp_server.py        # MCP stdio 服务器
├── api_fastapi.py       # OpenAI 兼容 API
└── run.py               # CLI 入口
```

## License

MIT
