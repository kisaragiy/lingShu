# A 线设计文档：灵枢 harness 工程补齐（2026-10-16）

> 状态：**待 zwq 拍板** · 未启动 · 大字预检门第 ①②③④ 步产物（设计 + 需求树 + 分级 + 权重底线）
> 依据：`frontier-scan-2026-10-16.md`（B 线 8 源扫描）· 灵枢实测侦察（本文件 §2）

---

## 1. 目的

B 线扫描结论：**新增 0 条给 Hermes、2 条给灵枢**。本文档把「迁移目标」落成可执行设计，回答：
- 灵枢 harness 维度**真缺口**到底是什么（不靠推断，靠实测）
- 先做哪条、怎么验、做到什么程度算完

---

## 2. 现状实测（2026-10-16 侦察 · 诚实修正）

灵枢仓库 = `C:/Users/zwq/agent-harness/`，v0.75.x，71 测试文件 / **368 passed**。

| 维度 | 实测 | 判定 |
|---|---|---|
| **多智能体编排** | `core/agents/supervisor.py`(470) + `workers.py`(587) + `core/graph/graph_multi.py`(903) | **已实现** |
| **并行 fan-out** | `graph_multi.py:257` `ThreadPoolExecutor(max_workers=len(parallel_workers))` + `as_completed` | **已实现** |
| **安全/危险分组** | `_partition_workers()`：只读 Worker 可并行、副作用 Worker 串行化（防桌面/ComfyUI 冲突） | **已实现（比 Anthropic 基线更细）** |
| **对冲 / 熔断 / 取消** | `core/hedge.py`（对冲请求）· 无进展熔断 · 派发前取消检测 | **已实现** |
| **引用溯源** | `core/safety/provenance.py` + `report_formatter._format_citations`（URL→`[N]` 编号）+ `test_report_citations.py` | **部分**：是格式化，**非独立验证 agent** |
| **评测** | `eval_results/`（drift-trend.jsonl）· `scripts/eval_drift_sentinel.py` · test_online_eval / module_eval / rag_eval | **有单元级，无 benchmark** |
| **benchmark 接入** | `grep benchmark/tau/OSWorld/Terminal-Bench` → **0 命中** | **零接入 = 真真空** |

### ⚠️ 重要修正：A 线 P0-a 的原始描述被推翻

10-16 裁定把「多智能体并行编排」列为**唯一真能力缺口**。实测显示：**灵枢早已通电，且比 Anthropic 基线更细**（安全/危险分组 + 对冲 + 熔断，Anthropic 原文未提副作用串行化）。
→ **P0-a 从「从零建编排」缩窄为「补两处真残余」**（见 §4 分支 3）。
> 这正是 SOUL 说的「装了没通电」复发病的反向检查：**先求证，再下料**。此处结论 = 「已通电」，不再重复建设。

---

## 3. BFS 需求树（广度枚举，到不可再分为止）

```
A 线根：让灵枢在 harness 维度具备「第三方可证伪数字 + 一手对标叙事 + 真残余已补」
│
├─ B1 基准数字（P0-b）★ 真真空
│   ├─ B1.1 选型 —— τ²-bench / Terminal-Bench / OSWorld / SWE-bench
│   │   ├─ B1.1.1 能否本地离线跑（无云依赖）？
│   │   ├─ B1.1.2 是否对口灵枢能力域（多轮工具/检索/分析）？
│   │   └─ B1.1.3 授权许可（MIT/Apache？能否公开数字）
│   ├─ B1.2 适配层 —— 灵枢 agent 接口 → benchmark 协议
│   │   ├─ B1.2.1 输入/输出格式对齐
│   │   ├─ B1.2.2 工具集映射（benchmark 要求 vs 灵枢提供的能力）
│   │   └─ B1.2.3 失败模式归一（超时/拒答/幻觉如何计分）
│   ├─ B1.3 跑基线 + 出数字（本地模型 + 远程模型各一档）
│   ├─ B1.4 落 README（可证伪声明：模型 + 日期 + 命令可复现）
│   └─ B1.5 成本闸：单次跑批 token/时间上限（防烧钱）
│
├─ B2 一手对标叙事（P0-c）★ 零代码最高性价比
│   ├─ B2.1 逐层盘点 core/ 落位 ETCLOVG 七层
│   │   ├─ E Execution      → 执行环境/沙箱/工作目录
│   │   ├─ T Tool           → tools/ + ACI 原则
│   │   ├─ C Context        → 记忆/压缩/context 隔离
│   │   ├─ L Lifecycle      → supervisor/workers/graph_multi（★最厚）
│   │   ├─ O Observability  → observability.md / 审计 / 指标
│   │   ├─ V Verification   → eval/judge/溯源
│   │   └─ G Governance     → 预算/准入/安全闸（★最薄）
│   ├─ B2.2 标出缺口层（L/G 之外哪层也薄）→ 决定是否补
│   ├─ B2.3 写 `docs/architecture-harness-layers.md`（含图）
│   └─ B2.4 README 摘要段（面试首屏可讲）
│
├─ B3 编排真残余（P0-a'）☆ 缩窄后的真缺口
│   ├─ B3.1 独立溯源验证 agent（Anthropic CitationAgent 模式）
│   │   ├─ B3.1.1 现状：仅 URL→[N] 格式化，无「claim↔source 一致性校验」
│   │   ├─ B3.1.2 设计：report 定稿前过一道 verify agent，逐 claim 核源
│   │   └─ B3.1.3 失败处理：核不上的 claim 标黄/剔除（不静默通过）
│   ├─ B3.2 动态子代理（现固定 search/analyze/execute 三角色）
│   │   ├─ B3.2.1 是否真要动态？——**先评估 ROI**（Anthropic 自述编码类不宜并行）
│   │   └─ B3.2.2 若做：supervisor 按任务生成 N 子任务而非选固定角色
│   └─ B3.3 编排可观测（拓扑图已有 → 补每 Worker 时序/成本）
│
├─ B4 ACI 强制入库（P1）—— 原则齐、未强制
│   └─ B4.1 把工具设计原则做成 CI 门禁（非人工自觉）
│
├─ B5 KV-cache 命中率采集（P1）—— Manus 列为 agent 成本第一约束
│   └─ B5.1 现有 `.usage.json` / llm_metrics 是否已采？→ 补命中率维度
│
└─ B0 工程卫生（前置，P0）—— 未提交改动先清
    └─ B0.1 `M tools/harness/harness.py`(+12/-2) + `?? test_wp5_harness_conformance.py` 待收口
```

