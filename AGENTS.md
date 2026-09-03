# 灵枢 (LingShu) — AI 应用开发实战项目

> **灵枢者，智之枢也。** 以 Supervisor 为枢，Worker 为四肢，调度万端。
> 一个完整的 AI 应用落地实践——从多 Agent 编排到产品化交付。

---

## 一、项目定位

**一句话：灵枢是一个独立的多 Agent AI 应用工程实践——以「防复现知识管道」为真实落点，以「Agent 可靠性工程」为核心深度。**

> ⚠️ **本文件是内部工作准则（AGENTS），不是对外 README。** README 只展示最好面（真实数据/无主观词/不写缺陷踩坑）；本文件可写红线、对账、工作法则。

**【真实落点 = 防复现知识管道】**（主叙事，面试讲透，有真据）
- 解决的真实问题：`~/knowledge/daily/` 高产出零索引、教训全在事后记录无事前拦截、项目自己不被使用=样板房。
- 真实产出：规则库 **32 条**（技术复现 21 + 求职 9 → README 旧文"30 条"需对照修正）· 中文 2-gram 检索层（零外部依赖）· 沉淀机制扫 daily 抽候选 · **28 测试通过**。
- 真实拦截验证：`guardian.py --search "显存不够"` 命中 R-11/12；`guard "给简历写平台"` 拦截 R-22/23 共 4 条。

**【核心深度 = Agent 可靠性工程】**（辅证，相对应届稀缺）
- 熔断（circuit_breaker）· 预算控制（budget）· 重试退避（retry）· 降级链（degradation）· 安全三层（safety: risk/mode/backup）· 审计（audit）。
- Supervisor-Worker 编排 + LangGraph 图，45+ 工具。

**【红线（内部对账，不写进对外）】**
- 对外只展示最好面；**踩坑/教训/重构/缺陷 → 口头讲**（面试是真实证据），不写 README。
- 数字必须对上仓库：README 历史"63 测试"与实测（知识管道 28 + 项目自身 29）不符，**需修正**；"45+ 工具"需核对实际注册数。
- 实测（2026-08-31 `pytest tests/ -q`）：**163 passed / 8 failed / 2 skipped**。8 个失败均为**环境依赖**（非回归）：`test_cs_stream.py` 6 个（需本地起 `:8788` 服务）；`test_llm_cache.py` 2 个（需本地 llama.cpp 在 `:8080`）。README 已按实测 163 修正。
- 别让技术广度稀释"用 AI 解决真实问题"的浓度——主打是"这个 Agent 怎么设计、怎么保证最坏不发生"，不是"造了框架"或"写了多少行前端"。
- **AIGC 集成** — ComfyUI API 对接、LoRA 训练、批量生图管线

---

### 已实现（v0.53+）

| 能力 | 说明 |
|------|------|
| **Supervisor-Worker 编排** | LangGraph 多 Agent 图：分析→分配并行 Worker→验收→汇总 |
| **多引擎搜索** | SearXNG → DuckDuckGo(5层解析去重+UA轮换) → skill 三级降级 |
| **网页抓取** | fetch/web_scrape/agent_browser 三级抓取，Playwright 兜底 |
| **RAG 知识库** | PDF/DOCX/TXT → 向量搜索 / BM25 关键词降级 + 嵌入状态提示 |
| **JWT 认证 + RBAC** | HMAC-SHA256 双 token + admin/user 两角色 + CSP + 审计日志 |
| **45+ 工具** | 搜索/代码/桌面/浏览器/绘画/RAG/股票 6 大类 |
| **核心+场景分层** | `core/` 共享基础设施 + `apps/research` 调研 + `apps/cs_demo` 客服 |
| **测试体系** | 63 单元测试 + CI 集成 |
| **exe 打包** | PyInstaller 单文件夹 exe，开箱即用 |
| **Docker 部署** | 多阶段构建 + docker-compose（LingShu + SearXNG） |
| 微信小程序 | 7 页面移动端（对话/报告/客服/个人中心），4 tab，对接灵枢 API | v0.63 |
| 自动 LLM 配置 | 启动时自动检测 Ollama，pull qwen3:1.7b，无需手动配置 | v0.63 |

---

## 三、项目边界

| 不做 | 原因 |
|------|------|
| **不做云服务** | 本地优先——证明的是你懂架构，不是你会运维 |
| **不做通用聊天机器人** | @ChatGPT 做得更好，灵枢展示的是 Agent 编排能力 |
| **不做全栈 IDE** | 灵枢展示的是 AI 应用集成，不是代码编辑器 |

---

## 四、架构总览（v0.75.0）

