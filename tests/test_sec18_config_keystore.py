"""SEC-18 回归门禁：config.json 里的 llm.api_key 必须加密落盘（本机派生 KEK）。

背景（2026-10-22 问题清单 SEC-18，定稿方向）：
- `save_config()` 曾把 `llm.api_key` 明文 `json.dump` 进 `~/.agent-harness/config.json`；
- 第一轮方案 A（剥离到 `HARNESS_CLOUD_KEY` 环境变量）被否决 —— 环境变量是进程内存态，
  进程重启全丢，桌面应用每次重开都要重填 key；
- 定稿：新增 `core/pipeline/keystore.py`，用「机器标识 + 用户名」PBKDF2 派生 KEK，
  Fernet 只加密 `llm.api_key` 单字段落 `api_key_enc`，启动时解密回内存。

本文件覆盖：
1. save 后磁盘上没有明文 key（只有 api_key_enc 且 != 明文）
2. load 后内存里 key 正确解密
3. 旧明文 config.json 一次性迁移成密文
4. 解密失败（换了机器/用户）不静默给空 key —— 有明确 ERROR 日志，且不崩
5. `redact_config()` 对外契约不变（仍只吐 has_api_key 布尔）
"""
from __future__ import annotations

import json
import logging
import os

import pytest

PLAINTEXT = "sk-test-123-sec18"


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """把 config.json 落点钉到 tmp_path，绝不碰真实 ~/.agent-harness。"""
    from agent_harness.core.pipeline import config_manager as cm

    cdir = tmp_path / "cfg"
    monkeypatch.setattr(cm, "CONFIG_DIR", cdir, raising=False)
    monkeypatch.setattr(cm, "CONFIG_PATH", cdir / "config.json", raising=False)
    for k in ("HARNESS_LLAMA_API", "HARNESS_CLOUD_KEY", "HARNESS_MODEL_LLAMA"):
        monkeypatch.delenv(k, raising=False)
    # 每个用例都从干净的身份缓存开始（有用例会改 _machine_id）
    _clear_keystore_cache()
    yield cm
    _clear_keystore_cache()


def _clear_keystore_cache():
    from agent_harness.core.pipeline import keystore

    for fn in ("_machine_id", "_derive_kek"):
        cache_clear = getattr(getattr(keystore, fn, None), "cache_clear", None)
        if cache_clear:
            cache_clear()


def _raw(cm) -> dict:
    return json.loads(cm.CONFIG_PATH.read_text(encoding="utf-8"))


# ─── 1. save：磁盘上无明文 ───
def test_save_config_writes_no_plaintext_api_key(_isolated_config):
    cm = _isolated_config
    cm.save_config({"llm": {"api_url": "https://api.example/v1", "api_key": PLAINTEXT,
                            "model": "m"}})

    text = cm.CONFIG_PATH.read_text(encoding="utf-8")
    assert PLAINTEXT not in text, "config.json 里仍有明文 api_key"
    llm = _raw(cm)["llm"]
    assert "api_key" not in llm, "明文 api_key 字段仍被写出"
    assert llm.get("api_key_enc"), "缺少 api_key_enc 密文字段"
    assert llm["api_key_enc"] != PLAINTEXT
    # 其余字段照旧落盘
    assert llm["api_url"] == "https://api.example/v1"
    assert llm["model"] == "m"


def test_save_config_keeps_caller_dict_in_memory(_isolated_config):
    """明文只留在内存 dict（调用方拿到的 config 不被就地清空）。"""
    cm = _isolated_config
    cfg = {"llm": {"api_key": PLAINTEXT, "api_url": "https://api.example/v1"}}
    cm.save_config(cfg)
    assert cfg["llm"]["api_key"] == PLAINTEXT


def test_save_config_empty_key_drops_ciphertext(_isolated_config):
    """用户清空 key 时，旧密文不能残留（否则 has_api_key 会一直为真）。"""
    cm = _isolated_config
    cm.save_config({"llm": {"api_url": "https://api.example/v1", "api_key": PLAINTEXT}})
    cm.save_config({"llm": {"api_url": "https://api.example/v1", "api_key": ""}})
    llm = _raw(cm)["llm"]
    assert "api_key_enc" not in llm and "api_key" not in llm


# ─── 2. load：内存里解密正确 ───
def test_load_config_decrypts_api_key(_isolated_config):
    cm = _isolated_config
    cm.save_config({"llm": {"api_url": "https://api.example/v1", "api_key": PLAINTEXT}})
    assert cm.load_config()["llm"]["api_key"] == PLAINTEXT


