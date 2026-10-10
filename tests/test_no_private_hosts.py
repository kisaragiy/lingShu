"""P0 门禁：禁止把开发机 / 私网地址硬编码进产品代码。

背景（2026-09-10 评审发现）：
- `rag_store.py` 曾默认把用户上传的文档原文 POST 到 `172.18.9.126:11434`
  （172.16.0.0/12 是私网段 —— 分发到熟人机器后，那个地址可能是路由器 DHCP
  池里的别人的电脑）→ 用户资料出境 + 嵌入静默失败退化 BM25。
- `config_manager.py` 曾硬编码 `C:\\Users\\zwq\\Downloads\\llama.cpp` →
  在任何非开发机上"一键修复"必然报"未找到 model_proxy.py"。

本测试是发版门禁：产品代码（src/）不得出现私网 IP 或个人绝对路径。
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

# 私网段：10/8、172.16/12、192.168/16
_PRIVATE_IP = re.compile(
    r"\b(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b"
)
# Windows 个人目录绝对路径：C:\Users\<name>\
_PERSONAL_PATH = re.compile(r"[A-Za-z]:\\Users\\[A-Za-z0-9_.\-]+\\")


def _py_files():
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _scan(pattern):
    hits = []
    for p in _py_files():
        text = p.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{p.relative_to(SRC)}:{i}: {line.strip()[:100]}")
    return hits


def test_no_private_ip_hardcoded():
    hits = _scan(_PRIVATE_IP)
    assert not hits, (
        "产品代码里出现私网 IP 硬编码（分发后会指向用户局域网内任意主机）：\n"
        + "\n".join(hits)
    )


def test_no_personal_abs_path():
    hits = _scan(_PERSONAL_PATH)
    assert not hits, (
        "产品代码里出现个人绝对路径（在别人机器上必然失败）：\n" + "\n".join(hits)
    )


def test_rag_store_embed_defaults_to_localhost():
    """RAG 嵌入端点默认必须是本机，且可通过环境变量覆盖。"""
    txt = (SRC / "agent_harness" / "core" / "tools" / "rag_store.py").read_text(
        encoding="utf-8"
    )
    assert "HARNESS_OLLAMA_API" in txt, "嵌入端点应可通过 HARNESS_OLLAMA_API 覆盖"
    assert "127.0.0.1:11434/api/embed" in txt, "默认嵌入端点应为 127.0.0.1"


def test_auto_setup_candidates_are_local():
    """Ollama 探测候选必须首选项为本机。"""
    txt = (SRC / "agent_harness" / "auto_setup.py").read_text(encoding="utf-8")
    assert '"http://127.0.0.1:11434"' in txt
    assert "172.18.9.126" not in txt
