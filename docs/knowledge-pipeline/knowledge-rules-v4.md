# 灵枢防复现规则库 v4 — 工程可靠性线（从 2026-09-04 可靠性/安全/成本实战抽取）

> v4 工程可靠性线：来源 = 灵枢 Day1-7 可靠性工程硬化（熔断接线/fail-closed/指标压测）
> + cost_ledger 费率盲点 + delegation key 误判 + worktree 工件测试。
> 与 v1(技术复现 01-10)/v2(AIGC 11-21)/career(求职 22-30) 风格一致：每条=真实踩过+有明确"事前"拦截。
> 规则编号从 R-31 起（v3=career R-22~30）。

## 规则 R-31   可靠性机制：验"接线"不验"存在"
- trigger: 即将宣称"实现了熔断/降级/重试/XX机制"，验收前
- source: docs/architecture-review-day1-20260904.md + 灵枢 v0.75.1
- lesson: 灵枢"三重熔断"实测只有超时生效——add_tokens()/record_output() 全库从未被调用，token 预算/无进展检测永不触发；熔断器只在运行结束后查一次、运行中拦不住失控。文档说"已实现"≠ 真接线，"写了类"≠"喂了数据"
- block_hint: 验收前 grep 全库调用点（add_tokens/record_output/@with_retry/call_with_degradation）；把"写了吗"升级成三问——"喂了吗/在循环里调了吗/真会触发吗"，再写测试证明触发

## 规则 R-32   安全护栏：fail-open 是反模式
- trigger: 即将写 `except ImportError: pass` 或"降级为无护栏/向后兼容"
- source: 灵枢 v0.75.2（safety fail-closed 硬化）
- lesson: registry.py 两处 ImportError 静默降级=护栏坏了时裸奔（权限检查降级成全放行）；安全保守原则要求 fail-closed——权限模块坏→抛错拒绝；safety 坏→不可逆操作拒绝 + CRITICAL 日志
- block_hint: 护栏异常路径必须 fail-closed + 响亮日志；用 sys.modules[模块]=None 写测试证明"护栏坏时会拒绝，不是假装安全"

## 规则 R-33   子代理/远程调用失败：先探 key/额度，再归因模型
- trigger: 即将把 delegation 失败/输出截断/max_iterations 归因于"模型缺陷"
- source: fact_store #666 修正（2026-09-04）
- lesson: 百炼连续 5 次"模型截断"实为 key 异常——key 修好后同样长分析任务全过。把 key/401/额度问题误判成模型缺陷，会带偏整个修复方向、白烧免费额度
- block_hint: 远程调用失败先 quick probe 验证 key 有效性/401/额度，再谈模型行为；写入记忆/fact 时标"待定因"，别急着固化"模型缺陷"结论

## 规则 R-34   成本看板：费率表必须覆盖所用模型
- trigger: 即将用 cost_ledger / cost --real 下"没烧钱/¥0"结论前
- source: cost_ingest.py PRICES 盲点（2026-09-04 实测）
- lesson: 主脑模型 deepseek-v4-flash-vision-exp 不在 cost_ingest PRICES 费率表 → 6688 次调用/11.29M token 全计 ¥0，成本看板主通道隐形，¥845 实际低估约 17%
- block_hint: 新模型接入先补 PRICES 费率；看到 ¥0 先问"这模型真免费还是没配费率"；成本结论必须对账模型清单

## 规则 R-35   测试失败先分清"回归"还是"环境工件"
- trigger: 即将把 worktree/CI 里某个测试失败当成自己改出来的回归来修
- source: test_rev_utils 工件失败（2026-09-04）+ AGENTS.md 环境依赖记录
- lesson: worktree 里 test_ghidra_scripts_exist 失败=reference/ghidra_scripts 是 gitignore 本地文件没带进 worktree（主仓库同测试通过）；test_cs_stream 需 :8788 服务 / test_llm_cache 需 :8080 = 文档注明的环境依赖。把工件/环境依赖误当回归会白修一通
- block_hint: 测试失败先查它依赖什么（本地 gitignore 文件？服务？env？）：grep 测试引用 + git check-ignore + 对照主仓库同测试是否过；AGENTS.md 已注明的环境依赖失败 ≠ 回归
