# 灵枢的 Harness 工程分层（ETCLOVG 映射）

> 本文用 **ETCLOVG 七层分类法**审视灵枢的 harness 工程覆盖度——把「我有哪些能力」从散点清单变成有坐标的架构图。
> 分类法来源：《Agent Harness Engineering: A Survey》(2026) · <https://picrew.github.io/LLM-Harness/main.pdf> · 配套清单 <https://github.com/RUCAIBox/awesome-agent-harness>

---

## 1. 为什么用 ETCLOVG

自评「我的 agent 工程做得怎么样」有个天然缺陷：**没有坐标系就看不出哪里薄**。ETCLOVG 把 harness（除模型权重之外的一切工程）切成七层，映射 170+ 开源项目：

| 层 | 含义 | 一句话 |
|---|---|---|
| **E** Execution | 执行环境 | 代码/命令在哪跑、怎么隔离、副作用怎么兜 |
| **T** Tool | 工具接口 | 工具怎么注册、怎么暴露、怎么设计对模型友好 |
| **C** Context | 上下文 | 什么进上下文、什么时候压、花多少钱 |
| **L** Lifecycle-Orchestration | 生命周期与编排 | 谁调谁、怎么并行、怎么失败恢复 |
| **O** Observability | 可观测 | 一次 run 发生了什么、哪里慢了、为什么挂 |
| **V** Verification | 验证 | 怎么知道它对不对——判据、评测、溯源 |
| **G** Governance | 治理 | 预算、权限、版本、准入——谁能做什么、能做多少 |

---

## 2. 灵枢七层落位（实测，2026-10-16）

> 口径：`src/agent_harness/core/` 29 个模块 + 6 个子包（agents 2 / graph 2 / safety 5 / auth 4 / tools 17 / pipeline 8）。

### E — Execution
| 模块 | 职责 |
|---|---|
| `safety/mode.py` | 会话安全模式 `default`(沙箱优先) / `full`(全权全审计) |
| `safety/backup.py` | 写前自动备份（覆盖写存 `.bak`） |
| `safety/risk.py` | 危险操作确认矩阵（敏感路径/批量删/脚本/外发/网络） |
| `tools/permission.py` | 三级权限 + 路径遍历防护 |
| `idempotency.py` | **决策/执行分离** + 幂等 key（防重复副作用） |
| `tools/parallel.py` · `tools/timeout.py` | 执行隔离与超时 |

### T — Tool
| 模块 | 职责 |
|---|---|
| `tools/registry.py` | 工具注册表（40+ 工具，六大类） |
| `tools/` (17 模块) | web / desktop / rag_store / reasoning / semantic / skill_generator / rev_utils … |
| `mcp_server.py` | 工具以 MCP 协议对外暴露 |
| `plugin_loader.py` | 第三方插件加载 |
| `tools/mcp_health.py` · `mcp_utils.py` · `tool_config.py` | MCP 健康与开关治理 |

### C — Context
| 模块 | 职责 |
|---|---|
| `config.py` | LLM 端点 / 阈值 / 路径 |
| `budget.py` | **Token 预算管理**（上下文成本一等公民） |
| `tools/rag_store.py` | RAG 检索（向量 → BM25 降级） |
| `tools/memory_import.py` | 记忆导入 |
| `pipeline/session_store.py` | 会话持久化（RLock + 原子写入） |

### L — Lifecycle-Orchestration ★ 最厚
| 模块 | 职责 |
|---|---|
| `agents/supervisor.py` | Supervisor：分析 → 分配 → 验收 → 汇总 |
| `agents/workers.py` | Search / Analyze / Execute 三类 Worker |
| `graph/graph.py` · `graph/graph_multi.py` | LangGraph 图；**多 Worker 真并行 fan-out**（`ThreadPoolExecutor` + `as_completed`） |
| `pipeline/circuit_breaker.py` | 三重熔断器（LLM 调用点 → 熔断短路 finalize） |
| `degradation.py` | 降级矩阵（每功能一条降级链） |
| `retry.py` | 指数退避重试 |
| `hedge.py` | provider 对冲：主挂了 failover，过 P95 发第二副本 |
| `tasks.py` | 任务队列（SQLite 持久化 + 状态轮询 + 重试） |
| `pipeline/cancel.py` | 可中断任务 |
| `agent_cron.py` | 定时任务调度 |
| `skill_lifecycle.py` | 技能状态机 trial/active/stable/retired |

