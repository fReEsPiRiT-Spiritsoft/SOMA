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

    async def initialize(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.ollama_url,
            timeout=120.0,  # LLM-Calls können dauern
        )
        logger.info("heavy_engine_init", model=self._model, url=settings.ollama_url)

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """Generiere Antwort via Ollama Chat API."""
        if not self._client:
            raise RuntimeError("HeavyEngine nicht initialisiert")

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

        # Ollama API Call
        async def _call() -> str:
            resp = await self._client.post(
                "/api/chat",
                json={
                    "model": self._model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "num_ctx": 4096,
                        "temperature": 0.7,
                        "top_p": 0.9,
                    },
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["message"]["content"]

        response = await self._cb.call(self._retry.execute, _call)

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
        )

        return response

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
