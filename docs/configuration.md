# 配置

灵枢的配置有三个来源，优先级从高到低：

1. **`config.json`**（设置页写的运行时配置，路径 `~/.agent-harness/config.json`）—— 设置页改完立即生效
2. **环境变量 / `.env`**（进程启动时读取）
3. **代码默认值**

> 为什么 `config.json` 优先：环境变量对已 import 的模块是快照，设置页填的地址必须立刻生效（ADR-003）。

---

## 环境变量

### 服务

| 变量 | 说明 | 默认 |
|---|---|---|
| `HARNESS_API_HOST` | 监听地址 | `127.0.0.1`（绑 `0.0.0.0` 会告警：API 暴露到局域网） |
| `HARNESS_API_PORT` | 监听端口 | `8788` |
| `HARNESS_WORKERS` | uvicorn worker 数 | `1`（**传入 >1 会被强制降为 1**，会话/metrics/取消/缓存都是进程内状态） |

### 模型后端

| 变量 | 说明 | 默认 |
|---|---|---|
| `HARNESS_OLLAMA_API` | Ollama API 地址 | `http://127.0.0.1:11434/api/embed` |
| `HARNESS_LLAMA_API` | llama.cpp API 地址 | 空 |
| `HARNESS_DEEPSEEK_API` | DeepSeek API 地址 | 空 |
| `HARNESS_CLOUD_API` | API 聚合地址 | 空 |
| `HARNESS_CLOUD_KEY` | 云端 API Key | 空 |
| `HARNESS_MODEL_LLAMA` | 默认模型名 | 见 `config.json`（设置页可改） |
| `HARNESS_MODEL_DEEPSEEK` | DeepSeek 模型名 | `deepseek-v4-pro` |

### 编排行为

| 变量 | 说明 | 默认 |
|---|---|---|
| `HARNESS_MAX_RETRIES` | 单步重试次数 | `2` |
| `HARNESS_MAX_ITERATIONS` | 单 Agent 最大迭代 | `10` |
| `HARNESS_MAX_TOKENS` | 单次请求最大 token | `100000` |
| `HARNESS_MAX_TIME` | 单任务最大时长（秒） | `600` |
| `HARNESS_MAX_NO_PROGRESS` | 连续无进展多少次放弃 | `5` |
| `HARNESS_MAX_WORKERS` | 并行 Worker 数 | `3` |
| `HARNESS_SUPERVISOR_ROUNDS` | Supervisor 最多轮次 | `3` |
| `HARNESS_LLM_THINKING` | 思考模式 `off` / `on` | `off` |

### 认证与权限

| 变量 | 说明 | 默认 |
|---|---|---|
| `HARNESS_DISABLE_AUTH` | 关闭认证（**仅本地演示，不得暴露公网**） | `False` |
| `HARNESS_API_KEY_ROLE` | `X-API-Key` 持有的角色：`admin` / `user` | `admin`（非法值降级为 `user` 并告警） |
| `HARNESS_DEMO_ALLOW_WRITE_TOOLS` | `DISABLE_AUTH=1` 时允许的写类工具（逗号分隔） | 空 = 全部拒绝 |

### 知识库（RAG）

| 变量 | 说明 | 默认 |
|---|---|---|
| `HARNESS_RAG_DIR` | collection 存储目录 | 仓库根下 `rag_data/` |
| `HARNESS_EMBED_MODEL` | 嵌入模型 | `nomic-embed-text` |
| `HARNESS_EMBED_DIM` | 向量维度 | `768` |

### 搜索与密钥

| 变量 | 说明 |
|---|---|
| `DEEPSEEK_API_KEY` / `HARNESS_DEEPSEEK_API` | DeepSeek 密钥（同时用于 `/v1/tools/search`） |
| `TAVILY_API_KEY` | Tavily 搜索密钥 |

密钥的完整优先级：`config.json` 的密文字段（`search.api_key_enc` / `search.tavily_key_enc`）→ `llm.api_key` → 环境变量。**界面只显示「已配置 / 未配置」，不回显任何片段**。

### 目录

| 变量 | 说明 |
|---|---|
| `HARNESS_MEMORY_DIR` | 记忆目录（空 = 默认位置） |
| `HARNESS_SKILLS_DIR` | 技能目录（空 = 默认位置） |

---

## 运行时配置 `config.json`

设置页（☰ → 设置）写入，路径 `~/.agent-harness/config.json`。主要字段：

```json
{
  "api_url": "http://127.0.0.1:11434/v1/chat/completions",
  "model": "<模型名>",
  "api_key": "",
  "search": {
    "api_key_enc": "<密文>",
    "tavily_key_enc": "<密文>"
  }
}
```

- 任何 OpenAI 兼容端点都能填（本地 ollama / llama.cpp / DeepSeek / OpenAI）
- 密钥以密文落盘（keystore），配置文件里看不到明文
- 写入走临时文件替换，避免半截文件

---

## `.env`

```bash
cp .env.example .env
```

`.env.example` 里只有三个变量（地址与端口），**不含任何密钥** —— 密钥请在设置页填，或自行加进 `.env`（环境变量优先级最低）。

> 没有 YAML 配置文件，也不支持 `agent-harness serve --config`。`serve` 只接受 `-h`。

---

> 📖 [上一步：CS Demo](cs-demo.md)