### O — Observability
| 模块 | 职责 |
|---|---|
| `agent_log.py` | per-session agent run 轨迹 |
| `audit.py` | API 操作审计（trace_id/params/duration/result） |
| `log_kit.py` | 结构化日志（替代 print） |
| `metrics.py` | Prometheus（HTTP/Agent 耗时/Token 消耗） |
| `trace_replay.py` | 轨迹冻结 → 回归用例（真实 run → 可回放样本） |
| `failure_attribution.py` | 失败归因（决定性错误） |
| `health.py` | 启动自检（全链路依赖） |
| `pipeline/tracing.py` | 管线追踪 |

### V — Verification
| 模块 | 职责 |
|---|---|
| `module_eval.py` | core 模块评测（角色接口 + 行为评测） |
| `online_eval.py` | 在线采样：离线均值 − 生产均值 |
| `eval_isolation.py` | 判据隔离（评测能奖励什么对被测 agent 不可见） |
| `judge_registry.py` | judge 注册/冻结（防量尺漂移） |
| `safety/provenance.py` | 溯源与准入 |
| `reproducibility.py` | 可复现信封（每次 LLM 调用可重放） |
| `slo.py` | SLO 定义 + 可测检查 |
| `docs/knowledge-pipeline/` | 防复现知识管道（guardian 规则库 + 检索层，38 测试） |

### G — Governance
| 模块 | 职责 |
|---|---|
| `budget.py` | Token 预算（WARN/ASK/STOP 软限制） |
| `auth/` | JWT(RBAC) + `credential_broker.py` + `api_security.py` |
| `safety/admission.py` | 准入闸 |
| `versioning.py` | 配置 / Prompt 版本戳 |
| `exceptions.py` | 结构化错误分层 |
| ADR-001/002/003 | 架构决策记录（边界/禁硬编码/打包约束） |

---

## 3. 覆盖度结论

**七层全覆盖，无空白层。** 差异化最深的两层：

- **L（编排）**——不只是「跑起来」，而是「跑飞了能停、挂了能降级、慢了能对冲」：熔断 + 降级 + 对冲 + 幂等 + 可取消 + 任务队列六件套齐备。这是灵枢的核心深度叙事。
- **V（验证）**——判据隔离、judge 冻结、在线采样、轨迹回放，构成一个**自评闭环**。

**两处真实薄弱点**（自评发现，内部对账口径 —— 证据均为实测）:

| 层 | 现象 | 证据（实测） |
|---|---|---|
| **G** | 成本软限制**有芯未接线**——`budget.py`（234 行：WARN/ASK/STOP 三层 + kill switch + cache 命中率 + 成本预估 + DailyBudget）功能完整，但**生产编排链零引用**；实跑时的成本兜底靠 `circuit_breaker.py` 的 token 硬阈值熔断 | `grep -n 'TokenBudget\|DailyBudget' src/ --include=*.py`（排除定义与评测适配）→ **0 命中** |
| **V** | 验证只有**自评闭环，无外部坐标**——全是自己跟自己比，没有第三方 benchmark 分数 | `grep -rniE 'tau2\|osworld\|terminal.bench\|swe.bench\|benchmark' src/ tests/ --include=*.py \| wc -l` → **0** |

> 这两条不是「没做」，是「做了但没拧到最后一扣」——正是 harness 工程里最容易被忽略的深度。**注意**：旧 ADR-001 里「`budget` 在代码里没有执行原语」的说法已**过时**（模块后来补齐了原语），当前准确状态是「原语齐备、未接进调用链」。

---

## 4. 演进方向

1. **V 层接外部坐标**：接入 `τ²-bench` 一类第三方 benchmark，让「灵枢好不好」有一个**别人也能复现**的数字（自证 → 他证）。
2. **G 层预算接线**：把 `budget.py` 的三层软限制（WARN/ASK/STOP）接进编排调用链，与 `circuit_breaker.py` 的硬阈值熔断形成「软提醒 + 硬熔断」双闸。
3. **C 层上下文显式化**：目前 Worker 输出靠 2000 字截断粗控；下一步做显式上下文预算 + 压缩策略（KV-cache 命中率采集）。

---

*本文的七层分类法引用一手综述（LLM-Harness Survey, 2026）；模块落位均为实测（模块 docstring + 源码结构），非设计意图。*
