"""
Integration — Drop-in Hooks fuer die bestehende Pipeline.
==========================================================
Import + aufrufen, keine Struktur-Aenderung noetig.
Jetzt mit Salience-Filter, Diary-Writer und erweitertem Store.
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
    _consolidator = BackgroundConsolidator(
        _orchestrator,
        diary_writer=_orchestrator.diary,
    )
    _consolidator.start()

    logger.info(
        "memory_system_initialized",
        components="L1+L2+L3+Salience+Diary+Dreaming+Preloader",
    )
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
    logger.debug("Wake word -> preloading context")


async def build_context_for_query(
    user_text: str,
    emotion: str = "neutral",
    is_child: bool = False,
    interaction_count: int = 0,
) -> str:
    """
    Baut den kompletten System-Prompt mit Gedaechtnis.
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
    arousal: float = 0.0,
    valence: float = 0.0,
    stress: float = 0.0,
    intent: str = "",
    topic: str = "",
    event_type: str = "conversation",
    emotion_vector: dict | None = None,
):
    """
    Nach jedem Response aufrufen. Non-blocking.
    Jetzt mit Salience-Filter: unwichtige Interaktionen werden NICHT gespeichert.
    Phase 4: emotion_vector (dict) wird als Metadata an L2 durchgereicht.
    """
    orchestrator = get_orchestrator()
    await orchestrator.store_interaction(
        user_text=user_text,
        soma_text=soma_text,
        emotion=emotion,
        arousal=arousal,
        valence=valence,
        stress=stress,
        intent=intent,
        topic=topic,
        event_type=event_type,
        emotion_vector=emotion_vector,
    )
    if _consolidator:
        _consolidator.touch()


async def store_system_event(
    event_type: str,
    description: str,
    user_text: str = "",
    soma_text: str = "",
    emotion: str = "neutral",
    importance: float = 0.8,
):
    """
    Speichert ein System-Event (Phone-Call, Plugin, Intervention, etc.).
    Bypassed Salience-Filter — Events sind IMMER wichtig.
    """
    orchestrator = get_orchestrator()
    await orchestrator.store_event(
        event_type=event_type,
        description=description,
        user_text=user_text,
        soma_text=soma_text,
        emotion=emotion,
        importance=importance,
    )


def set_consolidation_llm(llm_callable):
    """LLM-Callback fuer Background-Consolidation + Diary setzen."""
    if _consolidator:
        _consolidator.set_llm(llm_callable)
    # Diary bekommt auch ein LLM (Light-Engine fuer Speed)
    if _orchestrator and _orchestrator.diary:
        _orchestrator.diary.set_llm(llm_callable)


def set_diary_llm(llm_callable):
    """Separates LLM fuer Diary (z.B. Light-Engine statt Heavy)."""
    if _orchestrator and _orchestrator.diary:
        _orchestrator.diary.set_llm(llm_callable)
