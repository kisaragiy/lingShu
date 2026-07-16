#!/usr/bin/env python3
"""Mock OpenAI-compatible API — 返回固定回复，用于测试小程序 UI 流程。"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json

PORT = 18888

class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        messages = body.get("messages", [])
        user_msg = messages[-1]["content"] if messages else ""

        reply = (
            f"你好！我是灵枢（测试模式）。\n\n"
            f"你刚才说的是：「{user_msg[:50]}」\n\n"
            f"当前是 mock 回复。连上真实 LLM 后，我会帮你搜索→分析→出报告。"
        )

        resp = {
            "id": "mock-chat-123",
            "object": "chat.completion",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": reply},
                "finish_reason": "stop"
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 30, "total_tokens": 40}
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(resp, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        if self.path == "/v1/models":
            resp = {"object": "list", "data": [{"id": "default", "object": "model"}]}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode())
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

    def log_message(self, fmt, *args):
        print(f"[mock-llm] {args[0]} {args[1]}")

if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Mock LLM API 运行中: http://127.0.0.1:{PORT}")
    print(f"灵枢的 HARNESS_LLAMA_API 设为: http://127.0.0.1:{PORT}/v1/chat/completions")
    server.serve_forever()
