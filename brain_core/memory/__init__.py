"""
SOMA Hierarchical Memory System
=================================
3-Layer-Architektur wie das menschliche Gehirn:

  L1: Working Memory  — RAM, 0ms, letzte N Turns
  L2: Episodic Memory — SQLite + Embeddings, ~50ms, konkrete Erlebnisse
  L3: Semantic Memory  — Abstrahierte Fakten, ~50ms, "Patrick mag Kaffee"

Plus:
  - Speculative Pre-Loader (Kontext laden während STT läuft)
  - Background Consolidation (Episoden → Fakten im Idle)
  - Dynamic Prompt Builder (nie repetitiver System-Prompt)
  - Two-Phase Response (Bridge + Deep Follow-up)
"""

from brain_core.memory.working_memory import WorkingMemory
from brain_core.memory.episodic_memory import EpisodicMemory
from brain_core.memory.semantic_memory import SemanticMemory
from brain_core.memory.memory_orchestrator import MemoryOrchestrator

# ── Legacy re-exports (alte JSON-Memory bleibt als Fallback) ────────────
from brain_core.memory.legacy import SomaMemory, MemoryCategory, get_memory

__all__ = [
    # New hierarchical memory
    "WorkingMemory",
    "EpisodicMemory",
    "SemanticMemory",
    "MemoryOrchestrator",
    # Legacy (used by logic_router, pipeline, call_session)
    "SomaMemory",
    "MemoryCategory",
    "get_memory",
]
