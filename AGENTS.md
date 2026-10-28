# 灵枢 (LingShu) — 开发者约定

> **灵枢者，智之枢也。** 以 Supervisor 为枢，Worker 为四肢，调度万端。
> 本文件面向开发者与贡献者：说明项目结构、本地开发、测试与架构约束。产品介绍见 [README](README.md)。

---

## 一、项目结构

```
src/agent_harness/
├── core/                 # 与场景无关的基础设施
│   ├── agents/           # Supervisor 与 Worker 实现
│   ├── graph/            # LangGraph 编排：单 Agent 图 + 多 Agent 图
│   ├── pipeline/         # LLM 调用、重试、熔断、预算、配置、取消
│   ├── tools/            # 工具注册表（搜索/抓取/代码/RAG/桌面/MCP…）
│   ├── safety/           # 风险判定、写入准入、备份、claim 校验
│   ├── skills/           # 技能加载与注入
│   └── auth/             # JWT + RBAC + SQLite 会话
├── apps/                 # 应用层，每个子目录是独立可裁的应用
│   ├── research/         # 调研助手：API + 前端 + 报告管线 + eval
│   ├── knowledge_qa/     # 知识库问答
│   ├── cs_demo/          # 客服演示
│   └── template/         # 新应用脚手架
├── static/admin/         # 管理后台静态资源
├── main.py               # FastAPI 入口（默认 :8788）
├── api_fastapi.py        # OpenAI 兼容 API 入口
├── auto_setup.py         # 首次运行自检与配置生成
└── plugin_loader.py      # 插件动态加载
tests/                    # pytest（单元 + 集成 + 发版门禁）
docs/                     # 架构、配置、API、快速上手
tools/                    # 打包、构建、运维脚本（仓库根）
```

## 二、本地开发

```bash
python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -e ".[dev]"
agent-harness serve            # 启动服务，浏览器打开 http://127.0.0.1:8788
```

常用环境变量（完整列表见 `docs/configuration.md`）：

| 变量 | 作用 | 默认 |
|---|---|---|
| `HARNESS_API_PORT` | 服务端口 | 8788 |
| `HARNESS_API_HOST` | 绑定地址 | 127.0.0.1 |
| `HARNESS_WORKERS` | uvicorn worker 数 | 1（**必须保持 1**，见下） |
| `HARNESS_CLOUD_KEY` / `HARNESS_CLOUD_URL` | 云端推理后端 | 未设置 |
| `HARNESS_OLLAMA_API` | 本地嵌入/推理地址 | 127.0.0.1:11434 |

## 三、测试

```bash
pytest -q                      # 全量
pytest tests/ -q -k "not integration"
```

基线（2026-10-23 实测，8 个 integration 默认 deselect）：具体数字只写在 `README.md` 徽章一处，这里不重复——两处数字必然漂移。改动后跑全量：通过数**只增不减**，出现任何 failed/error 即视为回归；新增用例后同步更新 README 徽章。

发版门禁测试（不要绕过）：

- `tests/test_no_private_hosts.py` — 禁止硬编码私网地址与个人绝对路径
- `tests/test_packaging_and_config_gates.py` — 打包形态与运行时配置读取

### git hooks（换机器必装）

`.git/hooks/` 不进版本库，本仓库自带一份在 `scripts/git-hooks/`，一条命令装回：

```bash
bash scripts/install-git-hooks.sh     # 设 core.hooksPath → scripts/git-hooks
```

pre-commit 做两件事：有代码变更时跑 `pytest -x -q tests/`；校验 pyproject、包
`__version__`、AGENTS.md 三处版本号一致。紧急情况用 `git commit --no-verify`
跳过，但**不要**改 hook 去放宽门禁。

## 四、架构约束（ADR）

### ADR-001 不在 `core/` 里引入服务端 / 多租户 / 配额组件
**决策**：`core/` 只承载单机可复用的基础设施；配额、计费、埋点这类运营能力不进代码库。允许的补丁类型仅限配置外置、错误恢复与诊断、文档。
**理由**：打包为桌面应用后额度必然可被提取，客户端限额等于没有限额；服务端形态另有分支。
**后果**：需要多租户隔离的场景不在当前支持范围。

### ADR-002 环境相关地址一律走配置
**决策**：私网地址、绝对路径不得硬编码，默认值只能是 `127.0.0.1`；由测试守门。
**后果**：非默认环境（如 WSL 内 Ollama）需显式设置 `HARNESS_OLLAMA_API`。

### ADR-003 运行时配置必须经 `get_llm_config()`
**决策**：① 打包一律 `console=False`，失败路径走 `tkinter` 弹窗；② 配置读取走运行时函数，import 常量只作回退；③ 自动配置必须落 `config.json`（环境变量对已 import 的模块是快照）。
**理由**：设置页填入的地址必须立即生效，且在打包形态下可见。