---

## 4. 分级与权重底线

| 级别 | 项 | ROI | 工作量 | 依赖 |
|---|---|---|---|---|
| **P0** | **B0 工程卫生**（清未提交） | 必做前置 | ~10 min | — |
| **P0** | **B2 ETCLOVG 映射**（P0-c） | ★★★ 零代码、一手综述背书、面试直接讲 | ~半天 | B0 |
| **P0** | **B1 τ²-bench 接入**（P0-b） | ★★★ 唯一能产出「可证伪数字」= 护城河 | 1–3 天 | B0 |
| **P1** | **B3.1 独立溯源验证 agent** | ★★ 补真残余、有 Anthropic 一手对标 | ~1 天 | B0 |
| **P1** | B4 ACI 强制 · B5 KV-cache | ★ 懂行细节、低成本 | ~半天 | — |
| **P2** | B3.2 动态子代理 | 低（ROI 待评估，可能不需要） | — | B3.1 |
| **不做** | 重造编排引擎 / 换框架（LangGraph→MS） | — | — | 定位选择，非能力缺口 |

### 维度权重底线（判定「A 线成功」的门槛）

| 维度 | 权重 | 底线（不达标 = 不算成功） |
|---|---|---|
| **可证伪数字** | 40% | ≤200 字内给出「模型 + benchmark + 分数 + 可复现命令」；数字必须实测非估算 |
| **一手对标叙事** | 30% | ETCLOVG 七层有落位文档 + 明确标出薄层；引用带 URL+日期 |
| **真残余补齐** | 20% | 溯源验证 agent 有测试覆盖（claim 核不上必须可见失败） |
| **工程卫生** | 10% | 无未提交改动；`pytest` 全绿；AGENTS.md/README 同步 |

**总门槛：加权 ≥ 80% 才算 A 线达标**；任一项 0 分则整体不达标（短板否决）。

---

## 5. 验收标准（客观可查证）

- [ ] B0：`git status -s` 干净 · `pytest -q` 368+ passed
- [ ] B1：`README.md` 出现 benchmark 分数段 · 附带可复现命令 · 本地实测跑通 ≥1 次
- [ ] B2：`docs/architecture-harness-layers.md` 存在 · 七层全覆盖映射 · 薄层有标记
- [ ] B3.1：新增 verify-agent 模块 + 测试 · 构造「claim 与源不符」用例必须被拦
- [ ] 全项：`pytest` 无回归 · AGENTS.md 版本/测试数同步

---

## 6. 不做什么（明确排除）

- ❌ 不重造多智能体编排引擎（已通电，重复建设 = 浪费）
- ❌ 不给 Hermes 打补丁（第三方 repo，进不了作品集）
- ❌ 不换框架（LangGraph → 其他）——定位选择，非能力缺口
- ❌ 不为 benchmark 刷分做数据倾斜（数字要真）
- ❌ 不动 `.env` / 凭据

---

## 7. 待拍板项

1. **起跑顺序**：建议 **B0 → B2（半天见效）→ B1（数字护城河）→ B3.1 → P1**
2. **B1 选型**：τ²-bench 优先，还是先做更轻的 Terminal-Bench？需 zwq 定
3. **B3.2 动态子代理**：做不做（我倾向**不做**，ROI 低，与 Anthropic 「编码类不宜并行」一致）
4. **成本闸**：B1 跑批单次上限（我建议：本地模型跑主流，远程仅抽样；单次 ≤¥5）
