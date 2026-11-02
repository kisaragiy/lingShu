# 企业研究试点与阶段冻结实施方案

> 状态：规划已编写，实施与验收均未完成。编写日期：2026-11-02。
> 核对基线：master `87034360d684193f762c22fa8107a5684d8b7230`（v0.75.6）。
> 本文件是企业研究增量阶段的唯一主方案；[评测规范](../acceptance/enterprise-eval.md)、[验收记录](../acceptance/pilot-results.md)配套使用。机器状态以 [PROGRESS.json](../../PROGRESS.json) 为准。方案存在不代表能力交付。

## 1. 目标、范围与完成口径

首个业务限定为：单企业内 3–5 名受控员工，按固定模板跟踪最多三家竞品的产品功能、公开价格与公告，形成带证据的周报，经过人工修改、复核、定稿、导出、归档。先使用公开或获准的低敏数据。企业名称、试用人员、真实任务和收益均待实际招募与记录。

沿用本地优先、单实例、单 Uvicorn worker、SQLite/文件存储。跨企业 SaaS、多租户组织树、计费、SSO、分布式调度、PostgreSQL、Kubernetes、自动外发报告不在本期。员工级对象隔离是本期必需条件，不等于承诺多租户隔离。不能满足单实例目标时先缩小试点或停止，不能借此默认扩架构。

| 状态 | 必须有的证据 | 可以宣称 |
|---|---|---|
| Engineering Ready | P0–P3、P4 工程回归/容量/安全/恢复通过 | 已达到本方案受控试点工程基线 |
| Pilot Ready | Engineering Ready + 员工独立走完业务闭环 + P5 交付验收 | 可交付受控部门试点，可冻结工程版本 |
| Business Validated | 至少 30 个真实任务、3–5 名真实用户及质量/效率门槛通过 | 此范围内业务价值已验证 |

工程冻结允许业务验证仍为未验证，但必须在发布记录显式声明；没有员工独立操作证据就不能标 Pilot Ready。无企业用户时可完成工程候选版本，不能把开发者演示替代员工验收。冻结仅针对本期范围，不代表 AI OS 长期愿景完成。

## 2. 实际基线与待核实缺口

下表仅表示本次静态阅读到的实现/文档，不表示本次运行通过。实现前重新 pin master SHA 并复核差异。

| 领域 | 已观察到的路径与事实 | 本期动作 |
|---|---|---|
| 研究入口 | `apps/research/api.py` 的 `_execute_multi` 调用 `core/graph/graph_multi.py:run_multi_agent`，注入 checkpointer、session_id 与 RAG scope | 复用；建立入口到工具的调用证据 |
| 唯一工具准入 | `core/tools/registry.py:call_tool` 有 PRE hooks、权限、风险与开关检查 | 在既有 seam 绑定应用策略；禁止直接调用注册函数绕开准入 |
| 预算 | `graph_multi.py` 已创建/消费 TokenBudget，`tests/test_budget_wiring.py` 已有接线用例 | 验证并发归属、取消与实际消费；不得按旧分层文档“零引用”结论重做 |
| 任务入口 | `main.py` 注册 `core/tasks.py` 的 /v1/tasks；research API 同时声明任务列表/取消；core tasks 数据表未见 owner 字段，研究列表/取消函数未接收 Request | P0 枚举实际路由顺序和中间件，P2 证明对象授权；不能因有 JWT 即判隔离成立 |
| 报告 | `apps/research/pipeline/report_store.py` 有 owner 过滤、索引锁和原子写；报告核查说明明确“数字匹配不等于语义支持” | 增量加版本/审核/归档元数据；原报告只作草稿，不自动批准 |
| 会话/知识库 | session JSONL；RAG scope 在数据层校验，线程需传播 contextvars；admin/空 scope 可全见 | 默认员工私有；分享采用明确 ACL，禁止用空 scope 实现共享 |
| 持久化 | compose 的 LingShu 无数据卷；auth/checkpoint 可用 HARNESS_DATA_DIR，session/report/RAG 各有目录设置；core tasks.db 位于源码相对 data 目录 | P3 做完整写入清单、挂载与恢复；不能只设 DATA_DIR 就宣称全持久化 |
| 密钥 | keystore 以机器身份和用户名派生 KEK | 重建/异机恢复必须验证可解密或安全重录，不可仅拷 config.json |
| 测试/历史 | CI 含 quality、integration、onboarding、source-security；README 数字是 10-23 历史口径，closeout 是早期候选记录 | 新提交以实际 CI 与运行日志为证；保留历史，不重写旧分数 |
| 路线图 | TASK_SPLIT 是 7 月 v0.52.0；PROGRESS 旧 5 项来自 8 月盘点 | 保留历史条目，P0 逐项核验后给证据，不自动标完成或重做 |

