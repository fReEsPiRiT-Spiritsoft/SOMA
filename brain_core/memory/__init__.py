"""
SOMA Hierarchical Memory System
=================================
3-Layer-Architektur wie das menschliche Gehirn:

  L1: Working Memory  — RAM, 0ms, letzte N Turns
  L2: Episodic Memory — SQLite + Embeddings, ~50ms, konkrete Erlebnisse
  L3: Semantic Memory  — Abstrahierte Fakten, ~50ms, "Der Nutzer mag Kaffee"

Plus:
  - Salience Filter (nur wichtige Events speichern)
  - Diary Writer (narrative Selbstreflexion)
  - Speculative Pre-Loader (Kontext laden waehrend STT laeuft)
  - Background Consolidation / Dreaming (Episoden -> Fakten im Idle)
  - Dynamic Prompt Builder (nie repetitiver System-Prompt)
  - Two-Phase Response (Bridge + Deep Follow-up)
"""

from brain_core.memory.working_memory import WorkingMemory
from brain_core.memory.episodic_memory import EpisodicMemory
from brain_core.memory.semantic_memory import SemanticMemory
from brain_core.memory.memory_orchestrator import MemoryOrchestrator
from brain_core.memory.salience_filter import SalienceFilter, SalienceScore
from brain_core.memory.diary_writer import DiaryWriter

# ── Legacy re-exports (alte JSON-Memory bleibt als Fallback) ────────────
from brain_core.memory.legacy import SomaMemory, MemoryCategory, get_memory

__all__ = [
    # New hierarchical memory
    "WorkingMemory",
    "EpisodicMemory",
    "SemanticMemory",
    "MemoryOrchestrator",
    # Phase 1 new components
    "SalienceFilter",
    "SalienceScore",
    "DiaryWriter",
    # Legacy (used by logic_router, pipeline, call_session)
    "SomaMemory",
    "MemoryCategory",
    "get_memory",
]
