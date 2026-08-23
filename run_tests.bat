@echo off
cd /d C:\Users\zwq\agent-harness
C:\Users\zwq\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_skill_generator.py tests/test_rev_utils.py tests/test_search_chain.py -v --tb=line 2>&1 | findstr /v "PASSED" | findstr "FAILED ERROR"
echo ===
C:\Users\zwq\AppData\Local\Programs\Python\Python311\python.exe -c "import sys; sys.path.insert(0,'src'); exec(open('tmp_check_imports.py').read())" 2>&1