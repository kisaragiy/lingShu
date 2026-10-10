"""打包 / 配置链路门禁（对应 2026-09-10 评审的 P0-2/P0-3/P0-4）。

这三条缺陷的共同点：**只在打包分发后才暴露**，开发机上跑得好好的。

- P0-2 `console=True` → 用户双击第一眼是 cmd 黑框，关掉=杀进程；
  而 launcher 的失败路径用 `input()` 等待 —— 无控制台时抛 EOFError 崩掉。
- P0-3 `auto_setup` 只挂在 CLI 入口（main.py），打包版入口是 launcher
  → 「无 key 也能用本地模型」在实现上不存在。
- P0-4 `llm.py` 从 `..config` import 快照常量 → 用户在设置页填的
  api_url/model 永远不生效（config.json 被写了但没人读）。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
LAUNCHER = ROOT / "scripts" / "lingShu_launcher.py"
SPEC = ROOT / "scripts" / "lingShu.spec"


def test_packaged_build_is_gui_no_console():
    """打包必须是无控制台的 GUI 模式。"""
    spec = SPEC.read_text(encoding="utf-8")
    assert "console=False" in spec, "打包 spec 必须是 console=False（否则用户看到黑框）"
    assert "console=True" not in spec


def test_launcher_survives_without_console():
    """无控制台时不得裸调 input()：失败路径要走弹窗。"""
    txt = LAUNCHER.read_text(encoding="utf-8")
    assert "def _fail(" in txt, "launcher 需要弹窗式失败提示"
    assert "def _pause(" in txt, "launcher 需要无控制台安全的等待函数"
    naked = [
        f"{i}: {l.strip()}"
        for i, l in enumerate(txt.splitlines(), 1)
        if "input(" in l and "def _pause" not in l and "input(msg)" not in l
    ]
    assert not naked, "存在无控制台会崩溃的裸 input() 调用：\n" + "\n".join(naked)


def test_launcher_wires_auto_setup():
    """打包入口必须调用 auto_setup（否则「无 key 用本地模型」不存在）。"""
    txt = LAUNCHER.read_text(encoding="utf-8")
    assert "_ensure_llm_configured" in txt
    assert "auto_setup" in txt


def test_auto_setup_persists_to_config_json():
    """auto_setup 必须落 config.json —— 环境变量对已 import 的模块是快照。"""
    txt = (SRC / "agent_harness" / "auto_setup.py").read_text(encoding="utf-8")
    assert "save_config" in txt, "auto_setup 未持久化配置，LLM 调用读不到"


def test_llm_reads_runtime_config():
    """llm.py 必须走运行时配置读取，而不是 import 快照。"""
    txt = (SRC / "agent_harness" / "core" / "pipeline" / "llm.py").read_text(encoding="utf-8")
    assert "get_llm_config" in txt, "llm.py 仍在用 import 快照，设置页配置不生效"
    assert '_cfg.get("api_url")' in txt and '_cfg.get("model")' in txt
