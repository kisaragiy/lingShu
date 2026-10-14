# Long-task harness (WP5)

给「横跨多个会话的长任务」一个**机器可读的进度载体**，替代散文进度（AGENTS.md / daily 日志）——散文一压缩就断片。

行业依据：Anthropic《Effective harnesses for long-running agents》。
四大失败模式 → 本 harness 的对策：

| 失败模式 | 对策 |
|---|---|
| 过早宣告完成 | `pass` 必须带 `--evidence`，否则**拒绝**；`passes=true ⇒ evidence 非空` 是硬不变量 |
| 留给下一会话烂摊子 | 会话末把 PROGRESS.json 落盘 + git 提交（clean state） |
| 没测就标完成 | 同上：无证据不可 pass（连"证据太短/占位符"都拒） |
| 每次重新摸索怎么跑 | `init.sh` + `smoke_cmd` 固化「怎么启动 / 怎么冒烟」 |

## 文件

- `PROGRESS.schema.json` — PROGRESS.json 的规格（含 `x-invariants`）
- `PROGRESS.example.json` — 示例
- `harness.py` — CLI（契约合规：`0=OK · 2=ALERT · 1=ERROR`）
- `init.sh` — 启动 + 冒烟模板（Windows/MSYS 路径已处理）
- 本仓 pilot：灵枢根 `PROGRESS.json`（由 TASK-PROGRESS.md 种子的 5 项待办）

## PROGRESS.json 长什么样

```json
{
  "schema_version": 1,
  "project": "harness-demo",
  "smoke_cmd": "python wordcount.py README.md",
  "features": [
    {"id": "f1-count-words", "desc": "wordcount.py <file> 打印词数，退出码 0",
     "passes": true, "evidence": "python wordcount.py README.md -> 41 (exit 0)",
     "verified_at": "2026-10-08T09:29:34+08:00", "notes": ""},
    {"id": "f2-empty-file", "desc": "空文件输入打印 0", "passes": false, "evidence": null}
  ]
}
```

不变量：`id` 唯一；`passes=true ⇒ evidence` 非空；**「下一个该做的」= features 中第一个 `passes=false`（数组顺序 = 优先级）**。

## 用法

```bash
PY=.venv/Scripts/python.exe
$PY harness/harness.py --dir <项目目录> init --project X --smoke "python app.py --selftest" \
      --feature "f1:做 A" --feature "f2:做 B"
$PY harness/harness.py --dir <项目目录> status [--json]   # 未完成列表 + 下一个
$PY harness/harness.py --dir <项目目录> next               # 只打印下一个
$PY harness/harness.py --dir <项目目录> pass f1 --evidence "命令或输出摘要"
$PY harness/harness.py --dir <项目目录> unpass f1          # 重开
$PY harness/harness.py --dir <项目目录> add f3 --desc "..."
$PY harness/harness.py --dir <项目目录> check [--json]     # watchdog：不变量坏了才出声
```

`--dir` 默认 `$HARNESS_DIR` 或 cwd。退出码：`status/next/pass/add` 成功 0；缺文件 / 证据不合格 / id 不存在 = 2（ALERT）；未捕获异常 = 1（ERROR）。`check` 在不变量成立时**完全静默**（适合 cron/watchdog）。

## 新会话 3 步接手

1. `cd <项目目录>` （`pwd` 确认）
2. `python tools/harness/harness.py --dir . status`（或 `bash init.sh`，它会冒烟+报进度）
3. 认领 `→ next` 那个 feature；做完 `pass <id> --evidence "..."`，然后 git 提交

## 在真实项目落地（2–3 步，不改那些项目本身）

**A. AIGC 管线项目**
1. 在项目根跑 `init --smoke "<一条能证明管线通的命令>"`，feature 写成可验收行为（如"ControlNet 出图 1 张尺寸 1024 且非全黑"）。
2. 每完成一个就 `pass <id> --evidence "<实际输出>"`；会话末 git 提交 PROGRESS.json。

**B. 灵枢 / 其他长线工程**
1. `PROGRESS.json` 只放"可验证 feature"（别放步骤粒度），10 条以内最好——它是**接班简历**不是待办清单。
2. 把 `harness.py --dir . check --json` 挂进现有巡检（watchdog 语义，干净静默）；`status` 由人/agent 主动跑。

**C. 通用**
- 新项目从 `PROGRESS.example.json` 复制，或 `harness.py init --feature ...`。
- `harness.py` 是全局单副本（放 `HERMES_HOME/harness/`），每个项目只多一个 `PROGRESS.json`。

## 设计上不成立 / 需要注意的地方（诚实声明）

1. **"机器可读"挡不住"证据造假"**。`--evidence` 只是**要求你留下痕迹**，工具无法核实它是不是真跑过。它能防"忘了验证就标完成"，防不了"故意编一条"。要真防，得让 evidence 是可复现命令 + 有人/CI 复核。
2. **无法强制会话末 clean state**。harness 自己不 git 提交、不重启会话；"提交 PROGRESS.json"仍需约定（或加进现有 stop hook）。
3. **不解决"上下文压缩本身"**。它保证压缩后能**重建进度**，但压缩掉的知识（为什么这么设计）不在 PROGRESS.json 里；`notes` 字段易被滥用成散文进度——要克制。
4. **单文件易冲突**。若多会话/多 agent 并行改同一个 PROGRESS.json，会互相覆盖（无锁）。当前是"一次一个 feature、串行会话"的假设。
5. Windows 上给原生 python 传路径必须 `cygpath -w`（见 `init.sh` 的 `winpath`），MSYS 的 `/c/...` 会让 python 解析成 `C:\c\...`。
