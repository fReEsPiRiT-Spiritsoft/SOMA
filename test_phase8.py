"""
SOMA-AI Phase 8 Tests — Dashboard & Monitoring
=================================================
Teste:
  - Ego-Snapshot Builder (_build_ego_snapshot): Consciousness + Interoception + Monologue
  - Memory-Snapshot Builder (_build_memory_snapshot)
  - main.py: Neue Ego/Dashboard REST-Endpoints (/api/v1/ego/*)
  - main.py: Erweitertes /api/v1/dashboard/full (enthält ego, phone, memory, agent)
  - Django Dashboard API: Phase 8 Endpoints (live/*) + _fetch_from_brain Helper
  - Django URLs: Alle Phase 8 Routes vorhanden
  - Integration: Ego-Snapshot Datenstruktur vollständig

Phase 8 Kernregel:
  Das Dashboard sieht SOMAs VOLLSTÄNDIGES Innenleben:
  Bewusstsein, Körpergefühl, Gedanken, Memory, Agent-Actions, Emotionen.
"""

import asyncio
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# ── Projekt-Root + Django Setup ──────────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "brain_memory_ui"))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core_settings.settings")

import django
django.setup()


def _run(coro):
    """Helper: Async-Coroutine synchron ausführen."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ═══════════════════════════════════════════════════════════════════════════
#  1. Interoception — SomaEmotionalVector für Dashboard
# ═══════════════════════════════════════════════════════════════════════════


class TestInteroceptionDashboard:
    """Phase 8: Interoception-Daten für Dashboard-Visualisierung."""

    def test_emotional_vector_has_all_fields(self):
        from brain_ego.interoception import SomaEmotionalVector
        v = SomaEmotionalVector()
        # Negative
        assert hasattr(v, "frustration")
        assert hasattr(v, "congestion")
        assert hasattr(v, "survival_anxiety")
        assert hasattr(v, "physical_stress")
        assert hasattr(v, "exhaustion")
        # Positive
        assert hasattr(v, "calm")
        assert hasattr(v, "vitality")
        assert hasattr(v, "clarity")
        # Meta
        assert hasattr(v, "dominant_feeling")
        assert hasattr(v, "arousal")
        assert hasattr(v, "valence")

    def test_emotional_vector_to_narrative(self):
        from brain_ego.interoception import SomaEmotionalVector
        v = SomaEmotionalVector(
            frustration=0.8,
            calm=0.1,
            vitality=0.1,
        )
        narrative = v.to_narrative()
        assert isinstance(narrative, str)
        assert len(narrative) > 10
        assert "frustriert" in narrative.lower() or "langsam" in narrative.lower()

    def test_emotional_vector_to_compact(self):
        from brain_ego.interoception import SomaEmotionalVector
        v = SomaEmotionalVector(dominant_feeling="calm", arousal=0.2, valence=0.7)
        compact = v.to_compact()
        assert "calm" in compact
        assert "arousal=" in compact
        assert "valence=" in compact

    def test_interoception_current_property(self):
        from brain_ego.interoception import Interoception, SomaEmotionalVector
        intero = Interoception()
        current = intero.current
        assert isinstance(current, SomaEmotionalVector)

    def test_interoception_feel_updates_state(self):
        """feel() mit Metriken verändert den Emotional Vector."""
        from brain_ego.interoception import Interoception
        from shared.health_schemas import SystemMetrics
        intero = Interoception()

        # Mock SystemMetrics
        metrics = MagicMock(spec=SystemMetrics)
        metrics.cpu_percent = 95.0
        metrics.ram_percent = 92.0
        metrics.gpu = MagicMock()
        metrics.gpu.vram_percent = 88.0
        metrics.gpu.gpu_temp_celsius = 90.0
        metrics.cpu_temp_celsius = 85.0
        metrics.load_level = "critical"

        result = intero.feel(metrics)
        # Bei kritischer Last sollte Frustration/Anxiety hoch sein
        assert result.frustration > 0.3 or result.survival_anxiety > 0.3

    def test_emotional_vector_default_neutral(self):
        from brain_ego.interoception import SomaEmotionalVector
        v = SomaEmotionalVector()
        assert v.dominant_feeling == "neutral"
        assert v.arousal == 0.0
        assert v.valence == 0.0


# ═══════════════════════════════════════════════════════════════════════════
#  2. Consciousness — State für Dashboard
# ═══════════════════════════════════════════════════════════════════════════


class TestConsciousnessDashboard:
    """Phase 8: ConsciousnessState für Dashboard-Visualisierung."""

    def test_consciousness_state_has_all_fields(self):
        from brain_ego.consciousness import ConsciousnessState
        s = ConsciousnessState()
        assert hasattr(s, "mood")
        assert hasattr(s, "attention_focus")
        assert hasattr(s, "current_thought")
        assert hasattr(s, "body_feeling")
        assert hasattr(s, "body_arousal")
        assert hasattr(s, "body_valence")
        assert hasattr(s, "uptime_feeling")
        assert hasattr(s, "diary_insight")
        assert hasattr(s, "recent_memory_summary")
        assert hasattr(s, "identity")
        assert hasattr(s, "update_count")
        assert hasattr(s, "generation_ms")
        assert hasattr(s, "perception")

    def test_consciousness_state_default_values(self):
        from brain_ego.consciousness import ConsciousnessState
        s = ConsciousnessState()
        assert s.mood == "neutral"
        assert s.attention_focus == "idle"
        assert s.current_thought == ""
        assert s.update_count == 0

    def test_perception_snapshot_has_all_fields(self):
        from brain_ego.consciousness import PerceptionSnapshot
        p = PerceptionSnapshot()
        assert hasattr(p, "last_user_text")
        assert hasattr(p, "last_soma_response")
        assert hasattr(p, "user_emotion")
        assert hasattr(p, "user_arousal")
        assert hasattr(p, "user_valence")
        assert hasattr(p, "room_id")
        assert hasattr(p, "room_mood")
        assert hasattr(p, "is_child_present")
        assert hasattr(p, "people_present")
        assert hasattr(p, "seconds_since_last_interaction")

    def test_consciousness_to_prompt_prefix(self):
        from brain_ego.consciousness import ConsciousnessState
        s = ConsciousnessState(
            identity="Ich bin SOMA",
            mood="zufrieden und gelassen",
            current_thought="Alles ist ruhig heute",
        )
        prefix = s.to_prompt_prefix()
        assert "SOMA" in prefix
        assert "BEWUSSTSEINSZUSTAND" in prefix

    def test_consciousness_to_compact_log(self):
        from brain_ego.consciousness import ConsciousnessState
        s = ConsciousnessState(mood="energisch", attention_focus="im Gespraech")
        log = s.to_compact_log()
        assert "mood=energisch" in log
        assert "focus=im Gespraech" in log

    def test_consciousness_class_exists(self):
        from brain_ego.consciousness import Consciousness
        assert Consciousness is not None

    def test_consciousness_state_property(self):
        """Consciousness hat ein .state Property."""
        from brain_ego.consciousness import Consciousness, ConsciousnessState
        from brain_ego.interoception import Interoception
        from brain_ego.identity_anchor import IdentityAnchor

        intero = Interoception()
        anchor = IdentityAnchor()
        c = Consciousness(interoception=intero, identity_anchor=anchor)
        state = c.state
        assert isinstance(state, ConsciousnessState)


# ═══════════════════════════════════════════════════════════════════════════
#  3. InternalMonologue — Stats für Dashboard
# ═══════════════════════════════════════════════════════════════════════════


class TestMonologueDashboard:
    """Phase 8: InternalMonologue Stats für Dashboard."""

    def test_monologue_stats_format(self):
        from brain_ego.internal_monologue import InternalMonologue
        from brain_ego.consciousness import Consciousness
        from brain_ego.interoception import Interoception
        from brain_ego.identity_anchor import IdentityAnchor
        intero = Interoception()
        anchor = IdentityAnchor()
        c = Consciousness(interoception=intero, identity_anchor=anchor)
        m = InternalMonologue(consciousness=c)
        stats = m.stats
        assert isinstance(stats, dict)
        assert "thoughts_generated" in stats
        assert "thoughts_spoken" in stats
        assert "has_llm" in stats
        assert "has_speak" in stats

    def test_monologue_default_stats(self):
        from brain_ego.internal_monologue import InternalMonologue
        from brain_ego.consciousness import Consciousness
        from brain_ego.interoception import Interoception
        from brain_ego.identity_anchor import IdentityAnchor
        intero = Interoception()
        anchor = IdentityAnchor()
        c = Consciousness(interoception=intero, identity_anchor=anchor)
        m = InternalMonologue(consciousness=c)
        stats = m.stats
        assert stats["thoughts_generated"] == 0
        assert stats["thoughts_spoken"] == 0
        assert stats["has_llm"] is False
        assert stats["has_speak"] is False


# ═══════════════════════════════════════════════════════════════════════════
#  4. main.py — Ego Endpoints Existenz
# ═══════════════════════════════════════════════════════════════════════════


class TestMainEgoEndpoints:
    """Phase 8: main.py hat Ego/Consciousness/Interoception/Monologue Endpoints."""

    def _get_routes(self):
        import brain_core.main as m
        return [r.path for r in m.app.routes if hasattr(r, "path")]

    def test_ego_consciousness_endpoint(self):
        assert "/api/v1/ego/consciousness" in self._get_routes()

    def test_ego_interoception_endpoint(self):
        assert "/api/v1/ego/interoception" in self._get_routes()

    def test_ego_monologue_endpoint(self):
        assert "/api/v1/ego/monologue" in self._get_routes()

    def test_ego_snapshot_endpoint(self):
        assert "/api/v1/ego/snapshot" in self._get_routes()

    def test_dashboard_full_endpoint(self):
        assert "/api/v1/dashboard/full" in self._get_routes()

    def test_memory_stats_endpoint(self):
        assert "/api/v1/memory/stats" in self._get_routes()

    def test_agent_status_endpoint(self):
        assert "/api/v1/agent/status" in self._get_routes()

    def test_voice_emotion_endpoint(self):
        assert "/api/v1/voice/emotion" in self._get_routes()


# ═══════════════════════════════════════════════════════════════════════════
#  5. main.py — _build_ego_snapshot() Logic
# ═══════════════════════════════════════════════════════════════════════════


class TestEgoSnapshotBuilder:
    """Phase 8: _build_ego_snapshot() vereinigt Consciousness + Interoception + Monologue."""

    def test_build_ego_snapshot_exists(self):
        import brain_core.main as m
        assert hasattr(m, "_build_ego_snapshot")
        assert callable(m._build_ego_snapshot)

    def test_ego_snapshot_returns_dict(self):
        import brain_core.main as m
        result = m._build_ego_snapshot()
        assert isinstance(result, dict)

    def test_ego_snapshot_has_interoception(self):
        import brain_core.main as m
        result = m._build_ego_snapshot()
        assert "interoception" in result

    def test_ego_snapshot_interoception_has_emotions(self):
        import brain_core.main as m
        result = m._build_ego_snapshot()
        intero = result["interoception"]
        # Entweder volle Daten oder status
        if "status" not in intero:
            assert "frustration" in intero
            assert "calm" in intero
            assert "vitality" in intero
            assert "dominant_feeling" in intero
            assert "arousal" in intero
            assert "valence" in intero
            assert "narrative" in intero

    def test_ego_snapshot_has_consciousness(self):
        import brain_core.main as m
        result = m._build_ego_snapshot()
        assert "consciousness" in result

    def test_ego_snapshot_has_monologue(self):
        import brain_core.main as m
        result = m._build_ego_snapshot()
        assert "monologue" in result

    def test_ego_snapshot_has_status(self):
        import brain_core.main as m
        result = m._build_ego_snapshot()
        assert "status" in result

    def test_ego_snapshot_consciousness_fields_when_online(self):
        """Wenn Consciousness läuft, hat der Snapshot alle Felder."""
        import brain_core.main as m
        if m.soma_consciousness is not None:
            result = m._build_ego_snapshot()
            c = result["consciousness"]
            if "status" not in c:
                assert "mood" in c
                assert "attention_focus" in c
                assert "current_thought" in c
                assert "body_feeling" in c
                assert "perception" in c
                assert "update_count" in c


# ═══════════════════════════════════════════════════════════════════════════
#  6. main.py — _build_memory_snapshot() Logic
# ═══════════════════════════════════════════════════════════════════════════


class TestMemorySnapshotBuilder:
    """Phase 8: _build_memory_snapshot() für Dashboard."""

    def test_build_memory_snapshot_exists(self):
        import brain_core.main as m
        assert hasattr(m, "_build_memory_snapshot")

    def test_memory_snapshot_returns_dict(self):
        import brain_core.main as m
        result = _run(m._build_memory_snapshot())
        assert isinstance(result, dict)

    def test_memory_snapshot_graceful_on_error(self):
        """Ohne Memory-System: kein Crash, nur 'unavailable'."""
        import brain_core.main as m
        # Wenn Orchestrator nicht initialisiert → graceful degradation
        result = _run(m._build_memory_snapshot())
        # Entweder echte Daten oder "unavailable"
        assert isinstance(result, dict)


# ═══════════════════════════════════════════════════════════════════════════
#  7. main.py — Dashboard/Full enthält Ego-Daten
# ═══════════════════════════════════════════════════════════════════════════


class TestDashboardFullExtended:
    """Phase 8: /api/v1/dashboard/full enthält jetzt ego, phone, memory, agent."""

    def test_dashboard_full_source_has_ego(self):
        """Source code enthält ego-Daten im Dashboard-Full Response."""
        import inspect
        import brain_core.main as m
        source = inspect.getsource(m.get_dashboard_data)
        assert "ego" in source
        assert "_build_ego_snapshot" in source

    def test_dashboard_full_source_has_phone(self):
        import inspect
        import brain_core.main as m
        source = inspect.getsource(m.get_dashboard_data)
        assert "phone" in source

    def test_dashboard_full_source_has_memory(self):
        import inspect
        import brain_core.main as m
        source = inspect.getsource(m.get_dashboard_data)
        assert "memory" in source
        assert "_build_memory_snapshot" in source

    def test_dashboard_full_source_has_agent(self):
        import inspect
        import brain_core.main as m
        source = inspect.getsource(m.get_dashboard_data)
        assert "agent" in source


# ═══════════════════════════════════════════════════════════════════════════
#  8. Django Dashboard API — Phase 8 Endpoint Functions
# ═══════════════════════════════════════════════════════════════════════════


class TestDjangoDashboardAPI:
    """Phase 8: Django Dashboard API hat alle Live-Endpoint-Functions."""

    def test_ego_snapshot_function_exists(self):
        from dashboard import api
        assert hasattr(api, "ego_snapshot")
        assert callable(api.ego_snapshot)

    def test_consciousness_state_function_exists(self):
        from dashboard import api
        assert hasattr(api, "consciousness_state")
        assert callable(api.consciousness_state)

    def test_interoception_state_function_exists(self):
        from dashboard import api
        assert hasattr(api, "interoception_state")
        assert callable(api.interoception_state)

    def test_monologue_state_function_exists(self):
        from dashboard import api
        assert hasattr(api, "monologue_state")
        assert callable(api.monologue_state)

    def test_memory_stats_function_exists(self):
        from dashboard import api
        assert hasattr(api, "memory_stats")
        assert callable(api.memory_stats)

    def test_agent_status_function_exists(self):
        from dashboard import api
        assert hasattr(api, "agent_status")
        assert callable(api.agent_status)

    def test_agent_history_function_exists(self):
        from dashboard import api
        assert hasattr(api, "agent_history")
        assert callable(api.agent_history)

    def test_voice_emotion_function_exists(self):
        from dashboard import api
        assert hasattr(api, "voice_emotion")
        assert callable(api.voice_emotion)

    def test_phone_stats_function_exists(self):
        from dashboard import api
        assert hasattr(api, "phone_stats")
        assert callable(api.phone_stats)

    def test_spatial_overview_function_exists(self):
        from dashboard import api
        assert hasattr(api, "spatial_overview")
        assert callable(api.spatial_overview)

    def test_dashboard_full_function_exists(self):
        from dashboard import api
        assert hasattr(api, "dashboard_full")
        assert callable(api.dashboard_full)

    def test_system_health_function_exists(self):
        from dashboard import api
        assert hasattr(api, "system_health")
        assert callable(api.system_health)

    def test_evolution_plugins_live_function_exists(self):
        from dashboard import api
        assert hasattr(api, "evolution_plugins_live")
        assert callable(api.evolution_plugins_live)

    def test_evolution_proposals_function_exists(self):
        from dashboard import api
        assert hasattr(api, "evolution_proposals")
        assert callable(api.evolution_proposals)

    def test_policy_audit_function_exists(self):
        from dashboard import api
        assert hasattr(api, "policy_audit")
        assert callable(api.policy_audit)

    def test_fetch_from_brain_helper_exists(self):
        from dashboard import api
        assert hasattr(api, "_fetch_from_brain")
        assert callable(api._fetch_from_brain)

    def test_brain_core_url_defined(self):
        from dashboard import api
        assert hasattr(api, "BRAIN_CORE_URL")
        assert "localhost" in api.BRAIN_CORE_URL or "127.0.0.1" in api.BRAIN_CORE_URL


# ═══════════════════════════════════════════════════════════════════════════
#  9. Django Dashboard API — _fetch_from_brain Helper
# ═══════════════════════════════════════════════════════════════════════════


class TestFetchFromBrain:
    """Phase 8: _fetch_from_brain() HTTP-Helper robustheit."""

    def test_fetch_returns_dict(self):
        from dashboard.api import _fetch_from_brain
        # brain_core läuft nicht → Fehler graceful gehandelt
        result = _fetch_from_brain("/api/v1/nonexistent", timeout=0.5)
        assert isinstance(result, dict)

    def test_fetch_error_contains_source(self):
        from dashboard.api import _fetch_from_brain
        result = _fetch_from_brain("/api/v1/test_fail", timeout=0.5)
        # Bei Connection-Error: source + error + status
        if "error" in result:
            assert "source" in result or "status" in result

    def test_fetch_graceful_on_timeout(self):
        from dashboard.api import _fetch_from_brain
        # Timeout sollte nicht crashen
        result = _fetch_from_brain("/api/v1/timeout_test", timeout=0.1)
        assert isinstance(result, dict)


# ═══════════════════════════════════════════════════════════════════════════
#  10. Django URLs — Alle Phase 8 Routes vorhanden
# ═══════════════════════════════════════════════════════════════════════════


class TestDjangoURLs:
    """Phase 8: Django Dashboard URLs enthalten alle Live-Endpoints."""

    def _get_url_patterns(self):
        from dashboard.urls import urlpatterns
        return [str(p.pattern) for p in urlpatterns]

    def test_ego_snapshot_url(self):
        assert "live/ego/" in self._get_url_patterns()

    def test_consciousness_url(self):
        assert "live/consciousness/" in self._get_url_patterns()

    def test_interoception_url(self):
        assert "live/interoception/" in self._get_url_patterns()

    def test_monologue_url(self):
        assert "live/monologue/" in self._get_url_patterns()

    def test_memory_url(self):
        assert "live/memory/" in self._get_url_patterns()

    def test_agent_status_url(self):
        assert "live/agent/" in self._get_url_patterns()

    def test_agent_history_url(self):
        assert "live/agent/history/" in self._get_url_patterns()

    def test_voice_emotion_url(self):
        assert "live/voice/emotion/" in self._get_url_patterns()

    def test_phone_stats_url(self):
        assert "live/phone/" in self._get_url_patterns()

    def test_spatial_url(self):
        assert "live/spatial/" in self._get_url_patterns()

    def test_health_url(self):
        assert "live/health/" in self._get_url_patterns()

    def test_evolution_plugins_url(self):
        assert "live/evolution/plugins/" in self._get_url_patterns()

    def test_evolution_proposals_url(self):
        assert "live/evolution/proposals/" in self._get_url_patterns()

    def test_policy_audit_url(self):
        assert "live/policy/audit/" in self._get_url_patterns()

    def test_dashboard_full_url(self):
        assert "live/full/" in self._get_url_patterns()

    def test_total_url_count(self):
        """Mindestens 21 URLs (6 alte + 15 neue Phase 8)."""
        urls = self._get_url_patterns()
        assert len(urls) >= 21


# ═══════════════════════════════════════════════════════════════════════════
#  11. Integration — Ego Snapshot Datenstruktur
# ═══════════════════════════════════════════════════════════════════════════


class TestEgoSnapshotIntegration:
    """Phase 8: Ego-Snapshot-Datenstruktur vollständig und konsistent."""

    def test_snapshot_interoception_values_are_rounded(self):
        """Interoception-Werte sind gerundet (3 Dezimalstellen)."""
        import brain_core.main as m
        result = m._build_ego_snapshot()
        intero = result.get("interoception", {})
        if "frustration" in intero:
            # Prüfe dass alle floats gerundet sind
            for key in ["frustration", "congestion", "survival_anxiety",
                        "physical_stress", "exhaustion", "calm", "vitality",
                        "clarity", "arousal", "valence"]:
                val = intero.get(key, 0)
                assert isinstance(val, (int, float))
                # Max 3 Dezimalstellen
                assert val == round(val, 3)

    def test_snapshot_structure_documented(self):
        """Ego-Snapshot hat eine dokumentierte Struktur."""
        import brain_core.main as m
        result = m._build_ego_snapshot()

        # Top-level Keys
        assert "status" in result
        assert "interoception" in result
        assert "consciousness" in result
        assert "monologue" in result

    def test_snapshot_handles_offline_gracefully(self):
        """Wenn Subsysteme offline: status=unavailable/not_started."""
        import brain_core.main as m
        result = m._build_ego_snapshot()

        # Consciousness kann not_started sein
        c = result["consciousness"]
        assert "status" in c or "mood" in c

        # Monologue kann not_started sein
        mn = result["monologue"]
        assert "status" in mn or "thoughts_generated" in mn


# ═══════════════════════════════════════════════════════════════════════════
#  12. WebSocket — Thinking Stream existiert
# ═══════════════════════════════════════════════════════════════════════════


class TestWebSocketThinking:
    """Phase 8: WebSocket /ws/thinking existiert und ist konfiguriert."""

    def test_ws_thinking_endpoint_exists(self):
        import brain_core.main as m
        routes = []
        for r in m.app.routes:
            if hasattr(r, "path"):
                routes.append(r.path)
        assert "/ws/thinking" in routes

    def test_broadcast_thought_function_exists(self):
        import brain_core.main as m
        assert hasattr(m, "broadcast_thought")
        assert callable(m.broadcast_thought)

    def test_ws_connections_set_exists(self):
        import brain_core.main as m
        assert hasattr(m, "ws_connections")


# ═══════════════════════════════════════════════════════════════════════════
#  13. Cross-System Consistency
# ═══════════════════════════════════════════════════════════════════════════


class TestCrossSystemConsistency:
    """Phase 8: Alle Subsysteme sind konsistent verdrahtet."""

    def test_interoception_global_exists(self):
        import brain_core.main as m
        assert hasattr(m, "interoception")
        from brain_ego.interoception import Interoception
        assert isinstance(m.interoception, Interoception)

    def test_identity_anchor_global_exists(self):
        import brain_core.main as m
        assert hasattr(m, "identity_anchor")
        from brain_ego.identity_anchor import IdentityAnchor
        assert isinstance(m.identity_anchor, IdentityAnchor)

    def test_soma_consciousness_global_exists(self):
        import brain_core.main as m
        assert hasattr(m, "soma_consciousness")

    def test_internal_monologue_global_exists(self):
        import brain_core.main as m
        assert hasattr(m, "internal_monologue")

    def test_health_monitor_global_exists(self):
        import brain_core.main as m
        assert hasattr(m, "health_monitor")

    def test_logic_router_global_exists(self):
        import brain_core.main as m
        assert hasattr(m, "logic_router")

    def test_voice_pipeline_global_exists(self):
        import brain_core.main as m
        assert hasattr(m, "voice_pipeline")

    def test_phone_pipeline_global_exists(self):
        import brain_core.main as m
        assert hasattr(m, "phone_pipeline")

    def test_soma_agent_global_exists(self):
        import brain_core.main as m
        assert hasattr(m, "soma_agent")

    def test_policy_engine_global_exists(self):
        import brain_core.main as m
        assert hasattr(m, "policy_engine")

    def test_all_endpoints_counted(self):
        """main.py hat mindestens 40 Endpoints (alle Phasen zusammen)."""
        import brain_core.main as m
        routes = [r.path for r in m.app.routes if hasattr(r, "path")]
        # Phase 1-8 zusammen: viele Endpoints
        assert len(routes) >= 40, f"Nur {len(routes)} Endpoints gefunden"


# ═══════════════════════════════════════════════════════════════════════════
#  14. Edge Cases & Robustheit
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Phase 8: Robustheit-Tests für Dashboard-Endpoints."""

    def test_ego_snapshot_no_crash_with_defaults(self):
        """Ego Snapshot mit Default-Interoception crasht nicht."""
        import brain_core.main as m
        try:
            result = m._build_ego_snapshot()
            assert isinstance(result, dict)
        except Exception as e:
            pytest.fail(f"_build_ego_snapshot crashed: {e}")

    def test_memory_snapshot_no_crash(self):
        """Memory Snapshot crasht nicht wenn Memory-System nicht läuft."""
        import brain_core.main as m
        try:
            result = _run(m._build_memory_snapshot())
            assert isinstance(result, dict)
        except Exception as e:
            pytest.fail(f"_build_memory_snapshot crashed: {e}")

    def test_django_fetch_no_crash_on_bad_url(self):
        """_fetch_from_brain crasht nicht bei ungültiger URL."""
        from dashboard.api import _fetch_from_brain
        result = _fetch_from_brain("/this/does/not/exist", timeout=0.3)
        assert isinstance(result, dict)

    def test_django_fetch_no_crash_on_invalid_host(self):
        """_fetch_from_brain crasht nicht bei unreachable Host."""
        from dashboard import api
        original = api.BRAIN_CORE_URL
        try:
            api.BRAIN_CORE_URL = "http://192.168.255.255:9999"
            result = api._fetch_from_brain("/api/v1/health", timeout=0.3)
            assert isinstance(result, dict)
            assert "error" in result
        finally:
            api.BRAIN_CORE_URL = original

    def test_consciousness_state_when_none(self):
        """Ego Snapshot ist graceful wenn consciousness=None."""
        import brain_core.main as m
        saved = m.soma_consciousness
        try:
            m.soma_consciousness = None
            result = m._build_ego_snapshot()
            assert result["consciousness"]["status"] == "not_started"
        finally:
            m.soma_consciousness = saved

    def test_monologue_state_when_none(self):
        """Ego Snapshot ist graceful wenn monologue=None."""
        import brain_core.main as m
        saved = m.internal_monologue
        try:
            m.internal_monologue = None
            result = m._build_ego_snapshot()
            assert result["monologue"]["status"] == "not_started"
        finally:
            m.internal_monologue = saved


