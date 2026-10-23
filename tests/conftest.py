"""Shared fixtures for LingShu Agent test suite."""
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure the src package is importable
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


# ═══════════════════════════════════════════════════════════════════════════
# 第一道防线：**事前**把数据根重定向到 tmp（ADR#5）
# ═══════════════════════════════════════════════════════════════════════════
# 为什么必须是 pytest_configure 而不是 fixture：
#   pytest 在 **collection 期**就 import 测试模块，fixture 要到那之后才执行；而数据根绑定
#   大多是 **import-time 常量**（`config_manager.CONFIG_DIR`、`report_store.REPORTS_DIR`…），
#   "谁先 import 谁说了算"。模块级 `os.environ[...] = ...` 那类写法（曾出现在
#   tests/test_dynamic_llm.py）会在 fixture 之前落地 → fixture 拦不住。
# 实测过的最贵后果：`config_manager.CONFIG_PATH` 指向真实 `~/.agent-harness/config.json`，
#   而 `save_config` 是**整文件覆盖**（不是合并）→ 会清空用户已填的 api_url/api_key/model。
DATA_ROOT_ENV = (
    "HARNESS_DATA_DIR",
    "HARNESS_REPORTS_DIR",
    "HARNESS_CONFIG_DIR",
    "HARNESS_SESSION_DIR",
    "HARNESS_SCHEDULER_DIR",
    "HARNESS_SKILLS_DIR",
    "HARNESS_PLUGINS_DIR",
    "HARNESS_RAG_DIR",
)

_TMP_DATA_ROOT: Path | None = None


def pytest_configure(config):  # noqa: ARG001  (pytest hook)
    """startup 期（早于 collection）把每个数据根环境变量指向同一个 tmp 根。"""
    global _TMP_DATA_ROOT
    if _TMP_DATA_ROOT is None:
        _TMP_DATA_ROOT = Path(tempfile.mkdtemp(prefix="ling_shu_test_root_"))
    for name in DATA_ROOT_ENV:
        sub = name.removeprefix("HARNESS_").removesuffix("_DIR").removesuffix("_DATA").lower()
        os.environ[name] = str(_TMP_DATA_ROOT / sub)
    # 审计库是**文件**路径（不是目录），不能用上面的目录规则；不重定向则测试会在
    # 源码树里写 src/data/audit.db（原实现把 DB_PATH 钉在 __file__ 上三级）。
    os.environ["HARNESS_AUDIT_DB"] = str(_TMP_DATA_ROOT / "audit" / "audit.db")


@pytest.fixture(scope="session")
def tmp_data_root() -> Path:
    """本会话的测试数据根（所有 HARNESS_*_DIR 都指向它的子目录）。"""
    assert _TMP_DATA_ROOT is not None, "pytest_configure 未执行，数据根重定向失效"
    return _TMP_DATA_ROOT


# ═══════════════════════════════════════════════════════════════════════════
# 第二道防线：真实数据根指纹（事后检测 —— 覆盖第一道防线漏掉的东西）
# ═══════════════════════════════════════════════════════════════════════════
# 运行期由**活进程**写入的目录：任何进程调用灵枢工具层都会落审计文件
# （core/tools/permission.py AUDIT_DIR + core/tools/rag_store.py import 即建目录）。
# 对这类目录做"会话首尾必须一致"的断言必然假红（不是测试越界，是别的进程在写），
# 假红的守卫会被关掉 → 比覆盖面窄更糟。故显式排除并在此留痕，**替代断言**见
# tests/test_architecture_boundaries.py::test_data_root_bindings_resolve_under_tmp
# （路径归属断言：这类目录必须解析到 tmp 下，而不是靠内容指纹）。
_RUNTIME_OWNED_PREFIXES = ("audit/", "agent_logs/", "pending/", "scheduler/")


def _fingerprint_tree(root: Path) -> dict:
    """递归指纹：**内容哈希 + size + mtime_ns**，排除 sqlite 运行期易变项。

    - 只列文件名会漏"既有文件被改写"；
    - 只哈希内容会漏**"内容幂等的错误写入"**（写回同样的内容 → 哈希不变 → 守卫只红一次，
      之后永远绿；2026-09-10 实测到过这种"第二次跑就绿"的形状）。所以才带上 size/mtime。
    """
    snap: dict = {}
    if not root.is_dir():
        return snap
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name.endswith(("-wal", "-shm", "-journal")):
            continue
        rel = p.relative_to(root).as_posix()
        if rel.startswith(_RUNTIME_OWNED_PREFIXES):
            continue
        try:
            st = p.stat()
            snap[rel] = (hashlib.sha256(p.read_bytes()).hexdigest(), st.st_size, st.st_mtime_ns)
        except OSError as exc:          # 读不到也要留痕，不能当"没有这个文件"
            snap[rel] = (f"<unreadable: {exc.__class__.__name__}>", -1, -1)
    return snap


