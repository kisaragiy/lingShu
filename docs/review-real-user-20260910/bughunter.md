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