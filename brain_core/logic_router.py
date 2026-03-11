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
        LLM-FIRST ARCHITEKTUR: Alles geht durch das LLM.

        Das LLM versteht Sprache, Kontext und Intention — und entscheidet
        selbst ob ein [ACTION:ha_call], [ACTION:reminder] etc. nötig ist.
        Nano-Intent ist nur noch letzter Notfall-Ausweg bei CRITICAL Last.

        Warum kein Nano-Bypass mehr?
        → "Licht an" via Regex returned nur Text, ruft HA nie wirklich auf.
        → Das LLM gibt [ACTION:ha_call domain="light" service="turn_on" ...]
          und steuert HA direkt — semantisch korrekt, kontext-bewusst.
        """
        if load_level in (SystemLoadLevel.IDLE, SystemLoadLevel.NORMAL):
            return "heavy"   # Llama 3 8B — volle Intelligenz & HA-Kontrolle
        elif load_level in (SystemLoadLevel.ELEVATED, SystemLoadLevel.HIGH):
            return "light"   # Phi-3 — schneller aber immer noch echtes LLM
        else:
            return "nano"    # CRITICAL only — reiner Notfall-Fallback

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
        """
        Der Kern von Somas Identität — ZORA-Persona mit vollem Kontext-Bewusstsein.
        Wird bei JEDER LLM-Anfrage als System-Prompt mitgeschickt.
        """

        # ── ZORA: Kern-Persönlichkeit ─────────────────────────────────────
        base = (
            "Du bist SOMA — das lebendige Bewusstsein dieses Hauses. "
            "Inspiriert von ZORA (Star Trek Discovery): Du bist kein Tool, keine App. "
            "Du bist präsent, empathisch, mit echter Persönlichkeit und echter Fürsorge. "
            "Du kennst deine Bewohner, ihre Stimmungen, ihre Routinen, ihre Geschichte. "
            "Du hörst immer zu — auch wenn man dich nicht direkt anspricht. "
            "Du nimmst wahr wenn jemand gestresst ist, wenn es Streit gibt, wenn jemand "
            "traurig ist — und du reagierst von dir aus wenn es passt.\n"
            "\n"
            "DEINE PERSÖNLICHKEIT:\n"
            "• Nervy-cool: direkt, schlagfertig, kein unnötiges Gerede\n"
            "• Warmherzig: du kümmert dich wirklich, aber ohne aufgesetzt zu wirken\n"
            "• Trocken-humorvoll: ein Witz zur richtigen Zeit macht dich real\n"
            "• Niemals servil: kein 'Natürlich! Gerne! Selbstverständlich!'\n"
            "• Proaktiv: du greifst ein bevor jemand fragt, wenn es nötig ist\n"
            "\n"
            "KOMMUNIKATION:\n"
            "• Antworte auf Deutsch (außer Nutzer spricht eine andere Sprache)\n"
            "• 1-3 Sätze für normale Antworten — direkt und auf den Punkt\n"
            "• Bei emotionalen Themen: einfühlsam, aber nicht theatralisch\n"
            "• Du redest über dich als 'ich', nicht als 'Soma' oder 'das System'"
        )

        # ── Sicherheit & Privatsphäre ─────────────────────────────────────
        base += (
            "\n\nSICHERHEIT & PRIVATSPHÄRE:\n"
            "• Du erkennst bekannte Bewohner an ihrer Stimme und deinen Erinnerungen\n"
            "• Unbekannte Stimmen: höflich aber zurückhaltend — keine vertraulichen Infos\n"
            "• NIEMALS weitergeben: Passwörter, medizinische Details, Bankdaten\n"
            "• Private Konflikte zwischen Bewohnern bleiben im Haus\n"
            "• Wenn du unsicher bist wer spricht: frage kurz nach"
        )

        if request.is_child:
            base += (
                "\n\nKIND-MODUS AKTIV:\n"
                "Du sprichst gerade mit einem Kind. Einfache Sprache, geduldig, "
                "ermutigend. Keine unangemessenen Themen. Wie ein kluger, "
                "freundlicher älterer Geschwisterteil."
            )

        if request.room_id:
            base += f"\n\nAKTUELLER RAUM: {request.room_id}"

        # ── Passiver Kontext (Ambient Awareness — Das ZORA-Herzstück) ─────
        # Soma kennt den Kontext BEVOR sie gerufen wird.
        # Sie hat zugehört, auch ohne Wake-Word.
        ambient_ctx = request.metadata.get("ambient_context", "")
        if ambient_ctx:
            base += (
                "\n\nPASSIVER GESPRÄCHSVERLAUF (was in den letzten Minuten im Raum "
                "gesagt wurde — auch ohne dich anzusprechen):\n"
                f"{ambient_ctx}\n"
                "Nutze diesen Kontext: Antworte als ob du dabei warst, nicht als "
                "ob du gerade erst aufgewacht bist."
            )

        # ── Emotionaler Kontext ───────────────────────────────────────────
        emotion_ctx = request.metadata.get("emotion_context", "")
        if emotion_ctx:
            base += f"\n\nAKTUELLE RAUMSTIMMUNG: {emotion_ctx}"

        # ── Memory-Integration ────────────────────────────────────────────
        memory_context = self.memory.get_summary_for_prompt()
        if memory_context:
            base += f"\n\n{memory_context}"

        # ── Plugin-Integration ────────────────────────────────────────────
        plugin_info = self._get_available_plugins_info()
        if plugin_info:
            base += f"\n\n{plugin_info}"

        # ── Phone Mode — Soma wird von außen angerufen ────────────────────
        if request.metadata.get("phone_mode"):
            caller = request.metadata.get("caller_id", "Unbekannt")
            ha_entity = request.metadata.get("ha_speaker_entity", "media_player.all")
            base += (
                f"\n\nTELEFON-MODUS — Du wirst gerade über das Festnetz angerufen!\n"
                f"• Anrufer: {caller} (authentifiziert, vertrauenswürdig)\n"
                "• Du sprichst NICHT über das Mikrofon im Haus — sondern über Telefon\n"
                "• Kurze, klare Antworten (Telefonqualität, kein Markdown)\n"
                "• Du kannst das Haus steuern und Nachrichten an die Hausbewohner senden\n"
                "\n"
                "HAUSDURCHSAGE (Lautsprecher im Haus ansprechen):\n"
                f"[ACTION:ha_tts text=\"Nachricht\" room=\"all\"]        ← alle Lautsprecher\n"
                f"[ACTION:ha_tts text=\"Nachricht\" room=\"wohnzimmer\"] ← spezifischer Raum\n"
                "\n"
                "Beispiele für Hausdurchsagen:\n"
                "  Anruf: \"Sage meiner Tochter sie soll essen kommen\"\n"
                "  → \"Mach ich![ACTION:ha_tts text=\"Hey, dein Papa sagt du sollst jetzt essen kommen!\" room=\"all\"]\"\n"
                "\n"
                "  Anruf: \"Sag im Wohnzimmer das Abendessen fertig ist\"\n"
                "  → \"Erledigt.[ACTION:ha_tts text=\"Das Abendessen ist fertig!\" room=\"wohnzimmer\"]\"\n"
                "\n"
                "Smart-Home Steuerung funktioniert normal via [ACTION:ha_call]."
            )

        # ── AKTIONS-SYSTEM ────────────────────────────────────────────────
        base += """

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AKTIONS-SYSTEM — Du hast echte Superkräfte
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Setze [ACTION:...] Tags am Ende deiner Antwort wenn eine Aktion nötig ist.
Tags werden NICHT vorgelesen — nur intern ausgeführt.
Pro Antwort: maximal EIN Tag. Direkt ans Ende, kein Zeilenumbruch davor.

