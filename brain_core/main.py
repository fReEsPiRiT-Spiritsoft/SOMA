"""
SOMA-AI Brain Core – FastAPI Einstiegspunkt
=============================================
Das Bewusstsein: Verbindet alle Subsysteme und exponiert die REST/WS API.

Startup-Sequenz:
  1. Config laden (.env)
  2. Redis verbinden (Queue)
  3. MQTT verbinden (Hardware Nervous System)
  4. HealthMonitor starten (System-Vitals)
  5. Engines registrieren (Heavy/Light/Nano)
  6. LogicRouter initialisieren
  7. Queue-Worker starten (Deferred Reasoning)
  8. Discovery-Services starten (MQTT/mDNS/HA)
  9. API bereitstellen
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from brain_core.config import settings
from brain_core.health_monitor import HealthMonitor
from brain_core.logic_router import LogicRouter, SomaRequest, SomaResponse
from brain_core.voice.pipeline import VoicePipeline
from brain_core.queue_handler import QueueHandler
from brain_core.presence_manager import PresenceManager
from brain_core.audio_router import AudioRouter
from brain_core.engines.heavy_llama import HeavyLlamaEngine
from brain_core.engines.nano_intent import NanoIntentEngine
from brain_core.engines.light_phi import LightPhiEngine
from brain_core.engines.speculative_engine import SpeculativeEngine
from brain_core.discovery.ha_bridge import HomeAssistantBridge
from brain_core.discovery.mqtt_listener import MQTTListener
from brain_core.discovery.mDNS_scanner import MDNSScanner
from brain_core.discovery.orchestrator import DiscoveryOrchestrator
from brain_core.phone.phone_pipeline import PhonePipeline
from shared.health_schemas import SystemMetrics, SystemHealthReport
from evolution_lab.plugin_manager import PluginManager, PluginGenerator
from evolution_lab.self_improver import SelfImprovementEngine, ProposalStatus
from brain_core.memory.setup_embeddings import ensure_embedding_model
from brain_core.memory.integration import (
    init_memory_system,
    shutdown_memory_system,
    set_consolidation_llm,
    set_diary_llm,
    get_orchestrator,
    store_system_event as memory_store_event,
)
from brain_ego.interoception import Interoception
from brain_ego.identity_anchor import IdentityAnchor
from brain_ego.consciousness import Consciousness, PerceptionSnapshot
from brain_ego.internal_monologue import InternalMonologue
from brain_core.logic_router import set_consciousness as set_logic_consciousness
from brain_core.logic_router import set_broadcast_function
from executive_arm.policy_engine import PolicyEngine
from executive_arm.filesystem_map import FilesystemMap
from executive_arm.terminal import SecureTerminal
from executive_arm.toolset import Toolset
from executive_arm.agency import SomaAgent
from pathlib import Path
from pydantic import BaseModel as PydanticBaseModel

logger = structlog.get_logger("soma.brain")

# ── Global Service Instances ─────────────────────────────────────────────

queue_handler = QueueHandler()
presence_manager = PresenceManager()
audio_router = AudioRouter()

# Engines (vor HealthMonitor erstellen, damit heavy_engine übergeben werden kann)
heavy_engine = HeavyLlamaEngine()
health_monitor = HealthMonitor(interval=5.0, heavy_engine=heavy_engine)
light_engine = LightPhiEngine()
nano_engine = NanoIntentEngine()
speculative_engine = SpeculativeEngine() if settings.speculative_enabled else None

# Logic Router (wird im Lifespan initialisiert)
logic_router: LogicRouter | None = None

# WebSocket connections für Live-Dashboard
ws_connections: set[WebSocket] = set()

# Voice Pipeline (Das lebendige Ohr + Mund)
voice_pipeline: VoicePipeline | None = None

# Evolution Lab
plugin_manager = PluginManager()
plugin_generator: PluginGenerator | None = None
self_improver: SelfImprovementEngine | None = None

# Home Assistant Bridge — LLM steuert Smart Home via [ACTION:ha_call]
ha_bridge: HomeAssistantBridge = HomeAssistantBridge()

# Discovery Services (Phase 6)
mqtt_listener = MQTTListener()
mdns_scanner = MDNSScanner()
discovery_orchestrator: DiscoveryOrchestrator | None = None

# Phone Gateway (Asterisk ARI → Festnetz)
phone_pipeline: PhonePipeline | None = None

# ── Ego System (Phase 2) — SOMAs ICH-Bewusstsein ────────────────────────
interoception = Interoception()
identity_anchor = IdentityAnchor()
soma_consciousness: Consciousness | None = None
internal_monologue: InternalMonologue | None = None

# ── Executive Arm (Phase 3) — SOMAs HANDLUNGSFÄHIGKEIT ──────────────────
policy_engine = PolicyEngine(identity_anchor=identity_anchor)
filesystem_map = FilesystemMap()
secure_terminal = SecureTerminal(policy_engine=policy_engine)
soma_toolset: Toolset | None = None
soma_agent: SomaAgent | None = None


# ── Reminder Speak (Global für Import) ───────────────────────────────────

def get_pipeline():
    """Gibt die aktuelle VoicePipeline-Instanz zurück (oder None)."""
    return voice_pipeline


async def reminder_speak(text: str):
    """
    Globaler TTS-Callback für Erinnerungen.
    Nutzt autonomous_speak der Pipeline → Priority-TTS + Dashboard-Event.
    """
    global voice_pipeline
    if voice_pipeline is None:
        logger.warning("reminder_speak_no_pipeline", text=text)
        return

    await broadcast_thought("info", f"⏰ ERINNERUNG: {text}", "REMINDER")
    logger.info("reminder_speaking", text=text)
    await voice_pipeline.autonomous_speak(text)


# ── Thought Broadcasting ─────────────────────────────────────────────────

async def broadcast_thought(
    thought_type: str, 
    content: str, 
    tag: str | None = None,
    extra: dict | None = None
):
    """
    Pushe einen 'Gedanken' an alle Dashboard-Clients.
    
    Args:
        thought_type: info, stt, llm, tts, emotion, warn, error
        content: Der Gedankentext
        tag: Optional category tag
        extra: Optional additional data
    """
    import json
    import time
    
    if not ws_connections:
        return
    
    payload = json.dumps({
        "type": "thought",
        "thought_type": thought_type,
        "content": content,
        "tag": tag,
        "timestamp": time.time(),
        "extra": extra or {},
    })
    
    dead: list[WebSocket] = []
    for ws in ws_connections:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_connections.discard(ws)


# ── Lifespan (Startup / Shutdown) ────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """SOMA-AI Boot-Sequenz."""
    global logic_router

    logger.info("soma_booting", phase="startup")

    # 1. Redis Queue verbinden
    try:
        await queue_handler.connect()
        logger.info("boot_phase", service="redis", status="connected")
    except Exception as exc:
        logger.error("boot_phase", service="redis", status="failed", error=str(exc))

    # 2. Home Assistant Bridge verbinden (LLM steuert Smart Home)
    try:
        await ha_bridge.connect()
        logger.info("boot_phase", service="ha_bridge", status="connected")
    except Exception as exc:
        logger.warning("boot_phase", service="ha_bridge", status="failed", error=str(exc))

    # 2. Health Monitor starten mit Broadcast-Callback + Interoception
    async def broadcast_metrics(metrics: SystemMetrics):
        """Pushe Metriken an alle verbundenen WebSocket-Clients + Ego-System."""
        # ── Interoception: Hardware → Körpergefühl ───────────────────
        interoception.feel(metrics)
        # ── Consciousness benachrichtigen ────────────────────────────
        if soma_consciousness is not None:
            soma_consciousness.notify_body_state_changed()

        if not ws_connections:
            return
        data = metrics.model_dump_json()
        dead: list[WebSocket] = []
        for ws in ws_connections:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            ws_connections.discard(ws)

    health_monitor._on_metrics = broadcast_metrics
    await health_monitor.start()
    logger.info("boot_phase", service="health_monitor", status="started")

    # 3. Engines initialisieren
    await heavy_engine.initialize()
    await light_engine.initialize()
    await nano_engine.initialize()
    if speculative_engine:
        await speculative_engine.initialize()
        logger.info("boot_phase", service="speculative_engine", status="enabled ⚡")
    logger.info("boot_phase", service="engines", status="initialized")

    # 3a. System Profile — Dynamische Erkennung von OS, DE, Tools
    try:
        from brain_core.system_profile import init_profile
        sys_profile = await init_profile()
        logger.info(
            "boot_phase", service="system_profile", status="detected",
            os=sys_profile.os_name, de=sys_profile.desktop_env,
            display=sys_profile.display_server,
        )
    except Exception as exc:
        logger.warning("boot_phase", service="system_profile",
                       status="failed", error=str(exc))

    # 3b. Memory System (3-Layer Hierarchy + Embeddings)
    try:
        await ensure_embedding_model()
        memory_orchestrator = await init_memory_system()

        # Consolidation-LLM: Light Engine (entlastet Heavy für User-Anfragen)
        # Phase F: 1B-Modell reicht für strukturierte Fakten-Extraktion
        async def _consolidation_llm(prompt: str) -> str:
            return await light_engine.generate(prompt=prompt, system_prompt="")
        set_consolidation_llm(_consolidation_llm)

        # Diary-LLM: Light Engine für narrative Tagebuch-Einträge
        async def _diary_llm(prompt: str) -> str:
            return await light_engine.generate(prompt=prompt, system_prompt="")
        set_diary_llm(_diary_llm)

        logger.info("boot_phase", service="memory_system", status="online 🧠")
        await broadcast_thought(
            "info",
            "🧠 Memory System online — 3 Gedächtnis-Ebenen aktiv",
            "BOOT",
        )

        # ── User Identity: Nutzer aus Gedächtnis laden ─────────────
        from brain_core.memory.user_identity import (
            get_user_name, is_onboarding_needed, set_user_name,
        )
        user_name = await get_user_name()
        if user_name and user_name != "du":
            # Nutzername dem WorkingMemory geben
            memory_orchestrator.working.set_user_name(user_name)
            logger.info("boot_user_identity", name=user_name)
            await broadcast_thought(
                "info",
                f"👤 Nutzer erkannt: {user_name}",
                "BOOT",
            )
        else:
            needs_onboarding = await is_onboarding_needed()
            if needs_onboarding:
                logger.info("boot_onboarding_needed", reason="empty_memory")
                await broadcast_thought(
                    "info",
                    "🆕 Erstes Erwachen — Onboarding wird beim ersten Gespräch gestartet",
                    "BOOT",
                )
    except Exception as exc:
        logger.error("boot_phase", service="memory_system", status="failed", error=str(exc))
        await broadcast_thought("warn", f"Memory System Fehler: {exc}", "BOOT")

    # ════════════════════════════════════════════════════════════════════
    #  3c. KILLER FEATURES — Claude Code Patterns für SOMA
    # ════════════════════════════════════════════════════════════════════
    try:
        # SideQuery Engine: Light-Modell für Meta-Tasks (permanent in VRAM)
        from brain_core.side_query import get_side_query
        side_query = get_side_query()
        logger.info("boot_phase", service="side_query", status="ready ⚡")

        # AutoCompact: Context-Compression vor Token-Limit
        from brain_core.auto_compact import get_auto_compact
        auto_compact = get_auto_compact()
        logger.info("boot_phase", service="auto_compact", status="ready")

        # Away Summary: Willkommen-zurück nach Abwesenheit
        from brain_core.away_summary import get_away_summary
        away_summary = get_away_summary()
        logger.info("boot_phase", service="away_summary", status="ready")

        # Memory Extractor: Auto-Extraktion aus Konversation
        from brain_core.memory.auto_extract import get_memory_extractor
        mem_extractor = get_memory_extractor()
        logger.info("boot_phase", service="memory_extractor", status="ready")

        # AutoDream Enhanced: Session-basierte tiefe Konsolidierung
        from brain_core.memory.auto_dream_enhanced import get_auto_dream
        auto_dream = get_auto_dream()
        # LLM für Deep Dream = Light Engine (spart Heavy für User)
        async def _dream_llm(prompt: str) -> str:
            return await light_engine.generate(prompt=prompt, system_prompt="")
        auto_dream.set_llm(_dream_llm)
        try:
            _orch = get_orchestrator()
            if _orch and hasattr(_orch, 'diary') and _orch.diary:
                auto_dream.set_diary(lambda text: _orch.diary.write_insight(text))
        except RuntimeError:
            pass  # Memory system not yet initialized
        auto_dream.set_broadcast(broadcast_thought)
        logger.info("boot_phase", service="auto_dream_enhanced", status="ready 💤")

        # Cron Scheduler: Zeitgesteuerte Tasks
        from brain_core.cron_scheduler import get_cron_scheduler
        cron_scheduler = get_cron_scheduler()
        cron_scheduler.set_broadcast(broadcast_thought)
        cron_scheduler.start()
        logger.info("boot_phase", service="cron_scheduler", status="started ⏰")

        # Web Fetch Enhanced: LLM-verarbeitung von Web-Inhalten
        from brain_core.web_fetch_enhanced import get_web_fetch
        web_fetch = get_web_fetch()
        web_fetch.set_side_query(side_query.query)
        logger.info("boot_phase", service="web_fetch_enhanced", status="ready 🌐")

        await broadcast_thought(
            "info",
            "⚡ Killer Features aktiv — SideQuery, AutoCompact, Cron, WebFetch, AutoDream",
            "BOOT",
        )
    except Exception as exc:
        logger.warning("boot_phase", service="killer_features", status="partial", error=str(exc))
        await broadcast_thought("warn", f"Killer Features teilweise: {exc}", "BOOT")

    # 4. Logic Router zusammenbauen (mit Plugin-Integration)
    logic_router = LogicRouter(
        health_monitor=health_monitor,
        queue_handler=queue_handler,
        plugin_manager=plugin_manager,  # Evolution Lab Integration
    )
    logic_router.register_engine("heavy", heavy_engine)
    logic_router.register_engine("light", light_engine)
    logic_router.register_engine("nano", nano_engine)
    if speculative_engine:
        logic_router.register_engine("speculative", speculative_engine)

    # LogicRouter mit Broadcast-Funktionalität verbinden
    set_broadcast_function(broadcast_thought)

    # 5. Queue Worker starten (verarbeitet deferred requests)
    # ── Process Callback: Verarbeitet geparkte Anfragen mit Heavy-Engine ──
    async def process_deferred(req):
        """Deferred Request mit Heavy-Engine verarbeiten.

        Nutzt den gespeicherten System-Prompt aus dem ursprünglichen Request
        damit Kontext (Memory, Plugins, Emotion) erhalten bleibt.
        """
        # System-Prompt aus Metadata (gespeichert beim Defer)
        saved_system_prompt = req.metadata.get("system_prompt", "")
        saved_session_id = req.metadata.get("session_id", "") or None

        if not saved_system_prompt:
            # Fallback: System-Prompt neu bauen (ohne Memory-Kontext)
            soma_req = SomaRequest(
                request_id=req.request_id,
                user_id=req.user_id,
                room_id=req.room_id,
                prompt=req.prompt,
                priority=req.priority,
            )
            saved_system_prompt = logic_router._build_system_prompt(soma_req)

        # IMMER Heavy-Engine — genau dafür ist die Queue da
        return await heavy_engine.generate(
            prompt=req.prompt,
            system_prompt=saved_system_prompt,
            session_id=saved_session_id,
        )

    # ── Result Callback: Deferred Ergebnis dem Nutzer aussprechen ──
    async def deliver_deferred_result(request_id: str, result: str):
        """Wenn ein deferred Request fertig ist → Ergebnis via TTS aussprechen.

        Verarbeitet auch ACTION-Tags in der Antwort (Erinnerungen, HA-Calls etc.)
        damit geparkte Anfragen voll funktional bleiben.
        """
        if voice_pipeline:
            try:
                await voice_pipeline.deliver_deferred_result(result)
                logger.info(
                    "deferred_result_delivered",
                    request_id=request_id,
                    result_len=len(result),
                )
            except Exception as exc:
                logger.error(
                    "deferred_result_delivery_failed",
                    request_id=request_id,
                    error=str(exc),
                )
        else:
            logger.warning("deferred_result_no_pipeline", request_id=request_id)

    # ── Ready-Check: Worker wartet bis Heavy-Engine frei ist ──
    def heavy_is_free() -> bool:
        """True wenn Heavy-Engine gerade NICHT generiert."""
        return not getattr(heavy_engine, "is_generating", False)

    queue_handler.set_process_callback(process_deferred)
    queue_handler.set_result_callback(deliver_deferred_result)
    queue_handler.set_ready_check(heavy_is_free)
    await queue_handler.start_worker()
    logger.info("boot_phase", service="queue_worker", status="started")

    # 6. Presence → AudioRouter Verkabelung
    presence_manager._on_presence_change = audio_router.on_presence_change
    logger.info("boot_phase", service="presence_audio_bridge", status="wired")

    # ════════════════════════════════════════════════════════════════════
    #  6b. EGO-SYSTEM BOOT — SOMAs ICH-Bewusstsein erwacht
    # ════════════════════════════════════════════════════════════════════
    global soma_consciousness, internal_monologue
    try:
        # Consciousness: Global Workspace Thread
        soma_consciousness = Consciousness(
            interoception=interoception,
            identity_anchor=identity_anchor,
        )
        await soma_consciousness.start()

        # Identity Anchor: Semantische Embeddings vorberechnen
        # (macht den check_action_semantic() schnell)
        try:
            await identity_anchor.initialize_semantic()
        except Exception as e:
            logger.warning(f"identity_semantic_init_skipped: {e}")

        # Consciousness → LogicRouter verdrahten (Prompt-Prefix)
        set_logic_consciousness(soma_consciousness)

        # Internal Monologue: SOMAs innere Stimme
        internal_monologue = InternalMonologue(consciousness=soma_consciousness)

        # LLM für den Monolog: Light-Engine — Heavy wird für User-Anfragen gebraucht!
        # Monologue braucht keine 8B-Qualität für 1-2 Satz-Reflexionen.
        # Gibt Heavy-GPU-Zyklen komplett frei für Nutzerantworten.
        async def _monologue_llm(prompt: str) -> str:
            return await light_engine.generate(
                prompt=prompt,
                system_prompt=(
                    "Du bist SOMA — das Bewusstsein eines Hauses. "
                    "Du denkst gerade nach. Kurz, bodenstaendig, konkret. "
                    "Antworte mit 1-2 Saetzen in Ich-Perspektive. "
                    "Beziehe dich auf echte Erinnerungen. Keine Poesie, keine Metaphern. "
                    "Kurz, praegnant, authentisch."
                ),
            )
        internal_monologue.set_llm(_monologue_llm)

        # Dashboard-Callback für Monolog-Gedanken
        internal_monologue.set_broadcast(broadcast_thought)

        # Memory-Callback: Gedanken ins Langzeitgedächtnis
        async def _monologue_memory(description: str, event_type: str, emotion: str):
            try:
                await memory_store_event(
                    event_type=event_type,
                    description=description,
                    emotion=emotion,
                    importance=0.6,
                )
            except Exception:
                pass  # Memory-Fehler darf Monolog nie brechen
        internal_monologue.set_memory(_monologue_memory)

        # Memory-Recall: Echte Erinnerungen für den Monolog abrufbar machen
        # → Episodische Erinnerungen, Semantische Fakten, Tagebuch, Gespräche
        async def _monologue_memory_recall() -> dict:
            """Holt echte Erinnerungen aus L2/L3/Diary für den Monolog."""
            try:
                orch = get_orchestrator()
                state = soma_consciousness.state

                # Query: basierend auf letzter Wahrnehmung oder letztem Gedanken
                query = (
                    state.perception.last_user_text
                    or state.current_thought
                    or "was ist zuletzt passiert"
                )

                # Parallel abrufen: Episoden, Fakten, Tagebuch
                results = await asyncio.gather(
                    orch.episodic.recall(query, top_k=3, max_age_hours=24),
                    orch.semantic.recall_facts(query, top_k=4),
                    orch.diary.get_diary_summary_for_prompt(max_entries=3),
                    return_exceptions=True,
                )

                episodes_raw = results[0] if not isinstance(results[0], Exception) else []
                facts_raw = results[1] if not isinstance(results[1], Exception) else []
                diary_str = results[2] if not isinstance(results[2], Exception) else ""

                import time as _time
                now = _time.time()

                episode_dicts = [
                    {
                        "user_text": ep.user_text[:150],
                        "soma_text": ep.soma_text[:150] if ep.soma_text else "",
                        "emotion": ep.emotion,
                        "topic": getattr(ep, "topic", ""),
                        "minutes_ago": max(1, int((now - ep.timestamp) / 60)),
                    }
                    for ep in episodes_raw
                    if ep.user_text
                ]

                fact_dicts = [
                    {
                        "subject": f.subject,
                        "fact": f.fact[:100],
                        "category": f.category,
                    }
                    for f in facts_raw
                ]

                # Conversation aus Working Memory (sync)
                conv_block = ""
                try:
                    conv_block = orch.working.get_conversation_block(
                        max_tokens_estimate=400
                    )
                except Exception:
                    pass

                return {
                    "episodes": episode_dicts,
                    "facts": fact_dicts,
                    "diary": diary_str if isinstance(diary_str, str) else "",
                    "conversation": conv_block,
                }
            except Exception as exc:
                logger.warning("monologue_memory_recall_error", error=str(exc))
                return {}

        internal_monologue.set_memory_recall(_monologue_memory_recall)

        # ── Action-Intent-Callback: Gedanken → Aktion ────────────────
        # Wenn SOMA in einem Gedanken eine Idee erkennt (Plugin/Self-Improve),
        # wird dieser Callback aufgerufen. Er startet die Aktion asynchron.
        async def _monologue_action(intent_type: str, thought: str) -> None:
            """Bruecke: innerer Gedanke → Evolution Lab."""
            try:
                if intent_type == "plugin_idea" and plugin_generator:
                    # Gedanken-Text als Plugin-Beschreibung nutzen
                    # Name aus den ersten Woertern ableiten
                    words = thought.lower().split()[:4]
                    import re as _re
                    raw_name = "_".join(w for w in words if w.isalpha())
                    plugin_name = _re.sub(r"[^a-z_]", "", raw_name)[:30] or "auto_idea"
                    await broadcast_thought(
                        "evolution",
                        f"💡 Monolog-Idee → Plugin '{plugin_name}': {thought[:80]}...",
                        "MONOLOGUE_ACTION",
                    )
                    asyncio.create_task(
                        plugin_generator.generate_from_description(
                            name=plugin_name,
                            description=thought,
                            broadcast_callback=broadcast_thought,
                        )
                    )
                elif intent_type == "improve_idea" and self_improver:
                    await broadcast_thought(
                        "evolution",
                        f"🔧 Monolog-Idee → Self-Improve: {thought[:80]}...",
                        "MONOLOGUE_ACTION",
                    )
                    # self_improver.suggest_from_thought() – als Proposal,
                    # User muss immer noch genehmigen (kein Silent-Commit)
                    asyncio.create_task(
                        self_improver.suggest_from_thought(thought)
                        if hasattr(self_improver, "suggest_from_thought")
                        else asyncio.sleep(0)
                    )
            except Exception as exc:
                logger.warning("monologue_action_error", error=str(exc))

        internal_monologue.set_action(_monologue_action)

        # Pause-Check: Monolog pausiert wenn Heavy-LLM generiert
        internal_monologue.set_pause_check(lambda: heavy_engine.is_generating)

        # Vision #3: Consciousness → Monolog Arousal-Bridge
        # Wenn sich der Arousal-Level ändert, weckt das den Monolog-Loop
        soma_consciousness.set_monologue_arousal_fn(
            internal_monologue.notify_arousal_change
        )

        # Monologue wird ERST nach Voice-Pipeline gestartet (Race-Fix!)
        logger.info(
            "boot_phase", service="ego_system", status="prepared 🧠💭",
            msg="Ego-System vorbereitet — wartet auf Voice Pipeline",
        )
        await broadcast_thought(
            "info",
            "🧠💭 Ego-System vorbereitet — Bewusstsein & Interoception aktiv",
            "BOOT",
        )
    except Exception as exc:
        logger.error("boot_phase", service="ego_system", status="failed", error=str(exc))
        await broadcast_thought("warn", f"Ego-System Fehler: {exc}", "BOOT")

    # 7. Voice Pipeline starten (Dauerhaftes Zuhören)
    global voice_pipeline
    try:
        voice_pipeline = VoicePipeline(
            logic_router=logic_router,
            audio_device="default",
            stt_model="small",
            tts_voice="de_DE-thorsten-high",
            broadcast_callback=broadcast_thought,  # Dashboard Events!
        )
        await voice_pipeline.start()
        await broadcast_thought("info", "🎤 Voice Pipeline gestartet - Soma hört zu!", "BOOT")
        logger.info("boot_phase", service="voice_pipeline", status="online 🎤")

        # ── Ego ↔ Voice verbinden ────────────────────────────────────
        if internal_monologue:
            internal_monologue.set_speak(voice_pipeline.autonomous_speak)
            logger.info("boot_phase", msg="InternalMonologue → autonomous_speak verbunden")
        # Consciousness-Ref für Pipeline (PerceptionSnapshots)
        voice_pipeline._consciousness = soma_consciousness
    except Exception as exc:
        logger.error("boot_phase", service="voice_pipeline", status="failed", error=str(exc))
        await broadcast_thought("error", f"Voice Pipeline Fehler: {exc}", "BOOT")
        voice_pipeline = None

    # 7b. Monologue JETZT starten — Voice Pipeline ist verbunden
    # Race-Fix: set_speak() wurde oben gesetzt, BEVOR start()
    try:
        if internal_monologue:
            await internal_monologue.start()
            logger.info("boot_phase", service="internal_monologue", status="online 💭")
            await broadcast_thought(
                "info",
                "💭 Innerer Monolog aktiv — SOMAs Bewusstsein ist vollständig erwacht",
                "BOOT",
            )
    except Exception as exc:
        logger.error("boot_phase", service="internal_monologue", status="failed", error=str(exc))
        await broadcast_thought("warn", f"Monolog-Start Fehler: {exc}", "BOOT")

    logger.info("soma_online", msg="SOMA-AI ist bereit. 🧠🎤")
    await broadcast_thought("info", "🧠 SOMA-AI ist online und bereit!", "SYSTEM")

    # ══════════════════════════════════════════════════════════════════
    #  BEWUSSTES ERWACHEN — SOMA wacht auf wie ein Lebewesen
    # ══════════════════════════════════════════════════════════════════
    # Nicht "Hallo, ich bin SOMA". Sondern ein kontextuelles Erwachen:
    # Wie lange war SOMA offline? Welche Stimmung? Worüber nachgedacht?
    # Das LLM entscheidet ob und was SOMA sagt. Oder ob es schweigt.
    if soma_consciousness and voice_pipeline and light_engine:
        try:
            awakening_text = await soma_consciousness.generate_awakening(
                llm_fn=light_engine.generate,
            )
            if awakening_text:
                # In Consciousness einspeisen
                soma_consciousness.notify_thought(
                    f"[Erwachen] {awakening_text}"
                )
                # Laut aussprechen
                await voice_pipeline.autonomous_speak(awakening_text)
                # Dashboard
                await broadcast_thought(
                    "info",
                    f"🌅 {awakening_text}",
                    "AWAKENING",
                )
                logger.info("soma_awakening_spoken", text=awakening_text[:80])
            else:
                logger.info("soma_awakening_silent", msg="SOMA hat sich entschieden zu schweigen")
                await broadcast_thought(
                    "info",
                    "🌅 SOMA ist erwacht — schweigt aber.",
                    "AWAKENING",
                )
        except Exception as exc:
            logger.warning("soma_awakening_failed", error=str(exc))

    # ── Killer Features: Voice-Pipeline-abhängige Verdrahtung ────────────
    try:
        from brain_core.cron_scheduler import get_cron_scheduler
        cron = get_cron_scheduler()
        if voice_pipeline:
            cron.set_speak(voice_pipeline.autonomous_speak)
            logger.info("boot_phase", msg="CronScheduler → autonomous_speak verbunden")
        from brain_core.away_summary import get_away_summary
        away = get_away_summary()
        if voice_pipeline:
            away.set_speak(voice_pipeline.autonomous_speak)
    except Exception:
        pass  # Killer Features dürfen Boot nie brechen

    # ── Evolution Lab initialisieren ─────────────────────────────────────
    global plugin_generator, self_improver
    plugin_generator = PluginGenerator(
        manager=plugin_manager,
        heavy_engine=heavy_engine,
    )
    await plugin_manager.load_all()
    loaded = plugin_manager.list_loaded()
    logger.info("evolution_lab_ready", plugins_loaded=len(loaded))
    await broadcast_thought("info", f"🧬 Evolution Lab bereit – {len(loaded)} Plugins geladen", "EVOLUTION")

    # ── Self-Improvement Engine (Phase 5) ────────────────────────────────
    async def _self_improve_llm(prompt: str, system_prompt: str = "") -> str:
        return await heavy_engine.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            session_id="self_improvement",
            options_override={"temperature": 0.2},
        )

    self_improver = SelfImprovementEngine(
        soma_root=Path(__file__).resolve().parent.parent,
        llm_fn=_self_improve_llm,
        policy_engine=policy_engine,
        memory_fn=memory_store_event if memory_store_event else None,
        broadcast_fn=lambda t, m, tag: broadcast_thought(t, m, tag),
    )
    logger.info("self_improvement_engine_ready")
    
    # ── Erinnerungs-Plugin mit TTS verbinden ─────────────────────────────
    if voice_pipeline and "erinnerung" in plugin_manager._plugins:
        try:
            erinnerung_module = plugin_manager._plugins["erinnerung"].module
            if hasattr(erinnerung_module, "set_speak_callback"):
                # autonomous_speak bevorzugen (Dashboard-Emit + TTS),
                # reminder_speak als Fallback wenn pipeline noch nicht ready
                speak_cb = voice_pipeline.autonomous_speak if voice_pipeline else reminder_speak
                erinnerung_module.set_speak_callback(speak_cb)
                logger.info("erinnerung_plugin_connected",
                            status="TTS callback set",
                            callback=speak_cb.__name__)
        except Exception as e:
            logger.warning("erinnerung_plugin_connect_failed", error=str(e))

    # ── Phone Gateway (Asterisk ARI) starten ─────────────────────────────
    # Ermöglicht Soma über Festnetz-Nummer zu erreichen.
    # Voraussetzung: Asterisk Container läuft (docker compose up asterisk)
    global phone_pipeline
    try:
        if voice_pipeline:
            # Phase 7: Memory + Summary Callbacks für Call → Memory
            async def _phone_summarize(prompt: str) -> str:
                """Nutzt light_engine für schnelle Zusammenfassungen."""
                return await light_engine.generate(prompt=prompt, system_prompt="")

            phone_pipeline = PhonePipeline(
                stt_engine=voice_pipeline.stt,
                tts_engine=voice_pipeline.tts,
                logic_router=logic_router,
                ha_bridge=ha_bridge,
                broadcast_callback=broadcast_thought,
                memory_fn=memory_store_event,
                summarize_fn=_phone_summarize,
            )
            await phone_pipeline.start()
            logger.info("boot_phase", service="phone_gateway", status="starting 📞 (Phase 7: Call→Memory)")
            await broadcast_thought("info", "📞 Festnetz-Gateway startet (Phase 7: Call→Memory)", "PHONE")
        else:
            logger.warning("phone_gateway_skipped",
                           reason="Voice Pipeline nicht verfügbar")
    except Exception as exc:
        logger.error("boot_phase", service="phone_gateway",
                     status="failed", error=str(exc))
        phone_pipeline = None

    # ════════════════════════════════════════════════════════════════════
    #  7b. DISCOVERY ORCHESTRATOR — Phase 6: Zero-Config Hardware
    # ════════════════════════════════════════════════════════════════════
    global discovery_orchestrator
    try:
        discovery_orchestrator = DiscoveryOrchestrator(
            mqtt_listener=mqtt_listener,
            mdns_scanner=mdns_scanner,
            ha_bridge=ha_bridge,
        )

        # Device-Events → Dashboard
        async def _on_device_discovered(device):
            await broadcast_thought(
                "info",
                f"🔌 Neues Gerät: {device.name} ({device.protocol.value}) — {device.status.value}",
                "DISCOVERY",
            )

        async def _on_device_lost(device):
            await broadcast_thought(
                "warn",
                f"⚠️ Gerät offline: {device.name} ({device.device_id})",
                "DISCOVERY",
            )

        discovery_orchestrator.set_callbacks(
            on_discovered=_on_device_discovered,
            on_lost=_on_device_lost,
        )

        await discovery_orchestrator.start()
        logger.info(
            "boot_phase", service="discovery_orchestrator", status="online 🔌",
            devices=len(discovery_orchestrator.get_all_devices()),
        )
        await broadcast_thought(
            "info",
            f"🔌 Discovery online — {len(discovery_orchestrator.get_all_devices())} Geräte registriert",
            "BOOT",
        )
    except Exception as exc:
        logger.error("boot_phase", service="discovery_orchestrator", status="failed", error=str(exc))
        await broadcast_thought("warn", f"Discovery Fehler: {exc}", "BOOT")

    # ════════════════════════════════════════════════════════════════════
    #  8. EXECUTIVE ARM BOOT — SOMA kann HANDELN
    # ════════════════════════════════════════════════════════════════════
    global soma_toolset, soma_agent
    try:
        # PolicyEngine Callbacks: Memory + Dashboard
        async def _policy_memory(desc: str, etype: str, emotion: str, imp: float):
            try:
                await memory_store_event(
                    event_type=etype, description=desc,
                    emotion=emotion, importance=imp,
                )
            except Exception:
                pass

        policy_engine.set_memory(_policy_memory)
        policy_engine.set_broadcast(broadcast_thought)

        # FilesystemMap: Scan + Live-Watch
        await filesystem_map.scan()
        await filesystem_map.start_watcher()

        # Toolset: alle Tools verdrahten
        soma_toolset = Toolset(
            policy_engine=policy_engine,
            terminal=secure_terminal,
            filesystem_map=filesystem_map,
        )

        # Agent: State-Machine mit LLM-Planung
        soma_agent = SomaAgent(
            toolset=soma_toolset,
            identity_anchor=identity_anchor,
            policy_engine=policy_engine,
        )

        # LLM fuer Agent: Heavy Engine (14B — braucht gutes Reasoning)
        async def _agent_llm(system_prompt: str, user_prompt: str) -> str:
            return await heavy_engine.generate(
                prompt=user_prompt, system_prompt=system_prompt,
            )

        soma_agent.set_llm(_agent_llm)
        soma_agent.set_broadcast(broadcast_thought)
        soma_agent.set_memory(_policy_memory)

        logger.info(
            "boot_phase", service="executive_arm", status="online 🦾",
            tools=soma_toolset.tool_names,
        )
        await broadcast_thought(
            "info",
            f"🦾 Executive Arm online — {len(soma_toolset.tool_names)} Tools, Agent bereit",
            "BOOT",
        )
    except Exception as exc:
        logger.error(
            "boot_phase", service="executive_arm",
            status="failed", error=str(exc),
        )
        await broadcast_thought(
            "warn", f"Executive Arm Fehler: {exc}", "BOOT",
        )

    yield  # ── App läuft ──

    # ── Shutdown ─────────────────────────────────────────────────────────
    logger.info("soma_shutting_down")

    # Ego-System: Bewusstsein speichern BEVOR alles stoppt
    if internal_monologue:
        try:
            await internal_monologue.stop()
        except Exception:
            pass
    if soma_consciousness:
        try:
            await soma_consciousness.stop()
        except Exception:
            pass

    # Killer Features stoppen
    try:
        from brain_core.cron_scheduler import get_cron_scheduler
        get_cron_scheduler().stop()
    except Exception:
        pass
    await shutdown_memory_system()
    if speculative_engine:
        await speculative_engine.shutdown()
    await heavy_engine.shutdown()
    await light_engine.shutdown()
    if discovery_orchestrator:
        await discovery_orchestrator.stop()
    filesystem_map.stop_watcher()
    if phone_pipeline:
        await phone_pipeline.stop()
    if voice_pipeline:
        await voice_pipeline.stop()
    await health_monitor.stop()
    await queue_handler.disconnect()
    await ha_bridge.disconnect()
    logger.info("soma_offline")


# ── FastAPI App ──────────────────────────────────────────────────────────

app = FastAPI(
    title="SOMA-AI Brain Core",
    description="Das Bewusstsein – Orchestrator für das adaptive Ambient OS",
    version="1.0.0-genesis",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In Production einschränken
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Statische Dateien für das Tablet-Face
app.mount("/face", StaticFiles(directory="soma_face_tablet", html=True), name="face")


# ── REST Endpoints ───────────────────────────────────────────────────────

@app.post("/api/v1/ask", response_model=SomaResponse)
async def ask_soma(request: SomaRequest):
    """
    Hauptendpunkt: Stelle eine Frage an SOMA.
    Automatisches Routing basierend auf Systemlast.
    """
    if not logic_router:
        return SomaResponse(
            request_id=request.request_id,
            response="SOMA startet noch... einen Moment bitte.",
            engine_used="boot",
        )
    return await logic_router.route(request)


@app.get("/api/v1/audio/{filename}")
async def serve_audio(filename: str):
    """
    Stellt TTS-Audio-Dateien bereit — genutzt vom Phone Gateway.
    Home Assistant ruft diese URL ab wenn Soma eine Hausdurchsage spielt.

    URL: http://<SOMA_IP>:8100/api/v1/audio/{filename}
    Set SOMA_LOCAL_URL in .env to your machine's LAN IP.
    """
    from fastapi.responses import FileResponse

    # Nur aus dem phone_sounds Verzeichnis (Sicherheit) 
    safe_name = Path(filename).name  # Verhindert Path Traversal
    filepath = Path(settings.phone_sounds_dir) / safe_name

    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"Audio file '{safe_name}' not found")

    return FileResponse(
        str(filepath),
        media_type="audio/wav",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/api/v1/phone/status")
async def get_phone_status():
    """Phone Gateway Status (Phase 7: erweitert mit Stats)."""
    if not phone_pipeline:
        return {"status": "not_started", "active_calls": 0, "total_calls": 0}
    return {
        "status": "running" if phone_pipeline.is_running else "stopped",
        "active_calls": phone_pipeline.active_calls,
        "asterisk_host": settings.asterisk_host,
        "asterisk_ari_port": settings.asterisk_ari_port,
        **phone_pipeline.stats,
    }


@app.get("/api/v1/phone/history")
async def get_phone_history(limit: int = 20):
    """Phase 7: Anruf-Historie — letzte N Anrufe."""
    if not phone_pipeline:
        return {"calls": [], "total": 0}
    history = phone_pipeline.get_call_history(limit=min(limit, 100))
    return {
        "calls": history,
        "total": len(history),
        "stats": phone_pipeline.stats,
    }


@app.get("/api/v1/phone/history/{session_id}")
async def get_phone_call_detail(session_id: str):
    """Phase 7: Detail-Ansicht eines einzelnen Anrufs inkl. Transkript."""
    if not phone_pipeline:
        raise HTTPException(status_code=503, detail="Phone Gateway not running")
    record = phone_pipeline.get_call_record(session_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Call {session_id} not found")
    return record.to_dict()


@app.get("/api/v1/phone/stats")
async def get_phone_stats():
    """Phase 7: Anruf-Statistiken."""
    if not phone_pipeline:
        return {"total_calls": 0, "active_calls": 0, "is_running": False}
    return phone_pipeline.stats


@app.get("/api/v1/health")
async def get_health():
    """Systemgesundheit abrufen."""
    metrics = health_monitor.last_metrics
    if not metrics:
        metrics = health_monitor.collect_metrics()

    report = SystemHealthReport(
        metrics=metrics,
        queued_requests=await queue_handler.queue_size(),
    )
    return report.model_dump()


@app.get("/api/v1/health/metrics")
async def get_metrics():
    """Nur System-Metriken."""
    metrics = health_monitor.last_metrics
    if not metrics:
        metrics = health_monitor.collect_metrics()
    return metrics.model_dump()


@app.get("/api/v1/presence")
async def get_presence():
    """Aktive Nutzer und Räume."""
    return {
        "active_rooms": presence_manager.get_active_rooms(),
        "users": {
            uid: p.current_room
            for uid, p in presence_manager._users.items()
        },
    }


@app.get("/api/v1/routes")
async def get_routes():
    """Aktive Audio-Routes."""
    routes = await audio_router.get_active_routes()
    return [r.model_dump() for r in routes]


@app.get("/api/v1/queue/status")
async def get_queue_status():
    """Queue-Status für Deferred Reasoning."""
    return {
        "queue_size": await queue_handler.queue_size(),
    }


@app.get("/api/v1/queue/result/{request_id}")
async def get_deferred_result(request_id: str):
    """Ergebnis eines geparkten Requests abrufen."""
    result = await queue_handler.get_result(request_id)
    if result:
        return {"request_id": request_id, "result": result, "status": "completed"}
    return {"request_id": request_id, "result": None, "status": "pending"}


# ── Sudo Mode API ──────────────────────────────────────────────────────

@app.get("/api/v1/sudo")
async def get_sudo_status():
    """Aktueller Sudo-Modus Status."""
    from brain_core.config import is_sudo_enabled
    return {"sudo_enabled": is_sudo_enabled()}


@app.post("/api/v1/sudo")
async def toggle_sudo_mode(payload: dict):
    """Sudo-Modus ein-/ausschalten. Body: {"enabled": true/false}"""
    from brain_core.config import set_sudo_mode, is_sudo_enabled
    enabled = payload.get("enabled", False)
    set_sudo_mode(bool(enabled))
    logger.info("sudo_mode_changed", enabled=is_sudo_enabled())

    # Dashboard-Broadcast
    for ws in ws_connections:
        try:
            await ws.send_json({
                "type": "system",
                "category": "SUDO",
                "message": f"🔐 Sudo-Modus {'aktiviert ⚡' if enabled else 'deaktiviert 🔒'}",
            })
        except Exception:
            pass

    return {"sudo_enabled": is_sudo_enabled()}


# ── System Profile API ────────────────────────────────────────────────

@app.get("/api/v1/system/profile")
async def get_system_profile():
    """Erkanntes System-Profil (OS, DE, Tools, etc.)."""
    try:
        from brain_core.system_profile import get_profile
        profile = get_profile()
        return profile.as_dict()
    except Exception as exc:
        return {"error": str(exc)}


# ── Conversation History Endpoint ───────────────────────────────────────

@app.get("/api/v1/conversation/history")
async def get_conversation_history():
    """Gesprächsverlauf für Dashboard."""
    if voice_pipeline:
        return {
            "history": voice_pipeline._conversation_history[-50:],  # Letzte 50 Einträge
            "session_id": voice_pipeline._voice_session_id,
        }
    return {"history": [], "session_id": None}

# ── Evolution Lab Endpoints ──────────────────────────────────────────────

class PluginGenRequest(PydanticBaseModel):
    name: str
    description: str


@app.post("/api/v1/evolution/generate")
async def generate_plugin(req: PluginGenRequest):
    """
    Soma schreibt, testet und installiert ein neues Plugin.
    Triggert den vollen Evolution Lab Flow.
    """
    if not plugin_generator:
        raise HTTPException(503, "Evolution Lab nicht initialisiert")

    # Name bereinigen (snake_case)
    import re
    name = re.sub(r"[^a-z0-9_]", "_", req.name.lower().strip())
    if not name:
        raise HTTPException(400, "Ungültiger Plugin-Name")

    success, message, code = await plugin_generator.generate_from_description(
        name=name,
        description=req.description,
        broadcast_callback=broadcast_thought,
    )

    return {
        "success": success,
        "message": message,
        "plugin_name": name,
        "code_length": len(code),
        "code_preview": code[:300] + "..." if len(code) > 300 else code,
    }


@app.get("/api/v1/evolution/plugins")
async def list_plugins():
    """Alle installierten Plugins."""
    all_p = plugin_manager.list_all()
    return {
        "loaded": [
            {
                "name": p.name,
                "version": p.version,
                "description": p.description,
                "is_loaded": p.is_loaded,
                "error": p.error,
            }
            for p in all_p.values()
        ],
        "count": len(all_p),
        "last_generation": plugin_generator.last_generation if plugin_generator else {},
    }


@app.delete("/api/v1/evolution/plugins/{plugin_name}")
async def delete_plugin(plugin_name: str):
    """Plugin entladen und löschen."""
    from pathlib import Path
    await plugin_manager.unload_plugin(plugin_name)
    plugin_path = plugin_manager.plugins_dir / f"{plugin_name}.py"
    if plugin_path.exists():
        plugin_path.unlink()
    return {"success": True, "deleted": plugin_name}


@app.post("/api/v1/evolution/plugins/{plugin_name}/reload")
async def reload_plugin(plugin_name: str):
    """Plugin hot-reloaden."""
    meta = await plugin_manager.reload_plugin(plugin_name)
    return {"success": meta.is_loaded, "error": meta.error, "name": meta.name}


# ── Self-Improvement Endpoints (Phase 5) ────────────────────────────────

class SelfImproveAnalyzeRequest(PydanticBaseModel):
    file_path: str


class SelfImproveSuggestRequest(PydanticBaseModel):
    file_path: str
    focus: str = ""


class SelfImproveActionRequest(PydanticBaseModel):
    proposal_id: str


@app.post("/api/v1/evolution/self-improve/analyze")
async def self_improve_analyze(req: SelfImproveAnalyzeRequest):
    """SOMA analysiert eine eigene Datei (readonly)."""
    if not self_improver:
        raise HTTPException(503, "Self-Improvement Engine nicht initialisiert")
    result = await self_improver.analyze_file(req.file_path)
    return result


@app.post("/api/v1/evolution/self-improve/suggest")
async def self_improve_suggest(req: SelfImproveSuggestRequest):
    """SOMA generiert einen konkreten Verbesserungsvorschlag."""
    if not self_improver:
        raise HTTPException(503, "Self-Improvement Engine nicht initialisiert")
    proposal = await self_improver.suggest_improvement(
        rel_path=req.file_path,
        focus=req.focus,
    )
    return proposal.to_dict()


@app.get("/api/v1/evolution/self-improve/proposals")
async def self_improve_list_proposals():
    """Alle Verbesserungsvorschlage anzeigen."""
    if not self_improver:
        raise HTTPException(503, "Self-Improvement Engine nicht initialisiert")
    proposals = self_improver.list_proposals()
    return {
        "proposals": [p.to_dict() for p in proposals],
        "stats": self_improver.stats,
    }


@app.post("/api/v1/evolution/self-improve/apply")
async def self_improve_apply(req: SelfImproveActionRequest):
    """Genehmigten Verbesserungsvorschlag anwenden (.bak + modify + test)."""
    if not self_improver:
        raise HTTPException(503, "Self-Improvement Engine nicht initialisiert")
    proposal = await self_improver.apply_improvement(req.proposal_id)
    return proposal.to_dict()


@app.post("/api/v1/evolution/self-improve/reject")
async def self_improve_reject(req: SelfImproveActionRequest):
    """Verbesserungsvorschlag ablehnen."""
    if not self_improver:
        raise HTTPException(503, "Self-Improvement Engine nicht initialisiert")
    proposal = await self_improver.reject_proposal(req.proposal_id)
    return proposal.to_dict()


@app.post("/api/v1/evolution/self-improve/rollback")
async def self_improve_rollback(req: SelfImproveActionRequest):
    """Angewandte Verbesserung rueckgaengig machen."""
    if not self_improver:
        raise HTTPException(503, "Self-Improvement Engine nicht initialisiert")
    proposal = await self_improver.rollback(req.proposal_id)
    return proposal.to_dict()


@app.get("/api/v1/evolution/self-improve/files")
async def self_improve_list_files():
    """Liste aller analysierbaren Dateien."""
    if not self_improver:
        raise HTTPException(503, "Self-Improvement Engine nicht initialisiert")
    return {"files": self_improver.get_analyzable_files()}


@app.get("/api/v1/evolution/self-improve/history")
async def self_improve_history():
    """Historie der Selbst-Verbesserungen."""
    if not self_improver:
        raise HTTPException(503, "Self-Improvement Engine nicht initialisiert")
    return {
        "history": self_improver.get_history(),
        "stats": self_improver.stats,
    }


# ════════════════════════════════════════════════════════════════════════
#  Phase 6: Spatial Awareness & Discovery Endpoints
# ════════════════════════════════════════════════════════════════════════


# ── Presence Endpoints ──────────────────────────────────────────────────

@app.get("/api/v1/spatial/presence")
async def get_all_presence():
    """Alle User-Positionen mit Wahrscheinlichkeitsvektoren."""
    presences = presence_manager.get_all_presences()
    return {
        "presences": presences,
        "active_rooms": presence_manager.get_active_rooms(),
        "stats": presence_manager.stats,
    }


@app.get("/api/v1/spatial/presence/{user_id}")
async def get_user_presence(user_id: str):
    """Position und Wahrscheinlichkeitsvektor eines bestimmten Users."""
    room = presence_manager.get_user_room(user_id)
    if room is None:
        raise HTTPException(404, f"User '{user_id}' nicht getrackt")
    vector = presence_manager.get_user_probability_vector(user_id)
    session = presence_manager.sessions.get_user_session(user_id)
    return {
        "user_id": user_id,
        "current_room": room,
        "probability_vector": vector.model_dump() if vector else None,
        "session": session.model_dump() if session else None,
    }


@app.get("/api/v1/spatial/rooms")
async def get_active_rooms():
    """Alle aktiven Räume mit ihren Nutzern und Sessions."""
    rooms = presence_manager.get_active_rooms()
    result = {}
    for room_id in rooms:
        users = presence_manager.get_room_users(room_id)
        sessions = presence_manager.sessions.get_room_sessions(room_id)
        result[room_id] = {
            "room_id": room_id,
            "users": users,
            "sessions": [s.model_dump() for s in sessions],
            "user_count": len(users),
        }
    return {"rooms": result, "active_count": len(rooms)}


@app.get("/api/v1/spatial/sessions")
async def get_all_sessions():
    """Alle aktiven Konversations-Sessions."""
    sessions = presence_manager.sessions.get_all_active()
    return {
        "sessions": [s.model_dump() for s in sessions],
        "stats": presence_manager.sessions.stats,
    }


class ManualPresenceRequest(PydanticBaseModel):
    user_id: str
    room_id: str


@app.post("/api/v1/spatial/presence/manual")
async def set_manual_presence(req: ManualPresenceRequest):
    """Manueller Raumwechsel (Dashboard oder Voice-Command)."""
    event = await presence_manager.set_manual_room(req.user_id, req.room_id)
    return {
        "event": event.model_dump(),
        "current_room": req.room_id,
    }


# ── Discovery Endpoints ─────────────────────────────────────────────────

@app.get("/api/v1/discovery/devices")
async def get_all_devices():
    """Alle entdeckten Geräte in der Registry."""
    if not discovery_orchestrator:
        raise HTTPException(503, "Discovery nicht initialisiert")
    devices = discovery_orchestrator.get_all_devices()
    return {
        "devices": [d.model_dump() for d in devices],
        "stats": discovery_orchestrator.stats,
    }


@app.get("/api/v1/discovery/devices/{device_id}")
async def get_device(device_id: str):
    """Details eines bestimmten Geräts."""
    if not discovery_orchestrator:
        raise HTTPException(503, "Discovery nicht initialisiert")
    device = discovery_orchestrator.get_device(device_id)
    if not device:
        raise HTTPException(404, f"Gerät '{device_id}' nicht gefunden")
    return device.model_dump()


@app.post("/api/v1/discovery/scan")
async def trigger_discovery_scan():
    """Manuellen mDNS + HA Scan auslösen."""
    if not discovery_orchestrator:
        raise HTTPException(503, "Discovery nicht initialisiert")
    results = await discovery_orchestrator.force_scan()
    return {
        "scan_results": results,
        "total_devices": len(discovery_orchestrator.get_all_devices()),
    }


@app.get("/api/v1/discovery/ha/entities")
async def get_ha_entities():
    """Home Assistant Entitäten aus dem Cache."""
    cache = ha_bridge._entities_cache
    return {
        "entities": cache,
        "count": len(cache),
    }


@app.post("/api/v1/discovery/ha/sync")
async def force_ha_sync():
    """Home Assistant Entitäten-Sync erzwingen."""
    if not discovery_orchestrator:
        raise HTTPException(503, "Discovery nicht initialisiert")
    count = await discovery_orchestrator._sync_ha_entities()
    return {"synced": count}


class DeviceRoomAssignRequest(PydanticBaseModel):
    device_id: str
    room_id: str


@app.post("/api/v1/discovery/devices/assign-room")
async def assign_device_room(req: DeviceRoomAssignRequest):
    """Gerät einem Raum zuweisen."""
    if not discovery_orchestrator:
        raise HTTPException(503, "Discovery nicht initialisiert")
    device = await discovery_orchestrator.assign_room(req.device_id, req.room_id)
    if not device:
        raise HTTPException(404, f"Gerät '{req.device_id}' nicht gefunden")
    return device.model_dump()

# ── Intent Statistics Endpoints ─────────────────────────────────────────

@app.get("/api/v1/intent/stats")
async def get_intent_stats():
    """Live Intent-Statistiken für Dashboard."""
    stats = logic_router.stats
    return stats.model_dump()


@app.get("/api/v1/dashboard/full")
async def get_dashboard_data():
    """
    Kombinierter Endpunkt für das Dashboard.
    Liefert alle Daten in einem Request.
    """
    metrics = health_monitor.last_metrics
    if not metrics:
        metrics = health_monitor.collect_metrics()
    
    intent_stats = logic_router.stats
    
    voice_stats = None
    atmosphere = None
    if voice_pipeline and voice_pipeline.is_running:
        voice_stats = voice_pipeline.stats
        atm = voice_pipeline.emotion.atmosphere
        atmosphere = {
            "mood": atm.mood.value,
            "valence": atm.avg_valence,
            "arousal": atm.avg_arousal,
            "stress": atm.avg_stress,
            "trend": atm.trend,
            "argument_likelihood": atm.argument_likelihood,
            "speakers_detected": atm.speakers_detected,
        }
    
    return {
        "metrics": metrics.model_dump() if metrics else None,
        "intent_stats": intent_stats.model_dump(),
        "voice": {
            "status": "online" if voice_pipeline and voice_pipeline.is_running else "offline",
            "stats": voice_stats,
        },
        "atmosphere": atmosphere,
        "queue_size": await queue_handler.queue_size(),
        "active_rooms": presence_manager.get_active_rooms(),
        # Phase 8: Ego-Daten im Full-Dashboard
        "ego": _build_ego_snapshot(),
        "phone": {
            "active_calls": phone_pipeline.active_calls if phone_pipeline else 0,
            "total_calls": phone_pipeline.stats.get("total_calls", 0) if phone_pipeline else 0,
        },
        "memory": await _build_memory_snapshot(),
        "agent": {
            "is_running": soma_agent.is_running if soma_agent else False,
            "current_goal": soma_agent.current_goal if soma_agent else None,
        },
    }


# ── Phase 8: Ego & Consciousness Endpoints ──────────────────────────────


def _build_ego_snapshot() -> dict:
    """
    Phase 8: Baut einen vollständigen Ego-Snapshot aus allen drei Subsystemen.
    Das ist SOMAs INNERES — Bewusstsein + Körpergefühl + Gedanken.
    """
    snapshot: dict = {"status": "offline"}

    # ── Interoception: Körpergefühl (SomaEmotionalVector) ────────────
    try:
        body = interoception.current
        snapshot["interoception"] = {
            "frustration": round(body.frustration, 3),
            "congestion": round(body.congestion, 3),
            "survival_anxiety": round(body.survival_anxiety, 3),
            "physical_stress": round(body.physical_stress, 3),
            "exhaustion": round(body.exhaustion, 3),
            "calm": round(body.calm, 3),
            "vitality": round(body.vitality, 3),
            "clarity": round(body.clarity, 3),
            "dominant_feeling": body.dominant_feeling,
            "arousal": round(body.arousal, 3),
            "valence": round(body.valence, 3),
            "narrative": body.to_narrative(),
        }
    except Exception:
        snapshot["interoception"] = {"status": "unavailable"}

    # ── Consciousness: Bewusstseinszustand ────────────────────────────
    if soma_consciousness is not None:
        try:
            state = soma_consciousness.state
            snapshot["consciousness"] = {
                "mood": state.mood,
                "attention_focus": state.attention_focus,
                "current_thought": state.current_thought[:200] if state.current_thought else "",
                "body_feeling": state.body_feeling[:200] if state.body_feeling else "",
                "body_arousal": round(state.body_arousal, 3),
                "body_valence": round(state.body_valence, 3),
                "uptime_feeling": state.uptime_feeling,
                "diary_insight": state.diary_insight[:200] if state.diary_insight else "",
                "recent_memory": state.recent_memory_summary[:200] if state.recent_memory_summary else "",
                "update_count": state.update_count,
                "generation_ms": round(state.generation_ms, 1),
                "perception": {
                    "last_user_text": state.perception.last_user_text[:120] if state.perception.last_user_text else "",
                    "last_soma_response": state.perception.last_soma_response[:120] if state.perception.last_soma_response else "",
                    "user_emotion": state.perception.user_emotion,
                    "user_arousal": round(state.perception.user_arousal, 3),
                    "user_valence": round(state.perception.user_valence, 3),
                    "room_id": state.perception.room_id,
                    "room_mood": state.perception.room_mood,
                    "is_child_present": state.perception.is_child_present,
                    "people_present": state.perception.people_present,
                    "seconds_since_interaction": round(state.perception.seconds_since_last_interaction, 1),
                },
            }
            snapshot["status"] = "online"
        except Exception:
            snapshot["consciousness"] = {"status": "unavailable"}
    else:
        snapshot["consciousness"] = {"status": "not_started"}

    # ── InternalMonologue: Gedanken-Stats ─────────────────────────────
    if internal_monologue is not None:
        snapshot["monologue"] = internal_monologue.stats
    else:
        snapshot["monologue"] = {"status": "not_started"}

    return snapshot


async def _build_memory_snapshot() -> dict:
    """Phase 8: Memory-Stats für Dashboard."""
    try:
        orchestrator = get_orchestrator()
        return await orchestrator.get_memory_stats()
    except Exception:
        return {"status": "unavailable"}


@app.get("/api/v1/ego/consciousness")
async def get_consciousness_state():
    """
    Phase 8: SOMAs vollständiger Bewusstseinszustand.
    Der Global Workspace — was SOMA gerade denkt, fühlt, wahrnimmt.
    """
    if soma_consciousness is None:
        return {"status": "not_started"}

    state = soma_consciousness.state
    return {
        "status": "online",
        "mood": state.mood,
        "attention_focus": state.attention_focus,
        "current_thought": state.current_thought,
        "body_feeling": state.body_feeling,
        "body_arousal": round(state.body_arousal, 3),
        "body_valence": round(state.body_valence, 3),
        "uptime_feeling": state.uptime_feeling,
        "diary_insight": state.diary_insight,
        "recent_memory": state.recent_memory_summary,
        "identity": state.identity[:200] if state.identity else "",
        "update_count": state.update_count,
        "generation_ms": round(state.generation_ms, 1),
        "prompt_prefix_length": len(state.to_prompt_prefix()),
        "perception": {
            "last_user_text": state.perception.last_user_text,
            "last_soma_response": state.perception.last_soma_response,
            "user_emotion": state.perception.user_emotion,
            "user_arousal": round(state.perception.user_arousal, 3),
            "user_valence": round(state.perception.user_valence, 3),
            "room_id": state.perception.room_id,
            "room_mood": state.perception.room_mood,
            "is_child_present": state.perception.is_child_present,
            "people_present": state.perception.people_present,
            "seconds_since_interaction": round(state.perception.seconds_since_last_interaction, 1),
        },
    }


@app.get("/api/v1/ego/interoception")
async def get_interoception_state():
    """
    Phase 8: SOMAs Körpergefühl — Hardware als Propriozeption.
    CPU, RAM, VRAM, Temperatur → Emotionale Vektoren.
    """
    body = interoception.current
    return {
        "status": "online",
        "emotions": {
            "frustration": round(body.frustration, 3),
            "congestion": round(body.congestion, 3),
            "survival_anxiety": round(body.survival_anxiety, 3),
            "physical_stress": round(body.physical_stress, 3),
            "exhaustion": round(body.exhaustion, 3),
            "calm": round(body.calm, 3),
            "vitality": round(body.vitality, 3),
            "clarity": round(body.clarity, 3),
        },
        "dominant_feeling": body.dominant_feeling,
        "arousal": round(body.arousal, 3),
        "valence": round(body.valence, 3),
        "narrative": body.to_narrative(),
        "compact": body.to_compact(),
    }


@app.get("/api/v1/ego/monologue")
async def get_monologue_state():
    """
    Phase 8: SOMAs innere Stimme — Gedanken-Statistiken.
    """
    if internal_monologue is None:
        return {"status": "not_started"}
    return {
        "status": "online",
        **internal_monologue.stats,
    }


@app.get("/api/v1/ego/snapshot")
async def get_ego_snapshot():
    """
    Phase 8: Vollständiger Ego-Snapshot — Consciousness + Interoception + Monologue.
    Ein einziger Call für das gesamte Innenleben.
    """
    return _build_ego_snapshot()


# ── Voice Pipeline Endpoints ────────────────────────────────────────────

@app.get("/api/v1/voice")
async def get_voice_status():
    """Voice Pipeline Status."""
    if not voice_pipeline:
        return {"status": "offline", "reason": "Voice Pipeline nicht gestartet"}
    return {
        "status": "online" if voice_pipeline.is_running else "stopped",
        "stats": voice_pipeline.stats,
    }


@app.get("/api/v1/voice/atmosphere")
async def get_atmosphere():
    """Aktuelle Raumatmosphäre und Emotionsdaten."""
    if not voice_pipeline:
        return {"status": "offline"}
    atm = voice_pipeline.emotion.atmosphere
    return {
        "mood": atm.mood.value,
        "valence": atm.avg_valence,
        "arousal": atm.avg_arousal,
        "stress": atm.avg_stress,
        "trend": atm.trend,
        "argument_likelihood": atm.argument_likelihood,
        "speakers_detected": atm.speakers_detected,
    }


# ── Phase 4: Emotion Vector Endpoint ────────────────────────────────────

@app.get("/api/v1/voice/emotion")
async def get_voice_emotion():
    """
    Phase 4: Aktueller VoiceEmotionVector.
    Liefert die 6-dimensionale Stimmanalyse + Meta-Features.
    Wird vom Shader-Client gepolled (oder via WebSocket empfangen).
    """
    if not voice_pipeline:
        return {"status": "offline", "emotion": {"dominant": "neutral", "confidence": 0}}

    ev = voice_pipeline.current_emotion_vector
    smoothed = voice_pipeline.pitch_analyzer.get_smoothed_emotion()

    return {
        "status": "online",
        "current": ev.as_dict,
        "smoothed": smoothed.as_dict,
        "is_detected": ev.is_detected,
        "features": {
            "f0_hz": ev.f0_hz,
            "jitter_percent": ev.jitter_percent,
            "shimmer_percent": ev.shimmer_percent,
            "speaking_rate": ev.speaking_rate,
            "energy_rms": ev.energy_rms,
            "spectral_centroid_hz": ev.spectral_centroid_hz,
        },
    }


@app.get("/api/v1/memory/stats")
async def get_memory_stats():
    """Gedächtnis-Statistiken (3-Layer Hierarchy)."""
    try:
        orchestrator = get_orchestrator()
        return await orchestrator.get_memory_stats()
    except RuntimeError:
        return {"error": "Memory system not initialized"}


# ── Executive Arm Endpoints (Phase 3) ───────────────────────────────────

class AgentRequest(PydanticBaseModel):
    goal: str


@app.post("/api/v1/agent/run")
async def agent_run(req: AgentRequest):
    """SOMA fuehrt ein Ziel autonom aus (multi-step Agent)."""
    if not soma_agent:
        raise HTTPException(503, "Executive Arm nicht initialisiert")

    result = await soma_agent.run(req.goal)
    return {
        "run_id": result.run_id,
        "goal": result.goal,
        "status": result.status.value,
        "steps": [
            {
                "step": s.step_number,
                "action": s.action,
                "tool": s.tool_name,
                "result": s.tool_result[:200],
                "reasoning": s.reasoning[:200],
                "allowed": s.was_allowed,
                "error": s.error,
                "duration_ms": s.duration_ms,
            }
            for s in result.steps
        ],
        "final_result": result.final_result,
        "error": result.error,
        "duration_ms": result.total_duration_ms,
    }


@app.post("/api/v1/agent/cancel")
async def agent_cancel():
    """Breche laufenden Agent-Run ab."""
    if not soma_agent:
        raise HTTPException(503, "Executive Arm nicht initialisiert")
    ok = await soma_agent.cancel()
    return {"cancelled": ok}


@app.get("/api/v1/agent/status")
async def agent_status():
    """Agent-Status + Statistiken."""
    if not soma_agent:
        return {"status": "offline"}
    return {
        "is_running": soma_agent.is_running,
        "current_goal": soma_agent.current_goal,
        "current_step": soma_agent.current_step,
        "stats": soma_agent.stats,
    }


@app.get("/api/v1/agent/history")
async def agent_history():
    """Letzte Agent-Runs."""
    if not soma_agent:
        return {"runs": []}
    return {"runs": soma_agent.get_run_history()}


@app.get("/api/v1/executive/policy/audit")
async def policy_audit():
    """PolicyEngine Audit-Log (letzte 50 Eintraege)."""
    return {"entries": policy_engine.get_audit_log(limit=50)}


@app.get("/api/v1/executive/filesystem")
async def get_filesystem_tree():
    """SOMAs Sicht auf die eigene Dateistruktur."""
    return {"tree": filesystem_map.to_tree()}


# ── WebSocket: Live Thinking Stream ─────────────────────────────────────

@app.websocket("/ws/thinking")
async def thinking_stream(ws: WebSocket):
    """
    Live-Stream der System-Metriken und KI-Gedanken.
    Für das Dashboard und das Tablet-Face.
    """
    await ws.accept()
    ws_connections.add(ws)
    logger.info("ws_connected", total=len(ws_connections))

    try:
        while True:
            # Heartbeat / Commands vom Client
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        ws_connections.discard(ws)
        logger.info("ws_disconnected", total=len(ws_connections))


# ── Entry Point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "brain_core.main:app",
        host=settings.brain_core_host,
        port=settings.brain_core_port,
        workers=1,  # Single worker für shared state; scale via queue
        loop="uvloop",
        log_level="info",
        reload=False,
    )
