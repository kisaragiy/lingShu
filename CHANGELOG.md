# Changelog

## v0.47.0 — 2026-07-11

### Added — 测试体系
- 🧪 **测试文件**（56 个单元测试 + 6 个集成测试 = 62 个）：
  - `tests/test_search_chain.py` — 搜索链单元测试（mock HTTP，5 场景：3 DDG 策略 + 全失败 + SearXNG 优先）
  - `tests/test_llm_cache.py` — LLM 缓存测试（key 一致性、TTL、淘汰、高 temp 跳过、HTTP 错误）
  - `tests/test_api.py` — API 认证测试（JWT 创建/验证/过期/篡改/刷新）
  - `tests/test_cs_intents.py` — CS 意图分类测试（9 种意图 × 多变体 + 边缘情况）
  - `tests/test_report_citations.py` — 报告引用测试（[url]→[N]、去重、来源提取、URL 验证）
- ⚙️ **pytest.ini** — 超时 30s、`integration` 和 `slow` 标记、CI 排除集成测试
- 🔄 **CI workflow** 更新 — `-m "not integration"` 排除需服务器的测试

## v0.46.0 — 2026-07-11

### Added — 主应用体验打磨
- ⌨️ **快捷键**：`Ctrl+K` 聚焦会话搜索、`Ctrl+Shift+N` 新建对话（快捷键面板同步更新）
- 🏷️ **代码块语言标签**：代码块左上角显示语言名称（python/json/html 等），带圆角边框
- 🕐 **搜索建议**：输入框获取焦点时显示历史搜索词下拉（localStorage 持久化，最近 10 条）
- 📋 复制按钮 + 代码块已有实现，CSS 优化

### Added — Agent 核心增强
- 🔄 **LLM 响应缓存**：`call_llama` 和 `_post_cloud` 基于 (model, messages, temperature) 的 MD5 缓存，TTL 60s，上限 500 条，自动淘汰最旧
- 🔗 **报告引用格式**：`report_formatter._format_citations()` 将 `[url]` 转换为编号 `[N]`，自动去重，追加 `## 参考来源` 章节
- 🔍 **搜索链加固**：`web.py` 新增 DDG 第 4/5 解析策略、User-Agent 轮换（3 种）、URL 归一化去重（去尾部斜杠/www/UTM）、策略级诊断日志

## v0.45.0 — 2026-07-11

### Added
- 📖 **README 重写** — 面试官友好架构图、CS Demo 场景表、快速开始/Tech Stack/项目结构
- 🏷️ **GitHub 仓库产品化** — Topics（8 个）、CI workflow（`.github/workflows/ci.yml`）
- 🎧 **CS Demo 独立 exe** — PyInstaller 打包，19MB（原 881MB 的 1/46），双击运行
  - 精简 `cs_demo/server.py` — 仅 CS 路由，无 torch/transformers 依赖
  - 自动找可用端口、自动开浏览器、SSE 流式 + 模板回复
- 📦 **构建脚本** — `scripts/cs_demo.spec` + `scripts/build_cs_demo_exe.py` + `scripts/cs_demo_launcher.py`

### Changed
- README.md 完全重写（约 10KB），含 3 张截图占位 + ASCII 架构图
- 项目结构加入 `scripts/cs_demo/` 子包（server + 启动器）

## v0.44.0 — 2026-07-11

### Added
- CS Demo 场景扩充：
  - 🛒 **售前咨询** — 商品目录查询、对比、分期方案，10 款热销商品（`cs_query_product`）
  - 💰 **优惠查询** — 优惠券/促销活动查询，5 种可用优惠码（`cs_check_promotion`）
  - 📍 **地址修改** — 收货地址修改，含验证规则（`cs_modify_address`）
  - 🚚 **配送时效** — 订单级配送估算与通用时效参考（`cs_estimate_delivery`）
- Mock 数据大扩充：4→12 条订单（含待付款/已取消/已完成等多状态）、10 条 FAQ、5 条工单、5 种优惠券
- 前端 3×3 场景卡片（9 张）：新增 售前咨询、查优惠、改地址
- 意图分类器扩充：识别 售前咨询、优惠查询、地址修改（关键词库翻倍）
- 快捷回复自适应新场景：上下文感知的建议按钮
- `tests/test_cs_stream.py` — 新增 3 个测试：test_intent_presales / test_intent_coupon / test_intent_address

