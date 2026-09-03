# 灵枢 Day 1：架构剖析与可靠性现状评估（2026-09-04）

> 两周计划 Day 1-2（架构剖析→标单点故障），为 Day 3-4（熔断降级硬化）提供基线。
> 结论先行：**「三重熔断」名不符实——真实生效只有超时 1 重；token 预算和无进展检测从未被喂数据，永远不会触发。** retry.py 和 degradation.py 是「没接线的孤儿模块」。

---

## 一、架构调用图（可靠性视角）

```
用户 → FastAPI (:8788) → LangGraph 图
        ├─ graph.py        (单 Agent 路线: planner → executor_node 循环)
        └─ graph_multi.py  (多 Agent 路线: Supervisor → Worker 并行 → 验收 → Replan)
                │
        LLM 调用链: call_deepseek → _post_cloud → (失败降级) call_llama
                     (云端 timeout=120 单次)   (本地 llama timeout=300 单次)
                │
        工具: 40+ (搜索/代码/桌面/浏览器/RAG)  · 每步 call_tool
                │
        可靠性触点:
          ├─ CircuitBreaker  ← graph_multi.py:350 状态里每任务新建
          │                     graph.py:96 / graph_multi.py:358 仅 CHECK
          ├─ budget.py TokenBudget  ← 独立类（另一处，未与 breaker 联动）
          ├─ retry.py with_retry    ← 零使用（孤儿）
          ├─ degradation.py         ← 零使用（孤儿；真实降级在 llm.py/search 内联）
          └─ llm.py 缓存 + cloud→local 内联降级
```

## 二、可靠性机制真实状态（🔴 三个大发现）

| 机制 | 实现 | 接线 | 真实生效 |
|------|------|------|---------|
| **三重熔断 token 预算** | CircuitBreaker.add_tokens() 有 | **从未被调用**（全库仅 docstring 出现） | ❌ 永不触发（tokens_used 恒 0） |
| **三重熔断 无进展检测** | CircuitBreaker.record_output() 有 | **从未被调用** | ❌ 永不触发（last_outputs 恒空） |
| **三重熔断 超时** | check() 里 elapsed>600s | 每任务新建实例→计时有效 | ✅ 唯一真在工作的 |
| **重试退避** | retry.py with_retry 完整（指数+抖动） | **零使用**（无任何 @with_retry） | ❌ 孤儿模块；LLM 调用无重试，一次失败即降级/返回空 |
| **降级链框架** | degradation.py + 矩阵完整 | **零使用** | ❌ 孤儿模块；真实降级是 llm.py(云→本地)/search(SearXNG→DDG→skill) 内联 if/except |
| **LLM 缓存** | llm.py MD5 缓存 | 有 | ✅ 真实（temperature≤0.5） |
| **cloud→本地降级** | llm.py _post_cloud except → call_llama | 有 | ✅ 真实（单次、无重试） |

## 三、单点故障 / 面试会戳破的点

1. 🔴 **「三重熔断」= 1/3 实装**。面试官问「token 预算怎么累加？无进展窗口几轮、对比什么？」——现状答不上，因为从没触发过。**这是 Day 3-4 头号硬化目标。**
2. 🔴 **LLM 调用零重试**：云端/本地一次失败直接返回空/降级，无退避。真线上抖动一次就断。
3. 🟠 **熔断器与 budget.py(TokenBudget) 不联动**：两套预算逻辑各管各，无统一记账。
4. 🟠 **孤儿模块**：retry.py / degradation.py 是好代码但没接线——面试被问「你 retry 用在哪」会露怯；要么接线要么坦白「框架层有、未全接入」。
5. 🟠 **超时硬编码**：llm.py timeout=300/120 写死，breaker 600s 写死，无可配置入口。
6. 🟡 降级只覆盖 llm/search；工具层（ComfyUI/浏览器）失败多数直接返回 error 给 worker，无统一链。

## 四、Day 3-4 硬化计划（据此推进）

1. **P0 把熔断器喂起来**：在 graph.py executor_node + graph_multi.py 每轮 worker 完成后调 `cb.add_tokens(本轮消耗)` + `cb.record_output(本轮输出摘要)`——让 token/无进展两重真正触发；LLM 调用层（llm.py）返回 token 数并回传。
2. **P0 补 LLM 重试**：call_llama/_post_cloud 挂 `with_retry(3, 1.0)`（ConnectionError/TimeoutError/OSError 可重试）——顺带让 retry.py 从孤儿变实装。
3. **P1 统一记账**：TokenBudget 与 CircuitBreaker 联动或明确职责边界（一个管任务级熔断、一个管会话级预算）。
4. **P1 配置化**：超时/阈值进 config（config_manager），不硬编码。
5. **P1 接线 degradation 或删除**：把 llm/search 内联降级收口到 call_with_degradation，或标注为「未接入框架」避免简历/面试夸大。
6. 验收（Day 7）：写测试证明——token 超限触发熔断、连续无进展触发、重试次数与退避、降级链生效；指标：熔断触发率/降级成功率可测。

## 五、方法
- 源码 grep 定位接线点（`add_tokens(`/`record_output(`/`@with_retry`/`call_with_degradation(` 全库零真实调用）
- 读了 circuit_breaker.py / degradation.py / retry.py / llm.py 调用链 + graph 执行循环
- 纯分析，未改代码、未提交
