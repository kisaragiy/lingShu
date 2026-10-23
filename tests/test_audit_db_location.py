"""P1-2：审计库必须落在数据根，不能落源码树/安装目录。

缺陷（gpt6 评审 P1-2）：``core/audit.py`` 的 ``DB_PATH`` 是
``Path(__file__).parent.parent.parent / "data" / "audit.db"`` →
指向 ``src/data/audit.db``：pip 安装后不可写（审计静默失效），跑测试污染源码树。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core import audit as audit_mod  # noqa: E402

_SRC_ROOT = Path(__file__).resolve().parent.parent / "src"


def test_db_path_is_not_inside_source_tree():
    assert _SRC_ROOT not in audit_mod.DB_PATH.parents, (
        f"审计库落在源码树内: {audit_mod.DB_PATH}"
    )


def test_audit_write_does_not_touch_source_tree_db(tmp_path):
    """源码树里的 audit.db 可能是**历史遗留**（旧实现写的），所以断言用指纹不变：
    跑一次审计后它的大小/修改时间必须原封不动。回退源码（DB_PATH 钉回 __file__）
    该断言立刻转红。"""
    legacy = _SRC_ROOT / "data" / "audit.db"

    def _fingerprint():
        if not legacy.exists():
            return None
        st = legacy.stat()
        return (st.st_size, st.st_mtime_ns)

    before = _fingerprint()
    real = audit_mod.DB_PATH
    audit_mod.DB_PATH = tmp_path / "audit.db"
    try:
        audit_mod.audit("p1_2_probe", ok=True)
        assert (tmp_path / "audit.db").exists()
    finally:
        audit_mod.DB_PATH = real

    assert _fingerprint() == before, "测试期间往源码树的 src/data/audit.db 写了数据"


def test_db_path_follows_data_dir_env(tmp_path, monkeypatch):
    # data_dir 是 import-time 常量（与其它数据根一致），测试直接替换模块属性
    monkeypatch.setattr(audit_mod, "data_dir", str(tmp_path))
    monkeypatch.delenv("HARNESS_AUDIT_DB", raising=False)
    assert audit_mod._resolve_db_path() == tmp_path / "audit.db"

    monkeypatch.setenv("HARNESS_AUDIT_DB", str(tmp_path / "explicit.db"))
    assert audit_mod._resolve_db_path() == tmp_path / "explicit.db"
