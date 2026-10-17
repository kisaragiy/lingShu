# LWP17/18/19 接线详设（2026-10-17）

> 目的：三模块「有芯未接线」收口。代码与测试已在仓库（LWP17 15 tests / LWP18 11 tests / LWP19 19 tests，全绿），
> 但生产编排链 `grep -rn 'failure_attribution\|module_eval\|skill_lifecycle' src/ --include=*.py`（排除自身与测试）**零引用**
> —— 装好了没通电。本设计给出每一条的接线点、行为契约、验收标准与串行顺序。

---

## 1. LWP17 失败归因接线（`core/graph/graph_multi.py`）

### 目标
收尾时若完成契约存在「未完成项」，**自动归因到决定性步骤**，把「谁把整条链拖垮」写进 trace 与报告
—— 直接对治「观测到失败但不暴露根因」这一复发模式。

### 接线点
`supervisor_finalize(state)`，锚点：`contract = build_completion_contract(state)` 之后、
`if state.get("feasibility") == "infeasible":` 分支之前。

### 接口（已存在，零改动）
```python
from ..failure_attribution import attribute_from_run_result
report = attribute_from_run_result(state)     # state 即 SupervisorState(dict)，直接可传
# report.decisive -> tuple[str, str] | None    # (agent, action) 或 None（无决定性根因）
# report.to_dict() -> dict                     # 可 JSON 序列化，直接进 trace
```

### 行为契约（可机械验证）
1. `contract["complete"] is True` → **完全不调用**归因（零开销、final_output 与 trace 逐字节不变）。
2. 有 incomplete 项 → 调用归因，`state["trace_steps"].append({"step": "failure_attribution", **report.to_dict()})`。
3. **仅当** `report.decisive is not None` → final_output 追加小节 `## 🔍 失败归因`（决定性步骤 + 置信度 + 症状链）。
4. 归因抛任何异常 → 捕获 + stderr 记录 + **跳过**（保守原则：绝不因归因失败中断收尾）。
5. 归因是**纯确定性、零 LLM 调用**，不改变 contract、不改变 worker_results。

### 验收
新增 `tests/test_graph_failure_attribution_wiring.py`：
- (a) 全成功 state → finalize 输出**无**「失败归因」段，trace 无 `failure_attribution` step。
- (b) 一个 worker 失败 + finalize 依赖全 worker → 报告含「失败归因」，`decisive[0] == 该 worker 名`。
- (c) 非法 state（`worker_results` 为 str）→ 不抛异常、正常收尾、跳过归因。
- 全量回归：`pytest -q` 仍 **703 基线 + 新增 passed / 2 skipped**。

---

## 2. LWP18 core 模块评测接线（新增脚本 + 回归门禁）

### 目标
把「换实现能不能区分好坏」的探针跑成**可执行基线门禁**，而不是躺在仓库里的单测。

### 现状接口
- `LingShuModules`（retry / degradation / budget / circuit_breaker 四模块 bundle，默认真实实现）
- `evaluate_all(bundle) -> EvalReport`；`distinguishes(good: EvalReport, bad: EvalReport) -> bool`
- **缺**：无任何入口调用；只覆盖 4 模块（其余 core 模块 = P2）。

### 接线点（新文件，零冲突面）
- 新脚本 `scripts/eval_modules.py`：
  - 主体：`evaluate_all(LingShuModules())` → 打印各模块 score → 写 `eval_results/modules-<YYYYMMDD>.json`
  - 返回值契约（遵循 `scripts/lib/contract.py`）：`0`=全模块 score==1.0；`2`=有模块 <1.0（ALERT）；`1`=执行异常（ERROR）
- 回归门禁 `tests/test_module_eval_gate.py`：断言 `distinguishes(good_bundle, bad_bundle) is True`
  —— 防探针本身退化成「好坏都满分」。

### 验收
- `python scripts/eval_modules.py; echo $?` 实测输出 + 退出码语义正确。
- 故意把某模块换成永远失败的实现 → `distinguishes` 返回 `True`。
- 全量回归不破。

---

## 3. LWP19 技能生命周期接线（新建灵枢自有加载器）

### 现状（关键事实，先于设计）
- `skill_lifecycle` 原语齐备：`SkillRecord.from_skill_meta(meta)` / `loadable_skills(records)` /
  `assess(records, policy) -> LifecycleReport`；`LifecyclePolicy`（迟滞阈值 + 使用量门槛，构造时校验）。
- **但灵枢没有自己的 skill 加载器**：`apps/research/api.py:1314` 的 `_scan_skills()` 指向
  **Hermes 的 skills 目录**（`%LOCALAPPDATA%\hermes\skills`），且**只列目录名、从不把 skill 内容注入 prompt**。
  → 两个问题叠加：① 跨产品耦合（ADR-001 遗留）；② 生命周期完全没接。

### 接线点
- 新建 `src/agent_harness/core/skills/loader.py`（灵枢自有）：
  - `scan(roots: list[Path]) -> list[SkillRecord]`：遍历 `*/SKILL.md`，解析 YAML frontmatter → `SkillRecord.from_skill_meta`
  - `select_prompt_skills(records, policy) -> list[SkillRecord]`：`loadable_skills()` 过滤 → 按 `fitness()` 降序 → 按 token 预算裁剪
  - `render_skills_prompt(records) -> str`：渲染成 system prompt 片段（name + 描述 + 触发词）
- 注入点：supervisor/worker 的 system prompt 组装处（实施时在 `core/pipeline/` 定位 prompt 构建函数）。
- 解耦：`api.py` 的 `_scan_skills` 改读灵枢自己的 `config.SKILLS_DIR`，**消除指向 Hermes 目录的硬编码**。

### 验收
- `tests/test_skill_loader.py`：frontmatter 解析 / `loadable` 过滤（retired 不进）/ fitness 排序 / 预算裁剪。
- 端到端：造一个 mock skill 目录（含一个 retired 记录）→ 渲染出的 prompt **含** loadable、**不含** retired。
- 全量回归不破。

---

## 4. 顺序与理由（**不可并行**）

| 步 | 项 | 理由 |
|---|---|---|
| 1 | LWP17 | 只动 `graph_multi.py` |
| 2 | LWP19 | 动 skill 加载器（新建）+ `graph_multi.py` prompt 注入点 → **与 17 冲突面重叠** |
| 3 | LWP18 | 新脚本独立，可任意插空；跟随串行以降认知负荷 |

每条标配：改 → `pytest -q` 全量 → 贴证据 → 小步 commit。**回归不通过不得进入下一条。**

---

## 5. 不做（明确边界）
- 不给 LWP18 补其他 core 模块探针（P2：先把 4 模块门禁跑起来）。
- 不改 `LifecyclePolicy` 默认阈值（等真实 episode 数据再调，现在调=拍脑袋）。
- 不引入 LLM 参与归因或生命周期判定（确定性优先，可复现优先）。