路径除根文件外均相对 `src/agent_harness/`。旧 [architecture](../architecture.md)、[分层自评](../architecture-harness-layers.md)、[API 概览](../api.md)包含概念示例或过时判断，均不能优先于 pin 后源码、实际路由与实测。AGENTS 的 ADR-001（core 范围）与 docs/adr/ADR-001-safety-gate.md（安全双层）同号但主题不同，必须同时遵守，不能据后者撤销前者。

## 3. 不可违反的架构与改动边界

唯一执行内核是现有运行链，而非要求新建名为 Kernel 的类：

`认证请求 → research 应用服务 → 现有 run_multi_agent → Supervisor/Workers → registry.call_tool → 现有权限/风险/审计 → 工具`

HTTP、SSE、应用后台恢复、CLI、MCP、插件及直连 API 都必须核对；新增企业入口只能适配现有链。报告渲染/审核/归档是应用存储操作，不得因此建立第二套 LLM 工具循环、执行队列或审批后直调工具的旁路。复用已有并发名额/取消/任务生命周期；应用业务表只记录状态，不承担另一套执行调度。若现有持久任务接口不足，先在 P0 证明并选择最小适配，不同时引入双队列。

首选新增 `apps/research/enterprise/`（拟新增，当前不存在）：schemas.py、service.py、repository.py、api.py、policy.py、migrations/，由现有 research 路由挂载；前端沿用 research/static，报告沿用 pipeline/report_store 和 formatter。不同时新建 enterprise_research 应用与独立工作台。文件拆分随必要职责确定，禁止为了目录完整预建空模块。

core 仅允许 AGENTS 已授权的配置外置、错误恢复、诊断和文档；安全缺陷修复优先在应用/既有 seam 完成，若确需超出 core 允许范围，先单独提出最小 ADR 变更供维护者审阅，不得视本方案为豁免。每个 core 变更单列原因、影响面及回归证据，禁止大重构、新租户/运营/配额体系。预算仅使用现有 run 限制与部署级保护，不实现商业额度产品。配置仍走 get_llm_config()，不得 import 快照绕开设置页。地址和数据根可配置，不硬编码组织私网/个人路径。不得删除或放宽安全测试、覆盖率/版本门禁来提速。

## 4. 员工业务流程和状态机

1. 员工选择“竞品周报 v1”，填写目标、最多三家实体、研究起止日期/截止时间、语言、输出章节、允许来源域和已授权知识库。
2. 服务端验证身份、对象 ACL、模板版本、输入范围和运行预算，持久化任务与幂等键，再提交现有执行入口。
3. 检索/分析形成草稿；展示来源、无法核实项、失败/取消原因和使用量，不把完成契约当内容正确。
4. 作者修改生成新版本，提交给指定复核者；复核者逐项核查证据，批准或驳回并写理由。
5. 已批准的确定版本导出 HTML/Markdown（PDF 如需仅走已验证打印流程，不能承诺新增转换器）；作者或复核者归档，保留 hash、来源和审核链。
6. 定稿后修改创建新版本，重新复核，历史批准不转移到新内容。

执行状态和报告审核状态分开持久化：

| 对象 | 合法迁移 | 约束 |
|---|---|---|
| task | created → queued → running → succeeded / failed / cancelled / interrupted | queued/running 可请求取消；取消请求不等于线程已停；succeeded 仅指产物已完整提交 |
| interrupted task | interrupted → queued（显式恢复）或 cancelled | 先查 checkpoint、授权、预算和版本兼容；不能恢复时失败并给新任务入口 |
| failed/cancelled task | 保持终态，重试创建新 attempt | 不改写原失败；关联 parent_attempt_id |
| report version | draft → in_review → approved / rejected | 只有人工复核动作能批准；拒绝理由必填 |
| rejected version | 保持原记录，复制为新 draft | 不覆盖旧正文/审核 |
| approved version | approved → archived | 内容不可变；导出只绑定这一版本 |
| in_review | 作者撤回 → draft | 撤回取消待决审核，保留事件；审核期间禁止原地编辑 |

