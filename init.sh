#!/usr/bin/env bash
# init.sh — 灵枢长任务 harness 的「怎么启动 + 怎么冒烟」固化脚本
# 长任务 harness 初始化：校验 PROGRESS.json、定位项目 venv、确认默认路径。
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
  # 冒烟与状态解析必须用同一解释器，避免 PATH 上的 python 缺少项目依赖。
  if [[ "$SMOKE_CMD" == "python "* ]]; then
    SMOKE_CMD='"$PY" '"${SMOKE_CMD#python }"
  fi
  if ! eval "$SMOKE_CMD"; then
    echo "[init] ALERT: 冒烟失败 —— 先修环境再开工" >&2; exit 2
  fi
fi

# 报告下一个 feature（新会话接手第 2 步）
if [ -f "$HARNESS" ]; then "$PY" "$HARNESS_W" --dir "$PROJ_W" status || true; fi

# 缺失或无法解释的时间不能证明状态仍可信，必须与过期状态一样阻止接班。
if "$PY" - <<'PY'
import json
import sys
from datetime import datetime, timedelta, timezone

try:
    with open("PROGRESS.json", encoding="utf-8") as stream:
        updated = datetime.fromisoformat(json.load(stream)["updated"])
    if updated.tzinfo is None:
        raise ValueError("updated 必须带时区")
    if datetime.now(timezone.utc) - updated > timedelta(days=7):
        print("[init] ALERT: PROGRESS.json 超过 7 天未更新", file=sys.stderr)
        sys.exit(2)
except (OSError, ValueError, KeyError, TypeError) as exc:
    print(f"[init] ALERT: PROGRESS.json updated 缺失或无法解析（{exc}）", file=sys.stderr)
    sys.exit(2)
PY
then
  echo "[init] READY"
  exit 0
else
  exit 2
fi
