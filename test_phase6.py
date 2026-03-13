"""
SOMA-AI Phase 6 Tests — Spatial Awareness & Multi-Room
========================================================
Teste:
  - Signal Processing Helpers (EMA, RSSI→Distance, Amplitude→Probability)
  - UserPresence State Tracking
  - SessionManager (Create, Migrate, End, Multi-Session)
  - PresenceManager (Audio, RSSI, Fusion, Handover, Hysterese, Cleanup)
  - DiscoveryOrchestrator (MQTT Hello, mDNS, HA-Sync, Registry, Health)
  - Audio Types (neue Pydantic-Modelle)
  - main.py Wiring & API Endpoints
"""

import asyncio
import time
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _run(coro):
    """Helper: Async-Coroutine synchron ausführen."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ═══════════════════════════════════════════════════════════════════════════
#  1. Audio Types — Neue Pydantic-Modelle
# ═══════════════════════════════════════════════════════════════════════════


class TestAudioTypes:
    """Phase 6 Datentypen in shared/audio_types.py."""

    def test_rssi_reading_model(self):
        from shared.audio_types import RSSIReading
        r = RSSIReading(
            device_id="ble_mac_01",
            room_id="wohnzimmer",
            scanner_node_id="scanner_01",
            rssi_dbm=-55.0,
        )
        assert r.rssi_dbm == -55.0
        assert r.room_id == "wohnzimmer"
        assert r.frequency_mhz == 2400

    def test_room_probability_model(self):
        from shared.audio_types import RoomProbability
        rp = RoomProbability(room_id="kueche", probability=0.7, audio_confidence=0.8, rssi_confidence=0.6)
        assert rp.probability == 0.7
        assert rp.audio_confidence == 0.8

    def test_room_probability_vector(self):
        from shared.audio_types import RoomProbabilityVector, RoomProbability
        vec = RoomProbabilityVector(
            user_id="user_1",
            rooms=[
                RoomProbability(room_id="wohnzimmer", probability=0.7),
                RoomProbability(room_id="kueche", probability=0.3),
            ],
            best_room="wohnzimmer",
            best_confidence=0.7,
        )
        assert vec.is_confident  # > 0.6
        assert vec.best_room == "wohnzimmer"
        assert len(vec.rooms) == 2

    def test_room_probability_vector_not_confident(self):
        from shared.audio_types import RoomProbabilityVector
        vec = RoomProbabilityVector(user_id="u", best_confidence=0.4)
        assert not vec.is_confident

    def test_session_info_model(self):
        from shared.audio_types import SessionInfo
        s = SessionInfo(
            session_id="sid_1",
            user_id="user_1",
            current_room="wohnzimmer",
        )
        assert s.is_active
        assert s.turn_count == 0
        assert len(s.previous_rooms) == 0

    def test_session_info_migrate(self):
        from shared.audio_types import SessionInfo
        s = SessionInfo(session_id="s1", user_id="u1", current_room="wohnzimmer")
        s.migrate_to_room("kueche")
        assert s.current_room == "kueche"
        assert s.previous_rooms == ["wohnzimmer"]
        s.migrate_to_room("schlafzimmer")
        assert s.current_room == "schlafzimmer"
        assert s.previous_rooms == ["wohnzimmer", "kueche"]

    def test_session_info_no_duplicate_migrate(self):
        from shared.audio_types import SessionInfo
        s = SessionInfo(session_id="s1", user_id="u1", current_room="wohnzimmer")
        s.migrate_to_room("wohnzimmer")  # Same room
        assert len(s.previous_rooms) == 0  # No migration happened

    def test_discovered_device_model(self):
        from shared.audio_types import DiscoveredDevice, DeviceStatus, NodeType
        d = DiscoveredDevice(
            device_id="mic_01",
            name="Wohnzimmer Mic",
            device_type=NodeType.MIC,
            status=DeviceStatus.ONLINE,
        )
        assert d.is_online
        d.status = DeviceStatus.OFFLINE
        assert not d.is_online

    def test_presence_event_phase6_fields(self):
        from shared.audio_types import PresenceEvent, RoomProbabilityVector
        vec = RoomProbabilityVector(user_id="u1", best_room="kueche", best_confidence=0.8)
        e = PresenceEvent(
            user_id="u1",
            to_room="kueche",
            confidence=0.8,
            session_id="sid_1",
            probability_vector=vec,
        )
        assert e.session_id == "sid_1"
        assert e.probability_vector is not None

    def test_detection_method_enum(self):
        from shared.audio_types import DetectionMethod
        assert DetectionMethod.FUSED == "fused"
        assert DetectionMethod.AUDIO_AMPLITUDE == "audio_amplitude"
        assert DetectionMethod.RSSI == "rssi"
        assert DetectionMethod.MANUAL == "manual"

    def test_device_status_enum(self):
        from shared.audio_types import DeviceStatus
        assert DeviceStatus.ONLINE == "online"
        assert DeviceStatus.OFFLINE == "offline"
        assert DeviceStatus.INITIALIZING == "initializing"


# ═══════════════════════════════════════════════════════════════════════════
#  2. Signal Processing Helpers
# ═══════════════════════════════════════════════════════════════════════════


class TestSignalProcessing:
    """EMA, RSSI→Distance, Amplitude→Probability."""

    def test_ema_initial(self):
        from brain_core.presence_manager import ExponentialMovingAverage
        ema = ExponentialMovingAverage(alpha=0.5)
        assert ema.value == 0.0
        result = ema.update(1.0)
        assert result == 1.0  # First value = raw value

    def test_ema_smoothing(self):
        from brain_core.presence_manager import ExponentialMovingAverage
        ema = ExponentialMovingAverage(alpha=0.5)
        ema.update(1.0)
        result = ema.update(0.0)
        assert result == 0.5  # 0.5 * 0.0 + 0.5 * 1.0

    def test_ema_convergence(self):
        from brain_core.presence_manager import ExponentialMovingAverage
        ema = ExponentialMovingAverage(alpha=0.3)
        ema.update(0.0)
        for _ in range(50):
            ema.update(1.0)
        assert abs(ema.value - 1.0) < 0.01  # Should converge to 1.0

    def test_ema_reset(self):
        from brain_core.presence_manager import ExponentialMovingAverage
        ema = ExponentialMovingAverage(alpha=0.5)
        ema.update(10.0)
        ema.reset()
        assert ema.value == 0.0

    def test_rssi_to_distance_near(self):
        from brain_core.presence_manager import rssi_to_distance
        d = rssi_to_distance(-30.0)  # Very strong signal
        assert d < 1.0  # Should be very close

    def test_rssi_to_distance_far(self):
        from brain_core.presence_manager import rssi_to_distance
        d = rssi_to_distance(-80.0)
        assert d > 5.0  # Should be far away

    def test_rssi_to_distance_positive_clamp(self):
        from brain_core.presence_manager import rssi_to_distance
        d = rssi_to_distance(5.0)  # Invalid positive RSSI
        assert d == 0.1  # Clamped

    def test_distance_to_probability_near(self):
        from brain_core.presence_manager import distance_to_probability
        p = distance_to_probability(0.05)
        assert p == 1.0  # Very near → 100%

    def test_distance_to_probability_far(self):
        from brain_core.presence_manager import distance_to_probability
        p = distance_to_probability(20.0)
        assert p < 0.1  # Far → low probability

    def test_amplitude_to_probability_zero(self):
        from brain_core.presence_manager import amplitude_to_probability
        p = amplitude_to_probability(0.0)
        assert p == 0.0

    def test_amplitude_to_probability_high(self):
        from brain_core.presence_manager import amplitude_to_probability
        p = amplitude_to_probability(0.5)
        assert p == 1.0  # max_amp = 0.5

    def test_amplitude_to_probability_medium(self):
        from brain_core.presence_manager import amplitude_to_probability
        p = amplitude_to_probability(0.25)
        assert 0.5 < p < 0.8  # sqrt(0.5) ≈ 0.707


# ═══════════════════════════════════════════════════════════════════════════
#  3. UserPresence
# ═══════════════════════════════════════════════════════════════════════════


class TestUserPresence:
    """UserPresence state tracking."""

    def test_creation(self):
        from brain_core.presence_manager import UserPresence
        up = UserPresence("user1", "wohnzimmer")
        assert up.user_id == "user1"
        assert up.current_room == "wohnzimmer"
        assert up.previous_room is None
        assert len(up.known_rooms) == 0

    def test_update_audio(self):
        from brain_core.presence_manager import UserPresence
        up = UserPresence("u1", "wz")
        val = up.update_audio("wz", 0.3)
        assert val == 0.3  # First update = raw value
        assert "wz" in up.known_rooms
        assert up.room_amplitudes["wz"] == 0.3

    def test_update_rssi(self):
        from brain_core.presence_manager import UserPresence
        up = UserPresence("u1", "wz")
        val = up.update_rssi("kueche", -45.0)
        assert val == -45.0
        assert "kueche" in up.known_rooms
        assert up.room_rssi["kueche"] == -45.0

    def test_handover_cooldown(self):
        from brain_core.presence_manager import UserPresence
        up = UserPresence("u1", "wz")
        assert up.can_handover(cooldown_secs=0.0)  # No cooldown
        up.mark_handover()
        assert not up.can_handover(cooldown_secs=10.0)  # Cooldown active
        assert up.can_handover(cooldown_secs=0.0)  # Zero cooldown always true

    def test_get_audio_ema_missing(self):
        from brain_core.presence_manager import UserPresence
        up = UserPresence("u1", "wz")
        assert up.get_audio_ema("nonexistent") == 0.0

    def test_get_rssi_ema_missing(self):
        from brain_core.presence_manager import UserPresence
        up = UserPresence("u1", "wz")
        assert up.get_rssi_ema("nonexistent") == -100.0

    def test_known_rooms_combined(self):
        from brain_core.presence_manager import UserPresence
        up = UserPresence("u1", "wz")
        up.update_audio("wz", 0.3)
        up.update_rssi("kueche", -50.0)
        up.update_audio("bad", 0.1)
        assert up.known_rooms == {"wz", "kueche", "bad"}


# ═══════════════════════════════════════════════════════════════════════════
#  4. SessionManager
# ═══════════════════════════════════════════════════════════════════════════


class TestSessionManager:
    """Session creation, migration, multi-session."""

    def test_create_session(self):
        from brain_core.presence_manager import SessionManager
        sm = SessionManager()
        session = sm.create_session("user1", "wohnzimmer")
        assert session.user_id == "user1"
        assert session.current_room == "wohnzimmer"
        assert session.is_active

    def test_get_user_session(self):
        from brain_core.presence_manager import SessionManager
        sm = SessionManager()
        sm.create_session("u1", "wz")
        session = sm.get_user_session("u1")
        assert session is not None
        assert session.user_id == "u1"

    def test_get_nonexistent_session(self):
        from brain_core.presence_manager import SessionManager
        sm = SessionManager()
        assert sm.get_user_session("nobody") is None

    def test_migrate_session(self):
        from brain_core.presence_manager import SessionManager
        sm = SessionManager()
        sm.create_session("u1", "wz")
        migrated = sm.migrate_session("u1", "kueche")
        assert migrated is not None
        assert migrated.current_room == "kueche"
        assert migrated.previous_rooms == ["wz"]

    def test_migrate_same_room(self):
        from brain_core.presence_manager import SessionManager
        sm = SessionManager()
        sm.create_session("u1", "wz")
        migrated = sm.migrate_session("u1", "wz")
        assert migrated is not None
        assert migrated.current_room == "wz"
        assert len(migrated.previous_rooms) == 0

    def test_end_session(self):
        from brain_core.presence_manager import SessionManager
        sm = SessionManager()
        sm.create_session("u1", "wz")
        assert sm.end_session("u1")
        assert sm.get_user_session("u1") is None

    def test_end_nonexistent_session(self):
        from brain_core.presence_manager import SessionManager
        sm = SessionManager()
        assert not sm.end_session("nobody")

    def test_multi_session(self):
        from brain_core.presence_manager import SessionManager
        sm = SessionManager()
        sm.create_session("user1", "wohnzimmer")
        sm.create_session("user2", "kueche")
        s1 = sm.get_user_session("user1")
        s2 = sm.get_user_session("user2")
        assert s1.current_room == "wohnzimmer"
        assert s2.current_room == "kueche"
        assert s1.session_id != s2.session_id

    def test_room_sessions(self):
        from brain_core.presence_manager import SessionManager
        sm = SessionManager()
        sm.create_session("u1", "wz")
        sm.create_session("u2", "wz")
        room_sessions = sm.get_room_sessions("wz")
        assert len(room_sessions) == 2

    def test_update_context(self):
        from brain_core.presence_manager import SessionManager
        sm = SessionManager()
        sm.create_session("u1", "wz")
        sm.update_context("u1", "User sagte: Hallo")
        session = sm.get_user_session("u1")
        assert session.conversation_context == "User sagte: Hallo"
        assert session.turn_count == 1

    def test_stats(self):
        from brain_core.presence_manager import SessionManager
        sm = SessionManager()
        sm.create_session("u1", "wz")
        sm.create_session("u2", "kueche")
        stats = sm.stats
        assert stats["total_sessions"] == 2
        assert stats["active_sessions"] == 2
        assert stats["rooms_with_sessions"] == 2

    def test_get_all_active(self):
        from brain_core.presence_manager import SessionManager
        sm = SessionManager()
        sm.create_session("u1", "wz")
        sm.create_session("u2", "kueche")
        active = sm.get_all_active()
        assert len(active) == 2


# ═══════════════════════════════════════════════════════════════════════════
#  5. PresenceManager — Core Logic
# ═══════════════════════════════════════════════════════════════════════════


class TestPresenceManager:
    """PresenceManager: Audio, RSSI, Fusion, Handover."""

    def test_creation(self):
        from brain_core.presence_manager import PresenceManager
        pm = PresenceManager()
        assert pm.handover_threshold == 0.65
        assert pm.audio_weight == 0.6
        assert pm.rssi_weight == 0.4
        assert pm.sessions is not None
        assert pm.stats["tracked_users"] == 0

    def test_update_audio_creates_user(self):
        from brain_core.presence_manager import PresenceManager
        from shared.audio_types import AudioChunkMeta
        pm = PresenceManager()
        chunk = AudioChunkMeta(node_id="mic1", room_id="wz", amplitude_rms=0.3)
        vec = _run(pm.update_audio(chunk, "user1"))
        assert vec is not None
        assert pm.get_user_room("user1") == "wz"
        assert pm.stats["tracked_users"] == 1

    def test_update_audio_returns_vector(self):
        from brain_core.presence_manager import PresenceManager
        from shared.audio_types import AudioChunkMeta
        pm = PresenceManager()
        chunk = AudioChunkMeta(node_id="mic1", room_id="wz", amplitude_rms=0.3)
        vec = _run(pm.update_audio(chunk, "user1"))
        assert vec.user_id == "user1"
        assert vec.best_room == "wz"
        assert vec.best_confidence > 0.0

    def test_update_rssi_creates_user(self):
        from brain_core.presence_manager import PresenceManager
        from shared.audio_types import RSSIReading
        pm = PresenceManager()
        reading = RSSIReading(
            device_id="ble1", room_id="kueche",
            scanner_node_id="scan1", rssi_dbm=-45.0,
        )
        vec = _run(pm.update_rssi(reading, "user1"))
        assert vec is not None
        assert pm.get_user_room("user1") == "kueche"

    def test_fused_detection_method(self):
        from brain_core.presence_manager import PresenceManager
        from shared.audio_types import AudioChunkMeta, RSSIReading, DetectionMethod
        pm = PresenceManager()
        # Audio first
        _run(pm.update_audio(
            AudioChunkMeta(node_id="m1", room_id="wz", amplitude_rms=0.3), "u1"
        ))
        # Then RSSI
        vec = _run(pm.update_rssi(
            RSSIReading(device_id="b1", room_id="wz", scanner_node_id="s1", rssi_dbm=-40.0), "u1"
        ))
        assert vec.detection_method == DetectionMethod.FUSED

    def test_audio_only_detection_method(self):
        from brain_core.presence_manager import PresenceManager
        from shared.audio_types import AudioChunkMeta, DetectionMethod
        pm = PresenceManager()
        vec = _run(pm.update_audio(
            AudioChunkMeta(node_id="m1", room_id="wz", amplitude_rms=0.3), "u1"
        ))
        assert vec.detection_method == DetectionMethod.AUDIO_AMPLITUDE

    def test_probability_vector_normalized(self):
        from brain_core.presence_manager import PresenceManager
        from shared.audio_types import AudioChunkMeta
        pm = PresenceManager()
        # Two rooms with different amplitudes
        _run(pm.update_audio(
            AudioChunkMeta(node_id="m1", room_id="wz", amplitude_rms=0.4), "u1"
        ))
        vec = _run(pm.update_audio(
            AudioChunkMeta(node_id="m2", room_id="kueche", amplitude_rms=0.1), "u1"
        ))
        total = sum(rp.probability for rp in vec.rooms)
        assert abs(total - 1.0) < 0.01  # Should sum to ~1.0

    def test_handover_triggered(self):
        """Strong signal in new room should trigger handover."""
        from brain_core.presence_manager import PresenceManager
        from shared.audio_types import AudioChunkMeta
        events = []
        async def on_change(event):
            events.append(event)

        pm = PresenceManager(
            handover_threshold=0.5,
            handover_cooldown=0.0,  # No cooldown for testing
            on_presence_change=on_change,
        )

        # Start in wz with low amplitude
        _run(pm.update_audio(
            AudioChunkMeta(node_id="m1", room_id="wz", amplitude_rms=0.01), "u1"
        ))
        assert pm.get_user_room("u1") == "wz"

        # Very strong signal in kueche — should trigger handover
        _run(pm.update_audio(
            AudioChunkMeta(node_id="m2", room_id="kueche", amplitude_rms=0.5), "u1"
        ))
        assert pm.get_user_room("u1") == "kueche"
        assert len(events) == 1
        assert events[0].to_room == "kueche"

    def test_handover_cooldown(self):
        """Handover should not trigger during cooldown."""
        from brain_core.presence_manager import PresenceManager
        from shared.audio_types import AudioChunkMeta
        events = []
        async def on_change(event):
            events.append(event)

        pm = PresenceManager(
            handover_threshold=0.5,
            handover_cooldown=100.0,  # Very long cooldown
            on_presence_change=on_change,
        )

        _run(pm.update_audio(
            AudioChunkMeta(node_id="m1", room_id="wz", amplitude_rms=0.01), "u1"
        ))
        # Strong signal elsewhere — but cooldown after initial "discovery"
        # (user was just created, first handover sets cooldown)
        # Actually first creation doesn't set cooldown, so first handover should work
        _run(pm.update_audio(
            AudioChunkMeta(node_id="m2", room_id="kueche", amplitude_rms=0.5), "u1"
        ))
        # After this handover, cooldown is set to 100s → no more handovers
        count_after_first = len(events)
        
        # Try another handover back to wz — should be blocked by cooldown
        _run(pm.update_audio(
            AudioChunkMeta(node_id="m1", room_id="wz", amplitude_rms=0.5), "u1"
        ))
        assert len(events) == count_after_first  # No new event

    def test_session_migrates_with_handover(self):
        """Session should follow user during handover."""
        from brain_core.presence_manager import PresenceManager
        from shared.audio_types import AudioChunkMeta

        pm = PresenceManager(handover_threshold=0.5, handover_cooldown=0.0)

        _run(pm.update_audio(
            AudioChunkMeta(node_id="m1", room_id="wz", amplitude_rms=0.01), "u1"
        ))
        session = pm.sessions.get_user_session("u1")
        assert session is not None
        assert session.current_room == "wz"

        # Handover
        _run(pm.update_audio(
            AudioChunkMeta(node_id="m2", room_id="kueche", amplitude_rms=0.5), "u1"
        ))
        session = pm.sessions.get_user_session("u1")
        assert session.current_room == "kueche"
        assert "wz" in session.previous_rooms

    def test_manual_room_set(self):
        from brain_core.presence_manager import PresenceManager
        pm = PresenceManager()
        event = _run(pm.set_manual_room("user1", "schlafzimmer"))
        assert event.to_room == "schlafzimmer"
        assert event.confidence == 1.0
        assert pm.get_user_room("user1") == "schlafzimmer"

    def test_manual_room_same_room(self):
        from brain_core.presence_manager import PresenceManager
        pm = PresenceManager()
        _run(pm.set_manual_room("u1", "wz"))
        event = _run(pm.set_manual_room("u1", "wz"))  # Same room
        assert event.to_room == "wz"
        assert pm.stats["total_handovers"] == 0  # No actual handover

    def test_get_room_users(self):
        from brain_core.presence_manager import PresenceManager
        from shared.audio_types import AudioChunkMeta
        pm = PresenceManager()
        _run(pm.update_audio(
            AudioChunkMeta(node_id="m1", room_id="wz", amplitude_rms=0.3), "u1"
        ))
        _run(pm.update_audio(
            AudioChunkMeta(node_id="m1", room_id="wz", amplitude_rms=0.2), "u2"
        ))
        users = pm.get_room_users("wz")
        assert set(users) == {"u1", "u2"}

    def test_get_active_rooms(self):
        from brain_core.presence_manager import PresenceManager
        from shared.audio_types import AudioChunkMeta
        pm = PresenceManager()
        _run(pm.update_audio(
            AudioChunkMeta(node_id="m1", room_id="wz", amplitude_rms=0.3), "u1"
        ))
        _run(pm.update_audio(
            AudioChunkMeta(node_id="m2", room_id="kueche", amplitude_rms=0.2), "u2"
        ))
        rooms = pm.get_active_rooms()
        assert "wz" in rooms
        assert "kueche" in rooms

    def test_get_user_probability_vector(self):
        from brain_core.presence_manager import PresenceManager
        from shared.audio_types import AudioChunkMeta
        pm = PresenceManager()
        _run(pm.update_audio(
            AudioChunkMeta(node_id="m1", room_id="wz", amplitude_rms=0.3), "u1"
        ))
        vec = pm.get_user_probability_vector("u1")
        assert vec is not None
        assert vec.user_id == "u1"

    def test_get_user_probability_vector_nonexistent(self):
        from brain_core.presence_manager import PresenceManager
        pm = PresenceManager()
        assert pm.get_user_probability_vector("nobody") is None

    def test_get_all_presences(self):
        from brain_core.presence_manager import PresenceManager
        from shared.audio_types import AudioChunkMeta
        pm = PresenceManager()
        _run(pm.update_audio(
            AudioChunkMeta(node_id="m1", room_id="wz", amplitude_rms=0.3), "u1"
        ))
        presences = pm.get_all_presences()
        assert "u1" in presences
        assert presences["u1"]["current_room"] == "wz"
        assert presences["u1"]["probability_vector"] is not None
        assert presences["u1"]["session"] is not None

    def test_cleanup_inactive(self):
        from brain_core.presence_manager import PresenceManager
        from shared.audio_types import AudioChunkMeta
        pm = PresenceManager(inactivity_timeout=0.0)  # Immediate timeout
        _run(pm.update_audio(
            AudioChunkMeta(node_id="m1", room_id="wz", amplitude_rms=0.3), "u1"
        ))
        removed = _run(pm.cleanup_inactive())
        assert "u1" in removed
        assert pm.get_user_room("u1") is None

    def test_stats(self):
        from brain_core.presence_manager import PresenceManager
        from shared.audio_types import AudioChunkMeta
        pm = PresenceManager()
        _run(pm.update_audio(
            AudioChunkMeta(node_id="m1", room_id="wz", amplitude_rms=0.3), "u1"
        ))
        stats = pm.stats
        assert stats["tracked_users"] == 1
        assert stats["total_signal_updates"] == 1
        assert "session_stats" in stats
        assert "config" in stats


# ═══════════════════════════════════════════════════════════════════════════
#  6. DiscoveryOrchestrator
# ═══════════════════════════════════════════════════════════════════════════


class TestDiscoveryOrchestrator:
    """Zero-Config Hardware Discovery."""

    def _make_orchestrator(self):
        from brain_core.discovery.orchestrator import DiscoveryOrchestrator
        mqtt = MagicMock()
        mqtt.start = AsyncMock()
        mqtt.stop = AsyncMock()
        mqtt.set_hello_callback = MagicMock()
        mqtt.set_audio_callback = MagicMock()
        mdns = MagicMock()
        mdns.start = AsyncMock()
        mdns.stop = AsyncMock()
        mdns.set_callback = MagicMock()
        mdns.scan_once = AsyncMock(return_value={})
        ha = MagicMock()
        ha.connect = AsyncMock()
        ha.disconnect = AsyncMock()
        ha.sync_entities = AsyncMock(return_value={})
        orch = DiscoveryOrchestrator(
            mqtt_listener=mqtt,
            mdns_scanner=mdns,
            ha_bridge=ha,
            ha_sync_interval=9999.0,  # Don't auto-sync in tests
            cleanup_interval=9999.0,
        )
        return orch, mqtt, mdns, ha

    def test_creation(self):
        orch, _, _, _ = self._make_orchestrator()
        assert len(orch.get_all_devices()) == 0
        assert orch.stats["total_devices"] == 0

    def test_mqtt_hello_registers_device(self):
        from shared.audio_types import HardwareHello, NodeType, ProtocolType
        orch, _, _, _ = self._make_orchestrator()
        hello = HardwareHello(
            node_id="mic_wz_01",
            node_type=NodeType.MIC,
            protocol=ProtocolType.MQTT,
            capabilities=["audio_capture"],
            ip_address="192.168.1.100",
        )
        _run(orch._on_mqtt_hello(hello))
        assert len(orch.get_all_devices()) == 1
        device = orch.get_device("mic_wz_01")
        assert device is not None
        assert device.name == "mic_wz_01"
        assert device.is_online

    def test_mqtt_hello_heartbeat(self):
        from shared.audio_types import HardwareHello, NodeType, ProtocolType
        orch, _, _, _ = self._make_orchestrator()
        hello = HardwareHello(node_id="mic1", node_type=NodeType.MIC, protocol=ProtocolType.MQTT)
        _run(orch._on_mqtt_hello(hello))
        _run(orch._on_mqtt_hello(hello))  # Second hello = heartbeat
        assert len(orch.get_all_devices()) == 1  # Still just 1
        assert orch._mqtt_discoveries == 2

    def test_mdns_registers_device(self):
        orch, _, _, _ = self._make_orchestrator()
        service = {
            "name": "soma-mic-01._soma._tcp.local.",
            "service_type": "_soma._tcp.local.",
            "ip_address": "192.168.1.50",
            "port": 8080,
            "properties": {"version": "1.0"},
        }
        _run(orch._on_mdns_discovered(service))
        devices = orch.get_all_devices()
        assert len(devices) == 1
        assert devices[0].protocol.value == "mdns"
        assert orch._mdns_discoveries == 1

    def test_ha_sync(self):
        orch, _, _, ha = self._make_orchestrator()
        ha.sync_entities = AsyncMock(return_value={
            "light.wohnzimmer": {
                "state": "on",
                "attributes": {"friendly_name": "Wohnzimmer Licht"},
            },
            "media_player.kueche": {
                "state": "playing",
                "attributes": {"friendly_name": "Küche Speaker"},
            },
        })
        count = _run(orch._sync_ha_entities())
        assert count == 2
        assert len(orch.get_all_devices()) == 2
        assert orch._ha_syncs == 1

    def test_device_queries(self):
        from shared.audio_types import HardwareHello, NodeType, ProtocolType
        orch, _, _, _ = self._make_orchestrator()
        # Add a MIC and a SPK
        _run(orch._on_mqtt_hello(HardwareHello(
            node_id="mic1", node_type=NodeType.MIC, protocol=ProtocolType.MQTT
        )))
        _run(orch._on_mqtt_hello(HardwareHello(
            node_id="spk1", node_type=NodeType.SPK, protocol=ProtocolType.MQTT
        )))
        assert len(orch.get_devices_by_type(NodeType.MIC)) == 1
        assert len(orch.get_devices_by_type(NodeType.SPK)) == 1
        assert len(orch.get_devices_by_protocol(ProtocolType.MQTT)) == 2
        assert len(orch.get_online_devices()) == 2

    def test_assign_room(self):
        from shared.audio_types import HardwareHello, NodeType, ProtocolType
        orch, _, _, _ = self._make_orchestrator()
        _run(orch._on_mqtt_hello(HardwareHello(
            node_id="mic1", node_type=NodeType.MIC, protocol=ProtocolType.MQTT
        )))
        device = _run(orch.assign_room("mic1", "wohnzimmer"))
        assert device.room_id == "wohnzimmer"
        assert len(orch.get_devices_by_room("wohnzimmer")) == 1

    def test_assign_room_nonexistent(self):
        orch, _, _, _ = self._make_orchestrator()
        device = _run(orch.assign_room("nonexistent", "wz"))
        assert device is None

    def test_device_health_offline(self):
        from shared.audio_types import HardwareHello, NodeType, ProtocolType, DeviceStatus
        orch, _, _, _ = self._make_orchestrator()
        orch._device_timeout = 0.0  # Immediate timeout
        _run(orch._on_mqtt_hello(HardwareHello(
            node_id="mic1", node_type=NodeType.MIC, protocol=ProtocolType.MQTT
        )))
        _run(orch._check_device_health())
        device = orch.get_device("mic1")
        assert device.status == DeviceStatus.OFFLINE

    def test_stats(self):
        from shared.audio_types import HardwareHello, NodeType, ProtocolType
        orch, _, _, _ = self._make_orchestrator()
        _run(orch._on_mqtt_hello(HardwareHello(
            node_id="mic1", node_type=NodeType.MIC, protocol=ProtocolType.MQTT
        )))
        stats = orch.stats
        assert stats["total_devices"] == 1
        assert stats["online_devices"] == 1
        assert stats["mqtt_discoveries"] == 1
        assert stats["is_running"] is False  # Not started yet

    def test_device_discovered_callback(self):
        from shared.audio_types import HardwareHello, NodeType, ProtocolType
        orch, _, _, _ = self._make_orchestrator()
        discovered = []
        async def on_new(device):
            discovered.append(device)
        orch.set_callbacks(on_discovered=on_new)
        _run(orch._on_mqtt_hello(HardwareHello(
            node_id="mic1", node_type=NodeType.MIC, protocol=ProtocolType.MQTT
        )))
        assert len(discovered) == 1
        assert discovered[0].device_id == "mic1"

    def test_force_scan(self):
        orch, _, mdns, ha = self._make_orchestrator()
        mdns.scan_once = AsyncMock(return_value={"dev1": {}})
        ha.sync_entities = AsyncMock(return_value={})
        results = _run(orch.force_scan())
        assert "mdns" in results
        assert "ha" in results


# ═══════════════════════════════════════════════════════════════════════════
#  7. Main.py Wiring
# ═══════════════════════════════════════════════════════════════════════════


class TestMainWiring:
    """main.py imports, globals, endpoints."""

    def test_imports(self):
        from brain_core.discovery.orchestrator import DiscoveryOrchestrator
        from brain_core.discovery.mqtt_listener import MQTTListener
        from brain_core.discovery.mDNS_scanner import MDNSScanner
        from brain_core.presence_manager import (
            PresenceManager, SessionManager,
            ExponentialMovingAverage, rssi_to_distance,
            distance_to_probability, amplitude_to_probability,
        )
        from shared.audio_types import (
            RSSIReading, RoomProbability, RoomProbabilityVector,
            SessionInfo, DiscoveredDevice, DeviceStatus, DetectionMethod,
        )
        assert True

    def test_main_has_discovery_globals(self):
        import brain_core.main as m
        assert hasattr(m, "mqtt_listener")
        assert hasattr(m, "mdns_scanner")
        assert hasattr(m, "discovery_orchestrator")

    def test_main_has_presence_manager(self):
        import brain_core.main as m
        from brain_core.presence_manager import PresenceManager
        assert isinstance(m.presence_manager, PresenceManager)
        assert m.presence_manager.sessions is not None

    def test_spatial_endpoints_registered(self):
        from brain_core.main import app
        routes = [r.path for r in app.routes]
        assert "/api/v1/spatial/presence" in routes
        assert "/api/v1/spatial/presence/{user_id}" in routes
        assert "/api/v1/spatial/rooms" in routes
        assert "/api/v1/spatial/sessions" in routes
        assert "/api/v1/spatial/presence/manual" in routes

    def test_discovery_endpoints_registered(self):
        from brain_core.main import app
        routes = [r.path for r in app.routes]
        assert "/api/v1/discovery/devices" in routes
        assert "/api/v1/discovery/devices/{device_id}" in routes
        assert "/api/v1/discovery/scan" in routes
        assert "/api/v1/discovery/ha/entities" in routes
        assert "/api/v1/discovery/ha/sync" in routes
        assert "/api/v1/discovery/devices/assign-room" in routes

    def test_discovery_init_exports(self):
        from brain_core.discovery import (
            MQTTListener,
            MDNSScanner,
            HomeAssistantBridge,
            DiscoveryOrchestrator,
        )
        assert True

    def test_audio_types_all_models(self):
        """Alle Phase-6-Modelle importierbar."""
        from shared.audio_types import (
            RSSIReading,
            RoomProbability,
            RoomProbabilityVector,
            SessionInfo,
            DiscoveredDevice,
            DeviceStatus,
            DetectionMethod,
            NodeType,
            ProtocolType,
            AudioChunkMeta,
            PatchRoute,
            PresenceEvent,
            SpeakerProfile,
            HardwareHello,
        )
        assert True


# ═══════════════════════════════════════════════════════════════════════════
#  8. Integration: Presence → Audio Router
# ═══════════════════════════════════════════════════════════════════════════


class TestPresenceAudioRouterIntegration:
    """PresenceManager → AudioRouter Wiring."""

    def test_presence_fires_audio_router(self):
        from brain_core.presence_manager import PresenceManager
        from brain_core.audio_router import AudioRouter
        from shared.audio_types import AudioChunkMeta

        router = AudioRouter()
        events = []
        original_handler = router.on_presence_change
        async def capture_handler(event):
            events.append(event)
            await original_handler(event)

        pm = PresenceManager(
            handover_threshold=0.5,
            handover_cooldown=0.0,
            on_presence_change=capture_handler,
        )

        # Start in wz
        _run(pm.update_audio(
            AudioChunkMeta(node_id="m1", room_id="wz", amplitude_rms=0.01), "u1"
        ))
        # Handover to kueche
        _run(pm.update_audio(
            AudioChunkMeta(node_id="m2", room_id="kueche", amplitude_rms=0.5), "u1"
        ))
        assert len(events) >= 1
        assert events[-1].to_room == "kueche"

    def test_session_context_preserved_after_handover(self):
        from brain_core.presence_manager import PresenceManager
        from shared.audio_types import AudioChunkMeta

        pm = PresenceManager(handover_threshold=0.5, handover_cooldown=0.0)

        _run(pm.update_audio(
            AudioChunkMeta(node_id="m1", room_id="wz", amplitude_rms=0.01), "u1"
        ))
        # Set conversation context
        pm.sessions.update_context("u1", "User fragt nach dem Wetter")

        # Handover
        _run(pm.update_audio(
            AudioChunkMeta(node_id="m2", room_id="kueche", amplitude_rms=0.5), "u1"
        ))

        session = pm.sessions.get_user_session("u1")
        assert session.current_room == "kueche"
        assert session.conversation_context == "User fragt nach dem Wetter"
        assert session.turn_count == 1  # Context was set with turn increment

    def test_multi_user_independent_rooms(self):
        """Zwei User in verschiedenen Räumen — unabhängig."""
        from brain_core.presence_manager import PresenceManager
        from shared.audio_types import AudioChunkMeta

        pm = PresenceManager()
        _run(pm.update_audio(
            AudioChunkMeta(node_id="m1", room_id="wz", amplitude_rms=0.3), "alice"
        ))
        _run(pm.update_audio(
            AudioChunkMeta(node_id="m2", room_id="kueche", amplitude_rms=0.3), "bob"
        ))

        assert pm.get_user_room("alice") == "wz"
        assert pm.get_user_room("bob") == "kueche"
        assert pm.stats["tracked_users"] == 2

        # Separate sessions
        s_alice = pm.sessions.get_user_session("alice")
        s_bob = pm.sessions.get_user_session("bob")
        assert s_alice.session_id != s_bob.session_id
        assert s_alice.current_room == "wz"
        assert s_bob.current_room == "kueche"
