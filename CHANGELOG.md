# Changelog

## Unreleased

### Fixed — LLM 配置收敛到单一入口（4 处调用点，3 处在主链路）
- 🔍 **同一处缺陷有 4 个副本**：`supervisor.py`（模块级 import 快照，docstring 自认「绕过 `pipeline/llm.py`」）、`graph.py`、`comic_agent.py`（同为模块级）、`cs_agent.py`（函数内 import，同样只在首次生效）。修好 `workers.py` 并不够——规划层仍读进程启动时的配置
- 🚫 **思考轨迹不再冒充答案**：另有 3 处 `content or reasoning_content[-500:]`，答案为空就把思考尾段当答案返回（`supervisor.py` / `graph.py` / `comic_agent.py`）
- 🔧 在 `pipeline/llm.py` 收口三个入口，其余模块只许用它们：`runtime_endpoint()`（每次调用重读配置）、`extract_answer()`（复用 `_extract_openai_content`，答案与思考两通道永不合并）、`thinking_payload()`（`off` 显式禁用 / `on` 不发该字段）
- 📢 未配置端点时打印原因并返回空，不再向空 URL 发请求
- 🧪 `tests/test_llm_single_source.py` 9 用例

### Fixed — Worker 层 LLM 配置分裂（信任级：报告混入占位内容）
- 🎭 **一次调研里跑了三种后端**：实测一次 demo 调用串里同时出现 `model: "mock"`、`127.0.0.1:18888`（连接被拒）、`qwen3.5:9b` —— 报告里真实分析与占位内容混在一起。根因是 `workers.py` 用 `config` 模块的 **import 快照**（进程启动那一刻的 `.env`），而 `llm.py` 已改为运行时 `get_llm_config()`：同一进程两套配置来源
- 🔧 改为 `get_llm_config()`；简单任务只降预算、**不再切到第二个端点**（切端点正是配置分裂的成因）；未配置时直接说明并返回空，不发请求
- 🧠 思考策略与 `llm.py` 统一：默认发 `thinking:{type:disabled}`，`HARNESS_LLM_THINKING=on` 时不发该字段并把预算抬到 ≥4096
- 🚫 删掉 `content or reasoning[-500:]` —— 答案为空时曾把**思考轨迹尾段当答案**返回（ENG-23 在 `llm.py` 修过，此处的同类缺陷漏修）
- 🧪 `tests/test_workers_llm_config.py` 5 用例

### Fixed — searxng 部署配置（空 volume 起不来 + 搜索接口 403）
- 🧱 `cap_drop: ALL` 没有补 `DAC_OVERRIDE`：空 volume 首次启动时 entrypoint 写不进 `/etc/searxng/settings.yml`，容器进 `Restarting (127)` 循环
- 🔌 默认 `settings.yml` 的 `search.formats` 只有 `html`：`format=json` 一律 403
- 🌐 容器内无代理时国外引擎返回空文档（`bing: parsing error`），国内引擎高频抓取立刻 `CAPTCHA` → 表现为 HTTP 200 + `results=0` 的"假成功"。compose 增加 `HTTP(S)_PROXY` 指向宿主代理

### Fixed — 定时任务不再重复、漏跑、静默停摆（ENG-39 / ENG-35）
- ⏰ **重复触发**：去重靠比较 `last_run` 与当前 `tm_min`/`tm_hour`，`30 * * * *` 在 xx:31 会再跑一次。改为持久化 `next_run`、只判 `now >= next_run`，触发后从 `now` 重算（不是从错过的槽位）——停机三天也只补一次，不连环补
- 🔤 **语法缺失**：只认 `*`、`*/n`、单值，`1-5` / `1,3,5` 会 `int()` 抛 `ValueError` 后静默 `return False` —— `0 9 * * 1-5`（工作日简报）永不触发且无任何日志。现支持范围/列表/步长；坏字段只让该字段不命中，不炸 tick。weekday 采用 POSIX 口径（周日=0，7 为别名，周一=1），docstring 写明
- 🔁 **无补跑**：`next_run` 字段早已存在却从未被赋值。现在 `add_task` / `update_task` 都会计算，`_tick` 发现 `next_run` 已过则补跑一次并 `warning` 记 `missed_at`
- 🔇 **静默停摆**：`_loop` 的 `contextlib.suppress(Exception)` 吞掉 tick 的一切异常，调度器停了也没痕迹 → 改为 `logger.error` 并继续循环
- 📝 `update_task` 白名单只放行 `schedule/prompt/enabled`，`last_run`/`next_run`/`run_count` 显式传入会被丢弃 → 扩白名单
- 🚧 **ENG-35 多 worker 守卫**：`main()` 曾把 `HARNESS_WORKERS` 原样传给 uvicorn，而会话/指标/取消/LLM 缓存与调度器都是进程内状态 —— workers=N 等于 N 份调度线程、cron 成倍触发。新增 `_resolve_workers()`：>1 强制降为 1 并打印说明（不静默）
- 🧪 `tests/test_eng39_cron.py` 17 用例 + `tests/test_eng35_workers.py` 4 用例

### Fixed — 前端 PWA 与会话恢复（FRONT-1/2/3）
- 📱 **FRONT-1**：`sw.js` 预缓存指向**旧客服应用**的路径，主应用 PWA 实际没生效（装着旧 SW 时还可能回错资源）。现对齐当前应用资源、`allSettled` 逐个 add（单个 404 不再搞挂整单安装）、导航请求网络优先、`activate` 清理旧缓存 + `clients.claim()`；后端对 `sw.js` 回 `Service-Worker-Allowed: /` 与 `Cache-Control: no-cache`——sw.js 位于 `/static/` 下，不发该头则 `register(scope:'/')` 被浏览器拒绝；SW 脚本本身被 HTTP 缓存时 bump `CACHE_NAME` 也推不下去。补 `manifest.json` 并给 `.gitignore` 的 `*.json` 加豁免（否则部署后仍 404）
- ✂️ **FRONT-2**：会话恢复时助手消息被 `slice(0, 500)` 腰斩——长调研报告回到界面只剩残句，DOM 里也拿不到后半段（复制/搜索都丢）。改为渲染全文
- ⚡ **FRONT-3**（低风险部分）：7 个脚本加 `defer`，内联 `init()` 挪到 `DOMContentLoaded`（defer 脚本执行完才触发，顺序语义不变；只加 defer 不改内联会白屏）。1196 行的 `tabs.js` 不拆——重构风险大于收益
- 🧪 `tests/test_frontend_contract.py` 10 用例

