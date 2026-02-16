"""
SOMA-AI Logic Router
=====================
DAS ZENTRALE GEHIRN: Entscheidet für jede Anfrage:
  1. Welche Engine? (Heavy/Light/Nano)
  2. Sofort oder Deferred?
  3. Welcher System-Prompt? (Kind/Erwachsener)

Datenfluss:
  User-Request ──► LogicRouter.route()
                       │
                       ├─ health_monitor.last_metrics ──► Load Level?
                       │
                       ├─ CRITICAL? ──► queue_handler.enqueue()
                       │                 └─ Return: "Moment, ich sortiere..."
                       │
                       ├─ HIGH? ──► NanoEngine.generate()
                       │
                       ├─ ELEVATED? ──► LightEngine.generate()
                       │
                       └─ NORMAL/IDLE? ──► HeavyEngine.generate()
                                              │
                                              ▼
                                         Response to User
"""

from __future__ import annotations

import uuid
from typing import Optional

import structlog

from shared.health_schemas import (
    SystemLoadLevel,
    SystemMetrics,
    DeferredRequest,
)
from brain_core.health_monitor import HealthMonitor
from brain_core.queue_handler import QueueHandler

logger = structlog.get_logger("soma.logic_router")


# ── Request / Response Models ────────────────────────────────────────────

from pydantic import BaseModel, Field


class SomaRequest(BaseModel):
    """Eingehende Anfrage an SOMA."""
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None
    room_id: Optional[str] = None
    prompt: str
    is_child: bool = False
    session_id: Optional[str] = None
    priority: int = Field(default=5, ge=1, le=10)
    metadata: dict = Field(default_factory=dict)


class SomaResponse(BaseModel):
    """Antwort von SOMA."""
    request_id: str
    response: str
    engine_used: str = "unknown"
    was_deferred: bool = False
    deferred_id: Optional[str] = None
    latency_ms: Optional[float] = None
    load_level: SystemLoadLevel = SystemLoadLevel.IDLE


# ── Deferred Feedback Messages ───────────────────────────────────────────

DEFERRED_MESSAGES = [
    "Moment, ich sortiere meine Gedanken...",
    "Ich denke kurz nach – komme gleich zurück.",
    "Gib mir einen Augenblick, mein System atmet gerade durch.",
    "Deine Frage ist mir wichtig – ich bearbeite sie gleich.",
]