每个 task 只允许一个活跃 attempt；同一 attempt 一个终态。CAS revision 与数据库事务防并发批准/取消/完成互相覆盖；取消和完成竞争以先提交的终态为准，另一方记录冲突，产物可隔离待查。归档只对获批版本；草稿可预览导出但必须带“未批准/待核查”标识，不能进入正式归档。外部邮件、自动发布、定时自动批准不在本期。

## 5. 数据约束与持久化契约（拟新增，尚未实现）

在受配置管理的数据根下新增 enterprise.sqlite（应用数据库，复用现有 SQLite 技术），不得放源码目录。先使用现有 auth 用户 ID，不复制密码/令牌表。迁移有 schema_version、备份、前向脚本、失败恢复记录。

| 记录 | 必需字段/约束 |
|---|---|
| research_task | task_id（服务端生成）、owner_id、template_id/version、input_json、acl_version、status、revision、created_at/updated_at；owner 不由客户端赋值/转移 |
| run_attempt | attempt_id、task_id、session_id、checkpoint_thread_id、parent_attempt_id、status、start/end、stop_reason、model/config/prompt/policy 版本、token/time limits 与 actual usage；ID 映射唯一，恢复复用同 attempt/thread |
| report_version | report_id、version（联合唯一）、task_id/attempt_id、owner_id、status、正文相对路径、sha256、source_manifest_hash、created_by/at；不覆盖已提交版本 |
| source/claim | source_id、原始/最终 URL、标题、抓取 UTC 时间、摘录/内容 hash、访问权限；claim_id、正文位置、source_ids、支持/冲突/缺证标签、人工理由；无 URL/不可取源保留原因 |
| review_event | event_id、report_id/version/hash、actor_id、action、理由、UTC 时间、expected_revision；正文与 source manifest 有变化则批准无效 |
| object_acl | object_type/id、principal_id、read/review 能力、grantor、expires_at、revision；默认私有，无通配员工读权限 |
| audit_event | actor、action、object/version、attempt、trace_id、结果/原因、policy_version、UTC 时间；记录拒绝和授权变更，不记录密钥/完整敏感 prompt |
| idempotency/outbox | actor+operation+key 唯一、请求 hash、结果引用；待提交产物/审计与事务同写，完成后标记已交付 |

输入契约：题目 1–2000 字，实体 1–3 个，日期范围不倒置且不晚于声明的截至日，模板只接受服务端白名单版本，collection 只可选择授权集合；URL 只允许 http/https，重定向/解析后的内网地址、file/data scheme 和路径穿越拒绝；附件沿用受限上传流程，首期总量上限 20MB/任务，不能仅按扩展名判安全。超限返回明确错误，无静默截断。

UTC ISO-8601 存储时间，界面显式显示时区；未知字段拒绝，分页 limit 1–100，检索过滤同 ACL。hash 计算覆盖正文原字节和规范化来源清单；未知来源不得按搜索顺序补链接。日志和证据中的私密材料只保存受控引用。

文件/SQLite 不具备跨介质事务：先在私有暂存目录写正文/来源、fsync、计算 hash、原子 rename，再在 SQLite 事务中提交版本+审核/审计 outbox；失败文件不对外可见。启动按 manifest 对账：孤儿文件隔离、缺文件/错 hash 的记录标损坏并阻止批准/导出。幂等重复提交不能产生多个版本或多次执行。

## 6. 应用接口契约（拟新增，不能视为现有 API）

统一前缀 `/v1/enterprise`，避免现有 /v1/tasks 注册冲突。所有对象接口从认证上下文取 actor，先授权后取内容，不信任 body 中 owner、role、scope、approved 标志。角色沿用 admin/user；reviewer 是特定对象的应用授权能力，不新增 core 组织角色。

