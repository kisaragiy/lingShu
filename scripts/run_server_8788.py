#!/usr/bin/env python3
"""启动灵枢 FastAPI 后端（8788）"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))
import uvicorn
from agent_harness.main import app

if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=8788, log_level='warning')