### Changed — 思考模式按需开启，不再全局禁用
- 🧠 此前 `call_llama` / `_post_cloud` 硬编码 `thinking:{"type":"disabled"}` —— 等于**对所有请求永久禁思考**，需要推理的任务也拿不到思考带来的质量。现改为按 `HARNESS_LLM_THINKING=on` 开启：开启时不发 disabled 字段，且 `max_tokens` 自动抬到 ≥4096（思考与答案共用预算，不抬就等于让思考把答案挤掉）
- 🔁 新增预算不足识别与重试：`content` 为空 + `finish_reason=length` + 有思考轨迹 → 判为「思考吃光预算」，加预算重试一次（上限 8192）；仍失败则明确报错并**不写缓存**。普通模型截断（无思考轨迹）不重试，避免盲目烧钱
- 🔤 思考字段兼容三家命名：`reasoning_content`（DeepSeek/vLLM）、`reasoning`（ollama 新版 `/v1`）、`thinking`
- 🧪 `tests/test_llm_thinking_mode.py` 10 用例（开关、字段兼容、预算重试、达到上限后不再重试、截断不误重试）

### Security — 密钥与日志落盘（SEC-18/19）
- 🔐 **SEC-18**：`config.json` 曾把 `llm.api_key` 明文 `json.dump` 落盘。新增 `core/pipeline/keystore.py`，用 `PBKDF2(machine_id|username)` 派生本机 KEK 对单字段加密，落盘 `api_key_enc`，启动解密回内存。**否决了「剥离到环境变量」方案**——环境变量是进程内存态，重启全丢，桌面应用每次重开都要重填 key。老明文配置读一次即迁移（幂等）；解密失败明确 ERROR 并置空，不静默降级；配置目录 best-effort 收紧（POSIX 700 / Windows `icacls`，失败只 warning 不阻断启动）。防的是「配置文件被拷走」，不是「本机同账户被攻破」——后者要 OS keychain/TPM，明确不做
- 🧼 **SEC-19**：`audit()` 曾把工具实参（含 key、URL 签名）明文写盘。新增 `core/safety/pii_redact.py` 双层规则：键名（`api_key`/`token`/`secret`/`password`/`authorization`/`cookie` 及变体）+ 值形态（`sk-*`/`Bearer`/长 hex/长 base64/手机号/身份证/邮箱/银行卡）。单独成函数可单测，`HARNESS_AUDIT_REDACT=0` 可关
- 🧪 `test_sec18_config_keystore.py` 11 + `test_sec19_audit_redact.py` 31（全量 915 passed）

### Fixed — 流式路径的并发与取消（ENG-19/21）
- 🚦 **ENG-19**：流式分支（前端默认 `stream=true`）直接起 daemon 线程、**完全绕过 `_agent_semaphore`** —— 并发上限形同虚设；客户端断开后线程继续跑满超时白烧 token。现 handler 层 `asyncio.to_thread(sem.acquire(timeout=...))` 纳入闸门（直接 acquire 会阻塞 event loop；`Semaphore.acquire` 首参是阻断标志，`timeout` 必须关键字传），拿不到返回 503；`pre_acquired` 标志保证名额由持有者释放一次，计数配平；`_stream_progress` 全程 `try/finally`：断开 → `cancel_task` → `join(5s)` → 清注册表，半截 session 不落盘
- 🎯 **ENG-21**：`cancel.py` 曾是**全局单点** event —— 并发下后启动的任务覆盖它，取消 A 变成取消 B（或谁都取消不了）。改为按 `task_id` 注册表，`session_id` 透传到 supervisor/worker/graph_multi 的取消检查点；旧 API 保留为 CLI 兜底
- 🐛 顺带修：`acquire` 超时返回 `False` 时曾仍置 `acquired_here=True` → 每超时一次并发上限就 +1，**闸门被自己的错误处理漏光**。现检查返回值并在排队超时时返回明确的「繁忙」事件
- 🧪 `test_eng19_sse_concurrency.py` 10 用例

### Changed — 评测口径加事实性维度（ENG-40）
- ⚖️ 原评分 keyword 独占 40 分：「市值 1 亿」和「市值 1 万亿」只要都含关键词就同分——测的是「输出了相关词」，不是「内容对」。现拆为 keyword 20 + **factual 20**（总分仍 100）
- 断言写在任务里（`task["factual"]`）：`must_have_numbers`（该说的没说）/ `must_not_have_numbers`（编造数字）/ `must_cite_domains`（无来源裸答）/ `requires_fact_check_note`（无来源不自标未核验）
- 数字口径复用 `_normalize_number_tokens`：`1.2万亿` ↔ `1200000000000` 同口径；年份按既有判据剔除
- 无断言的任务给满分且 `asserted: False`，不假装测了；0-9 个位数断言被数字提取器剔除 → 显式跳过并记 `skipped_insignificant`，不静默判 fail
- 🧪 `tests/test_eval_factual.py` 12 用例（全量 864 passed）

### Fixed — 静默产出错误三连（L2 P1）
- 🧠 **ENG-23**：`call_llama` 在 `content` 为空时曾取 `reasoning_content[-200:]` 当答案返回 —— Qwen3 系默认思考模式下 `content` 恒空，用户拿到的是半截推理。改为返回空串信号（与后端不可达路径同构，上层已有降级语义）+ stderr 提示；`reasoning > max_tokens*0.8`（R1 系被截断）才返回但加前缀 `[推理截断输出]`，且不进缓存。`_post_cloud` payload 补 `thinking:{"type":"disabled"}`（此前只有本地路径有）
- 📄 **ENG-18**：`source_map` 循环内 `title = url.rsplit(...)` 覆盖了已转义的报告标题 → `<h1>` 与页面 `<title>` 变成最后一个引用的 URL 路径尾部且未转义。改用独立变量 `ref_title`
- 📊 **ENG-26**：Markdown 表格遇到 `| --- |` 分隔行直接 `continue`，上一行表头被渲染成 `<td>` —— 表格没有表头。改为暂存上一行、遇分隔行转 `<th>`；无分隔行的表格不误判；文末残留行与表格闭合补齐
- 🧪 `tests/test_l2p1_llm_report.py` 10 用例（回退源码 4 条转红）

