"""
Auto-Compact — Intelligente Context-Kompression.
=================================================
Inspiriert von Claude Code's autoCompact.ts:
Verhindert Context-Overflow durch automatische Zusammenfassung
alter Konversations-Turns wenn das Token-Limit sich nähert.

Architektur:
  WorkingMemory (L1)
       │
       ├─ Turns accumulate...
       │
       ├─ Token count > Threshold? ──► compact()
       │                                   │
       │                                   ├─ Alte Turns → SideQuery summarize
       │                                   ├─ Summary → als kompakter Turn
       │                                   └─ Alte Turns löschen
       │
       └─ Circuit Breaker: 3 Failures → stop

SOMA-spezifisch:
  - Nutzt SideQueryEngine (Light-Modell) für Kompression
  - Arbeitet auf WorkingMemory Turns (Ringbuffer)
  - Non-blocking → blockiert nie den Haupt-Loop
  - Sprachassistent-optimiert: Erhält Konversations-Kontext
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import structlog

logger = structlog.get_logger("soma.auto_compact")


# ── Konfiguration ────────────────────────────────────────────────────────

@dataclass
class CompactConfig:
    """Auto-Compact Schwellwerte."""
    # Token-Schätzung: ~3 chars = 1 token
    chars_per_token: int = 3

    # Context-Limits (in geschätzten Tokens)
    # Qwen3 8B hat 32K Context, wir nutzen konservativ
    max_context_tokens: int = 24_000

    # Compact auslösen wenn X Tokens verbraucht
    compact_threshold_tokens: int = 18_000   # ~75% von max

    # Warning ab diesem Level
    warning_threshold_tokens: int = 20_000   # ~83%

    # Harte Grenze — Turns werden gedropt
    blocking_threshold_tokens: int = 23_000  # ~96%

    # Mindest-Turns die nach Compact bleiben
    min_turns_after_compact: int = 4

    # Zusammenfassung maximal X Zeichen
    summary_max_chars: int = 800

    # Circuit Breaker
    max_consecutive_failures: int = 3


# ── State ────────────────────────────────────────────────────────────────

@dataclass
class CompactState:
    """Tracking-State für Auto-Compact."""
    compactions_count: int = 0
    last_compaction_time: float = 0.0
    consecutive_failures: int = 0
    total_tokens_saved: int = 0
    is_disabled: bool = False


class AutoCompact:
    """
    Automatische Context-Kompression für SOMAs Working Memory.
    Nutzt die SideQueryEngine für schnelle Zusammenfassungen.
    """

    def __init__(self, config: Optional[CompactConfig] = None):
        self.config = config or CompactConfig()
        self.state = CompactState()

    def estimate_tokens(self, text: str) -> int:
        """Grobe Token-Schätzung basierend auf Zeichenlänge."""
        return len(text) // self.config.chars_per_token

    def estimate_turns_tokens(self, turns: list) -> int:
        """Schätze Token-Verbrauch aller Turns."""
        total = 0
        for turn in turns:
            text = getattr(turn, "text", str(turn))
            total += self.estimate_tokens(text)
        return total

    def calculate_state(self, current_tokens: int) -> dict:
        """
        Berechne den aktuellen Context-Status.
        Wie Claude Code's calculateTokenWarningState.
        """
        max_tokens = self.config.max_context_tokens
        threshold = self.config.compact_threshold_tokens

        percent_left = max(
            0, round(((max_tokens - current_tokens) / max_tokens) * 100)
        )

        return {
            "current_tokens": current_tokens,
            "max_tokens": max_tokens,
            "percent_left": percent_left,
            "above_warning": current_tokens >= self.config.warning_threshold_tokens,
            "above_compact": current_tokens >= threshold,
            "at_blocking": current_tokens >= self.config.blocking_threshold_tokens,
            "compact_disabled": self.state.is_disabled,
        }

    async def compact_if_needed(
        self,
        working_memory,
        side_query_engine=None,
    ) -> Optional[str]:
        """
        Prüfe ob Kompression nötig ist und führe sie ggf. durch.

        Args:
            working_memory: WorkingMemory-Instanz mit _turns
            side_query_engine: SideQueryEngine für Zusammenfassung

        Returns:
            Summary-Text wenn komprimiert wurde, sonst None
        """
        if self.state.is_disabled:
            return None

        # Circuit Breaker
        if self.state.consecutive_failures >= self.config.max_consecutive_failures:
            logger.warning("auto_compact_circuit_breaker", failures=self.state.consecutive_failures)
            self.state.is_disabled = True
            return None

        turns = list(working_memory._turns)
        if len(turns) <= self.config.min_turns_after_compact:
            return None

        current_tokens = self.estimate_turns_tokens(turns)
        state = self.calculate_state(current_tokens)

        if not state["above_compact"]:
            return None

        logger.info(
            "auto_compact_triggered",
            tokens=current_tokens,
            turns=len(turns),
            percent_left=state["percent_left"],
        )

        # ── Compact ausführen ────────────────────────────────────────
        try:
            # Alte Turns die komprimiert werden (alle außer den letzten N)
            keep_count = self.config.min_turns_after_compact
            old_turns = turns[:-keep_count]
            recent_turns = turns[-keep_count:]

            # Zusammenfassung erstellen
            summary = await self._create_summary(old_turns, side_query_engine)

            if not summary:
                self.state.consecutive_failures += 1
                return None

            # Working Memory aktualisieren
            working_memory._turns.clear()

            # Summary als System-Turn vorn einfügen
            from brain_core.memory.working_memory import Turn
            working_memory._turns.append(Turn(
                role="soma",
                text=f"[Zusammenfassung bisheriger Konversation]\n{summary}",
            ))

            # Letzte Turns wieder einfügen
            for turn in recent_turns:
                working_memory._turns.append(turn)

            # Stats aktualisieren
            old_tokens = self.estimate_turns_tokens(old_turns)
            summary_tokens = self.estimate_tokens(summary)
            self.state.compactions_count += 1
            self.state.last_compaction_time = time.time()
            self.state.consecutive_failures = 0
            self.state.total_tokens_saved += old_tokens - summary_tokens

            logger.info(
                "auto_compact_done",
                old_turns=len(old_turns),
                kept_turns=keep_count,
                tokens_saved=old_tokens - summary_tokens,
                new_total=self.estimate_turns_tokens(list(working_memory._turns)),
            )

            return summary

        except Exception as exc:
            self.state.consecutive_failures += 1
            logger.error("auto_compact_error", error=str(exc))
            return None

    async def _create_summary(
        self,
        turns: list,
        side_query_engine=None,
    ) -> Optional[str]:
        """Erstelle Zusammenfassung alter Turns."""
        # Turns als Text formatieren
        text_parts = []
        for turn in turns:
            role = getattr(turn, "role", "?")
            text = getattr(turn, "text", str(turn))
            prefix = "User" if role == "user" else "SOMA"
            text_parts.append(f"{prefix}: {text}")

        conversation_text = "\n".join(text_parts)

        # Kürzen wenn zu lang für Side Query
        max_input = 3000
        if len(conversation_text) > max_input:
            conversation_text = conversation_text[-max_input:]

        # Side Query für Zusammenfassung nutzen
        if side_query_engine:
            result = await side_query_engine.summarize(
                text=conversation_text,
                max_sentences=4,
                focus="Behalte wichtige Fakten, User-Wünsche und offene Tasks. Kurz und knapp.",
            )
            if result and len(result) > 20:
                # Auf max Länge kürzen
                return result[:self.config.summary_max_chars]

        # Fallback ohne SideQuery: Naive Kompression
        return self._naive_compress(turns)

    def _naive_compress(self, turns: list) -> str:
        """
        Fallback: Einfache Kompression ohne LLM.
        Behält nur Schlüssel-Informationen.
        """
        parts = []
        for turn in turns:
            text = getattr(turn, "text", str(turn))
            role = getattr(turn, "role", "?")
            # Nur erste 60 Zeichen pro Turn
            short = text[:60].replace("\n", " ")
            if len(text) > 60:
                short += "..."
            parts.append(f"{'U' if role == 'user' else 'S'}: {short}")

        return "\n".join(parts[-8:])  # Max 8 komprimierte Turns

    def force_trim(self, working_memory, keep_last: int = 4) -> int:
        """
        Harte Notfall-Trimming wenn Blocking-Limit erreicht.
        Löscht alte Turns ohne Zusammenfassung.
        """
        turns = list(working_memory._turns)
        if len(turns) <= keep_last:
            return 0

        removed = len(turns) - keep_last
        working_memory._turns.clear()
        for turn in turns[-keep_last:]:
            working_memory._turns.append(turn)

        logger.warning("force_trim_executed", removed=removed, kept=keep_last)
        return removed

    @property
    def stats(self) -> dict:
        return {
            "compactions": self.state.compactions_count,
            "tokens_saved": self.state.total_tokens_saved,
            "failures": self.state.consecutive_failures,
            "disabled": self.state.is_disabled,
        }


# ── Module-Level Singleton ───────────────────────────────────────────────

_instance: Optional[AutoCompact] = None


def get_auto_compact() -> AutoCompact:
    global _instance
    if _instance is None:
        _instance = AutoCompact()
    return _instance
