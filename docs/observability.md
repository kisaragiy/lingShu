# 灵枢可观测性（Observability）

> 本文所有数字来自 **2026-09-11 实测**（真实起服务 + 真实流量 + Prometheus 文本协议读回），
> 非目标值、非估算。复现命令见每节末尾。

## 1. 指标清单

`core/metrics.py` 用 `prometheus_client` 暴露指标，`/metrics` 端点免认证（Prometheus 抓取约定）。

| 指标 | 类型 | 标签 | 说明 |
|------|------|------|------|
| `harness_http_requests_total` | Counter | method, endpoint, status | HTTP 请求数（**含被中间件短路拒绝的 401/429**） |
| `harness_http_request_duration_seconds` | Histogram | method, endpoint | HTTP 耗时（11 档桶，5ms–10s） |
| `harness_tool_calls_total` | Counter | tool, status | **工具调用结果 + 失败原因分类** |
| `harness_tool_call_duration_seconds` | Histogram | tool | 工具调用耗时（11 档桶，10ms–60s） |
| `harness_tool_results_total` | Counter | tool, verdict | **工具结果有效性**（ok / error / warning）——抓「调用成功但结果没用」，见 §3.5 |
| `harness_llm_calls_total` | Counter | model, status | LLM 调用数 ⚠️ 见 §5 已知缺口 |
| `harness_llm_tokens_total` | Counter | model, type | Token 消耗（prompt/completion）⚠️ 同上 |
| `harness_agent_calls_total` / `_duration_seconds` | Counter/Histogram | worker_type | Agent（Worker）调用 ⚠️ 同上 |
| `harness_active_sessions` | Gauge | — | 当前活跃会话 |

### 工具失败原因分类

失败不是一坨。`metrics.classify_tool_failure()` 把 `call_tool` 的失败按 error 前缀归类，
使各类失败可分别聚合——它们的运维动作完全不同：

| status | 含义 | 该做什么 |
|--------|------|---------|
| `success` | 成功 | — |
| `not_found` | 模型选了不存在的工具 | 改 prompt / 工具描述，不是改代码 |
| `disabled` | 工具被开关禁用 | 查配置，不是 bug |
| `denied` | 权限拒绝（不可逆工具未确认） | 正常防护，关注突增 |
| `needs_confirm` | 命中危险操作确认矩阵 | 正常防护 |
| `guard_unavailable` | 安全护栏模块不可用（fail-closed 拒绝） | **告警：护栏掉了** |
| `timeout` | 工具执行超时 | 查该工具的下游 |
| `error` | 工具自身异常 | 改代码 |

**实现要点**：指标记录包装在 `registry.call_tool()` 入口（`_call_tool_impl` 之外），
不侵入 8 个 return 分支；且整段指标代码被 `try/except` 包住——
**观测层故障绝不改变工具调用结果**（有测试守着：`tests/test_tool_metrics.py`）。

## 2. 真实快照

### 2.1 轻载（41 个请求，含认证拒绝路径）

```
harness_http_requests_total{endpoint="/health",method="GET",status="200"} 21.0
harness_http_requests_total{endpoint="/",method="GET",status="200"} 8.0
harness_http_requests_total{endpoint="/cs-demo",method="GET",status="200"} 5.0
harness_http_requests_total{endpoint="<unmatched>",method="GET",status="401"} 8.0
```

注意最后一行：8 个 401（三条不同路径）聚合成**一条**序列。

### 2.2 50 并发 / 60s 压测后

```
harness_http_requests_total{endpoint="/v1/cs/chat",method="POST",status="200"} 306.0
harness_http_requests_total{endpoint="/health",method="GET",status="200"} 172.0
harness_http_requests_total{endpoint="/cs-demo",method="GET",status="200"} 143.0
harness_http_requests_total{endpoint="/v1/cs/chat/stream",method="POST",status="200"} 125.0
harness_http_requests_total{endpoint="/knowledge-qa",method="GET",status="200"} 74.0
harness_http_requests_total{endpoint="/",method="GET",status="200"} 68.0
harness_http_requests_total{endpoint="/metrics",method="GET",status="200"} 58.0
```

## 3. 失败率怎么查（PromQL）

```promql
# 整体错误率（5 分钟窗口）
sum(rate(harness_http_requests_total{status=~"5.."}[5m]))
  / sum(rate(harness_http_requests_total[5m]))

# 按状态码分布 —— 401/429 突增 = 有人在爆破 / 限流被触发
sum by (status) (rate(harness_http_requests_total[5m]))

# 最慢的端点（P95）
histogram_quantile(0.95,
  sum by (endpoint, le) (rate(harness_http_request_duration_seconds_bucket[5m])))

# 工具失败原因分布（TOP 失败工具）
topk(5, sum by (tool, status) (rate(harness_tool_calls_total{status!="success"}[5m])))

# 工具调用成功率
sum(rate(harness_tool_calls_total{status="success"}[5m]))
  / sum(rate(harness_tool_calls_total[5m]))

# ⭐「调用成功但结果没用」——这一层靠 tool_results_total，tool_calls_total 看不见
sum by (tool) (rate(harness_tool_results_total{verdict!="ok"}[5m]))

# 工具结果有效率
sum(rate(harness_tool_results_total{verdict="ok"}[5m]))
  / sum(rate(harness_tool_results_total[5m]))
```

