"""端到端验证：客户端中途断开 → 499（而不是 500）。

用裸 socket 复现真实场景：声明 Content-Length: 5000 但只发一部分就关闭 ——
服务端的 `await request.json()` 会抛 ClientDisconnect。

修复前：冒成未捕获异常 → 500（污染错误率，实测 50 并发压测下 3 个 500 全源于此）
修复后：异常处理器返回 499 → `status=~"5.."` 不再匹配它

用法（需先起服务）：
    HARNESS_RATE_LIMIT=100000 ./.venv/Scripts/python scripts/run_server_8788.py &
    ./.venv/Scripts/python scripts/verify_client_disconnect_499.py
"""
import pathlib
import socket
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8788"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from agent_harness.core.auth.api_security import load_or_generate_token  # noqa: E402

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def metrics() -> str:
    return opener.open(BASE + "/metrics", timeout=10).read().decode("utf-8")


def sample(text: str, status: str) -> float:
    total = 0.0
    for ln in text.splitlines():
        if ln.startswith("harness_http_requests_total{") and f'status="{status}"' in ln:
            total += float(ln.rsplit(" ", 1)[1])
    return total


before = metrics()
b499, b500 = sample(before, "499"), sample(before, "500")

token = load_or_generate_token()
# 声明 5000 字节的 body，只发 10 字节就断开 → 服务端读 body 时 ClientDisconnect
s = socket.create_connection(("127.0.0.1", 8788), timeout=5)
s.sendall(
    b"POST /v1/cs/chat HTTP/1.1\r\n"
    b"Host: 127.0.0.1:8788\r\n"
    b"Content-Type: application/json\r\n"
    b"X-API-Key: " + token.encode() + b"\r\n"
    b"Content-Length: 5000\r\n\r\n"
    b'{"message"'
)
time.sleep(0.4)
s.close()
print("已模拟客户端中途断开")

time.sleep(2.0)
after = metrics()
a499, a500 = sample(after, "499"), sample(after, "500")

print(f"\n499 计数: {b499} -> {a499}  (+{a499 - b499})")
print(f"500 计数: {b500} -> {a500}  (+{a500 - b500})")

ok = (a499 > b499) and (a500 == b500)
print(f"\n判定: 断开被记为 499 且未污染 5xx → {'PASS' if ok else 'FAIL'}")

print("\n相关指标行:")
for ln in after.splitlines():
    if ln.startswith("harness_http_requests_total{") and ('"499"' in ln or '"500"' in ln):
        print("  " + ln)

sys.exit(0 if ok else 1)
