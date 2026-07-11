# 配置说明

Agent Harness 通过环境变量和配置文件进行灵活配置。

## 环境变量

所有配置项均可通过环境变量设置，推荐使用 `.env` 文件管理。

### 核心配置

| 变量名 | 说明 | 默认值 | 是否必填 |
|--------|------|--------|----------|
| `AGENT_HARNESS_PORT` | 服务监听端口 | `8000` | 否 |
| `AGENT_HARNESS_HOST` | 服务监听地址 | `0.0.0.0` | 否 |
| `AGENT_HARNESS_WORKERS` | 工作进程数 | `1` | 否 |
| `LOG_LEVEL` | 日志级别 | `INFO` | 否 |
| `LOG_FILE` | 日志文件路径 | `logs/agent-harness.log` | 否 |
| `SECRET_KEY` | 服务端密钥（用于 JWT 加密） | `change-me` | 是（生产环境） |
| `AUTH_TOKEN` | API 认证 Token | 空（不启用认证） | 否 |

### LLM 提供商

至少需要配置一个 LLM 提供商的 API 密钥。

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `OPENAI_API_KEY` | OpenAI API 密钥 | - |
| `OPENAI_BASE_URL` | OpenAI API 基础 URL | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | 默认 OpenAI 模型 | `gpt-4o` |
| `ANTHROPIC_API_KEY` | Anthropic Claude API 密钥 | - |
| `ANTHROPIC_MODEL` | 默认 Claude 模型 | `claude-3-opus-20240229` |
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | - |
| `DEEPSEEK_BASE_URL` | DeepSeek API 基础 URL | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 默认 DeepSeek 模型 | `deepseek-chat` |
| `LOCAL_MODEL_URL` | 本地模型 API 地址 | - |
| `LOCAL_MODEL_NAME` | 本地模型名称 | - |

### 知识库配置

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `VECTOR_STORE_TYPE` | 向量数据库类型 | `chroma` |
| `VECTOR_STORE_PATH` | 向量数据库存储路径 | `./data/vector_store` |
| `EMBEDDING_MODEL` | 嵌入模型名称 | `BAAI/bge-small-zh-v1.5` |
| `EMBEDDING_DIMENSION` | 嵌入向量维度 | `512` |
| `CHUNK_SIZE` | 文档分块大小 | `512` |
| `CHUNK_OVERLAP` | 文档分块重叠大小 | `64` |
| `TOP_K_RESULTS` | 检索返回的最大结果数 | `5` |
| `RERANK_ENABLED` | 是否启用重排序 | `false` |

### 数据库配置

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `DB_TYPE` | 数据库类型 | `sqlite` |
| `DB_PATH` | SQLite 数据库路径 | `./data/agent-harness.db` |
| `DB_HOST` | 数据库主机（PostgreSQL/MySQL） | `localhost` |
| `DB_PORT` | 数据库端口 | `5432` |
| `DB_NAME` | 数据库名称 | `agent_harness` |
| `DB_USER` | 数据库用户 | `postgres` |
| `DB_PASSWORD` | 数据库密码 | - |

### 浏览器配置

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `BROWSER_HEADLESS` | 是否为无头模式 | `true` |
| `BROWSER_TIMEOUT` | 浏览器操作超时（秒） | `30` |
| `BROWSER_VIEWPORT_WIDTH` | 视口宽度 | `1280` |
| `BROWSER_VIEWPORT_HEIGHT` | 视口高度 | `720` |

### 安全与沙箱

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `SANDBOX_ENABLED` | 是否启用沙箱 | `true` |
| `MAX_TOOL_CALLS` | 单次请求最大工具调用次数 | `20` |
| `MAX_EXECUTION_TIME` | 单次请求最大执行时间（秒） | `120` |
| `MAX_OUTPUT_SIZE` | 工具输出最大字符数 | `100000` |
| `ALLOWED_DOMAINS` | 允许访问的域名列表 | `*` |
| `RATE_LIMIT_ENABLED` | 是否启用限流 | `true` |
| `RATE_LIMIT_PER_MIN` | 每分钟最大请求数 | `60` |

### 评测配置

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `EVAL_OUTPUT_DIR` | 评测报告输出目录 | `./eval_reports` |
| `EVAL_DEFAULT_MODEL` | 评测默认模型 | `gpt-4o` |
| `EVAL_JUDGE_MODEL` | 评测裁判模型 | `gpt-4o` |
| `EVAL_MAX_CONCURRENCY` | 最大并行评测数 | `4` |

## 配置文件

除环境变量外，也支持 YAML 配置文件：

```yaml
# config.yaml
server:
  host: 0.0.0.0
  port: 8000
  workers: 2

llm:
  openai:
    model: gpt-4o
    temperature: 0.7
  deepseek:
    model: deepseek-chat
    temperature: 0.7

knowledge:
  vector_store: chroma
  embedding_model: BAAI/bge-small-zh-v1.5
  chunk_size: 512

security:
  sandbox: true
  rate_limit: 60
  max_tool_calls: 20
```

使用配置文件启动：

```bash
agent-harness serve --config config.yaml
```

## .env 示例文件

项目根目录下的 `.env.example` 包含了完整的配置模板：

```bash
# 复制并编辑
cp .env.example .env
```

完整内容请查看项目根目录的 `.env.example` 文件。

---

> 📖 [上一步：CS Demo](cs-demo.md)