| 方法/路径 | 输入 | 成功与副作用 |
|---|---|---|
| GET /templates | 无 | 200，固定模板及可用版本 |
| POST /tasks | template/version、业务输入；Idempotency-Key | 201，task_id/revision/status=created |
| GET /tasks；GET /tasks/{id} | 分页/过滤 | 200，仅可读对象；列表 count 也必须过滤 |
| POST /tasks/{id}/start | expected_revision；幂等键 | 202，attempt_id/session_id，提交唯一现有执行入口 |
| GET /tasks/{id}/events | SSE Last-Event-ID 或 after_seq | 有序事件 seq、trace_id；重连只补事件，不重启执行；不泄露其他任务 |
| POST /tasks/{id}/cancel | expected_revision；幂等键 | 202 cancel_requested；停下并落盘后才见 cancelled |
| POST /tasks/{id}/resume | interrupted attempt_id、expected_revision | 202，校验 checkpoint/policy 后复用同执行链；不兼容 409 |
| GET /reports/{id}/versions/{v} | 无 | 200，正文、来源、审核状态，只返回授权内容 |
| POST /reports/{id}/versions | base_version/revision、修改正文 | 201 新 draft/version；拒绝原地覆盖批准版 |
| POST /reports/{id}/submit | version/hash、reviewer_id、revision | 200 in_review；必须已有可读来源与明确未知项 |
| POST /reports/{id}/review | version/hash、approve/reject、理由、revision；幂等键 | 200；仅指定复核者，作者不得自批 |
| GET /reports/{id}/export | version、html/markdown | 200，安全下载，版本/hash/审核状态一致；不暴露磁盘路径 |
| POST /reports/{id}/archive | approved version/hash、revision | 200 archived + manifest hash |
| PUT /tasks/{id}/acl | principal、read/review、expiry、revision | 200；仅 owner/admin，授权不得超出自己的数据权限 |

错误体统一含 `code/message/trace_id/retryable`，不带堆栈/密钥。401 未登录/过期，403 已知可见对象但操作无权限，404 不存在或无可见权，409 revision/状态/幂等键同 key 异内容冲突，422 输入无效，429 容量/速率上限（Retry-After），503 存储或执行依赖不可用。持久化失败不能返回成功。相同 actor+key+内容重试返回原结果；保留幂等记录至少 7 天，过期 key 不允许用于模糊重放，客户端需新 key 并明确创建新 attempt。

接口实现前补 schema/OpenAPI 与契约用例；旧接口保持兼容但进入同样的授权/准入边界，不能仅保护新前缀。

## 7. 权限、工具与数据隔离

- 员工个人 JWT 登录；不分发默认 admin API Key，不关闭认证/限流。禁止员工更改全局模型密钥、工具开关、安全模式、插件、技能和设备登录配置。
- owner 可创建/编辑/提交/取消自己的任务，显式获权的同事可阅读或复核，作者不得自批。admin 运维越权查看必须审计理由；admin 不拥有模型自动批准能力。
- 默认不共享知识库；给复核者的报告 ACL 不自动扩大其底层 RAG 集合访问权。只包含该复核者可读的证据才允许提交，否则返回缺权限待处理。首期不做部门共享库。
- 每个运行带服务端不可由模型改写的 actor/task/attempt/scope/policy 上下文；线程与恢复显式传播，finally reset。缺身份/缺 scope fail-closed，不能落到空 scope 全见。取消、搜索、报告下载、静态报告链接、导出、审计、缓存、日志均做对象授权。
- 使用 registry PRE hook 或现有准入 seam 实施工具 allowlist，复用风险/权限/审计；允许经过核对的搜索、公共页面抓取、限定 scope 的 RAG 只读查询。实际工具名在 P0 从 registry 列出并锁定 hash。代码执行、桌面/浏览器控制、file_write、rag_index、技能生成、未审核 MCP/插件默认禁止；报告持久化由应用受控服务完成。
- 不用全局可变“当前员工”保存并发身份；插件/直接函数/MCP/兼容 API 不得绕过策略。验证分析 Worker 在禁代码工具后仍可生成报告；若不可行先记录阻塞，不为跑通开启 full 模式。
- 外部页面、知识库文本、模型输出均不可信：不能修改系统权限、外发数据或审批。引用/HTML 输出继续安全转义，验证恶意链接、脚本、下载文件名和路径穿越。只读工具也需验证 SSRF/内网、重定向、外部 URL 携带敏感查询的风险。
- 单实例管理员属于可信运维边界；同机系统管理员可读数据库，不宣称对其提供加密租户隔离。加密备份与访问控制按部署方案执行。

## 8. 持久化、恢复、容量与运行保护

