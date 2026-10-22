"""Keystore — 用本机派生密钥（KEK）加密落盘 `llm.api_key`。

背景（SEC-18，2026-10-22 定稿方向）
------------------------------------
`save_config()` 曾把 `llm.api_key` 明文 json.dump 进 `~/.agent-harness/config.json`。
第一轮方案 A（把 key 剥离到 `HARNESS_CLOUD_KEY` 环境变量）被否决：环境变量是**进程内存态**，
进程一重启全丢，桌面应用每次重开都要重填 key —— 产品上不可接受。

折中方案（本模块）：key 仍不进 config.json 明文，但以**本机派生密钥加密后的密文**落盘
（`llm.api_key_enc`），启动时解密回内存。这样：
- 配置文件被拷走（备份、误传、云同步）→ 拿到的是 Fernet 密文，别的机器解不开；
- 进程重启 → 本机 KEK 可重算，key 自动恢复，用户不必重填。

KEK 派生：`PBKDF2-HMAC-SHA256(machine_id + "|" + username, salt=b"lingshu-config-v1")`
- POSIX：`/etc/machine-id`
- Windows：注册表 `HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid`

权衡（面试要讲清）：这是**防「文件静置泄露」**，不是防「本机同一用户被攻破」——
后者需要 OS keychain / TPM，属于本项明确不做（不引入 Vault/KMS/keyring 依赖）。
机器身份变了（换机、换 Windows 账户、重装）→ 密文解不开，这里**明确报错并提示重填**，
绝不静默返回空 key 让上游以为「没配置」。
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import sys
from functools import lru_cache

logger = logging.getLogger(__name__)

_SALT = b"lingshu-config-v1"
_ITERATIONS = 200_000


class KeystoreError(RuntimeError):
    """KEK 派生 / 加解密失败（依赖缺失、机器身份变更、密文损坏）。"""


@lru_cache(maxsize=1)
def _machine_id() -> str:
    """本机唯一标识。取不到时返回空串（只用用户名做种子，仍能自洽）。"""
    posix_path = "/etc/machine-id"
    if os.path.exists(posix_path):
        try:
            with open(posix_path, encoding="utf-8", errors="ignore") as f:
                value = f.read().strip()
            if value:
                return value
        except OSError as e:  # 权限/竞态 —— 退到 Windows 分支或用户名
            logger.debug("读取 /etc/machine-id 失败：%s", e)

    if sys.platform == "win32":
        # 用 winreg 读（纯 Python、无子进程）——直接 `reg query` 会新增一处裸 subprocess，
        # 与 tests/test_architecture_boundaries.py 规则 8（外部调用必须收敛）冲突。
        try:
            import winreg  # noqa: PLC0415 —— 仅 Windows 存在

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Cryptography") as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
            if str(value).strip():
                return str(value).strip()
        except (ImportError, OSError) as e:
            logger.debug("winreg 读取 MachineGuid 失败：%s", e)

    return ""


def _username() -> str:
    try:
        import getpass

        return getpass.getuser()
    except Exception:  # noqa: BLE001 —— 无 tty/受限容器下 getuser 会抛
        return os.environ.get("USERNAME") or os.environ.get("USER") or ""


@lru_cache(maxsize=1)
def _derive_kek() -> bytes:
    """派生出 Fernet 需要的 32 字节 urlsafe-base64 key（本进程内缓存）。"""
    seed = f"{_machine_id()}|{_username()}".encode()
    raw = hashlib.pbkdf2_hmac("sha256", seed, _SALT, _ITERATIONS)
    return base64.urlsafe_b64encode(raw)


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError as e:  # pragma: no cover —— pyproject 已声明依赖
        raise KeystoreError(
            "缺少 cryptography 依赖，无法加解密 llm.api_key；请 pip install cryptography"
        ) from e
    return Fernet(_derive_kek())


def encrypt_key(plaintext: str) -> str:
    """明文 key → Fernet token（str，可直接进 JSON）。"""
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_key(token: str) -> str:
    """Fernet token → 明文 key。解不开抛 KeystoreError（调用方负责报错，不得静默吞）。"""
    if not token:
        return ""
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError) as e:
        raise KeystoreError(
            "无法解密 config.json 中的 llm.api_key_enc：本机标识或用户名已变更"
            "（换机/重装/换了 Windows 账户），密文不可恢复 —— 请重新填写 API key。"
        ) from e
