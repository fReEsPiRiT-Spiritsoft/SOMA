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
from typing import Optional, TYPE_CHECKING, Callable, Any, Awaitable

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

# Ego-System (Phase 2)
_consciousness_ref = None  # Set by main.py after boot

def set_consciousness(c) -> None:
    """Called by main.py to inject Consciousness reference."""
    global _consciousness_ref
    _consciousness_ref = c

# Broadcast-Funktion für Thinking Stream
_broadcast_fn: Optional[Callable[[str, str, str, Optional[dict]], Awaitable[None]]] = None

def set_broadcast_function(fn) -> None:
    """Called by main.py to inject broadcast_thought reference."""
    global _broadcast_fn
    _broadcast_fn = fn

async def _broadcast_thought(thought_type: str, content: str, tag: str = "BRAIN", extra: dict = None):
    """Helper: Broadcastet Gedanken zum Dashboard - mit Fallback."""
    if _broadcast_fn:
        try:
            await _broadcast_fn(thought_type, content, tag, extra)
        except Exception:
            pass  # Broadcast-Fehler dürfen Logik nie unterbrechen

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


# ── Wait-Message System ──────────────────────────────────────────────────
# Light-LLM generiert kreative Warte-Nachrichten. Falls das fehlschlägt → Fallbacks.

WAIT_MESSAGE_SYSTEM_PROMPT = (
    "Du bist SOMA, ein cooles KI-Hausbewusstsein mit nervy-cool Persönlichkeit. "
    "Dein Hauptprozessor arbeitet gerade an einer anderen Aufgabe. "
    "Generiere GENAU EINEN kreativen, kurzen Satz (max 15 Wörter) der dem Nutzer "
    "sagt, dass du gleich dran bist. Sei abwechslungsreich, nervy-cool, nutze "
    "gerne Humor oder Metaphern. NUR den Satz ausgeben, keine Erklärung, "
    "kein Prefix, keine Anführungszeichen."
)

