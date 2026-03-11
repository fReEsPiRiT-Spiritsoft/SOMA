"""
Integration — Drop-in Hooks für die bestehende Pipeline.
Import + aufrufen, keine Struktur-Änderung nötig.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from brain_core.memory.memory_orchestrator import MemoryOrchestrator
from brain_core.memory.preloader import SpeculativePreloader
from brain_core.memory.background_tasks import BackgroundConsolidator
from brain_core.memory.prompt_builder import build_system_prompt
from brain_core.memory.two_phase import TwoPhaseResponder  # noqa: F401

logger = logging.getLogger("soma.memory.integration")

# ── Singletons ───────────────────────────────────────────────────────
_orchestrator: Optional[MemoryOrchestrator] = None
_preloader: Optional[SpeculativePreloader] = None
_consolidator: Optional[BackgroundConsolidator] = None


async def init_memory_system() -> MemoryOrchestrator:
    """Einmal beim Start aufrufen (main.py lifespan)."""
    global _orchestrator, _preloader, _consolidator

    _orchestrator = MemoryOrchestrator()
    await _orchestrator.initialize()

    _preloader = SpeculativePreloader(_orchestrator)
    _consolidator = BackgroundConsolidator(_orchestrator)
    _consolidator.start()

    logger.info("SOMA Memory System fully initialized")
    return _orchestrator


def get_orchestrator() -> MemoryOrchestrator:
    if _orchestrator is None:
        raise RuntimeError(
            "Memory system not initialized. "
            "Call init_memory_system() first."
        )
    return _orchestrator


def get_preloader() -> SpeculativePreloader:
    if _preloader is None:
        raise RuntimeError("Memory system not initialized.")
    return _preloader


def get_consolidator() -> BackgroundConsolidator:
    if _consolidator is None:
        raise RuntimeError("Memory system not initialized.")
    return _consolidator


# ── Convenience hooks ────────────────────────────────────────────────

async def on_wake_word():
    """Sofort aufrufen wenn Wake-Word erkannt wird."""
    if _preloader:
        await _preloader.on_wake_word()
    logger.debug("Wake word → preloading context")


async def build_context_for_query(
    user_text: str,
    emotion: str = "neutral",
    is_child: bool = False,
    interaction_count: int = 0,
) -> str:
    """
    Baut den kompletten System-Prompt mit Gedächtnis.
    Returns: fertiger system_prompt String.
    """
    orchestrator = get_orchestrator()

    preloaded = None
    if _preloader:
        preloaded = await _preloader.get_preloaded_context()

    memory_context = await orchestrator.recall_context(user_text, emotion)

    if not memory_context and preloaded:
        memory_context = preloaded

    return build_system_prompt(
        memory_context=memory_context,
        emotion=emotion,
        is_child=is_child,
        interaction_count=interaction_count,
    )


async def after_response(
    user_text: str,
    soma_text: str,
    emotion: str = "neutral",
    intent: str = "",
    topic: str = "",
):
    """Nach jedem Response aufrufen. Non-blocking."""
    orchestrator = get_orchestrator()
    await orchestrator.store_interaction(
        user_text=user_text,
        soma_text=soma_text,
        emotion=emotion,
        intent=intent,
        topic=topic,
    )
    if _consolidator:
        _consolidator.touch()


def set_consolidation_llm(llm_callable):
    """LLM-Callback für Background-Consolidation setzen."""
    if _consolidator:
        _consolidator.set_llm(llm_callable)
