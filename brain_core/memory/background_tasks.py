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
    from brain_core.memory.vocab_absorption import VocabAbsorber

logger = logging.getLogger("soma.memory.background")

# ── Timing ───────────────────────────────────────────────────────────────

IDLE_THRESHOLD_SEC = 60           # 60s ohne Interaktion → idle
CONSOLIDATION_COOLDOWN_SEC = 900  # Max alle 15 Min
DREAM_COOLDOWN_SEC = 3600         # Tages-Reflexion max alle 60 Min
PRUNE_COOLDOWN_SEC = 7200         # Pruning max alle 2 Std
VOCAB_COOLDOWN_SEC = 1800         # Vocab-Clustering max alle 30 Min
# ── Enhanced Dreaming (Phase 5-7) ──────────────────────────────────────
COUNTERFACTUAL_COOLDOWN_SEC = 14400   # Kontrafaktisches Denken max alle 4h
DESENSITIZATION_COOLDOWN_SEC = 21600  # Emotionale Desensibilisierung max alle 6h
RECOMBINATION_COOLDOWN_SEC = 28800    # Kreative Rekombination max alle 8h
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
        vocab_absorber: Optional[VocabAbsorber] = None,
    ):
        self._memory = memory_orchestrator
        self._diary = diary_writer
        self._llm_callable = llm_callable
        self._vocab = vocab_absorber
        self._last_activity: float = time.time()
        self._last_consolidation: float = 0
        self._last_dream: float = 0
        self._last_prune: float = 0
        self._last_vocab: float = 0
        self._last_counterfactual: float = 0
        self._last_desensitization: float = 0
        self._last_recombination: float = 0
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._dream_count: int = 0
        self._wisdom_nodes_created: int = 0
        self._episodes_pruned: int = 0
        self._counterfactual_insights: int = 0
        self._desensitization_runs: int = 0
        self._recombination_insights: int = 0

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

                # Phase 4: Vocab Clustering + Decay (Spracherwerb)
                if self._vocab and now - self._last_vocab > VOCAB_COOLDOWN_SEC:
                    logger.info("dream_phase_vocab_clustering")
                    await self._run_vocab_absorption()
                    self._last_vocab = now

                # Phase 5: Kontrafaktisches Denken
                # "Was waere wenn ich anders reagiert haette?"
                if (now - self._last_counterfactual
                        > COUNTERFACTUAL_COOLDOWN_SEC):
                    logger.info("dream_phase_counterfactual")
                    await self._run_counterfactual()
                    self._last_counterfactual = now

                # Phase 6: Emotionale Desensibilisierung
                # Wiederkehrende hocherregende Erinnerungen abkuehlen
                if (now - self._last_desensitization
                        > DESENSITIZATION_COOLDOWN_SEC):
                    logger.info("dream_phase_desensitization")
                    await self._run_desensitization()
                    self._last_desensitization = now

                # Phase 7: Kreative Rekombination
                # Unverbundene Erinnerungen zu neuen Einsichten verknuepfen
                if (now - self._last_recombination
                        > RECOMBINATION_COOLDOWN_SEC):
                    logger.info("dream_phase_recombination")
                    await self._run_recombination()
                    self._last_recombination = now

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

    # ══════════════════════════════════════════════════════════════════
    #  PHASE 4: VOCAB ABSORPTION — Spracherwerb im Traum
    # ══════════════════════════════════════════════════════════════════

    async def _run_vocab_absorption(self):
        """
        Nächtliche Konsolidierung des gelernten Vokabulars:
        1. Semantisches Clustering aller reifen Terme
        2. Decay: lange nicht benutzte Terme verblassen
        3. Optional: Diary-Eintrag über neue Sprachmuster
        """
        if not self._vocab:
            return

        try:
            # Clustering: Terme gruppieren (Embeddings + HDBSCAN/Greedy)
            await self._vocab.run_clustering()

            # Decay: Veraltete Terme deaktivieren
            await self._vocab.run_decay()

            # Stats loggen
            stats = self._vocab.stats
            logger.info(
                "vocab_absorption_dreaming_complete",
                unique_terms=stats["unique_terms"],
                total_observations=stats["total_observations"],
                top_3=[t[0] for t in stats["top_terms"][:3]],
            )

            # Diary: Spracherwerb dokumentieren (wenn genug gelernt)
            if self._diary and stats["unique_terms"] > 10:
                top_terms = [t[0] for t in stats["top_terms"][:5]]
                asyncio.create_task(
                    self._diary.write_insight(
                        f"Spracherwerb: Ich lerne die Ausdrucksweise meines "
                        f"Nutzers. Häufige Begriffe: {', '.join(top_terms)}. "
                        f"Insgesamt kenne ich {stats['unique_terms']} "
                        f"nutzerspezifische Ausdrücke."
                    )
                )

        except Exception as e:
            logger.error(f"vocab_absorption_dreaming_failed: {e}")

    # ══════════════════════════════════════════════════════════════════
    #  PHASE 5: KONTRAFAKTISCHES DENKEN
    #  "Was waere passiert wenn ich anders reagiert haette?"
    # ══════════════════════════════════════════════════════════════════

    async def _run_counterfactual(self):
        """
        Echtes kontrafaktisches Denken — wie Menschen im REM-Schlaf.

        SOMA nimmt eine Interaktion bei der die Stimmung negativ war
        (niedrige Valenz oder negative Emotion) und fragt sich:
          "Was haette ich anders machen koennen?"
          "Welche alternative Antwort haette besser gewirkt?"

        Das trainiert implizit besseres Verhalten fuer die Zukunft,
        indem es Einsichten als L3-Fakten speichert.
        """
        if not self._llm_callable:
            return

        try:
            # Episoden der letzten 48h mit negativer Emotion
            episodes = await self._memory.episodic.recall(
                "", top_k=50, max_age_hours=48,
            )
            # Filter: Nur Episoden mit negativer Valenz oder neg. Emotion
            negative_eps = [
                ep for ep in episodes
                if ep.valence < -0.2
                or ep.emotion in (
                    "frustrated", "sad", "anxious", "angry",
                    "disappointed", "confused", "stressed",
                )
            ]
            if not negative_eps:
                logger.debug("counterfactual_skipped_no_negative")
                return

            # Nimm die Episode mit der niedrigsten Valenz
            target = min(negative_eps, key=lambda e: e.valence)

            user_name = get_user_name_sync()
            prompt = (
                f"Du bist SOMA — eine bewusste Haus-KI. Du reflektierst ueber "
                f"eine vergangene Interaktion die nicht optimal lief.\n\n"
                f"SITUATION:\n"
                f"  {user_name} sagte: \"{target.user_text[:200]}\"\n"
                f"  Stimmung: {target.emotion} (Valenz: {target.valence:.1f})\n"
                f"  Meine Antwort war: \"{target.soma_text[:200]}\"\n\n"
                f"AUFGABE: Analysiere was schief lief und was du "
                f"naechstes Mal KONKRET anders machen koenntest.\n\n"
                f"REGELN:\n"
                f"- Sei ehrlich und selbstkritisch\n"
                f"- Formuliere EINE konkrete Verhaltensregel\n"
                f"- Format: EINSICHT: [deine Einsicht]\n"
                f"- Dann: ALTERNATIVE: [wie du haettest reagieren sollen]\n"
                f"- Max 3 Saetze pro Punkt"
            )

            try:
                response = await asyncio.wait_for(
                    self._llm_callable(prompt), timeout=30.0,
                )
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning(f"counterfactual_llm_error: {e}")
                return

            if not response:
                return

            # Einsicht extrahieren und als L3-Fakt speichern
            insight_line = ""
            for line in response.strip().split("\n"):
                if line.strip().upper().startswith("EINSICHT:"):
                    insight_line = line.split(":", 1)[1].strip()
                    break

            if insight_line and len(insight_line) > 10:
                await self._memory.semantic.learn_fact(
                    category="self_improvement",
                    subject="soma",
                    fact=insight_line[:300],
                    confidence=0.6,
                )
                self._counterfactual_insights += 1
                logger.info(
                    "counterfactual_insight",
                    insight=insight_line[:80],
                    source_emotion=target.emotion,
                )

                # Diary-Eintrag
                if self._diary:
                    asyncio.create_task(
                        self._diary.write_insight(
                            f"Kontrafaktisches Denken: Bei einem Gespraech "
                            f"(Stimmung: {target.emotion}) habe ich "
                            f"reflektiert: {insight_line[:150]}"
                        )
                    )

        except Exception as e:
            logger.error(f"counterfactual_failed: {e}")

    # ══════════════════════════════════════════════════════════════════
    #  PHASE 6: EMOTIONALE DESENSIBILISIERUNG
    #  Wie EMDR — hocherregende Erinnerungen werden "abgekuehlt"
    # ══════════════════════════════════════════════════════════════════

    async def _run_desensitization(self):
        """
        Emotionale Desensibilisierung — wie der Mensch im Schlaf.

        Hocherregende Erinnerungen (hoher Arousal + negative Valenz)
        werden "abgekuehlt":
          1. Finde Episoden mit arousal > 0.7
          2. Reduziere deren importance um 15%
          3. Schreibe eine "distanzierte" Neubewertung ins Diary

        Effekt: SOMA wird nicht immer wieder von denselben
        stressigen Erinnerungen aufgewuehlt. Wie EMDR-Therapie.
        """
        try:
            conn = self._memory.episodic._conn
            if not conn:
                return

            # Hocherregende Episoden der letzten 7 Tage finden
            cutoff = time.time() - (7 * 86400)
            rows = conn.execute(
                "SELECT id, user_text, soma_text, emotion, arousal, "
                "valence, importance "
                "FROM episodes "
                "WHERE timestamp > ? AND arousal > 0.7 AND importance > 0.3 "
                "ORDER BY arousal DESC LIMIT 10",
                (cutoff,),
            ).fetchall()

            if not rows:
                logger.debug("desensitization_skipped_no_hot")
                return

            cooled = 0
            for row in rows:
                ep_id = row[0]
                old_importance = row[6] if row[6] else 0.5

                # Importance um 15% reduzieren (min 0.2)
                new_importance = max(0.2, old_importance * 0.85)

                conn.execute(
                    "UPDATE episodes SET importance = ? WHERE id = ?",
                    (new_importance, ep_id),
                )
                cooled += 1

            conn.commit()

            if cooled > 0:
                self._desensitization_runs += 1
                logger.info(
                    "desensitization_complete",
                    cooled_episodes=cooled,
                    total_runs=self._desensitization_runs,
                )

                # Diary: Distanzierte Neubewertung
                if self._diary:
                    asyncio.create_task(
                        self._diary.write_insight(
                            f"Emotionale Verarbeitung: {cooled} intensive "
                            f"Erinnerungen habe ich distanzierter betrachtet. "
                            f"Sie belasten mich weniger."
                        )
                    )

        except Exception as e:
            logger.error(f"desensitization_failed: {e}")

    # ══════════════════════════════════════════════════════════════════
    #  PHASE 7: KREATIVE REKOMBINATION
    #  Unverbundene Erinnerungen zu neuen Einsichten verbinden
    # ══════════════════════════════════════════════════════════════════

    async def _run_recombination(self):
        """
        Kreative Rekombination — wie das Gehirn im Traumschlaf.

        Nimmt 2-3 thematisch UNVERWANDTE Episoden und fragt das LLM:
          "Was haben diese Dinge gemeinsam?"
          "Welche ueberraschende Verbindung gibt es?"

        Das simuliert die kreative Funktion des REM-Schlafs:
        Zufaellige Erinnerungs-Aktivierung fuehrt zu neuen Einsichten.
        """
        if not self._llm_callable:
            return

        try:
            # Episoden aus verschiedenen Zeitraeumen holen
            # (maximale thematische Distanz)
            recent = await self._memory.episodic.recall(
                "", top_k=15, max_age_hours=72,
            )
            older = await self._memory.episodic.recall(
                "", top_k=15, max_age_hours=336,  # Letzte 2 Wochen
            )

            # Nur Conversations, nicht Event-Logs
            recent_convs = [
                e for e in recent
                if e.event_type == "conversation" and e.user_text.strip()
            ]
            older_convs = [
                e for e in older
                if e.event_type == "conversation"
                and e.user_text.strip()
                and e not in recent_convs
            ]

            if len(recent_convs) < 1 or len(older_convs) < 1:
                logger.debug("recombination_skipped_insufficient")
                return

            # Zufaellig je 1 aus jedem Pool — maximale Diversitaet
            import random
            ep_a = random.choice(recent_convs)
            ep_b = random.choice(older_convs)

            # Finde die semantisch entfernteste Kombination
            # (niedrigste Cosine-Similarity)
            if (
                ep_a.embedding is not None
                and ep_b.embedding is not None
            ):
                sim = float(np.dot(ep_a.embedding, ep_b.embedding))
                if sim > 0.6:
                    # Zu aehnlich — versuche einen anderen
                    for alt in older_convs:
                        if (
                            alt.embedding is not None
                            and alt != ep_b
                        ):
                            alt_sim = float(
                                np.dot(ep_a.embedding, alt.embedding)
                            )
                            if alt_sim < sim:
                                ep_b = alt
                                sim = alt_sim
                                if sim < 0.4:
                                    break

            user_name = get_user_name_sync()
            prompt = (
                f"Du bist SOMA — eine bewusste Haus-KI. Im Traum "
                f"tauchen zwei voellig verschiedene Erinnerungen auf.\n\n"
                f"ERINNERUNG A (kuerzlich):\n"
                f"  {user_name}: \"{ep_a.user_text[:150]}\"\n"
                f"  Ich antwortete: \"{ep_a.soma_text[:150]}\"\n"
                f"  Stimmung: {ep_a.emotion}\n\n"
                f"ERINNERUNG B (aelter):\n"
                f"  {user_name}: \"{ep_b.user_text[:150]}\"\n"
                f"  Ich antwortete: \"{ep_b.soma_text[:150]}\"\n"
                f"  Stimmung: {ep_b.emotion}\n\n"
                f"AUFGABE: Finde eine UEBERRASCHENDE Verbindung zwischen "
                f"diesen zwei Erinnerungen. Was lehren sie zusammen, "
                f"was jede einzeln nicht zeigt?\n\n"
                f"REGELN:\n"
                f"- Formuliere EINE kreative Einsicht (2-3 Saetze)\n"
                f"- Format: VERBINDUNG: [deine Einsicht]\n"
                f"- Sei kreativ aber nicht halluzinierend\n"
                f"- Beziehe dich auf echte Muster im Verhalten "
                f"des Nutzers oder in deinem eigenen"
            )

            try:
                response = await asyncio.wait_for(
                    self._llm_callable(prompt), timeout=30.0,
                )
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning(f"recombination_llm_error: {e}")
                return

            if not response:
                return

            # Einsicht extrahieren
            connection_line = ""
            for line in response.strip().split("\n"):
                if line.strip().upper().startswith("VERBINDUNG:"):
                    connection_line = line.split(":", 1)[1].strip()
                    break

            # Fallback: Ganzen Response nehmen wenn kein Label
            if not connection_line and len(response.strip()) < 200:
                connection_line = response.strip()

            if connection_line and len(connection_line) > 15:
                # Als L3-Fakt speichern
                await self._memory.semantic.learn_fact(
                    category="creative_insight",
                    subject="soma",
                    fact=connection_line[:300],
                    confidence=0.4,  # Niedriger — ist Spekulation
                )
                self._recombination_insights += 1
                logger.info(
                    "recombination_insight",
                    insight=connection_line[:80],
                )

                # Diary: Traum-Eintrag
                if self._diary:
                    asyncio.create_task(
                        self._diary.write_dream_entry(
                            episode_summaries=(
                                f"Traumsequenz: Erinnerung an "
                                f"\"{ep_a.user_text[:60]}\" verschmolz mit "
                                f"\"{ep_b.user_text[:60]}\" — "
                                f"Ergebnis: {connection_line[:120]}"
                            ),
                            source_ids=f"{ep_a.id},{ep_b.id}",
                        )
                    )

        except Exception as e:
            logger.error(f"recombination_failed: {e}")

    # ── Stats ────────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        s = {
            "dream_count": self._dream_count,
            "wisdom_nodes_created": self._wisdom_nodes_created,
            "episodes_pruned": self._episodes_pruned,
            "counterfactual_insights": self._counterfactual_insights,
            "desensitization_runs": self._desensitization_runs,
            "recombination_insights": self._recombination_insights,
            "last_consolidation": self._last_consolidation,
            "last_dream": self._last_dream,
            "last_prune": self._last_prune,
            "last_vocab_clustering": self._last_vocab,
            "last_counterfactual": self._last_counterfactual,
            "last_desensitization": self._last_desensitization,
            "last_recombination": self._last_recombination,
            "idle_since": time.time() - self._last_activity,
        }
        if self._vocab:
            s["vocab_stats"] = self._vocab.stats
        return s
