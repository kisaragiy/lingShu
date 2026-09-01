# 灵枢 × DeepSeek Harness 改造落地映射

> 版本：v1.0 · 2026-08-27
> 性质：**设计文档 / 后续改造蓝图**。本文只给方案 + 验收标准 + 优先级，**不含代码改动**（涉及 P1 大改需走大事预检门再动手）。
> 来源：DeepSeek Harness (`deepseek-ai/deepseek-harness`，0.1.1-rc.2，MIT) 代码级验证（2026-08-16 / 2026-08-27 复查）。核心模式已实锤，本文映射到灵枢现状。
> 前置约定：灵枢是独立项目，**不**被 dsh 产品依赖，只吸收架构思想；Hermes 是第三方产品不改 core。

---

## 0. TL;DR

灵枢已有 **Supervisor-Worker / 危险操作确认矩阵 / JWT+审计 / 三重熔断 / 预算 WARN-ASK-STOP**。dsh 真正能补的两处：

| 优先级 | 补什么 | 一句话 | 成本 | 预检门 |
|---|---|---|---|---|
| **P0** | turn/step 成本纪律 | "空轮短路 + 触顶不降级"——别为一次无意义调用烧 token | 小 | 否 | ✅ 已交付 (2026-08-27, commit 9485f45) |
| **P1** | Session 事件源 + 表面投影 | 会话从"全量 JSON 重写"→"append-only 事件日志 + 投影"，检查点/可复现/回溯 | **大** | **是（必须走）** | ✅ 已交付 (2026-08-27, commit 0c20541) |
| **P2** | 超时做成独立插件 | 工具超时从内嵌逻辑变成可替换 seam | 中 | 否 | ✅ 已交付 (2026-08-27, commit 944785e) |
| **P2** | 能力缝三角色哲学 | 重接 plugin_loader 时的设计原则 | 中 | 否 | ✅ 已交付 (2026-08-27, commit 944785e) |
| **★追加** | 工具三阶瀑布 pipeline | call_tool 拆成 PRE(可拦)/EXECUTE(超时)/POST(可变换) 可插拔横切 seam | 中 | 否 | ✅ 已交付 (2026-08-27, commit 7576de4) |

---

## 1. 现状盘点（谈改造，先摸清已有）

### 1.1 已有的（**不要重复造**）

| 能力 | 位置 | 状态 |
|---|---|---|
| Supervisor-Worker 编排 | `src/agent_harness/core/agents/{supervisor,workers}.py` | 已实现 |
| 危险操作确认矩阵 | `core/safety/{risk,mode,backup}.py` | 已实现（v0.28，approval 门相当 dsh 的 guard） |
| 工具权限门 | `core/tools/permission.py` | 已实现 |
| 预算三层（WARN/ASK/STOP） | `core/budget.py`（`BudgetLevel` 枚举） | 已实现 |
| 三重熔断 / 指数退避 | `core/{degradation,retry}.py` | 已实现 |
| JWT + RBAC + 审计 | `core/{auth,audit}.py` | 已实现 |
| 插件加载器 | `plugin_loader.py` | 代码存在，**待重接** |
| core/apps 分层 | `src/agent_harness/{core,apps}` | 已实现 |

### 1.2 缺的（dsh 能补的增量）

- **会话是全量 JSON 快照重写**：`core/pipeline/session_store.py::save_session` 每次把**整个 `messages` 数组** dump 回文件（89-100 行），不是事件日志。`update_session_meta`（270-297 行）更是直接 `open(path,"w")` 截断写（**非原子**，无 `.tmp`+`os.replace`）。
- **agent-loop 无空轮短路**：不判断"这次 turn 是否值得调模型"，可能为无意义输入烧调用。
- **无 max-tokens sticky**：某 step 触顶后，后续正常 step 可能被静默降级为"不完整结果"。
- **超时是否内嵌**（未核实）：dsh 模式是超时独立插件，灵枢需确认不是硬编码在工具执行层。

---

## 2. 优先级 P0 — turn/step 成本纪律（建议先做）

> 背景：`budget.py` 管的是**累计 token 总量阈值**（WARN/ASK/STOP），已够用。dsh 补的是**单次决策层面**——"发不发这一次模型调用"和"触顶后别静默糊弄"。两者互补，不重复。

### 2.1 Pattern（dsh 实锤）

