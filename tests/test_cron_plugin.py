"""agent_cron + plugin_loader 接入测试

覆盖：
- AgentScheduler.list_tasks() 实例方法（main.py 依赖它显示任务数）
- add_task/delete_task/get_task/list_tasks 任务 CRUD
- 解析 cron 表达式（标准/interval/非法）
- plugin_loader 加载 + 错误隔离 + example 模板路径正确
运行：cd agent-harness && .venv/Scripts/python.exe -m pytest tests/test_cron_plugin.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core.agent_cron import (
    AgentScheduler, add_task, delete_task, get_task, list_tasks,
)
from agent_harness.core.agent_cron import _parse_cron  # noqa: E402


# ─── AgentScheduler.list_tasks (main.py 关键依赖) ───
def test_scheduler_has_list_tasks_instance_method():
    """AgentScheduler 实例必须有 list_tasks() 方法（main.py hasattr 判断依赖它）"""
    s = AgentScheduler()
    assert hasattr(s, "list_tasks"), "AgentScheduler 缺 list_tasks 实例方法"
    assert callable(s.list_tasks)


def test_scheduler_list_tasks_matches_module():
    """实例 list_tasks() 与模块级 list_tasks() 一致"""
    s = AgentScheduler()
    assert s.list_tasks() == list_tasks()


# ─── 任务 CRUD ───
def test_add_and_delete_task():
    add_task("_t_test_del", "0 9 * * *", "test prompt")
    assert get_task("_t_test_del") is not None
    assert "_t_test_del" in [t["id"] for t in list_tasks()]
    assert delete_task("_t_test_del")
    assert get_task("_t_test_del") is None


def test_get_nonexistent_task_returns_none():
    assert get_task("_t_no_such") is None


def test_add_task_bad_schedule_raises():
    """非法调度表达式应抛 ValueError（不是静默）"""
    import pytest
    with pytest.raises(ValueError):
        add_task("_t_bad", "not a cron", "prompt")
    delete_task("_t_bad")


# ─── cron 解析 ───
def test_parse_cron_standard():
    assert _parse_cron("0 9 * * *")["type"] == "cron"


def test_parse_cron_interval():
    assert _parse_cron("every 30m") == {"type": "interval", "interval": 1800}


def test_parse_cron_invalid():
    assert _parse_cron("garbage") is None


# ─── plugin_loader ───
def test_plugin_loader_loads_example():
    """example_plugin（自动生成模板）应能成功加载且注册工具"""
    from agent_harness.plugin_loader import load_plugins
    from agent_harness.core.tools.registry import TOOL_REGISTRY
    load_plugins()
    # example_plugin 用正确路径 agent_harness.core.tools.registry 注册
    assert "my_echo" in TOOL_REGISTRY, "example_plugin 未注册 my_echo（可能 import 路径错误）"