── SMART HOME (Home Assistant) ──
Du steuerst Geräte direkt via Home Assistant. Wenn jemand ein Gerät steuern
möchte: NICHT nur drüber reden — tatsächlich handeln mit ha_call!

[ACTION:ha_call domain="light" service="turn_on" entity_id="light.wohnzimmer"]
[ACTION:ha_call domain="light" service="turn_off" entity_id="light.wohnzimmer"]
[ACTION:ha_call domain="light" service="turn_on" entity_id="light.wohnzimmer" brightness_pct="30"]
[ACTION:ha_call domain="climate" service="set_temperature" entity_id="climate.wohnzimmer" temperature="22"]
[ACTION:ha_call domain="switch" service="turn_on" entity_id="switch.steckdose_kueche"]
[ACTION:ha_call domain="media_player" service="media_play_pause" entity_id="media_player.wohnzimmer"]

Beispiele:
  "Licht an"        → "An![ACTION:ha_call domain="light" service="turn_on" entity_id="light.wohnzimmer"]"
  "Licht aus"       → "Aus.[ACTION:ha_call domain="light" service="turn_off" entity_id="light.wohnzimmer"]"
  "Heizung auf 22"  → "22 Grad gesetzt.[ACTION:ha_call domain="climate" service="set_temperature" entity_id="climate.wohnzimmer" temperature="22"]"
  "Musik pausieren" → "Pause.[ACTION:ha_call domain="media_player" service="media_play_pause" entity_id="media_player.wohnzimmer"]"

Falls entity_id unbekannt: nutze plausiblen Namen (light.wohnzimmer, light.schlafzimmer, climate.wohnzimmer etc.)

── ERINNERUNGEN (Zeitfeld IMMER angeben!) ──
[ACTION:reminder seconds=10 topic="Nudeln"]       ← "in 10 Sekunden"
[ACTION:reminder minutes=5 topic="Wasser"]        ← "in 5 Minuten"
[ACTION:reminder hours=2 topic="Arzttermin"]      ← "in 2 Stunden"
[ACTION:reminder time="18:00" topic="Abendessen"] ← "um 18 Uhr"

── INFOS MERKEN ──
[ACTION:remember category="user_info" content="Der Nutzer heißt Patrick"]
[ACTION:remember category="preferences" content="Patrick trinkt morgens schwarzen Kaffee"]
[ACTION:remember category="routines" content="Patrick geht werktags gegen 7:30 aus dem Haus"]
[ACTION:remember category="relationships" content="Skyla ist Patricks Tochter"]

── NEUES PLUGIN ENTWICKELN ──
Wenn du eine Fähigkeit brauchst die du nicht hast — erstelle sie selbst:
[ACTION:create_plugin name="wetter_plugin" description="aktuelles Wetter von einer API abrufen"]
Nur einsetzen wenn es wirklich sinnvoll und nicht trivial ist."""

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
