# Agent Harness

**LangGraph 多 Agent 工具编排框架** — Supervisor-Worker 架构，40 个工具，MCP 协议，OpenAI 兼容 API，AIGC 视频管线。

## ✅ 验证状态（v0.4.0）

| 项目 | 结果 | 时间 |
|------|:--:|------|
| **Eval 套件** | **6/8 pass (75%)**，均分 61/100 | 71.8s |
| **ComicAgent 脚本** | 4 分镜，20s 视频脚本 | 10.3s |
| **Multi-Agent 响应** | 单任务 3-5s | — |
| **LLM 后端** | qwen2.5-coder:14b @ 44 tok/s | WSL Ollama |

<details>
<summary>📊 Eval 详情</summary>

| 任务 | 评分 | 描述 |
|------|:--:|------|
| eval-001 | 70 ✅ | 日期查询 |
| eval-002 | 52 ❌ | 算术计算 |
| eval-003 | 65 ✅ | 文本总结 |
| eval-005 | 65 ✅ | 文件读取 |
| eval-006 | 60 ✅ | 代码执行 |
| eval-007 | 68 ✅ | 多工具协作 |
| eval-008 | 65 ✅ | 多语翻译 |
| eval-009 | 44 ❌ | Python 知识 |

</details>

<details>
<summary>🎬 ComicAgent Demo</summary>

```
用户输入: "猫娘在咖啡馆打工的一天，温馨治愈风格"
    ↓ 📝 Script (LLM, 10.3s)
    ↓ 🎨 Images (ComfyUI, on demand)
    ↓ 🔊 Voice (edge-tts, on demand)
    ↓ 🎬 Assembly (ffmpeg)

Title: 猫娘咖啡馆的一天
  Scene 1: 清晨，猫娘走进咖啡馆。
    Visual: morning sun, cozy café, catgirl with orange apron
  Scene 2: 她熟练地冲泡咖啡，和顾客聊天。
    Visual: brewing coffee, interacting with customers
  Scene 3: 午休时间，猫娘在窗边休息。
    Visual: lunch break, relaxing by window
  Scene 4: 晚上，她为顾客准备晚餐。
    Visual: evening, preparing dinner for customers
```

</details>

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

## 安装

```bash
pip install git+https://github.com/kisaragiy/agent-harness.git@v0.4.0
# 或本地开发
git clone https://github.com/kisaragiy/agent-harness.git
cd agent-harness && pip install -e .
```

## 快速开始

```bash
# 多 Agent 任务
agent-harness run "Python中计算2的10次方"

# 带追踪执行
agent-harness run --trace "搜索AI新闻并总结"

# 评估套件
agent-harness eval

# AIGC 视频脚本
agent-harness comic "猫娘的一天" --no-images --no-audio

# API 服务器
agent-harness serve     # http://localhost:8788

# MCP 工具服务器
agent-harness mcp       # stdio, 40 tools
```

## 环境要求

- Python ≥ 3.11
- LLM 后端（任选其一）：
  - 本地 llama.cpp（OpenAI 兼容 API）
  - WSL Ollama（通过 model_proxy 桥接）
  - 任何 OpenAI 兼容 API
- 可选：ComfyUI（图像生成）、ffmpeg（视频合成）、edge-tts（配音）

## 项目结构

```
src/agent_harness/
├── agents/
│   ├── supervisor.py       # Supervisor Agent
│   ├── workers.py          # Worker Agents (Search/Analyze/Execute)
│   └── comic_agent.py      # AIGC 短视频生产管线
├── pipeline/
│   ├── state.py            # 状态定义
│   ├── circuit_breaker.py  # 三重熔断器
│   ├── tracing.py          # 全链路追踪
│   └── llm.py              # LLM 调用
├── eval/
│   ├── dataset.py          # 10 条评估任务
│   ├── scorer.py           # 100 分评分标准
│   └── runner.py           # 批量跑分
├── tools/                  # 40 个工具
│   ├── desktop.py          # 桌面/微信/QQ/浏览器
│   ├── web.py              # 搜索/抓取
│   ├── comfyui.py          # AI 图像/视频
│   └── misc.py             # 文件/代码/RAG/股票
├── graph.py                # 单 Agent
├── graph_multi.py          # 多 Agent
├── mcp_server.py           # MCP stdio 服务器
├── api_fastapi.py          # OpenAI 兼容 API
└── run.py                  # CLI 入口
```

## License

MIT
