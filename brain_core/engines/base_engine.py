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
from typing import Optional, AsyncGenerator
from dataclasses import dataclass, field
import hashlib

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

    Phase E: KV-Cache-Strategie
    - static_system_prompt: Cacheable prefix (Persona, Action-Tags, Sicherheit)
    - dynamic_context: Volatile context (Consciousness, Memory, Emotion)
    - Ollama erkennt identische Prefixes → KV-Cache Hit → kein Re-Processing
    """
    session_id: str
    user_id: Optional[str] = None
    room_id: Optional[str] = None
    history: list[ConversationTurn] = field(default_factory=list)
    system_prompt: str = ""
    max_history: int = 20

    # Phase E: Cache-Aware Prompt Splitting
    static_system_prompt: str = ""
    dynamic_context: str = ""
    _static_hash: str = ""
    _last_stale_check: float = 0.0

    def add_turn(self, role: str, content: str) -> None:
        self.history.append(ConversationTurn(role=role, content=content))
        # Sliding window – behalte System-Prompt + letzte N Turns
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

    def set_split_prompt(self, static: str, dynamic: str) -> None:
        """Phase E: Setze statischen und dynamischen Teil getrennt.
        
        Der statische Teil wird gehashed — solange er identisch bleibt,
        kann Ollama den KV-Cache des System-Prompts wiederverwenden.
        """
        new_hash = hashlib.md5(static.encode()).hexdigest()[:12]
        if new_hash != self._static_hash:
            self._static_hash = new_hash
            self.static_system_prompt = static
            logger.debug(
                "session_static_prompt_changed",
                session=self.session_id,
                hash=new_hash,
                length=len(static),
            )
        self.dynamic_context = dynamic

    def to_messages(self, system_prompt: Optional[str] = None) -> list[dict]:
        """Konvertiere zu Ollama-kompatiblem Messages-Format.
        
        Phase E: Wenn split prompt gesetzt ist, wird der statische Teil
        als ERSTE system-Message gesendet (cache-friendly) und der
        dynamische Teil als ZWEITE system-Message (ändert sich pro Turn).
        
        Ollama matcht Prefixes im KV-Cache: Wenn die erste system-Message
        identisch ist, wird der KV-Cache des statischen Teils wiederverwendet.
        """
        messages = []

        # Phase E: Split Prompt für KV-Cache Optimierung
        if self.static_system_prompt:
            messages.append({"role": "system", "content": self.static_system_prompt})
            if self.dynamic_context:
                messages.append({"role": "system", "content": self.dynamic_context})
        else:
            # Fallback: Alles in einer Message (Legacy-Verhalten)
            sp = system_prompt or self.system_prompt
            if sp:
                messages.append({"role": "system", "content": sp})

        for turn in self.history:
            messages.append({"role": turn.role, "content": turn.content})
        return messages

    def trim_stale(self, max_idle_turns: int = 6) -> None:
        """Phase E: Trimme alte History bei Stale Sessions.
        
        Reduziert KV-Cache Verbrauch bei inaktiven Sessions
        (z.B. Raum wurde vor 5 Minuten verlassen).
        """
        if len(self.history) > max_idle_turns:
            self.history = self.history[-max_idle_turns:]


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

    async def generate_stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Streame Antwort Token für Token.

        Default-Implementierung: Ruft generate() auf und yielded alles als
        einen einzigen Chunk (Rückwärtskompatibilität für Nano etc.).

        Engines die Streaming unterstützen (Heavy, Light) überschreiben das.
        """
        result = await self.generate(prompt, system_prompt, session_id)
        yield result

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