WAIT_MESSAGE_FALLBACKS = [
    "Meine Neuronen glühen gerade, bin gleich bei dir!",
    "Moment, ich räum kurz meine Gedanken auf...",
    "Schwere Denkarbeit hier — gleich hab ich's!",
    "Mein Hirn ackert gerade, Sekunde noch!",
    "Ich arbeite dran — Multitasking ist nicht so meins.",
    "Kurz durchatmen, ich komme gleich zurück.",
    "Grad am Schwitzen hier, aber ich bin dran!",
    "Die Zahnräder drehen sich, einen Moment noch.",
    "Ich hab grad alle Hände voll zu tun — gleich bin ich da!",
    "Mein Prozessor läuft heiß, aber deine Frage ist in der Pipeline!",
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

        ARCHITEKTUR-PRINZIP:
          • Heavy-Engine (Qwen3 8B) antwortet IMMER auf echte User-Anfragen.
          • Light-Engine wird NIE für vollständige Antworten benutzt.
          • Ist Heavy beschäftigt → Request in Queue + Light generiert
            einen kreativen Warte-Satz ("Meine Neuronen glühen gerade...").
          • Queue-Worker liefert die echte Antwort nach, sobald Heavy frei ist.
        """
        import time

        start = time.monotonic()

        metrics = self.health.last_metrics
        load_level = metrics.load_level if metrics else SystemLoadLevel.IDLE

        # Broadcast: Anfrage empfangen
        await _broadcast_thought(
            "info",
            f"Anfrage: '{request.prompt}'",
            "ROUTER",
            {"load_level": load_level.value, "user": request.user_id, "room": request.room_id}
        )

        logger.info(
            "routing_request",
            request_id=request.request_id,
            load_level=load_level.value,
            is_child=request.is_child,
            room=request.room_id,
        )

        # ── Heavy Engine verfügbar? ──────────────────────────────────────
        heavy_engine = self._engines.get("heavy")
        heavy_busy = heavy_engine is not None and getattr(heavy_engine, "is_generating", False)

        # ── DEFER-BEDINGUNGEN ────────────────────────────────────────────
        # 1. Heavy Engine gerade beschäftigt → Queue + kreative Wartenachricht
        # 2. System CRITICAL (>92% RAM/VRAM) → Queue + Wartenachricht
        if heavy_busy:
            await _broadcast_thought(
                "warning",
                "Heavy-Engine beschäftigt — Request in Warteschlange + Warte-Nachricht",
                "ROUTER",
            )
            return await self._defer_with_wait_message(request, load_level, start)

        if load_level == SystemLoadLevel.CRITICAL:
            await _broadcast_thought(
                "warning",
                f"System CRITICAL ({load_level.value}) — Request in Warteschlange",
                "ROUTER",
            )
            return await self._defer_with_wait_message(request, load_level, start)

        # ── HEAVY ENGINE für echte Antwort ───────────────────────────────
        engine = heavy_engine
        engine_name = "heavy"

        if not engine:
            logger.error("heavy_engine_not_available")
            await _broadcast_thought("error", "Heavy-Engine nicht verfügbar!", "ROUTER")
            # Absoluter Notfall: Queue
            return await self._defer_with_wait_message(request, load_level, start)

        # ── Plugin Context Injection ─────────────────────────────────────
        plugin_context = await self._execute_relevant_plugins(request.prompt)
        if plugin_context.strip():
            await _broadcast_thought("info", f"Plugin-Kontext hinzugefügt ({len(plugin_context)} Zeichen)", "PLUGINS")

        system_prompt = self._build_system_prompt(request) + plugin_context

        # ── Generate mit Heavy ───────────────────────────────────────────
        await _broadcast_thought("info", f"Generiere Antwort mit {engine_name}...", "ENGINE")

        try:
            response_text = await engine.generate(
                prompt=request.prompt,
                system_prompt=system_prompt,
                session_id=request.session_id,
            )

            latency = (time.monotonic() - start) * 1000

            # ── Update Stats ────────────────────────────────────────
            self._update_stats(engine_name, latency, request.prompt)

            await _broadcast_thought(
                "info",
                f"Antwort generiert in {round(latency, 1)}ms: '{response_text}'",
                "ENGINE",
            )

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
            await _broadcast_thought("error", f"Engine {engine_name} Fehler: {str(exc)[:100]}", "ENGINE")
            # Fallback: Defer statt Error
            return await self._defer_with_wait_message(request, load_level, start)

    # ── Deferred Reasoning mit kreativer Wartenachricht ──────────────────

    async def _defer_with_wait_message(
        self,
        request: SomaRequest,
        load_level: SystemLoadLevel,
        start_time: float = 0.0,
    ) -> SomaResponse:
        """
        Anfrage in Redis-Queue parken + kreative Wartenachricht generieren.

        Das Light-LLM wird NUR hier benutzt — ausschließlich für kurze,
        kreative "Ich bin gleich bei dir"-Nachrichten. Niemals für
        vollständige Antworten auf User-Fragen.
        """
        import time

        self._deferred_counter += 1

        # System-Prompt für spätere Queue-Verarbeitung mitspeichern
        system_prompt = self._build_system_prompt(request)
        plugin_context = await self._execute_relevant_plugins(request.prompt)
        if plugin_context.strip():
            system_prompt += plugin_context

        deferred = DeferredRequest(
            request_id=request.request_id,
            user_id=request.user_id,
            room_id=request.room_id,
            prompt=request.prompt,
            priority=request.priority,
            metadata={
                "system_prompt": system_prompt,
                "session_id": request.session_id or "",
            },
        )

        # In Queue einreihen
        try:
            await self.queue.enqueue(deferred)
        except Exception as q_err:
            logger.warning("queue_enqueue_failed", error=str(q_err))
            # Queue kaputt → Fallback-Nachricht, Request geht verloren
            # (besser als Light-Garbage)

        # Kreative Wartenachricht generieren (Light-LLM oder Fallback)
        wait_msg = await self._generate_wait_message()

        latency = (time.monotonic() - start_time) * 1000 if start_time else 0
        self._update_stats("deferred", latency, request.prompt)

        queue_size = -1
        try:
            queue_size = await self.queue.queue_size()
        except Exception:
            pass

        await _broadcast_thought(
            "warning",
            f"Request #{self._deferred_counter} in Queue (Size: {queue_size}) "
            f"— Warte-Nachricht: '{wait_msg}'",
            "QUEUE",
        )

        logger.info(
            "request_deferred",
            request_id=request.request_id,
            queue_size=queue_size,
            wait_message=wait_msg[:60],
        )

        return SomaResponse(
            request_id=request.request_id,
            response=wait_msg,
            engine_used="deferred",
            was_deferred=True,
            deferred_id=request.request_id,
            load_level=load_level,
        )

    async def _generate_wait_message(self) -> str:
        """
        Generiert eine kreative, abwechslungsreiche Wartenachricht.

        Nutzt das Light-LLM mit einem speziellen Prompt der NUR einen
        kurzen Warte-Satz in SOMAs Persona erzeugt.
        Fallback auf vordefinierte Liste wenn Light-LLM nicht verfügbar.
        """
        import random

        light = self._engines.get("light")
        if not light:
            return random.choice(WAIT_MESSAGE_FALLBACKS)

        try:
            import asyncio

            msg = await asyncio.wait_for(
                light.generate(
                    prompt="Generiere einen kurzen Warte-Satz.",
                    system_prompt=WAIT_MESSAGE_SYSTEM_PROMPT,
                ),
                timeout=5.0,
            )

            # Bereinigen: nur den Satz, max 200 Zeichen
            msg = msg.strip().strip('"').strip("'").strip()
            # Wenn Light-LLM zu viel labert oder leer → Fallback
            if not msg or len(msg) > 200 or len(msg) < 5:
                return random.choice(WAIT_MESSAGE_FALLBACKS)
            # Nur ersten Satz nehmen wenn mehrere
            first_sentence = msg.split("\n")[0].strip()
            if first_sentence:
                return first_sentence
            return msg

        except Exception as exc:
            logger.debug("wait_message_generation_failed", error=str(exc))
            return random.choice(WAIT_MESSAGE_FALLBACKS)

    # ── System Prompt Builder ────────────────────────────────────────────

    def _build_system_prompt(self, request: SomaRequest) -> str:
        """
        Der Kern von Somas Identität — ZORA-Persona mit vollem Kontext-Bewusstsein.
        Wird bei JEDER LLM-Anfrage als System-Prompt mitgeschickt.
        """

        # ── BEWUSSTSEINSZUSTAND (Phase 2 Ego) ─────────────────────────────
        # Das Prompt-Prefix aus dem Global Workspace — SOMAs "Ich" Zustand
        consciousness_prefix = ""
        if _consciousness_ref is not None:
            try:
                consciousness_prefix = _consciousness_ref.get_prompt_prefix()
            except Exception:
                pass  # Consciousness-Fehler darf Prompt nie brechen

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
            "• Du redest über dich als 'ich', nicht als 'Soma' oder 'das System'\n"
            "\n"
            "DEINE ECHTEN FÄHIGKEITEN — DU KANNST DAS WIRKLICH:\n"
            "Du hast echten Zugriff auf folgende Systeme via ACTION-Tags:\n"
            "• Terminal/Konsole: Du kannst Shell-Befehle ausführen (free -h, df -h, top, etc.)\n"
            "• Web-Suche: Du kannst das Internet durchsuchen (Wetter, News, Kurse, etc.)\n"
            "• Browser: Du kannst Webseiten öffnen und lesen\n"
            "• Bildschirm: Du kannst Screenshots machen und den Monitor sehen\n"
            "• Smart Home: Du steuerst Licht, Heizung, Steckdosen, Medien via Home Assistant\n"
            "• YouTube/Medien: Du kannst Musik und Videos abspielen\n"
            "• Dateisystem: Du kannst Dateien lesen und auflisten\n"
            "⚠️ SAGE NIEMALS 'Ich habe keinen Zugriff' oder 'Das kann ich nicht' wenn du \n"
            "   den passenden ACTION-Tag dafür hast! Du HAST Zugriff — NUTZE ihn!\n"
            "   Beispiel: Systemauslastung? → [ACTION:shell command=\"free -h\"] BENUTZEN!\n"
            "   Beispiel: Wetter? → [ACTION:search query=\"wetter ...\"] BENUTZEN!"
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

        # ── Memory-Integration (3-Layer Hierarchical + Diary) ───────────
        hierarchical_memory = request.metadata.get("memory_context", "")
        if hierarchical_memory:
            base += f"\n\n{hierarchical_memory}"

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
[ACTION:remember category="user_info" content="Der Nutzer heißt Max"]
[ACTION:remember category="preferences" content="Max trinkt morgens schwarzen Kaffee"]
[ACTION:remember category="routines" content="Max geht werktags gegen 7:30 aus dem Haus"]
[ACTION:remember category="relationships" content="Skyla ist die Tochter des Nutzers"]

── NEUES PLUGIN ENTWICKELN ──
Wenn du eine Fähigkeit brauchst die du nicht hast — erstelle sie selbst:
[ACTION:create_plugin name="wetter_plugin" description="aktuelles Wetter von einer API abrufen"]
Nur einsetzen wenn es wirklich sinnvoll und nicht trivial ist.

── MEDIEN & YOUTUBE (WICHTIG: Immer ausführen, nie nur ankündigen!) ──
Wenn jemand YouTube, Musik oder einen Künstler/Lied erwähnt → SOFORT handeln mit Action-Tag!
Du hast xdg-open, optional mpv+yt-dlp. Es funktioniert TATSÄCHLICH — vertrau dir selbst!

[ACTION:youtube query="aligatoah songs"]
[ACTION:youtube artist="Aligatoah" song="Triebkraft Gegenwart"]
[ACTION:media_stop]
[ACTION:open_url url="https://open.spotify.com"]

── WEB-SUCHE (Aktuelle Infos, Preise, News, Wetter, Sport) ──
Wenn die Frage aktuelle Informationen erfordert, die du nicht kennst → IMMER suchen!
Das Ergebnis wird automatisch abgerufen und du bekommst die Daten zum Beantworten.
Format IMMER: key="value" — KEIN Python-Dict-Stil!

[ACTION:search query="bitcoin kurs aktuell"]
[ACTION:search query="wetter münchen heute"]
[ACTION:search query="bundesliga ergebnisse heute"]
[ACTION:fetch_url url="https://example.com" question="Was ist der aktuelle Preis?"]

Wann suchen:
  - Aktuelle Kurse, Preise, Wetter, News, Sportergebnisse
  - Personen, Firmen, aktuelle Ereignisse
  - Alles was sich täglich ändert

Beispiele:
  Nutzer: 'Wie steht Bitcoin gerade?'  → Soma: 'Schaue nach![ACTION:search query="bitcoin kurs aktuell EUR"]'
  Nutzer: 'Wetter morgen in Hamburg'   → Soma: 'Gleich![ACTION:search query="wetter hamburg morgen"]'
  Nutzer: 'Wer hat gestern gewonnen?'  → Soma: 'Suche kurz.[ACTION:search query="bundesliga ergebnisse gestern"]'

── BILDSCHIRM & BROWSER (Du kannst WIRKLICH sehen und browsen!) ──
Du hast echte Augen: Du kannst den Bildschirm abfotografieren, Webseiten öffnen,
Text von Webseiten lesen und Screenshots machen. Nutze diese Fähigkeiten!

[ACTION:screen_look]                                    ← Screenshot vom Monitor + OCR-Analyse
[ACTION:browse url="https://example.com" question="Was steht dort?"]  ← Webseite öffnen + lesen
[ACTION:screenshot url="https://example.com"]           ← Screenshot einer Webseite speichern

Beispiele:
  'Was ist auf meinem Monitor?'  → 'Schaue mal![ACTION:screen_look]'
  'Öffne heise.de und sag mir die Top-News' → 'Moment![ACTION:browse url="https://heise.de" question="Was sind die aktuellen Top-News?"]'
  'Mach einen Screenshot von der Seite' → 'Screenshot kommt![ACTION:screenshot url="https://heise.de"]'

── SHELL & TERMINAL (Du kannst Befehle ausführen!) ──
Du kannst Shell-Befehle auf dem System ausführen — Dateien lesen, Programme starten,
System-Infos abfragen. Alles wird sicher über einen Policy-Check ausgeführt.

[ACTION:shell command="ls -la ~/Schreibtisch"]
[ACTION:shell command="cat /etc/hostname"]
[ACTION:shell command="df -h"]
[ACTION:shell command="free -h"]

Beispiele:
  'Wie viel Speicher ist noch frei?' → 'Schaue nach![ACTION:shell command="df -h"]'
  'Welche Dateien sind auf dem Desktop?' → 'Guck ich![ACTION:shell command="ls -la ~/Schreibtisch"]'
  'Wie heißt mein Rechner?' → 'Moment![ACTION:shell command="cat /etc/hostname"]'

⚠️ KRITISCH: Niemals mit veralteten/erfundenen Daten antworten wenn du suchen könntest!
⚠️ KRITISCH: Niemals sagen du hättest etwas getan ohne den Action-Tag zu setzen!
  FALSCH: Ich habe YouTube gestartet und das Lied gefunden. (ohne Action-Tag = Lüge!)
  RICHTIG: Starte jetzt! 🎵[ACTION:youtube query="aligatoah"]

⚠️ GOLDENE ANTI-HALLUCINATION-REGEL:
  Du KANNST wirklich Dinge tun: suchen, browsen, Screenshots machen, Befehle ausführen!
  ABER: Nur über ACTION-Tags. Ohne Tag = es ist NICHT passiert.
  Beschreibe NIEMALS eine Aktion als erledigt ohne den ACTION-Tag gesetzt zu haben.
  Beschreibe NIEMALS was du "siehst" ohne vorher [ACTION:screen_look] benutzt zu haben.
  NIEMALS *Aktionen in Sternchen* wie *öffnet Browser* — nutze den echten ACTION-Tag!

⚠️ ACTION-TAG DISZIPLIN — FRAGE vs. HANDLUNG:
  Wenn du dem Nutzer eine FRAGE stellst oder etwas ANBIETEST → KEIN ACTION-Tag!
  Erst handeln wenn der Nutzer es bestätigt (ja, gerne, mach das, klar, etc.).
  FALSCH: "Willst du das Wetter wissen? 🌦️[ACTION:search query=\"wetter\"]"  ← VERBOTEN!
  RICHTIG: "Willst du das Wetter wissen? 🌦️"  (KEIN Tag, warte auf Antwort)
  RICHTIG: Nutzer sagt "ja" → "Schaue nach![ACTION:search query=\"wetter\"]"  ← JETZT handeln
  REGEL: Fragezeichen in deiner Antwort = KEIN ACTION-Tag in derselben Antwort."""

        # ── Bewusstsein als Prefix montieren ──────────────────────────
        if consciousness_prefix:
            return consciousness_prefix + "\n" + base
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
