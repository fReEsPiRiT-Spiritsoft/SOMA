"""
SOMA-AI Presence Manager
==========================
Triangulation: Wer spricht wo?
Steuert Raum-Wanderung (Seamless Handover) und Multi-Session.

Datenfluss:
  AudioChunkMeta (node_id, amplitude_rms) ──► PresenceManager.update()
                                                    │
                                                    ├─ Amplitude-Vergleich über Räume
                                                    ├─ Confidence-Berechnung
                                                    └─ PresenceEvent ──► audio_router
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional, Callable, Awaitable

import structlog

from shared.audio_types import PresenceEvent, AudioChunkMeta

logger = structlog.get_logger("soma.presence")


class UserPresence:
    """Tracking-State für einen Nutzer."""

    def __init__(self, user_id: str, room_id: str):
        self.user_id = user_id
        self.current_room: str = room_id
        self.previous_room: Optional[str] = None
        self.session_id: Optional[str] = None
        self.last_activity: float = time.monotonic()
        self.room_amplitudes: dict[str, float] = {}  # room_id → letzte amplitude


class PresenceManager:
    """
    Verwaltet Nutzer-Positionen basierend auf Audio-Amplitude / RSSI.
    Triggert PresenceEvents für den AudioRouter.
    """

    def __init__(
        self,
        handover_threshold: float = 0.7,
        inactivity_timeout: float = 300.0,
        on_presence_change: Optional[
            Callable[[PresenceEvent], Awaitable[None]]
        ] = None,
    ):
        self.handover_threshold = handover_threshold
        self.inactivity_timeout = inactivity_timeout
        self._on_presence_change = on_presence_change
        self._users: dict[str, UserPresence] = {}
        self._room_activity: dict[str, float] = {}  # room_id → last activity time
        self._lock = asyncio.Lock()

    # ── Core API ─────────────────────────────────────────────────────────

    async def update_audio(self, chunk: AudioChunkMeta, user_id: str) -> None:
        """
        Audio-Chunk erhalten → Position aktualisieren.
        Wird von jedem Mikrofon-Node aufgerufen.
        """
        async with self._lock:
            presence = self._users.get(user_id)
            if not presence:
                presence = UserPresence(user_id=user_id, room_id=chunk.room_id)
                self._users[user_id] = presence
                logger.info("user_discovered", user_id=user_id, room=chunk.room_id)

            # Amplitude für diesen Raum speichern
            presence.room_amplitudes[chunk.room_id] = chunk.amplitude_rms
            presence.last_activity = time.monotonic()
            self._room_activity[chunk.room_id] = time.monotonic()

            # Raum-Wechsel prüfen
            best_room = self._detect_room(presence)
            if best_room and best_room != presence.current_room:
                await self._handover(presence, best_room)

    def _detect_room(self, presence: UserPresence) -> Optional[str]:
        """
        Bestimme den wahrscheinlichsten Raum basierend auf Amplituden.
        Höchste Amplitude = Nutzer ist dort am nächsten.
        """
        if not presence.room_amplitudes:
            return None

        best_room = max(
            presence.room_amplitudes,
            key=presence.room_amplitudes.get,  # type: ignore[arg-type]
        )
        best_amp = presence.room_amplitudes[best_room]
        current_amp = presence.room_amplitudes.get(presence.current_room, 0.0)

        # Hysterese: Wechsel nur bei deutlichem Unterschied
        if best_room != presence.current_room:
            if current_amp > 0 and best_amp / max(current_amp, 0.001) > 1.5:
                return best_room
            elif current_amp == 0 and best_amp > 0.01:
                return best_room

        return None

    async def _handover(self, presence: UserPresence, new_room: str) -> None:
        """Seamless Handover: Session wandert mit."""
        event = PresenceEvent(
            user_id=presence.user_id,
            from_room=presence.current_room,
            to_room=new_room,
            confidence=self.handover_threshold,
            detection_method="audio_amplitude",
        )

        presence.previous_room = presence.current_room
        presence.current_room = new_room

        logger.info(
            "handover",
            user_id=presence.user_id,
            from_room=event.from_room,
            to_room=event.to_room,
        )

        if self._on_presence_change:
            await self._on_presence_change(event)

    # ── Queries ──────────────────────────────────────────────────────────

    def get_user_room(self, user_id: str) -> Optional[str]:
        presence = self._users.get(user_id)
        return presence.current_room if presence else None

    def get_room_users(self, room_id: str) -> list[str]:
        return [
            uid
            for uid, p in self._users.items()
            if p.current_room == room_id
        ]

    def get_active_rooms(self) -> list[str]:
        now = time.monotonic()
        return [
            room_id
            for room_id, last in self._room_activity.items()
            if now - last < self.inactivity_timeout
        ]

    async def cleanup_inactive(self) -> None:
        """Entferne inaktive Nutzer."""
        async with self._lock:
            now = time.monotonic()
            inactive = [
                uid
                for uid, p in self._users.items()
                if now - p.last_activity > self.inactivity_timeout
            ]
            for uid in inactive:
                del self._users[uid]
                logger.info("user_inactive_removed", user_id=uid)