def test_load_config_does_not_expose_ciphertext_in_memory(_isolated_config):
    """内存契约：只有 api_key 明文，不外泄 api_key_enc（防二次落盘/日志带密文）。"""
    cm = _isolated_config
    cm.save_config({"llm": {"api_url": "https://api.example/v1", "api_key": PLAINTEXT}})
    assert "api_key_enc" not in cm.load_config()["llm"]


def test_roundtrip_save_after_load_keeps_key(_isolated_config):
    """设置页「读-改-写」一轮后 key 仍在，且磁盘仍是密文。"""
    cm = _isolated_config
    cm.save_config({"llm": {"api_url": "https://api.example/v1", "api_key": PLAINTEXT}})
    cfg = cm.load_config()
    cfg["ui"] = {"theme": "light"}
    cm.save_config(cfg)

    assert cm.load_config()["llm"]["api_key"] == PLAINTEXT
    assert cm.load_config()["ui"]["theme"] == "light"
    assert PLAINTEXT not in cm.CONFIG_PATH.read_text(encoding="utf-8")


# ─── 3. 旧明文 → 一次性迁移 ───
def test_load_config_migrates_legacy_plaintext(_isolated_config, caplog):
    cm = _isolated_config
    cm.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cm.CONFIG_PATH.write_text(json.dumps({
        "llm": {"api_url": "https://api.example/v1", "api_key": PLAINTEXT, "model": "m"},
        "services": {"searxng": True},
    }), encoding="utf-8")

    with caplog.at_level(logging.INFO):
        cfg = cm.load_config()

    assert cfg["llm"]["api_key"] == PLAINTEXT, "迁移后内存里必须仍有 key"
    text = cm.CONFIG_PATH.read_text(encoding="utf-8")
    assert PLAINTEXT not in text, "迁移后磁盘上仍有明文"
    llm = _raw(cm)["llm"]
    assert "api_key" not in llm and llm.get("api_key_enc")
    assert _raw(cm)["services"]["searxng"] is True
    assert any("迁移" in r.message for r in caplog.records), "迁移要留日志"

    # 幂等：再 load 一次不重复加密、结果不变
    assert cm.load_config()["llm"]["api_key"] == PLAINTEXT
    assert _raw(cm)["llm"]["api_key_enc"] == llm["api_key_enc"]


# ─── 4. 解密失败：明确报错，不静默 ───
def test_decrypt_failure_is_logged_not_silent(_isolated_config, caplog):
    cm = _isolated_config
    cm.save_config({"llm": {"api_url": "https://api.example/v1", "api_key": PLAINTEXT}})

    from agent_harness.core.pipeline import keystore
    # 模拟「config.json 被拷到另一台机器 / 换了 Windows 账户」
    keystore._machine_id.cache_clear() if hasattr(keystore._machine_id, "cache_clear") else None
    original = keystore._machine_id
    keystore._machine_id = lambda: "another-machine-guid"
    keystore._derive_kek.cache_clear()
    try:
        with caplog.at_level(logging.ERROR):
            cfg = cm.load_config()
    finally:
        keystore._machine_id = original
        keystore._derive_kek.cache_clear()

    assert cfg["llm"]["api_key"] == ""
    assert any(r.levelno >= logging.ERROR and "解密" in r.message
               for r in caplog.records), "解密失败必须打 ERROR（不能静默给空 key）"


# ─── 5. redact_config 对外契约不变 ───
def test_redact_config_contract_unchanged_after_encryption(_isolated_config):
    cm = _isolated_config
    cm.save_config({"llm": {"api_url": "https://api.example/v1", "api_key": PLAINTEXT}})
    out = cm.redact_config(cm.load_config())

    assert out["llm"]["api_key"] == ""
    assert out["llm"]["has_api_key"] is True
    assert PLAINTEXT not in json.dumps(out, ensure_ascii=False)


# ─── 6. 目录权限收紧（POSIX 可断言，Windows 只保证不阻断） ───
@pytest.mark.skipif(os.name != "posix", reason="Windows 用 icacls，权限位不可用 os.stat 断言")
def test_ensure_dir_is_owner_only_on_posix(_isolated_config):
    cm = _isolated_config
    cm._ensure_dir()
    assert (cm.CONFIG_DIR.stat().st_mode & 0o777) == 0o700


def test_ensure_dir_never_blocks_startup(_isolated_config, monkeypatch):
    """权限收紧失败（如 icacls 不可用/权限模型不同）不能阻断启动。"""
    cm = _isolated_config
    monkeypatch.setattr(cm, "_hard_dir_permissions",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")), raising=False)
    monkeypatch.setattr(cm, "_ACL_HARDENED", False, raising=False)
    cm._ensure_dir()          # 不应抛
    assert cm.CONFIG_DIR.is_dir()
