@echo off
title 灵枢AI — LingShu Agent
cd /d C:\Users\zwq\agent-harness

:: ─── 配置 ───
:: 如需 DeepSeek 云端 API，取消注释并填入 Key：
:: set HARNESS_DEEPSEEK_API=https://api.deepseek.com/v1/chat/completions
:: set HARNESS_CLOUD_KEY=sk-你的Key

:: 使用本地 llama.cpp（无需 Key）：
set HARNESS_LLAMA_API=http://127.0.0.1:8081/v1/chat/completions

:: ─── 启动 ───
cls
echo.
echo  ╔══════════════════════════════════════════════╗
echo  ║      灵枢 AI — LingShu Agent               ║
echo  ║      AI 调研助手 · 多 Agent 编排平台       ║
echo  ╚══════════════════════════════════════════════╝
echo.
echo  启动中...
echo.
echo  ▶ 主应用:       http://127.0.0.1:8788
echo  ▶ CS Demo 客服: http://127.0.0.1:8788/cs-demo
echo  ▶ 知识库问答:   http://127.0.0.1:8788/knowledge-qa
echo  ▶ API:          http://127.0.0.1:8788/v1
echo.
echo  按 Ctrl+C 停止服务
echo.

python -m agent_harness.main
pause
