#!/usr/bin/env python3
"""启动灵枢 + mock LLM，避开代理问题。"""
import os, subprocess, sys, time

# 清代理
for k in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
    os.environ.pop(k, None)

# 启动 mock LLM
mock_port = 18888
mock_proc = subprocess.Popen(
    [sys.executable, "-c", f"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(b'{{"status":"ok"}}')
    def do_POST(self):
        length = int(self.headers.get('Content-Length',0))
        body = json.loads(self.rfile.read(length)) if length else {{}}
        msg = body.get('messages',[{{}}])[-1].get('content','')
        reply = f'你好！你刚才说：「{{msg[:50]]」这是 mock 回复。'
        resp = {{"choices":[{{"message":{{"role":"assistant","content":reply}}}}]}}
        self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers()
        self.wfile.write(json.dumps(resp,ensure_ascii=False).encode())
    def log_message(self,*a): pass
HTTPServer(('127.0.0.1',{mock_port}),H).serve_forever()
    """],
)
print(f"Mock LLM started on :{mock_port}")
time.sleep(1)

# 启动灵枢（改端口）
os.environ["HARNESS_LLAMA_API"] = f"http://127.0.0.1:{mock_port}/v1/chat/completions"
os.environ["HARNESS_MODEL_LLAMA"] = "mock"
os.environ["HARNESS_DISABLE_AUTH"] = "1"

import agent_harness.apps.research.api
agent_harness.apps.research.api.PORT = 8765

from agent_harness.main import main
main()