### Added — 溯源三态与数字核验（ENG-29/32/33/34）
- 🔗 **provenance**：从 worker 成功步骤解析一手来源（工具名 + URL + 抓取时间 + snippet）；`search` 对齐 `标题: 摘要 [url]`、`web_scrape` 对齐 `{"title","body","url"}` JSON（含被截断 JSON 的字段兜底）——**模型自述不算证据**
- ✅ **v2 核验**：报告数字与来源正文双向归一比对，差集即「未找到依据」；有 provenance 走 v2，无则回落 v1
- 🏷️ **三态标注**：未找到依据（数字 + 来源 URL）/ 未核验（skip 原因中文化：轮次触顶、无可核对数字、无来源正文、校验异常）/ 通过不变形 —— skipped 不再静默通过
- 🔢 **中文单位归一**：万 / 亿 / 万亿 + 复合单位按乘数换算；「2024 年」按时间限定排除（年份不是待核验数据，否则跨年引用必误报）
- 🖼️ `generate_report_html` 支持 `provenance` / `factcheck` 三态汇总块；`/v1/reports/formalize` 接受调用方传入（agent 侧 state 未落盘，默认不传 = 无块，不假装已接线）
- ✂️ 只读抓取类工具输出截断上限 500 → 2000（年报正文被截断会导致数字查不到）
- 🧪 `tests/test_finalize_fact_check.py` 37 用例（v1 14 条全保留）

### Fixed — 熔断器三态状态机（无进展检测真正生效）
- 🔥 `record_output(wname, output)`：worker 名改作分组 key，不再拼进被比较文本——旧实现每次前缀不同，唯一指纹恒 > 1，**无进展检测实际永不触发**
- 🔁 新增 CLOSED / OPEN / HALF_OPEN 三态：硬熔断（token 超支/墙钟超时）永久停；软熔断（无进展）冷却 60s 后半开放行一次探测，探测通过恢复 closed、失败重置冷却（原实现触发后**永不恢复**）
- 🧵 全部可变状态加锁；`_tripped` 属性保留兼容旧读点
- 🎯 滚动窗口长度跟随 `max_no_progress`（原为模块常量，构造参数是死参数）
- 🧪 新增 `tests/test_circuit_breaker.py`（10 用例，模块覆盖率 98%）；调用点同步 graph / graph_multi / module_eval

### Fixed — 鉴权撤销语义与写路径越权
- 🔒 SEC-4：JWT middleware 的 Bearer 分支补 `is_session_valid(jti)` —— 登出后 access token 立即 401（此前只验签不查表，撤销语义与 refresh 侧分裂）
- 🔒 SEC-15：`DELETE /v1/sessions/{id}` 与 `POST /v1/sessions/{id}/meta` 补 owner 校验，与 messages/export/logs 读路径同策略；非 owner 与不存在同回 404（不做存在性 oracle）
- 🧪 新增 `tests/test_authz_session_write.py`（4 身份 × 删/改 双向矩阵）、`tests/test_auth_session_revocation.py`（登出即失效 + 伪造 token 负向控制）；回退源码验证过 6 用例转红

### Fixed — knowledge 面跨用户隔离（SEC-16）
- 🔒 collection 改为按用户物理隔离：普通用户 `user_<uid>_<name>`，admin 直通原名；`collections / upload / query / delete / import` 五个端点全部收口，列表剥前缀展示
- 🔒 非 owner 拿他人物理名删 → 再套一层前缀 → 404（不做存在性 oracle）
- 🔒 chat 路径的知识库自动注入按 scope 过滤——此前把全库内容注入任意用户的 prompt
- 🐛 `rag_store._coll_dir()` 内部 `makedirs` → `delete_collection` 对不存在的 collection 也返回成功，且 query/info/delete 等读路径会制造空目录污染列表；改为默认不建目录，仅写路径建
- 🐛 `_safe_collection_name()`：collection 名即目录名，拒绝 `../` / 盘符 / NUL（路径穿越）
- 🐛 chat 流式路径 `_save_session` 不写 `owner_id` → 用户回看自己的会话被 404
- 🐛 补 `python-multipart` 依赖：缺它 upload 端点解析 form 直接 500
- 🧪 新增 `tests/test_kb_owner_isolation.py`（10 用例：A/B/admin/guest 矩阵 + import 端点 + chat 注入双向断言）

### Changed — 仓库卫生 / 对外文档
- 内部工作准则与自查文档移至本地 `docs-internal/`（不再入库）：AGENTS 内部版、用户征集设计、市场对齐、评审记录、架构自查、改造蓝图、升级计划、运维指标
- `AGENTS.md` 重写为面向开发者的中性约定（结构 / 本地开发 / 测试基线 / ADR / 已知设计边界）
- `render.yaml`（`DISABLE_AUTH=1` 公网无认证 demo）出库，存档 `docs-internal/`
- 移除 `chromadb` 死依赖（全仓库零 import；RAG 实为 numpy 余弦 + BM25）
- `site/` 52 个生成产物解除跟踪（`.gitignore` 的 `/site/` 对已跟踪文件无效）
- README：定位收窄到调研主线；新增「目标场景（含可证伪声明）」与「部署安全边界」

## v0.75.3 — 2026-09-07

### Fixed — Supervisor-Worker 架构加固（Cognition P1/P2 对照，Day 审查）
- 🧭 replan 上下文含失败原因：worker errors + 调度级 errors 全进 replan 决策（原只见 300 字输出摘要 → 会重派同一失败任务）；部分失败 worker 提示"只补缺失勿重复"
- ⚠️ worker 汇报反映真实完成度：_failed_step_summary 统计 validation 失败步骤，synthesize 强制开头声明失败/跳过项（原静默跳过失败步骤仍报成功）
- 🛡️ 副作用 Worker 串行化：analyze/execute 声明 side_effect，多个副作用 Worker 同时调度时只保留 1 个并行、其余串行（防文件系统/桌面/ComfyUI 队列冲突）
- 🔍 judge 上下文失败时附原始工具证据：collect 判定不再只依赖 worker 自报总结（self-report 核对）
- 🧪 新增 8 测试（tests/test_arch_cognition.py），全量 183 passed / 8 failed(环境依赖) / 2 skipped 零回归

