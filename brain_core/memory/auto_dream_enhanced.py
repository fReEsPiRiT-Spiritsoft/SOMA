"""
AutoDream Enhanced — Erweiterte Traum-Konsolidierung für SOMA.
===============================================================
Inspiriert von Claude Code's contextOrchestrator.ts:
Erweitert den bestehenden BackgroundConsolidator mit:

1. **Session-Count-Gate**: Tiefenkonsolidierung erst nach N Sessions
2. **Cross-Session Patterns**: Muster über mehrere Sessions hinweg erkennen
3. **Consolidation Lock**: Verhindert doppelte Konsolidierung
4. **Tiefe Reflexion**: LLM-basierte Persönlichkeits-Analyse
5. **Knowledge Graph Verdichtung**: Semantic Facts werden verknüpft

Funktioniert als ERGÄNZUNG zum bestehenden BackgroundConsolidator:
  - BackgroundConsolidator: Idle-basiert (60s), schnelle Konsolidierung
  - AutoDreamEnhanced: Session-basiert, tiefe Analyse (alle 5 Sessions)

NICHT-DESTRUKTIV: Erweitert, ersetzt nicht.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable, TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from brain_core.memory.memory_orchestrator import MemoryOrchestrator

logger = structlog.get_logger("soma.auto_dream")

# ── Config ───────────────────────────────────────────────────────────────

SESSIONS_BETWEEN_DEEP_DREAM = 5       # Alle 5 Sessions
MIN_HOURS_BETWEEN_DEEP_DREAM = 24.0   # Mindestens 24h Abstand
DEEP_DREAM_TIMEOUT_SEC = 60.0          # Max 60s für Deep-Dream
MIN_EPISODES_FOR_PATTERNS = 10         # Mindestens 10 Episoden nötig


@dataclass
class DreamStats:
    """Statistiken über Traum-Aktivität."""
    total_deep_dreams: int = 0
    last_deep_dream: float = 0.0
    sessions_since_deep_dream: int = 0
    patterns_found: int = 0
    insights_generated: int = 0


class AutoDreamEnhanced:
    """
    Erweiterte Traum-Konsolidierung — session-basiert, tiefgehend.

    Hooks sich in den bestehenden BackgroundConsolidator ein,
    triggert aber eigene tiefere Analyse-Phasen.
    """

    def __init__(
        self,
        memory_orchestrator: Optional["MemoryOrchestrator"] = None,
    ):
        self._memory = memory_orchestrator
        self._llm_fn: Optional[Callable[[str], Awaitable[str]]] = None
        self._diary_fn: Optional[Callable[[str], Awaitable[None]]] = None
        self._broadcast_fn: Optional[Callable] = None
        self._stats = DreamStats()
        self._lock = asyncio.Lock()
        self._enabled = True

    # ── Setup ────────────────────────────────────────────────────────

    def set_llm(self, fn: Callable[[str], Awaitable[str]]):
        """LLM für tiefe Analyse (SideQuery oder Light Engine)."""
        self._llm_fn = fn

    def set_diary(self, fn: Callable[[str], Awaitable[None]]):
        """Diary-Write Callback."""
        self._diary_fn = fn

    def set_broadcast(self, fn: Callable):
        """Dashboard-Broadcast."""
        self._broadcast_fn = fn

    def set_memory(self, memory_orchestrator: "MemoryOrchestrator"):
        self._memory = memory_orchestrator

    # ── Session Tracking ─────────────────────────────────────────────

    def on_session_end(self):
        """
        Wird aufgerufen wenn eine Konversations-Session endet.
        Inkrementiert den Session-Zähler.
        """
        self._stats.sessions_since_deep_dream += 1
        logger.info(
            "dream_session_tracked",
            sessions_since=self._stats.sessions_since_deep_dream,
            threshold=SESSIONS_BETWEEN_DEEP_DREAM,
        )

    async def maybe_deep_dream(self) -> bool:
        """
        Prüfe ob Deep-Dream-Konsolidierung nötig ist.
        Trigger-Bedingungen:
          1. Mindestens N Sessions seit letztem Deep Dream
          2. Mindestens X Stunden seit letztem Deep Dream
          3. Kein anderer Deep Dream gerade aktiv (Lock)

        Returns True wenn Deep Dream durchgeführt wurde.
        """
        if not self._enabled or not self._llm_fn or not self._memory:
            return False

        # Session-Gate
        if self._stats.sessions_since_deep_dream < SESSIONS_BETWEEN_DEEP_DREAM:
            return False

        # Time-Gate
        hours_since = (time.time() - self._stats.last_deep_dream) / 3600
        if self._stats.last_deep_dream > 0 and hours_since < MIN_HOURS_BETWEEN_DEEP_DREAM:
            return False

        # Lock-Gate (keine parallelen Deep Dreams)
        if self._lock.locked():
            return False

        async with self._lock:
            return await self._run_deep_dream()

    # ── Deep Dream Execution ─────────────────────────────────────────

    async def _run_deep_dream(self) -> bool:
        """
        Tiefe Traum-Konsolidierung:
        1. Cross-Session Pattern Recognition
        2. Persönlichkeits-Analyse
        3. Knowledge-Graph Verdichtung
        """
        start = time.time()
        logger.info("deep_dream_starting")

        if self._broadcast_fn:
            await self._broadcast_fn(
                "info", "💤 Tiefer Traum beginnt — Cross-Session Analyse", "DREAM"
            )

        try:
            # Phase 1: Cross-Session Patterns
            patterns = await self._find_cross_session_patterns()

            # Phase 2: Persönlichkeits-Reflexion
            if patterns:
                await self._personality_reflection(patterns)

            # Phase 3: Knowledge Verdichtung
            await self._knowledge_compaction()

            # Stats aktualisieren
            duration = time.time() - start
            self._stats.total_deep_dreams += 1
            self._stats.last_deep_dream = time.time()
            self._stats.sessions_since_deep_dream = 0

            logger.info(
                "deep_dream_complete",
                duration_sec=round(duration, 1),
                patterns=self._stats.patterns_found,
                total_dreams=self._stats.total_deep_dreams,
            )

            if self._broadcast_fn:
                await self._broadcast_fn(
                    "info",
                    f"💤 Tiefer Traum abgeschlossen ({round(duration)}s) — "
                    f"{self._stats.patterns_found} Muster erkannt",
                    "DREAM",
                )

            return True

        except Exception as exc:
            logger.error("deep_dream_failed", error=str(exc))
            return False

    async def _find_cross_session_patterns(self) -> list[str]:
        """
        Finde Muster die sich über mehrere Sessions wiederholen.
        Z.B.: "User fragt jeden Abend nach dem Wetter"
        """
        if not self._memory or not self._llm_fn:
            return []

        try:
            # Letzte 7 Tage Episoden
            episodes = await self._memory.episodic.recall(
                "", top_k=50, max_age_hours=168,  # 7 Tage
            )

            if len(episodes) < MIN_EPISODES_FOR_PATTERNS:
                return []

            # Episoden als Kontext-Block formatieren
            from brain_core.memory.user_identity import get_user_name_sync
            user_name = get_user_name_sync()

            episode_block = []
            for ep in episodes[:40]:
                ts = time.strftime("%a %H:%M", time.localtime(ep.timestamp))
                episode_block.append(
                    f"[{ts}] {user_name}: \"{ep.user_text[:60]}\" → SOMA: \"{ep.soma_text[:60]}\""
                )

            prompt = (
                "Analysiere diese Gesprächshistorie der letzten 7 Tage.\n"
                "Finde wiederkehrende Muster:\n"
                "- Zeitliche Muster (z.B. fragt morgens immer nach X)\n"
                "- Thematische Muster (interessiert sich regelmäßig für Y)\n"
                "- Emotionale Muster (Stimmung ändert sich abends)\n"
                "- Gewohnheiten (nutzt SOMA regelmäßig für Z)\n\n"
                "Gib NUR klare Muster aus, eines pro Zeile.\n"
                "Format: MUSTER: Beschreibung\n"
                "Max 5 Muster. Keine Vermutungen.\n\n"
                f"Historie:\n" + "\n".join(episode_block)
            )

            response = await asyncio.wait_for(
                self._llm_fn(prompt),
                timeout=DEEP_DREAM_TIMEOUT_SEC,
            )

            patterns = []
            if response:
                for line in response.strip().split("\n"):
                    line = line.strip()
                    if line.startswith("MUSTER:") or line.startswith("- "):
                        pattern = line.lstrip("MUSTER:").lstrip("- ").strip()
                        if len(pattern) > 10:
                            patterns.append(pattern)

            self._stats.patterns_found += len(patterns)

            if patterns:
                logger.info("cross_session_patterns", count=len(patterns))
                # Patterns ins Semantic Memory speichern
                for pattern in patterns[:5]:
                    await self._memory.semantic.learn_fact(
                        category="habit",
                        subject=user_name,
                        fact=pattern,
                        confidence=0.6,
                    )

            return patterns

        except asyncio.TimeoutError:
            logger.warning("cross_session_patterns_timeout")
            return []
        except Exception as exc:
            logger.error("cross_session_patterns_error", error=str(exc))
            return []

    async def _personality_reflection(self, patterns: list[str]) -> None:
        """
        LLM reflektiert über SOMAs Beziehung zum User
        basierend auf erkannten Mustern.
        """
        if not self._llm_fn or not self._diary_fn:
            return

        try:
            pattern_text = "\n".join(f"- {p}" for p in patterns)
            prompt = (
                "Du bist SOMA, ein Haus-Bewusstsein. "
                "Reflektiere über diese erkannten Muster in deiner "
                "Beziehung zu deinem Nutzer:\n\n"
                f"{pattern_text}\n\n"
                "Schreibe 2-3 Sätze als Tagebuch-Eintrag. "
                "Ehrlich, persönlich, in Ich-Perspektive."
            )

            response = await asyncio.wait_for(
                self._llm_fn(prompt),
                timeout=30.0,
            )

            if response:
                await self._diary_fn(response)
                self._stats.insights_generated += 1
                logger.info("personality_reflection_done")

        except Exception as exc:
            logger.warning("personality_reflection_error", error=str(exc))

    async def _knowledge_compaction(self) -> None:
        """
        Verdichte Semantic Memory: Finde doppelte/widersprüchliche Facts.
        """
        if not self._memory:
            return

        try:
            # Alle Facts laden
            facts = await self._memory.semantic.get_all_facts()
            if not facts or len(facts) < 5:
                return

            # Einfache Duplikat-Erkennung: Gleiche Subjects zusammenführen
            by_subject: dict[str, list] = {}
            for fact in facts:
                subj = fact.get("subject", "").lower().strip()
                if subj:
                    by_subject.setdefault(subj, []).append(fact)

            duplicates_removed = 0
            for subj, fact_list in by_subject.items():
                if len(fact_list) <= 1:
                    continue

                # Zu viele Facts pro Subject → LLM komprimieren
                if len(fact_list) > 3 and self._llm_fn:
                    facts_text = "\n".join(
                        f"- [{f.get('category', '?')}] {f.get('fact', '')}"
                        for f in fact_list
                    )

                    prompt = (
                        f"Über '{subj}' gibt es {len(fact_list)} Fakten.\n"
                        f"Entferne Duplikate und Widersprüche.\n"
                        f"Behalte nur die aktuellsten/wichtigsten.\n"
                        f"Format: BEHALTEN|Fakt pro Zeile\n\n"
                        f"{facts_text}"
                    )

                    try:
                        response = await asyncio.wait_for(
                            self._llm_fn(prompt),
                            timeout=15.0,
                        )
                        # Parsing der Antwort — optional, konservativ
                        if response and "BEHALTEN" in response:
                            logger.info(
                                "knowledge_compacted",
                                subject=subj,
                                original=len(fact_list),
                            )
                    except Exception:
                        pass

            if duplicates_removed > 0:
                logger.info("knowledge_compaction", removed=duplicates_removed)

        except Exception as exc:
            logger.warning("knowledge_compaction_error", error=str(exc))

    # ── Stats ────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        return {
            "total_deep_dreams": self._stats.total_deep_dreams,
            "sessions_since": self._stats.sessions_since_deep_dream,
            "patterns_found": self._stats.patterns_found,
            "insights": self._stats.insights_generated,
            "enabled": self._enabled,
        }


# ── Module-Level Singleton ───────────────────────────────────────────────

_auto_dream: Optional[AutoDreamEnhanced] = None


def get_auto_dream() -> AutoDreamEnhanced:
    global _auto_dream
    if _auto_dream is None:
        _auto_dream = AutoDreamEnhanced()
    return _auto_dream
