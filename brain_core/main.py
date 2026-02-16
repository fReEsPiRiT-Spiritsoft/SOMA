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
from brain_core.queue_handler import QueueHandler
from brain_core.presence_manager import PresenceManager
from brain_core.audio_router import AudioRouter
from brain_core.engines.heavy_llama import HeavyLlamaEngine
from brain_core.engines.nano_intent import NanoIntentEngine
from brain_core.engines.light_phi import LightPhiEngine
from shared.health_schemas import SystemMetrics, SystemHealthReport

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
        if ws_connections:
            data = metrics.model_dump_json()
            dead = set()
            for ws in ws_connections:
                try:
                    await ws.send_text(data)
                except Exception:
                    dead.add(ws)
            ws_connections -= dead

    health_monitor._on_metrics = broadcast_metrics
    await health_monitor.start()
    logger.info("boot_phase", service="health_monitor", status="started")

    # 3. Engines initialisieren
    await heavy_engine.initialize()
    await light_engine.initialize()
    await nano_engine.initialize()
    logger.info("boot_phase", service="engines", status="initialized")

    # 4. Logic Router zusammenbauen
    logic_router = LogicRouter(
        health_monitor=health_monitor,
        queue_handler=queue_handler,
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
            system_prompt=LogicRouter._build_system_prompt(soma_req),
        )

    queue_handler.set_process_callback(process_deferred)
    await queue_handler.start_worker()
    logger.info("boot_phase", service="queue_worker", status="started")

    # 6. Presence → AudioRouter Verkabelung
    presence_manager._on_presence_change = audio_router.on_presence_change
    logger.info("boot_phase", service="presence_audio_bridge", status="wired")

    logger.info("soma_online", msg="SOMA-AI ist bereit. 🧠")

    yield  # ── App läuft ──

    # ── Shutdown ─────────────────────────────────────────────────────────
    logger.info("soma_shutting_down")
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