P3 先产出“逻辑数据 → 实际写路径 → 配置变量 → 卷 → 备份 → 恢复验证”表，至少覆盖 auth.db、JWT/应用密钥、config/keystore、sessions 的 log/json/meta、报告正文/索引、checkpoint.sqlite、RAG 原文/向量/索引、审计、pending、安全备份、应用数据库及现有 tasks.db。扫描启动/执行/导出/失败路径的真实新增文件，未入清单的写入视为阻塞。

部署拟把数据集中到容器 /data 私有卷：显式设置 HARNESS_DATA_DIR、HARNESS_SESSION_DIR、HARNESS_REPORTS_DIR、HARNESS_RAG_DIR；config/密钥仍可能读用户家目录，须逐项绑定或配置外置，不得假定随 DATA_DIR 迁移。core tasks 路径若需改，仅作配置外置小补丁并回归。compose 当前没有这些改动，本方案不代替实施。

备份策略目标：每日一致快照、保留 7 份日备份和 4 份周备份，异机/离线受控副本；试点目标 RPO ≤24 小时、RTO ≤60 分钟。首次试点前、每次迁移前和候选发布前必须实练。小规模首期选择暂停接单、排空/标记中断、停止写进程后复制全套数据，或使用已证明跨 SQLite/文件一致性的快照；不得热拷单个 DB 遗漏 WAL。记录开始/结束、最近已备份事务时间、备份大小/hash、操作者与恢复用时。

在独立空目录/新容器恢复，不覆盖原卷：校验 hash、SQLite integrity_check、owner/ACL、事件序号、报告/来源/批准 hash；验证登录或安全轮换、密钥可用/安全重录、RAG 查询、checkpoint 恢复和无权限拒绝。机器身份 KEK 变化时不得以记录明文密钥解决，需受控重录/轮换并重新验证。容器重建保留数据与异机备份恢复是两个独立案例。

重启把失联 running 标 interrupted，queued 对账后仅在当前授权/预算仍有效时交给原运行入口；不盲目全量重跑。checkpoint 缺失、不可解码、版本不兼容则显式失败/新 attempt；节点内副作用不能承诺 exactly-once，依赖既有幂等键与操作对账。企业任务不可在 checkpointer 失效时静默承诺可恢复；停接新任务并告警。

容量锁定目标：5 个独立员工会话、同时最多 2 个研究 run，其余等待上限 3 个，超限 429；等待最多 120 秒，执行目标上限 600 秒，token 硬上限先沿用现有 100000/运行并在 P0 结合模型调小，软阈值低于硬阈值。不是新增 core 计费/配额模块。队列与名额复用现有机制，若 FIFO/持久性不足必须在 P0 明确适配和验证，不另建调度器。取消 10 秒内发出并记录信号；外部不可中断调用可能继续计费，必须记录残留和最终停止时间。不得把发信号当所有 provider 已停止。

P4 前锁定模型、机器规格、任务大小与容量参数；业务执行 P95 ≤600 秒，控制 API P95 ≤2 秒，非业务 5xx ≤1%，有效任务完成率 ≥95%。至少 60 分钟负载并覆盖 ≥40 次任务尝试，包含失败/取消/重连。真实模型成本单列，mock 压测只证明协议/调度。采集 queue/running、耗时、终态、token、磁盘余量、备份年龄、审计积压；存储不可写、备份超龄、越权、超时/预算停止必须可见并有处置步骤。现有 SLO/Locust 仅为复用资产，未实跑不视为容量通过。

保留策略：会话/草稿默认 30 天，批准/归档报告与审核默认 90 天，审计 90 天，均为待业务负责人确认的试点策略而非法规结论。核对现有 session TTL 默认 7 天与懒清理行为，确保活跃审核证据不被提前删除；到期删除须授权、审计并覆盖备份到期规则和 RAG 派生物。冻结前记录实际配置，不能只写策略。

## 9. 任务依赖、实施位置与阶段验收

以下均未实施，顺序和 ID 与 PROGRESS 对齐。每阶段先提交小 PR；若前置未通过，不把后续完成作为替代。P4 的数据集设计可与 P1 并行，但正式结果必须来自最终候选。

