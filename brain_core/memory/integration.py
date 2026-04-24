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
from brain_core.memory.embedding_service import get_embedding_service
from brain_core.memory.vocab_absorption import VocabAbsorber
from brain_core.memory.auto_extract import get_memory_extractor
from brain_core.memory.auto_dream_enhanced import get_auto_dream

logger = logging.getLogger("soma.memory.integration")

# ── Singletons ───────────────────────────────────────────────────────
_orchestrator: Optional[MemoryOrchestrator] = None
_preloader: Optional[SpeculativePreloader] = None
_consolidator: Optional[BackgroundConsolidator] = None
_vocab_absorber: Optional[VocabAbsorber] = None


async def init_memory_system() -> MemoryOrchestrator:
    """Einmal beim Start aufrufen (main.py lifespan)."""
    global _orchestrator, _preloader, _consolidator, _vocab_absorber

    # Shared Embedding Service (persistent aiohttp session + LRU cache)
    await get_embedding_service().initialize()

    _orchestrator = MemoryOrchestrator()
    await _orchestrator.initialize()

    # Vocabulary Absorption — SOMA lernt die Sprache des Nutzers
    _vocab_absorber = VocabAbsorber()
    await _vocab_absorber.initialize()

    _preloader = SpeculativePreloader(_orchestrator)
    _consolidator = BackgroundConsolidator(
        _orchestrator,
        diary_writer=_orchestrator.diary,
        vocab_absorber=_vocab_absorber,
    )
    _consolidator.start()

    # AutoDream Enhanced: Session-basierte tiefe Konsolidierung
    auto_dream = get_auto_dream()
    auto_dream.set_memory(_orchestrator)

    logger.info(
        "memory_system_initialized",
        components="L1+L2+L3+Salience+Diary+Dreaming+Preloader+VocabAbsorption+AutoDream",
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


def get_vocab_absorber() -> VocabAbsorber:
    if _vocab_absorber is None:
        raise RuntimeError("Memory system not initialized.")
    return _vocab_absorber


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

    # Vocabulary Absorption: Idiolekt-Block fuer Persona-Prompt
    idiolect_block = ""
    if _vocab_absorber:
        try:
            idiolect_block = await _vocab_absorber.get_idiolect_prompt_block()
        except Exception as e:
            logger.debug(f"vocab_prompt_block_failed: {e}")

    return build_system_prompt(
        memory_context=memory_context,
        emotion=emotion,
        is_child=is_child,
        interaction_count=interaction_count,
        idiolect_block=idiolect_block,
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

    # Vocabulary Absorption: User-Text → Vokabular-Extraktion
    if _vocab_absorber and user_text:
        asyncio.create_task(_safe_vocab_feed(user_text, soma_text))

    # Auto Memory Extraction: Fakten aus Konversation extrahieren
    try:
        extractor = get_memory_extractor()
        if extractor and _orchestrator:
            # Working Memory hat die vollständige Gesprächshistorie
            messages = []
            try:
                wm = _orchestrator.working
                for turn in wm._turns:
                    role = "assistant" if turn.role == "soma" else turn.role
                    messages.append({"role": role, "text": turn.text})
            except Exception:
                # Fallback: Nur aktuellen Turn
                messages = [
                    {"role": "user", "text": user_text},
                    {"role": "assistant", "text": soma_text},
                ]

            # Prüfe ob User explizit "merk dir" sagt → force
            force = extractor.check_explicit_remember(user_text)

            # Prüfe ob User eine Anweisung gibt → sofort als preference speichern
            if _is_user_instruction(user_text):
                asyncio.create_task(
                    _store_user_instruction(user_text, _orchestrator)
                )

            asyncio.create_task(
                _safe_auto_extract(messages, _orchestrator, force=force)
            )
    except Exception:
        pass  # Extractor nicht verfügbar → kein Problem


def _is_user_instruction(text: str) -> bool:
    """Erkennt ob der User SOMA eine Verhaltensanweisung gibt."""
    lower = text.lower()
    instruction_signals = [
        "nächstes mal", "naechstes mal", "in zukunft",
        "ab jetzt", "ab sofort", "immer wenn",
        "du sollst", "du musst", "du brauchst nicht",
        "sag einfach", "sag mir einfach", "reicht wenn",
        "nicht so ausführlich", "kürzer", "knapper",
        "du darfst", "du kannst dir sparen",
        "mach das nicht mehr", "lass das",
        "vergiss nicht", "denk dran",
    ]
    return any(sig in lower for sig in instruction_signals)


async def _store_user_instruction(user_text: str, orchestrator) -> None:
    """Speichert eine User-Anweisung sofort als preference-Fakt."""
    try:
        if hasattr(orchestrator, "semantic") and orchestrator.semantic:
            await orchestrator.semantic.learn_fact(
                category="preference",
                subject="Owner",
                fact=user_text[:200],
                confidence=0.8,
            )
            logger.info("user_instruction_saved", instruction=user_text[:80])
    except Exception as e:
        logger.debug(f"user_instruction_save_error: {e}")


async def _safe_auto_extract(messages: list, orchestrator, force: bool = False):
    """Fire-and-forget: Auto Memory Extraction mit korrekter Signatur."""
    try:
        extractor = get_memory_extractor()
        # Side Query Engine holen
        side_query = None
        try:
            from brain_core.side_query import get_side_query
            side_query = get_side_query()
            if not side_query._client:          # ← NEU
                await side_query.initialize()   # ← NEU
        except Exception:
            pass
        await extractor.maybe_extract(
            messages=messages,
            side_query_engine=side_query,
            memory_orchestrator=orchestrator,
            force=force,
        )
    except Exception as e:
        logger.debug(f"auto_extract_error: {e}")


async def _safe_vocab_feed(user_text: str, soma_text: str):
    """Fire-and-forget: Vocab-Extraktion + SOMA-Usage-Tracking."""
    try:
        await _vocab_absorber.feed(user_text)
        if soma_text:
            await _vocab_absorber.track_soma_usage(soma_text)
    except Exception as e:
        logger.debug(f"vocab_feed_error: {e}")


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


async def shutdown_memory_system():
    """Sauber herunterfahren: Consolidator + Embedding Service."""
    if _consolidator:
        _consolidator.stop()
    await get_embedding_service().shutdown()
    logger.info("memory_system_shutdown")
