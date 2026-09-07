# 灵枢升级方案 v2（求职驱动）

> 目标：让灵枢能接住 8-13K AI应用/Agent工程师岗的面试深挖。
> 原则：每个升级点必须能讲出「解决了什么真实问题 → 怎么做的 → 怎么验证的」三段式。
> 当前版本：v0.75.2 · 175 passed / 8 failed（全环境依赖）· 35 条规则 · 三重熔断已实装
> 日期：2026-09-05

---

## 〇、现状基线（实事求是）

### 已闭环（不用动）
| 能力 | 状态 | 面试可讲度 |
|------|------|-----------|
| Supervisor-Worker 编排 | ✅ LangGraph 图，40+ 工具 | ⭐⭐⭐⭐ |
| 三重熔断 | ✅ 实测 100% 触发/0% 误伤 | ⭐⭐⭐⭐⭐ |
| 重试退避 | ✅ llm.py 已接 with_retry | ⭐⭐⭐⭐ |
| 降级链 | ✅ cloud→local，降级 100% 接管 | ⭐⭐⭐⭐ |
| JWT + RBAC + 审计 | ✅ 28 测试 | ⭐⭐⭐（够用了） |
| RAG（向量 + BM25 降级） | ✅ rag_store.py 完整 | ⭐⭐⭐⭐ |
| 知识管道 v4 | ✅ 35 规则 + 2-gram 检索 + 沉淀机制 | ⭐⭐⭐⭐ |
| Docker + CI | ✅ Dockerfile + test.yml | ⭐⭐⭐ |

### 已知缺口（本方案要补的）
| 缺口 | 严重度 | 来源 |
|------|--------|------|
| 知识管道是"静态规则库"，没有活的使用闭环 | 🔴 P0 | 市场对齐分析 |
| 无可观测性（metrics/tracing 只有 Prometheus client 骨架） | 🔴 P0 | 架构剖析 Day 1 |
| 8 个测试因环境依赖失败，clone 即红 | 🟠 P1 | 实测 |
| 25 个 tmp_* 文件散落仓库 | 🟠 P1 | TASK-PROGRESS |
| 前端 1500 行 index.html 未模块化 | 🟡 P2 | AGENTS.md |
| 多模型路由是硬编码 if/else | 🟠 P1 | llm.py 源码 |
| 结构化输出无保障（LLM 返回自由文本，解析靠正则） | 🟠 P1 | graph_multi.py |
| 缺端到端集成测试 | 🟠 P1 | 测试覆盖率分析 |

---

## 一、Phase 1：知识管道活化（P0 · 3-5 天）

> **叙事核心**：「规则不是写完就完的——从 daily 信号自动抽候选，入库前过验证，入库后在真实场景拦截错误，拦截到的案例反馈回来改进规则。这是一个活的知识系统。」

### 1.1 规则自动进化闭环

**现状**：`extract_candidates.py` 能从 daily 抽信号词 → 输出候选规则。但候选→验证→入库是手动的，没有闭环。

**交付物**：
1. `scripts/rule_evolve.py` — 自动化管线：
   - 输入：daily 文件 + 现有规则库
   - 步骤：extract_candidates → 去重（与现有规则相似度 >0.8 跳过）→ 生成测试用例（至少 1 个 guardian 搜索命中）→ 写入规则库 + 更新索引
   - 输出：新增规则数 / 跳过数 / 失败数
2. 测试：`test_rule_evolve.py`（≥10 用例）

**验收标准**：
- [ ] 跑一次 `rule_evolve.py` 能从 daily 自动产出 ≥1 条新规则
- [ ] 新规则能通过 guardian 搜索验证（至少命中 1 条）
- [ ] 去重生效（重复规则不被二次入库）
- [ ] 全流程有日志，可追溯

**面试叙事**：
> "规则不是拍脑袋写的。我做了自动进化管线——从 daily 日志里用信号词抽取候选，跟现有规则做相似度去重，通过 guardian 验证后才入库。这样知识系统能自我生长，不是写完就死的静态库。"

---

### 1.2 检索层升级（2-gram → 混合检索）

