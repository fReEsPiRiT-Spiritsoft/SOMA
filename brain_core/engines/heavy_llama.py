"""
SOMA-AI Heavy Engine – Llama 3 (8B) via Ollama
================================================
Deep Reasoner: Volle Sprachpower für komplexe Gespräche.
Wird bei IDLE/NORMAL Load genutzt.

Datenfluss:
  LogicRouter ──► HeavyLlamaEngine.generate(prompt)
                       │
                       ├─ Session-History aufbauen
                       ├─ Ollama API Call (Circuit Breaker geschützt)
                       └─ Response + Session-Update
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

import httpx
import structlog

from brain_core.engines.base_engine import BaseEngine
from brain_core.config import settings
from shared.resilience import SomaCircuitBreaker, SomaRetryLogic

logger = structlog.get_logger("soma.engine.heavy")


class HeavyLlamaEngine(BaseEngine):
    """
    Ollama/Llama 3 8B Engine.
    Maximale Antwortqualität, höchster Ressourcenverbrauch.

    VRAM-Strategie:
      – Modell bleibt bis zu vram_unload_idle_secs (default 10s) nach dem
        letzten Request im VRAM geladen.
      – Danach wird Ollama angewiesen das Modell zu entladen (keep_alive=0).
      – Beim nächsten Request lädt Ollama es automatisch nach.
      – Liegt VRAM-Auslastung > heavy_engine_max_vram_pct (default 90%),
        wird das Modell sofort entladen und der LogicRouter fällt auf die
        Light-Engine zurück.
    """

    def __init__(self):
        super().__init__(name="heavy")
        self._client: Optional[httpx.AsyncClient] = None
        self._cb = SomaCircuitBreaker(
            name="ollama-heavy",
            failure_threshold=3,
            recovery_timeout=30.0,
        )
        self._retry = SomaRetryLogic(max_retries=2, base_delay=1.0)
        self._model = settings.ollama_heavy_model
        self._last_request_time: float = 0.0
        self._model_loaded: bool = False
        self._idle_unload_task: Optional[asyncio.Task] = None
        self._is_generating: bool = False

    async def initialize(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.ollama_url,
            timeout=120.0,  # LLM-Calls können dauern
        )
        logger.info("heavy_engine_init", model=self._model, url=settings.ollama_url)
        # Idle-Wächter starten
        if settings.vram_unload_idle_secs > 0:
            self._idle_unload_task = asyncio.create_task(
                self._idle_unload_loop(), name="heavy-vram-idle-watcher"
            )

    async def shutdown(self) -> None:
        if self._idle_unload_task:
            self._idle_unload_task.cancel()
            try:
                await self._idle_unload_task
            except asyncio.CancelledError:
                pass
        await self._unload_model()  # VRAM beim Shutdown immer freigeben
        if self._client:
            await self._client.aclose()

    # ── VRAM Management ─────────────────────────────────────────────────

    async def _unload_model(self) -> None:
        """Teilt Ollama mit, das Modell sofort aus dem VRAM zu entladen."""
        if not self._client or not self._model_loaded:
            return
        try:
            await self._client.post(
                "/api/generate",
                json={"model": self._model, "keep_alive": 0},
                timeout=10.0,
            )
            self._model_loaded = False
            logger.info("heavy_vram_unloaded", model=self._model)
        except Exception as exc:
            logger.warning("heavy_vram_unload_failed", error=str(exc))

    async def _idle_unload_loop(self) -> None:
        """Hintergrund-Task: Entlädt Modell nach N Sekunden Inaktivität."""
        idle_secs = settings.vram_unload_idle_secs
        while True:
            await asyncio.sleep(2.0)  # alle 2s prüfen
            if not self._model_loaded:
                continue
            idle = time.monotonic() - self._last_request_time
            if idle >= idle_secs:
                logger.info(
                    "heavy_vram_idle_unload",
                    idle_seconds=round(idle, 1),
                    threshold=idle_secs,
                )
                await self._unload_model()

    @property
    def is_generating(self) -> bool:
        """True wenn gerade eine Anfrage verarbeitet wird.
        Monolog & Ambient pausieren solange um VRAM/Compute freizuhalten."""
        return self._is_generating

    def notify_vram_pressure(self) -> None:
        """Vom LogicRouter/HealthMonitor aufgerufen wenn VRAM > 90%.
        Entlädt sofort — asynchron via create_task."""
        if self._model_loaded:
            logger.warning("heavy_vram_pressure_unload", reason="VRAM > 90%")
            asyncio.create_task(self._unload_model())

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
        options_override: Optional[dict] = None,
    ) -> str:
        """Generiere Antwort via Ollama Chat API.

        Args:
            options_override: Überschreibt Ollama-Options (z.B. temperature=0.1 für Code).
        """
        if not self._client:
            raise RuntimeError("HeavyEngine nicht initialisiert")

        # Aktivitätsstempel → Idle-Timer zurücksetzen
        self._last_request_time = time.monotonic()
        self._model_loaded = True

        # Session-Kontext
        messages = []
        if session_id:
            session = self.get_or_create_session(
                session_id, system_prompt=system_prompt or ""
            )
            session.add_turn("user", prompt)
            messages = session.to_messages(system_prompt)
        else:
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

        # Basis-Options + optionaler Override (z.B. temperature=0.1 für Code-Generierung)
        ollama_options = {
            "num_ctx": 16384,
            "temperature": 0.4,
            "top_p": 0.85,
            "repeat_penalty": 1.15,
        }
        if options_override:
            ollama_options.update(options_override)

        # ── Qwen3 Thinking-Mode Steuerung ─────────────────────────────
        # Thinking ist mächtig aber langsam (~5x). Nur bei komplexen Fragen aktivieren.
        # Einfache Befehle (Licht, Suche, Erinnerung) → kein Thinking → 6x schneller.
        use_thinking = self._should_use_thinking(prompt)

        # Ollama API Call
        async def _call() -> str:
            payload = {
                "model": self._model,
                "messages": messages,
                "stream": False,
                "options": ollama_options,
            }
            # Qwen3: think=false deaktiviert den internen Reasoning-Modus
            if not use_thinking:
                payload["think"] = False
            resp = await self._client.post("/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["message"]["content"]

        self._is_generating = True
        try:
            response = await self._cb.call(self._retry.execute, _call)
        finally:
            self._is_generating = False

        # Session updaten
        if session_id:
            session = self._sessions.get(session_id)
            if session:
                session.add_turn("assistant", response)

        logger.info(
            "heavy_generated",
            model=self._model,
            prompt_len=len(prompt),
            response_len=len(response),
            thinking=use_thinking,
        )

        return response

    @staticmethod
    def _should_use_thinking(prompt: str) -> bool:
        """
        Entscheide ob Qwen3 Thinking-Mode aktiv sein soll.
        
        Thinking AN (langsamer, ~5x, aber klüger):
          - Komplexe Fragen, Erklärungen, Diskussionen
          - Kreative Aufgaben, Planung
          - Code-Generierung, Plugin-Erstellung
        
        Thinking AUS (schnell, ~0.5-1s):
          - Smart Home Befehle (Licht, Heizung, etc.)
          - Einfache Suchen, Erinnerungen
          - Kurze Antworten, Smalltalk
          - Re-Ask Sessions (search_reask, fetch_reask, etc.)
        """
        p = prompt.lower()
        
        # Re-Ask Sessions → kein Thinking (schon recherchiert, nur zusammenfassen)
        if any(marker in p for marker in [
            "kein [action:", "kein action", "fasse die wichtigsten",
            "fasse das ergebnis", "basierend auf dem seiteninhalt",
            "du hast gerade", "zusammen —",
        ]):
            return False
        
        # Smart Home Befehle → kein Thinking
        smart_home_words = [
            "licht", "lampe", "heizung", "temperatur", "an ", " aus",
            "heller", "dunkler", "wärmer", "kälter", "steckdose",
            "rolladen", "jalousie", "musik", "pause", "stop", "leiser", "lauter",
        ]
        if len(p.split()) <= 8 and any(w in p for w in smart_home_words):
            return False
        
        # Einfache Grüße / Smalltalk → kein Thinking
        short_phrases = [
            "hallo", "hi ", "hey ", "guten morgen", "gute nacht",
            "danke", "tschüss", "wie geht", "alles klar",
        ]
        if any(p.startswith(sp) or p == sp.strip() for sp in short_phrases):
            return False
        
        # Erinnerungen, Merken → kein Thinking
        if any(w in p for w in ["erinner", "timer", "weck", "merke", "merken"]):
            return False
        
        # Alles andere → Thinking AN für maximale Qualität
        return True

    async def health_check(self) -> bool:
        """Prüfe ob Ollama erreichbar und Model geladen."""
        if not self._client:
            return False
        try:
            resp = await self._client.get("/api/tags")
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                model_names = [m.get("name", "") for m in models]
                return self._model in model_names
            return False
        except Exception:
            return False
