"""
L1: Working Memory — Immer im RAM, immer im Prompt.
Die letzten N Turns + aktuelle Stimmung + Tageskontext.
Ultra-schnell (0ms), kein DB-Zugriff.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from collections import deque
from typing import Optional


@dataclass
class Turn:
    role: str          # "user" | "soma"
    text: str
    timestamp: float = field(default_factory=time.time)
    emotion: Optional[str] = None
    intent: Optional[str] = None


class WorkingMemory:
    """
    Ringbuffer der letzten Interaktionen.
    Flüchtig wie echtes Kurzzeit-Gedächtnis — nie persistiert.
    """

    def __init__(self, max_turns: int = 10):
        self._turns: deque[Turn] = deque(maxlen=max_turns)
        self._current_emotion: str = "neutral"
        self._current_intent: str = ""
        self._user_name: str = "Patrick"
        self._session_start: float = time.time()
        self._interaction_count: int = 0
        self._last_topic: str = ""
        self._active_context: dict = {}

    # ── Turns ────────────────────────────────────────────────────────

    def add_user_turn(self, text: str, emotion: str = "neutral", intent: str = ""):
        self._turns.append(Turn(
            role="user", text=text, emotion=emotion, intent=intent,
        ))
        self._current_emotion = emotion
        self._current_intent = intent
        self._interaction_count += 1
        self._last_topic = text[:120]

    def add_soma_turn(self, text: str):
        self._turns.append(Turn(role="soma", text=text))

    # ── Context Keys ─────────────────────────────────────────────────

    def set_context(self, key: str, value):
        self._active_context[key] = value

    def get_context(self, key: str, default=None):
        return self._active_context.get(key, default)

    # ── Prompt-Block ─────────────────────────────────────────────────

    def get_conversation_block(self, max_tokens_estimate: int = 1500) -> str:
        """
        Letzte Turns als formatierter String.
        Respektiert ein grobes Token-Limit.
        """
        lines: list[str] = []
        char_budget = max_tokens_estimate * 3  # ~3 chars/token
        used = 0

        for turn in reversed(self._turns):
            prefix = "Patrick" if turn.role == "user" else "SOMA"
            line = f"{prefix}: {turn.text}"
            if used + len(line) > char_budget:
                break
            lines.insert(0, line)
            used += len(line)

        return "\n".join(lines)

    def get_session_summary(self) -> dict:
        """Kompakter Zustandsbericht für Prompt-Injection."""
        elapsed = time.time() - self._session_start
        minutes = int(elapsed // 60)
        return {
            "user": self._user_name,
            "emotion": self._current_emotion,
            "intent": self._current_intent,
            "topic": self._last_topic,
            "turns_count": self._interaction_count,
            "session_minutes": minutes,
            "active_context": self._active_context,
        }

    # ── Utilities ────────────────────────────────────────────────────

    def clear(self):
        self._turns.clear()
        self._interaction_count = 0
        self._session_start = time.time()
        self._active_context.clear()

    @property
    def is_fresh_session(self) -> bool:
        return self._interaction_count == 0

    @property
    def last_user_text(self) -> str:
        for turn in reversed(self._turns):
            if turn.role == "user":
                return turn.text
        return ""

    @property
    def last_soma_text(self) -> str:
        for turn in reversed(self._turns):
            if turn.role == "soma":
                return turn.text
        return ""
