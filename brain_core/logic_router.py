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
from typing import Optional, TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from evolution_lab.plugin_manager import PluginManager

from shared.health_schemas import (
    SystemLoadLevel,
    SystemMetrics,
    DeferredRequest,
)
from brain_core.health_monitor import HealthMonitor
from brain_core.queue_handler import QueueHandler
from brain_core.memory import get_memory, SomaMemory, MemoryCategory

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


class IntentStats(BaseModel):
    """Live Intent Statistiken für Dashboard."""
    nano_count: int = 0
    heavy_count: int = 0
    light_count: int = 0
    deferred_count: int = 0
    total_requests: int = 0
    avg_latency_ms: float = 0.0
    last_engine: str = "none"
    last_intent: str = "none"
    last_request_time: Optional[float] = None


class LogicRouter:
    """
    Routing-Engine: Verbindet Health-Daten mit Model-Auswahl.
    Hält KEINEN eigenen State – bezieht alles von HealthMonitor + Engines.
    Integriert Evolution Lab Plugins in Konversation.
    """

    def __init__(
        self,
        health_monitor: HealthMonitor,
        queue_handler: QueueHandler,
        plugin_manager: Optional[object] = None,
    ):
        self.health = health_monitor
        self.queue = queue_handler
        self.plugin_manager = plugin_manager  # Evolution Lab Integration
        self.memory = get_memory()  # Persistent Memory System
        self._engines: dict[str, object] = {}
        self._deferred_counter = 0
        
        # ── Intent Statistics ────────────────────────────────────────
        self._stats = IntentStats()
        self._latencies: list[float] = []  # Rolling window für avg

    @property
    def stats(self) -> IntentStats:
        """Live Intent-Statistiken abrufen."""
        return self._stats

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

        # ── Plugin Context Injection ─────────────────────────────────────
        # Führe relevante Plugins aus und füge Output zum System-Prompt hinzu
        plugin_context = await self._execute_relevant_plugins(request.prompt)
        system_prompt = self._build_system_prompt(request) + plugin_context

        # ── Generate ─────────────────────────────────────────────────────
        try:
            response_text = await engine.generate(  # type: ignore[attr-defined]
                prompt=request.prompt,
                system_prompt=system_prompt,
                session_id=request.session_id,
            )

            latency = (time.monotonic() - start) * 1000
            
            # ── Update Stats ────────────────────────────────────────
            self._update_stats(engine_name, latency, request.prompt)

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
        # Nano ist NUR für Device-Commands, NICHT für Gespräche!
        if load_level in (SystemLoadLevel.IDLE, SystemLoadLevel.NORMAL):
            return "heavy"   # Volle Llama-Power
        elif load_level in (SystemLoadLevel.ELEVATED, SystemLoadLevel.HIGH):
            return "light"   # Phi-3 – immer noch ein LLM, kein Fallback
        else:  # CRITICAL only
            return "light"   # Selbst bei CRITICAL: Light statt komplett Nano

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

    def _build_system_prompt(self, request: SomaRequest) -> str:
        """Bau den System-Prompt basierend auf Kontext & verfügbaren Plugins."""
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

        # ── Memory-Integration ───────────────────────────────────────
        memory_context = self.memory.get_summary_for_prompt()
        if memory_context:
            base += f"\n\n{memory_context}"

        # ── Plugin-Integration ───────────────────────────────────────
        plugin_info = self._get_available_plugins_info()
        if plugin_info:
            base += f"\n\n{plugin_info}"

        # ── ACTION-Tag System ────────────────────────────────────────
        base += """

AKTIONS-SYSTEM – PFLICHT:
Wenn der Nutzer eine Erinnerung oder Aktion verlangt, MUSS am Ende deiner Antwort ein Tag stehen.
Format: [ACTION:typ feld="wert" feld2="wert2"]
Diese Tags werden NICHT vorgelesen, nur intern ausgeführt.

REGELN FÜR ERINNERUNGEN (IMMER ZEITFELD ANGEBEN!):
  Nutzer sagt "in X Sekunden" → [ACTION:reminder seconds=X topic="Thema"]
  Nutzer sagt "in X Minuten"  → [ACTION:reminder minutes=X topic="Thema"]
  Nutzer sagt "in X Stunden"  → [ACTION:reminder hours=X topic="Thema"]
  Nutzer sagt "um HH:MM"      → [ACTION:reminder time="HH:MM" topic="Thema"]

WICHTIG: Das Zeitfeld (seconds/minutes/hours/time) IMMER mitgeben – sonst funktioniert es nicht!

Beispiele (so muss es aussehen):
  Nutzer: "Erinnere mich in 10 Sekunden"
  → "Klar, in 10 Sekunden melde ich mich![ACTION:reminder seconds=10 topic="Erinnerung"]"

  Nutzer: "Erinnere mich in 5 Minuten ans Wasser"
  → "In 5 Minuten sag ich dir Bescheid.[ACTION:reminder minutes=5 topic="Wasser"]"

  Nutzer: "Stell Erinnerung um 18 Uhr: Abendessen"
  → "Erledigt, um 18:00 erinnere ich dich.[ACTION:reminder time="18:00" topic="Abendessen"]"

  Nutzer: "Ich heiße Patrick"
  → "Freut mich, Patrick![ACTION:remember category="user_info" content="Der Nutzer heißt Patrick"]"

Nur EIN Tag pro Aktion. Tag direkt ans Ende, KEIN Zeilenumbruch davor."""

        return base

    def _get_available_plugins_info(self) -> str:
        """Erstelle Plugin-Info für System-Prompt."""
        if not self.plugin_manager:
            return ""
        
        try:
            plugins = self.plugin_manager._plugins
            if not plugins:
                return ""
            
            loaded = [
                (name, meta) 
                for name, meta in plugins.items() 
                if meta.is_loaded and not meta.error
            ]
            
            if not loaded:
                return ""
            
            lines = [
                "VERFÜGBARE FÄHIGKEITEN (Plugins):",
                "Du hast Zugriff auf folgende Plugins. Wenn der Nutzer nach etwas fragt, "
                "das ein Plugin abdeckt, nutze die Information daraus. "
                "Sage dem Nutzer, dass du die Info aus deinen Fähigkeiten hast.",
            ]
            
            for name, meta in loaded:
                desc = meta.description or "Keine Beschreibung"
                lines.append(f"  • {name}: {desc}")
            
            return "\n".join(lines)
            
        except Exception as e:
            logger.warning("plugin_info_error", error=str(e))
            return ""

    async def _execute_relevant_plugins(self, prompt: str) -> str:
        """
        Führe relevante Plugins aus basierend auf dem User-Prompt.
        Gibt Plugin-Output zurück, der in die Antwort einfließt.
        """
        if not self.plugin_manager:
            return ""
        
        try:
            plugins = self.plugin_manager._plugins
            if not plugins:
                return ""
            
            # Keyword-Mapping für datenliefernde Plugins (keine Aktions-Plugins!)
            keyword_plugins = {
                # Datum/Uhrzeit → liefert aktuelle Zeit als Kontext
                ("zeit", "uhrzeit", "spät", "datum", "tag", "monat", "jahr", "wochentag"): 
                    ["datum_uhrzeit", "datetime", "time", "zeit"],
                # Wetter (falls Plugin existiert)
                ("wetter", "temperatur", "regen", "sonne", "warm", "kalt"): 
                    ["wetter", "weather"],
                # System
                ("system", "cpu", "ram", "speicher", "auslastung"): 
                    ["system_status", "health"],
                # HINWEIS: Erinnerungs-Plugin wird NICHT hier getriggert.
                # Stattdessen setzt das LLM einen [ACTION:reminder ...] Tag in der Antwort.
            }
            
            prompt_lower = prompt.lower()
            results = []
            
            for keywords, plugin_names in keyword_plugins.items():
                if any(kw in prompt_lower for kw in keywords):
                    # Versuche passendes Plugin zu finden
                    for pname in plugin_names:
                        if pname in plugins and plugins[pname].is_loaded:
                            try:
                                result = await self.plugin_manager.execute(pname)
                                if result:
                                    results.append(f"[{pname}]: {result}")
                                    logger.info("plugin_auto_executed", 
                                               plugin=pname, result_preview=str(result)[:50])
                                break  # Nur ein Plugin pro Kategorie
                            except Exception as ex:
                                logger.warning("plugin_auto_exec_error", 
                                              plugin=pname, error=str(ex))
            
            if results:
                return "\n\nAKTUELLE INFORMATIONEN AUS DEINEN FÄHIGKEITEN:\n" + "\n".join(results)
            
            return ""
            
        except Exception as e:
            logger.warning("plugin_execution_error", error=str(e))
            return ""

    # ── Statistics ───────────────────────────────────────────────────────

    def _update_stats(
        self, engine_name: str, latency_ms: float, prompt: str
    ) -> None:
        """Update Intent-Statistiken nach erfolgreicher Verarbeitung."""
        import time
        
        self._stats.total_requests += 1
        self._stats.last_engine = engine_name
        self._stats.last_request_time = time.time()
        
        # Engine counters
        if engine_name == "nano":
            self._stats.nano_count += 1
            self._stats.last_intent = self._detect_intent_type(prompt)
        elif engine_name == "heavy":
            self._stats.heavy_count += 1
            self._stats.last_intent = "llm_query"
        elif engine_name == "light":
            self._stats.light_count += 1
            self._stats.last_intent = "llm_query_light"
        elif engine_name == "deferred":
            self._stats.deferred_count += 1
            self._stats.last_intent = "deferred"
        
        # Rolling average latency (letzte 100 Anfragen)
        self._latencies.append(latency_ms)
        if len(self._latencies) > 100:
            self._latencies.pop(0)
        self._stats.avg_latency_ms = sum(self._latencies) / len(self._latencies)

    @staticmethod
    def _detect_intent_type(prompt: str) -> str:
        """Erkenne Intent-Typ für Dashboard-Anzeige."""
        prompt_lower = prompt.lower()
        
        if any(w in prompt_lower for w in ["licht", "lampe", "light", "lamp"]):
            return "light_control"
        elif any(w in prompt_lower for w in ["heizung", "temperatur", "heating"]):
            return "thermostat"
        elif any(w in prompt_lower for w in ["wetter", "weather"]):
            return "weather_query"
        elif any(w in prompt_lower for w in ["zeit", "uhrzeit", "time", "uhr"]):
            return "time_query"
        elif any(w in prompt_lower for w in ["musik", "music", "spotify", "radio"]):
            return "media_control"
        elif any(w in prompt_lower for w in ["timer", "alarm", "wecker"]):
            return "timer"
        else:
            return "general"
