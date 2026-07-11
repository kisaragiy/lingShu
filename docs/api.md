# API 参考

Agent Harness 提供完整的 RESTful API，所有接口均返回 JSON 格式响应。

## 基础信息

- **基础路径**：`http://localhost:8000`
- **认证方式**：Bearer Token（可选，通过 `AUTH_TOKEN` 配置）
- **内容类型**：`application/json`

---

## 接口概览

| 类别 | 端点 | 方法 | 说明 |
|------|------|------|------|
| 🩺 **健康检查** | `/health` | GET | 服务状态与版本信息 |
| 🔐 **认证** | `/auth/login` | POST | 用户登录获取 Token |
| | `/auth/verify` | GET | 验证 Token 有效性 |
| 💬 **聊天** | `/chat/completions` | POST | 标准聊天补全 |
| | `/chat/stream` | POST | 流式聊天补全（SSE） |
| 🎯 **CS Demo** | `/cs-demo/scenarios` | GET | 获取所有演示场景 |
| | `/cs-demo/run` | POST | 运行指定演示场景 |
| 📋 **会话** | `/sessions` | GET | 获取会话列表 |
| | `/sessions` | POST | 创建新会话 |
| | `/sessions/{id}` | GET | 获取会话详情 |
| | `/sessions/{id}` | DELETE | 删除会话 |
| 📊 **评测报告** | `/reports` | GET | 获取评测报告列表 |
| | `/reports/{id}` | GET | 获取评测报告详情 |
| 📚 **知识库** | `/knowledge/upload` | POST | 上传文档到知识库 |
| | `/knowledge/search` | POST | 知识库语义搜索 |
| | `/knowledge/delete/{id}` | DELETE | 删除知识库文档 |
| 🛠️ **工具** | `/tools` | GET | 获取可用工具列表 |
| | `/tools/{name}/execute` | POST | 执行指定工具 |

---

## 详细接口说明

### 🩺 健康检查

```http
GET /health
```

**响应示例：**

```json
{
  "status": "ok",
  "version": "0.1.0",
  "uptime": "0:01:23",
  "python_version": "3.11.9",
  "active_models": ["gpt-4o", "claude-3-opus", "deepseek-v3"]
}
```

---

### 🔐 认证

```http
POST /auth/login
```

**请求体：**

```json
{
  "username": "admin",
  "password": "your-password"
}
```

**响应示例：**

```json
{
  "token": "eyJhbGciOiJIUzI1NiIs...",
  "expires_in": 86400,
  "token_type": "Bearer"
}
```

---

### 💬 聊天

#### 标准聊天

```http
POST /chat/completions
```

**请求体：**

```json
{
  "model": "gpt-4o",
  "messages": [
    {"role": "system", "content": "你是一个有用的助手。"},
    {"role": "user", "content": "你好，请介绍一下你自己。"}
  ],
  "temperature": 0.7,
  "max_tokens": 2048
}
```

**响应示例：**

```json
{
  "id": "chat-abc123",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "你好！我是 Agent Harness 驱动的 AI 助手..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 25,
    "completion_tokens": 68,
    "total_tokens": 93
  }
}
```

#### 流式聊天

```http
POST /chat/stream
```

与标准聊天相同的请求体。响应通过 Server-Sent Events (SSE) 流式返回：

```
data: {"choices": [{"delta": {"content": "你好"}}]}
data: {"choices": [{"delta": {"content": "！"}}]}
data: {"choices": [{"delta": {"content": "我是"}}]}
...
data: [DONE]
```

---

### 🎯 CS Demo

#### 获取演示场景

```http
GET /cs-demo/scenarios
```

**响应示例：**

```json
{
  "scenarios": [
    {"id": "customer-support", "name": "客户支持", "description": "模拟客户服务对话"},
    {"id": "code-review", "name": "代码审查", "description": "自动化代码审查流程"}
  ]
}
```

#### 运行演示场景

```http
POST /cs-demo/run
```

**请求体：**

```json
{
  "scenario_id": "customer-support",
  "params": {
    "customer_name": "张三",
    "issue_type": "退款咨询"
  }
}
```

---

### 📋 会话管理

```http
GET /sessions?page=1&limit=20
```

```http
POST /sessions
```

**请求体：**

```json
{
  "name": "我的对话会话",
  "model": "gpt-4o",
  "system_prompt": "你是一个专业的客服助手。"
}
```

---

### 📊 评测报告

```http
GET /reports
```

**响应示例：**

```json
{
  "reports": [
    {
      "id": "eval-001",
      "name": "客服场景评测",
      "created_at": "2026-07-10T12:00:00Z",
      "status": "completed",
      "score": 92.5
    }
  ],
  "total": 5,
  "page": 1
}
```

---

### 📚 知识库

```http
POST /knowledge/upload
```

**请求体（multipart/form-data）：**

| 字段 | 类型 | 说明 |
|------|------|------|
| file | File | 文档文件（PDF, TXT, MD） |
| collection | String | 知识库集合名称 |

```http
POST /knowledge/search
```

**请求体：**

```json
{
  "query": "Agent Harness 的安装步骤",
  "collection": "docs",
  "top_k": 5
}
```

---

### 🛠️ 工具

```http
GET /tools
```

**响应示例：**

```json
{
  "tools": [
    {
      "name": "web_search",
      "description": "搜索互联网信息",
      "parameters": {
        "type": "object",
        "properties": {
          "query": {"type": "string", "description": "搜索关键词"}
        }
      }
    }
  ]
}
```

---

## 错误码

| 状态码 | 含义 | 说明 |
|--------|------|------|
| 200 | OK | 请求成功 |
| 400 | Bad Request | 请求参数错误 |
| 401 | Unauthorized | 未授权，需要认证 |
| 403 | Forbidden | 权限不足 |
| 404 | Not Found | 资源不存在 |
| 429 | Too Many Requests | 请求频率超限 |
| 500 | Internal Server Error | 服务器内部错误 |

**错误响应格式：**

```json
{
  "error": {
    "code": "INVALID_PARAMETERS",
    "message": "缺少必需的参数：model",
    "details": null
  }
}
```

---

> 📖 [上一步：架构设计](architecture.md) | 📖 [下一步：CS Demo →](cs-demo.md)
