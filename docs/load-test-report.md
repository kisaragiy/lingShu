# 压测报告（实测）

> 灵枢 (LingShu Agent) — API 压力测试。**本文全部为实测数据**（2026-09-11，Locust 2.46.5
> headless 模式，真实起服务、真实打流量）。旧版本只写了「预期结果/目标值」，本次改为实测值 ——
> 因为**预期值不是证据，压测的价值全在实测数字**。

## 一、方法

```bash
# 起服务（必须放宽限流：默认 100 req/min/IP 会让 50 并发立刻 429，测出来的是限流器不是服务）
cd ~/agent-harness
HARNESS_RATE_LIMIT=100000 ./.venv/Scripts/python scripts/run_server_8788.py &

# 压测
./.venv/Scripts/python -m locust -f locustfile.py --host http://127.0.0.1:8788 \
    --headless -u 50 -r 5 --run-time 60s --csv=loadtest
```

| 参数 | 值 |
|------|-----|
| 并发用户数 `-u` | 50 |
| 启动速率 `-r` | 5/s |
| 时长 | 60s |
| 负载分布 | `/v1/cs/chat` ×5、`/health` ×3、`/cs-demo` ×2、`/v1/cs/chat/stream` ×2、`/`、`/knowledge-qa`、`/metrics` 各 ×1 |
| 认证 | `/v1/*` 带 `X-API-Key`（压测端与 API 同机读同一 token 文件） |

### 环境与限制（必读，防止误读数字）

- 单进程 `uvicorn`（`HARNESS_WORKERS=1`），Windows 单机，**LLM 后端未启动**（Ollama / ComfyUI 均 down）。
- 因此 `/v1/cs/chat` 走的是**模板降级路径**，数字反映的是「LLM 不可用时」的管线性能，
  **不能外推为接入真实 LLM 后的延迟**（届时瓶颈会从本机 CPU 转到模型推理）。
- 本报告可信的结论是**同一环境下的前后对比**，不是绝对性能基线。

## 二、实测结果

### 2.1 汇总

| 指标 | 修复前 | 修复后 | 变化 |
|------|--------|--------|------|
| 总请求数（60s） | 66 | **938** | **14.2×** |
| 吞吐 | 1.16 req/s | **15.79 req/s** | **13.7×** |
| 平均响应 | 25,248 ms | **929 ms** | **27× 更快** |
| P50 | 24,000 ms | **10 ms** | **2400× 更快** |
| P95 | 45,000 ms | **2,100 ms** | **21× 更快** |
| P99 | 51,000 ms | **2,100 ms** | **24× 更快** |

### 2.2 分端点

| 端点 | 修复前 请求数 | 修复后 请求数 | 修复前 P50 | 修复后 P50 | 修复前 P95 | 修复后 P95 |
|------|------|------|------|------|------|------|
| `GET /health` | 11 | 171 | 22,000 ms | **3 ms** | 48,000 ms | **11 ms** |
| `GET /` | 5 | 67 | 6,400 ms | **3 ms** | 23,000 ms | **11 ms** |
| `GET /cs-demo` | 14 | 142 | 25,000 ms | **3 ms** | 46,000 ms | **11 ms** |
| `GET /knowledge-qa` | 3 | 74 | 22,000 ms | **3 ms** | 41,000 ms | **12 ms** |
| `GET /metrics` | 6 | 57 | 24,000 ms | **4 ms** | 42,000 ms | **10 ms** |
| `POST /v1/cs/chat` | 24 | 304 | 35,000 ms | **2,000 ms** | 44,000 ms | **2,000 ms** |
| `POST /v1/cs/chat/stream` | 3 | 123 | 37,000 ms | **2,100 ms** | 51,000 ms | **2,100 ms** |

**最大响应时间**（最能反映阻塞）：

| 端点 | 修复前 Max | 修复后 Max |
|------|-----------|-----------|
| `GET /health` | 48,043 ms | **53 ms** |
| `GET /` | 22,601 ms | **38 ms** |
| `GET /cs-demo` | 46,016 ms | **23 ms** |
| `POST /v1/cs/chat` | 45,006 ms | **2,693 ms** |

### 2.3 错误

Locust 口径 **0 失败**（两次压测都是）。

⚠️ 但服务端指标记录到修复前有 **3 个 500**——它们是 `ClientDisconnect`
（客户端等不到响应就超时断开，服务端此时才去读 body）。Locust 因为已放弃等待未计入。
这正是「只看压测工具的错误率会漏掉真实错误」的实例；修复后 500 归零。

## 三、发现与修复：async 端点里的同步阻塞

### 症状

纯内存操作、无任何 IO 的 `GET /health`，**空闲时 2ms**（实测三次：5850ms 冷启 / 2.1ms / 1.9ms），
**50 并发下 22,000 ms**（P50）。同时 60 秒只跑完 66 个请求。

### 根因

`apps/cs_demo/api.py` 的 `cs_chat` 是 `async def`，但内部**直接调用同步阻塞函数**：

```python
async def cs_chat(request: Request):
    ...
    result = run_cs_agent(message, context=context)   # ← 同步调用，无 await / 无线程池
```

`run_cs_agent` → `_call_cs_llm` → 阻塞式 HTTP 调 LLM（`timeout=2`）。它在事件循环里跑，
**整个循环被卡住**：LLM 不可用时每个请求阻塞约 2 秒，50 个并发请求全部串行排队，
连 `/health` 都要排在后面 —— 于是 2ms 变成 22 秒。

旁证：同文件的 `cs_chat_stream` **已经**正确用了 `loop.run_in_executor` 把同步 LLM 调用
丢进线程池。所以这不是架构选择，是 `cs_chat` 漏了。

### 修复

```python
from starlette.concurrency import run_in_threadpool
result = await run_in_threadpool(run_cs_agent, message, context=context)
```

### 效果

见 §2：吞吐 13.7×、P50 从 24 秒降到 10 毫秒、`/health` Max 从 48 秒降到 53 毫秒，
且并发下的 3 个 500 一并消失（它们是阻塞的下游症状，不是独立 bug）。

## 四、结论

1. **50 并发、60 秒、938 请求、0 失败** —— 修复后的服务在单进程下可稳定承载该负载。
2. 公开页面类端点 P95 ≤ 12ms（纯本地渲染）；`/v1/cs/chat` P95 = 2.1s（走模板降级，
   受内部 LLM 调用 2s 超时主导）。
3. 性能瓶颈已从「事件循环阻塞」转移到**外部 LLM 延迟**——继续优化本机代码的边际收益很低，
   下一步该做的是 LLM 调用的超时/并发策略（流式端点已用线程池，可复用该模式）。

## 五、复现

```bash
cd ~/agent-harness
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY   # 本机走 Clash TUN，显式代理会 000
export NO_PROXY="127.0.0.1,localhost"
HARNESS_RATE_LIMIT=100000 ./.venv/Scripts/python scripts/run_server_8788.py &

./.venv/Scripts/python -m locust -f locustfile.py --host http://127.0.0.1:8788 \
    --headless -u 50 -r 5 --run-time 60s --csv=loadtest --only-summary

# 压测后看服务端真实指标（包含 Locust 漏掉的 4xx/5xx）
curl -s --noproxy '127.0.0.1' http://127.0.0.1:8788/metrics | grep '^harness_http_requests_total'
```

原始数据：`loadtest_stats.csv` / `loadtest-after_stats.csv`（含 `_history` 分秒序列）。
