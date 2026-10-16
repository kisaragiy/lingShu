"""
安全护栏模块 — WorkBuddy 权限双层架构移植（P0）

├── mode.py    会话安全模式: default(沙箱优先) / full(全权+全量审计)
├── risk.py    危险操作确认矩阵: 按参数分类检测 + 待确认队列
└── backup.py  写前自动备份 + 删除保护(回收站)

对外统一入口:
    from agent_harness.core.safety import get_mode, set_mode, is_full_access
    from agent_harness.core.safety import check_operation, confirm_operation, pending_operations
    from agent_harness.core.safety import backup_before_write, safe_delete
"""
from .mode import get_mode, set_mode, is_full_access, MODE_DEFAULT, MODE_FULL
from .risk import (
    check_operation,
    confirm_operation,
    pending_operations,
)
from .backup import backup_before_write, safe_delete

# ── P0-6：外源不可信标记 + 记忆写入准入 ──
from .provenance import (
    TRUSTED,
    UNTRUSTED,
    UNKNOWN,
    EXTERNAL_SOURCE_TOOLS,
    Provenance,
    TaggedContent,
    mark_untrusted,
    mark_trusted,
    is_tagged,
    trust_of,
    provenance_of,
    unwrap,
    is_external_source,
    classify_tool_result,
    tag_tool_result,
)
from .admission import (
    Policy,
    DEFAULT_POLICY,
    DEFAULT_MEMORY_SINKS,
    Decision,
    Released,
    admit,
    guard_write,
    release,
    make_memory_admission_hook,
    install_memory_admission,
    uninstall_memory_admission,
)

# ── 溯源验证 agent：claim ↔ source 一致性判定（生成/评判分离） ──
from .claim_verifier import (
    SUPPORTED,
    PARTIAL,
    UNSUPPORTED,
    Evidence,
    Verdict,
    RuleBasedJudge,
    LLMJudge,
    extract_citations,
    verify_claims,
    verify_one,
    sources_from_citation_map,
    rule_judge_spec,
)

__all__ = [
    "get_mode", "set_mode", "is_full_access", "MODE_DEFAULT", "MODE_FULL",
    "check_operation", "confirm_operation", "pending_operations",
    "backup_before_write", "safe_delete",
    # provenance
    "TRUSTED", "UNTRUSTED", "UNKNOWN", "EXTERNAL_SOURCE_TOOLS",
    "Provenance", "TaggedContent",
    "mark_untrusted", "mark_trusted", "is_tagged", "trust_of", "provenance_of",
    "unwrap", "is_external_source", "classify_tool_result", "tag_tool_result",
    # admission
    "Policy", "DEFAULT_POLICY", "DEFAULT_MEMORY_SINKS", "Decision", "Released",
    "admit", "guard_write", "release",
    "make_memory_admission_hook", "install_memory_admission", "uninstall_memory_admission",
    # claim_verifier（溯源验证）
    "SUPPORTED", "PARTIAL", "UNSUPPORTED", "Evidence", "Verdict",
    "RuleBasedJudge", "LLMJudge", "extract_citations", "verify_claims", "verify_one",
    "sources_from_citation_map", "rule_judge_spec",
]