```
┌── 用户交互层 ──────────────────────────────────────┐
│                                                      │
│  原生窗口 (pywebview)                                │
│    ├── 💬 对话      ← 主要使用入口                  │
│    ├── 📊 服务状态   ← 骨架屏+错误卡片+重试按钮     │
│    ├── 📚 知识库     ← 上传管理+嵌入状态指示        │
│    ├── 🧠 技能       ← 已安装列表+技能市场           │
│    └── ⚙️ 菜单       ← 设置/报告/MCP/会话/用户      │
│                                                      │
│  侧边栏                                             │
│    ├── 会话列表     ← 搜索过滤+owner_id 隔离        │
│    └── 状态栏       ← 🟢 运行中 / 🔴 API 离线       │
│                                                      │
│  认证层                                             │
│    ├── 首次启动 → 创建管理员                         │
│    ├── 登录页   → JWT Bearer                         │
│    └── API Key  → CLI/Open WebUI 管理级 fallback    │
│                                                      │
└──────────────────────────┬──────────────────────────┘
                           │ HTTP/SSE + JWT/X-API-Key
┌── 服务层 ───────────────▼─────────────────────────┐
│                                                      │
│  FastAPI Server (:8788)  · 双模 Auth Middleware       │
│    ├── /v1/chat/completions  (OpenAI API)              │
│    ├── /v1/auth/*            (Login/Logout/Me)        │
│    ├── /v1/admin/*           (用户 CRUD)              │
│    ├── /v1/skills/*          (管理+市场)              │
│    ├── /v1/tools/*           (列表+开关)              │
│    ├── /v1/knowledge/*       (RAG 索引+查询)          │
│    ├── /v1/setup/*           (配置/诊断/修复)         │
│    ├── /v1/sessions/*        (owner_id 隔离)          │
│    ├── /v1/tasks/*           (可中断+并发限流)        │
│    ├── /v1/reports/*         (生成+列表+下载)         │
│    └── /v1/health            (状态栏轮询)             │
│                                                      │
│  40+ 工具 · 三级权限 · 路径遍历防护 · CSP 头         │
│  Agent Semaphore(5) · SQLite 每线程连接              │
│                                                      │
└──────────────────────────┬──────────────────────────┘
                           │
┌── 编排层 ───────────────▼─────────────────────────┐
│                                                      │
│  LangGraph 多 Agent 图                                │
│    Supervisor (分析→分配→验收→汇总)                   │
│      ├── Search Worker   (搜索/抓取/缓存)            │
│      ├── Analyze Worker  (代码/分析/总结)            │
│      └── Execute Worker  (桌面/浏览器/绘画)          │
│                                                      │
│  三重熔断器 · 指数退避重试 · Worker 输出 2000 字     │
│  Finalizer 结构化报告 (800-1500 字 + 来源标注)       │
│                                                      │
└──────────────────────────┬──────────────────────────┘
                           │
┌── 基础设施 ─────────────▼─────────────────────────┐
│                                                      │
│  推理后端 (任选其一):                                │
│    model_proxy (:8081) → DeepSeek Flash (云端)       │
│    llama.cpp (:8080)     → Qwen3.6-35B (本地)        │
│    Ollama (:11434)       → 多模型群 (WSL)            │
│                                                      │
│  辅助服务:                                           │
│    SearXNG (:4000)   · ComfyUI (:8188)               │
│    SkillHub          · Open WebUI (:3000)             │
│                                                      │
│  存储:                                               │
│    SQLite auth.db    · 会话 JSON (RLock+原子写入)    │
│    RAG NPY+JSON      · 报告 MD+HTML+JSON 索引        │
│    tool_config.json  · api_token.txt / jwt_secret.txt│
│                                                      │
└──────────────────────────────────────────────────────┘
```

---

## 五、技术栈

| 层 | 技术 |
|------|------|
| 窗口 | pywebview (WebView2) |
| 后端 | Python 3.11+ / FastAPI / Uvicorn |
| 编排 | LangGraph (Supervisor-Worker) |
| 认证 | HMAC-SHA256 JWT + pbkdf2_hmac 密码哈希 + SQLite |
| 向量 | Ollama nomic-embed-text → NPY 文件 → BM25 降级 |
| 搜索 | SearXNG → DuckDuckGo HTML → skill 三级降级 |
| 存储 | SQLite / JSON 文件 / NPY |
| 技能 | SkillHub CLI (+ 目录 moveto _disabled/) |
| 打包 | PyInstaller (单文件夹 exe, ~130MB) |
| 推理 | llama.cpp / Ollama / DeepSeek Flash API |

---

## 六、版本规划