## 五、已知设计边界

- **单 worker**：`session_store` / `metrics` / `cancel` / LLM 缓存均为进程内状态，`HARNESS_WORKERS>1` 会导致状态分裂与指标断裂。多进程支持需要共享存储，当前未实现。
- **认证模式**：默认开启 JWT；`HARNESS_DISABLE_AUTH=1` 仅用于本地单人演示，**不得暴露到公网**。API Key 持有的角色由 `HARNESS_API_KEY_ROLE`（`admin` / `user`）决定，默认 `admin`；**非法值 fail-closed 降级为 `user`** —— 配置失误不该变成提权。
- **知识库隔离（RAG scope）**：collection 可见性在 `rag_store` **数据层**用 `contextvars` 兜底（`set_scope` / `reset_scope` / `current_scope`），全部读写只经 `_coll_dir()` 一处校验，`list_collections()` 同处过滤。越权抛 `PermissionError`（不返回空结果 —— 空结果会被误读成「库是空的」）。空 scope = 全见（admin / CLI / demo）。**新增调用方若处在需要隔离的路径上，必须在入口 `set_scope` 并 `finally reset`**；跨线程需 `contextvars.copy_context()`（`ThreadPoolExecutor` 不传播）。
- **demo / 共享实例的写类工具**：`HARNESS_DISABLE_AUTH=1` 时 reversible / irreversible 工具**一律拒绝**，只有 `HARNESS_DEMO_ALLOW_WRITE_TOOLS`（逗号分隔工具名）点名的才放开。不依赖 `auto_confirm` —— 它默认 `True`，靠它兜底等于默认全开，而 reversible 里是写盘工具（`file_write` / `rag_index` / `skill_generate`）、irreversible 里是 `code_execute`。
- **数据落盘**：会话为 append-only JSONL 事件源，报告为磁盘 HTML + JSON 索引；未内置备份策略，重要数据请自行备份 `~/.agent-harness/`。

### 已知未修（边界清单，非缺陷）

| 项 | 现状 | 取舍理由 |
|---|---|---|
| CSP 含 `unsafe-inline` | `main.py` 的安全头允许内联 script/style | 前端大量内联样式与事件，改 nonce/hash 需全量前端改造，收益与改动量不成比例；已用 CORS 白名单与 CSP 其余指令收敛 |
| `static/js/tabs.js` 单文件 ~1300 行 | 所有 tab 渲染集中一处 | 纯可维护性问题、无行为影响；拆分需回归全部交互，优先级低于功能正确性 |
| 无自动备份/恢复流程 | 报告与会话依赖人工备份 `~/.agent-harness/` | 桌面单机形态下数据在用户本机且路径可见；服务端形态需另设计（与 ADR-001 一致） |
| metrics 不跨进程聚合 | `HARNESS_WORKERS>1` 时指标断裂 | 与「必须单 worker」同源，属已知边界而非缺陷；多进程需 Prometheus 多进程模式 + 共享目录 |
| `agents/` `pipeline/` `tools/` 三个空壳目录 | `src/agent_harness/` 下这三个目录只剩 `__pycache__`，代码已迁入 `core/` 同名模块 | 历史遗留，属清洁问题、无行为影响；删除前需确认无延迟 import 引用 |

## 六、技术栈

| 层 | 技术 |
|------|------|
| 窗口 | pywebview (WebView2) |
| 后端 | Python 3.11+ / FastAPI / Uvicorn |
| 编排 | LangGraph（Supervisor-Worker） |
| 认证 | HMAC-SHA256 JWT + pbkdf2_hmac + SQLite |
| 向量 | Ollama nomic-embed-text → NPY；BM25 降级 |
| 搜索 | SearXNG → DuckDuckGo HTML → skill 三级降级 |
| 存储 | SQLite / JSON / NPY |
| 打包 | PyInstaller（单文件夹） |

## 七、版本与变更

当前版本 **v0.75.6**。版本记录见 [CHANGELOG.md](CHANGELOG.md)。版本节奏偏慢（v0.X.Y 小步），每次改动同步更新本文件与 CHANGELOG。

### v0.75.4 收尾修复（2026-10-28）
聊天写入口在执行前原子确认会话归属；管理员写入保留原 owner；异步队列/并发等待不阻塞事件循环。离线回归、真实 HTTP 集成及扫描结果见 docs/closeout-2026-10-28.md。仍保持单 worker、本机优先的范围。

### v0.75.5 演示与核查修复（2026-10-28）
模型鉴权与运行时配置接线修复；报告导出保留Markdown；未解析引用不绑定搜索顺序；数字匹配不等于事实核验，历史预测与混合市场范围显示复核提示。仍为单worker、本机优先；未宣称语义核验已自动完成。