| ID / 阶段 | 依赖 | 具体改动/交付 | 验收与证据 |
|---|---|---|---|
| ep-p0-baseline | 无 | 核对入口/中间件/registry、数据根、旧 5 项、当前 CI；建立调用矩阵、缺口表、工具清单和参数表；确认试点负责人/复核者 | 每入口授权及工具调用有源码行/测试定位；所有缺口分已证实/待实测；旧任务逐项分类且不凭猜测 pass |
| ep-p1-workflow | P0 | research/enterprise 应用模型、数据库迁移、service/API；现有 report_store/formatter 适配；research/static 表单/审核/导出 | 走通创建→运行→草稿→修改→驳回→新版本→批准→归档；重复/并发/撤回/终态冲突/输出脚本有用例 |
| ep-p2-isolation | P0、P1 契约 | 应用 policy/ACL，既有入口补对象校验，registry 准入接线、线程上下文；main 路由重叠整理证据 | 双用户交叉读写/取消/RAG/日志/导出/旧 API/MCP/注入全部拒绝；无意外执行、泄漏或 full 模式旁路 |
| ep-p3-recovery | P1、P2 | compose 挂载/配置、应用迁移、备份恢复与对账脚本（拟 scripts/backup_pilot.*、restore_pilot.*）、运维文档 | 重建与空环境恢复均通过；RPO/RTO 实测；崩溃/磁盘满/损坏/重复请求不误报成功 |
| ep-p4-evaluation | P1–P3 | 独立评测集、工程集成、安全/负载/真实模型与员工操作记录；补齐必要测试 | 依 enterprise-eval 逐项出分母/原始记录/失败；CI 对最终 SHA 通过；工程与业务结论分开 |
| ep-p5-freeze | P0–P4 工程项 | README/API/部署/排障/已知限制、候选标签与发布清单、维护规则；pilot-results 签字 | Engineering/Pilot Ready 分别有签字；冻结表无缺项；Business 状态真实；再由维护者发稳定 tag |

P1 前拟新增测试 tests/test_enterprise_workflow.py、test_enterprise_contract.py；P2 拟新增 test_enterprise_isolation.py；P3 拟新增 test_enterprise_recovery.py；P4 拟新增容量/业务评测入口。这些文件当前不存在，不能将其命令写成已通过。优先扩展现有 authz、report、budget、resume 用例，避免重复测试体系。

每个 PR 记录任务 ID、base/head SHA、改动文件、接口/迁移影响、所选测试层及原因、命令/退出码/通过失败跳过、产物 hash、已知限制和回滚步骤。证据保存在受控附件/CI artifact，pilot-results 只保存脱敏摘要与可访问引用；不能把内部报告/令牌提交公共仓库。

## 10. 分级测试门禁

保留所有现有测试/CI 门禁，不按“通过数必须增长”替代行为回归判定；收集数变化需解释。当前文档 PR 只验证文档/JSON/引用，不宣称业务回归已跑。

| 层级 | 触发 | 执行与边界 |
|---|---|---|
| L0 文档/状态 | 仅文档和 PROGRESS | JSON schema/harness check/status、相对链接、状态/ID/历史保留；不跑付费模型 |
| L1 快速 | 每次实现小改动 | 修改模块测试和静态检查；鉴权/工具准入/数据根修改立即运行对应安全用例 |
| L2 功能 | 单功能 PR | 工作流/接口/存储事务、双用户、注入、幂等/冲突、失败路径；数据根必须临时隔离 |
| L3 阶段 | P1–P3 验收 | 开认证的真实 HTTP/SSE + SQLite/文件 + 真实现有图（可控模型替身）；恢复/重建演练；mock 不能证明业务正确 |
| L4 候选/合并 | master 合并检查、RC/稳定发布 | 保留 ci.yml 全部阻断 job、全量离线、真实 HTTP integration、覆盖率、SAST/依赖/源码/本 SHA 镜像扫描、版本和首次安装；手动 mutation 保持现有非分数门禁 |
| L5 业务/容量 | 试点候选 | 固定真实模型、人工核查、真实员工、同口径人工基线、容量与成本；无凭据/预算则记 blocked/not_run，禁止以替身结果顶替 |