## v0.75.2 — 2026-09-04

### Fixed — 安全护栏 fail-closed（Day 5 硬化）
- 🔒 registry.py 两处 ImportError 从 fail-open（静默无护栏）改 fail-closed：权限模块不可用 → 抛错拒绝；safety 模块不可用 → 不可逆操作直接拒绝 + CRITICAL 日志（可逆操作警告放行防卡死）
- 🧪 新增 3 测试证明 fail-closed：safety 不可用拒 irreversible / 可逆仅告警 / permission 不可用抛错（sys.modules=None 模拟导入失败）
- 修正：test_safety.py 实为 27 测试（此前误判 0），本版 +3 = 30

## v0.75.1 — 2026-09-04

### Fixed — 三重熔断接线（Day 3-4 硬化）
- 🔥 熔断器真正喂数据：LLM token 总账（llm/supervisor/worker 三调用点统一 bump）+ graph 每轮差值喂 `add_tokens` + 每 worker 输出喂 `record_output` → **token 预算 / 无进展检测从"从未触发"变真触发**
- 🛑 熔断能在运行中拦住：`supervisor_route` 检测熔断即 finalize（原实现只在 invoke 结束后查一次，失控拦不住）
- 🔁 retry.py 从孤儿变实装：`pipeline/llm.py` 的 POST 挂 `with_retry(3, 指数退避+抖动)`，全失败才降级/返回空
- 🧪 新增 9 测试（tests/test_circuit_breaker_wiring.py）：token/无进展/重置/增量喂入/route finalize/重试成功/重试放弃/优雅空返回；全量 164 passed 零回归
- 🐛 graph.py 单 Agent 路线补 `_circuit_breaker` 初始化（原从未实例化→熔断永久失效）

## v0.75.0 — 2026-08-21

### Added — 重试机制 + 预算控制 + 安全审计
- 🔁 Agent 重试机制(失败自动重试,带退避)
- 💰 预算控制(token/成本软限制)
- 🔒 安全审计(操作全量审计追踪)

## v0.74.0 — 2026-08-20

### Added — P1-P3 保底能力补全
- 补齐核心保底能力,覆盖 P1-P3 优先级链路

## v0.72.0 — 2026-08-20

### Added — Cloud Demo 适配
- ⏱️ CS 超时 2s + 模板降级
- 🎬 Demo 横幅展示

## v0.71.0 — 2026-08-20

### Added — Render 部署配置
- 📦 render.yaml + start_prod.py 线上部署就绪

## v0.69.0 — 2026-08-19

### Added — 小程序多用户 + JWT 自动登录
- 👥 多用户支持
- 🔑 JWT 自动登录

## v0.68.0 — 2026-08-19

### Fixed — CS 场景全链路修复
- ✅ switchTab + logistics + 参数传递 修复

## v0.67.2 — 2026-07-17

### Fixed — CS API 超时修复
- ⏱️ **CS Agent LLM 超时 8s** — 新增 `_call_llm` timeout 参数，CS 模式 LLM 超时后快速降级模板回复
- 🐛 **`_call_llm` 硬编码 300s 超时** — 改为使用参数传入，其他调用保持原有超时
- 💬 CS 客服和小程序普通对话现在都能秒级回复

## v0.67.1 — 2026-07-17

### Fixed — LLM 配置修复
- 🐛 **graph_multi.py 导入路径** — `agent_log` 和 `supervisor` 的 import 路径修复（代码分层后未更新）
- 🐛 **环境变量名错误** — `MODEL_LLAMA` 改为 `HARNESS_MODEL_LLAMA`，使 Ollama 配置生效
- ✅ **Ollama qwen3.5:9b 对接验证通过** — 多 Agent 管线全链路可用

## v0.67.0 — 2026-07-17

### Fixed — 对话连接问题彻底修复
- ⏱️ **超时 30s→10s** — 避免 LLM 不可用时请求长时间挂起
- 🔄 **三级回退链路** — normal/research 模式失败后自动回退到 CS API（有模板回复）
- 📱 即使后端没有 LLM，所有对话模式都能正常回复（使用 CS 模板）
- 🧹 删除过时错误提示，统一走回退链路

## v0.66.1 — 2026-07-17

### Fixed — 小程序连接修复
- 🐛 **端口自动回退** — 首页健康检查失败时自动尝试另一个端口（8765↔8788），自动保存正确配置
- 🐛 **start_server.bat** — 修复中文编码乱码，改为纯 ASCII 版本

## v0.66.0 — 2026-07-16

### Added — 小程序真机可用
- 📱 **真机调试支持** — 后端绑定 `0.0.0.0`，手机同一 WiFi 下可访问
- 🖥️ **开启手机预览.bat** — 双击启动后端，自动显示电脑 IP 和配置指引
- 🔧 **设置页优化** — 端口改为 `:8788`，新增真机调试提示，替换过时说明
- 🎯 默认 API 地址从 `:8765` 改为 `:8788`（主服务端口）

## v0.65.0 — 2026-07-16

### Fixed — 主应用体验修复
- 🐛 **连接失败友好提示** — fetch 后检查 `resp.ok`，非 200 时读取错误体并显示中文友好提示，而非抛出原始 HarnessError
- 🗂️ **侧边栏展开收起** — 新增 ☰ 按钮，点击收起/展开对话列表，状态持久化到 localStorage
  - 收起时侧边栏平滑消失，按钮浮动到左上角
  - 收起时聊天区域全屏，适合单任务专注模式
  - CSS transition 动画过渡

## v0.64.0 — 2026-07-16

### Added — 小程序 UI 打磨
- 🎨 **统一 UI 风格** — 所有页面 #2563eb 主色调，卡片圆角 12px，阴影统一
- ⏳ **加载状态** — report-detail 页加载中/错误/空状态完整处理
- 📱 **安全区域适配** — 全部页面添加 safe-area-inset-bottom 刘海屏适配
- 🧹 **WXSS 清洗** — 统一 page 背景色、字体大小层级、间距规范
- 🔧 **页面 JSON** — 补充缺失的 .json 配置

## v0.63.1 — 2026-07-16

