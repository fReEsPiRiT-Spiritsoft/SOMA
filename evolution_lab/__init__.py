# evolution_lab/__init__.py
"""
SOMA-AI Evolution Lab
Self-Coding, Plugin-Sandbox, Code-Validierung und Selbst-Verbesserung.
Soma schreibt, testet und installiert eigene Plugins.
Soma analysiert und verbessert seinen eigenen Code.

Phase 5 Komponenten:
  - CodeValidator: Forbidden Patterns + AST + Black Formatting
  - SandboxRunner: Docker-Isolation mit Subprocess-Fallback
  - SelfImprovementEngine: SOMA verbessert sich selbst
  - PluginManager + PluginGenerator: Dynamisches Plugin-System
"""

from evolution_lab.code_validator import (
    CodeValidator,
    ForbiddenPatternChecker,
    ASTValidator,
    ValidationReport,
    ValidationFinding,
    Severity,
    format_with_black,
)
from evolution_lab.sandbox_runner import (
    SandboxRunner,
    SandboxResult,
    SandboxMode,
)
from evolution_lab.self_improver import (
    SelfImprovementEngine,
    ImprovementProposal,
    ProposalStatus,
    ImprovementCategory,
    IMMUTABLE_FILES,
)
from evolution_lab.plugin_manager import (
    PluginManager,
    PluginGenerator,
    PluginMeta,
    PluginNotFoundError,
    PluginError,
)

__all__ = [
    # Code Validator (P5.1)
    "CodeValidator",
    "ForbiddenPatternChecker",
    "ASTValidator",
    "ValidationReport",
    "ValidationFinding",
    "Severity",
    "format_with_black",
    # Sandbox Runner (P5.2)
    "SandboxRunner",
    "SandboxResult",
    "SandboxMode",
    # Self-Improvement (P5.3)
    "SelfImprovementEngine",
    "ImprovementProposal",
    "ProposalStatus",
    "ImprovementCategory",
    "IMMUTABLE_FILES",
    # Plugin Manager
    "PluginManager",
    "PluginGenerator",
    "PluginMeta",
    "PluginNotFoundError",
    "PluginError",
]
