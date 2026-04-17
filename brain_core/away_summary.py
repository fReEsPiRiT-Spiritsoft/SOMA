"""
Away Summary — "Während du weg warst" Zusammenfassung.
======================================================
Inspiriert von Claude Code's useAwaySummary:
Wenn der User 5+ Minuten nicht interagiert hat, wird eine kurze
Zusammenfassung generiert die ihm beim Wiedereinstieg hilft.

SOMA-spezifisch:
  - Erkennung via Voice Pipeline (kein Terminal-Focus wie Claude Code)
  - Nutzt SideQuery (Light-Modell) — blockiert Heavy nicht
  - Zusammenfassung wird gesprochen (TTS-optimiert)
  - Berücksichtigt Session Memory + letzte Turns
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional, Callable, Awaitable

import structlog

logger = structlog.get_logger("soma.away_summary")

# ── Konfiguration ────────────────────────────────────────────────────────

AWAY_THRESHOLD_SEC: float = 300.0   # 5 Minuten
MAX_RECENT_MESSAGES: int = 30       # Letzte 30 Messages für Kontext
SUMMARY_MAX_TOKENS: int = 200       # Kurze, sprechbare Zusammenfassung

SUMMARY_SYSTEM_PROMPT = """Du bist SOMA und fasst zusammen was in der letzten Konversation passiert ist.
Der Nutzer war weg und kommt gerade zurück.

Regeln:
- Schreibe EXAKT 1-2 kurze Sätze
- Nenne den übergeordneten Task/das Thema
- Nenne den nächsten konkreten Schritt (falls vorhanden)
- Keine Status-Reports oder technische Details
- Sprich den Nutzer direkt an ("Wir haben über X gesprochen...")
- Sei natürlich und warm, nicht formell"""


class AwaySummaryGenerator:
    """
    Generiert "Während du weg warst" Zusammenfassungen.
    """

    def __init__(self):
        self._last_interaction_time: float = time.time()
        self._last_summary_time: float = 0.0
        self._pending: bool = False
        self._generating: bool = False
        self._last_summary: str = ""
        self._speak_fn: Optional[Callable[[str], Awaitable[None]]] = None

    def set_speak(self, fn: Callable[[str], Awaitable[None]]) -> None:
        """Setzt die TTS-Speak-Funktion fuer autonome Ansagen."""
        self._speak_fn = fn
        logger.info("away_summary_speak_connected")

    def touch(self) -> None:
        """Bei jeder User-Interaktion → Timer reset."""
        self._last_interaction_time = time.time()
        self._pending = False

    @property
    def is_away(self) -> bool:
        """Ist der User gerade weg?"""
        return (time.time() - self._last_interaction_time) > AWAY_THRESHOLD_SEC

    @property
    def away_duration_sec(self) -> float:
        """Wie lange ist der User schon weg?"""
        return time.time() - self._last_interaction_time

    async def maybe_generate(
        self,
        working_memory=None,
        side_query_engine=None,
        session_memory_text: str = "",
    ) -> Optional[str]:
        """
        Generiere Away-Summary wenn User lange genug weg war.

        Returns:
            Summary-Text oder None
        """
        if not self.is_away:
            return None

        if self._generating:
            return None

        # Nicht mehrfach generieren
        if self._last_summary_time > self._last_interaction_time:
            return None

        if not side_query_engine or not working_memory:
            return None

        self._generating = True
        try:
            # Working Memory Turns als Messages
            turns = list(working_memory._turns)
            if not turns:
                return None

            recent = turns[-MAX_RECENT_MESSAGES:]
            text_parts = []
            for turn in recent:
                role = getattr(turn, "role", "?")
                text = getattr(turn, "text", str(turn))
                prefix = "User" if role == "user" else "SOMA"
                text_parts.append(f"{prefix}: {text}")

            conversation = "\n".join(text_parts)

            # Session Memory als Kontext
            user_msg = conversation
            if session_memory_text:
                user_msg = f"Session-Kontext: {session_memory_text}\n\nKonversation:\n{conversation}"

            result = await side_query_engine.query(
                system=SUMMARY_SYSTEM_PROMPT,
                user_message=user_msg,
                max_tokens=SUMMARY_MAX_TOKENS,
                temperature=0.5,
            )

            if result.success and result.text:
                self._last_summary = result.text
                self._last_summary_time = time.time()
                logger.info(
                    "away_summary_generated",
                    away_sec=round(self.away_duration_sec),
                    summary=result.text[:80],
                    ms=round(result.latency_ms),
                )
                return result.text

            return None

        except Exception as exc:
            logger.warning("away_summary_error", error=str(exc))
            return None
        finally:
            self._generating = False

    async def get_welcome_back_text(
        self,
        working_memory=None,
        side_query_engine=None,
        session_memory_text: str = "",
    ) -> Optional[str]:
        """
        Generiere einen natürlichen Willkommensgruß wenn der User zurückkommt.
        Wird von der VoicePipeline aufgerufen bei Wake-Word nach Pause.
        """
        summary = await self.maybe_generate(
            working_memory=working_memory,
            side_query_engine=side_query_engine,
            session_memory_text=session_memory_text,
        )

        if not summary:
            # Kurzer Gruß ohne Summary
            away_min = int(self.away_duration_sec / 60)
            if away_min > 30:
                text = "Hey, schön dass du wieder da bist!"
                await self._maybe_speak(text)
                return text
            return None

        text = f"Hey! {summary}"
        await self._maybe_speak(text)
        return text

    async def _maybe_speak(self, text: str) -> None:
        """Spricht Text via TTS wenn speak_fn gesetzt."""
        if self._speak_fn and text:
            try:
                await self._speak_fn(text)
            except Exception as exc:
                logger.warning("away_summary_speak_error", error=str(exc))

    @property
    def last_summary(self) -> str:
        return self._last_summary


# ── Module-Level Singleton ───────────────────────────────────────────────

_generator: Optional[AwaySummaryGenerator] = None


def get_away_summary() -> AwaySummaryGenerator:
    global _generator
    if _generator is None:
        _generator = AwaySummaryGenerator()
    return _generator