### Fixed — 微信小程序 Bug 修复
- 🐛 **HarnessError 处理** — 后端错误信息不再显示给用户，改为友好提示
- 🐛 **对话历史逻辑修复** — 消息切片算法修复，防止漏发/多发历史
- 📱 **客服图标缩小** — 36px→28px，适配手机屏幕
- 🆔 **关于页脱敏** — 开发者名改为网名 `kisaragiy`，微信改为 `RT2965783750`

## v0.63.0 — 2026-07-16

### Added — 微信小程序全功能实现
- 📱 **7 个页面全部调通** — 首页/对话/客服/报告/详情/设置/关于
- 💬 **对话页三种模式**：普通对话 / 调研助手 / 智能客服，自动切换 API 端点
- 🏠 **首页** — 5 张功能卡片可点击跳转，服务状态绿/红点实时检测
- 🎯 **客服场景** — 6 个场景卡片直达对话，带入预设问题
- 📊 **报告列表+详情** — API 获取，rich-text 渲染 HTML，支持分享
- ⚙️ **设置页** — API 地址/Token 输入保存，测试连接按钮
- 💾 **会话持久化** — 按模式分开存储对话历史
- 🎨 **统一 UI** — 蓝色主调 #2563eb，卡片圆角，加载动画，空状态提示

## v0.62.0 — 2026-07-12

### Added
- 🖼️ **README 截图** — CS Demo/主应用/知识库问答 截图替换 SVG 占位
- 📋 **Demo 脚本** — `docs/demo-guide.md` 面试 5 分钟演示流程
- ⚡ **压测工具** — `locustfile.py` + `docs/load-test-report.md`
- 📊 压测覆盖：health/CS Chat/CS Stream/知识库问答

## v0.61.0 — 2026-07-11

### Added
- 🚀 **全链路验证通过** — CS Demo/调研助手/多 Agent 管线全部 API 200
- 🖥️ **桌面快捷方式** — `start_lingshu.bat` 一键启动，复制到桌面即用
- 🧪 **已验证**：CS Chat 非流式/流式、多 Agent 聊天、健康检查、静态文件

## v0.60.0 — 2026-07-11

### Fixed
- 🐛 **CS Demo 非流式接口 500** — cs_agent.py 导入路径错误（代码分层后未更新）
- 🐛 **`sys` undefined in supervisor.py** — F821 真实 bug
- 🧹 **Ruff 全部修复** — UP031（27 个）+ F401（9 个）+ F821 = 0 error

### Added
- 🧩 **knowledge-qa 场景真实化** — 独立页面 + API 端点 + 注册到 main.py
  - 访问 `/knowledge-qa` 查看
  - API: `POST /knowledge-qa/api/query`

## v0.59.0 — 2026-07-11

### Added — README 截图 + 第二垂直场景
- 🖼️ **README 截图** — 3 张 SVG 占位图（CS Demo 对话/流式/主应用 Dashboard）
- 🧩 **knowledge-qa 场景** — 通过 `create-app.py` 脚手架生成的知识库问答助手
  - 注册到 main.py，访问 `/knowledge-qa`
  - 骨架代码，可直接扩展为完整 RAG 问答 Demo

## v0.58.0 — 2026-07-11

### Added — 调研助手大升级（14 版本首次）
- 🧠 **Agent 思考可视化** — SSE streaming 实时展示思考步骤（🤔→✅），同 CS Demo 体验
- 🃏 **搜索结果卡片** — URL/title/snippet 自动解析为结构化卡片，可点击链接
- 📄 **一键生成报告** — 对话完成后「📄 生成正式报告」按钮，调用 report_formatter 生成 HTML

### Added — CS Demo TTS
- 🔊 **语音朗读** — AI 回复增加扬声器按钮，点击朗读，支持中文

## v0.57.0 — 2026-07-11

### Added — 垂直场景平台化
- 🏗️ **`apps/template/`** — 垂直场景脚手架，包含 api/agents/tools/static/tests 目录结构
- 🛠️ **`scripts/create-app.py`** — 自动生成新场景：`python scripts/create-app.py my_app`
- 📋 生成后自动替换占位名，输出注册指引

### Added — CS Demo 升级
- 🎤 **语音输入** — 麦克风按钮调用 Web Speech API，自动发送
- 🌐 **多语言支持** — 中/EN 切换，关键 UI 字符串全部双语
- 📦 **Service Worker 缓存** — 扩展预缓存列表，离线更快加载

## v0.56.0 — 2026-07-11

### Added — Android APK 项目
- 📱 **完整 Android 项目** — `android/` 目录，Java 21 + Gradle + Kotlin
  - WebView 包装器，打开 CS Demo 页面
  - Adaptive 图标（🎧 耳机图标）
  - 沉浸式全屏 / 返回键导航
- 🤖 **GitHub Actions 编译 APK** — `.github/workflows/android-apk.yml`
  - 手动触发或推送自动编译
  - 产物：`app-debug.apk` 直接下载安装

## v0.55.0 — 2026-07-11

### Added — PWA 安卓应用
- 📱 **PWA 支持** — CS Demo 页面可「添加到主屏幕」成为独立 App
  - `manifest.json` — 应用名/图标/主题色/standalone 模式
  - 192x192 + 512x512 图标（🎧 耳机图标）
  - Service Worker — 离线缓存 + 快速加载
- 🌙 **PWA 全屏适配** — `safe-area-inset` 适配刘海屏
- 🔗 灵枢主应用同步添加 PWA 支持

## v0.54.0 — 2026-07-11

### Changed — 代码分层（Phase 1）
- 🏗️ **目录重组** — 扁平的 `agent_harness/` 拆为三层架构：
  - `core/` — 共享基础设施（agents/tools/pipeline/auth/graph/config）
  - `apps/research/` — 灵枢调研助手（report/eval/ComfyUI/static）
  - `apps/cs_demo/` — 客服 Demo（cs_agent/customer_service/static）
- 🧭 **`main.py`** — 统一入口，挂载两个 app
- 📦 **所有 git mv 保留文件历史** — `agent_harness/api_fastapi.py` 拆分后移除
- ✅ **63 测试全通过**

## v0.53.0 — 2026-07-11