# ═══════════════════════════════════════════════════════════════════════════
#  15. Django Alte Endpoints intakt
# ═══════════════════════════════════════════════════════════════════════════


class TestDjangoLegacyEndpoints:
    """Phase 8: Bestehende Django-Endpoints sind unverändert."""

    def test_dashboard_view_exists(self):
        from dashboard import api
        assert hasattr(api, "dashboard_view")

    def test_thinking_stream_view_exists(self):
        from dashboard import api
        assert hasattr(api, "thinking_stream_view")

    def test_hardware_overview_exists(self):
        from dashboard import api
        assert hasattr(api, "hardware_overview")

    def test_plugins_list_exists(self):
        from dashboard import api
        assert hasattr(api, "plugins_list")

    def test_plugin_toggle_exists(self):
        from dashboard import api
        assert hasattr(api, "plugin_toggle")

    def test_plugin_delete_exists(self):
        from dashboard import api
        assert hasattr(api, "plugin_delete")

    def test_collect_plugins_helper(self):
        from dashboard import api
        assert hasattr(api, "_collect_plugins")
        plugins = api._collect_plugins()
        assert isinstance(plugins, list)

    def test_legacy_urls_intact(self):
        from dashboard.urls import urlpatterns
        patterns = [str(p.pattern) for p in urlpatterns]
        assert "" in patterns  # dashboard root
        assert "thinking/" in patterns
        assert "hardware/" in patterns
        assert "plugins/" in patterns
