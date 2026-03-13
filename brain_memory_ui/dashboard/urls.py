"""Dashboard URL Configuration — Phase 8: Live Monitoring."""
from django.urls import path
from dashboard import api

urlpatterns = [
    # Dashboard Views
    path("", api.dashboard_view, name="dashboard"),
    path("thinking/", api.thinking_stream_view, name="thinking-stream"),
    
    # API Endpoints – Hardware
    path("hardware/", api.hardware_overview, name="hardware-overview"),
    path("hardware/type/<str:node_type>/", api.nodes_by_type, name="nodes-by-type"),
    path("hardware/room/<slug:slug>/", api.room_detail, name="room-detail"),

    # API Endpoints – Plugin Management
    path("plugins/", api.plugins_list, name="plugins-list"),
    path("plugins/<str:name>/toggle/", api.plugin_toggle, name="plugin-toggle"),
    path("plugins/<str:name>/delete/", api.plugin_delete, name="plugin-delete"),

    # ── Phase 8: Live Monitoring Endpoints ────────────────────────────────
    # Ego / Consciousness / Interoception / Monologue
    path("live/ego/", api.ego_snapshot, name="ego-snapshot"),
    path("live/consciousness/", api.consciousness_state, name="consciousness-state"),
    path("live/interoception/", api.interoception_state, name="interoception-state"),
    path("live/monologue/", api.monologue_state, name="monologue-state"),

    # Memory & Agent
    path("live/memory/", api.memory_stats, name="memory-stats"),
    path("live/agent/", api.agent_status, name="agent-status"),
    path("live/agent/history/", api.agent_history, name="agent-history"),

    # Voice & Emotion
    path("live/voice/emotion/", api.voice_emotion, name="voice-emotion"),

    # Phone & Spatial
    path("live/phone/", api.phone_stats, name="phone-stats"),
    path("live/spatial/", api.spatial_overview, name="spatial-overview"),

    # System
    path("live/health/", api.system_health, name="system-health"),
    path("live/evolution/plugins/", api.evolution_plugins_live, name="evolution-plugins-live"),
    path("live/evolution/proposals/", api.evolution_proposals, name="evolution-proposals"),
    path("live/policy/audit/", api.policy_audit, name="policy-audit"),

    # Combined — ALLES in einem Call
    path("live/full/", api.dashboard_full, name="dashboard-full"),
]
