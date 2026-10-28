# 灵枢 — 演示指南

> 面向面试与技术评审。三条路径按「让人相信它真的能干活」的速度排序。
> 本文所有数字都能用文中给的命令自行核对，对不上就是文档的错。

---

## 路径 A：一条命令，看证据链（约 2 分钟，最快）

README 首屏那句「每条结论都带可回溯的来源（工具名 + 原始 URL + 抓取时间），核不到的显式标注」，这条命令就是它的实物验证。

### 前置

需要**一个 OpenAI 兼容的 LLM 端点** + **一个搜索来源**。两者都不内置，必须自带：

在设置中选择实际后端类型（本地 Ollama 填 `ollama`），并检查模型与搜索均可用。运行脚本与 Web 页面都应读取同一份运行时配置。

- LLM：本地 ollama、DeepSeek、OpenAI 任选其一（设置页填地址 + 模型名）
- 搜索：DeepSeek 搜索 key 或 Tavily key（设置页「🔎 搜索来源（自带 Key）」粘贴，密文落盘，界面只显示「已配置」）

### 运行

```bash
pip install -e ".[dev]"
python scripts/demo_research.py "2025 年中国新能源汽车销量" --json
```

实测输出形态（数字随问题与搜索结果变化，格式固定）：

```
[demo] 报告：outputs/demo-report-20261023-004122.html  (16,7xx 字符, 总耗时 300.xs)
[demo] 来源记录：15 条 / 去重 URL 15 个 / 工具 search,fetch
[demo] 来源片段数字比对已执行（不代表事实核验），未匹配 0 项
{"out": "outputs/demo-report-....html", "summary": {"provenance_count": 15, "unique_urls": 15, ...}}
```

### 退出码是契约，不是「能跑就算过」

| 码 | 含义 | 为什么这么定 |
|---|---|---|
| `0` | 生成成功，且存在来源记录 | 内容与引用仍待复核 |
| `2` | 报告生成了，但**零来源** | 可读不可核对，属于「看起来能用但不可信」，必须告警而不是静默成功 |
| `1` | 失败（LLM 不可达 / 报告为空 / 异常） | 不落任何半截文件 |

### 证据链在哪看

打开生成的 HTML，翻到文末「事实核验说明」：每条结论标注核验状态（已核 / 部分 / 未核），并给出工具名、原始 URL、抓取时间。核不到依据的项列在「未找到强相关依据」里，不隐藏。

`--json` 输出的 `summary` 可用于检查链路（`provenance_count` / `unique_urls` / `factcheck_checked` / `not_found`）。退出码 0 和来源数量只表示生成及溯源链路有产物；搜索结果不全是一手来源，数字核验也不等于语义核验。演示前仍需逐条检查正文与引用、访问日期及字数约束。

不想现在跑也没关系：`docs/samples/demo-report-2025-nev.html` 是某次真实运行的产物（15 条一手来源），直接打开就能看到「事实核验说明」长什么样。

> 设计取舍：脚本**不内置假数据、不提供离线演示模式**。跑不出真实结果就退出码 1 并说明原因，演示与真实能力脱节比演示失败更糟。

---

## 路径 B：Web UI 全流程（约 5 分钟）

### 启动

```bash
agent-harness serve
# 浏览器打开 http://127.0.0.1:8788
```

端口默认 8788（`HARNESS_API_PORT` 可改），默认只绑 `127.0.0.1`。

### 演示顺序

1. **设置页（☰ → 设置）**：先展示自带 Key 的两个入口（模型端点、搜索来源）。界面只报「已配置 / 未配置」，不回显任何密钥片段（前 4 后 4 也算泄掉一半）。
2. **调研主流程**：输入一个具体问题（别用「介绍一下 AI」这类没有可核对结论的），观察 thinking steps：Supervisor 拆解 → Worker 并行（搜索 / 抓取 / 分析）→ 汇总。
3. **产出报告**：同一份 HTML 模板，带目录、引用编号、事实核验块。
4. **客服场景**（`/cs-demo`）：SSE 流式回复、场景卡片、消息评价，展示同一套编排跑在另一个业务形态上。

### 脚手架（可选加分）

