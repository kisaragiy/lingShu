# 灵枢 — 路线图

> 根据 2026-07-11 复盘结论更新。

## 当前状态（v0.52.0）

```
产线状态:
  核心 Agent 管线（搜索→分析→报告）: 14 个版本未碰
  CS Demo: 6 个版本迭代，最完善的部分
  前端/测试/Docker/防御: 全部就绪
  README: 缺截图
```

## 定位

**灵枢 = 超级智能体平台**（Supervisor-Worker + LangGraph）。

垂直场景（CS Demo）是它的特化应用，不是独立产品。当前 CS Demo 和核心管线是「两个独立项目共用 repo」的关系，需要改为「平台→应用」。

## 执行顺序

### Phase 0（今天）: 修复核心管线

核心 Agent 管线 LLM 不可达时全线静默失败。加降级：
- `call_llama()` 返回空 → log + 哨兵值
- Supervisor/Worker/API 输出 → 模板降级回复（同 CS Demo 模式）

### Phase 1（3-4 小时）: 代码分层

不拆包，只做目录整理 + import 隔离：

```
当前:                             改造后:
agent_harness/                    agent_harness/
├── agents/*.py                   ├── core/           ← 共享
├── tools/*.py                    │   ├── agents/
├── pipeline/*.py                 │   ├── tools/
├── auth_*.py                     │   ├── pipeline/
├── api_fastapi.py                │   └── auth/
├── static/                       ├── apps/
│   ├── index.html                │   ├── research/   ← 调研助手
│   └── cs-demo.html              │   │   ├── api.py
└── ...                           │   │   ├── static/
                                  │   │   └── run.py
                                  │   └── cs_demo/    ← 客服
                                  │       ├── api.py
                                  │       ├── static/
                                  │       └── run.py
                                  ├── main.py         ← 入口路由
                                  └── ...
```

核心原则：
- `core/` 内部模块保持相对 import 不变
- 只有 `apps/` 的代码改成 `from agent_harness.core import ...`
- `core/` 不加任何业务逻辑（不放 report_formatter、不放 customer_service）

### Phase 2（20 分钟）: README 截图

启动主应用截 3 张图：
- CS Demo 对话界面
- 主应用 Dashboard / 对话
- 报告页面

### Phase 3（往后）: 投简历

剩下的（拆独立 repo、Docker 镜像源适配）等面试反馈再决定。

## 不做的事

- 拆三个独立 repo（当前阶段单包迭代更快）
- 继续给 CS Demo 加功能（核心管线优先）