**现状**：`guardian.py --search` 用 2-gram 字符重叠匹配。对中文有效，但对同义词/近义表达召回率低。

**交付物**：
1. 新增 `core/knowledge/hybrid_search.py`：
   - 向量检索（接百炼 text-embedding-v4，dim1024，本地 NPY 缓存）
   - BM25 关键词检索（现有 2-gram 保留作为降级）
   - 融合排序（RRF: Reciprocal Rank Fusion）
   - 降级链：向量不可用 → BM25 → 2-gram
2. 索引构建脚本：`scripts/build_search_index.py`（扫描规则库 → 生成向量 + BM25 索引）
3. 测试：`test_hybrid_search.py`（≥15 用例，含降级场景）

**验收标准**：
- [ ] 向量检索在百炼 API 可用时生效
- [ ] API 不可用时自动降级到 BM25，不报错
- [ ] BM25 不可用时降级到 2-gram
- [ ] 融合排序结果 ≥ 任一单路召回（用已知测试集验证）
- [ ] 冷启动无索引时能自动构建

**面试叙事**：
> "2-gram 对中文有效但召回不够——'显存不够'能命中，但'GPU 内存不足'就漏了。我加了向量检索（百炼 embedding）和 BM25 关键词检索，三路融合用 RRF 排序。更重要的是做了降级链——API 挂了不会整个检索崩，层层兜底。"

---

### 1.3 真实场景闭环数据

**现状**：知识管道有 35 条规则 + 38 测试，但缺「在真实工作流中被使用并拦截错误」的数据。

**交付物**：
1. `docs/knowledge-pipeline/real-intercepts.md` — 真实拦截案例集：
   - 从 daily 和 session_search 中提取 ≥5 个真实案例
   - 格式：场景描述 → 哪条规则命中 → 拦截了什么 → 结果
2. 在 guardian 中增加 `--audit-log` 模式：记录每次搜索的命中/未命中，积累数据

**验收标准**：
- [ ] 收集 ≥5 个真实拦截案例（有来源可追溯）
- [ ] 每个案例能复现（guardian 搜索命中对应规则）
- [ ] 文档格式统一，面试可直接引用

**面试叙事**：
> "不是写完就完了——这个管道真的在用。比如 R-22 拦截了'给简历写平台'这种夸大表述，R-11/12 在我处理显存问题时被自动命中。这些是我从 daily 日志里提取的真实案例，不是构造的测试数据。"

---

## 二、Phase 2：可观测性（P0 · 3-4 天）

> **叙事核心**：「可靠性不是靠说的，是靠能看到的。我做了完整的 metrics/tracing/logging 三件套，出了问题能秒级定位。」

### 2.1 Prometheus Metrics 完善

**现状**：`metrics.py` 有 Prometheus client 骨架，`prometheus-client` 在 dependencies 里。但指标覆盖不全。

**交付物**：
1. 完善 `core/metrics.py`：
   - `agent_task_total` (Counter, labels: status=success/failure/timeout/circuit_break)
   - `agent_task_duration_seconds` (Histogram)
   - `llm_request_total` (Counter, labels: model, status, is_cache_hit)
   - `llm_request_duration_seconds` (Histogram, labels: model)
   - `llm_tokens_total` (Counter, labels: model, direction=input/output)
   - `tool_call_total` (Counter, labels: tool_name, status)
   - `tool_call_duration_seconds` (Histogram, labels: tool_name)
   - `circuit_breaker_trips_total` (Counter, labels: reason)
   - `retry_attempts_total` (Counter, labels: outcome=success/exhausted)
   - `rag_query_duration_seconds` (Histogram, labels: strategy=vector/bm25/2gram)
2. 在 `api_fastapi.py` 暴露 `/metrics` 端点
3. 在 graph.py / graph_multi.py / llm.py / tool 调用点埋点

**验收标准**：
- [ ] `curl localhost:8788/metrics` 返回标准 Prometheus 格式
- [ ] 所有 Counter/Histogram 在真实请求后有数据
- [ ] 熔断触发时 `circuit_breaker_trips_total` 正确递增
- [ ] 无性能退化（埋点开销 <5%）