1. **空决策短路**：turn 开头/首 step 前，若无有效输入（空消息/纯系统触发/noop），直接置 `completed`，**不调模型**。
2. **max-tokens sticky**：一旦某 step 触顶（`finish_reason=length`），后续**正常完成**的 step 不得把 turn 结果降级为"截断/不完整"。已完整生成的部分要保留，别拿"触顶了所以整个结果都废"糊弄。
3. **phase-abort 贯穿**：每个 await 点可取消（`throwIfAborted`），取消立即生效应渗透到所有 step。

### 2.2 落点

- 改动文件：`core/agents/supervisor.py`（turn 入口判空） + `core/agents/workers.py`（step 返回处理处 sticky） + 模型调用封装（phase-abort）。
- 注入点：turn 开始处、每次 LLM 调用返回处理处、工具调用 await 处。

### 2.3 验收标准（客观可查证）

| # | 验证 | 方法 | 断言 |
|---|---|---|---|
| C1.1 | 空轮短路 | 注入空/无输入 turn，mock LLM 计数 | 模型调用次数 == 0，turn 状态 == completed |
| C1.2 | 触顶 sticky | mock 返回 `finish_reason=length`，再喂一个正常完成 step | 最终结果保留完整 step，未被降级/截断标注 |
| C1.3 | phase-abort | 取消信号在工具调用 await 中途触发 | 下一次工具不执行，立即返回 cancelled，无残留副作用 |
| C1.4 | 不回归 | 现有 63 测试全过 | `pytest` 通过 |

### 2.4 风险

极小，几个注入点。改完跑全量测试即可。**建议作为本次唯一落地的变更。**

---

## 3. 优先级 P1 — Session 事件源 + 表面投影（大改，须走预检门）

> ⚠️ **触发大事预检门**：维度多（存储/投影/兼容/前端/审计）、易漏边界、做完难返工、需拍板。**动手前必须**：① 本文 BFS 需求树评审 ② 定 P0/P1/P2 ③ 用户确认维度权重底线。**本文只给树 + 方案，不动手。**

### 3.1 BFS 需求树（枚举到叶子）

```
目标：会话 "全量 JSON 重写" → "append-only 事件日志 + 投影" 
├─ N1 事件模型定义
│   ├─ N1.1 事件类型枚举：user_msg / assistant_msg / tool_call / tool_result /
│   │        meta(title) / pin / delete / system
│   ├─ N1.2 每事件字段：{seq, ts, type, surfaceOp, payload, model_visible}
│   └─ N1.3 "model-visible means logged"：进模型的东西必须能从日志重建（断言）
├─ N2 存储层
│   ├─ N2.1 每 session 一个 append-only 文件（.jsonl / 序号分段）
│   ├─ N2.2 追加语义：`session/{id}.log` 只尾追加，天然抗并发/抗部分写
│   ├─ N2.3 深冻结：payload 不可变，无共享对象被 mutate
│   └─ N2.4 落盘原子性（append 窗口用 O_APPEND 或单锁）
├─ N3 投影层
│   ├─ N3.1 derive_messages()：从 log 投影出模型可见 message 数组
│   ├─ N3.2 增量缓存：每 surface node 只投影一次，O(new) 而非 O(all)
│   ├─ N3.3 compaction/replace：超长时按边界截断，generation 递增重建投影
│   └─ N3.4 fork：从某 seq 分叉（session fork，可选，P2）
├─ N4 兼容层（关键，防破坏线上）
│   ├─ N4.1 保持 load_session/save_session 现有签名（调用方无感）
│   ├─ N4.2 旧 JSON 会话一次性迁移：读旧 → 转事件 log → 删旧或留备份
│   └─ N4.3 list_sessions/search_messages 改读投影或 meta
├─ N5 元数据分离
│   ├─ N5.1 title/pinned/message_count/exchanges 移入 meta（频繁更新不重写 log）
│   └─ N5.2 修 update_session_meta 非原子截断写 → 改为 meta 文件原子写
├─ N6 检查点 / 回溯 / 审计
│   ├─ N6.1 事件 seq 提供天然 checkpoint（断点续跑）
│   ├─ N6.2 可复现：给定 log 必得同投影
│   └─ N6.3 审计：危险操作作为事件入 log（衔接已有 audit）
├─ N7 前端 / 接口影响
│   ├─ N7.1 会话列表/详情返回字段变化（session/<id>/messages 等）
│   ├─ N7.2 SSE 增量推送（新事件 → 投影增量 → 前端）
│   └─ N7.3 移动端（wechat-mp）会话列表兼容
└─ N8 测试
    ├─ N8.1 事件写读 + 投影一致性
    ├─ N8.2 旧 JSON 迁移正确性（含损坏文件）
    ├─ N8.3 并发 append 不丢事件
    └─ N8.4 超长 compaction / fork 边界
```

