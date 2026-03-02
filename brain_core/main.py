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
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

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
from shared.health_schemas import SystemMetrics, SystemHealthReport
from evolution_lab.plugin_manager import PluginManager, PluginGenerator
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel as PydanticBaseModel

logger = structlog.get_logger("soma.brain")

# ── Global Service Instances ─────────────────────────────────────────────

queue_handler = QueueHandler()
health_monitor = HealthMonitor(interval=5.0)
presence_manager = PresenceManager()
audio_router = AudioRouter()

# Engines
heavy_engine = HeavyLlamaEngine()
light_engine = LightPhiEngine()
nano_engine = NanoIntentEngine()

# Logic Router (wird im Lifespan initialisiert)
logic_router: LogicRouter | None = None

# WebSocket connections für Live-Dashboard
ws_connections: set[WebSocket] = set()

# Voice Pipeline (Das lebendige Ohr + Mund)
voice_pipeline: VoicePipeline | None = None

# Evolution Lab
plugin_manager = PluginManager()
plugin_generator: PluginGenerator | None = None


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

    # 2. Health Monitor starten mit Broadcast-Callback
    async def broadcast_metrics(metrics: SystemMetrics):
        """Pushe Metriken an alle verbundenen WebSocket-Clients."""
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
    logger.info("boot_phase", service="engines", status="initialized")

    # 4. Logic Router zusammenbauen (mit Plugin-Integration)
    logic_router = LogicRouter(
        health_monitor=health_monitor,
        queue_handler=queue_handler,
        plugin_manager=plugin_manager,  # Evolution Lab Integration
    )
    logic_router.register_engine("heavy", heavy_engine)
    logic_router.register_engine("light", light_engine)
    logic_router.register_engine("nano", nano_engine)

    # 5. Queue Worker starten (verarbeitet deferred requests)
    async def process_deferred(req):
        from shared.health_schemas import DeferredRequest
        soma_req = SomaRequest(
            request_id=req.request_id,
            user_id=req.user_id,
            room_id=req.room_id,
            prompt=req.prompt,
            priority=req.priority,
        )
        # Bei deferred: immer Heavy wenn möglich
        engine = heavy_engine if health_monitor.last_metrics and \
            health_monitor.last_metrics.ram_percent < 80 else nano_engine
        return await engine.generate(
            prompt=soma_req.prompt,
            system_prompt=logic_router._build_system_prompt(soma_req),
        )

    queue_handler.set_process_callback(process_deferred)
    await queue_handler.start_worker()
    logger.info("boot_phase", service="queue_worker", status="started")

    # 6. Presence → AudioRouter Verkabelung
    presence_manager._on_presence_change = audio_router.on_presence_change
    logger.info("boot_phase", service="presence_audio_bridge", status="wired")

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
    except Exception as exc:
        logger.error("boot_phase", service="voice_pipeline", status="failed", error=str(exc))
        await broadcast_thought("error", f"Voice Pipeline Fehler: {exc}", "BOOT")
        voice_pipeline = None

    logger.info("soma_online", msg="SOMA-AI ist bereit. 🧠🎤")
    await broadcast_thought("info", "🧠 SOMA-AI ist online und bereit!", "SYSTEM")

    # ── Evolution Lab initialisieren ─────────────────────────────────────
    global plugin_generator
    plugin_generator = PluginGenerator(
        manager=plugin_manager,
        heavy_engine=heavy_engine,
    )
    await plugin_manager.load_all()
    loaded = plugin_manager.list_loaded()
    logger.info("evolution_lab_ready", plugins_loaded=len(loaded))
    await broadcast_thought("info", f"🧬 Evolution Lab bereit – {len(loaded)} Plugins geladen", "EVOLUTION")

    yield  # ── App läuft ──

    # ── Shutdown ─────────────────────────────────────────────────────────
    logger.info("soma_shutting_down")
    if voice_pipeline:
        await voice_pipeline.stop()
    await health_monitor.stop()
    await queue_handler.disconnect()
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
    }


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
