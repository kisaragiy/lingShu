@echo off
title 灵枢 — 开启手机预览
cd /d C:\Users\zwq\agent-harness

:: 获取局域网 IP
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /C:"IPv4"') do (
    set LAN_IP=%%a
    goto :got_ip
)
:got_ip
set LAN_IP=%LAN_IP: =%

cls
echo  ╔══════════════════════════════════════════════╗
echo  ║    灵枢 — 手机预览模式                     ║
echo  ╚══════════════════════════════════════════════╝
echo.
echo  电脑局域网 IP: %LAN_IP%
echo.
echo  == 小程序真机调试步骤 ==
echo  1. 打开微信开发者工具 → 项目 → 真机调试
echo  2. 手机扫码 → 小程序在手机上运行
echo  3. 在小程序「设置」页填入：
echo     API 地址: http://%LAN_IP%:8788
echo     API Token: dev-token-placeholder
echo  4. 点击「测试连接」验证
echo.
echo  == 手机浏览器直接访问 ==
echo  CS Demo:  http://%LAN_IP%:8788/cs-demo
echo  主应用:   http://%LAN_IP%:8788
echo.
echo  按任意键启动服务（按 Ctrl+C 停止）...
pause >nul

:: 启动后端（0.0.0.0 使局域网可访问）
set HARNESS_API_HOST=0.0.0.0
set HARNESS_DISABLE_AUTH=1
set HARNESS_DISABLE_RATE_LIMIT=1
set HARNESS_LLAMA_API=http://dummy:8000
set HARNESS_CLOUD_KEY=sk-dummy

python -m agent_harness.main
pause
