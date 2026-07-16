#!/usr/bin/env python3
"""BOSS 直聘智能招呼语生成服务 — 调本地 qwen3.5 小模型。

用法:
    python boss_reply_server.py
    # 监听在 http://127.0.0.1:8765

油猴脚本配置:
    Custom API URL: http://127.0.0.1:8765/greet
    Custom API Method: POST
    Custom API Body: {"job_title": "{job_title}", "company": "{company}", "salary": "{salary}"}
"""

import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import URLError

# ── 你的简历和项目背景（改这里） ──
RESUME_CONTEXT = """
个人信息：kisaragiy，本科院校软件工程本科，2025届，求职AI应用开发工程师（广州/深圳）。
技术栈：Python, FastAPI, LangGraph, LLM Agent, RAG, ComfyUI, SDXL/Flux LoRA, Docker, Git。

核心项目：
1. 灵枢 (LingShu) — AI 调研助手（多Agent编排 + 搜索分析报告全链路 + 45+工具）
2. AIGC 创作工坊 — ComfyUI编排 + LoRA训练 + 批量生图 + 质量检测 + 漫画视频
3. 摄像头看门狗 — IoT 监控 + DNS 阻断 + 桌面告警

经历：自学，无全职 AI 经验，但有完整个人项目，能独立搭建一整套系统。
期望：AI 应用开发 / Python 后端 / AIGC 工程化 方向。
"""

# ── Ollama 配置 ──
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://172.18.9.126:11434/api/generate")
MODEL_NAME = os.environ.get("OLLAMA_MODEL", "qwen3:14b")  # qwen3:14b 或 qwen2.5:7b

SYSTEM_PROMPT = f"""你是一个求职者，正在 BOSS 直聘上找工作。
你的任务是：根据 HR 发布的岗位信息和你的个人背景，生成一个简短、得体、有针对性的打招呼语。

要求：
- 语气礼貌、专业，不卑不亢
- 突出与岗位相关的经验
- 控制在 50 字以内
- 不要问"请问还在招吗"之类废话
- 直接表达意向+匹配点

你的背景：
{RESUME_CONTEXT}
"""


def call_llm(job_title: str, company: str, salary: str = "", jd: str = "") -> str:
    """调本地 Ollama 生成招呼语。"""
    user_prompt = f"""
岗位：{job_title}
公司：{company}
薪资：{salary}
描述：{jd[:200]}

请根据以上岗位和你的背景，生成一句打招呼语。
"""

    payload = json.dumps({
        "model": MODEL_NAME,
        "prompt": SYSTEM_PROMPT + "\n" + user_prompt,
        "stream": False,
        "options": {"num_predict": 128}
    }).encode()

    try:
        req = Request(OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"})
        resp = urlopen(req, timeout=15)
        result = json.loads(resp.read())
        reply = result.get("response", "").strip()
        # 清理多余换行和引号
        reply = reply.replace("\n", "").replace("\"", "").replace("'", "")
        return reply[:100] or f"您好，我对{job_title}岗位很感兴趣，希望能进一步沟通。"
    except (URLError, json.JSONDecodeError) as e:
        print(f"[warn] Ollama 不可用: {e}，使用默认招呼语")
        return f"您好，我对贵公司的{job_title}岗位比较感兴趣，希望可以进一步沟通，谢谢。"


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        job_title = body.get("job_title", body.get("title", "AI 应用开发工程师"))
        company = body.get("company", body.get("brandName", "贵公司"))
        salary = body.get("salary", body.get("salaryDesc", ""))
        jd = body.get("jd", body.get("description", ""))

        reply = call_llm(job_title, company, salary, jd)

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps({"reply": reply}).encode("utf-8"))

    def log_message(self, format, *args):
        print(f"[boss-reply] {args[0]} {args[1]} {args[2]}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"BOSS 直聘智能回复服务运行中: http://127.0.0.1:{port}")
    print(f"模型: {MODEL_NAME} | Ollama: {OLLAMA_URL}")
    print(f"油猴脚本 Custom API URL 设为: http://127.0.0.1:{port}/greet")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