### Fixed — 核心 Agent 管线降级（14 版本首次修复）
- 🔴 **LLM 全线降级** — `call_llama()` / `_post_cloud()` 不可达时写 stderr 日志 + 返回空
- 🔴 **Supervisor 降级** — `_fallback_plan()` 关键词规则拆解任务（不走 LLM）
- 🔴 **Worker 降级** — 三 Worker 各自有模板回复（通知用户 LLM 不可用）
- 🔴 **API 输出降级** — 多 Agent 返回空时给友好提示 + 配置指引
- 🐛 **修复 `WORKSPACE_DIR`** — 从 `src/` 修正为项目根目录

## v0.52.0 — 2026-07-11

### Added — CS Demo 大升级
- 💬 **对话持久化** — 消息自动保存到 localStorage，刷新不丢失，切换会话保留历史
- 📋 **历史侧边栏** — 左侧列出所有历史对话（时间+消息数），点击切换，支持删除
- 👍 **消息评价** — 每条 AI 回复可 👍/👎，评价持久化存储
- 📋 **复制按钮** — AI 回复 hover 显示「📋 复制」
- 🌙 **深色模式独立切换** — 不影响主应用，持久化偏好
- 🆕 **Demo v0.52** — 版本徽标更新

## v0.51.0 — 2026-07-11

### Added — 文档站
- 📚 **mkdocs-material 文档站** — 6 页专业文档（首页/快速开始/架构/API参考/CS Demo/配置）
- 🚀 **GitHub Pages 自动部署** — `.github/workflows/docs.yml`
- 面试官直接访问 `https://kisaragiy.github.io/lingShu` 查看

### Added — 代码质量收尾
- 🧹 **Ruff 修复** — UP031 从 38→27 个（手动修复 11 个复杂 `%`→f-string 转换）
- 📖 **AGENTS.md 更新** — 从 v0.28.0 → v0.50.0，新增 12 行功能记录

## v0.50.0 — 2026-07-11

### Added — 前端拆模块
- 📦 **5 个 JS 模块** — `index.html` 从 2175 行（全部内联 JS）拆为 35 行骨架 + 5 个独立文件：
  - `static/js/api.js` — API 封装、Auth、fetch 拦截器、UI 工具（182 行）
  - `static/js/ui.js` — Dark mode、快捷键、Toast、Router、设置面板（222 行）
  - `static/js/chat.js` — mdToHtml、SSE 流式渲染、消息发送/取消（336 行）
  - `static/js/sidebar.js` — 侧边栏、会话列表、搜索建议（167 行）
  - `static/js/tabs.js` — 7 个 Tab 渲染函数（status/knowledge/sessions/skills/settings/MCP/reports）（352 行）
- 所有全局函数保持兼容，零功能变更

### Added — Docker 部署
- 🐳 **Dockerfile** — 多阶段构建（build → slim runtime），HEALTHCHECK + curl
- 🐳 **docker-compose.yml** — LingShu + SearXNG 一键启动，环境变量透传
- 📋 **`.dockerignore`**
- ⚠️ 国内网络下 `docker compose build` 需配置 Docker Hub 镜像

## v0.49.0 — 2026-07-11

### Added — 产品保护（防白嫖）
- 🔒 **启动守卫** — `config.require_config()` 启动时检查 LLM 后端配置，缺失则红色错误退出
- 🔑 **API 认证默认开启** — `/v1/*` 全部要求 JWT 或 X-API-Key，`HARNESS_DISABLE_AUTH=1` 绕过
- 🚦 **速率限制** — 默认 100 请求/分钟/IP，429 响应含 `Retry-After` 头，`HARNESS_DISABLE_RATE_LIMIT=1` 绕过
- 📋 **`.env.example`** — 配置模板文件
- 🧹 **移除魔法默认值** — `CLOUD_API_KEY` 不再默认 `sk-local`、`DEEPSEEK_API` 不再默认 `127.0.0.1:9000`

## v0.48.0 — 2026-07-11

### Added — 代码质量
- 🧹 **Ruff 配置** — 集成到 pyproject.toml，target py311，line-length 120，select E/F/I/UP/SIM
- 🐛 **修复 2 个真实 bug** — cs_agent.py 缺 `import json`（F821）、api_fastapi.py 缺 `classify_cs_intent` 导入
- 🚀 **自动 lint 修复** — `ruff check --fix` 修复 163 个问题（未使用导入、格式、简化等）
- 🧑‍💻 **User-Agent 轮换** — 4 种浏览器 UA 自动轮换，降低 DDG 频率限制

### Added — 搜索链稳定性
- 🔥 **搜索预热** — `_warm_search_cache()` 在模块加载时后台线程预热 SearXNG + DDG，消除首次搜索冷启动延迟
- 预热失败静默忽略

## v0.47.0 — 2026-07-11

### Added — 测试体系
- 🧪 **测试文件**（56 个单元测试 + 6 个集成测试 = 62 个）：
  - `tests/test_search_chain.py` — 搜索链单元测试（mock HTTP，5 场景：3 DDG 策略 + 全失败 + SearXNG 优先）
  - `tests/test_llm_cache.py` — LLM 缓存测试（key 一致性、TTL、淘汰、高 temp 跳过、HTTP 错误）
  - `tests/test_api.py` — API 认证测试（JWT 创建/验证/过期/篡改/刷新）
  - `tests/test_cs_intents.py` — CS 意图分类测试（9 种意图 × 多变体 + 边缘情况）
  - `tests/test_report_citations.py` — 报告引用测试（[url]→[N]、去重、来源提取、URL 验证）
- ⚙️ **pytest.ini** — 超时 30s、`integration` 和 `slow` 标记、CI 排除集成测试
- 🔄 **CI workflow** 更新 — `-m "not integration"` 排除需服务器的测试

## v0.46.0 — 2026-07-11

### Added — 主应用体验打磨
- ⌨️ **快捷键**：`Ctrl+K` 聚焦会话搜索、`Ctrl+Shift+N` 新建对话（快捷键面板同步更新）
- 🏷️ **代码块语言标签**：代码块左上角显示语言名称（python/json/html 等），带圆角边框
- 🕐 **搜索建议**：输入框获取焦点时显示历史搜索词下拉（localStorage 持久化，最近 10 条）
- 📋 复制按钮 + 代码块已有实现，CSS 优化