---

### 2.2 结构化日志 + 链路追踪

**现状**：`structlog` 在 dependencies 里，`core/logging.py` 有基础配置。但日志无 trace_id，跨模块关联困难。

**交付物**：
1. 完善 `core/logging.py`：
   - 每个请求自动注入 `trace_id`（UUID）
   - 日志字段：trace_id / agent_id / tool_name / duration / status
   - 输出格式：JSON Lines（方便 ELK/Loki 接入）
2. FastAPI middleware：自动注入 trace_id 到请求上下文
3. `scripts/log_analyzer.py`：简单的日志分析脚本（统计错误率/慢请求/熔断次数）

**验收标准**：
- [ ] 一个完整请求的所有日志行共享同一个 trace_id
- [ ] 日志 JSON 格式可被 `jq` 解析
- [ ] `log_analyzer.py` 能从日志文件输出统计摘要

**面试叙事**：
> "我做了一套完整的可观测性：Prometheus 指标暴露了任务成功率/LLM 延迟/token 消耗/工具调用/熔断触发；structlog 做了 JSON 结构化日志 + trace_id 全链路追踪。出了问题不是靠猜，是靠 metrics 看趋势、靠 trace_id 秒级定位到哪个工具哪次 LLM 调用慢。"

---

## 三、Phase 3：LLM 编排层深化（P0 · 3-4 天）

> **叙事核心**：「不是接了个 API 就完事——多模型路由有策略、输出有格式保障、长对话有窗口管理。这些是生产级 LLM 应用的基本功。」

### 3.1 多模型路由策略化

**现状**：`llm.py` 有 cloud→local 降级，但路由是硬编码。百炼免费额度已分配（见 knowledge/reports/bailian-model-allocation-20260904.md）但未接入。

**交付物**：
1. 新增 `core/llm_router.py`：
   - 路由表配置（YAML/JSON）：按任务类型/复杂度/成本选模型
   - 角色锁定：主对话用 qwen3.8-max，子任务用 qwen3.7-plus/flash，视觉用 qwen-vl-max
   - 配额感知：每模型剩余额度查询 + 阈值告警
   - 降级链：首选模型 → 备选模型 → 本地 ollama
2. 接入百炼 DashScope SDK（保持 OpenAI 兼容接口）
3. 测试：`test_llm_router.py`（≥12 用例）

**验收标准**：
- [ ] 路由表可配置，改配置不改代码
- [ ] 首选模型不可用时自动降级（<1s 切换）
- [ ] 配额低于阈值时自动切备选
- [ ] 日志记录每次路由决策（选了哪个模型、为什么）

**面试叙事**：
> "我做了模型路由层——不是写死调哪个 API。配置表定义了不同任务该用什么模型、降级链是什么、配额阈值在哪。生产环境不可能只依赖一个模型，路由层让系统对单点故障有弹性。"

---

### 3.2 结构化输出保障

**现状**：Supervisor 要求 Worker 返回 JSON，但实际 LLM 返回自由文本，解析靠正则——容易崩。

**交付物**：
1. 新增 `core/output_parser.py`：
   - JSON 提取器：从 LLM 响应中可靠提取 JSON（处理 markdown 代码块/多余文本/转义问题）
   - Schema 验证：用 Pydantic 定义 Worker 输出 schema，校验 + 自动重试
   - 降级：解析失败 → 要求 LLM 重新输出（最多 2 次）→ 返回原始文本 + 标记 parse_failed
2. 为 Supervisor 的验收节点和 Finalizer 定义 Pydantic 模型
3. 测试：`test_output_parser.py`（≥15 用例，含各种脏输入）

**验收标准**：
- [ ] 对标准 JSON 输出 100% 解析成功
- [ ] 对脏输入（markdown 包裹/多余前缀后缀/转义错误）≥90% 恢复
- [ ] 解析失败时有明确降级，不抛异常到用户
- [ ] Worker 输出 schema 可被 Pydantic 校验

