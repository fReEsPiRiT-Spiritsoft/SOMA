# brain_core/safety/__init__.py
"""
SOMA-AI Safety Module
======================
Child Safety, Content Filter, and Action Validation.

Components:
  - ActionValidator: Validiert Actions vor Ausführung
  - PitchAnalyzer: Stimm-Analyse für Kindererkennung
  - PromptInjector: Sicheres Prompt-Handling
"""

from brain_core.safety.action_validator import (
    ActionValidator,
    ValidationResult,
    PermissionResult,
    get_validator,
    validate_action,
    check_action_permission,
)

__all__ = [
    "ActionValidator",
    "ValidationResult", 
    "PermissionResult",
    "get_validator",
    "validate_action",
    "check_action_permission",
]
