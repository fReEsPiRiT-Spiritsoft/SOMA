"""
SOMA-AI Presence Manager — Phase 6: Spatial Awareness
======================================================
DAS RAUMGEFÜHL: Wer ist wo? Wohin bewegt sich jemand?

Architektur:
  ┌─────────────────────────────────────────────────────────────────┐
  │                    PresenceManager                               │
  │                                                                  │
  │  ┌──────────────┐   ┌──────────────┐   ┌───────────────────┐   │
  │  │ AudioTracker  │   │ RSSITracker   │   │ BayesianFusion   │   │
  │  │ (Amplitude)   │   │ (BLE/WiFi)    │   │ (Kombination)    │   │
  │  └──────┬───────┘   └──────┬───────┘   └───────┬───────────┘   │
  │         └──────────┬───────┘                    │                │
  │                    ▼                            │                │
  │         ┌──────────────────┐                    │                │
  │         │ Signal Merger    │ ◄──────────────────┘                │
  │         └────────┬─────────┘                                     │
  │                  ▼                                               │
  │         ┌──────────────────────┐                                │
  │         │ RoomProbabilityVector │ → pro User ein Vektor         │
  │         └────────┬─────────────┘                                │
  │                  ▼                                               │
  │         ┌──────────────────┐                                    │
  │         │ Handover Engine  │ → PresenceEvent → AudioRouter     │
  │         └────────┬─────────┘                                    │
  │                  ▼                                               │
  │         ┌──────────────────┐                                    │
  │         │ SessionManager   │ → Session wandert mit dem Nutzer  │
  │         └──────────────────┘                                    │
  └─────────────────────────────────────────────────────────────────┘

Datenfluss:
  AudioChunkMeta  ──► update_audio()  ──► Signal Merge ──► Probability Vector
  RSSIReading     ──► update_rssi()   ──┘                  │
                                                            ├─ Handover? ──► PresenceEvent
                                                            └─ Session-Migration
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections import defaultdict
from typing import Optional, Callable, Awaitable

import structlog

from shared.audio_types import (
    AudioChunkMeta,
    DetectionMethod,
    PresenceEvent,
    RoomProbability,
    RoomProbabilityVector,
    RSSIReading,
    SessionInfo,
)

logger = structlog.get_logger("soma.presence")


# ═══════════════════════════════════════════════════════════════════════════
#  Signal Processing Helpers
# ═══════════════════════════════════════════════════════════════════════════


class ExponentialMovingAverage:
    """Geglätteter Signal-Wert mit konfigurierbarem Alpha."""

    def __init__(self, alpha: float = 0.3):
        self.alpha = alpha
        self._value: float | None = None

    @property
    def value(self) -> float:
        return self._value if self._value is not None else 0.0

    def update(self, new_value: float) -> float:
        if self._value is None:
            self._value = new_value
        else:
            self._value = self.alpha * new_value + (1.0 - self.alpha) * self._value
        return self._value

    def reset(self) -> None:
        self._value = None


def rssi_to_distance(rssi_dbm: float, tx_power: float = -59.0, n: float = 2.0) -> float:
    """
    RSSI (dBm) → geschätzte Distanz in Metern.
    Formel: d = 10 ^ ((tx_power - rssi) / (10 * n))

    tx_power: RSSI bei 1m Abstand (Kalibrierungswert)
    n: Path-loss exponent (2.0 = Freiraum, 2.5-3.5 = Innenraum)
    """
    if rssi_dbm >= 0:
        return 0.1  # Clamp: unrealistisch starkes Signal
    exponent = (tx_power - rssi_dbm) / (10.0 * n)
    return max(0.1, 10.0 ** exponent)


def distance_to_probability(distance_m: float, max_range: float = 15.0) -> float:
    """
    Distanz → Wahrscheinlichkeit (inverse quadratische Abnahme).
    0.1m = ~1.0, max_range = ~0.0.
    """
    if distance_m <= 0.1:
        return 1.0
    prob = 1.0 / (1.0 + (distance_m / 2.0) ** 2)
    return max(0.0, min(1.0, prob))


def amplitude_to_probability(amplitude: float, max_amp: float = 0.5) -> float:
    """
    Audio-Amplitude → Wahrscheinlichkeit.
    Höhere Amplitude = Nutzer ist näher am Mikrofon.
    """
    if amplitude <= 0.0:
        return 0.0
    # Logarithmische Skalierung (dB-ähnlich)
    normalized = min(amplitude / max_amp, 1.0)
    return normalized ** 0.5  # Square root compression


# ═══════════════════════════════════════════════════════════════════════════
#  User Presence State
# ═══════════════════════════════════════════════════════════════════════════


class UserPresence:
    """Vollständiger Tracking-State für einen einzelnen Nutzer."""

    def __init__(self, user_id: str, room_id: str):
        self.user_id = user_id
        self.current_room: str = room_id
        self.previous_room: str | None = None
        self.last_activity: float = time.monotonic()

        # Signal-Tracker pro Raum
        self._audio_emas: dict[str, ExponentialMovingAverage] = defaultdict(
            lambda: ExponentialMovingAverage(alpha=0.3)
        )
        self._rssi_emas: dict[str, ExponentialMovingAverage] = defaultdict(
            lambda: ExponentialMovingAverage(alpha=0.4)
        )

        # Letzte Rohwerte
        self.room_amplitudes: dict[str, float] = {}
        self.room_rssi: dict[str, float] = {}

        # Probability Vector Cache
        self._probability_vector: RoomProbabilityVector | None = None

        # Session Tracking
        self.session: SessionInfo | None = None

        # Handover-Cooldown (verhindert Ping-Pong zwischen Räumen)
        self._last_handover_time: float = 0.0

    def update_audio(self, room_id: str, amplitude: float) -> float:
        """Audio-Signal aktualisieren, gibt geglätteten Wert zurück."""
        self.room_amplitudes[room_id] = amplitude
        self.last_activity = time.monotonic()
        return self._audio_emas[room_id].update(amplitude)

    def update_rssi(self, room_id: str, rssi_dbm: float) -> float:
        """RSSI-Signal aktualisieren, gibt geglätteten Wert zurück."""
        self.room_rssi[room_id] = rssi_dbm
        self.last_activity = time.monotonic()
        return self._rssi_emas[room_id].update(rssi_dbm)

    def can_handover(self, cooldown_secs: float = 5.0) -> bool:
        """True wenn letzte Handover-Cooldown abgelaufen ist."""
        return (time.monotonic() - self._last_handover_time) >= cooldown_secs

    def mark_handover(self) -> None:
        """Handover durchgeführt — Cooldown starten."""
        self._last_handover_time = time.monotonic()

    def get_audio_ema(self, room_id: str) -> float:
        """Geglätteter Audio-Wert für einen Raum."""
        ema = self._audio_emas.get(room_id)
        return ema.value if ema else 0.0

    def get_rssi_ema(self, room_id: str) -> float:
        """Geglätteter RSSI-Wert für einen Raum."""
        ema = self._rssi_emas.get(room_id)
        return ema.value if ema else -100.0  # -100 dBm = kein Signal

    @property
    def known_rooms(self) -> set[str]:
        """Alle Räume von denen wir Signale haben."""
        return set(self.room_amplitudes.keys()) | set(self.room_rssi.keys())


# ═══════════════════════════════════════════════════════════════════════════
#  Session Manager
# ═══════════════════════════════════════════════════════════════════════════


class SessionManager:
    """
    Verwaltet Konversations-Sessions die mit Nutzern wandern.
    Jeder Raum kann eine unabhängige Session haben (Multi-Session).
    """

    def __init__(self):
        self._sessions: dict[str, SessionInfo] = {}  # session_id → SessionInfo
        self._user_sessions: dict[str, str] = {}      # user_id → session_id
        self._room_sessions: dict[str, set[str]] = defaultdict(set)  # room → {session_ids}

    def create_session(self, user_id: str, room_id: str) -> SessionInfo:
        """Neue Session für User in einem Raum erstellen."""
        session = SessionInfo(
            session_id=str(uuid.uuid4()),
            user_id=user_id,
            current_room=room_id,
        )
        self._sessions[session.session_id] = session
        self._user_sessions[user_id] = session.session_id
        self._room_sessions[room_id].add(session.session_id)
        logger.info(
            "session_created",
            session_id=session.session_id,
            user_id=user_id,
            room=room_id,
        )
        return session

    def get_user_session(self, user_id: str) -> SessionInfo | None:
        """Aktive Session eines Users abrufen."""
        sid = self._user_sessions.get(user_id)
        return self._sessions.get(sid) if sid else None

    def get_room_sessions(self, room_id: str) -> list[SessionInfo]:
        """Alle aktiven Sessions in einem Raum."""
        sids = self._room_sessions.get(room_id, set())
        return [self._sessions[s] for s in sids if s in self._sessions]

    def migrate_session(self, user_id: str, new_room: str) -> SessionInfo | None:
        """
        Session wandert mit dem Nutzer in einen neuen Raum.
        Seamless Handover: Kontext bleibt erhalten.
        """
        session = self.get_user_session(user_id)
        if not session:
            return None

        old_room = session.current_room
        if old_room == new_room:
            return session

        # Aus altem Raum entfernen
        self._room_sessions[old_room].discard(session.session_id)
        # In neuem Raum registrieren
        self._room_sessions[new_room].add(session.session_id)
        # Session-Daten migrieren
        session.migrate_to_room(new_room)

        logger.info(
            "session_migrated",
            session_id=session.session_id,
            user_id=user_id,
            from_room=old_room,
            to_room=new_room,
            turn_count=session.turn_count,
        )
        return session

    def end_session(self, user_id: str) -> bool:
        """Session beenden (User inaktiv)."""
        sid = self._user_sessions.pop(user_id, None)
        if not sid:
            return False
        session = self._sessions.pop(sid, None)
        if session:
            self._room_sessions[session.current_room].discard(sid)
            logger.info("session_ended", session_id=sid, user_id=user_id)
        return True

    def get_all_active(self) -> list[SessionInfo]:
        """Alle aktiven Sessions."""
        return [s for s in self._sessions.values() if s.is_active]

    def update_context(self, user_id: str, context: str, increment_turn: bool = True) -> None:
        """Konversationskontext einer Session aktualisieren."""
        session = self.get_user_session(user_id)
        if session:
            session.conversation_context = context
            if increment_turn:
                session.turn_count += 1
            from datetime import datetime
            session.last_activity = datetime.utcnow()

    @property
    def stats(self) -> dict:
        """Session-Statistiken."""
        active = [s for s in self._sessions.values() if s.is_active]
        rooms_with_sessions = {s.current_room for s in active}
        return {
            "total_sessions": len(self._sessions),
            "active_sessions": len(active),
            "rooms_with_sessions": len(rooms_with_sessions),
            "users_with_sessions": len(self._user_sessions),
        }


# ═══════════════════════════════════════════════════════════════════════════
#  Presence Manager — Das Herzstück
# ═══════════════════════════════════════════════════════════════════════════


class PresenceManager:
    """
    DAS RAUMGEFÜHL von SOMA.

    Funktionen:
      - Audio-Amplitude + RSSI → Bayesian Fusion → RoomProbabilityVector
      - Seamless Session Handover (Kontext wandert mit dem Nutzer)
      - Multi-Session (verschiedene Nutzer in verschiedenen Räumen)
      - Hysterese + Cooldown (kein Ping-Pong zwischen Räumen)
      - Exponential Moving Average für Signal-Glättung

    Config:
      handover_threshold: Minimum-Confidence für Raumwechsel (default 0.65)
      handover_cooldown: Sekunden zwischen Handover-Events (default 5.0)
      inactivity_timeout: Sekunden bis User als "weg" gilt (default 300.0)
      audio_weight: Gewichtung Audio vs. RSSI (default 0.6 → 60% Audio)
      rssi_weight: Gewichtung RSSI (default 0.4 → 40% RSSI)
    """

    def __init__(
        self,
        handover_threshold: float = 0.65,
        handover_cooldown: float = 5.0,
        inactivity_timeout: float = 300.0,
        audio_weight: float = 0.6,
        rssi_weight: float = 0.4,
        on_presence_change: Callable[[PresenceEvent], Awaitable[None]] | None = None,
    ):
        # Config
        self.handover_threshold = handover_threshold
        self.handover_cooldown = handover_cooldown
        self.inactivity_timeout = inactivity_timeout
        self.audio_weight = audio_weight
        self.rssi_weight = rssi_weight
        self._on_presence_change = on_presence_change

        # State
        self._users: dict[str, UserPresence] = {}
        self._room_activity: dict[str, float] = {}
        self._lock = asyncio.Lock()

        # Session Manager
        self.sessions = SessionManager()

        # Stats
        self._handover_count: int = 0
        self._signal_updates: int = 0

    # ── Core API: Signal Updates ─────────────────────────────────────────

    async def update_audio(self, chunk: AudioChunkMeta, user_id: str) -> RoomProbabilityVector | None:
        """
        Audio-Signal von einem Mic-Node empfangen.
        Aktualisiert den Wahrscheinlichkeitsvektor und prüft Handover.

        Returns:
            Aktualisierter RoomProbabilityVector oder None bei Error.
        """
        async with self._lock:
            self._signal_updates += 1
            presence = self._ensure_user(user_id, chunk.room_id)

            # EMA-geglättetes Audio-Signal
            presence.update_audio(chunk.room_id, chunk.amplitude_rms)
            self._room_activity[chunk.room_id] = time.monotonic()

            # Probability Vector berechnen
            vector = self._compute_probability_vector(presence)
            presence._probability_vector = vector

            # Handover prüfen
            await self._check_handover(presence, vector)

            return vector

    async def update_rssi(self, reading: RSSIReading, user_id: str) -> RoomProbabilityVector | None:
        """
        RSSI-Signal (BLE/WiFi) von einem Scanner-Node empfangen.
        Wird mit Audio fusioniert für genauere Lokalisierung.

        Returns:
            Aktualisierter RoomProbabilityVector oder None bei Error.
        """
        async with self._lock:
            self._signal_updates += 1
            presence = self._ensure_user(user_id, reading.room_id)

            # EMA-geglättetes RSSI-Signal
            presence.update_rssi(reading.room_id, reading.rssi_dbm)
            self._room_activity[reading.room_id] = time.monotonic()

            # Probability Vector berechnen
            vector = self._compute_probability_vector(presence)
            presence._probability_vector = vector

            # Handover prüfen
            await self._check_handover(presence, vector)

            return vector

    async def set_manual_room(self, user_id: str, room_id: str) -> PresenceEvent:
        """
        Manueller Raumwechsel (z.B. über Dashboard oder Voice-Command).
        Überschreibt alle Signale.
        """
        async with self._lock:
            presence = self._ensure_user(user_id, room_id)
            old_room = presence.current_room

            event = PresenceEvent(
                user_id=user_id,
                from_room=old_room,
                to_room=room_id,
                confidence=1.0,
                detection_method=DetectionMethod.MANUAL.value,
            )

            if old_room != room_id:
                presence.previous_room = old_room
                presence.current_room = room_id
                presence.mark_handover()
                self._handover_count += 1

                # Session migrieren
                session = self.sessions.migrate_session(user_id, room_id)
                if session:
                    event.session_id = session.session_id

                logger.info(
                    "manual_handover",
                    user_id=user_id,
                    from_room=old_room,
                    to_room=room_id,
                )

                if self._on_presence_change:
                    await self._on_presence_change(event)

            return event

    # ── Probability Vector Computation ───────────────────────────────────

    def _compute_probability_vector(self, presence: UserPresence) -> RoomProbabilityVector:
        """
        Bayesian-style Fusion: Audio + RSSI → Wahrscheinlichkeitsvektor.

        Für jeden Raum:
          audio_prob = amplitude_to_probability(smoothed_amplitude)
          rssi_prob  = distance_to_probability(rssi_to_distance(smoothed_rssi))
          combined   = audio_weight * audio_prob + rssi_weight * rssi_prob

        Dann normalisieren → Summe = 1.0.
        """
        rooms = list(presence.known_rooms)
        if not rooms:
            return RoomProbabilityVector(user_id=presence.user_id)

        # Rohwerte pro Raum berechnen
        raw_probs: dict[str, tuple[float, float]] = {}  # room → (audio_prob, rssi_prob)

        for room in rooms:
            # Audio-Komponente
            audio_ema = presence.get_audio_ema(room)
            audio_prob = amplitude_to_probability(audio_ema)

            # RSSI-Komponente
            rssi_ema = presence.get_rssi_ema(room)
            if rssi_ema > -99.0:  # Nur wenn echtes RSSI-Signal vorhanden
                distance = rssi_to_distance(rssi_ema)
                rssi_prob = distance_to_probability(distance)
            else:
                rssi_prob = 0.0

            raw_probs[room] = (audio_prob, rssi_prob)

        # Gewichtete Fusion
        has_rssi = any(rp > 0.0 for _, rp in raw_probs.values())
        combined: dict[str, float] = {}

        for room, (ap, rp) in raw_probs.items():
            if has_rssi:
                combined[room] = self.audio_weight * ap + self.rssi_weight * rp
            else:
                combined[room] = ap  # Nur Audio verfügbar

        # Normalisieren
        total = sum(combined.values())
        if total > 0:
            for room in combined:
                combined[room] /= total

        # Detection Method bestimmen
        if has_rssi:
            method = DetectionMethod.FUSED
        else:
            method = DetectionMethod.AUDIO_AMPLITUDE

        # Best Room
        best_room = max(combined, key=combined.get) if combined else None  # type: ignore[arg-type]
        best_conf = combined.get(best_room, 0.0) if best_room else 0.0

        # Probability-Objekte bauen
        room_probs = []
        for room in rooms:
            ap, rp = raw_probs.get(room, (0.0, 0.0))
            room_probs.append(
                RoomProbability(
                    room_id=room,
                    probability=combined.get(room, 0.0),
                    audio_confidence=ap,
                    rssi_confidence=rp,
                )
            )

        return RoomProbabilityVector(
            user_id=presence.user_id,
            rooms=room_probs,
            best_room=best_room,
            best_confidence=best_conf,
            detection_method=method,
        )

    # ── Handover Engine ──────────────────────────────────────────────────

    async def _check_handover(
        self,
        presence: UserPresence,
        vector: RoomProbabilityVector,
    ) -> bool:
        """
        Prüfe ob ein Raumwechsel stattfinden soll.

        Regeln:
        1. Best Room ≠ Current Room
        2. Best Confidence > handover_threshold
        3. Cooldown abgelaufen (kein Ping-Pong)
        4. Hysterese: Best muss DEUTLICH besser sein als Current

        Returns:
            True wenn Handover ausgelöst wurde.
        """
        if not vector.best_room:
            return False

        if vector.best_room == presence.current_room:
            return False

        if not presence.can_handover(self.handover_cooldown):
            return False

        if vector.best_confidence < self.handover_threshold:
            return False

        # Hysterese: Best muss mindestens 20% besser sein als Current
        current_prob = 0.0
        for rp in vector.rooms:
            if rp.room_id == presence.current_room:
                current_prob = rp.probability
                break

        if current_prob > 0.0 and vector.best_confidence < current_prob * 1.2:
            return False

        # ═══ HANDOVER! ═══
        await self._execute_handover(presence, vector)
        return True

    async def _execute_handover(
        self,
        presence: UserPresence,
        vector: RoomProbabilityVector,
    ) -> None:
        """Handover durchführen: Room wechseln, Session migrieren, Event feuern."""
        old_room = presence.current_room
        new_room = vector.best_room
        assert new_room is not None

        # User-State aktualisieren
        presence.previous_room = old_room
        presence.current_room = new_room
        presence.mark_handover()
        self._handover_count += 1

        # Session migrieren (Kontext wandert mit!)
        session = self.sessions.migrate_session(presence.user_id, new_room)

        # PresenceEvent bauen
        event = PresenceEvent(
            user_id=presence.user_id,
            from_room=old_room,
            to_room=new_room,
            confidence=vector.best_confidence,
            detection_method=vector.detection_method.value,
            session_id=session.session_id if session else None,
            probability_vector=vector,
        )

        logger.info(
            "handover",
            user_id=presence.user_id,
            from_room=old_room,
            to_room=new_room,
            confidence=f"{vector.best_confidence:.2f}",
            method=vector.detection_method.value,
            session=session.session_id if session else "none",
        )

        # Callback (→ AudioRouter)
        if self._on_presence_change:
            await self._on_presence_change(event)

    # ── User Management ──────────────────────────────────────────────────

    def _ensure_user(self, user_id: str, room_id: str) -> UserPresence:
        """User-Objekt holen oder erstellen."""
        if user_id not in self._users:
            presence = UserPresence(user_id=user_id, room_id=room_id)
            self._users[user_id] = presence
            # Automatisch Session erstellen
            self.sessions.create_session(user_id, room_id)
            logger.info("user_discovered", user_id=user_id, room=room_id)
        return self._users[user_id]

    # ── Queries ──────────────────────────────────────────────────────────

    def get_user_room(self, user_id: str) -> str | None:
        """Aktueller Raum eines Users."""
        presence = self._users.get(user_id)
        return presence.current_room if presence else None

    def get_user_probability_vector(self, user_id: str) -> RoomProbabilityVector | None:
        """Vollständiger Wahrscheinlichkeitsvektor eines Users."""
        presence = self._users.get(user_id)
        if not presence:
            return None
        return presence._probability_vector

    def get_room_users(self, room_id: str) -> list[str]:
        """Alle User in einem Raum."""
        return [
            uid for uid, p in self._users.items()
            if p.current_room == room_id
        ]

    def get_active_rooms(self) -> list[str]:
        """Alle Räume mit kürzlicher Aktivität."""
        now = time.monotonic()
        return [
            room_id
            for room_id, last in self._room_activity.items()
            if now - last < self.inactivity_timeout
        ]

    def get_all_presences(self) -> dict[str, dict]:
        """
        Alle User-Presences als dict (für API-Response).
        """
        result = {}
        for uid, p in self._users.items():
            vector = p._probability_vector
            session = self.sessions.get_user_session(uid)
            result[uid] = {
                "user_id": uid,
                "current_room": p.current_room,
                "previous_room": p.previous_room,
                "probability_vector": (
                    vector.model_dump() if vector else None
                ),
                "session": (
                    session.model_dump() if session else None
                ),
                "known_rooms": list(p.known_rooms),
                "last_activity_ago_sec": round(
                    time.monotonic() - p.last_activity, 1
                ),
            }
        return result

    # ── Cleanup ──────────────────────────────────────────────────────────

    async def cleanup_inactive(self) -> list[str]:
        """
        Entferne inaktive Nutzer und deren Sessions.
        Returns: Liste der entfernten User-IDs.
        """
        async with self._lock:
            now = time.monotonic()
            inactive = [
                uid
                for uid, p in self._users.items()
                if now - p.last_activity > self.inactivity_timeout
            ]
            for uid in inactive:
                del self._users[uid]
                self.sessions.end_session(uid)
                logger.info("user_inactive_removed", user_id=uid)
            return inactive

    # ── Statistics ───────────────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        """Presence-System Statistiken."""
        return {
            "tracked_users": len(self._users),
            "active_rooms": len(self.get_active_rooms()),
            "total_handovers": self._handover_count,
            "total_signal_updates": self._signal_updates,
            "session_stats": self.sessions.stats,
            "config": {
                "handover_threshold": self.handover_threshold,
                "handover_cooldown": self.handover_cooldown,
                "audio_weight": self.audio_weight,
                "rssi_weight": self.rssi_weight,
                "inactivity_timeout": self.inactivity_timeout,
            },
        }