**面试叙事**：
> "LLM 返回结构化输出是不可靠的——它会加 markdown 代码块、多余解释、甚至转义错误。我做了输出解析器：先提取 JSON、用 Pydantic 校验、失败自动重试。降级方案是返回原始文本但标记 parse_failed，不让解析错误直接崩到用户面前。"

---

### 3.3 上下文窗口管理

**现状**：对话历史直接拼接，无压缩。长对话会超 token 限制。

**交付物**：
1. 新增 `core/context_manager.py`：
   - Token 计数（tiktoken / 近似估算）
   - 滑动窗口：保留最近 N 轮 + 系统提示
   - 摘要压缩：超窗口时调 LLM 生成历史摘要，替换旧消息
   - 重要消息锚定：标记为重要的消息不压缩
2. 接入 graph.py 的上下文构建环节
3. 测试：`test_context_manager.py`（≥10 用例）

**验收标准**：
- [ ] 长对话（>50 轮）不超 token 限制
- [ ] 摘要压缩后关键信息不丢失（用测试集验证）
- [ ] Token 计数误差 <5%
- [ ] 压缩操作本身不阻塞主流程（<2s）

---

## 四、Phase 4：工程质量补全（P1 · 2-3 天）

> 这部分不是面试主菜，但面试官 clone 下来看代码质量时会加分。

### 4.1 代码卫生

| 动作 | 说明 |
|------|------|
| 清理 25 个 tmp_* 文件 | 逐个判定：有并入价值的提取到正式位置，其余删除 |
| 清理垃圾目录 | 2 个编码事故产生的垃圾目录，确认无用后删除 |
| 前端模块化 | index.html → 拆成 `static/pages/` 下独立组件（对话/知识库/编排/设置） |

### 4.2 测试体系

| 动作 | 说明 |
|------|------|
| 环境依赖用例 mock 化 | 8 个 failed（6 客服流式 + 2 llama 缓存）用 pytest-mock 隔离 |
| 集成测试 | Supervisor→Worker 端到端至少 1 条 happy path + 1 条熔断触发 |
| 突变测试 | 跑一轮 mutmut，证明测试质量（mutation score >60% 即合格） |

### 4.3 文档同步

| 动作 | 说明 |
|------|------|
| AGENTS.md 版本号同步 | 当前写 v0.28.0，实际已 v0.75.2 |
| 架构文档更新 | docs/architecture.md 过时，需反映 v0.75+ 真实架构 |
| ADR 补充 | 可靠性硬化（Day 1→7）写 ADR-002 |
| README 最终打磨 | 数字对账 + 截图 + 对外零负面（已有基础，微调） |

### 4.4 部署完善

| 动作 | 说明 |
|------|------|
| docker-compose 一键启动 | 灵枢 + SearXNG + 可选 Ollama，`docker compose up` 即用 |
| CI 补全 | 已有 test.yml，补 ruff lint + bandit security scan |

---

## 五、Phase 5：差异化加分（P2 · 可选）

> 锦上添花，不做也不影响面试。如果 Phase 1-4 做完还有余力再碰。

| 项目 | 价值 | 工时 |
|------|------|------|
| 可观测仪表盘 | 内置简单 metrics 页（不依赖 Grafana），面试演示用 | 1-2 天 |
| 插件系统 | plugin_loader.py 已有骨架，重新接入 + 1 个示例插件 | 2 天 |
| 定时任务 | agent_cron.py 已有骨架，重新接入 | 1 天 |
| MCP 服务器 | mcp_server.py 已有，打磨对外暴露 + 文档 | 1-2 天 |

---

## 六、不做什么（红线）

| 不做 | 原因 |
|------|------|
| 前端窗口大重构 | pywebview/小程序是加分项不是主线，不值得大改 |
| Java/微服务/消息队列 | A 路线不碰，这是 B 路线需求 |
| 安全护栏继续深挖 | JWT/RBAC/CSP/审计已有 28 测试，够了 |
| 新 Agent 模式/框架 | Supervisor-Worker 够用，不追框架时髦 |
| 做新用户/真部署 | 求职驱动，不是产品化 |

