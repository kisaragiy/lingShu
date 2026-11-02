"""The local selection gate must default to exhaustive checks on uncertainty."""
from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "test_selection.py"
spec = importlib.util.spec_from_file_location("test_selection", MODULE)
assert spec and spec.loader
selector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selector)


def test_docs_only_skips_pytest():
    assert selector.select_tests(["docs/plans/testing-strategy.md", "README.md"])[0] == "skip"


def test_frontend_change_selects_audited_tests():
    mode, tests, _ = selector.select_tests(["src/agent_harness/apps/research/static/index.html"])
    assert mode == "selected"
    assert "tests/test_frontend_contract.py" in tests


def test_core_security_change_forces_full():
    assert selector.select_tests(["src/agent_harness/core/auth/auth_db.py"])[0] == "full"


def test_unknown_file_forces_full():
    assert selector.select_tests(["scripts/mystery.sh"])[0] == "full"


def test_test_file_change_is_selected():
    assert selector.select_tests(["tests/test_slo.py"])[0] == "selected"


def test_shared_fixture_forces_full():
    assert selector.select_tests(["tests/conftest.py"])[0] == "full"


def test_missing_affected_test_forces_full():
    mode, _, _ = selector.select_tests(["src/agent_harness/core/slo.py"], exists=lambda _: False)
    assert mode == "full"


def test_integration_change_forces_full():
    assert selector.select_tests(["tests/test_integration.py"])[0] == "full"


def test_empty_staging_skips():
    assert selector.select_tests([])[0] == "skip"