### Changed
- `customer_service.py`：重写为 v2，新增工具函数 + 商品目录 + 优惠券 + FAQ 内建库
- `cs_agent.py`：`_execute_tools` 新增 3 个意图分支；`_template_fallback` + `_get_quick_replies` + 系统提示词同步更新
- `api_fastapi.py`：`_get_cs_quick_replies` 同步更新
- `cs-demo.html`：3×3 场景网格、欢迎词重写、emoji 剥离正则更新

## v0.43.0 — 2026-07-10

### Added
- CS Demo SSE 流式回复：POST `/v1/cs/chat/stream` 端点
- 前端逐 token 流式渲染：意图→工具→回复实时展示，打字机光标效果
- `tests/test_cs_stream.py` — SSE 流测试文件

### Changed
- `cs_agent.py`: 新增 `_call_cs_llm_stream_tokens()` — 流式调用 LLM，超时缩至 `(5, 60)`s
- `api_fastapi.py`: 新增 SSE 端点，5 种事件类型（intent/tool/token/done）
- `cs-demo.html`: `csSend()` 重写为 SSE 消费者，think→stream 过渡动画
- LLM 不可达时优雅降级到模板回复

## v0.42.0 — 2026-07-10

### Added
- Splash 屏升级：深蓝渐变背景 `#1a1a2e → #2563eb`，80px 圆角 logo 脉冲 + 光晕动画
- 首页体验打磨：白色元素、32px spinner、状态栏脉冲动画
- `window.__VERSION__` 由 Python `__version__` 注入前端，根除硬编码

### Changed
- `index.html` 移除客服 Demo Tab（已迁移到独立页面 `/cs-demo`）
- 修复服务页 `v0.25.0`、设置页 `v0.31.0`、检查更新 `'0.28.0'` 三处硬编码版本号
- 设置页"关于"添加 CS Demo 链接

## v0.41.0 — 2026-07-10

### Added
- 客服 Demo 产品化包装：独立全屏页面 `/cs-demo`
- `static/cs-demo.html` — 品牌化 Splash 屏 + 左面板架构介绍 + 场景推荐卡片 + 沉浸式聊天 UI
- `run_cs_demo.py` — 一键启动脚本（`python run_cs_demo.py` → 自动打开浏览器）
- 场景推荐卡片：6 种预设场景（查订单/查物流/退换货/投诉/FAQ/转人工），一键演示

### Changed
- 客服页面不再是主应用 Tab，改为独立产品级页面，适合面试展示

## v0.40.0 — 2026-07-10

### Added
- CS Agent v2：由 LLM（DeepSeek Flash）生成自然、有同理心的回复
- 前端显示三步思考过程：🔍 意图分析 → 📋/⚡ 工具执行 → 🤖 LLM 生成回复
- 回复自动降级：LLM 不可达时回退模板回复

### Changed
- `agents/cs_agent.py` 重写：新增 `_call_cs_llm` + `_execute_tools` + `_template_fallback`
- API `/v1/cs/chat` 返回 `tool_results` + `quick_replies` 字段

## v0.39.0 — 2026-07-10

### Added
- 客服 Demo 初版：🎧 Tab 页 + Mock 订单/工单/FAQ + 意图分类
- `agents/cs_agent.py` — 规则引擎式客服 Agent（v1）
- `tools/customer_service.py` — Mock 数据（4 条订单、工单系统、FAQ 知识库）
- `POST /v1/cs/chat` API + 会话持久化

## v0.21.0 — 2026-07-09

### Added
- 定时任务系统：后台线程轮询，支持 cron 表达式和人类语法（`every 30m`）
- `GET /v1/scheduler/tasks` + CRUD 端点
- 前端定时任务管理 Tab（创建/启用/禁用/删除）
- 插件加载器：启动时扫描 `~/.agent-harness/plugins/*.py`
- `GET /v1/plugins/loaded` API
- sync-version.py 版本同步脚本

### Fixed
- pyproject.toml 与 `__init__.py` 版本号不一致问题

## v0.20.0 — 2026-07-09

### Added
- AGENTS.md 全面重写（已实现列表→24项、架构图同步、版本哲学更新）
- DuckDuckGo 搜索多策略降级解析（3层 selector 链）
- 搜索 URL 去重（`seen_urls`）

