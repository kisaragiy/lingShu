#!/usr/bin/env bash
# init.sh — 灵枢长任务 harness 的「怎么启动 + 怎么冒烟」固化脚本
# 移植自 Hermes WP5 harness（2026-10-14），并修 D3（默认 harness 路径）/D4（缺 PROGRESS.json 应 ALERT）/D5（用项目 venv）。
# 契约: 0=READY / 2=ALERT（能起但冒烟失败，或依赖缺失）/ 1=ERROR（脚本自身故障）
set -uo pipefail

winpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }

PROJ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS="${HARNESS_PY:-$PROJ_DIR/tools/harness/harness.py}"   # D3: 指向本仓 tools/harness/
PROJ_W="$(winpath "$PROJ_DIR")"; HARNESS_W="$(winpath "$HARNESS")"
cd "$PROJ_DIR" || { echo "[init] ERROR: 进不去 $PROJ_DIR" >&2; exit 1; }

# D5: 优先项目 venv，其次 $PYTHON，最后 PATH 上的 python
if [ -n "${PYTHON:-}" ]; then PY="$PYTHON"
elif [ -x "$PROJ_DIR/.venv/Scripts/python.exe" ]; then PY="$PROJ_DIR/.venv/Scripts/python.exe"
else PY="python"; fi

echo "[init] project dir: $PROJ_W"
if ! command -v "$PY" >/dev/null 2>&1 && [ ! -x "$PY" ]; then
  echo "[init] ERROR: 找不到 python ($PY)" >&2; exit 1
fi
echo "[init] python: $("$PY" --version 2>&1)"

# D4: 缺 PROGRESS.json = 依赖缺失 → ALERT（原版会静默 READY，掩盖问题）
if [ ! -f PROGRESS.json ]; then
  echo "[init] ALERT: 缺 PROGRESS.json（先 harness.py init，或从 PROGRESS.example.json 种子）" >&2
  exit 2
fi

# 冒烟：从 PROGRESS.json 的 smoke_cmd 读
SMOKE_CMD="$("$PY" -c 'import json;print(json.load(open("PROGRESS.json",encoding="utf-8")).get("smoke_cmd",""))' 2>/dev/null)"
if [ -n "${SMOKE_CMD:-}" ]; then
  echo "[init] smoke: $SMOKE_CMD"
  if ! eval "$SMOKE_CMD"; then
    echo "[init] ALERT: 冒烟失败 —— 先修环境再开工" >&2; exit 2
  fi
fi

# 报告下一个 feature（新会话接手第 2 步）
if [ -f "$HARNESS" ]; then "$PY" "$HARNESS_W" --dir "$PROJ_W" status || true; fi

echo "[init] READY"
exit 0