### 3.2 推荐方案（事件源核心）

**存储**：每 session 一个 `{id}.log`（JSON Lines 追加式）。**投影**：`derive_messages(log)` 返回模型可见数组，带 generation 缓存。**meta**：`{id}.meta.json`（标题/置顶/计数），与 log 分离，原子写。

**关键平衡**：不要为了"纯粹事件源"而破坏 N4 兼容层。**优先保 load_session/save_session 签名不变**，让内部从"全量 JSON"换成"事件 + 投影"，对外接口无感。这样风险集中在存储层内部，前端/调用方零改动可先行验证。

### 3.3 验收标准（客观可查证）

| # | 验证 | 方法 | 断言 |
|---|---|---|---|
| C2.1 | 旧会话可读 | 有存量旧 JSON，升级后 load_session 返回相同 messages | 前后 messages 逐条一致 |
| C2.2 | 事件源可复现 | 同一 log 两次 derive_messages | 结果完全相同 |
| C2.3 | 增量正确 | 追加 N 个新事件后投影 | 只新增 N 个，旧投影不变（缓存命中） |
| C2.4 | 原子性 | 中途 kill 进程 | log 不损坏，可读 |
| C2.5 | 检查点 | 从某 seq 恢复 | 消息数 == seq，无丢失 |
| C2.6 | 元数据分离 | 高频改 title/pin | 不重写消息 log，meta 原子更新 |
| C2.7 | 不回归 | 现有测试 + 新事件源测试 | pytest 全过 |

### 3.4 风险 & 预检门要点

- **破坏性**：改动会话核心存储，涉及迁移 + 前端 + 移动端。
- **多维**：N1-N8 八条线，N4 兼容层是最大风险（线上会话）。
- **难返工**：一旦出了新格式，回退要迁移。
- **拍板**：在动工前，用户需确认：A) 是否作为面试差异化主线（"我用事件源而非存 JSON"）；B) N4 兼容层成本（旧会话迁移）是否接受；C) 前端/移动端是否同步改。**三项权重由用户拍板。**

---

## 4. 优先级 P2 — 超时做成独立插件（seam）

- **dsh 模式**：`tool-call-timeout-policy` 是 `tools/execute` 的 wrapper，不是内置硬编码。
- **落点**：核实灵枢超时是否硬编码在 `core/tools/registry.py` 或执行层；若是，抽成可替换的 timeout policy seam。
- **验收**：注入自定义 timeout 策略（如按工具类别不同超时）生效，默认行为不变，测试通过。

---

## 5. 优先级 P2 — 能力缝三角色哲学（plugin_loader 重接原则）

- **dsh 哲学**：无特权核心（"There is no privileged core to patch"），能力缝分 Definition/Provider/Consumer，微内核只调度不硬编码。
- **落点**：重接 `plugin_loader.py` 时，按此设计——核心只做能力注册/发现，不内置任何具体能力；插件以 provider/consumer 三角色接入。
- **验收**：新插件免改核心即可注册并提供能力；核心不 import 任何具体插件实现。

---

## 6. 不做什么（防过度工程）

- **不重复造轮子**：已有 approval 门、Supervisor-Worker、审计、熔断、预算——dsh 没给更新。
- **不迁移依赖 dsh 产品**：dsh 是 dev-preview 0.1.1-rc.2，天天 breaking，只抄思想。
- **不给 Hermes 改 core**：Hermes 是第三方产品，改核心会被升级覆盖；它只需要我在操作层面用对成本/审批纪律（已在 agent SOUL 内化）。
- **不把 P1 当本轮任务**：P1 是"比较大的事"，必须先走预检门 + 用户拍板，本轮只落 P0。

---

## 7. 建议实施顺序

1. **本轮**：落 **P0**（成本纪律三小项）——便宜、稳、可出验证证据、面试能讲"空轮短路边省钱 + 触顶不静默降级"。
2. **下一步（可选项）**：P2 超时插件化（若已内嵌）+ 能力缝哲学用于 plugin_loader 重接。
3. **待拍板**：P1 会话事件源——先按本文 3.1 需求树 + 3.3 验收标准评审，用户在 3.4 三项权重上拍板后，才进入设计定稿 + 实施。

---

*文档结束。改动点以 `src/agent_harness/` 真实路径为准；P0/P2 可随时动手，P1 需预检门。*
