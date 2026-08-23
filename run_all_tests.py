import subprocess, sys
r = subprocess.run(
    ["C:/Users/zwq/AppData/Local/Programs/Python/Python311/python.exe",
     "-m", "pytest", "tests/", "-q",
     "--ignore=tests/test_cs_stream.py"],
    capture_output=True, text=True, timeout=300,
    cwd="C:/Users/zwq/agent-harness",
)
print(r.stdout[-500:] if r.stdout else r.stderr[-500:])
print("EXIT:", r.returncode)