"""通过真实 init.sh 验证状态新鲜度，避免测试副本逻辑与启动门禁漂移。"""

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


INIT_SH = Path(__file__).resolve().parents[1] / "init.sh"
BASH = shutil.which("bash")
NOW = datetime.now(timezone(timedelta(hours=8)))


@pytest.mark.skipif(BASH is None, reason="init.sh requires bash")
@pytest.mark.parametrize(
    ("payload", "exit_code", "message"),
    [
        ({"updated": NOW.isoformat()}, 0, "READY"),
        ({"updated": (NOW - timedelta(days=8)).isoformat()}, 2, "ALERT"),
        ({}, 2, "ALERT"),
        ({"updated": "not-a-date"}, 2, "ALERT"),
        ({"updated": None}, 2, "ALERT"),
        ({"updated": 123}, 2, "ALERT"),
        ({"updated": "2026-10-23T00:00:00"}, 2, "ALERT"),
        ([], 2, "ALERT"),
        ("invalid-json", 2, "ALERT"),
    ],
    ids=["fresh", "stale", "missing", "malformed", "null", "numeric", "no-timezone", "not-object", "bad-json"],
)
def test_progress_freshness(tmp_path, payload, exit_code, message):
    shutil.copyfile(INIT_SH, tmp_path / "init.sh")
    text = payload if isinstance(payload, str) else json.dumps(payload)
    (tmp_path / "PROGRESS.json").write_text(text, encoding="utf-8")
    result = subprocess.run(
        [BASH, "init.sh"],
        cwd=tmp_path,
        env={**os.environ, "PYTHON": sys.executable, "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    output = result.stdout + result.stderr
    assert result.returncode == exit_code, output
    assert message in output
    if exit_code == 2:
        assert "READY" not in output