```bash
python scripts/create-app.py my_app
```

一键生成新 Agent 应用的骨架。

---

## 路径 C：Docker

```bash
docker compose up -d
```

起 LingShu + SearXNG 两个服务。注意 compose 里的 `SEARXNG_BASE_URL` 是**容器内**变量（`http://localhost:8080`），宿主访问仍走 `127.0.0.1:4000` 映射。

SearXNG 三个常见坑（都是真踩过的）：

1. `cap_drop: ALL` 缺 `DAC_OVERRIDE` → 容器 `Restarting (127)`
2. `settings.yml` 的 `search.formats` 只有 `html` → 请求 `format=json` 直接 403
3. 出网：代理只绑 `127.0.0.1` 时容器内全 timeout；绑通则境外 IP 容易被 CAPTCHA

诊断口诀：响应里的 `unresponsive_engines` 出现 `timeout` 是出网断，出现 `CAPTCHA` / `too many requests` 是出网通但 IP 被封，两者修法完全不同。

---

## 数字（自查用）

| 项 | 值 | 怎么自己核 |
|---|---|---|
| 测试 | 以当前 pytest 实测为准（离线回归与真实模型彩排分开记录） | `pytest` |
| 版本 | 以 `pyproject.toml` 为准 | `grep '^version' pyproject.toml` |
| 版本记录 | 以当前 CHANGELOG 为准 | `grep -c '^## ' CHANGELOG.md` |
| 注册工具 | **37 个**（read-only 25 / reversible 4 / irreversible 8） | `python -c "import agent_harness.apps.research.api; from agent_harness.core.tools.registry import TOOL_REGISTRY as R; print(len(R))"` |

> 37 是**完整应用导入链**下的注册数。单独 import 某个工具模块可能多注册（只在该模块内注册的工具），那不是运行时形态。

---

## 面试 Q&A

| 问题 | 回答要点（都能在仓库里指到代码） |
|---|---|
| 架构亮点？ | Supervisor-Worker（LangGraph 图）；工具按 privilege 三级分闸（read-only / reversible / irreversible）；抓取走 L0→L3 降级链 |
| 怎么保证结论可信？ | provenance（工具名 + URL + 抓取时间）+ 三态核验；零来源时 demo 以退出码 2 告警而不是报成功 |
| 多用户隔离做到哪？ | JWT + RBAC + 会话 `owner_id`；知识库 collection 作用域在**数据层**用 `contextvars` 兜底（agent 工具也绕不过）；并发信号量 + 限流 |
| 为什么单 worker？ | 会话 / metrics / 取消 / LLM 缓存都是进程内状态，多 worker 会状态分裂。写在 AGENTS.md 的已知边界里，不是漏配 |
| 成本怎么控？ | 本地模型干大部分活；搜索按次计价（DeepSeek 搜索 1000 次 5 元）；LLM 缓存 key 含 thinking / max_tokens 维度 |
| 安全上做过什么？ | 密钥密文落盘、日志脱敏、审计旁路（失败不杀业务但必告警）、demo 模式写类工具默认全拒、CSP / CORS 收敛 |

---

## 常见失败与排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `[demo] 报告为空或 LLM 不可用` | LLM 端点不可达 / 模型名错 | 设置页核对地址与模型名；本地 ollama 确认实际端口 |
| demo 退出码 `2`（零来源） | 搜索链全部失败 | 依次查：搜索 key 是否配置 → SearXNG 是否在跑 → `format=json` 是否 403 → 容器能否出网 |
| 设置页「未配置」但 `.env` 有 key | 优先级：`config.json` 密文 → `llm.api_key` → 环境变量 | 在设置页粘贴一次，会以密文落到 `~/.agent-harness/config.json` |
| `agent-harness serve` 起不来 | 端口被占 / 依赖缺失 | `agent-harness --help` 确认子命令；换 `HARNESS_API_PORT` |

## 内容核查边界

退出码0只表示生成成功且存在来源记录。报告会显示待人工核查；无法唯一绑定URL的引用显示未解析，不能按搜索顺序推定。已经结束的预测期及混合市场范围会提示复核。详见[报告核查说明](report-verification.md)。
