"""
凭据代理（Credential Broker）— 让密钥不进 context（P0-7 L 部分）

痛点：凭据一旦进了 context（prompt / 工具参数 / 日志），一次提示注入即可把
它外带。所以 agent 与工具**不应直接持有密钥**，而应通过 broker **按需换取**
一次短时、有限的访问权。

本模块把密钥挡在 context 之外：
  - 密钥只存在 broker 内部（内存 + 可选从环境变量加载），**永不随接口返回**；
  - agent/工具拿到的是**不透明句柄**（Lease），句柄本身泄露不了密钥；
  - 真正使用密钥的唯一途径是 :meth:`CredentialBroker.use`：把密钥交给一个
    **进程内回调** consumer 使用，回调返回值被返回，密钥本身不被返回；
  - 句柄有 TTL 与可用次数上限（短时访问）；
  - :meth:`CredentialBroker.redact` 把已知密钥从任意文本里擦除，防日志/回显泄露。

设计原则：不硬造基础设施 —— 灵枢没有独立的 secrets 服务，本 broker 是一个
纯进程内的可测封装，落在既有的 ``core/auth`` 下，不触碰任何现有公开 API。
"""
from __future__ import annotations

import os
import secrets as _secrets
import time
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

T = TypeVar("T")

DEFAULT_TTL = 60.0        # 秒：一次短时访问默认 60s 失效
DEFAULT_MAX_USES = 1      # 默认一次性
_ENV_PREFIX = "HARNESS_SECRET_"


class CredentialError(Exception):
    """凭据代理相关错误（未知句柄 / 过期 / 用尽）。"""


@dataclass(frozen=True)
class Lease:
    """一次性/短时的访问租约。

    ``handle`` 是不透明句柄，**可以**放进 context（它不含密钥）；密钥本身
    只能由 :meth:`CredentialBroker.use` 在进程内消费。
    """

    handle: str
    name: str
    expires_at: float
    max_uses: int
    uses_left: int

    def __repr__(self) -> str:  # noqa: D105
        return (f"Lease(name={self.name!r}, handle={self.handle!r}, "
                f"uses_left={self.uses_left})")


class CredentialBroker:
    """进程内凭据代理。密钥只进不出。"""

    def __init__(self, *, default_ttl: float = DEFAULT_TTL,
                 clock: Callable[[], float] = time.monotonic):
        self._secrets: dict[str, str] = {}
        self._leases: dict[str, dict] = {}
        self._default_ttl = default_ttl
        self._clock = clock

    # ── 登记（密钥入口，集中于此） ──
    def register(self, name: str, value: str) -> None:
        """登记一个密钥。这是**唯一**的密钥写入入口（集中化）。"""
        if not name:
            raise ValueError("凭据名不能为空")
        if value is None:
            raise ValueError("凭据值不能为 None")
        self._secrets[name] = str(value)

    def has(self, name: str) -> bool:
        """是否已登记该凭据（不泄露值）。"""
        return name in self._secrets

    def names(self) -> list[str]:  # 只回名字，绝不回值
        return sorted(self._secrets)

    # ── 换取短时访问（发句柄，不发密钥） ──
    def issue(self, name: str, *, ttl: float | None = None,
              max_uses: int = DEFAULT_MAX_USES) -> Lease:
        """为某凭据签发一个短时租约，返回只含**不透明句柄**的 Lease。"""
        if name not in self._secrets:
            raise CredentialError(f"未知凭据: {name}")
        if max_uses < 1:
            raise ValueError("max_uses 必须 >= 1")
        handle = _secrets.token_hex(16)
        lease = Lease(
            handle=handle,
            name=name,
            expires_at=self._clock() + (self._default_ttl if ttl is None else ttl),
            max_uses=max_uses,
            uses_left=max_uses,
        )
        self._leases[handle] = {"lease": lease, "state": lease}
        return lease

    # ── 使用（唯一能碰到密钥的路径） ──
    def use(self, handle: str, consumer: Callable[[str], T]) -> T:
        """用租约换取一次访问：把密钥交给 ``consumer`` 使用，返回其返回值。

        密钥**不会**作为返回值出现在本方法的输出里；过期/用尽的租约一律拒绝。
        失败（consumer 抛错）也会消耗一次使用，避免失败重放绕过次数上限。
        """
        entry = self._leases.get(handle)
        if entry is None:
            raise CredentialError("未知或已撤销的句柄")
        lease = entry["lease"]
        if self._clock() >= lease.expires_at:
            del self._leases[handle]
            raise CredentialError("租约已过期")
        if entry["state"].uses_left <= 0:
            raise CredentialError("租约可用次数已用尽")

        secret = self._secrets.get(lease.name)
        if secret is None:  # 登记被清除
            raise CredentialError(f"凭据已不可用: {lease.name}")

        # 先扣次数（fail-safe：即便 consumer 抛错也不返还）
        remaining = entry["state"].uses_left - 1
        entry["state"] = Lease(
            handle=lease.handle, name=lease.name, expires_at=lease.expires_at,
            max_uses=lease.max_uses, uses_left=remaining,
        )
        if remaining <= 0:
            del self._leases[handle]
        return consumer(secret)

    def revoke(self, handle: str) -> bool:
        """撤销一个租约。"""
        return self._leases.pop(handle, None) is not None

    # ── 防泄露 ──
    def redact(self, text: Any) -> str:
        """把文本中出现的所有已知密钥替换为 ``***``（日志/回显前调用）。"""
        s = "" if text is None else str(text)
        for value in self._secrets.values():
            if value:
                s = s.replace(value, "***")
        return s

    def __repr__(self) -> str:  # noqa: D105 — 绝不泄露密钥值
        return f"CredentialBroker(secrets={len(self._secrets)}, leases={len(self._leases)})"


# ─── 模块级默认 broker + 便捷函数 ───

_default_broker = CredentialBroker()


def get_broker() -> CredentialBroker:
    """取默认 broker 实例。"""
    return _default_broker


def register_credential(name: str, value: str) -> None:
    _default_broker.register(name, value)


def issue_lease(name: str, *, ttl: float | None = None,
                max_uses: int = DEFAULT_MAX_USES) -> Lease:
    return _default_broker.issue(name, ttl=ttl, max_uses=max_uses)


def use_credential(handle: str, consumer: Callable[[str], T]) -> T:
    return _default_broker.use(handle, consumer)


def revoke_lease(handle: str) -> bool:
    return _default_broker.revoke(handle)


def redact(text: Any) -> str:
    return _default_broker.redact(text)


def load_from_env(prefix: str = _ENV_PREFIX, broker: CredentialBroker | None = None) -> list[str]:
    """从环境变量加载密钥：``HARNESS_SECRET_<NAME>`` → 凭据 ``<name>``。

    这是把密钥从 env 收进 broker（而非散落在调用方）的集中入口。
    返回加载到的凭据名列表（**不含值**）。
    """
    b = broker or _default_broker
    loaded: list[str] = []
    for key, value in os.environ.items():
        if key.startswith(prefix) and len(key) > len(prefix):
            name = key[len(prefix):].lower()
            b.register(name, value)
            loaded.append(name)
    return sorted(loaded)
