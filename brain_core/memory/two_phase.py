"""
Two-Phase Response — Sofortige Überbrückung + Deep Follow-up.
Phase 1: < 300ms Bridge (nur wenn LLM > 1.5s braucht)
Phase 2: Full LLM mit Kontext
"""

from __future__ import annotations

import random
import asyncio
import logging
from typing import Optional, Callable, Awaitable

logger = logging.getLogger("soma.twophase")


# ── Bridge-Pools (kontextsensitiv, nie repetitiv) ────────────────────

BRIDGES = {
    "default": [
        "Moment...",
        "Lass mich kurz nachdenken...",
        "Hmm...",
        "Gute Frage...",
        "Sekunde...",
    ],
    "complex": [
        "Das ist ne interessante Frage, gib mir einen Moment...",
        "Da muss ich kurz meine Gedanken sortieren...",
        "Lass mich da kurz tiefer einsteigen...",
    ],
    "action": [
        "Mach ich...",
        "Alles klar...",
        "Wird erledigt...",
        "Geht klar...",
    ],
    "emotional": [
        "Ich hör dir zu...",
        "Verstehe...",
        "Ich bin da...",
    ],
}

_recent_bridges: list[str] = []


def get_bridge_response(
    intent: str = "default",
    emotion: str = "neutral",
) -> Optional[str]:
    """
    Kurze Überbrückungs-Antwort.
    None für direkte Befehle die der Nano-Handler abfängt.
    """
    global _recent_bridges

    # Direkte Befehle brauchen keine Bridge
    if intent in ("light_control", "timer", "volume", "skip"):
        return None

    # Pool wählen
    if emotion in ("stressed", "sad", "angry"):
        pool = BRIDGES["emotional"]
    elif intent in ("question", "complex", "philosophy"):
        pool = BRIDGES["complex"]
    elif intent in ("action", "command"):
        pool = BRIDGES["action"]
    else:
        pool = BRIDGES["default"]

    # Nicht repetitiv
    available = [b for b in pool if b not in _recent_bridges]
    if not available:
        _recent_bridges.clear()
        available = pool

    choice = random.choice(available)
    _recent_bridges.append(choice)
    if len(_recent_bridges) > 5:
        _recent_bridges.pop(0)

    return choice


class TwoPhaseResponder:
    """
    Orchestriert Bridge + Deep Response.

    Wenn das LLM in < 1.5s antwortet → keine Bridge, direkte Antwort.
    Wenn es länger braucht → Bridge sofort sprechen, dann volle Antwort.
    """

    def __init__(
        self,
        speak_callback: Callable[[str], Awaitable[None]],
        llm_callback: Callable[[str, str], Awaitable[str]],
    ):
        self._speak = speak_callback
        self._llm = llm_callback
        self._phase1_threshold_ms: float = 1500

    async def respond(
        self,
        user_text: str,
        system_prompt: str,
        intent: str = "default",
        emotion: str = "neutral",
    ) -> str:
        """
        Phase 1: Bridge sofort sprechen (wenn LLM zu langsam)
        Phase 2: Deep response sprechen
        Returns: volle LLM-Antwort
        """
        llm_task = asyncio.create_task(
            self._llm(system_prompt, user_text),
        )

        try:
            result = await asyncio.wait_for(
                asyncio.shield(llm_task),
                timeout=self._phase1_threshold_ms / 1000,
            )
            # LLM war schnell genug
            return result

        except asyncio.TimeoutError:
            # LLM braucht länger → Bridge
            bridge = get_bridge_response(intent, emotion)
            if bridge:
                asyncio.create_task(self._speak(bridge))

        # Auf echte Antwort warten
        return await llm_task
