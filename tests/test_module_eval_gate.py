"""LWP18 — core 模块评测**门禁**（防探针退化 + 脚本返回值契约）。

与 ``tests/test_module_eval.py``（验证 module_eval 自身判据）互补，本文件守的是
「探针被接成门禁之后不能悄悄失效」：

  ① **防退化（核心）**：把**任意单个**模块换成恒错的实现，评测都必须区分好坏。
     若探针退化成「好坏都满分」（例如为了绿而放宽断言、或适配器被换成了 return
     原函数），这条立刻红。
  ② **基线**：真实 bundle 全模块 score == 1.0（门禁的 OK 基线）。
  ③ **差分**：坏 bundle 的 score < 1.0 且未被判 passed。
  ④ **返回值契约（D33）**：``scripts/eval_modules.py`` 用 subprocess 实跑一次，
     断言退出码 0、打印各模块 score、当日 json 落盘且含四模块全量结果；
     同时用 ``exit_code_for`` 覆盖 ALERT=2 分支（不靠改文件造坏实现）。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from agent_harness.core import module_eval as me  # noqa: E402

SCRIPT = REPO / "scripts" / "eval_modules.py"


# ── 坏实现：按探针期望**反向**构造，每个都恒错 ───────────────────────────


class AlwaysFailRetry:
    """wrap 竟恒抛异常（连调用者都没机会跑）—— 三条重试探针全灭。"""

    def wrap(self, fn, *, max_attempts: int, base_delay: float = 0.0):
        def broken(*a, **kw):
            raise ConnectionError("retry module is a stub")

        return broken


class StubDegradation:
    """永远返回 None：首选/回退/兜底全丢，全失败也拿不到降级结果。"""

    def run(self, capability, *, preferred_fn=None, fallback_fns=None,
            on_failure=None, **kwargs):
        return None


class FlatBudget:
    """永远 ok、永远可继续 —— 预算永不升级（warn/ask/stop 三档失效）。"""

    def new_budget(self, soft_limit: int):
        return {"soft_limit": soft_limit, "used": 0}

    def add(self, budget, tokens: int) -> str:
        budget["used"] += tokens
        return "ok"

    def can_continue(self, budget) -> bool:
        return True


class AlwaysTrippedBreaker:
    """恒判熔断：健康流也 trips → 反向失败（正常流不误触这条挂）。"""

    def new_breaker(self, max_tokens: int = 100_000, max_no_progress: int = 5):
        return {"max_tokens": max_tokens}

    def add_tokens(self, breaker, n: int) -> None:
        breaker["tokens"] = breaker.get("tokens", 0) + n

    def record_output(self, breaker, output: str) -> None:
        pass

    def tripped(self, breaker) -> bool:
        return True


BAD_IMPLS = {
    "retry": AlwaysFailRetry(),
    "degradation": StubDegradation(),
    "budget": FlatBudget(),
    "circuit_breaker": AlwaysTrippedBreaker(),
}


def bundle(**overrides):
    """真实 bundle 上**只换一个**模块（其余保持真实实现）。"""
    return me.LingShuModules(**overrides)


def bad_bundle_all():
    return bundle(**BAD_IMPLS)


# ── ① 防退化（核心断言）───────────────────────────────────────────


def test_gate_distinguishes_good_from_bad_bundle():
    good = me.evaluate_all(me.LingShuModules())
    bad = me.evaluate_all(bad_bundle_all())
    assert me.distinguishes(good, bad) is True, (
        f"探针退化了：好={good.score} 坏={bad.score}；"
        f"坏实现未失败探针={[m.failed_probes for m in bad.modules]}"
    )


@pytest.mark.parametrize("module_name", sorted(BAD_IMPLS))
def test_gate_distinguishes_even_if_single_module_is_bad(module_name):
    """只坏一个模块也必须被抓住（探针按角色独立生效，不靠别的模块背锅）。"""
    good = me.evaluate_all(me.LingShuModules())
    bad = me.evaluate_all(bundle(**{module_name: BAD_IMPLS[module_name]}))
    assert me.distinguishes(good, bad) is True
    assert bad.module(module_name).score < 1.0
    assert bad.module(module_name).failed_probes
    # 其余三个真实模块仍然满分 —— 失败定位是**角色级**的，不是一锅端
    for other in BAD_IMPLS:
        if other != module_name:
            assert bad.module(other).score == 1.0, (module_name, other)


# ── ② 基线：真实 bundle 全满分 ───────────────────────────────────


def test_good_bundle_scores_one():
    report = me.evaluate_all(me.LingShuModules())
    assert report.score == 1.0
    assert report.passed is True
    for m in report.modules:
        assert m.score == 1.0, (m.module, m.failed_probes)


# ── ③ 差分：坏 bundle 必须掉分 ───────────────────────────────────


def test_bad_bundle_scores_below_one():
    bad = me.evaluate_all(bad_bundle_all())
    assert bad.score < 1.0
    assert bad.passed is False
    assert any(m.failed_probes for m in bad.modules)


# ── ④ 返回值契约（D33）：0=OK / 2=ALERT / 1=ERROR ─────────────────


def _load_script():
    """按路径加载 scripts/eval_modules.py（scripts/ 不是包，用 importlib 直取）。"""
    spec = importlib.util.spec_from_file_location("eval_modules_script", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_exit_code_contract_mapping():
    """纯函数映射：全 1.0 → 0；有模块掉分 → 2（**不是** 1）。"""
    script = _load_script()
    assert script.EXIT_OK == 0 and script.EXIT_ERROR == 1 and script.EXIT_ALERT == 2
    assert script.exit_code_for(me.evaluate_all(me.LingShuModules())) == 0
    bad = me.evaluate_all(bad_bundle_all())
    assert script.exit_code_for(bad) == script.EXIT_ALERT == 2


def test_script_runs_green_and_writes_daily_json():
    """端到端：真跑一次脚本 → 退出码 0 + 打印模块 score + 当日 json 落盘。"""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(REPO), capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"stdout=\n{proc.stdout}\nstderr=\n{proc.stderr}"
    for name in BAD_IMPLS:
        assert name in proc.stdout, proc.stdout
    assert "score=1.00" in proc.stdout

    path = REPO / "eval_results" / f"modules-{datetime.now():%Y%m%d}.json"
    assert path.exists(), f"未落盘: {path}"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["score"] == 1.0
    assert len(payload["modules"]) == len(me.PROBE_REGISTRY)
    assert {m["module"] for m in payload["modules"]} == set(me.PROBE_REGISTRY)
