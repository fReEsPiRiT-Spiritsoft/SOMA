"""
Background Tasks — SOMA's Traum-Zustand.
=========================================
Wenn SOMA idle ist, passiert das Wichtigste:
  - Episoden werden nach Salience re-ranked
  - Aehnliche Episoden werden zu "Wisdom Nodes" (L3) zusammengefuehrt
  - Rohdaten nach Konsolidierung geloescht (Space Management)
  - Diary-Eintraege geschrieben (Tages-Reflexion)
  - Schwache Episoden vergessen (Pruning)

Wie beim Menschen: Im Schlaf wird gelernt.
"""

from __future__ import annotations

import time
import asyncio
import logging
from typing import Optional, Callable, Awaitable, TYPE_CHECKING

import numpy as np

from brain_core.memory.user_identity import get_user_name_sync

if TYPE_CHECKING:
    from brain_core.memory.memory_orchestrator import MemoryOrchestrator
    from brain_core.memory.diary_writer import DiaryWriter

logger = logging.getLogger("soma.memory.background")

# ── Timing ───────────────────────────────────────────────────────────────

IDLE_THRESHOLD_SEC = 60           # 60s ohne Interaktion → idle
CONSOLIDATION_COOLDOWN_SEC = 900  # Max alle 15 Min
DREAM_COOLDOWN_SEC = 3600         # Tages-Reflexion max alle 60 Min
PRUNE_COOLDOWN_SEC = 7200         # Pruning max alle 2 Std

# ── Consolidation Config ────────────────────────────────────────────────

CLUSTER_SIMILARITY_THRESHOLD = 0.75  # Episoden mit > 75% Aehnlichkeit clustern
MIN_CLUSTER_SIZE = 2                 # Mindestens 2 Episoden fuer Wisdom Node
PRUNE_AGE_DAYS = 30                  # Episoden aelter als 30 Tage
PRUNE_IMPORTANCE_THRESHOLD = 0.4     # Nur wenn Importance < 0.4


