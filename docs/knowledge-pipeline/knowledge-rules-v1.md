# 灵枢防复现规则库 — v1（从 daily/2026-08-21.md 结构化抽取）

> 目的：把"事后记进 daily 的教训"变成"事前拦截规则"。
> 触发机制：当 zwq/Hermes 即将执行某类动作时，规则库返回应拦截的坑。
> 数据源：daily/2026-08-21.md（31KB 天量教训，20+ 条真实踩坑）。
> 结构：{trigger(触发条件), lesson(教训), block_hint(事前该怎么做)}。

## 规则 R-01   启动 venv Python
- trigger: 即将用 `.venv/Scripts/python.exe` 或任何 venv 内的 python 运行脚本/服务
- lesson: Hermes 会话环境注入 PYTHONPATH=hermes-agent/venv → 劫持任何 venv python → ModuleNotFoundError（pydantic_core 等）。Flux 修复链曾被拖垮，ComfyUI 启动失败
- block_hint: 启动前必须 `unset PYTHONPATH`。检验：`./.venv/Scripts/python.exe -c "import sys; print(sys.path[0])"` 确认是项目 venv 而非 Hermes venv

## 规则 R-02   创建 cron
- trigger: 即将创建/更新一个 cron job
- lesson: cron 的 `script` 字段**不支持带参数**——`'env_doctor.py --quiet'` 被当完整文件名 → "Script not found"。导致 disk-cleanup 5 天没跑、env-doctor 失败
- block_hint: 带参数必须建**无参 wrapper 脚本**（内部 subprocess 传参）；建完必须 `cronjob run` 验证首次执行

## 规则 R-03   信任一个工具/Skill
- trigger: 即将使用/信任一个新的工具、脚本、Skill、MCP（尤其是"看起来很深"的）
- lesson: 全库曾查出 2 个 **mock 假工具**（vlm_calibrate actual=expected+0.3 写死；cot_engine 多路/取证/交叉验证全是模板占位符）。"状态深≠干活行"
- block_hint: 使用前必须抽检实证——真的跑一次，看输出是否符合预期，不凭"看起来专业"信任

## 规则 R-04   自称"最佳实践"并动手
- trigger: 即将说"这符合业界最佳实践/这很标准"并据此动手改造
- lesson: 曾凭印象自称"索引14K=浪费"并动手压缩 → 被用户纠正"没先对标业界"。动手前先查官方文档
- block_hint: 自称最佳实践前，先第 1 步对标官方/论文/成熟实现，引用来源；未对标不得动手。"对标-穷举-验证"三件套第一步不可跳过

## 规则 R-05   建完 cron / 写成脚本
- trigger: 建完 cron、写完一个工具/脚本
- lesson: "建 cron 后必须立刻验证首次执行"；工具要"从手动验证到可回归（有测试）"，否则只能靠手动验证、没保护
- block_hint: 建 cron → 立即 `cronjob run`；写工具 → 补最小测试（测试的价值是发现"没想到的错"，不是"验证写对了"）

## 规则 R-06   机器级工具验证
- trigger: 即将交付/使用一个声称"经过验证"的安全性/底层工具（shellcode/fuzzer/逆向）
- lesson: 安全产出必须机器码级/字节级验证（如 gs:[0x60]→Ldr→导出表→哈希），不是"跑通了就行"
- block_hint: 底层工具交付前做字节级/机器码级核对，打印真实数据，不靠"应该对"

## 规则 R-07   显示/显存判断
- trigger: 即将在 12G 显卡上跑一个重型模型（如 Flux.2 Klein 9B）
- lesson: Flux.2 Klein 9B 需求 17.2G > 12G → 动态加载死锁（VBAR 换页风暴）。先算需求再跑，别盲目"试试"
- block_hint: 跑前先算显存需求（后端+模型），超卡则换 GGUF 量化 / 降级到 SDXL+Wan2.2 系；12G 实用上限=SDXL 系+Wan2.2 GGUF

## 规则 R-08   知识/记忆正确性
- trigger: 即将自动提取/写入一个 fact / 知识条目（尤其经 LLM 反射器自动提取）
- lesson: 反射器自动提取 fact 364 有错（mistral vs qwen_3_8b 混淆）→ 自动管道需要人工校正
- block_hint: 自动提取的知识必须人工校一遍；记忆质量审计的价值在（审查自动管道，而非只记流水）

## 规则 R-09   成本敏感操作
- trigger: 即将跑一个可能高成本的会话/任务（尤其远程 API、超长上下文）
- lesson: 10 天烧 ¥401.5，免费占比 0%——本地免费模型从未进主对话；in>300K 占 49% 成本；冷启动(cache=0)单次 ¥0.56
- block_hint: 先 `cost --estimate` 预飞行，识别任务类型→选免费/付费模型；超长上下文先压缩；冷启动重试要注意成本

## 规则 R-10   上下文/循环
- trigger: 即将在同一个会话里反复重试同一参数/同一错误
- lesson: 同参数错误≥3次=循环；连续 5 条无工具调用=思考空转；跑飞烧 token 是真实风险（$12+ 事故）
- block_hint: 同参数失败 2 次就换方法论；熔断（3次内同失败就停）；察觉空转立即给结论或行动
