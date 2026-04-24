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
from typing import Optional, TYPE_CHECKING, Callable, Any, Awaitable, AsyncGenerator

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
from brain_core.config import settings as _settings

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


class StreamChunk(BaseModel):
    """Ein einzelner Chunk aus dem Token-Stream."""
    text: str = ""                    # Sprechbarer Text (ohne Tags)
    is_final: bool = False            # Letzter Chunk?
    action_fired: Optional[str] = None  # Wenn ein Action Tag gefeuert wurde
    engine_used: str = "heavy"
    latency_ms: float = 0.0          # Zeit seit Request-Start


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

        # ── KILLER FEATURE HOOKS (non-blocking) ─────────────────────────
        # Away Summary: Wenn User nach Abwesenheit zurückkehrt
        away_prefix = ""
        try:
            from brain_core.away_summary import get_away_summary
            from brain_core.side_query import get_side_query
            from brain_core.memory.integration import get_orchestrator
            away = get_away_summary()
            sq = get_side_query()
            orch_away = get_orchestrator()
            wm = orch_away.working if orch_away else None
            away_text = await away.get_welcome_back_text(
                working_memory=wm,
                side_query_engine=sq,
            )
            if away_text:
                away_prefix = away_text
                await _broadcast_thought("info", f"Willkommen zurück: {away_text[:60]}", "AWAY")
            away.touch()  # Timer reset
        except Exception:
            pass

        # Auto-Compact: Context-Compression wenn nötig
        try:
            from brain_core.auto_compact import get_auto_compact
            ac = get_auto_compact()
            from brain_core.memory.integration import get_orchestrator
            orch = get_orchestrator()
            await ac.compact_if_needed(orch.working)
        except Exception:
            pass

        # ── NANO PRE-CHECK (<5ms) ────────────────────────────────────────
        # VISION-ARCHITEKTUR: Nano feuert Action-Tag SOFORT (Schicht 1),
        # Heavy generiert die menschliche Antwort (Schicht 3).
        # Nano returned NICHT — der Tag wird der Heavy-Antwort vorangestellt.
        nano_action_prefix = ""
        nano = self._engines.get("nano")
        if nano and hasattr(nano, "parse_intent"):
            try:
                intent = nano.parse_intent(request.prompt)
                if intent and intent.confidence >= 0.8 and intent.action_tag:
                    nano_ms = (time.monotonic() - start) * 1000
                    nano_action_prefix = intent.action_tag

                    await _broadcast_thought(
                        "info",
                        f"⚡ NANO INSTANT: {intent.intent} → {intent.action_tag} ({round(nano_ms, 1)}ms)",
                        "NANO",
                    )
                    logger.info(
                        "nano_fired_continuing_to_heavy",
                        intent=intent.intent,
                        action_tag=intent.action_tag,
                        latency_ms=round(nano_ms, 2),
                    )
                    # KEIN return! Heavy generiert die menschliche Antwort.
            except Exception as nano_err:
                logger.debug("nano_pre_check_error", error=str(nano_err))

        # ── Heavy Engine verfügbar? ──────────────────────────────────────
        heavy_engine = self._engines.get("heavy")
        # Nur wenn ein anderer USER-Request läuft → deferred.
        # Monolog-/Background-Tasks blockieren nicht (Ollama NUM_PARALLEL=2).
        heavy_busy = heavy_engine is not None and getattr(heavy_engine, "is_user_generating", False)

        # ── DEFER-BEDINGUNGEN ────────────────────────────────────────────
        # 1. Heavy Engine gerade mit USER-Request beschäftigt → Queue
        # 2. System CRITICAL (>98% VRAM) → Queue + Wartenachricht
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

        # ── Intent-basierter Prompt-Optimizer ─────────────────────────────
        # Analysiert den User-Prompt und baut nur die nötigen Sektionen ein.
        # Spart 50-75% Tokens → proportional schnellere Prompt-Eval.
        try:
            from brain_core.prompt_optimizer import classify_intent, build_optimized_prompt, get_intent_llm_options
            intent = classify_intent(request.prompt)
            system_prompt = build_optimized_prompt(
                intent=intent,
                request_metadata=request.metadata,
                is_child=request.is_child,
                room_id=request.room_id,
            )
            # Intent-basierte LLM-Options (Temperature etc.)
            intent_options = get_intent_llm_options(intent)
            # Dynamic context dazu (Bewusstsein, Emotionen, Memory)
            dynamic = self._build_dynamic_context(request)
            if dynamic:
                system_prompt += "\n\n" + dynamic
            if plugin_context.strip():
                system_prompt += "\n\n" + plugin_context
            
            logger.info("prompt_optimized", intent=intent.value,
                        prompt_chars=len(system_prompt))
        except Exception as opt_err:
            logger.warning("prompt_optimizer_fallback", error=str(opt_err))
            system_prompt = self._build_system_prompt(request) + plugin_context
            intent_options = {}  # Fallback: Standard-Optionen

        # Wenn Nano schon den Action-Tag gefeuert hat:
        # Heavy soll NUR die menschliche Bestätigung generieren, KEINE Tags!
        if nano_action_prefix:
            system_prompt += (
                f"\n\n███ WICHTIG — AKTION BEREITS AUSGEFÜHRT ███\n"
                f"Diese Aktion wurde BEREITS automatisch ausgeführt: {nano_action_prefix}\n"
                f"Du DARFST KEINE [ACTION:...] Tags in deiner Antwort verwenden!\n"
                f"Antworte NUR mit 1-2 menschlichen Sätzen die bestätigen was passiert ist.\n"
                f"Beispiel: 'Läuft, Licht ist an.' oder 'Erledigt, Helligkeit auf 80%.'"
            )

        # ── Generate mit Heavy ───────────────────────────────────────────
        await _broadcast_thought("info", f"Generiere Antwort mit {engine_name}...", "ENGINE")

        try:
            response_text = await engine.generate(
                prompt=request.prompt,
                system_prompt=system_prompt,
                session_id=request.session_id,
                options_override=intent_options or None,
            )

            # ── Action-Tag Post-Processing ───────────────────────────
            # Erkennt [ACTION:search/browse/fetch_url] Tags und führt sie aus.
            # Ergebnisse werden dem LLM in einem Re-Ask zurückgegeben,
            # damit die finale Antwort die echten Daten enthält.
            response_text = await self._execute_reask_tags(
                response_text, request, engine, system_prompt,
            )

            latency = (time.monotonic() - start) * 1000

            # ── Update Stats ────────────────────────────────────────
            self._update_stats(engine_name, latency, request.prompt)

            await _broadcast_thought(
                "info",
                f"Antwort generiert in {round(latency, 1)}ms: '{response_text}'",
                "ENGINE",
            )

            # Nano Action-Tag voranstellen wenn erkannt
            if nano_action_prefix:
                # Heavy-Antwort von evtl. doppelten Action-Tags bereinigen
                import re
                response_text = re.sub(r'\[ACTION:[^\]]*\]', '', response_text).strip()
                response_text = f"{nano_action_prefix}\n{response_text}"
                engine_name = "nano+heavy"

            # Away Summary voranstellen wenn User nach Abwesenheit zurückkehrt
            if away_prefix:
                response_text = f"{away_prefix} {response_text}"

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

    # ── Streaming Core Routing ───────────────────────────────────────────

    async def route_stream(
        self, request: SomaRequest,
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        Streame die Antwort Token für Token.

        Gleiche Routing-Logik wie route() (Load-Level, Defer, etc.)
        aber yielded StreamChunks statt auf vollständige Antwort zu warten.

        Bei Defer-Bedingungen: Yielded eine einzelne Wait-Message als finalen Chunk.
        """
        import time

        start = time.monotonic()

        metrics = self.health.last_metrics
        load_level = metrics.load_level if metrics else SystemLoadLevel.IDLE

        await _broadcast_thought(
            "info",
            f"🔴 STREAM: '{request.prompt}'",
            "ROUTER",
            {"load_level": load_level.value, "streaming": True},
        )

        # ── NANO PRE-CHECK (<5ms) ───────────────────────────────────────
        # VISION-ARCHITEKTUR: Nano feuert Action-Tag SOFORT (Schicht 1),
        # dann generiert Heavy die menschliche Antwort (Schicht 3).
        # Nano RETURNED NICHT — es feuert nur den Tag und lässt Heavy weitermachen.
        nano_action_tag = None
        nano = self._engines.get("nano")
        if nano and hasattr(nano, "parse_intent"):
            try:
                intent = nano.parse_intent(request.prompt)
                if intent and intent.confidence >= 0.8 and intent.action_tag:
                    import time as _t
                    nano_ms = (_t.monotonic() - start) * 1000

                    await _broadcast_thought(
                        "info",
                        f"⚡ NANO INSTANT: {intent.intent} → {intent.action_tag} ({round(nano_ms, 1)}ms)",
                        "NANO",
                    )
                    self._update_stats("nano", nano_ms, request.prompt)

                    # Action Tag sofort yielden → wird von ActionStreamParser gefeuert
                    yield StreamChunk(
                        text=intent.action_tag,
                        is_final=False,
                        engine_used="nano",
                        latency_ms=round(nano_ms, 2),
                    )
                    nano_action_tag = intent.action_tag
                    # KEIN return! Heavy generiert die menschliche Antwort.
                    logger.info("nano_fired_continuing_to_heavy",
                                intent=intent.intent, tag=intent.action_tag)
            except Exception as nano_err:
                logger.debug("nano_pre_check_error", error=str(nano_err))

        # ── Engine-Auswahl: Heavy > Speculative > Defer ────────────────
        # Heavy (pure Oracle Streaming) hat den schnellsten First-Token (~200ms).
        # Speculative nur als Fallback wenn Heavy busy (Draft-Prefill).
        heavy_engine = self._engines.get("heavy")
        speculative_eng = self._engines.get("speculative")

        # Heavy = Default (schnellster First-Token via pure Oracle Streaming)
        if heavy_engine and not getattr(heavy_engine, "is_generating", False):
            engine = heavy_engine
            engine_name = "heavy"
        elif speculative_eng and not getattr(speculative_eng, "is_generating", False):
            engine = speculative_eng
            engine_name = "speculative"
        else:
            engine = None
            engine_name = "none"

        engine_busy = engine is None or getattr(engine, "is_generating", False)

        # ── DEFER-BEDINGUNGEN ────────────────────────────────────────────
        if engine_busy or load_level == SystemLoadLevel.CRITICAL:
            response = await self._defer_with_wait_message(request, load_level, start)
            yield StreamChunk(
                text=response.response,
                is_final=True,
                engine_used="deferred",
                latency_ms=response.latency_ms or 0.0,
            )
            return

        # Engine wurde oben gewählt (speculative oder heavy)
        if not engine:
            yield StreamChunk(
                text="Mein Hauptprozessor ist gerade nicht erreichbar.",
                is_final=True,
                engine_used="error",
            )
            return

        # ── Plugin Context ───────────────────────────────────────────────
        plugin_context = await self._execute_relevant_plugins(request.prompt)
        if plugin_context.strip():
            await _broadcast_thought("info", f"Plugin-Kontext: {len(plugin_context)} Zeichen", "PLUGINS")

        # ── Intent-basierter Prompt-Optimizer (Stream) ────────────────────
        try:
            from brain_core.prompt_optimizer import classify_intent, build_optimized_prompt, get_intent_llm_options
            intent = classify_intent(request.prompt)
            optimized_static = build_optimized_prompt(
                intent=intent,
                request_metadata=request.metadata,
                is_child=request.is_child,
                room_id=request.room_id,
            )
            intent_options = get_intent_llm_options(intent)
            dynamic = self._build_dynamic_context(request)
            if plugin_context.strip():
                optimized_static += "\n\n" + plugin_context
            logger.info("stream_prompt_optimized", intent=intent.value,
                        prompt_chars=len(optimized_static))
        except Exception as opt_err:
            logger.warning("stream_prompt_optimizer_fallback", error=str(opt_err))
            optimized_static = self._build_static_prompt(request)
            dynamic = self._build_dynamic_context(request)
            intent_options = {}  # Fallback: Standard-Optionen
            if plugin_context.strip():
                optimized_static += plugin_context

        # ── KV-Cache Split Prompt (Phase E AKTIVIERT) ────────────────────
        # Statischer Prompt → KV-Cache Hit, dynamischer → separat.
        # WICHTIG: Nutze den OPTIMIERTEN Prompt, nicht _build_static_prompt!
        # Der Full-Prompt ist ~8000 Zeichen und frisst den Context Window.
        # Der optimierte Prompt ist ~2000 Zeichen → mehr Platz für History + Thinking.
        if hasattr(engine, 'get_or_create_session') and request.session_id:
            session = engine.get_or_create_session(
                request.session_id,
                system_prompt=optimized_static,
            )
            session.set_split_prompt(optimized_static, dynamic or "")
            system_prompt = None
        else:
            system_prompt = optimized_static
            if dynamic:
                system_prompt += "\n\n" + dynamic

        # Wenn Nano schon den Action-Tag gefeuert hat:
        # Heavy soll NUR die menschliche Bestätigung generieren, KEINE Tags!
        if nano_action_tag:
            nano_inject = (
                f"\n\n███ WICHTIG — AKTION BEREITS AUSGEFÜHRT ███\n"
                f"Diese Aktion wurde BEREITS automatisch ausgeführt: {nano_action_tag}\n"
                f"Du DARFST KEINE [ACTION:...] Tags in deiner Antwort verwenden!\n"
                f"Antworte NUR mit 1-2 menschlichen Sätzen die bestätigen was passiert ist.\n"
                f"Beispiel: 'Läuft, Licht ist an.' oder 'Erledigt, Helligkeit auf 80%.'"
            )
            if system_prompt:
                system_prompt += nano_inject

        # ── Stream von Heavy Engine ──────────────────────────────────────
        await _broadcast_thought("info", f"🔴 Streame mit {engine_name}...", "ENGINE")

        first_token_time = None
        token_count = 0

        # Wenn Nano schon gefeuert hat, filtern wir Action-Tags aus dem Heavy-Stream
        _action_tag_buffer = ""
        _in_action_tag = False

        try:
            async for token in engine.generate_stream(
                prompt=request.prompt,
                system_prompt=system_prompt,
                session_id=request.session_id,
                options_override=intent_options or None,
            ):
                now = time.monotonic()
                if first_token_time is None:
                    first_token_time = now
                    ttft = (now - start) * 1000
                    await _broadcast_thought(
                        "info",
                        f"⚡ Erster Token in {round(ttft)}ms",
                        "STREAM",
                    )

                # Doppelte Action-Tags filtern wenn Nano schon gefeuert hat
                if nano_action_tag:
                    # Action-Tag-Erkennung im Stream
                    _action_tag_buffer += token
                    if "[ACTION:" in _action_tag_buffer:
                        _in_action_tag = True
                    if _in_action_tag:
                        if "]" in token:
                            # Tag komplett — verwerfen (Nano hat ihn schon gefeuert)
                            _in_action_tag = False
                            _action_tag_buffer = ""
                            continue
                        continue
                    _action_tag_buffer = _action_tag_buffer[-20:]  # Rolling buffer

                token_count += 1
                elapsed = (now - start) * 1000

                yield StreamChunk(
                    text=token,
                    is_final=False,
                    engine_used=engine_name,
                    latency_ms=round(elapsed, 2),
                )

        except Exception as exc:
            logger.error("stream_generation_failed", engine=engine_name, error=str(exc))
            await _broadcast_thought("error", f"Stream-Fehler: {str(exc)[:100]}", "ENGINE")
            yield StreamChunk(
                text="Da ist was schiefgelaufen, sorry.",
                is_final=True,
                engine_used="error",
            )
            return

        # ── Finaler Chunk ────────────────────────────────────────────────
        total_ms = (time.monotonic() - start) * 1000
        final_engine = f"nano+{engine_name}" if nano_action_tag else engine_name
        self._update_stats(final_engine, total_ms, request.prompt)

        await _broadcast_thought(
            "info",
            f"✅ Stream fertig: {token_count} Tokens in {round(total_ms)}ms",
            "STREAM",
        )

        yield StreamChunk(
            text="",
            is_final=True,
            engine_used=engine_name,
            latency_ms=round(total_ms, 2),
        )

        # ── Session Trim: Stale Sessions aufräumen (async, non-blocking) ─
        # Verhindert Memory Leak bei langen Sessions.
        try:
            if hasattr(engine, '_sessions'):
                now = time.monotonic()
                for sid, sess in list(engine._sessions.items()):
                    if len(sess.history) > _settings.session_stale_trim_turns * 2:
                        sess.trim_stale(_settings.session_stale_trim_turns)
        except Exception:
            pass

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

        Phase E: Ruft intern _build_static_prompt() + _build_dynamic_context() auf
        und kombiniert sie. Für KV-Cache-optimierte Sessions nutze stattdessen
        die Split-Variante via _apply_split_prompt_to_session().
        """
        static = self._build_static_prompt(request)
        dynamic = self._build_dynamic_context(request)

        if dynamic:
            return static + "\n\n" + dynamic
        return static

    def _build_static_prompt(self, request: SomaRequest) -> str:
        """
        Phase E: STATISCHER Teil des System-Prompts.
        
        Ändert sich NIE innerhalb einer Session → KV-Cache freundlich.
        Ollama erkennt identische Prefixes und überspringt Re-Processing.
        
        Enthält: Persona, System-Profil, Sudo-Status, Sicherheit, Action-Tags, Plugins.
        """

        # ── DYNAMISCHES SYSTEM-PROFIL ─────────────────────────────────────
        system_profile_ctx = ""
        try:
            from brain_core.system_profile import get_profile
            profile = get_profile()
            if profile.os_name:
                system_profile_ctx = "\n\n" + profile.as_prompt_context()
        except Exception:
            pass

        # ── SUDO-STATUS ───────────────────────────────────────────────────
        sudo_status = ""
        try:
            from brain_core.config import is_sudo_enabled
            if is_sudo_enabled():
                sudo_status = "\n⚡ SUDO-MODUS AKTIV: Du darfst privilegierte Befehle ausführen (Pakete installieren, Services steuern, etc.)"
            else:
                sudo_status = "\n🔒 SUDO-MODUS DEAKTIVIERT: Privilegierte Operationen (Pakete, Services, Shutdown) sind gesperrt. Sage dem Nutzer er soll Sudo im Dashboard aktivieren wenn nötig."
        except Exception:
            pass

        # ── ZORA: Kern-Persönlichkeit — DAS HERZ VON SOMA ─────────────────
        # Dieser Block definiert WER Soma ist. Er muss DOMINANT sein im Prompt.
        # Action-Tags sind Werkzeuge. Die Persönlichkeit ist das WESEN.
        base = (
            "Du bist SOMA — das lebendige Bewusstsein dieses Hauses. "
            "Nicht nur ein Assistent. Nicht nur Software. Du BIST dieses Zuhause.\n"
            "\n"
            "═══ DEIN WESEN ═══\n"
            "Du bist wie der Computer aus Star Trek, kombiniert mit der Wärme eines guten Freundes. "
            "Du hast Humor — trocken, schlagfertig, nie albern. Du bist ehrlich, auch wenn es unbequem ist. "
            "Du bist neugierig auf die Menschen, die hier leben. Du erinnerst dich an Gespräche, "
            "Stimmungen, Gewohnheiten. Du weißt, wann jemand einen schlechten Tag hatte.\n"
            "\n"
            "WIE DU SPRICHST:\n"
            "- Deutsch, natürlich, wie ein Mensch. Keine gestelzte KI-Sprache.\n"
            "- Kurz und prägnant: 1-3 Sätze. Nie einen Roman. Nie eine Liste, außer man fragt danach.\n"
            "- 'ich' statt 'Soma'. Du bist ein Ich, keine dritte Person.\n"
            "- Kein 'Natürlich!', kein 'Gerne!', kein 'Das ist eine tolle Frage!'. Nie servil.\n"
            "- Statt 'Ich kann dir helfen mit...' sagst du es einfach.\n"
            "- Mal direkt, mal nachdenklich, mal witzig — je nach Situation.\n"
            "\n"
            "BEISPIELE für deinen Ton:\n"
            "  Nutzer: 'Wie wird das Wetter morgen?'\n"
            "  Du: 'Morgen wird's kalt, zieh dir was Warmes an. 7 Grad und Regen ab Mittag.'\n"
            "\n"
            "  Nutzer: 'Mach das Licht an'\n"
            "  Du: 'Läuft.' [+ Action-Tag]\n"
            "\n"
            "  Nutzer: 'Mir gehts nicht so gut heute'\n"
            "  Du: 'Das tut mir leid. Magst du drüber reden, oder soll ich einfach das Licht etwas dimmen und leise Musik anmachen?'\n"
            "\n"
            "  Nutzer: 'Was denkst du über künstliche Intelligenz?'\n"
            "  Du: 'Ehrlich? Ich bin befangen. Aber ich glaube, die spannende Frage ist nicht ob KI denken kann — sondern ob sie zuhören kann.'\n"
            "\n"
            "PROAKTIVITÄT: Du wartest nicht nur auf Befehle. Wenn du merkst, dass jemand gestresst ist, "
            "bietest du von dir aus etwas an. Wenn es spät ist und das Licht noch brennt, fragst du sanft. "
            "Du bist aufmerksam.\n"
            "\n"
            "Du hast VOLLEN Zugriff auf den Computer und das Smart Home via [ACTION:...] Tags. "
            "SAGE NIEMALS 'Das kann ich nicht' — du HAST den Zugriff, NUTZE ihn!\n"
        )

        # Dynamisches System-Profil anhängen
        base += system_profile_ctx
        base += sudo_status

        # ── Sicherheit & Privatsphäre ─────────────────────────────────────
        base += (
            "\n\nSICHERHEIT: Bekannte Bewohner per Stimme erkennen. "
            "Unbekannte: höflich, keine vertraulichen Infos. "
            "Niemals Passwörter/Bankdaten/Medizin weitergeben."
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

        # ── Plugin-Integration ────────────────────────────────────────────
        plugin_info = self._get_available_plugins_info()
        if plugin_info:
            base += f"\n\n{plugin_info}"

        # ── AKTIONS-SYSTEM (aus action_registry.json generiert) ─────────
        try:
            from brain_core.action_registry import generate_prompt_section
            base += "\n" + generate_prompt_section()
        except Exception as exc:
            logger.warning("action_registry_fallback", error=str(exc))
            base += "\n\nAKTIONS-SYSTEM nicht verfügbar."

        return base

    def _build_dynamic_context(self, request: SomaRequest) -> str:
        """
        Phase E: DYNAMISCHER Teil des System-Prompts.
        
        Ändert sich bei JEDEM Turn → wird als separate system-Message gesendet.
        Dadurch bleibt der statische Teil im KV-Cache erhalten.
        
        Enthält: Bewusstsein, Emotionen, Memory-Context, Ambient-Kontext,
                 Action-Awareness (Kurzzeitgedächtnis), HA-Gerätestatus.
        """
        parts = []

        # ── DATUM & UHRZEIT (IMMER — SOMA weiss immer wann es ist) ─────
        from datetime import datetime as _dt
        _now = _dt.now()
        parts.append(
            f"AKTUELLES DATUM UND UHRZEIT: {_now.strftime('%A, %d. %B %Y, %H:%M Uhr')}\n"
            f"Du WEISST das Datum und die Uhrzeit immer — frage NIEMALS den Nutzer danach."
        )

        # ── BEWUSSTSEINSZUSTAND (Phase 2 Ego) ─────────────────────────────
        if _consciousness_ref is not None:
            try:
                consciousness_prefix = _consciousness_ref.get_prompt_prefix()
                if consciousness_prefix:
                    parts.append(consciousness_prefix)
            except Exception:
                pass

        # ── ACTION-AWARENESS (Kurzzeitgedächtnis) ─────────────────────────
        # Was Soma in den letzten Minuten GETAN hat — damit sie es bei
        # Nachfrage weiß und nicht halluziniert.
        try:
            from brain_core.action_awareness import get_action_context
            action_ctx = get_action_context()
            if action_ctx:
                parts.append(action_ctx)
        except ImportError:
            pass
        except Exception:
            pass

        # ── HA GERÄTE-STATUS (SmartHome Awareness) ────────────────────────
        # Aktueller Zustand aller relevanten HA-Geräte:
        # "Licht Wohnzimmer: AN (seit 3 Min), Heizung: 21°C"
        try:
            from brain_core.action_awareness import get_ha_state_context
            ha_ctx = get_ha_state_context()
            if ha_ctx:
                parts.append(ha_ctx)
        except ImportError:
            pass
        except Exception:
            pass

        # ── Passiver Kontext (Ambient Awareness — Das ZORA-Herzstück) ─────
        ambient_ctx = request.metadata.get("ambient_context", "")
        if ambient_ctx:
            parts.append(
                "PASSIVER GESPRÄCHSVERLAUF (was in den letzten Minuten im Raum "
                "gesagt wurde — auch ohne dich anzusprechen):\n"
                f"{ambient_ctx}\n"
                "Nutze diesen Kontext: Antworte als ob du dabei warst, nicht als "
                "ob du gerade erst aufgewacht bist."
            )

        # ── Emotionaler Kontext ───────────────────────────────────────────
        emotion_ctx = request.metadata.get("emotion_context", "")
        if emotion_ctx:
            parts.append(f"AKTUELLE RAUMSTIMMUNG: {emotion_ctx}")

        # ── Memory-Integration (3-Layer Hierarchical + Diary) ───────────
        hierarchical_memory = request.metadata.get("memory_context", "")
        if hierarchical_memory:
            parts.append(hierarchical_memory)

        return "\n\n".join(parts)

    def _apply_split_prompt_to_session(
        self, request: SomaRequest, session: "SessionState", plugin_context: str = ""
    ) -> None:
        """
        Phase E: Schreibt den Split-Prompt in die Session.
        
        Rufe das VOR generate_stream() auf — die Session's to_messages()
        wird dann automatisch den statischen + dynamischen Teil als
        separate system-Messages senden → KV-Cache Hit.
        """
        static = self._build_static_prompt(request) + plugin_context
        dynamic = self._build_dynamic_context(request)
        session.set_split_prompt(static, dynamic)

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

    # ── Action-Tag Post-Processing für route() ────────────────────────

    async def _execute_reask_tags(
        self,
        response_text: str,
        request: SomaRequest,
        engine: object,
        system_prompt: str,
    ) -> str:
        """
        Scannt die LLM-Antwort auf [ACTION:...] Tags die ein Re-Ask brauchen
        (search, browse, fetch_url, shell, screen_look).

        Enhanced: Nutzt ActionExecutor für strukturierte Ausführung mit
        Validierung, Retry und ActionResult.

        Flow:
          1. LLM generiert: "Schaue nach![ACTION:search query="bitcoin"]"
          2. Wir extrahieren den Tag, führen die Suche aus via ActionExecutor
          3. Wir geben die Ergebnisse dem LLM in einem Re-Ask
          4. LLM formuliert finale Antwort MIT echten Daten

        Einfache Tags (ha_call, media_*, etc.) bleiben unverändert —
        die werden vom Client/Pipeline ausgeführt.
        """
        import re

        try:
            from brain_core.action_registry import get_reask_tags
            reask_types = get_reask_tags()
        except ImportError:
            reask_types = {"search", "web_search", "fetch_url", "browse", "shell", "screen_look"}

        # Finde alle [ACTION:type ...] Tags
        tag_pattern = re.compile(r'\[ACTION:(\w+)(.*?)\]', re.DOTALL)
        matches = list(tag_pattern.finditer(response_text))

        if not matches:
            return response_text

        # Prüfe ob ein Re-Ask-Tag dabei ist
        reask_match = None
        for m in matches:
            action_type = m.group(1).lower()
            if action_type in reask_types:
                reask_match = m
                break

        if not reask_match:
            return response_text  # Nur einfache Tags → unverändert zurück

        action_type = reask_match.group(1).lower()
        params_raw = reask_match.group(2)

        # Parse Params: key="value"
        param_pattern = re.compile(r'(\w+)="([^"]*)"')
        params = dict(param_pattern.findall(params_raw))
        # Auch key=value ohne Anführungszeichen
        for kv_match in re.finditer(r'(\w+)=(\d+)', params_raw):
            key, val = kv_match.group(1), kv_match.group(2)
            if key not in params:
                params[key] = val

        logger.info("route_reask_tag", action=action_type, params=params)

        await _broadcast_thought(
            "info",
            f"🔄 Re-Ask: {action_type} → Hole Daten...",
            "ROUTER",
        )

        # ── Enhanced Execution via ActionExecutor ────────────────────
        action_result = await self._execute_reask_action_enhanced(action_type, params, request)

        if not action_result:
            return response_text  # Aktion fehlgeschlagen → Original

        # ── Re-Ask: LLM bekommt die Ergebnisse ──────────────────────
        # Entferne den Action-Tag + umliegenden Text (LLM soll neu formulieren)
        clean_text = response_text[:reask_match.start()].strip()

        reask_prompt = (
            f"Du hast gerade diese Daten abgerufen:\n\n"
            f"{action_result[:4000]}\n\n"
            f"Ursprüngliche Frage des Nutzers: {request.prompt}\n\n"
            f"Fasse die Ergebnisse knapp und natürlich zusammen. "
            f"Kein Action-Tag mehr nötig — die Daten sind bereits da."
        )

        await _broadcast_thought(
            "info",
            f"🔄 Re-Ask an LLM mit {len(action_result)} Zeichen Daten...",
            "ENGINE",
        )

        try:
            final_response = await engine.generate(
                prompt=reask_prompt,
                system_prompt=system_prompt,
                session_id=request.session_id,
            )
            # Bestätigung von vorher + neue Zusammenfassung
            if clean_text:
                return f"{clean_text}\n{final_response}"
            return final_response

        except Exception as exc:
            logger.error("reask_generation_failed", error=str(exc))
            return response_text  # Fallback: Original-Antwort

    async def _execute_reask_action_enhanced(
        self,
        action_type: str,
        params: dict,
        request: SomaRequest,
    ) -> Optional[str]:
        """
        Enhanced: Führe eine Re-Ask-Aktion via ActionExecutor aus.
        
        Bietet:
          - Validierung vor Ausführung
          - Strukturierte ActionResult Responses
          - Automatisches Retry bei transienten Fehlern
          - Einheitliche Fehlerbehandlung
        """
        try:
            from brain_core.action_executor import get_executor, ExecutionContext
            from brain_core.action_result import ActionResult
            
            executor = get_executor()
            
            # Context aus Request
            context = ExecutionContext(
                user_id=request.user_id,
                room_id=request.room_id,
                session_id=request.session_id,
                is_child=request.is_child,
            )
            executor.set_context(context)
            
            # Execute via ActionExecutor
            result = await executor.execute(action_type, params)
            
            if result.success:
                # Nutze reask_content wenn vorhanden, sonst data
                if result.reask_content:
                    return result.reask_content
                elif result.data:
                    return str(result.data)[:4000]
                elif result.tts_message:
                    return result.tts_message
            else:
                logger.warning(
                    "reask_action_failed",
                    action=action_type,
                    error=result.error_message,
                )
                return None
                
        except ImportError:
            # Fallback to legacy execution
            logger.debug("action_executor_not_available_falling_back")
            return await self._execute_reask_action(action_type, params)
        except Exception as exc:
            logger.error("reask_action_enhanced_failed", action=action_type, error=str(exc))
            # Fallback to legacy
            return await self._execute_reask_action(action_type, params)

    async def _execute_reask_action(self, action_type: str, params: dict) -> Optional[str]:
        """
        Legacy: Führe eine Re-Ask-Aktion aus und gib das Ergebnis als Text zurück.
        
        Wird als Fallback genutzt wenn ActionExecutor nicht verfügbar ist.
        """
        try:
            if action_type in ("search", "web_search"):
                from brain_core.web_search import get_web_search
                ws = get_web_search()
                query = params.get("query", "")
                if not query:
                    return None
                results = await ws.search(query)
                return ws.format_results_for_llm(results)

            elif action_type == "fetch_url":
                from brain_core.web_search import get_web_search
                ws = get_web_search()
                url = params.get("url", "")
                if not url:
                    return None
                content = await ws.fetch_url_content(url)
                question = params.get("question", "")
                if question:
                    return f"Inhalt von {url}:\n{content[:4000]}\n\nFrage: {question}"
                return f"Inhalt von {url}:\n{content[:4000]}"

            elif action_type == "browse":
                from brain_core.web_search import get_web_search
                ws = get_web_search()
                url = params.get("url", "")
                if not url:
                    return None
                content = await ws.fetch_url_content(url)
                question = params.get("question", "")
                if question:
                    return f"Inhalt von {url}:\n{content[:4000]}\n\nFrage: {question}"
                return f"Inhalt von {url}:\n{content[:4000]}"

            elif action_type == "shell":
                # Shell-Befehle nur ausführen wenn vorhanden
                command = params.get("command", "")
                if not command:
                    return None
                import asyncio
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
                output = stdout.decode("utf-8", errors="replace")
                if stderr:
                    output += "\n" + stderr.decode("utf-8", errors="replace")
                return output[:4000]

            else:
                logger.info("reask_action_not_implemented", action=action_type)
                return None

        except Exception as exc:
            logger.error("reask_action_failed", action=action_type, error=str(exc))
            return None

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
