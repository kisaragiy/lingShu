# 灵枢 (LingShu) — AI 应用开发实战项目

> **灵枢者，智之枢也。** 以 Supervisor 为枢，Worker 为四肢，调度万端。
> 一个完整的 AI 应用落地实践——从多 Agent 编排到产品化交付。

---

## 一、项目定位

**一句话：灵枢是一个完整的 AI 应用开发实践。** 覆盖了从 Agent 架构设计、LLM 集成、RAG、认证安全到前端交付的全链路。

这不是一个 demo，是一个能打包成 exe 给用户用的产品。
这不是一个 API 调用的演示，是一个自己设计架构、自己写前后端、自己部署的完整项目。

面试时讲清楚这几点：

- **架构设计能力** — 设计 Supervisor-Worker 多 Agent 编排架构，解耦核心与场景
- **LLM 工程化** — 多模型路由（本地/云端降级）、提示词工程、RAG、流式 SSE
- **全栈交付** — FastAPI + pywebview + HTML/CSS/JS，打包 exe，Docker 部署
- **安全工程** — JWT 双 token、RBAC、CSP、速率限制、审计日志
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

## 四、架构总览（v0.19.1）

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
--- 以上为当前已发布 (v0.27.0) ---

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
