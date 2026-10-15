"""P0-7 L 部分 凭据代理（broker）测试 — 确定性、无网络。

核心不变式：**密钥不进 context**。
  - 接口只返回不透明句柄（Lease），句柄本身不含密钥；
  - 唯一能用密钥的路径是 broker.use(handle, consumer)，密钥交进程内回调，
    不作为返回值出现；
  - 过期 / 用尽 / 未知句柄一律拒绝；
  - redact / repr 不泄露密钥。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core.auth import credential_broker as cb
from agent_harness.core.auth.credential_broker import (
    CredentialBroker,
    CredentialError,
    load_from_env,
)


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def broker(clock):
    b = CredentialBroker(default_ttl=60.0, clock=clock)
    b.register("openai", "sk-secret-VALUE-123")
    return b


# ─── 登记 ───

class TestRegister:
    def test_has_and_names(self, broker):
        assert broker.has("openai")
        assert not broker.has("nope")
        assert broker.names() == ["openai"]

    def test_register_rejects_empty_name_and_none(self, broker):
        with pytest.raises(ValueError):
            broker.register("", "x")
        with pytest.raises(ValueError):
            broker.register("k", None)


# ─── 换取短时访问：只发句柄，不发密钥 ───

class TestIssueLease:
    def test_lease_handle_does_not_contain_secret(self, broker):
        lease = broker.issue("openai")
        assert lease.handle
        assert "sk-secret-VALUE-123" not in lease.handle
        # 句柄可安全放进 context
        assert isinstance(lease.handle, str)

    def test_lease_and_broker_repr_do_not_leak_secret(self, broker):
        lease = broker.issue("openai")
        for obj in (lease, broker):
            assert "sk-secret-VALUE-123" not in repr(obj)
            assert "sk-secret-VALUE-123" not in str(obj)

    def test_unknown_credential_refused(self, broker):
        with pytest.raises(CredentialError):
            broker.issue("ghost")

    def test_two_leases_have_distinct_handles(self, broker):
        a = broker.issue("openai")
        b = broker.issue("openai")
        assert a.handle != b.handle


# ─── 使用：密钥只进回调，不进 context ───

class TestUse:
    def test_use_invokes_consumer_with_secret_and_returns_result(self, broker):
        seen = {}

        def consumer(secret):
            seen["secret"] = secret
            return {"status": 200, "tokens": 42}

        lease = broker.issue("openai")
        out = broker.use(lease.handle, consumer)
        assert seen["secret"] == "sk-secret-VALUE-123"      # 回调拿到了密钥
        assert out == {"status": 200, "tokens": 42}         # 返回的是结果，不是密钥
        assert "sk-secret-VALUE-123" not in str(out)

    def test_default_lease_is_single_use(self, broker):
        lease = broker.issue("openai")
        broker.use(lease.handle, lambda s: s)
        with pytest.raises(CredentialError):   # 第二次用同句柄 → 拒绝
            broker.use(lease.handle, lambda s: s)

    def test_max_uses_respected(self, broker):
        lease = broker.issue("openai", max_uses=2)
        broker.use(lease.handle, lambda s: 1)
        broker.use(lease.handle, lambda s: 1)
        with pytest.raises(CredentialError):
            broker.use(lease.handle, lambda s: 1)

    def test_expired_lease_refused(self, broker, clock):
        lease = broker.issue("openai", ttl=10.0)
        clock.advance(11.0)
        with pytest.raises(CredentialError):
            broker.use(lease.handle, lambda s: s)

    def test_unknown_handle_refused(self, broker):
        with pytest.raises(CredentialError):
            broker.use("deadbeef", lambda s: s)

    def test_consumer_error_still_consumes_use(self, broker):
        lease = broker.issue("openai")

        def boom(_secret):
            raise RuntimeError("upstream failed")

        with pytest.raises(RuntimeError):
            broker.use(lease.handle, boom)
        # 失败重放不得绕过次数上限
        with pytest.raises(CredentialError):
            broker.use(lease.handle, lambda s: s)

    def test_revoke(self, broker):
        lease = broker.issue("openai")
        assert broker.revoke(lease.handle) is True
        assert broker.revoke(lease.handle) is False
        with pytest.raises(CredentialError):
            broker.use(lease.handle, lambda s: s)


# ─── 防泄露 ───

class TestRedact:
    def test_redact_scrubs_secret_from_text(self, broker):
        text = "Authorization: Bearer sk-secret-VALUE-123 done"
        out = broker.redact(text)
        assert "sk-secret-VALUE-123" not in out
        assert "***" in out

    def test_redact_handles_none_and_non_str(self, broker):
        assert broker.redact(None) == ""
        assert "sk-secret-VALUE-123" not in broker.redact({"k": "sk-secret-VALUE-123"})


# ─── 集中化入口：env 加载 + 模块级便捷函数 ───

class TestEnvLoading:
    def test_load_from_env_returns_names_not_values(self, monkeypatch):
        monkeypatch.setenv("HARNESS_SECRET_STRIPE", "sk-live-xyz")
        monkeypatch.setenv("HARNESS_SECRET_SENDGRID", "sg-abc")
        b = CredentialBroker()
        names = load_from_env(broker=b)
        assert names == ["sendgrid", "stripe"]
        assert b.has("stripe") and "sk-live-xyz" not in repr(names)

    def test_load_from_env_uses_default_broker(self, monkeypatch):
        # 隔离：换掉模块级默认 broker，避免污染其他测试
        monkeypatch.setattr(cb, "_default_broker", CredentialBroker())
        monkeypatch.setenv("HARNESS_SECRET_FOO", "bar")
        names = cb.load_from_env()
        assert names == ["foo"]
        assert cb._default_broker.has("foo")


class TestModuleLevel:
    def test_module_level_roundtrip(self, monkeypatch):
        b = CredentialBroker()
        monkeypatch.setattr(cb, "_default_broker", b)
        cb.register_credential("svc", "s3cr3t")
        lease = cb.issue_lease("svc")
        assert cb.use_credential(lease.handle, lambda s: s == "s3cr3t") is True
        assert cb.redact("token=s3cr3t") == "token=***"
