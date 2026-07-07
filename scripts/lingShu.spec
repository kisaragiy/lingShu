# -*- mode: python ; coding: utf-8 -*-

"""PyInstaller build spec for 灵枢 (LingShu Agent).

Build:
    pyinstaller lingShu.spec --clean

Note: run from the project root directory (where pyproject.toml lives).
__file__ is NOT available in PyInstaller spec context — paths use CWD.
"""

import os
import sys
from pathlib import Path

# ─── Root is CWD (must run pyinstaller from project root) ───
ROOT = Path.cwd()
SRC = ROOT / "src"
STATIC = SRC / "agent_harness" / "static"

assert (SRC / "agent_harness").is_dir(), "Run pyinstaller from project root (where pyproject.toml lives)"

# ─── Collect all hidden imports ───
HIDDEN_IMPORTS = [
    # Agent Harness
    "agent_harness",
    "agent_harness.api_fastapi",
    "agent_harness.graph_multi",
    "agent_harness.graph",
    "agent_harness.mcp_server",
    "agent_harness.config",
    "agent_harness.pipeline.state",
    "agent_harness.pipeline.llm",
    "agent_harness.pipeline.circuit_breaker",
    "agent_harness.pipeline.tracing",
    "agent_harness.pipeline.session_store",
    "agent_harness.pipeline.config_manager",
    "agent_harness.pipeline.cancel",
    "agent_harness.agents.supervisor",
    "agent_harness.agents.workers",
    "agent_harness.tools",
    "agent_harness.tools.registry",
    "agent_harness.tools.misc",
    "agent_harness.tools.web",
    "agent_harness.tools.desktop",
    "agent_harness.tools.comfyui",
    "agent_harness.tools.rag_store",
    # LangGraph
    "langgraph.graph",
    "langgraph.graph.state",
    # FastAPI / Uvicorn
    "fastapi",
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    # ChromaDB
    "chromadb",
    # NumPy
    "numpy",
    "numpy.core._multiarray_umath",
    # Network
    "requests",
    "urllib3",
    # Pydantic
    "pydantic",
    # YAML
    "yaml",
    # pywebview (native window)
    "webview",
    "webview.platforms",
    "webview.platforms.win32_edge",
    "webview.platforms.cef",
    "clr_loader",
    "clr_loader.netfx",
    "clr_loader.netcore",
]

# ─── Data files (static HTML/CSS/JS) ───
DATA_FILES = []
if STATIC.exists():
    for f in STATIC.rglob("*"):
        if f.is_file():
            rel = f.relative_to(SRC / "agent_harness")
            dest = str(rel.parent).replace("\\", "/")
            DATA_FILES.append((str(f), "agent_harness/" + dest))

# ─── Exclusions ───
EXCLUDES = [
    "tkinter", "matplotlib", "PyQt5", "PySide2",
    "notebook", "jupyter", "test", "tests", "setuptools", "pip",
    "cairosvg", "zmq", "ipykernel", "ipython", "jedi",
    # Heavy ML libs not used by agent-harness
    "torch", "torchvision", "torchaudio",
    "tensorflow", "keras",
    "bitsandbytes", "onnxruntime",
    "transformers", "tokenizers",
    "datasets", "accelerate",
    "scipy", "scipy.special", "scipy.linalg",
    "sklearn", "sklearn.metrics",
    "pandas", "pandas.io",
    "pyarrow", "openpyxl",
    "sounddevice",
    "discord",
    "av",
    "opentelemetry",
    "boto3", "botocore",
    "cryptography",
    "nacl",
    "markdown",
    "lxml",
]

a = Analysis(
    [str(ROOT / "scripts" / "lingShu_launcher.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=DATA_FILES,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="lingShu",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "scripts" / "icon.ico") if (ROOT / "scripts" / "icon.ico").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="lingShu",
)
