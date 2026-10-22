"""报告索引的原子性与损坏恢复。

原状（三处真缺陷）：
1. `_save_index` 直接 `open(INDEX_FILE, "w")` 截断写 —— 崩溃/断电留下半截 JSON；
2. `_load_index` 解析失败 `return []` 静默吞 —— **紧接着一次保存就把空列表写回，
   历史索引永久消失**（.md 还在，但列表里全没了，且无任何痕迹）；
3. 只有进程内 RLock —— server + CLI 同时写会互相覆盖（后写者赢）。

注意第 2 点是复利型缺陷：单次看只是"读到空"，实际会把数据变成永久丢失。
"""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parent.parent / "src")


@pytest.fixture()
def store(tmp_path, monkeypatch):
    from agent_harness.apps.research.pipeline import report_store

    monkeypatch.setattr(report_store, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(report_store, "INDEX_FILE", tmp_path / "index.json")
    return report_store


# ─── 1. 损坏索引：隔离而不是静默清零 ───

def test_corrupt_index_is_quarantined_not_erased(store, tmp_path, capsys):
    (tmp_path / "index.json").write_text('{"broken": ', encoding="utf-8")
    # 报告文件本身是好的，只有索引坏了
    (tmp_path / "rpt_1_a.md").write_text("---\ntitle: a\n---\nbody", encoding="utf-8")

    assert store._load_index() == []

    corrupt = list(tmp_path.glob("index.json.corrupt-*"))
    assert corrupt, "坏索引必须被隔离留证，不能直接覆盖掉"
    assert "损坏" in capsys.readouterr().err, "静默吞掉损坏 = 用户永远不知道索引出过事"
    assert (tmp_path / "rpt_1_a.md").exists(), "报告正文不能被牵连"


def test_corrupt_index_does_not_destroy_history_on_next_save(store, tmp_path):
    """复利缺陷回归：读完坏索引再保存一次，必须还能找回隔离文件里的历史。"""
    history = [{"id": "rpt_old", "title": "旧报告", "filename": "rpt_old.md", "owner_id": ""}]
    # 模拟"写到一半崩了"：前 20 字节是好 JSON 的开头，后面截断
    (tmp_path / "index.json").write_text(json.dumps(history)[:20], encoding="utf-8")

    store.save_report("新报告", "body")
    corrupt = list(tmp_path.glob("index.json.corrupt-*"))
    assert corrupt, "历史索引必须留有副本，否则永久丢失"

    assert len(store.list_reports()) == 1, "新索引只含新报告（旧索引已隔离）"


def test_non_list_index_is_quarantined(store, tmp_path):
    (tmp_path / "index.json").write_text('{"a": 1}', encoding="utf-8")
    assert store._load_index() == []
    assert list(tmp_path.glob("index.json.corrupt-*"))


# ─── 2. 原子写：写到一半崩掉不能破坏旧索引 ───

def test_save_index_failure_keeps_old_index_intact(store, tmp_path, monkeypatch):
    good = [{"id": "rpt_keep", "title": "保留", "filename": "rpt_keep.md", "owner_id": ""}]
    store._save_index(good)
    before = (tmp_path / "index.json").read_text(encoding="utf-8")

    def _boom(obj, f, **kw):
        f.write('[{"id": "half')      # 先写半截，再炸
        raise OSError("磁盘满了")

    monkeypatch.setattr(store.json, "dump", _boom)
    with pytest.raises(OSError):
        store._save_index([{"id": "rpt_new"}])

    assert (tmp_path / "index.json").read_text(encoding="utf-8") == before, \
        "失败时旧索引必须原封不动（这才是 tmp+replace 的意义）"
    assert not list(tmp_path.glob("*.tmp")), "临时文件不能残留"


def test_save_index_leaves_no_tmp_on_success(store, tmp_path):
    store._save_index([{"id": "rpt_x"}])
    assert not list(tmp_path.glob("*.tmp"))
    assert json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))[0]["id"] == "rpt_x"


# ─── 3. 多线程：进程内锁 ───

def test_concurrent_threads_all_recorded(store):
    def worker(n):
        for i in range(5):
            store.save_report(f"并发-{n}-{i}", "body")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    reports = store.list_reports(limit=100)
    assert len(reports) == 20, f"并发写丢索引：{len(reports)}/20"


# ─── 4. 跨进程：文件锁（RLock 挡不住的部分）───

_CHILD = """
import sys
sys.path.insert(0, {src!r})
from agent_harness.apps.research.pipeline import report_store
for i in range(5):
    report_store.save_report("进程{n}-{{i}}".format(i=i), "body")
"""


def test_concurrent_processes_all_recorded(tmp_path):
    env = {**os.environ, "HARNESS_REPORTS_DIR": str(tmp_path), "PYTHONPATH": SRC}
    procs = [
        subprocess.Popen([sys.executable, "-c", _CHILD.format(src=SRC, n=n)], env=env)
        for n in range(3)
    ]
    for p in procs:
        assert p.wait(timeout=120) == 0

    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert len(index) == 15, f"跨进程写丢索引：{len(index)}/15（文件锁没起作用）"
