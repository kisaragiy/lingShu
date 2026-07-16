@echo off
set http_proxy=
set https_proxy=
set HTTP_PROXY=
set HTTPS_PROXY=
set NO_PROXY=127.0.0.1,localhost
set HARNESS_LLAMA_API=http://127.0.0.1:18888/v1/chat/completions
set HARNESS_MODEL_LLAMA=mock
set HARNESS_DISABLE_AUTH=1
cd /d C:\Users\zwq\agent-harness
C:\Users\zwq\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe -c "import agent_harness.apps.research.api; agent_harness.apps.research.api.PORT=8765; from agent_harness.main import main; main()"
pause
