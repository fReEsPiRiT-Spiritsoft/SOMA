"""
SOMA-AI Base Engine
====================
Abstrakte Klasse für alle Intelligenz-Layer.
Definiert das Interface das LogicRouter erwartet.
Session-State wird ENGINE-UNABHÄNGIG gehalten,
damit nahtloses Umschalten möglich ist.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger("soma.engine")


@dataclass
class ConversationTurn:
    """Ein einzelner Gesprächs-Turn."""
    role: str  # "user" | "assistant" | "system"
    content: str


@dataclass
class SessionState:
    """
    Engine-unabhängiger Session-State.
    Wird vom LogicRouter verwaltet, nicht von der Engine selbst.
    Erlaubt nahtloses Engine-Switching.
    """
    session_id: str
    user_id: Optional[str] = None
    room_id: Optional[str] = None
    history: list[ConversationTurn] = field(default_factory=list)
    system_prompt: str = ""
    max_history: int = 20

    def add_turn(self, role: str, content: str) -> None:
        self.history.append(ConversationTurn(role=role, content=content))
        # Sliding window – behalte System-Prompt + letzte N Turns
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

    def to_messages(self, system_prompt: Optional[str] = None) -> list[dict]:
        """Konvertiere zu Ollama-kompatiblem Messages-Format."""
        messages = []
        sp = system_prompt or self.system_prompt
        if sp:
            messages.append({"role": "system", "content": sp})
        for turn in self.history:
            messages.append({"role": turn.role, "content": turn.content})
        return messages


class BaseEngine(ABC):
    """
    Abstract Base für SOMA Engines.
    Jede Engine muss generate() implementieren.
    """

    def __init__(self, name: str):
        self.name = name
        self._sessions: dict[str, SessionState] = {}

    async def initialize(self) -> None:
        """Optional: Engine-spezifische Initialisierung."""
        logger.info("engine_initialized", engine=self.name)

    async def shutdown(self) -> None:
        """Optional: Cleanup."""
        logger.info("engine_shutdown", engine=self.name)

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """
        Generiere eine Antwort.

        Args:
            prompt: User-Eingabe
            system_prompt: System-Prompt (überschreibt Session-Default)
            session_id: Session für Kontext-Kontinuität

        Returns:
            Antwort-Text
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Ist die Engine verfügbar?"""
        ...

    # ── Session Management ───────────────────────────────────────────────

    def get_or_create_session(
        self,
        session_id: str,
        user_id: Optional[str] = None,
        system_prompt: str = "",
    ) -> SessionState:
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionState(
                session_id=session_id,
                user_id=user_id,
                system_prompt=system_prompt,
            )
        return self._sessions[session_id]

    def drop_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
