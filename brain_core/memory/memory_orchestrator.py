"""
Memory Orchestrator — Das Bewusstsein hinter dem Gedaechtnis.
==============================================================
Koordiniert alle Memory-Layer + Salience + Diary.
Stellt den Kontext-Block fuer den LLM-Prompt zusammen.

Garantien:
  - recall_context() < 200ms
  - store_interaction() fire-and-forget (aber Salience-gefilter!)
  - Diary-Eintraege bei hoher Salience
  - Consolidation nur im Idle (via BackgroundConsolidator)
"""

from __future__ import annotations

import time
import asyncio
import logging
from datetime import datetime
from typing import Optional

from brain_core.memory.working_memory import WorkingMemory
from brain_core.memory.episodic_memory import EpisodicMemory
from brain_core.memory.semantic_memory import SemanticMemory
from brain_core.memory.salience_filter import SalienceFilter, SalienceScore
from brain_core.memory.diary_writer import DiaryWriter
from brain_core.memory.user_identity import get_user_name_sync

logger = logging.getLogger("soma.memory.orchestrator")


class MemoryOrchestrator:
    """
    Koordiniert L1/L2/L3 + Salience + Diary.
    SSOT fuer alle Gedaechtnis-Operationen.
    """

    def __init__(self):
        self.working = WorkingMemory(max_turns=10)
        self.episodic = EpisodicMemory()
        self.semantic = SemanticMemory()
        self.salience = SalienceFilter()
        self.diary = DiaryWriter()
        self._initialized = False
        self._consolidation_running = False
        self._store_count: int = 0
        self._filtered_count: int = 0
        self._diary_count: int = 0

    async def initialize(self):
        if self._initialized:
            return
        await self.episodic.initialize()
        await self.semantic.initialize()
        await self.diary.initialize()
        self._initialized = True
        logger.info(
            "memory_orchestrator_online",
            layers="L1+L2+L3+Salience+Diary",
        )

    # ══════════════════════════════════════════════════════════════════
    #  CONTEXT ASSEMBLY — VOR dem LLM-Call
    # ══════════════════════════════════════════════════════════════════

    async def recall_context(
        self,
        user_text: str,
        emotion: str = "neutral",
    ) -> str:
        """
        Baut den kompletten Gedaechtnis-Block zusammen.
        L2 + L3 + Diary werden parallel abgerufen.

        Returns:
            Formatierter String -> direkt in den System-Prompt.
        """
        if not self._initialized:
            await self.initialize()

        # L1 — immer da (0ms)
        session = self.working.get_session_summary()
        conversation = self.working.get_conversation_block(
            max_tokens_estimate=800,
        )

        # L2 + L3 + Diary + Preferences — parallel (~50ms)
        ep_task = self.episodic.recall(user_text, top_k=4)
        fact_task = self.semantic.recall_facts(user_text, top_k=6)
        personality_task = self.semantic.get_personality_snapshot(
            session["user"]
        )
        diary_task = self.diary.get_diary_summary_for_prompt(max_entries=3)
        prefs_task = self.semantic.get_user_preferences()

        episodes, facts, personality, diary_block, user_prefs = await asyncio.gather(
            ep_task, fact_task, personality_task, diary_task, prefs_task,
            return_exceptions=True,
        )

        if isinstance(episodes, BaseException):
            logger.warning(f"Episodic recall failed: {episodes}")
            episodes = []
        if isinstance(facts, BaseException):
            logger.warning(f"Semantic recall failed: {facts}")
            facts = []
        if isinstance(personality, BaseException):
            personality = ""
        if isinstance(diary_block, BaseException):
            diary_block = ""
        if isinstance(user_prefs, BaseException):
            logger.warning(f"Preference recall failed: {user_prefs}")
            user_prefs = []

        # ── Block zusammenbauen ──────────────────────────────────
        now = datetime.now()
        blocks: list[str] = []

        # Zeitkontext
        blocks.append(
            f"[Zeitkontext] {now.strftime('%A, %d. %B %Y, %H:%M Uhr')} | "
            f"Session: {session['session_minutes']} Min, "
            f"{session['turns_count']} Interaktionen"
        )

        # Emotion
        user_name = get_user_name_sync()
        if emotion and emotion != "neutral":
            blocks.append(f"[Emotion] {user_name} wirkt gerade: {emotion}")

        # ── NUTZER-PRÄFERENZEN (IMMER dabei, NICHT optional!) ─────
        # Diese Regeln gelten für JEDE Antwort, unabhängig vom Thema.
        if user_prefs:
            pref_lines = []
            seen = set()
            for p in user_prefs:
                # Deduplizierung
                key = p.fact.lower().strip()
                if key in seen:
                    continue
                seen.add(key)
                pref_lines.append(f"- {p.fact}")
            if pref_lines:
                blocks.append(
                    "[NUTZER-PRÄFERENZEN — Befolge diese IMMER]\n"
                    + "\n".join(pref_lines[:10])
                )

        # Persoenlichkeitsprofil (L3)
        if personality and isinstance(personality, str) and len(personality) > 10:
            blocks.append(f"[Langzeit-Wissen]\n{personality}")

        # Relevante Fakten (L3)
        if facts:
            fact_lines = [
                f"- {f.subject}: {f.fact}"
                for f in facts
                if f.relevance > 0.3
            ]
            if fact_lines:
                blocks.append(
                    "[Relevante Fakten]\n" + "\n".join(fact_lines[:5])
                )

        # Aehnliche vergangene Gespraeche (L2)
        if episodes:
            ep_lines = []
            for ep in episodes:
                if ep.relevance <= 0.25:
                    continue
                age = (time.time() - ep.timestamp) / 3600
                if age < 1:
                    ago = f"vor {int(age * 60)} Min"
                elif age < 24:
                    ago = f"vor {int(age)} Std"
                else:
                    ago = f"vor {int(age / 24)} Tagen"
                ep_lines.append(
                    f"- [{ago}, Stimmung: {ep.emotion}] "
                    f"{user_name}: \"{ep.user_text[:80]}\" "
                    f"-> SOMA: \"{ep.soma_text[:80]}\""
                )
            if ep_lines:
                blocks.append(
                    "[Erinnerungen an aehnliche Gespraeche]\n"
                    + "\n".join(ep_lines[:4])
                )

        # Tagebuch-Eintraege (Selbstreflexion)
        if diary_block and isinstance(diary_block, str) and len(diary_block) > 10:
            blocks.append(diary_block)

        # Aktuelles Gespraech (L1)
        if conversation:
            blocks.append(f"[Aktuelles Gespraech]\n{conversation}")

        return "\n\n".join(blocks)

    # ══════════════════════════════════════════════════════════════════
    #  STORE — fire-and-forget nach dem LLM-Call (mit Salience!)
    # ══════════════════════════════════════════════════════════════════

    async def store_interaction(
        self,
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
        L1 IMMER sofort.
        L2 nur wenn Salience > Threshold.
        Diary nur wenn Salience HOCH.
        Blockiert nie.

        Phase 4: emotion_vector (dict) wird in L1 + L2 gespeichert.
        """
        # L1: IMMER — Working Memory muss alles haben
        self.working.add_user_turn(user_text, emotion=emotion, intent=intent)
        self.working.add_soma_turn(soma_text)

        # Phase 4: Emotion Vector in L1 Working Memory setzen
        if emotion_vector:
            self.working.set_context("emotion_vector", emotion_vector)
            self.working.set_context(
                "voice_dominant_emotion",
                emotion_vector.get("dominant", "neutral"),
            )

        # Salience bewerten
        salience = self.salience.evaluate(
            user_text=user_text,
            soma_text=soma_text,
            emotion=emotion,
            arousal=arousal,
            valence=valence,
            stress=stress,
        )

        if not salience.is_salient:
            self._filtered_count += 1
            logger.debug(
                "memory_filtered_out",
                score=salience.total,
                reason=salience.reason,
                text=user_text[:40],
            )
            return

        # Importance aus Salience ableiten
        importance = min(1.0, salience.total + 0.2)

        # L2: Async store (fire-and-forget)
        self._store_count += 1
        # Phase 4: Emotion Vector als Summary-Enrichment fuer L2
        _summary_extra = ""
        if emotion_vector:
            dom = emotion_vector.get("dominant", "neutral")
            conf = emotion_vector.get("confidence", 0)
            _summary_extra = (
                f" [Voice: {dom} ({conf:.0%})"
                f" H={emotion_vector.get('happy', 0):.1f}"
                f" S={emotion_vector.get('sad', 0):.1f}"
                f" St={emotion_vector.get('stressed', 0):.1f}"
                f" T={emotion_vector.get('tired', 0):.1f}"
                f" A={emotion_vector.get('angry', 0):.1f}]"
            )
        asyncio.create_task(self._store_episode_safe(
            user_text=user_text,
            soma_text=soma_text,
            emotion=emotion,
            arousal=arousal,
            valence=valence,
            event_type=event_type,
            topic=topic + _summary_extra,
            importance=importance,
        ))

        # Diary: Nur bei hoher Salience
        if salience.is_highly_salient:
            self._diary_count += 1
            asyncio.create_task(self._write_diary_safe(
                user_text=user_text,
                soma_text=soma_text,
                emotion=emotion,
                arousal=arousal,
            ))

    async def store_event(
        self,
        event_type: str,
        description: str,
        user_text: str = "",
        soma_text: str = "",
        emotion: str = "neutral",
        importance: float = 0.8,
    ):
        """
        Speichert ein System-Event (Phone-Call, Plugin, Intervention, etc.).
        Bypass Salience — Events sind IMMER wichtig.
        """
        asyncio.create_task(self._store_episode_safe(
            user_text=user_text or description,
            soma_text=soma_text,
            emotion=emotion,
            arousal=0.5,
            valence=0.0,
            event_type=event_type,
            topic=description[:60],
            importance=importance,
        ))

        # Event-Diary-Eintrag
        asyncio.create_task(self.diary.write_event_entry(
            event_type=event_type,
            description=description,
            emotion=emotion,
        ))

    async def _store_episode_safe(
        self,
        user_text: str,
        soma_text: str,
        emotion: str,
        arousal: float,
        valence: float,
        event_type: str,
        topic: str,
        importance: float,
    ):
        try:
            await self.episodic.store_episode(
                user_text=user_text,
                soma_text=soma_text,
                emotion=emotion,
                arousal=arousal,
                valence=valence,
                event_type=event_type,
                topic=topic,
                importance=importance,
            )
        except Exception as e:
            logger.error(f"Failed to store episode: {e}")

    async def _write_diary_safe(
        self,
        user_text: str,
        soma_text: str,
        emotion: str,
        arousal: float,
    ):
        try:
            await self.diary.write_interaction_entry(
                user_text=user_text,
                soma_text=soma_text,
                emotion=emotion,
                arousal=arousal,
            )
        except Exception as e:
            logger.error(f"Failed to write diary: {e}")

    # ══════════════════════════════════════════════════════════════════
    #  CONSOLIDATION — Episoden → Fakten (Idle only)
    #  NOTE: Jetzt vom BackgroundConsolidator direkt gesteuert.
    #  Diese Methode bleibt als Legacy-Compat.
    # ══════════════════════════════════════════════════════════════════

    async def consolidate(self, llm_callable=None):
        """Legacy-Compat. Wird vom BackgroundConsolidator aufgerufen."""
        if self._consolidation_running or llm_callable is None:
            return
        self._consolidation_running = True
        try:
            recent = await self.episodic.recall("", top_k=20, max_age_hours=72)
            if len(recent) < 3:
                return

            episode_text = ""
            user_name = get_user_name_sync()
            for ep in recent[:15]:
                episode_text += (
                    f"- [{ep.emotion}] {user_name}: \"{ep.user_text[:100]}\" "
                    f"-> SOMA: \"{ep.soma_text[:100]}\"\n"
                )

            prompt = (
                "Analysiere diese Gespraechsfragmente und extrahiere "
                "allgemeine Fakten.\n"
                "Gib NUR Fakten im Format: KATEGORIE|SUBJEKT|FAKT\n"
                "Kategorien: preference, habit, relationship, knowledge, "
                "personality\n"
                "Keine Vermutungen — nur was klar hervorgeht.\n"
                "Max 8 Fakten.\n\n"
                f"Gespraeche:\n{episode_text}\n\nFakten:"
            )

            response = await llm_callable(prompt)
            if not response:
                return

            for line in response.strip().split("\n"):
                line = line.strip().lstrip("- ")
                parts = line.split("|")
                if len(parts) >= 3:
                    category = parts[0].strip().lower()
                    subject = parts[1].strip()
                    fact = parts[2].strip()
                    valid_cats = {
                        "preference", "habit", "relationship",
                        "knowledge", "personality",
                    }
                    if category in valid_cats:
                        await self.semantic.learn_fact(
                            category=category,
                            subject=subject,
                            fact=fact,
                            confidence=0.5,
                        )
                        logger.info(
                            "consolidated_fact",
                            subject=subject,
                            fact=fact[:60],
                        )
        except Exception as e:
            logger.error(f"Consolidation failed: {e}")
        finally:
            self._consolidation_running = False

    # ── Diagnostik ───────────────────────────────────────────────────

    async def get_memory_stats(self) -> dict:
        ep_stats = await self.episodic.get_stats()
        sem_stats = await self.semantic.get_stats()
        diary_stats = await self.diary.get_stats()
        return {
            "working_memory_turns": self.working._interaction_count,
            "episodic_episodes": ep_stats.get("total_episodes", 0),
            "semantic_facts": sem_stats.get("total_facts", 0),
            "diary_entries": diary_stats.get("total_entries", 0),
            "salience_stored": self._store_count,
            "salience_filtered": self._filtered_count,
            "diary_triggers": self._diary_count,
            "session_minutes": self.working.get_session_summary()[
                "session_minutes"
            ],
        }