### Changed
- 版本节奏改为慢速（参考群星 DLC 式）
- 移除 V1.0/V2.0/V3.0 空想路线图

## v0.19.1 — 2026-07-09

### Added
- 技能市场：`GET /v1/skills/marketplace`（SkillHub 集成）
- `POST /v1/skills/marketplace/install` 远程安装
- `POST /v1/skills/:name/toggle` 启用/禁用
- MCP 工具开关：`tools/tool_config.py` + `POST /v1/tools/:name/toggle`
- 前端技能 Tab 改造（已安装列表+市场搜索+安装按钮）
- 前端 MCP Tab 改造（已启用/已禁用分组+启用/禁用按钮）

## v0.19.0 — 2026-07-09

### Added
- 骨架屏（shimmer 动画，所有 Tab 加载时显示）
- 统一错误处理（错误卡片+中文描述+重试按钮）
- Toast 队列（连续通知排队显示）
- 侧边栏状态栏（健康检查 🟢/🔴）
- Tab 切换动画（opacity 渐隐渐现）

## v0.18.0 — 2026-07-09

### Added
- BDuckDuckGo 多策略降级解析
- BM25 关键词搜索（中文双字分词+英文词+TF-IDF 加权）
- 嵌入批量化（1 HTTP 请求代替 N 次）
- 嵌入重试（3次指数退避）
- 嵌入缓存（MD5 指纹）
- `threading.RLock` 文件操作锁
- 原子写入（tmp→replace）

### Changed
- `index()` 返回 dict（含 `chunks_count`/`embedding_status`/`fallback`）
- 前端知识库显示嵌入状态（🟢/🔴 点 + toast 中文提示）

## v0.17.0 — 2026-07-09

### Added
- 专业灰蓝报告模板（TOC 目录+字数统计+阅读时间+标签行）
- 前端报告下载按钮（⬇ 下载 .html）
- Worker 输出截断 500→2000 字符
- 中文错误提示（超时/API Key/频率限制）
- 搜索提示词优化（4角度：品类/评测/对比/行业）

### Changed
- Finalizer 提示词重构（先结论→分小节→引用数据→800-1500字）

## v0.16.0 — 2026-07-09

### Added
- SQLite 线程安全（`threading.local()` + WAL + `busy_timeout`）
- Agent 并发限流（`Semaphore(5)`，流式+非流式全覆盖）
- `HARNESS_WORKERS` 多进程支持
- `owner_id` 数据隔离（会话/报告/正式报告）
- 原子文件写入（tmp→replace）
- `HARNESS_MAX_CONCURRENT_AGENTS` 环境变量

## v0.15.0 — 2026-07-09

### Added
- JWT 用户认证（HMAC-SHA256，零外部依赖）
- RBAC 角色权限（admin/user）
- 登录页面 + 首次启动管理员创建
- 用户管理面板（创建/删除/改角色/重置密码）
- `POST /v1/auth/login` / `POST /v1/auth/logout` / `GET /v1/auth/me`
- Admin CRUD：`GET/POST /v1/admin/users` + 角色/密码/删除
- 前端 fetch 代理（自动附加 JWT Bearer 或 X-API-Key）

## v0.14.0 — 2026-07-09

### Added
- API 双模认证（JWT + X-API-Key）
- 首次启动自动生成 256-bit API token
- CORS 收紧（`["*"]` → 具体 origin）
- `code_execute` 沙箱（安全 builtins 白名单+模块白名单+30s 超时）
- 工具权限三级（read-only / reversible / irreversible）
- CSP 响应头 + 安全头模板
- `/docs` 默认关闭（`HARNESS_ENABLE_DOCS=1` 重开）
- 路径遍历防护（`_safe_path_param()`）
- 审计日志（`~/.agent-harness/audit/`）

## v0.13.0 — 2026-07-07

### Added
- Worker 自动重试（3次指数退避 2s/4s/8s）
- 搜索兜底信号统一（`[搜索失败]` 格式）
- 报告数据来源引用（超链接上标+锚点列表）
- `report_formatter.py` 重写（自动提取 URL + 来源列表）
- 快捷键：Enter 发送 / Ctrl+Enter 换行 / Esc 取消
- 窗口位置持久化（`window_config.json`）
- 版本号显示（侧边栏底部）
