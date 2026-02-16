"""
SOMA-AI Home Assistant Bridge
==============================
Synchronisiert Entitäten (Lights, Switches, Sensors) von Home Assistant.
Bidirektionale Kommunikation über die HA REST API.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import httpx
import structlog

from shared.resilience import SomaCircuitBreaker, SomaRetryLogic
from brain_core.config import settings

logger = structlog.get_logger("soma.ha_bridge")


class HomeAssistantBridge:
    """
    Home Assistant API Client.
    Synchronisiert Entitäten und führt Service-Calls aus.
    """

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._cb = SomaCircuitBreaker(name="homeassistant", failure_threshold=3)
        self._retry = SomaRetryLogic(max_retries=2, base_delay=1.0)
        self._entities_cache: dict[str, dict] = {}

    async def connect(self) -> None:
        if not settings.ha_token:
            logger.warning("ha_bridge_no_token", msg="HA Token nicht gesetzt – Bridge deaktiviert")
            return

        self._client = httpx.AsyncClient(
            base_url=settings.ha_url,
            headers={
                "Authorization": f"Bearer {settings.ha_token}",
                "Content-Type": "application/json",
            },
            timeout=10.0,
        )

        # Connection test
        try:
            resp = await self._cb.call(self._client.get, "/api/")
            if resp.status_code == 200:
                logger.info("ha_bridge_connected", url=settings.ha_url)
            else:
                logger.warning("ha_bridge_auth_failed", status=resp.status_code)
        except Exception as exc:
            logger.error("ha_bridge_connect_failed", error=str(exc))

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()

    async def get_states(self) -> list[dict]:
        """Alle Entitäten-States abrufen."""
        if not self._client:
            return []

        async def _fetch():
            resp = await self._client.get("/api/states")
            resp.raise_for_status()
            return resp.json()

        return await self._cb.call(_fetch)

    async def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str,
        data: Optional[dict] = None,
    ) -> dict:
        """
        HA Service Call (z.B. light.turn_on).
        domain='light', service='turn_on', entity_id='light.wohnzimmer'
        """
        if not self._client:
            raise ConnectionError("HA Bridge nicht verbunden")

        payload = {"entity_id": entity_id}
        if data:
            payload.update(data)

        async def _call():
            resp = await self._client.post(
                f"/api/services/{domain}/{service}",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

        result = await self._cb.call(_call)
        logger.info(
            "ha_service_called",
            domain=domain,
            service=service,
            entity=entity_id,
        )
        return result

    async def sync_entities(self) -> dict[str, dict]:
        """Sync und Cache aller HA-Entitäten."""
        states = await self.get_states()
        self._entities_cache = {s["entity_id"]: s for s in states}
        logger.info("ha_entities_synced", count=len(self._entities_cache))
        return self._entities_cache

    def get_cached_entity(self, entity_id: str) -> Optional[dict]:
        return self._entities_cache.get(entity_id)
