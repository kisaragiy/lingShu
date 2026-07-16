@echo off
title LingShu Server
cd /d C:\Users\zwq\agent-harness

set HARNESS_API_HOST=0.0.0.0
set HARNESS_DISABLE_AUTH=1
set HARNESS_DISABLE_RATE_LIMIT=1
set HARNESS_LLAMA_API=http://dummy:8000
set HARNESS_CLOUD_KEY=sk-dummy

cls
echo ==============================
echo  LingShu Server
echo ==============================
echo.
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /C:"IPv4" ^| findstr /V "127.0"') do set LAN=%%a
set LAN=%LAN: =%
echo  LAN IP: %LAN%
echo.
echo  Mini Program Settings:
echo    API: http://%LAN%:8788
echo    Token: dev-token-placeholder
echo.
echo  Ctrl+C to stop. Starting...
echo.

python -m agent_harness.main
pause
