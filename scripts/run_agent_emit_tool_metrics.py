"""让 Agent 路径真跑一次，产生真实的工具调用指标（含失败）。

为什么不需要 LLM：
  - `workers.py:266-277` 有 Auto-route：任务含「搜索/查/找」→ 自动 call_tool("search")；
    数学任务 → 自动 call_tool("code_execute")。不经 LLM 选工具。
  - LLAMA_API 非空（指向不可达的 :18888）→ `/v1/chat/completions` 走**真 Agent 路径**
    而非 demo 回退分支；LLM 不可达时 Supervisor 走关键词 `_fallback_plan`，仍会派 Worker。
  - Worker 调完工具失败后还会 fallback 到 think（再一次 call_tool）——失败数据更丰富。

跑完直接读回 /metrics，验证 harness_tool_calls_total 真的有了数据。
"""
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8788"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from agent_harness.core.auth.api_security import load_or_generate_token  # noqa: E402

TOKEN = load_or_generate_token()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post(path: str, body: dict, timeout: int = 240):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-API-Key": TOKEN},
        method="POST",
    )
    try:
        r = opener.open(req, timeout=timeout)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:400]
    except Exception as e:
        return f"ERR:{type(e).__name__}", str(e)[:200]


def metrics() -> str:
    return opener.open(BASE + "/metrics", timeout=10).read().decode("utf-8")


def tool_rows(text: str) -> list[str]:
    return [ln for ln in text.splitlines()
            if ln.startswith("harness_tool_calls_total") and "_created" not in ln]


TASKS = [
    ("执行类（→ execute worker，可能打到 file_read）", "执行一下，读取文件 C:/nonexistent/missing-file.txt 的内容"),
    ("代码执行类（→ code_execute，不可逆工具需确认）", "运行 python 代码：print(sum(range(10)))"),
    ("搜索类（重复，验证计数累加）", "查找一下 langgraph 多智能体的资料"),
]

before = tool_rows(metrics())
print(f"跑之前 harness_tool_calls_total 行数: {len(before)}\n")

for i, (label, msg) in enumerate(TASKS, 1):
    print(f"[{i}/{len(TASKS)}] {label}")
    t0 = time.time()
    status, body = post("/v1/chat/completions",
                        {"model": "any", "messages": [{"role": "user", "content": msg}],
                         "stream": False})
    try:
        reply = json.loads(body)["choices"][0]["message"]["content"]
    except Exception:
        reply = body
    print(f"    -> HTTP {status} | {time.time()-t0:.1f}s")
    print(f"       Worker 实际输出: {reply[:200]!r}")
    # Bug A 回归信号：工具明明成功了，输出却是"不可用"模板 = 数据被丢弃
    if "不可用" in reply and "搜索结果" in reply:
        print("       ⚠️ 仍出现降级模板覆盖（Bug A 未修好）")

after = tool_rows(metrics())
print(f"\n跑之后 harness_tool_calls_total 行数: {len(after)}")
print("\n=== 工具调用指标（真实数据）===")
for ln in sorted(after):
    print("  " + ln)

print("\n=== 会话级汇总 ===")
total = err = 0.0
for ln in after:
    v = float(ln.rsplit(" ", 1)[1])
    total += v
    if 'status="success"' not in ln:
        err += v
print(f"  总调用 {total:.0f} 次，非成功 {err:.0f} 次")

out = pathlib.Path(r"C:\Users\zwq\AppData\Local\Temp\lingshu-p0p1-backup\tool-metrics-real.txt")
out.write_text("\n".join(after), encoding="utf-8")
print(f"\n已存快照: {out}")
sys.exit(0 if after else 1)
