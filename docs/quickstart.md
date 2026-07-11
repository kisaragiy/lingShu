# 快速开始

本指南将帮助你在 5 分钟内从零开始运行第一个 Agent Harness 实例。

## 前提条件

- Python 3.11 或更高版本
- pip 包管理器
- （可选）Docker 环境

## 第一步：安装

### 使用 pip 安装

```bash
pip install agent-harness
```

推荐在虚拟环境中安装：

```bash
# 创建虚拟环境
python -m venv .venv

# 激活（Windows）
.venv\Scripts\activate

# 激活（macOS / Linux）
source .venv/bin/activate

# 安装
pip install agent-harness
```

### 从源码安装

```bash
git clone https://github.com/your-org/agent-harness.git
cd agent-harness
pip install -e .
```

## 第二步：配置环境变量

复制示例环境变量文件并填写必要的配置：

```bash
cp .env.example .env
```

编辑 `.env` 文件，至少配置以下内容：

```env
# LLM API 密钥（至少配置一个）
OPENAI_API_KEY=sk-your-openai-key
DEEPSEEK_API_KEY=sk-your-deepseek-key

# 服务端口（可选，默认 8000）
AGENT_HARNESS_PORT=8000

# 日志级别
LOG_LEVEL=INFO
```

!!! tip "API 密钥"
    至少需要配置一个 LLM 提供商的 API 密钥才能正常使用。支持 OpenAI、Anthropic Claude、DeepSeek 等主流模型。

## 第三步：启动服务

### 标准启动

```bash
agent-harness serve
```

启动后终端显示如下信息：

```
╭──────────────────────────────╮
│   Agent Harness v0.1.0       │
│   🚀 Service is running      │
│   📡 http://localhost:8000    │
│   📖 Docs: /docs             │
╰──────────────────────────────╯
```

### 使用 Docker

```bash
# 构建镜像
docker build -t agent-harness .

# 运行容器
docker run -d \
  --name agent-harness \
  -p 8000:8000 \
  -v $(pwd)/.env:/app/.env \
  agent-harness
```

或者使用 Docker Compose：

```bash
docker-compose up -d
```

## 第四步：验证运行

打开浏览器访问 `http://localhost:8000`，或使用 curl 验证：

```bash
# 健康检查
curl http://localhost:8000/health

# 预期响应
{"status": "ok", "version": "0.1.0", "uptime": "0:01:23"}
```

## 下一步

- 🏗️ 查看 [架构设计](architecture.md) — 了解系统架构与设计理念
- 📚 阅读 [API 参考](api.md) — 探索完整的 API 接口文档
- 🎯 体验 [CS Demo](cs-demo.md) — 查看客户成功演示场景
- ⚙️ 了解 [配置说明](configuration.md) — 详细的环境变量配置

---

## 常见问题

### Q: 启动时提示端口被占用？

```bash
# 查看端口占用
netstat -ano | findstr :8000

# 修改端口重新启动
set AGENT_HARNESS_PORT=8001
agent-harness serve
```

### Q: API 密钥无效？

确保 `.env` 文件中的 API 密钥正确，且对应的 LLM 服务账户余额充足。

### Q: 如何查看日志？

```bash
# 控制台日志
agent-harness serve --log-level DEBUG

# 日志文件默认位于
ls logs/agent-harness-*.log
```

---

> 📖 [上一步：首页](index.md) | 📖 [下一步：架构设计 →](architecture.md)
