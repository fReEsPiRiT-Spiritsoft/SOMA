"""
SOMA-AI Executive Arm — Phase 3: SOMA handelt
================================================
Nicht nur denken, nicht nur sprechen — HANDELN.

Module:
  policy_engine   — Gatekeeper: jede Aktion wird geprueft + geloggt
  filesystem_map  — SOMA kennt seine eigene Dateistruktur (live)
  terminal        — Sichere Shell-Ausfuehrung (.bak vor Aenderungen)
  browser         — Playwright headless Chromium (lesen, nie leaken)
  bluetooth       — BLE Discovery + Steuerung via bleak
  toolset         — Tool-Definitionen fuer den LangGraph Agent
  agency          — State-Machine Agent: Ziel → Plan → Execute → Verify
"""

from executive_arm.policy_engine import (   # noqa: F401
    PolicyEngine,
    ActionType,
    RiskLevel,
    ActionRequest,
    ActionResult,
)
from executive_arm.filesystem_map import (  # noqa: F401
    FilesystemMap,
    FileCategory,
)
from executive_arm.terminal import (        # noqa: F401
    SecureTerminal,
)
from executive_arm.toolset import (         # noqa: F401
    Toolset,
    ToolResult,
)
from executive_arm.agency import (          # noqa: F401
    SomaAgent,
    AgentPhase,
    AgentRun,
    AgentStep,
)

__all__ = [
    "PolicyEngine", "ActionType", "RiskLevel", "ActionRequest", "ActionResult",
    "FilesystemMap", "FileCategory",
    "SecureTerminal",
    "Toolset", "ToolResult",
    "SomaAgent", "AgentPhase", "AgentRun", "AgentStep",
]