### Added — Agent 核心增强
- 🔄 **LLM 响应缓存**：`call_llama` 和 `_post_cloud` 基于 (model, messages, temperature) 的 MD5 缓存，TTL 60s，上限 500 条，自动淘汰最旧
- 🔗 **报告引用格式**：`report_formatter._format_citations()` 将 `[url]` 转换为编号 `[N]`，自动去重，追加 `## 参考来源` 章节
- 🔍 **搜索链加固**：`web.py` 新增 DDG 第 4/5 解析策略、User-Agent 轮换（3 种）、URL 归一化去重（去尾部斜杠/www/UTM）、策略级诊断日志

## v0.45.0 — 2026-07-11

### Added
- 📖 **README 重写** — 面试官友好架构图、CS Demo 场景表、快速开始/Tech Stack/项目结构
- 🏷️ **GitHub 仓库产品化** — Topics（8 个）、CI workflow（`.github/workflows/ci.yml`）
- 🎧 **CS Demo 独立 exe** — PyInstaller 打包，19MB（原 881MB 的 1/46），双击运行
  - 精简 `cs_demo/server.py` — 仅 CS 路由，无 torch/transformers 依赖
  - 自动找可用端口、自动开浏览器、SSE 流式 + 模板回复
- 📦 **构建脚本** — `scripts/cs_demo.spec` + `scripts/build_cs_demo_exe.py` + `scripts/cs_demo_launcher.py`

### Changed
- README.md 完全重写（约 10KB），含 3 张截图占位 + ASCII 架构图
- 项目结构加入 `scripts/cs_demo/` 子包（server + 启动器）

## v0.44.0 — 2026-07-11

### Added
- CS Demo 场景扩充：
  - 🛒 **售前咨询** — 商品目录查询、对比、分期方案，10 款热销商品（`cs_query_product`）
  - 💰 **优惠查询** — 优惠券/促销活动查询，5 种可用优惠码（`cs_check_promotion`）
  - 📍 **地址修改** — 收货地址修改，含验证规则（`cs_modify_address`）
  - 🚚 **配送时效** — 订单级配送估算与通用时效参考（`cs_estimate_delivery`）
- Mock 数据大扩充：4→12 条订单（含待付款/已取消/已完成等多状态）、10 条 FAQ、5 条工单、5 种优惠券
- 前端 3×3 场景卡片（9 张）：新增 售前咨询、查优惠、改地址
- 意图分类器扩充：识别 售前咨询、优惠查询、地址修改（关键词库翻倍）
- 快捷回复自适应新场景：上下文感知的建议按钮
- `tests/test_cs_stream.py` — 新增 3 个测试：test_intent_presales / test_intent_coupon / test_intent_address

### Changed
- `customer_service.py`：重写为 v2，新增工具函数 + 商品目录 + 优惠券 + FAQ 内建库
- `cs_agent.py`：`_execute_tools` 新增 3 个意图分支；`_template_fallback` + `_get_quick_replies` + 系统提示词同步更新
- `api_fastapi.py`：`_get_cs_quick_replies` 同步更新
- `cs-demo.html`：3×3 场景网格、欢迎词重写、emoji 剥离正则更新

## v0.43.0 — 2026-07-10

### Added
- CS Demo SSE 流式回复：POST `/v1/cs/chat/stream` 端点
- 前端逐 token 流式渲染：意图→工具→回复实时展示，打字机光标效果
- `tests/test_cs_stream.py` — SSE 流测试文件

### Changed
- `cs_agent.py`: 新增 `_call_cs_llm_stream_tokens()` — 流式调用 LLM，超时缩至 `(5, 60)`s
- `api_fastapi.py`: 新增 SSE 端点，5 种事件类型（intent/tool/token/done）
- `cs-demo.html`: `csSend()` 重写为 SSE 消费者，think→stream 过渡动画
- LLM 不可达时优雅降级到模板回复

## v0.42.0 — 2026-07-10

### Added
- Splash 屏升级：深蓝渐变背景 `#1a1a2e → #2563eb`，80px 圆角 logo 脉冲 + 光晕动画
- 首页体验打磨：白色元素、32px spinner、状态栏脉冲动画
- `window.__VERSION__` 由 Python `__version__` 注入前端，根除硬编码

### Changed
- `index.html` 移除客服 Demo Tab（已迁移到独立页面 `/cs-demo`）
- 修复服务页 `v0.25.0`、设置页 `v0.31.0`、检查更新 `'0.28.0'` 三处硬编码版本号
- 设置页"关于"添加 CS Demo 链接

## v0.41.0 — 2026-07-10

### Added
- 客服 Demo 产品化包装：独立全屏页面 `/cs-demo`
- `static/cs-demo.html` — 品牌化 Splash 屏 + 左面板架构介绍 + 场景推荐卡片 + 沉浸式聊天 UI
- `run_cs_demo.py` — 一键启动脚本（`python run_cs_demo.py` → 自动打开浏览器）
- 场景推荐卡片：6 种预设场景（查订单/查物流/退换货/投诉/FAQ/转人工），一键演示

### Changed
- 客服页面不再是主应用 Tab，改为独立产品级页面，适合面试展示

## v0.40.0 — 2026-07-10

### Added
- CS Agent v2：由 LLM（DeepSeek Flash）生成自然、有同理心的回复
- 前端显示三步思考过程：🔍 意图分析 → 📋/⚡ 工具执行 → 🤖 LLM 生成回复
- 回复自动降级：LLM 不可达时回退模板回复

### Changed
- `agents/cs_agent.py` 重写：新增 `_call_cs_llm` + `_execute_tools` + `_template_fallback`
- API `/v1/cs/chat` 返回 `tool_results` + `quick_replies` 字段

## v0.39.0 — 2026-07-10

### Added
- 客服 Demo 初版：🎧 Tab 页 + Mock 订单/工单/FAQ + 意图分类
- `agents/cs_agent.py` — 规则引擎式客服 Agent（v1）
- `tools/customer_service.py` — Mock 数据（4 条订单、工单系统、FAQ 知识库）
- `POST /v1/cs/chat` API + 会话持久化

## v0.28.0 — 2026-08-19

### Added — 安全护栏（WorkBuddy P0 权限双层架构移植）

灵感来自 WorkBuddy（腾讯云桌面 AI agent）的权限安全设计。新增 `core/safety/` 模块：

