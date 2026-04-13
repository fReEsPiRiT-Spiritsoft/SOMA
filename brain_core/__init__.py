"""
SOMA-AI Brain Core
==================
Zentrales Gehirn des SOMA Systems.

Enthält:
- LogicRouter: Request-Routing und Engine-Auswahl
- ActionExecutor: Zentrale Action-Ausführung mit Validation
- ActionOrchestrator: Intelligente Parallelisierung
- ActionResult: Strukturierte Ergebnisse
- ActionValidator: Zwei-Stufen-Validierung
- Action Registry: Single Source of Truth für Action Tags
- ActionStreamParser: Echtzeit-Token-Scanning

Claude Code Pattern Integration:
- Strukturierte Tool-Definitionen
- DO/DON'T Rules pro Action
- Concurrency-Safe Partitionierung
- Retry-Policies mit Backoff
- Permission-System
"""

# Core Exports
from brain_core.action_result import ActionResult
from brain_core.action_executor import (
    ActionExecutor,
    ExecutionContext,
    ExecutionStats,
    get_executor,
    set_executor,
    execute_action,
    execute_actions,
)
from brain_core.action_orchestrator import (
    ActionOrchestrator,
    TrackedAction,
    OrchestrationResult,
)
from brain_core.action_stream_parser import ActionStreamParser
from brain_core.action_registry import (
    get_all_tags,
    get_tag_info,
    get_nano_capable_tags,
    get_reask_tags,
    get_tts_confirm_tags,
    get_concurrency_safe_tags,
    get_read_only_tags,
    get_destructive_tags,
    get_tag_retry_policy,
    get_tag_timeout,
    get_do_rules,
    get_dont_rules,
    get_global_settings,
    search_tags,
    validate_tag,
    validate_tag_detailed,
    generate_prompt_section,
    generate_tool_reference,
    reload_registry,
    is_enhanced_registry,
)
from brain_core.logic_router import (
    LogicRouter,
    SomaRequest,
    SomaResponse,
    StreamChunk,
)

# Safety Exports
from brain_core.safety import (
    ActionValidator,
    ValidationResult,
    PermissionResult,
    get_validator,
)

# Killer Features (Claude Code Pattern Integration)
from brain_core.side_query import SideQueryEngine, get_side_query
from brain_core.auto_compact import AutoCompact, get_auto_compact
from brain_core.prompt_sections import build_structured_prompt
from brain_core.away_summary import AwaySummaryGenerator, get_away_summary
from brain_core.coordinator import CoordinatorMode, get_coordinator
from brain_core.cron_scheduler import CronScheduler, get_cron_scheduler
from brain_core.web_fetch_enhanced import WebFetchEnhanced, get_web_fetch
from brain_core.memory.auto_extract import MemoryExtractor, get_memory_extractor
from brain_core.memory.auto_dream_enhanced import AutoDreamEnhanced, get_auto_dream

__all__ = [
    # Core
    "ActionResult",
    "ActionExecutor",
    "ExecutionContext",
    "ExecutionStats",
    "get_executor",
    "set_executor",
    "execute_action",
    "execute_actions",
    "ActionOrchestrator",
    "TrackedAction",
    "OrchestrationResult",
    "ActionStreamParser",
    # Registry
    "get_all_tags",
    "get_tag_info",
    "get_nano_capable_tags",
    "get_reask_tags",
    "get_tts_confirm_tags",
    "get_concurrency_safe_tags",
    "get_read_only_tags",
    "get_destructive_tags",
    "get_tag_retry_policy",
    "get_tag_timeout",
    "get_do_rules",
    "get_dont_rules",
    "get_global_settings",
    "search_tags",
    "validate_tag",
    "validate_tag_detailed",
    "generate_prompt_section",
    "generate_tool_reference",
    "reload_registry",
    "is_enhanced_registry",
    # Router
    "LogicRouter",
    "SomaRequest",
    "SomaResponse",
    "StreamChunk",
    # Safety
    "ActionValidator",
    "ValidationResult",
    "PermissionResult",
    "get_validator",
    # Killer Features
    "SideQueryEngine",
    "get_side_query",
    "AutoCompact",
    "get_auto_compact",
    "build_structured_prompt",
    "AwaySummaryGenerator",
    "get_away_summary",
    "CoordinatorMode",
    "get_coordinator",
    "CronScheduler",
    "get_cron_scheduler",
    "WebFetchEnhanced",
    "get_web_fetch",
    "MemoryExtractor",
    "get_memory_extractor",
    "AutoDreamEnhanced",
    "get_auto_dream",
]
