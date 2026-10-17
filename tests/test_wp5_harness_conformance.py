"""WP5 长任务 harness（tools/harness/harness.py）契约 + 不变量回归测试。

为什么需要这个文件
------------------
2026-10-14 把 WP5 harness 移植进本仓（commit b5d209b）并接进
`.pre-commit-config.yaml` 的 local hook `harness-check`，但**没有任何测试**
覆盖它：`grep -rl 'tools/harness\\|PROGRESS.json' tests/` 为空。
于是「没证据不许标完成」这条唯一防线靠人肉守——2026-10-16 复核发现
`PLACEHOLDER` 里全是 <8 字符，会被长度门抢先生效，长糊弄话（"no evidence"、
"see logs"、"全绿"）曾是**合法证据**。

本文件把 harness 的四件契约钉死（对应 scripts/lib/contract.py）：
  ① 退出码语义：0=OK · 2=ALERT（请求被拒/不变量被破坏）· 1=ERROR（工具自身故障）
  ② watchdog 契约：`check` 干净时 stdout **完全为空**
  ③ 证据门：pass 无证据 / 太短 / 占位符 / 糊弄话 → 拒绝且**不落盘**
  ④ 不变量：passes=true ⇒ evidence 非空；id 唯一合法；desc 非空

`HARNESS_PY` 环境变量可指向另一份 harness.py（用于验证移植副本之间的
行为一致，例如宿主环境侧 `$LINGSHU_HARNESS_LIB/harness/harness.py`）。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HARNESS = Path(os.environ.get("HARNESS_PY") or (REPO / "tools" / "harness" / "harness.py"))
PILOT = REPO / "PROGRESS.json"       # 灵枢根的真实载体（由 TASK-PROGRESS.md 种子）

OK, ERROR, ALERT = 0, 1, 2


def run(*argv: str, cwd: Path) -> subprocess.CompletedProcess:
    """在 cwd 下跑 harness.py，捕获 stdout/stderr（UTF-8）。"""
    return subprocess.run(
        [sys.executable, str(HARNESS), "--dir", str(cwd), *argv],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )


def write_progress(d: Path, **over: object) -> Path:
    data = {
        "schema_version": 1, "project": "probe", "smoke_cmd": "",
        "features": [
            {"id": "f1", "desc": "做 A", "passes": False, "evidence": None,
             "verified_at": None, "notes": ""},
            {"id": "f2", "desc": "做 B", "passes": False, "evidence": None,
             "verified_at": None, "notes": ""},
        ],
    }
    data.update(over)
    p = d / "PROGRESS.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


@pytest.fixture()
def proj(tmp_path: Path) -> Path:
    write_progress(tmp_path)
    return tmp_path


def load(d: Path) -> dict:
    return json.loads((d / "PROGRESS.json").read_text(encoding="utf-8"))


# ═══════════════ ① init ═══════════════

def test_init_creates_carrier(tmp_path: Path) -> None:
    r = run("init", "--project", "demo", "--feature", "f1:做 A", cwd=tmp_path)
    assert r.returncode == OK, r.stderr
    data = load(tmp_path)
    assert data["project"] == "demo" and data["schema_version"] == 1
    assert [f["id"] for f in data["features"]] == ["f1"]
    assert data["features"][0]["passes"] is False


def test_init_refuses_overwrite_without_force(proj: Path) -> None:
    before = (proj / "PROGRESS.json").read_text(encoding="utf-8")
    r = run("init", "--project", "demo", cwd=proj)
    assert r.returncode == ALERT and "--force" in r.stdout
    assert (proj / "PROGRESS.json").read_text(encoding="utf-8") == before
    assert run("init", "--project", "demo", "--force", cwd=proj).returncode == OK


def test_init_rejects_illegal_feature_id(tmp_path: Path) -> None:
    r = run("init", "--project", "demo", "--feature", "bad id!:做 A", cwd=tmp_path)
    assert r.returncode == ALERT and "非法" in r.stdout
    assert not (tmp_path / "PROGRESS.json").exists()


# ═══════════════ ② status / next ═══════════════

def test_status_json_is_single_line_and_reports_next(proj: Path) -> None:
    r = run("status", "--json", cwd=proj)
    assert r.returncode == OK
    assert r.stdout.count("\n") == 1, "report 型 --json 必须是单行"
    payload = json.loads(r.stdout)
    assert (payload["total"], payload["done"], payload["pending"]) == (2, 0, 2)
    assert payload["next"]["id"] == "f1"


def test_next_follows_array_order(proj: Path) -> None:
    assert run("next", cwd=proj).stdout.startswith("f1")
    assert run("pass", "f1", "--evidence", "pytest -q => 2 passed", cwd=proj).returncode == OK
    assert run("next", cwd=proj).stdout.startswith("f2")


def test_next_reports_all_done(proj: Path) -> None:
    for fid in ("f1", "f2"):
        run("pass", fid, "--evidence", f"python t.py {fid} => exit 0", cwd=proj)
    assert "全部 feature 已完成" in run("next", cwd=proj).stdout


# ═══════════════ ③ 证据门（核心防线）═══════════════

def test_pass_without_evidence_rejected_and_not_persisted(proj: Path) -> None:
    r = run("pass", "f1", cwd=proj)
    assert r.returncode == ALERT and "必须带 --evidence" in r.stdout
    assert load(proj)["features"][0]["passes"] is False


def test_pass_short_evidence_rejected(proj: Path) -> None:
    r = run("pass", "f1", "--evidence", "abc", cwd=proj)
    assert r.returncode == ALERT and "太短" in r.stdout
    assert load(proj)["features"][0]["passes"] is False


@pytest.mark.parametrize("bad", ["done", "n/a", "TODO"])
def test_pass_placeholder_rejected(proj: Path, bad: str) -> None:
    """占位符分支必须**可达**（2026-10-16 前它是长度门后的死代码）。"""
    r = run("pass", "f1", "--evidence", bad, cwd=proj)
    assert r.returncode == ALERT and "占位符" in r.stdout
    assert load(proj)["features"][0]["passes"] is False


@pytest.mark.parametrize("bad", [
    "no evidence", "see logs", "verified", "already verified",
    "tests pass", "Tests Pass", "  全绿  ", "已完成", "没问题",
])
def test_pass_fake_evidence_rejected(proj: Path, bad: str) -> None:
    """长度达标的糊弄话也必须拒（这条回归曾漏：它们曾是合法证据）。"""
    r = run("pass", "f1", "--evidence", bad, cwd=proj)
    assert r.returncode == ALERT and "占位符" in r.stdout, r.stdout
    assert load(proj)["features"][0]["passes"] is False


def test_valid_evidence_marks_pass(proj: Path) -> None:
    ev = "pytest tests/test_wp5_harness_conformance.py -q => 20 passed (exit 0)"
    r = run("pass", "f1", "--evidence", ev, "--note", "kp", cwd=proj)
    assert r.returncode == OK, r.stderr
    f1 = load(proj)["features"][0]
    assert f1["passes"] is True and f1["evidence"] == ev
    assert f1["notes"] == "kp" and f1["verified_at"]
    assert "下一步：f2" in r.stdout


def test_double_pass_rejected(proj: Path) -> None:
    run("pass", "f1", "--evidence", "python t.py => 42 (exit 0)", cwd=proj)
    r = run("pass", "f1", "--evidence", "python t.py => 42 (exit 0)", cwd=proj)
    assert r.returncode == ALERT and "已经 passes=true" in r.stdout


def test_unpass_reopens_and_clears_evidence(proj: Path) -> None:
    run("pass", "f1", "--evidence", "python t.py => 42 (exit 0)", cwd=proj)
    assert run("unpass", "f1", cwd=proj).returncode == OK
    f1 = load(proj)["features"][0]
    assert f1["passes"] is False and f1["evidence"] is None and f1["verified_at"] is None
    r = run("unpass", "nope", cwd=proj)
    assert r.returncode == ALERT and "找不到" in r.stdout


def test_add_rejects_duplicate_and_illegal_id(proj: Path) -> None:
    assert run("add", "f1", "--desc", "重复", cwd=proj).returncode == ALERT
    assert run("add", "bad id", "--desc", "非法", cwd=proj).returncode == ALERT
    assert run("add", "f3", "--desc", "做 C", cwd=proj).returncode == OK
    assert [f["id"] for f in load(proj)["features"]] == ["f1", "f2", "f3"]


# ═══════════════ ④ check（watchdog）与不变量 ═══════════════

def test_check_is_silent_when_clean(proj: Path) -> None:
    r = run("check", cwd=proj)
    assert r.returncode == OK
    assert r.stdout == "", "watchdog 契约：干净时 stdout 必须为空"
    j = run("check", "--json", cwd=proj)
    assert j.returncode == OK and json.loads(j.stdout)["status"] == "ok"


def test_check_alerts_on_passes_without_evidence(tmp_path: Path) -> None:
    d = tmp_path / "sabotaged"
    d.mkdir()
    write_progress(d, features=[{"id": "f1", "desc": "做 A", "passes": True,
                                 "evidence": "   ", "verified_at": None, "notes": ""}])
    r = run("check", cwd=d)
    assert r.returncode == ALERT and "没有 evidence" in r.stdout
    j = run("check", "--json", cwd=d)
    assert j.returncode == ALERT and json.loads(j.stdout)["status"] == "alert"


def test_check_alerts_on_duplicate_id_and_empty_desc(tmp_path: Path) -> None:
    d = tmp_path / "dup"
    d.mkdir()
    write_progress(d, features=[{"id": "f1", "desc": "做 A", "passes": False},
                                {"id": "f1", "desc": "  ", "passes": False}])
    r = run("check", cwd=d)
    assert r.returncode == ALERT
    assert "重复" in r.stdout and "desc 为空" in r.stdout


def test_check_alerts_when_carrier_missing(tmp_path: Path) -> None:
    r = run("check", cwd=tmp_path)
    assert r.returncode == ALERT and "未找到" in r.stdout


def test_unexpected_structure_is_error_not_alert(tmp_path: Path) -> None:
    """契约：脚本自身故障 = 1，必须与「发现问题」= 2 可区分，且不静默。"""
    (tmp_path / "PROGRESS.json").write_text(
        json.dumps({"schema_version": 1, "features": {"f1": "不是数组"}}),
        encoding="utf-8")
    r = run("status", cwd=tmp_path)
    assert r.returncode == ERROR
    assert "[ERROR]" in r.stderr and r.stdout == ""


# ═══════════════ ⑤ 真实载体接线（灵枢 pilot）═══════════════

@pytest.mark.skipif(not PILOT.exists(), reason="本仓尚未 init PROGRESS.json")
def test_repo_pilot_carrier_satisfies_invariants() -> None:
    """灵枢根 PROGRESS.json 是 WP5 的**真实**载体（TASK-PROGRESS.md 种子）。"""
    r = run("check", cwd=REPO)
    assert r.returncode == OK, r.stdout
    assert r.stdout == ""
    payload = json.loads(run("status", "--json", cwd=REPO).stdout)
    assert payload["project"] and payload["total"] >= 1
    assert payload["done"] + payload["pending"] == payload["total"]