## 3.5 工具失败数据（真实跑出来的）

2026-09-11 通过真实 Agent 路径（`POST /v1/chat/completions`）跑任务，实测（修复后）：

```
harness_tool_calls_total{status="success",tool="search"}     2.0
harness_tool_calls_total{status="success",tool="think"}      2.0
harness_tool_calls_total{status="needs_confirm",tool="code_execute"} 1.0
harness_tool_results_total{tool="search",verdict="ok"}       2.0
harness_tool_results_total{tool="think",verdict="error"}     2.0
```

**这张表说明了为什么需要两层指标**：

| 视角 | 数字 | 会得出的结论 |
|------|------|------------|
| 只看 `tool_calls_total` | 4 次调用全 `success` | 「工具运行良好」❌ |
| 加上 `tool_results_total` | think 的 2 次结果全 `error` | 「think 工具产出的内容全是无效的」✅ |

`think` 调用返回成功（函数没抛异常）但结果被判为无效——LLM 后端不可用时的典型形态。
**如果只建调用层指标，生产上「模型/工具在跑但产不出有用结果」这种最危险的静默劣化是看不见的。**

### ⚠️ 这两个 bug 只有真跑才暴露（都已在同日修掉）

**Bug 1：结果有效性判定漏判 → 指标在说谎**

`validate_result` 的降级信号词表有「失败」没有「不可用」。LLM 后端不可用时 Worker 输出
`[搜索] 由于 LLM 后端不可用，搜索结果不可用`，被判成 `severity=ok` ——
于是 `/metrics` 里 search 一路显示「结果有效」，**而实际输出是废的**。

修：`_FAILURE_MARKERS` 补上「不可用」。→ `tests/test_result_validation.py`（11 项）

**Bug 2：LLM 不可用时丢弃已到手的工具结果（可用性无谓降低）**

`_worker_synthesize` 把工具结果（`evidence`）**只喂给 LLM 做总结**；LLM 一挂就走
`_fallback_worker_result` 模板，把 evidence 整个丢掉。实测日志：

```
[Search] DuckDuckGo → ok (5 结果)
[Search] final → ok (5 结果)
  ✅ search (9.3s): [搜索] 由于 LLM 后端不可用，搜索结果不可用。   ← 5 条结果被扔了
```

搜索明明成功拿到 5 条真实结果，用户却被告知「不可用」。修：LLM 全挂时优先交出
`evidence`，让上层决定是否润色。修复后同样任务输出变成真实搜索结果：

```
### search ['langchain系列（终）- LangGraph 多智能体详解-CSDN博客: ...']
```

→ `tests/test_worker_llm_fallback.py`（2 项）

> **教训**：`validate_result` 一直在跑，但结果从未接入指标 —— 这正是「装了没通电」。
> 而且它自己还判错过。**指标的数据源本身也需要被验证**，否则监控会把「无输出」
> 报成「一切正常」——比没有监控更危险。

复现：
```bash
HARNESS_RATE_LIMIT=100000 ./.venv/Scripts/python scripts/run_server_8788.py &
./.venv/Scripts/python scripts/run_agent_emit_tool_metrics.py
```

> 该脚本不需要 LLM：`workers.py` 的 Auto-route（含「搜索/查/找」→ 自动 `call_tool("search")`，
> 数学 → `call_tool("code_execute")`）使工具调用不依赖 LLM 选工具；`LLAMA_API` 非空
> （指向不可达地址）保证走真 Agent 路径而非 demo 回退分支。

## 4. 修复过的两个监控盲区（真实事故复盘）

两个盲区都是**实测**发现的（对真实 :8788 打 41 个请求，8 个 401 在 /metrics 里零命中），
不是代码走查猜的。修复均有回归测试锁：`tests/test_metrics_coverage.py`。

### 盲区一：认证短路响应不进指标

**根因**：Starlette 的 `add_middleware` 是 `insert(0)` —— **后注册者在外层**。
指标中间件原本注册在认证中间件之前，因此处在**内侧**；认证短路返回的 401
根本到不了指标中间件。

**为什么严重**：401/429 突增是"有人在爆破 / 被限流"的第一信号，
恰恰是安全监控最该报警的地方 —— 监控在最该看见的地方失明。

**修复**：把指标中间件挪到**所有中间件之后**注册（`main.py` 文件末尾），确保它包住全部短路路径。

**验证**（同一组流量，修复前后）：

| | 修复前 | 修复后 |
|---|---|---|
| 发出的 401 | 8 | 8 |
| `/metrics` 里的 401 | **0** | **8**（1 条序列） |

