# 灵枢 (LingShu Agent) — 面试 Demo 指南

> 5 分钟流程，展示核心架构与产品能力。

## 准备工作

```bash
pip install agent-harness
cp .env.example .env   # 配置 LLM Key（DeepSeek / OpenAI / Ollama）
```

确保依赖就绪后，启动服务即可进入演示。

---

## 5 分钟演示流程

### 1. 架构展示（1 分钟）

- 打开 `AGENTS.md`，指向核心架构图
- 点出 **Supervisor-Worker 三层架构**：
  - 用户交互层 → FastAPI Server (:8788)
  - 编排层 → LangGraph 多 Agent 图
  - 基础设施层 → 推理后端 / 搜索 / 存储
- 一句话概括：*"你告诉灵枢调研什么，它自己决定怎么搜、怎么分析，做完了给你一份正式报告。"*

### 2. 启动服务（30 秒）

```bash
agent-harness serve
```

- 终端输出 `INFO:     Uvicorn running on http://127.0.0.1:8788`
- 浏览器打开 `http://127.0.0.1:8788` 进入主界面

### 3. CS Demo 客服演示（1 分钟）

- 导航到 `http://127.0.0.1:8788/cs-demo`
- 展示：
  - **语音输入**：点击麦克风图标，说"查一下我的订单"
  - **多语言切换**：切换中/英文界面
  - **消息评价**：对回复点👍/👎
  - **场景卡片**：点击"退换货"、"物流查询"等预设场景，观察 LLM 驱动的客服回复（SSE 流式输出）

### 4. 调研助手核心功能（1 分钟）

- 回到主界面 `http://127.0.0.1:8788`
- 输入一个搜索查询，例如：*"2025 年大模型 Agent 框架对比"*
- 展示 **thinking steps 可视化** — 观察 Supervisor 如何：
  1. 分析用户意图
  2. 并行分配 Search / Analyze Worker
  3. 验收结果
  4. 汇总为正式报告
- 打开生成的 HTML 报告：专业灰蓝模板 + 自动目录 + 引用来源编号

### 5. 代码质量展示（30 秒）

- GitHub 仓库 → **62 tests passing** CI badge（56 单元 + 6 集成）
- CHANGELOG.md → **61 个版本迭代**（v0.4 → v0.61）
- `mkdocs` 文档站（如有部署）

### 6. Docker 部署（30 秒）

```bash
docker compose up -d
```

- 展示 `docker-compose.yml` 中 LingShu + SearXNG 的多服务编排
- 多阶段构建、依赖分层缓存

### 7. 加分项（30 秒）

- **PWA**：浏览器地址栏右侧「安装」→ 添加到手机主屏幕
- **Android APK**：展示编译后的 APK 安装包
- **快速创建场景**：
  ```bash
  python scripts/create-app.py my_app
  ```
  演示脚手架一键生成新 Agent 应用

---

## 面试 Q&A 参考

| 可能的问题 | 回答要点 |
|-----------|---------|
| 架构上有什么亮点？ | Supervisor-Worker 编排、三级抓取降级、双模认证、SQLite 线程安全 |
| 使用什么 LLM？ | DeepSeek Flash（云端）/ Qwen-35B（本地）/ Ollama 多模型群，可切换 |
| 多人用怎么办？ | JWT+RBAC、owner_id 会话隔离、Semaphore(5) 并发限流、100req/min/IP |
| 怎么保证搜索质量？ | SearXNG→DuckDuckGo→skill 三级降级，5 层解析去重，5 分钟缓存 |
| 数据安全？ | 本地优先、JWT 双 token、审计日志、CSP 头、路径遍历防护 |

---

## 常见问题

- **问题**：启动时报 `No LLM backend configured`
  - **解决**：检查 `.env` 配置，确保 `LLM_API_KEY` 和 `LLM_BASE_URL` 正确
- **问题**：搜索返回空
  - **解决**：确认 SearXNG 运行在 `:4000`，或检查网络连通性
- **问题**：CS Demo 无响应
  - **解决**：确认 LLM 后端可达，查看终端错误日志
