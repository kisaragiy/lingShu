# 压测报告

> 灵枢 (LingShu Agent) — API 压力测试报告

## 测试方式

使用 [Locust](https://locust.io/) 对核心 API 端点进行压力测试，模拟多用户并发访问。

## 运行

```bash
pip install locust
locust -f locustfile.py --host http://127.0.0.1:8788 --headless -u 50 -r 5 --run-time 60s
```

| 参数 | 值 | 说明 |
|------|-----|------|
| `-u` | 50 | 模拟用户数 |
| `-r` | 5 | 每秒启动用户数 |
| `--run-time` | 60s | 运行时长 |
| `--headless` | 启用 | 无 Web UI 模式 |

## 测试场景

| 端点 | 权重 | 方法 | 说明 |
|------|------|------|------|
| `GET /health` | 3 | GET | 健康检查 |
| `GET /cs-demo` | 2 | GET | CS Demo 页面 |
| `GET /` | 1 | GET | 主页面 |
| `POST /v1/cs/chat` | 5 | POST | 客服对话（带 LLM 调用） |
| `POST /v1/cs/chat/stream` | 2 | POST | 流式客服对话（SSE） |
| `GET /knowledge-qa` | 1 | GET | 知识库问答页面 |

## 预期结果

| 指标 | 目标值 |
|------|--------|
| 平均响应时间 (avg) | < 500ms |
| P95 响应时间 | < 1000ms |
| P99 响应时间 | < 2000ms |
| 错误率 | < 1% |
| RPS (每秒请求数) | 可稳定处理 50+ |
| 服务稳定性 | 压测过程中不崩溃、不 OOM |

## 注意事项

- `POST /v1/cs/chat` 和 `POST /v1/cs/chat/stream` 涉及 LLM 调用，响应时间取决于后端模型推理速度
- 建议在压测前确保 LLM 后端（DeepSeek Flash / Ollama / llama.cpp）已预热
- 压测时监控服务端 CPU、内存占用，以及 LLM 后端的排队情况
- Semaphore(5) 并发限流会自然限制 Agent 类请求的并行数，超时返回 503
- 100 req/min/IP 速率限制可能在单机高并发下触发，如有需要可临时调高