---

## 七、执行顺序与工时估算

```
Week 1:
  Day 1-2:  Phase 1.1 规则自动进化闭环
  Day 3-4:  Phase 1.2 检索层升级（混合检索 + 降级链）
  Day 5:    Phase 1.3 真实场景闭环数据收集

Week 2:
  Day 1-2:  Phase 2.1 Prometheus Metrics 完善 + 埋点
  Day 3:    Phase 2.2 结构化日志 + trace_id
  Day 4-5:  Phase 3.1 多模型路由策略化

Week 3:
  Day 1-2:  Phase 3.2 结构化输出保障
  Day 3:    Phase 3.3 上下文窗口管理
  Day 4-5:  Phase 4 工程质量补全（卫生+测试+文档+部署）

Week 4 (可选):
  Phase 5 差异化加分项（挑 1-2 个做）
```

**总工时：3 周核心 + 1 周可选 ≈ 15-20 个工作日**

---

## 八、验收总门禁

Phase 1-4 全做完后，以下全部通过才算「升级完成」：

| # | 验收项 | 标准 |
|---|--------|------|
| 1 | 知识管道闭环 | rule_evolve.py 能自动产出新规则 + 通过验证 + 入库 |
| 2 | 混合检索 | 向量/BM25/2-gram 三路 + 降级链 + 融合排序 |
| 3 | 真实拦截案例 | ≥5 个有来源可追溯的真实案例文档 |
| 4 | Metrics | /metrics 端点有真实数据，熔断触发时正确递增 |
| 5 | 日志追踪 | 一个请求的所有日志共享 trace_id |
| 6 | 模型路由 | 可配置路由表 + 降级链 + 配额感知 |
| 7 | 结构化输出 | 脏输入 ≥90% 恢复 + Pydantic 校验 |
| 8 | 上下文管理 | 50+ 轮不超窗口 + 摘要压缩不丢关键信息 |
| 9 | 测试全绿 | 0 failed（环境依赖已 mock 化） |
| 10 | 代码卫生 | 无 tmp_* 残留，无垃圾目录 |
| 11 | 文档同步 | AGENTS.md/架构文档/ADR 版本对齐 |
| 12 | 面试叙事 | 每个 Phase 能 3 分钟讲透「问题→方案→验证」 |

---

## 九、面试叙事地图（升级后）

面试官问「你最近做了什么」时的回答结构：

```
30 秒版：
"灵枢是一个多 Agent 系统，我做了一个活的知识管道——规则从日志自动抽取、
验证后入库、在真实场景拦截错误。还做了完整的可观测性和多模型路由。"

3 分钟版：
"知识管道方面，我从 daily 日志里用信号词自动抽取候选规则，跟现有规则
做相似度去重，通过 guardian 验证后才入库。检索层做了向量+BM25+2-gram
三路融合，有完整降级链。收集了 5 个真实拦截案例。

可观测性方面，Prometheus 指标覆盖了任务成功率/LLM 延迟/token 消耗/
熔断触发；structlog 做了 JSON 日志 + trace_id 全链路追踪。

LLM 编排方面，做了模型路由层——配置表定义不同任务用什么模型，有降级链
和配额感知。输出解析器用 Pydantic 校验 LLM 返回的 JSON，脏输入能恢复。
上下文管理做了滑动窗口 + 摘要压缩。

这些都有测试——175 条全绿，加上这次新增的。"
```

---

## 十、风险与降级

| 风险 | 应对 |
|------|------|
| 百炼 embedding API 不可用 | 降级到本地 ollama nomic-embed-text，检索层设计时已考虑 |
| 模型路由接百炼 SDK 有兼容问题 | 保持 OpenAI 兼容接口，SDK 只是薄封装 |
| 前端模块化工作量大 | 可只做关键页面拆分，不追求完美组件化 |
| 环境依赖测试 mock 后行为不同 | 保留原测试为 integration 标记，新增 mock 版为 unit |
| 工时超预算 | Phase 1-2 必做（知识管道 + 可观测），Phase 3 次之，Phase 4-5 可砍 |