```
v0.4   多 Agent 编排框架
v0.5   产品化（会话/流式/品牌）
v0.6   质量提升（路由/Worker/进度）
v0.7   知识库 RAG
v0.8   会话持久化
v0.9   前端（Setup Wizard + Dashboard）
v0.10  exe 打包（git tag 缺失，历史遗留）
v0.11  独立窗口（pywebview）
v0.12  一键配置 + 容错
v0.13  搜索链路稳定性
v0.14  安全加固（API认证+CORS+沙箱+权限+CSP+审计）
v0.15  用户登录与权限（JWT+RBAC+用户管理）
v0.16  高并发+数据隔离（SQLite线程池+Agent限流+owner_id）
v0.17  报告质量+搜索质量（专业模板+下载+Worker输出完整+中文错误）
v0.18  RAG 稳定性（批量嵌入+BM25降级+线程安全+原子写入）
v0.19  前端体验打磨 / 技能市场+MCP开关
v0.20  AGENTS.md重写 / 搜索多策略解析
v0.21  定时任务系统 / 插件加载器
v0.22  CHANGELOG / 版本信息 / 设置页关于
v0.23  报告体验闭环（自动保存草稿+生成即打开）
v0.24  报告PDF打印 / 报告搜索
v0.25  报告规范度（结构化输出+置信度+来源元数据）
v0.26  新手上路（欢迎页+状态增强）
v0.27  数据导出（会话+报告+完整备份ZIP）
v0.28  **安全护栏（WorkBuddy P0 移植）**:
         ├── 会话安全模式 default(沙箱优先)/full(全权全审计)
         ├── 危险操作确认矩阵(按参数分类:敏感路径/批量删/脚本/外部发送/网络)
         ├── 写前自动备份(覆盖写存.bak副本)
         ├── 删除保护(文件走回收站 ~/.agent-harness/trash/)
         └── API: GET/POST /v1/safety/mode, POST /v1/safety/confirm
v0.75.1 三重熔断接线硬化 — LLM token 总账(三调用点)喂熔断器 + route 熔断短路 finalize(运行中能拦住) + retry 从孤儿变实装 + graph.py 补熔断器初始化 + 9 测试证明真触发
v0.75.2 安全护栏 fail-closed — registry 两处 ImportError 不再静默裸奔：权限模块坏→抛错拒绝；safety 坏→不可逆拒+CRITICAL 日志；+3 测试(共30)
--- 以上为当前已发布 (v0.75.2) ---

版本节奏（慢，参考群星 DLC 式）:
  v0.19.1  → 小修补
  v0.20.0  → 下一个功能版本
  v0.21.0  → ...
  V1.0     → 不设预期日期，功能够了再谈

短期方向（不设具体版本号）:
  - 搜索全链路可靠性（多 selector 解析+结果去重+预热缓存）
  - 定时任务（已实现的 agent_cron 代码，需重新接入）
  - 第三方插件系统（已实现的 plugin_loader 代码，需重新接入）
  - 前端拆模块（1500 行 index.html 逐步分离）
  - 知识管道（docs/knowledge-pipeline/：21 条防复现规则 v1+v2 + 检索层 search_rules + 沉淀机制 extract_candidates，A 路线真实场景，独立资产，22 测试）
```

---

## 七、面试核心叙事

> **灵枢是一个完整的 AI 应用开发实践，不是一个 ChatGPT wrapper。**

和面试官讲这三层：

```
第一层（15 秒）：
  "我搭了一个多 Agent 系统——Supervisor 分配任务，Worker 并行执行，
   做完出报告。不是调 API 的 demo，是自己写架构、写前端、打包 exe 的产品。"

第二层（60 秒）：
  "核心架构是 Supervisor-Worker 模式，用 LangGraph 定义工作流图。
   Worker 并行跑搜索/分析/执行，Supervisor 验收结果。做了三重熔断防失控。
   前端 pywebview，后端 FastAPI，JWT 认证，RAG 知识库，63 个测试。"  

第三层（3 分钟）：
  "迭代了 50+ 版本，重构过一次架构（从单体到 core+apps 分层）。
   踩过的坑包括：Worker import 路径问题、API Key 泄露修复、方向漂移回到聚焦。
   现在正在做质量闭环——出图自动质检、不合格重试。"
```

## 八、和市场岗位的对应关系

| 市场要求 | 灵枢对应的能力 |
|---------|--------------|
| **Python + FastAPI** | 整个后端是 FastAPI |
| **LLM/大模型应用** | 多模型路由、RAG、提示词工程 |
| **Agent 开发** | Supervisor-Worker 编排、45+ 工具 |
| **AIGC** | ComfyUI 对接、LoRA 训练管线 |
| **全栈能力** | pywebview 前端 + 后端 + 部署 |
| **安全意识** | JWT + RBAC + CSP + 审计日志 |
| **工程规范** | 63 测试 + CI + 版本管理 + AGENTS.md |
