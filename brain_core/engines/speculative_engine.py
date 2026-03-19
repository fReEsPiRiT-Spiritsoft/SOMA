"""
SOMA-AI Speculative Engine — Draft-Prefill Architektur
========================================================
Ersetzt die alte Round-Trip Verifikation durch Draft-Prefill Streaming.

Prinzip (DLSS-inspiriert, aber OHNE Round-Trip-Overhead):
  1. Draft-Modell (Qwen3:1.7b) generiert einen Komplettentwurf
     (~200-400ms fuer 30-50 Tokens = SCHNELL)
  2. Entwurf wird als Kontext-Hint an Oracle uebergeben
  3. Oracle streamt die finale Antwort, beeinflusst vom Draft-Hint
  4. Erster Oracle-Token kommt nach ~300-600ms (Draft + Oracle Start)

WANN BENUTZT:
  - NUR als Fallback wenn Heavy Engine beschaeftigt ist
  - Heavy (pure Oracle Streaming) ist DEFAULT (schnellster First-Token)
  - Speculative = Backup fuer gleichzeitige Anfragen

VRAM: Draft ~1.2GB (permanent) + Oracle ~4.7GB (5min idle) = 5.9GB / 12GB
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Optional, AsyncGenerator

import httpx
import structlog

from brain_core.engines.base_engine import BaseEngine
from brain_core.config import settings
from shared.resilience import SomaCircuitBreaker, SomaRetryLogic

logger = structlog.get_logger("soma.engine.speculative")


class SpeculativeEngine(BaseEngine):
    """
    Draft-Prefill Speculative Engine.

    Nutzt Draft als schnellen Gedanken-Generator und Oracle als Korrektor.
    Pure Oracle Streaming ist die DEFAULT-Route (Heavy Engine).
    Diese Engine dient als Fallback bei parallelen Anfragen.
    """

    def __init__(self):
        super().__init__(name="speculative")
        self._client: Optional[httpx.AsyncClient] = None
        self._cb = SomaCircuitBreaker(
            name="ollama-speculative", failure_threshold=3, recovery_timeout=30.0
        )
        self._retry = SomaRetryLogic(max_retries=1, base_delay=0.5)
        self._oracle_model = settings.ollama_heavy_model
        self._draft_model = settings.ollama_light_model
        self._is_generating: bool = False

        # Statistiken
        self._draft_used_count = 0
        self._draft_fallback_count = 0
        self._total_draft_ms = 0.0

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.ollama_url,
            timeout=120.0,
        )
        logger.info(
            "speculative_init",
            oracle=self._oracle_model,
            draft=self._draft_model,
            strategy="draft-prefill",
        )
        # Warm-up: Beide Modelle in VRAM laden
        asyncio.create_task(self._warmup_models())

    async def _warmup_models(self) -> None:
        """Lade beide Modelle in VRAM bei Engine-Start."""
        try:
            for model, keep_alive in [
                (self._draft_model, settings.ollama_light_keep_alive),
                (self._oracle_model, settings.ollama_heavy_keep_alive),
            ]:
                await self._client.post(
                    "/api/generate",
                    json={"model": model, "prompt": "hi", "keep_alive": keep_alive},
                    timeout=60.0,
                )
                logger.info("speculative_warmup", model=model, keep_alive=keep_alive)
        except Exception as e:
            logger.warning("speculative_warmup_failed", error=str(e))

    async def shutdown(self) -> None:
        total = self._draft_used_count + self._draft_fallback_count
        if total > 0:
            avg_draft = self._total_draft_ms / max(self._draft_used_count, 1)
            logger.info(
                "speculative_stats",
                total_requests=total,
                draft_used=self._draft_used_count,
                draft_fallback=self._draft_fallback_count,
                avg_draft_ms=round(avg_draft),
            )
        if self._client:
            await self._client.aclose()

    @property
    def is_generating(self) -> bool:
        return self._is_generating

    # ── Hauptmethoden ────────────────────────────────────────────────────

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
        options_override: Optional[dict] = None,
    ) -> str:
        """Non-streaming: sammelt alle Tokens."""
        chunks = []
        async for token in self.generate_stream(
            prompt, system_prompt, session_id, options_override
        ):
            chunks.append(token)
        return "".join(chunks)

    async def generate_stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
        options_override: Optional[dict] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Draft-Prefill Streaming.

        Fuer EINFACHE Queries: Pure Oracle Streaming (schnellster First-Token).
        Fuer KOMPLEXE Queries: Draft generiert Kontext-Hint -> Oracle nutzt ihn.
        """
        if not self._client:
            raise RuntimeError("SpeculativeEngine nicht initialisiert")

        self._is_generating = True
        start = time.monotonic()

        # Session-Kontext aufbauen
        messages = self._build_messages(prompt, system_prompt, session_id)

        oracle_options = {
            "num_ctx": 8192,
            "temperature": 0.4,
            "top_p": 0.85,
            "repeat_penalty": 1.15,
        }
        if options_override:
            oracle_options.update(options_override)

        # Qwen3 Thinking-Mode Steuerung
        use_thinking = self._should_use_thinking(prompt)

        full_response = ""

        try:
            # Entscheide: Einfach -> pure Oracle, Komplex -> Draft-Prefill
            is_simple = len(prompt.split()) <= 12 or not use_thinking

            if is_simple:
                # Pure Oracle Streaming (fastest first-token)
                self._draft_fallback_count += 1
                async for token in self._oracle_stream(
                    messages, oracle_options, use_thinking
                ):
                    full_response += token
                    yield token
            else:
                # Draft-Prefill fuer komplexe Queries
                draft_start = time.monotonic()
                draft_text = await self._draft_generate(messages)
                draft_ms = (time.monotonic() - draft_start) * 1000

                if draft_text:
                    self._draft_used_count += 1
                    self._total_draft_ms += draft_ms
                    logger.debug(
                        "speculative_draft_ready",
                        draft_len=len(draft_text),
                        draft_ms=round(draft_ms),
                    )

                    # Draft als Kontext-Hint in Messages injizieren
                    hint_messages = list(messages)
                    # Fuege Draft als Vorentwurf-Kontext hinzu
                    hint_messages.insert(-1, {
                        "role": "system",
                        "content": (
                            "[Dein Vorentwurf — verbessere/korrigiere wenn noetig, "
                            "antworte direkt ohne Meta-Kommentare]: " + draft_text
                        ),
                    })

                    async for token in self._oracle_stream(
                        hint_messages, oracle_options, use_thinking
                    ):
                        full_response += token
                        yield token
                else:
                    # Draft fehlgeschlagen -> pure Oracle
                    self._draft_fallback_count += 1
                    async for token in self._oracle_stream(
                        messages, oracle_options, use_thinking
                    ):
                        full_response += token
                        yield token

        except Exception as e:
            logger.error("speculative_error", error=str(e))
            # Letzter Fallback: Oracle ohne alles
            if not full_response:
                try:
                    async for token in self._oracle_stream(
                        messages, oracle_options, False
                    ):
                        full_response += token
                        yield token
                except Exception as e2:
                    logger.error("speculative_total_failure", error=str(e2))
                    yield "Entschuldige, mein Sprachzentrum hat gerade einen Aussetzer."
        finally:
            self._is_generating = False

        # Session updaten
        if session_id and full_response:
            session = self._sessions.get(session_id)
            if session:
                session.add_turn("assistant", full_response)

        total_ms = (time.monotonic() - start) * 1000
        logger.info(
            "speculative_complete",
            total_ms=round(total_ms),
            response_len=len(full_response),
        )

    # ── Draft Model: Schneller Entwurf ───────────────────────────────────

    async def _draft_generate(self, messages: list[dict]) -> str:
        """Generiere Komplettentwurf mit Draft-Modell (~200-400ms)."""
        draft_options = {
            "num_ctx": 2048,
            "temperature": 0.4,
            "top_p": 0.85,
        }
        try:
            payload = {
                "model": self._draft_model,
                "messages": messages,
                "stream": False,
                "keep_alive": settings.ollama_light_keep_alive,
                "think": False,
                "options": {
                    **draft_options,
                    "num_predict": 100,
                },
            }
            resp = await self._client.post(
                "/api/chat", json=payload, timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            draft = data.get("message", {}).get("content", "")
            return draft.strip() if draft else ""
        except Exception as e:
            logger.debug("draft_generate_failed", error=str(e))
            return ""

    # ── Oracle: Streaming ────────────────────────────────────────────────

    async def _oracle_stream(
        self,
        messages: list[dict],
        options: dict,
        use_thinking: bool = False,
    ) -> AsyncGenerator[str, None]:
        """Oracle streamt die finale Antwort."""
        payload = {
            "model": self._oracle_model,
            "messages": messages,
            "stream": True,
            "keep_alive": settings.ollama_heavy_keep_alive,
            "options": options,
        }
        if not use_thinking:
            payload["think"] = False

        try:
            async with self._client.stream(
                "POST", "/api/chat", json=payload, timeout=120.0,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("done"):
                        break
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        yield token
        except Exception as e:
            logger.error("oracle_stream_failed", error=str(e))

    # ── Hilfsmethoden ────────────────────────────────────────────────────

    def _build_messages(
        self,
        prompt: str,
        system_prompt: Optional[str],
        session_id: Optional[str],
    ) -> list[dict]:
        """Baue Messages-Array aus Session-History."""
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
        return messages

    @staticmethod
    def _should_use_thinking(prompt: str) -> bool:
        """Bestimme ob Qwen3 Thinking Mode aktiv sein soll."""
        p = prompt.lower()

        # Kein Thinking fuer:
        if any(marker in p for marker in [
            "kein [action:", "kein action", "fasse die wichtigsten",
            "fasse das ergebnis", "basierend auf dem seiteninhalt",
            "du hast gerade", "zusammen —",
            "korrigiere und verbessere",
        ]):
            return False

        smart_home_words = [
            "licht", "lampe", "heizung", "temperatur", "an ", " aus",
            "heller", "dunkler", "waermer", "kaelter", "steckdose",
            "rolladen", "jalousie", "musik", "pause", "stop", "leiser", "lauter",
        ]
        if len(p.split()) <= 10 and any(w in p for w in smart_home_words):
            return False

        short_phrases = [
            "hallo", "hi ", "hey ", "guten morgen", "gute nacht",
            "danke", "tschuess", "wie geht", "alles klar",
        ]
        if any(p.startswith(sp) or p == sp.strip() for sp in short_phrases):
            return False

        if any(w in p for w in ["erinner", "timer", "weck", "merke", "merken"]):
            return False

        return True

    async def health_check(self) -> bool:
        """Pruefe ob Oracle-Modell erreichbar ist."""
        if not self._client:
            return False
        try:
            resp = await self._client.get("/api/tags")
            if resp.status_code != 200:
                return False
            models = resp.json().get("models", [])
            model_names = [m.get("name", "") for m in models]
            return self._oracle_model in model_names
        except Exception:
            return False