def _diff_summary(before: dict, after: dict) -> str:
    """把"变了"变成**可归因**的信息（只报"变了"等于没报）。"""
    lines = []
    for k in sorted(set(after) - set(before)):
        lines.append(f"  新增 {k}  size={after[k][1]} mtime={after[k][2]}")
    for k in sorted(set(before) - set(after)):
        lines.append(f"  删除 {k}（原有 size={before[k][1]}）")
    for k in sorted(set(before) & set(after)):
        if before[k] != after[k]:
            h_b, h_a = before[k][0][:12], after[k][0][:12]
            what = "内容变了" if h_b != h_a else "内容未变但 size/mtime 变了"
            lines.append(f"  {k}: {what}  hash {h_b}→{h_a}  size {before[k][1]}→{after[k][1]}  mtime {before[k][2]}→{after[k][2]}")
    return "\n".join(lines)


@pytest.fixture(scope="session", autouse=True)
def _no_writes_to_real_user_data():
    """测试**不得**改真实 `~/.agent-harness` 里的任何文件。

    这是**事后检测**（第二道防线），不是隔离——只能告诉你有越界，拦不住写；
    真正的隔离是第一道防线（上面的 `pytest_configure` 重定向）。
    若这里红：先看 `_diff_summary` 的信息 ——
      * "内容变了" → 有绑定没走 tmp 根（多半是 import-time 常量在 collection 前就绑死了）；
      * "内容未变但 size/mtime 变了" → 多半是**运行期活进程**（设置页/定时任务）写回同一内容，
        不是测试所为；确认后把该路径加进 `_RUNTIME_OWNED_PREFIXES` 并在此留痕，别默默关守卫。
    """
    real = Path.home() / ".agent-harness"
    before = _fingerprint_tree(real)
    yield
    after = _fingerprint_tree(real)

    if before != after:
        pytest.fail(
            "测试改动了真实用户数据（ADR#5 违规）—— 可归因明细：\n"
            + _diff_summary(before, after)
            + "\n常见根因：import-time 路径常量在 collection 前就绑定到真实根（要用 "
              "conftest.pytest_configure 重定向，而不是测试模块里的模块级 env 写）。"
        )


@pytest.fixture
def mock_response():
    """Build a simple mock HTTP response object with status_code, text, and json()."""

    class MockResponse:
        def __init__(self, status_code=200, text="", json_data=None):
            self.status_code = status_code
            self.text = text
            self._json = json_data or {}

        def json(self):
            return self._json

    return MockResponse


@pytest.fixture
def mock_session(mock_response, monkeypatch):
    """Fixture that patches requests.Session with a controlled mock."""
    import requests

    class MockSession:
        def __init__(self):
            self.responses = {}
            self.last_url = None
            self.last_params = None
            self.last_headers = None
            self.last_json = None

        def get(self, url, **kwargs):
            self.last_url = url
            self.last_params = kwargs.get("params")
            self.last_headers = kwargs.get("headers", {})
            resp = self.responses.get(url) or mock_response()
            return resp

        def post(self, url, **kwargs):
            self.last_url = url
            self.last_json = kwargs.get("json")
            self.last_headers = kwargs.get("headers", {})
            resp = self.responses.get(url) or mock_response()
            return resp

    fake_session = MockSession()
    monkeypatch.setattr("agent_harness.core.pipeline.llm._session", fake_session)
    # Also patch the session used by web.py which imports it from llm
    from agent_harness.core.tools import web as web_module
    monkeypatch.setattr(web_module, "_session", fake_session)

    return fake_session


@pytest.fixture
def test_config():
    """Provide standard test configuration values."""
    return {
        "MODEL_LLAMA": "test-model",
        "LLAMA_API": "http://test.local:8081/v1/chat/completions",
        "HARNESS_DIR": Path(__file__).resolve().parent.parent / "src" / "agent_harness",
    }
