"""P2 对话式 Skill 生成器测试"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

def test_skill_generator_module_imports():
    from agent_harness.core.tools.skill_generator import generate_skill
    assert callable(generate_skill)

def test_skill_generator_empty_input():
    from agent_harness.core.tools.skill_generator import generate_skill
    result = generate_skill("")
    assert not result.get("ok")
    assert "为空" in result.get("error", "")