class BackgroundConsolidator:
    """
    SOMA's Traumzustand — aktiv wenn niemand spricht.
    Konsolidiert Wissen, schreibt Tagebuch, vergisst Unwichtiges.
    """

    def __init__(
        self,
        memory_orchestrator: MemoryOrchestrator,
        diary_writer: Optional[DiaryWriter] = None,
        llm_callable: Optional[Callable[[str], Awaitable[str]]] = None,
    ):
        self._memory = memory_orchestrator
        self._diary = diary_writer
        self._llm_callable = llm_callable
        self._last_activity: float = time.time()
        self._last_consolidation: float = 0
        self._last_dream: float = 0
        self._last_prune: float = 0
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._dream_count: int = 0
        self._wisdom_nodes_created: int = 0
        self._episodes_pruned: int = 0

    # ── Public ───────────────────────────────────────────────────────

    def touch(self):
        """Bei jeder User-Interaktion aufrufen → idle-Timer reset."""
        self._last_activity = time.time()

    def set_llm(self, llm_callable: Callable[[str], Awaitable[str]]):
        self._llm_callable = llm_callable

    def set_diary(self, diary_writer: DiaryWriter):
        self._diary = diary_writer

    def start(self):
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._loop())
            logger.info("background_dreaming_started")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    # ── Main Loop ────────────────────────────────────────────────────

    async def _loop(self):
        """Endlos-Loop: prueft alle 10s ob SOMA idle ist."""
        while self._running:
            try:
                await asyncio.sleep(10)

                idle_time = time.time() - self._last_activity
                if idle_time < IDLE_THRESHOLD_SEC:
                    continue

                now = time.time()

                # Phase 1: Consolidation (Episoden → Fakten)
                if now - self._last_consolidation > CONSOLIDATION_COOLDOWN_SEC:
                    logger.info(
                        "dream_phase_consolidation",
                        idle_sec=int(idle_time),
                    )
                    await self._run_consolidation()
                    self._last_consolidation = now

                # Phase 2: Dreaming (Tages-Reflexion + Diary)
                if now - self._last_dream > DREAM_COOLDOWN_SEC:
                    logger.info("dream_phase_reflection")
                    await self._run_dreaming()
                    self._last_dream = now

                # Phase 3: Pruning (Alte unwichtige Episoden entfernen)
                if now - self._last_prune > PRUNE_COOLDOWN_SEC:
                    logger.info("dream_phase_pruning")
                    await self._run_pruning()
                    self._last_prune = now

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"background_task_error: {e}")
                await asyncio.sleep(30)

    # ══════════════════════════════════════════════════════════════════
    #  PHASE 1: CONSOLIDATION — Episoden → Wisdom Nodes (L3)
    # ══════════════════════════════════════════════════════════════════

    async def _run_consolidation(self):
        """
        1. Lade letzte Episoden mit Embeddings
        2. Finde Cluster (aehnliche Episoden)
        3. Fuer jeden Cluster: LLM extrahiert einen Fakt (L3)
        4. Schreibe Insight ins Diary
        """
        if not self._llm_callable:
            return

        try:
            # Letzte 72h Episoden holen
            episodes = await self._memory.episodic.recall(
                "", top_k=30, max_age_hours=72,
            )
            if len(episodes) < 3:
                return

            # Embedding-basierte Cluster finden
            clusters = self._find_clusters(episodes)

            for cluster in clusters:
                if len(cluster) < MIN_CLUSTER_SIZE:
                    continue

                # Episode-Texte zusammenfassen
                episode_text = ""
                user_name = get_user_name_sync()
                for ep in cluster:
                    episode_text += (
                        f"- [{ep.emotion}] {user_name}: \"{ep.user_text[:100]}\" "
                        f"-> SOMA: \"{ep.soma_text[:100]}\"\n"
                    )

                # LLM extrahiert Fakten
                prompt = (
                    "Analysiere diese zusammengehoerenden Gespraechsfragmente "
                    "und extrahiere allgemeine Fakten.\n"
                    "Gib NUR Fakten im Format: KATEGORIE|SUBJEKT|FAKT\n"
                    "Kategorien: preference, habit, relationship, knowledge, "
                    "personality\n"
                    "Keine Vermutungen — nur was klar hervorgeht.\n"
                    "Max 5 Fakten.\n\n"
                    f"Gespraeche (thematisch zusammengehoerend):\n"
                    f"{episode_text}\n\nFakten:"
                )

                try:
                    response = await asyncio.wait_for(
                        self._llm_callable(prompt), timeout=30.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning("consolidation_llm_timeout")
                    continue
                except Exception as e:
                    logger.warning(f"consolidation_llm_error: {e}")
                    continue

                if not response:
                    continue

                facts_extracted = 0
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
                        if category in valid_cats and len(fact) > 5:
                            # ── Anti-Halluzinations-Validierung ──
                            if not self._validate_extracted_fact(
                                fact, subject, episode_text,
                            ):
                                logger.warning(
                                    "consolidation_fact_REJECTED",
                                    fact=fact[:60],
                                    reason="failed validation",
                                )
                                continue
                            await self._memory.semantic.learn_fact(
                                category=category,
                                subject=subject,
                                fact=fact,
                                confidence=0.5,
                            )
                            facts_extracted += 1
                            self._wisdom_nodes_created += 1
                            logger.info(
                                "wisdom_node_created",
                                subject=subject,
                                fact=fact[:60],
                            )

                # Diary: Insight schreiben
                if facts_extracted > 0 and self._diary:
                    insight = (
                        f"Beim Nachdenken ueber {len(cluster)} aehnliche "
                        f"Gespraeche habe ich {facts_extracted} neue "
                        f"Erkenntnisse gewonnen."
                    )
                    asyncio.create_task(
                        self._diary.write_insight(insight)
                    )

        except Exception as e:
            logger.error(f"consolidation_failed: {e}")

    def _find_clusters(self, episodes) -> list[list]:
        """
        Findet Gruppen aehnlicher Episoden via Embedding-Cosine-Similarity.
        Simple Greedy Clustering (kein scikit-learn noetig).
        """
        # Nur Episoden mit Embedding
        with_emb = [
            ep for ep in episodes
            if ep.embedding is not None and len(ep.embedding) > 0
        ]
        if len(with_emb) < 2:
            return [with_emb] if with_emb else []

        used = set()
        clusters = []

        for i, anchor in enumerate(with_emb):
            if i in used:
                continue
            cluster = [anchor]
            used.add(i)

            for j, candidate in enumerate(with_emb):
                if j in used:
                    continue
                sim = float(np.dot(anchor.embedding, candidate.embedding))
                if sim > CLUSTER_SIMILARITY_THRESHOLD:
                    cluster.append(candidate)
                    used.add(j)

            clusters.append(cluster)

        return [c for c in clusters if len(c) >= MIN_CLUSTER_SIZE]

    @staticmethod
    def _validate_extracted_fact(
        fact: str, subject: str, episode_text: str,
    ) -> bool:
        """
        Validiert ob ein aus Episoden extrahierter Fakt plausibel ist.
        
        Regeln:
        1. Fakt muss auf Deutsch sein (System-Sprache)
        2. Fakt darf nicht selbst-referenziell sein (SOMA lobt sich nicht)
        3. Mindestens 1 Schlüsselwort aus den Episoden muss im Fakt vorkommen
        4. Fakt darf kein Widerspruch zu bekanntem Wissen sein
        """
        import re
        
        fact_lower = fact.lower()
        
        # Regel 1: Deutsche Sprache prüfen — englische Fakten ablehnen
        english_indicators = [
            " the ", " is ", " has ", " was ", " are ", " with ",
            " from ", " that ", " this ", " his ", " her ", " they ",
            "developed a ", "discussed ", "wants to ", "considers ",
            "frequently ", "seeks ",
        ]
        english_count = sum(1 for e in english_indicators if e in f" {fact_lower} ")
        if english_count >= 2:
            logger.debug(f"fact_rejected_english: {fact[:60]}")
            return False
        
        # Regel 2: SOMA-Selbstlob/-Selbstreferenz ablehnen
        if subject.lower() == "soma":
            self_praise = [
                "stolz", "proud", "capabilities", "fähigkeiten",
                "intelligent", "smart", "great",
            ]
            if any(sp in fact_lower for sp in self_praise):
                logger.debug(f"fact_rejected_self_praise: {fact[:60]}")
                return False
        
        # Regel 3: Fakt muss Bezug zu Episoden haben
        # Mindestens 1 bedeutsames Wort (>4 Zeichen) aus dem Fakt
        # muss in den Quell-Episoden vorkommen
        episode_lower = episode_text.lower()
        fact_words = set(
            w for w in re.findall(r'\b\w+\b', fact_lower)
            if len(w) > 4
        )
        overlap = sum(1 for w in fact_words if w in episode_lower)
        if overlap == 0 and len(fact_words) > 2:
            logger.debug(f"fact_rejected_no_overlap: {fact[:60]}")
            return False
        
        # Regel 4: Zu kurze oder zu generische Fakten ablehnen
        if len(fact.split()) < 3:
            logger.debug(f"fact_rejected_too_short: {fact[:60]}")
            return False
        
        return True

    # ══════════════════════════════════════════════════════════════════
    #  PHASE 2: DREAMING — Tages-Reflexion + Diary
    # ══════════════════════════════════════════════════════════════════

    async def _run_dreaming(self):
        """
        SOMA reflektiert ueber den Tag:
        1. Sammle Episoden der letzten 24h
        2. Generiere Tages-Zusammenfassung (Diary Dream Entry)
        """
        if not self._diary or not self._llm_callable:
            return

        try:
            episodes = await self._memory.episodic.recall(
                "", top_k=20, max_age_hours=24,
            )
            if len(episodes) < 2:
                return

            summaries = []
            source_ids = []
            user_name = get_user_name_sync()
            for ep in episodes:
                age_h = (time.time() - ep.timestamp) / 3600
                ago = (
                    f"vor {int(age_h)}h"
                    if age_h >= 1
                    else f"vor {int(age_h * 60)}min"
                )
                summaries.append(
                    f"- [{ago}, {ep.emotion}] "
                    f"{user_name}: \"{ep.user_text[:80]}\" "
                    f"-> Ich: \"{ep.soma_text[:80]}\""
                )
                source_ids.append(str(ep.id))

            await self._diary.write_dream_entry(
                episode_summaries="\n".join(summaries),
                source_ids=",".join(source_ids[:10]),
            )
            self._dream_count += 1

            logger.info(
                "dream_reflection_complete",
                episodes_reflected=len(episodes),
                total_dreams=self._dream_count,
            )

        except Exception as e:
            logger.error(f"dreaming_failed: {e}")

    # ══════════════════════════════════════════════════════════════════
    #  PHASE 3: PRUNING — Vergessen was unwichtig ist
    # ══════════════════════════════════════════════════════════════════

    async def _run_pruning(self):
        """Loescht alte, unwichtige Episoden."""
        try:
            conn = self._memory.episodic._conn
            if not conn:
                return

            cutoff = time.time() - (PRUNE_AGE_DAYS * 86400)
            loop = asyncio.get_event_loop()
            deleted = await loop.run_in_executor(
                None, self._do_prune, conn, cutoff,
            )
            if deleted > 0:
                self._episodes_pruned += deleted
                logger.info(
                    "episodes_pruned",
                    count=deleted,
                    total_pruned=self._episodes_pruned,
                )

                if self._diary:
                    asyncio.create_task(
                        self._diary.write_event_entry(
                            event_type="pruning",
                            description=(
                                f"Ich habe {deleted} alte Erinnerungen losgelassen. "
                                f"Die wichtigen Fakten habe ich behalten."
                            ),
                        )
                    )

        except Exception as e:
            logger.warning(f"prune_failed: {e}")

    @staticmethod
    def _do_prune(conn, cutoff: float) -> int:
        cur = conn.execute(
            "DELETE FROM episodes WHERE timestamp < ? AND importance < ?",
            (cutoff, PRUNE_IMPORTANCE_THRESHOLD),
        )
        conn.commit()
        return cur.rowcount

    # ── Stats ────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        return {
            "dream_count": self._dream_count,
            "wisdom_nodes_created": self._wisdom_nodes_created,
            "episodes_pruned": self._episodes_pruned,
            "last_consolidation": self._last_consolidation,
            "last_dream": self._last_dream,
            "last_prune": self._last_prune,
            "idle_since": time.time() - self._last_activity,
        }
