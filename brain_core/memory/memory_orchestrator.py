"""
Memory Orchestrator — Koordiniert alle 3 Memory-Layer.
Stellt den Kontext-Block für den LLM-Prompt zusammen.

Garantien:
  - recall_context() < 200ms
  - store_interaction() fire-and-forget
  - consolidate() nur im Idle
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

logger = logging.getLogger("soma.memory.orchestrator")


class MemoryOrchestrator:

    def __init__(self):
        self.working = WorkingMemory(max_turns=10)
        self.episodic = EpisodicMemory()
        self.semantic = SemanticMemory()
        self._initialized = False
        self._consolidation_running = False

    async def initialize(self):
        if self._initialized:
            return
        await self.episodic.initialize()
        await self.semantic.initialize()
        self._initialized = True
        logger.info("Memory Orchestrator online (3-layer hierarchy)")

    # ══════════════════════════════════════════════════════════════════
    #  CONTEXT ASSEMBLY — VOR dem LLM-Call
    # ══════════════════════════════════════════════════════════════════

    async def recall_context(
        self,
        user_text: str,
        emotion: str = "neutral",
    ) -> str:
        """
        Baut den kompletten Gedächtnis-Block zusammen.
        L2 + L3 werden parallel abgerufen.

        Returns:
            Formatierter String → direkt in den System-Prompt.
        """
        if not self._initialized:
            await self.initialize()

        # L1 — immer da (0ms)
        session = self.working.get_session_summary()
        conversation = self.working.get_conversation_block(
            max_tokens_estimate=800,
        )

        # L2 + L3 — parallel (~50ms)
        ep_task = self.episodic.recall(user_text, top_k=4)
        fact_task = self.semantic.recall_facts(user_text, top_k=6)
        personality_task = self.semantic.get_personality_snapshot(
            session["user"]
        )

        episodes, facts, personality = await asyncio.gather(
            ep_task, fact_task, personality_task,
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
        if emotion and emotion != "neutral":
            blocks.append(f"[Emotion] Patrick wirkt gerade: {emotion}")

        # Persönlichkeitsprofil (L3)
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

        # Ähnliche vergangene Gespräche (L2)
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
                    f"Patrick: \"{ep.user_text[:80]}\" "
                    f"→ SOMA: \"{ep.soma_text[:80]}\""
                )
            if ep_lines:
                blocks.append(
                    "[Erinnerungen an ähnliche Gespräche]\n"
                    + "\n".join(ep_lines[:4])
                )

        # Aktuelles Gespräch (L1)
        if conversation:
            blocks.append(f"[Aktuelles Gespräch]\n{conversation}")

        return "\n\n".join(blocks)

    # ══════════════════════════════════════════════════════════════════
    #  STORE — fire-and-forget nach dem LLM-Call
    # ══════════════════════════════════════════════════════════════════

    async def store_interaction(
        self,
        user_text: str,
        soma_text: str,
        emotion: str = "neutral",
        intent: str = "",
        topic: str = "",
    ):
        """
        L1 sofort, L2 async (fire-and-forget). Blockiert nie.
        """
        # L1: sofort
        self.working.add_user_turn(user_text, emotion=emotion, intent=intent)
        self.working.add_soma_turn(soma_text)

        # Importance-Heuristik
        importance = 0.5
        if emotion not in ("neutral", ""):
            importance += 0.2
        if len(user_text) > 100:
            importance += 0.1
        if "?" in user_text:
            importance += 0.1

        # L2: async
        asyncio.create_task(self._store_episode_safe(
            user_text, soma_text, emotion, topic, min(1.0, importance),
        ))

    async def _store_episode_safe(self, user_text, soma_text, emotion, topic, importance):
        try:
            await self.episodic.store_episode(
                user_text=user_text,
                soma_text=soma_text,
                emotion=emotion,
                topic=topic,
                importance=importance,
            )
        except Exception as e:
            logger.error(f"Failed to store episode: {e}")

    # ══════════════════════════════════════════════════════════════════
    #  CONSOLIDATION — Episoden → Fakten (Idle only)
    # ══════════════════════════════════════════════════════════════════

    async def consolidate(self, llm_callable=None):
        """
        Destilliert Episoden zu semantischen Fakten.
        Nur im Idle aufrufen.

        llm_callable: async func(prompt: str) -> str
        """
        if self._consolidation_running or llm_callable is None:
            return
        self._consolidation_running = True
        try:
            recent = await self.episodic.recall("", top_k=20, max_age_hours=72)
            if len(recent) < 3:
                return

            episode_text = ""
            for ep in recent[:15]:
                episode_text += (
                    f"- [{ep.emotion}] Patrick: \"{ep.user_text[:100]}\" "
                    f"→ SOMA: \"{ep.soma_text[:100]}\"\n"
                )

            prompt = (
                "Analysiere diese Gesprächsfragmente und extrahiere "
                "allgemeine Fakten.\n"
                "Gib NUR Fakten im Format: KATEGORIE|SUBJEKT|FAKT\n"
                "Kategorien: preference, habit, relationship, knowledge, "
                "personality\n"
                "Keine Vermutungen — nur was klar hervorgeht.\n"
                "Max 8 Fakten.\n\n"
                f"Gespräche:\n{episode_text}\n\nFakten:"
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
                            f"Consolidated fact: {subject} → {fact}"
                        )
        except Exception as e:
            logger.error(f"Consolidation failed: {e}")
        finally:
            self._consolidation_running = False

    # ── Diagnostik ───────────────────────────────────────────────────

    async def get_memory_stats(self) -> dict:
        ep_stats = await self.episodic.get_stats()
        sem_stats = await self.semantic.get_stats()
        return {
            "working_memory_turns": self.working._interaction_count,
            "episodic_episodes": ep_stats.get("total_episodes", 0),
            "semantic_facts": sem_stats.get("total_facts", 0),
            "session_minutes": self.working.get_session_summary()[
                "session_minutes"
            ],
        }