- **会话安全模式双模式** — `default`（沙箱优先，危险操作需确认） / `full`（全权+全量审计），持久化持久化+切换审计
- **危险操作确认矩阵** — 按参数分类检测，不再是单纯工具级分级：
  - 路径逃逸（`..` 试图离开工作区）
  - 敏感路径写（系统目录 `/etc/` `/windows/`、密钥文件 `.env` `.pem` 等）
  - 批量删除（通配符、目录递归、多文件参数）
  - 脚本执行（`code_execute` 等）
  - 外部发送（QQ/微信/消息等不可逆外部影响）
  - 网络访问（非白名单域名）
- **写前自动备份** — 覆盖写已有文件时自动存 `.bak` 副本到 `~/.agent-harness/backup/`
- **删除保护** — 文件删除走回收站 `~/.agent-harness/trash/`，可恢复
- **操作确认流** — 被护栏拦截的操作返回 `confirm_code`，用户确认后一次性放行
- **API 端点** — `GET/POST /v1/safety/mode`、`POST /v1/safety/confirm`
- 接入 `call_tool` 统一入口（`registry.py`）和 `file_write` 工具
- 27 个单元测试 + 全量回归 0 回归破坏

### Fixed
- `file_write` 追加/覆盖模式参数解析修复
- `disable_auth` 配置字段支持字符串布尔值（`"true"`/`"***"` 等）

## v0.21.0 — 2026-07-09

### Added
- 定时任务系统：后台线程轮询，支持 cron 表达式和人类语法（`every 30m`）
- `GET /v1/scheduler/tasks` + CRUD 端点
- 前端定时任务管理 Tab（创建/启用/禁用/删除）
- 插件加载器：启动时扫描 `~/.agent-harness/plugins/*.py`
- `GET /v1/plugins/loaded` API
- sync-version.py 版本同步脚本

### Fixed
- pyproject.toml 与 `__init__.py` 版本号不一致问题

## v0.20.0 — 2026-07-09

### Added
- AGENTS.md 全面重写（已实现列表→24项、架构图同步、版本哲学更新）
- DuckDuckGo 搜索多策略降级解析（3层 selector 链）
- 搜索 URL 去重（`seen_urls`）

### Changed
- 版本节奏改为慢速（参考群星 DLC 式）
- 移除 V1.0/V2.0/V3.0 空想路线图

## v0.19.1 — 2026-07-09

### Added
- 技能市场：`GET /v1/skills/marketplace`（SkillHub 集成）
- `POST /v1/skills/marketplace/install` 远程安装
- `POST /v1/skills/:name/toggle` 启用/禁用
- MCP 工具开关：`tools/tool_config.py` + `POST /v1/tools/:name/toggle`
- 前端技能 Tab 改造（已安装列表+市场搜索+安装按钮）
- 前端 MCP Tab 改造（已启用/已禁用分组+启用/禁用按钮）

## v0.19.0 — 2026-07-09

### Added
- 骨架屏（shimmer 动画，所有 Tab 加载时显示）
- 统一错误处理（错误卡片+中文描述+重试按钮）
- Toast 队列（连续通知排队显示）
- 侧边栏状态栏（健康检查 🟢/🔴）
- Tab 切换动画（opacity 渐隐渐现）

## v0.18.0 — 2026-07-09

### Added
- BDuckDuckGo 多策略降级解析
- BM25 关键词搜索（中文双字分词+英文词+TF-IDF 加权）
- 嵌入批量化（1 HTTP 请求代替 N 次）
- 嵌入重试（3次指数退避）
- 嵌入缓存（MD5 指纹）
- `threading.RLock` 文件操作锁
- 原子写入（tmp→replace）

### Changed
- `index()` 返回 dict（含 `chunks_count`/`embedding_status`/`fallback`）
- 前端知识库显示嵌入状态（🟢/🔴 点 + toast 中文提示）

## v0.17.0 — 2026-07-09

### Added
- 专业灰蓝报告模板（TOC 目录+字数统计+阅读时间+标签行）
- 前端报告下载按钮（⬇ 下载 .html）
- Worker 输出截断 500→2000 字符
- 中文错误提示（超时/API Key/频率限制）
- 搜索提示词优化（4角度：品类/评测/对比/行业）

### Changed
- Finalizer 提示词重构（先结论→分小节→引用数据→800-1500字）

## v0.16.0 — 2026-07-09

### Added
- SQLite 线程安全（`threading.local()` + WAL + `busy_timeout`）
- Agent 并发限流（`Semaphore(5)`，流式+非流式全覆盖）
- `HARNESS_WORKERS` 多进程支持
- `owner_id` 数据隔离（会话/报告/正式报告）
- 原子文件写入（tmp→replace）
- `HARNESS_MAX_CONCURRENT_AGENTS` 环境变量

## v0.15.0 — 2026-07-09

### Added
- JWT 用户认证（HMAC-SHA256，零外部依赖）
- RBAC 角色权限（admin/user）
- 登录页面 + 首次启动管理员创建
- 用户管理面板（创建/删除/改角色/重置密码）
- `POST /v1/auth/login` / `POST /v1/auth/logout` / `GET /v1/auth/me`
- Admin CRUD：`GET/POST /v1/admin/users` + 角色/密码/删除
- 前端 fetch 代理（自动附加 JWT Bearer 或 X-API-Key）

## v0.14.0 — 2026-07-09

### Added
- API 双模认证（JWT + X-API-Key）
- 首次启动自动生成 256-bit API token
- CORS 收紧（`["*"]` → 具体 origin）
- `code_execute` 沙箱（安全 builtins 白名单+模块白名单+30s 超时）
- 工具权限三级（read-only / reversible / irreversible）
- CSP 响应头 + 安全头模板
- `/docs` 默认关闭（`HARNESS_ENABLE_DOCS=1` 重开）
- 路径遍历防护（`_safe_path_param()`）
- 审计日志（`~/.agent-harness/audit/`）

## v0.13.0 — 2026-07-07

### Added
- Worker 自动重试（3次指数退避 2s/4s/8s）
- 搜索兜底信号统一（`[搜索失败]` 格式）
- 报告数据来源引用（超链接上标+锚点列表）
- `report_formatter.py` 重写（自动提取 URL + 来源列表）
- 快捷键：Enter 发送 / Ctrl+Enter 换行 / Esc 取消
- 窗口位置持久化（`window_config.json`）
- 版本号显示（侧边栏底部）
