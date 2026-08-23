#!/bin/bash
cd /c/Users/zwq/agent-harness
echo "=== Failed tests ==="
C:/Users/zwq/AppData/Local/Programs/Python/Python311/python.exe -m pytest tests/test_skill_generator.py tests/test_rev_utils.py tests/test_search_chain.py -v --tb=line 2>&1 | grep -E "FAILED|ERROR" | head -20
echo ""
echo "=== Import check ==="
cat > /tmp/chk_imports.py << 'PYEOF'
import sys
sys.path.insert(0, "src")
modules = [
    "agent_harness.core.tools.pattern_scan",
    "agent_harness.core.tools.mcp_health", 
    "agent_harness.core.tools.reasoning",
    "agent_harness.core.tools.rev_utils",
]
for m in modules:
    try:
        __import__(m)
        print(f"  ✅ {m}")
    except Exception as e:
        print(f"  ❌ {m}: {e}")
PYEOF
C:/Users/zwq/AppData/Local/Programs/Python/Python311/python.exe /tmp/chk_imports.py