### 盲区二：未捕获异常（5xx）不进指标

**根因**：`metrics_middleware` 没有异常路径 —— `await call_next(request)` 抛异常时
直接冒泡到最外层 `ServerErrorMiddleware`（它才转成 500），指标代码根本没执行。

**修复**：加 `try/except`，异常时记 `status="500"` 后 `raise`（不吞异常，行为不变）。

**验证**：注入必然抛异常的路由，修复前 `/metrics` 里 500 计数 = 0，修复后正确计数。
压测中也实证捕获到 3 个 500（修复前的并发缺陷，见 `docs/load-test-report.md`）。

### 顺带修掉的高基数隐患

原实现从 `request.scope["route"]` 取路径，未匹配时回退到**原始 URL** ——
攻击者用随机路径刷 404 就能让 Prometheus 每个请求建一条时间序列（内存/查询性能杀手）。

改为：路由模板（`/v1/reports/{id}`）→ 取模板；未匹配 → 归一为 `<unmatched>`，
**绝不回退原始 URL**。回归测试断言不同未匹配路径必须聚合成 1 条序列。

> 代价：被认证拦下的请求看到的是 `<unmatched>` 而非具体路径。
> 这是有意的取舍——**明细查日志**（logging 中间件记录完整 method/path/status），
> **趋势看指标**（有界标签，不会被攻击者撑爆）。

## 5. 已知缺口（未做，不是"没问题"）

| 缺口 | 影响 | 说明 |
|------|------|------|
| 无采集/存储/告警后端 | `/metrics` 只有拉取端点，无 Prometheus/Grafana 落库，**无历史趋势、无告警** | 本地优先的产品定位下有意不引入（见 ADR-001）；接后端是部署侧的事，不是代码侧 |
| 无分布式追踪落地 | `core/pipeline/tracing.py` 有 trace_tree，但未与指标打通 | 排查单次请求仍需看结构化日志 |

### 已完成（原缺口，2026-09-11 关掉）

`record_llm_call` / `record_agent_call` 曾长期零调用点 —— LLM 调用数、Token 消耗、Agent 调用
三个指标**恒为 0**（"装了没通电"）。接线时又发现**两条独立的 LLM 调用路径**：

| 路径 | 入口 | 谁在用 |
|------|------|--------|
| `core/pipeline/llm.py::call_llama` | `get_llm_config()["api_url"]` | 各业务模块 |
| `core/agents/supervisor.py::_call_llm_full` | **直接 post `LLAMA_API`，绕过 llm.py** | **Agent 主链路（Supervisor/Worker）** |

只接前者时，日志里 8+ 次「LLM 调用异常」，而 `/metrics` 里 `status="error"` **一行都没有** ——
监控对主链路 LLM 完全失明。两条都接上后的实测：

```
harness_llm_calls_total{model="mock",status="error"}          12.0   ← 接线前此行为 0
harness_llm_calls_total{model="qwen3:1.7b",status="success"}   2.0
harness_llm_tokens_total{model="qwen3:1.7b",type="prompt"}    58.0
harness_llm_tokens_total{model="qwen3:1.7b",type="completion"} 1916.0
harness_agent_calls_total{worker_type="search"}                2.0
harness_agent_calls_total{worker_type="analyze"}               2.0
```

**教训**：接线前先确认「这个入口是不是真实调用路径」。同一个能力可能有多个入口
（这里 `pipeline/llm.py` 和 `agents/supervisor.py` 各有一套 HTTP 调用），
只监控其中一个会出现「有失败、无告警」的假安全感。

> 记缓存命中**不计入** `llm_calls_total` —— 它不是 LLM 调用，计进去会让调用数与 token 数虚高
> （与 §3.5 的「指标必须能被信任」同一条原则）。

> 工具调用原本也**没有**指标，已补 `TOOL_CALLS_TOTAL`（调用层 + 失败原因分类）
> 与 `TOOL_RESULTS_TOTAL`（结果有效性层），见 §3.5。

> 本文件只记录**代码侧**的可观测性能力。告警规则、看板、采集器属于部署配置，
> 不进代码库（与 ADR-001「不为外部用户引入服务端组件」一致）。

## 6. 复现命令

```bash
# 起服务（压测前务必放宽限流，默认 100 req/min/IP 会掩盖真实吞吐）
cd ~/agent-harness
HARNESS_RATE_LIMIT=100000 ./.venv/Scripts/python scripts/run_server_8788.py &

# 抓一份真实快照
curl -s --noproxy '127.0.0.1' http://127.0.0.1:8788/metrics | grep '^harness_'

# 只看失败数据
curl -s --noproxy '127.0.0.1' http://127.0.0.1:8788/metrics \
  | grep '^harness_http_requests_total' | grep -v 'status="200"'

# 观测性回归测试
./.venv/Scripts/python -m pytest tests/test_tool_metrics.py tests/test_metrics_coverage.py -q
```