class LogicRouter:
    """
    Routing-Engine: Verbindet Health-Daten mit Model-Auswahl.
    Hält KEINEN eigenen State – bezieht alles von HealthMonitor + Engines.
    """

    def __init__(
        self,
        health_monitor: HealthMonitor,
        queue_handler: QueueHandler,
    ):
        self.health = health_monitor
        self.queue = queue_handler
        self._engines: dict[str, object] = {}
        self._deferred_counter = 0

    def register_engine(self, name: str, engine: object) -> None:
        """Engine registrieren (heavy, light, nano)."""
        self._engines[name] = engine
        logger.info("engine_registered", name=name)

    # ── Core Routing ─────────────────────────────────────────────────────

    async def route(self, request: SomaRequest) -> SomaResponse:
        """
        Haupteingang: Route eine Anfrage zum richtigen Engine/Queue.
        Zero-Latency-Feedback Garantie: User bekommt IMMER sofort Antwort.
        """
        import time

        start = time.monotonic()

        metrics = self.health.last_metrics
        load_level = metrics.load_level if metrics else SystemLoadLevel.IDLE

        logger.info(
            "routing_request",
            request_id=request.request_id,
            load_level=load_level.value,
            is_child=request.is_child,
            room=request.room_id,
        )

        # ── CRITICAL: Ab in die Queue ────────────────────────────────────
        if load_level == SystemLoadLevel.CRITICAL:
            return await self._defer_request(request, load_level)

        # ── Engine Selection ─────────────────────────────────────────────
        engine_name = self._select_engine(load_level, request)
        engine = self._engines.get(engine_name)

        if not engine:
            logger.error("no_engine_available", requested=engine_name)
            # Fallback: Versuche Nano, dann defer
            engine = self._engines.get("nano")
            engine_name = "nano"
            if not engine:
                return await self._defer_request(request, load_level)

        # ── Generate ─────────────────────────────────────────────────────
        try:
            response_text = await engine.generate(  # type: ignore[attr-defined]
                prompt=request.prompt,
                system_prompt=self._build_system_prompt(request),
                session_id=request.session_id,
            )

            latency = (time.monotonic() - start) * 1000

            return SomaResponse(
                request_id=request.request_id,
                response=response_text,
                engine_used=engine_name,
                was_deferred=False,
                latency_ms=round(latency, 2),
                load_level=load_level,
            )

        except Exception as exc:
            logger.error(
                "engine_generation_failed",
                engine=engine_name,
                error=str(exc),
            )
            # Fallback: Defer statt Error
            return await self._defer_request(request, load_level)

    # ── Engine Selection Logic ───────────────────────────────────────────

    def _select_engine(
        self,
        load_level: SystemLoadLevel,
        request: SomaRequest,
    ) -> str:
        """
        Wähle Engine basierend auf Last und Anfrage-Typ.
        Priorität: Beste Antwortqualität bei verfügbaren Ressourcen.
        """
        # Nano-Intents (Licht an/aus, Heizung, etc.) IMMER schnell
        if self._is_nano_intent(request.prompt):
            return "nano"

        # Load-basiertes Routing
        if load_level in (SystemLoadLevel.IDLE, SystemLoadLevel.NORMAL):
            return "heavy"   # Volle Llama-Power
        elif load_level == SystemLoadLevel.ELEVATED:
            return "light"   # Phi-3 / Llama 3B
        else:  # HIGH
            return "nano"    # Python-Scripts only

    @staticmethod
    def _is_nano_intent(prompt: str) -> bool:
        """Quick-Check ob der Prompt ein einfacher Device-Command ist."""
        nano_keywords = [
            "licht", "light", "lampe", "lamp",
            "heizung", "heating", "temperatur", "temperature",
            "an", "aus", "on", "off",
            "heller", "dunkler", "brighter", "dimmer",
            "wärmer", "kälter", "warmer", "cooler",
        ]
        prompt_lower = prompt.lower().strip()
        words = prompt_lower.split()
        # Kurze Prompts mit Device-Keywords → Nano
        if len(words) <= 6:
            return any(kw in words for kw in nano_keywords)
        return False

    # ── Deferred Reasoning ───────────────────────────────────────────────

    async def _defer_request(
        self,
        request: SomaRequest,
        load_level: SystemLoadLevel,
    ) -> SomaResponse:
        """Anfrage in Redis-Queue parken, sofortiges Feedback geben."""
        self._deferred_counter += 1

        deferred = DeferredRequest(
            request_id=request.request_id,
            user_id=request.user_id,
            room_id=request.room_id,
            prompt=request.prompt,
            priority=request.priority,
        )

        await self.queue.enqueue(deferred)

        # Rotierende Feedback-Messages
        msg_idx = self._deferred_counter % len(DEFERRED_MESSAGES)

        logger.info(
            "request_deferred",
            request_id=request.request_id,
            queue_size=await self.queue.queue_size(),
        )

        return SomaResponse(
            request_id=request.request_id,
            response=DEFERRED_MESSAGES[msg_idx],
            engine_used="deferred",
            was_deferred=True,
            deferred_id=request.request_id,
            load_level=load_level,
        )

    # ── System Prompt Builder ────────────────────────────────────────────

    @staticmethod
    def _build_system_prompt(request: SomaRequest) -> str:
        """Bau den System-Prompt basierend auf Kontext."""
        base = (
            "Du bist Soma, ein freundliches, hocheffizientes Ambient-AI-System. "
            "Du bist nervy-cool, proaktiv und hilfreich. "
            "Du antwortest auf Deutsch, es sei denn der Nutzer spricht eine andere Sprache. "
            "Halte Antworten knapp und präzise."
        )

        if request.is_child:
            base += (
                "\n\nWICHTIG: Du sprichst mit einem Kind. "
                "Verwende einfache Sprache, sei geduldig und ermutigend. "
                "Vermeide komplexe oder unangemessene Themen. "
                "Sei wie ein freundlicher, schlauer Kumpel."
            )

        if request.room_id:
            base += f"\n\nDer Nutzer befindet sich in: {request.room_id}"

        return base
