"""
SOMA-AI Light Engine – Qwen3 1.7B via Ollama (Draft Model)
============================================================
Balanced Mode: Gute Qualität bei reduziertem Ressourcenverbrauch.
Wird bei ELEVATED Load genutzt + für Memory-Tasks (Diary, Consolidation).
VRAM: ~1.2GB Q4 — bleibt permanent im VRAM (keep_alive=-1).
"""

from __future__ import annotations

from typing import Optional, AsyncGenerator

import httpx
import structlog

from brain_core.engines.base_engine import BaseEngine
from brain_core.config import settings
from shared.resilience import SomaCircuitBreaker, SomaRetryLogic

logger = structlog.get_logger("soma.engine.light")


class LightPhiEngine(BaseEngine):
    """
    Leichteres Modell für Situationen mit erhöhter Last.
    Gleiche API wie HeavyEngine, aber schneller.
    """

    def __init__(self):
        super().__init__(name="light")
        self._client: Optional[httpx.AsyncClient] = None
        self._cb = SomaCircuitBreaker(
            name="ollama-light",
            failure_threshold=3,
            recovery_timeout=20.0,
        )
        self._retry = SomaRetryLogic(max_retries=2, base_delay=0.5)
        self._model = settings.ollama_light_model

    async def initialize(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.ollama_url,
            timeout=60.0,
        )
        logger.info("light_engine_init", model=self._model)

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        if not self._client:
            raise RuntimeError("LightEngine nicht initialisiert")

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

        async def _call() -> str:
            resp = await self._client.post(
                "/api/chat",
                json={
                    "model": self._model,
                    "messages": messages,
                    "stream": False,
                    "think": False,  # Qwen3 1.7B Thinking-Mode AUS — zu klein für gutes Reasoning
                    "keep_alive": settings.ollama_light_keep_alive,
                    "options": {
                        "num_ctx": 2048,
                        "temperature": 0.7,
                    },
                },
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"]

        response = await self._cb.call(self._retry.execute, _call)

        if session_id:
            session = self._sessions.get(session_id)
            if session:
                session.add_turn("assistant", response)

        logger.info(
            "light_generated",
            model=self._model,
            prompt_len=len(prompt),
            response_len=len(response),
        )
        return response

    async def generate_stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Streame Antwort Token für Token via Ollama Chat API (stream=True)."""
        if not self._client:
            raise RuntimeError("LightEngine nicht initialisiert")

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

        payload = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "think": False,  # Qwen3 1.7B Thinking-Mode AUS — zu klein für gutes Reasoning
            "keep_alive": settings.ollama_light_keep_alive,
            "options": {
                "num_ctx": 2048,
                "temperature": 0.7,
            },
        }

        full_response = ""
        try:
            async with self._client.stream(
                "POST", "/api/chat", json=payload, timeout=60.0
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    import json as _json
                    try:
                        chunk = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
                    if chunk.get("done"):
                        break
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        full_response += token
                        yield token
        finally:
            pass

        # Session updaten
        if session_id:
            session = self._sessions.get(session_id)
            if session:
                session.add_turn("assistant", full_response)

        logger.info(
            "light_stream_complete",
            model=self._model,
            prompt_len=len(prompt),
            response_len=len(full_response),
        )

    async def health_check(self) -> bool:
        if not self._client:
            return False
        try:
            resp = await self._client.get("/api/tags")
            return resp.status_code == 200
        except Exception:
            return False
