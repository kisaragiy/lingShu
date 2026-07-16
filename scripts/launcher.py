import os, sys, subprocess, time

# 杀掉旧进程
for port in [8765, 18888]:
    try:
        import psutil
        for conn in psutil.net_connections():
            if conn.laddr.port == port and conn.status == 'LISTEN':
                psutil.Process(conn.pid).kill()
    except:
        pass
time.sleep(2)

# 干净环境（无代理）
env = {k: v for k, v in os.environ.items()
       if not k.lower().startswith('http_') and not k.lower().startswith('https_')}
env['NO_PROXY'] = '127.0.0.1,localhost'

# 启动 mock LLM
subprocess.Popen(
    [sys.executable, os.path.join(os.path.dirname(__file__), 'mock_llm.py')],
    env=env, cwd=os.path.dirname(__file__)
)
time.sleep(2)

# 先设环境变量，再导入灵枢
env['HARNESS_LLAMA_API'] = 'http://127.0.0.1:18888/v1/chat/completions'
env['HARNESS_MODEL_LLAMA'] = 'mock'
env['HARNESS_DISABLE_AUTH'] = '1'
os.environ.clear()
os.environ.update(env)

# 启动灵枢
os.chdir(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'src'))
import agent_harness.apps.research.api
agent_harness.apps.research.api.PORT = 8765
from agent_harness.main import main
main()