可直接复用的现有命令（实施阶段执行并记录，不是本次已运行）：
```bash
python tools/harness/harness.py --dir . check --json
python tools/harness/harness.py --dir . status --json
python -m pytest tests/test_authz_matrix.py tests/test_authz_session_write.py tests/test_session_owner_scope.py tests/test_rag_scope.py tests/test_rag_scope_thread_boundary.py -q
python -m pytest tests/test_report_store_atomicity.py tests/test_report_citations.py tests/test_report_review_safety.py tests/test_graph_resume.py tests/test_budget_wiring.py -q
uv sync --extra dev --frozen
uv run pytest tests/ -q --cov=src/agent_harness --cov-report=xml --cov-fail-under=35
uv run coverage report --include='*/core/pipeline/*,*/core/graph/*' --fail-under=70
```

integration 按 ci.yml 启动隔离服务并显式覆盖默认 marker：`uv run pytest -m integration -o addopts="-q --tb=short"`。该 job 关闭认证验证 HTTP/降级，不能替代本期 L3 开认证隔离测试。完整静态/扫描/版本命令以 pin 后 ci.yml 为准，不能仅跑 pytest 就称“全门禁通过”。

## 11. 失败、停止与回滚

| 触发 | 立即处置 | 恢复/回滚证据 |
|---|---|---|
| 跨用户泄漏/越权工具/注入成功 | 停止试点、撤销访问和外发、保留审计、必要时轮换密钥 | 根因修复 + 全隔离矩阵重跑，业务负责人重新批准 |
| 迁移/索引/hash/备份损坏 | 停写，不以空索引掩盖错误，不覆盖原卷 | 独立环境恢复最近一致快照，完整性/权限/产物校验后切换 |
| 模型/搜索失败、预算/超时 | 标 failed/停止原因，保留已用成本和草稿；不标 succeeded/approved | 显式新 attempt 或校验后的恢复，不无限重试 |
| 磁盘满/审计 outbox 无法落盘 | 拒绝新任务/审批，不返回虚假成功 | 清理依保留策略，恢复写入后对账，无遗漏事件 |
| 容量/SLO/质量不达标 | 不升级试点范围，不冻结为 Pilot Ready | 缩范围须在新一轮测前批准并版本化阈值；保留原失败结果 |
| 发布后阻塞回归 | 停接单，保留现场 | 回退上个已验收 tag/镜像及对应配置；数据不兼容时恢复升级前快照，不直接降级读新 schema |

迁移前保存代码 SHA、镜像 digest、schema_version、配置 hash 与全量快照；先预演恢复再迁移。禁止为了回滚直接删除用户数据或破坏性降表。回滚后验证旧版本健康、登录、ACL、报告/证据 hash、RAG 和安全拒绝，再开放；记录可能丢失的升级后写入及人工补录，不隐瞒 RPO 损失。功能未验收前可禁用新应用路由，但必须同时封住旧接口旁路。

## 12. 最终冻结与维护

冻结检查全部由 pilot-results 指向真实证据：

- [ ] 范围/模型/部署/数据与工具白名单版本锁定，P0–P4 工程项通过，员工独立闭环通过。
- [ ] 单 Kernel/唯一执行链与所有入口准入审计完成；无第二执行路径。
- [ ] 状态机、对象隔离、注入/SSRF、密钥与审计、安全门禁通过；P0/P1 缺陷为零。
- [ ] 容器重建、备份/恢复、迁移回退、容量与 SLO 实测通过；已知风险有 owner/期限/处理理由。
- [ ] 最终提交所有必需 CI job 成功；跳过/未跑必须说明，不可直接当通过。
- [ ] README/API/配置/部署/排障、数据保留与维护责任齐备；机器进度与验收记录一致。
- [ ] Engineering Ready、Pilot Ready、Business Validated 分别决定；没有业务样本的状态明确为 not_validated，不伪造客户和节时率。
- [ ] 维护者/业务复核者签字后，创建稳定 tag/发布记录，记录精确 SHA、镜像 digest、测试/恢复证据，进入维护模式。

写完本方案、合并本文件 PR、测试数量增加，都不触发冻结。正式 tag 由验收后的发布操作创建，本次不预设版本号或标记完成。

冻结后只接受安全漏洞、严重缺陷、必要依赖安全/兼容修复；保持提交 CI，发布前全量门禁。不再无需求新增 Agent/工具/框架/模型适配器。重开必须有真实用户需求或重大安全/可靠性证据、范围/验收/成本/负责人，并新建增量方案。Business Validated 可在冻结后继续收集数据，若需改行为则走维护补丁与相应回归。
