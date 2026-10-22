"""SEC-5：API Key 的角色不再是硬编码 admin。

原状：认证中间件里 `"role": "admin"` 是字面量 —— 单 token 自托管下算设计意图，
但脚本/CI/网关转发用的 token 无法降权，且配置失误也没法纠正。
现在由 HARNESS_API_KEY_ROLE 显式决定，非法值一律降级为 user（配置失误 ≠ 提权）。
"""

import pytest

from agent_harness.core.auth.api_security import (
    VALID_API_KEY_ROLES,
    resolve_api_key_role,
)


def test_default_is_admin_for_backward_compat():
    """不配 = 现有客户端零改动（自托管单用户场景一个 key 服务一个部署者）。"""
    assert resolve_api_key_role(None) == "admin"
    assert resolve_api_key_role("") == "admin"


def test_explicit_user_downgrade():
    """脚本/CI token 可以显式降权到 user —— 这是原硬编码做不到的事。"""
    assert resolve_api_key_role("user") == "user"
    assert resolve_api_key_role("  ADMIN  ") == "admin"


@pytest.mark.parametrize("bogus", ["root", "superuser", "superadmin", "true", "1", "管理员"])
def test_invalid_value_fails_closed_to_user(bogus):
    """配置写错时必须取最小权限 —— 绝不能让一个拼错的角色名变成全通行。"""
    warnings = []
    assert resolve_api_key_role(bogus, warn=warnings.append) == "user"
    assert warnings and "非法" in warnings[0]


def test_warning_goes_to_stderr_by_default(capsys):
    """没有 warn 回调时也要留痕，不能静默降级。"""
    assert resolve_api_key_role("nope") == "user"
    err = capsys.readouterr().err
    assert "[AUTH]" in err and "降级为 user" in err


def test_role_whitelist_is_closed():
    assert set(VALID_API_KEY_ROLES) == {"admin", "user"}
