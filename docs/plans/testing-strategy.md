# LingShu 分层测试与耗时优化实施规范

状态：**已增加本地选择器和 hook 接线；真实耗时基线与 CI 实测待验证**。本文件是企业研究试点的一项工程要求，不代表企业试点已经验收。

## 1. 当前证据与约束

- 2026-10-28 既有收尾记录：Windows 离线 pytest 1207 passed / 3 skipped / 8 deselected，约 143.02 秒；历史样本，不代表最新分支或当前机器速度。
- 原 `scripts/git-hooks/pre-commit` 对几乎所有代码提交执行 `pytest -x -q tests/`，并用 `tail -10` 截断诊断；这是日常重复耗时的已确认来源。
- CI `quality` 执行全量离线 pytest、全局覆盖率 >=35%、pipeline+graph >=70%、SAST、依赖审计；`integration` 实际启动 HTTP 服务；`source-security` 构建并扫描镜像。**不得减少这些发布门禁**。
- 单 worker、SQLite、进程内状态、RAG scope/contextvars、权限门禁均须保留；禁止默认并行 pytest 或共享真实用户数据目录。
- `pytest.ini` 默认不运行 `integration`；在线集成另执行。默认离线全绿不代表真实 LLM 质量或生产负载通过。

## 2. L1–L5 责任与命令

| 等级 | 触发 | 执行 | 作用 |
| --- | --- | --- | --- |
| L1 快速反馈 | 每次本地提交 | `python scripts/test_selection.py --execute`（hook 自动调用） | 仅对可证明低风险的路径选局部测试；不确定则**全量** |
| L2 模块回归 | 每个功能完成 | `python -m pytest -q tests/test_xxx.py` + 对应权限/契约测试 | 验证模块行为和安全边界 |
| L3 集成 | 阶段结束 | 启动真实服务后 `python -m pytest -m integration -o addopts="-q --tb=short"` | HTTP/SSE/真实服务和数据根隔离 |
| L4 发布门禁 | PR / master / release | `uv run pytest tests/ -q --cov=src/agent_harness --cov-report=term --cov-report=xml --cov-fail-under=35` + CI 全套 | 全量、类型、安全、镜像、依赖、打包与上手 |
| L5 业务验收 | 试点验收 | 实际模型+真实任务集+人工核验+Locust 场景负载 | 质量、速度、成本、权限、恢复与业务价值 |

时间目标（L1 15–30 秒）**只是优化目标不是已测成绩**，超过目标应记录主要耗时测试，而不是省略测试。

## 3. 风险分层与 fail-closed

`scripts/test_selection.py` 检查 **staged 文件**；默认只展示，带 `--execute` 才运行 pytest。

- 文档、图示、LICENSE、mkdocs 说明性修改可不启动 pytest，但仍保留版本一致性检查。
- 精确白名单的低风险模块（如 SLO、失败归因）和纯前端静态资源执行明确列出的相关测试；单独改 `tests/test_x.py` 时至少执行该测试。
- 配置、依赖、内核/编排/安全/认证/LLM/RAG、测试共用 fixture、hook、CI、未知文件、目标测试缺失等一律回退完整离线套件。
- 删除已登记测试文件、修改集成测试或者无法判断依赖传播时回退完整离线套件；**线上集成仍由 CI 单独执行**。
- 无可用 Python、测试无法运行、选择器报错、pytest 非零退出码，全部导致 hook 非零退出，不允许假装成功。
- 本地 L1 仅是反馈加速，不是准入批准；合并前必须看 L4 全量 CI 结果。

如维护人员要扩展白名单，必须在 PR 中提供受影响模块的依赖分析、对应测试与失败路径的验证；安全敏感路径永不加入局部白名单。不能按模糊文件名猜测影响范围。

## 4. 建议的下一轮性能剖析（尚未执行）

1. 在同一环境和同一提交执行 `python -m pytest tests/ -q --durations=30`，保存耗时前 30 和整体耗时；记录硬件、Python 版本、时间和测试总数。
2. 将结果按 fixtures 初始化、sleep/网络重试、全局状态清理、数据库、重复真实服务启动分类。
3. 优先修复慢测试自身的确定性和隔离问题；不允许缩短业务超时导致漏测，不允许强行 `pytest-xdist` 并行。
4. 单独记录优化前后全量测试秒数、L1 秒数、测试数、失败数、覆盖率；没有实测不填 0、不声称加速百分比。
5. 检查 Windows/Linux hook Python 解释器一致性；在 PR 的 CI 中确认新 selector 可用、输出有完整失败日志。

## 5. 冻结门槛

- selector 的低风险/高风险/未知文件/不存在文件/文档变更/执行失败等路径均有测试；
- 至少一次真实全量回归与线上集成通过，CI 质量/安全/打包门禁通过；
- 覆盖率未回退，关键权限测试未删除，测试耗时对比和执行环境留档；
- 无 silent failure；失败可定位、可复现、可回滚。

## 6. 回滚

如果增量选择错误漏测：立即在 hook 中暂时强制 `--full`，保留 L4 CI，修复选择逻辑并补回归测试。切勿通过降低 CI 阈值“修好”问题。
