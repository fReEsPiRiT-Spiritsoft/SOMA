"""
SOMA-AI GitHub Models Client
==============================
High-quality code generation via GitHub Models API.
Used EXCLUSIVELY for plugin code generation — everything else stays local.

Endpoint: https://models.github.ai/inference/chat/completions
Auth:     GitHub PAT with `models:read` scope
Docs:     https://docs.github.com/en/rest/models/inference

Model-Format: publisher/model_name (z.B. "openai/o4-mini")

Unterstützte Modelle (Copilot Pro):
  openai/o4-mini      – Schnelles Reasoning, perfekt für Code (12 req/day)
  openai/o3-mini      – Reasoning-Modell (12 req/day)
  openai/gpt-4.1      – Starker Allrounder mit Code (12 req/day)
  openai/gpt-4.1-mini – Budget-Allrounder (12 req/day)
  openai/gpt-4o       – Bewährter Allrounder (50 req/day Low-Tier)
  openai/gpt-4o-mini  – Budget (150 req/day Low-Tier)
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx
import structlog

logger = structlog.get_logger("soma.evolution.github_models")

GITHUB_MODELS_ENDPOINT = "https://models.github.ai"

# o1/o3/o4 models: kein temperature/top_p, nutzen max_completion_tokens statt max_tokens,
# system prompt geht über "developer" role statt "system".
# Prefix-Match: "openai/o4-mini" starts with "openai/o4" etc.
O1_MODEL_PREFIXES = ("openai/o1", "openai/o3", "openai/o4", "o1", "o3", "o4")


class GitHubModelsClient:
    """
    Async client for GitHub Models API.

    Drop-in compatible mit HeavyLlamaEngine.generate() Interface:
      - generate(prompt, system_prompt, session_id, options_override)
      - drop_session(session_id)

    Wird nur für Plugin-Code-Generierung im Evolution Lab genutzt.
    """

    def __init__(self, token: str, model: str = "openai/o4-mini"):
        self._token = token
        # Normalize: "o4-mini" → "openai/o4-mini"
        if "/" not in model:
            model = f"openai/{model}"
        self._model = model
        self._client: Optional[httpx.AsyncClient] = None
        self._is_o1 = any(model.startswith(p) for p in O1_MODEL_PREFIXES)

    async def initialize(self) -> None:
        """HTTP-Client starten."""
        self._client = httpx.AsyncClient(
            base_url=GITHUB_MODELS_ENDPOINT,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/vnd.github+json",
            },
            timeout=240.0,  # o1 Reasoning kann 2-3 Minuten dauern
        )
        logger.info(
            "github_models_init",
            model=self._model,
            is_o1=self._is_o1,
            endpoint=GITHUB_MODELS_ENDPOINT,
        )

    async def shutdown(self) -> None:
        """HTTP-Client schließen."""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Main Interface ───────────────────────────────────────────────────

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,  # ignored — API ist stateless
        options_override: Optional[dict] = None,
    ) -> str:
        """
        Generiere Text via GitHub Models API.

        Interface-kompatibel mit HeavyLlamaEngine.generate() für Drop-in-Nutzung.
        """
        if not self._client:
            await self.initialize()

        # ── Messages zusammenbauen ────────────────────────────────────────
        messages: list[dict[str, str]] = []

        if system_prompt:
            if self._is_o1:
                # o1/o3 Modelle: "developer" role statt "system"
                messages.append({"role": "developer", "content": system_prompt})
            else:
                messages.append({"role": "system", "content": system_prompt})

        messages.append({"role": "user", "content": prompt})

        # ── Payload bauen ─────────────────────────────────────────────────
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }

        if self._is_o1:
            # o1/o3: kein temperature/top_p, eigenes Token-Limit
            payload["max_completion_tokens"] = 16384
        else:
            # Standard-Modelle (GPT-4o etc.)
            params: dict[str, Any] = {
                "temperature": 0.1,
                "top_p": 0.95,
                "max_tokens": 16384,
            }
            if options_override:
                # Nur relevante Keys übernehmen
                for k in ("temperature", "top_p", "max_tokens"):
                    if k in options_override:
                        params[k] = options_override[k]
            payload.update(params)

        # ── API Call ──────────────────────────────────────────────────────
        try:
            resp = await self._client.post("/inference/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()

            content = data["choices"][0]["message"]["content"]

            usage = data.get("usage", {})
            logger.info(
                "github_models_generated",
                model=self._model,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                response_len=len(content),
            )

            return content

        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            body = exc.response.text[:500]

            if status == 429:
                logger.warning(
                    "github_models_rate_limit",
                    model=self._model,
                    response=body,
                )
                raise RuntimeError(
                    f"GitHub Models Rate Limit erreicht für '{self._model}'. "
                    f"Tägliches Limit erschöpft — versuche es morgen erneut "
                    f"oder wechsle auf ein Modell mit höherem Limit (z.B. gpt-4o-mini)."
                ) from exc

            elif status in (401, 403):
                raise RuntimeError(
                    f"GitHub Token hat keinen Zugriff auf '{self._model}'. "
                    f"Fehler {status}: {body[:200]}\n"
                    "Erstelle einen neuen Fine-grained PAT mit 'Models: Read' Berechtigung:\n"
                    "https://github.com/settings/personal-access-tokens/new\n"
                    "→ Account permissions → Models → Read"
                ) from exc

            elif status == 404:
                raise RuntimeError(
                    f"Modell '{self._model}' nicht gefunden auf GitHub Models. "
                    f"Verfügbare Modelle: o1-mini, o1-preview, gpt-4o, gpt-4o-mini, o4-mini"
                ) from exc

            else:
                raise RuntimeError(
                    f"GitHub Models API Fehler {status}: {body}"
                ) from exc

        except httpx.TimeoutException as exc:
            raise RuntimeError(
                f"GitHub Models API Timeout (Model: {self._model}). "
                f"o1/o3 Modelle können bis zu 3 Minuten für Reasoning brauchen."
            ) from exc

        except Exception as exc:
            if "RuntimeError" in type(exc).__name__:
                raise  # Unsere eigenen Errors durchlassen
            raise RuntimeError(f"GitHub Models API Fehler: {exc}") from exc

    # ── Interface-Kompatibilität mit HeavyLlamaEngine ────────────────────

    def drop_session(self, session_id: str) -> None:
        """No-op — API ist stateless. Erfüllt HeavyLlamaEngine-Interface."""
        pass

    async def health_check(self) -> bool:
        """Prüfe ob GitHub Models API erreichbar und Token gültig ist."""
        if not self._client:
            try:
                await self.initialize()
            except Exception:
                return False
        try:
            resp = await self._client.get("/catalog/models", timeout=10.0)
            return resp.status_code == 200
        except Exception:
            return False

    @property
    def is_generating(self) -> bool:
        """Immer False — Cloud API blockiert keinen lokalen VRAM."""
        return False
