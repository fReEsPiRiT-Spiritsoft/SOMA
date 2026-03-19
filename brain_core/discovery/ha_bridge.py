"""
SOMA-AI Home Assistant Bridge
==============================
Synchronisiert Entitäten (Lights, Switches, Sensors) von Home Assistant.
Bidirektionale Kommunikation über die HA REST API.

Features:
  ✅ REST API Client für Service-Calls
  ✅ Periodischer State-Sync (alle 30s) → Action Awareness
  ✅ Entity-Cache mit Auto-Refresh
  ✅ State-Injection ins LLM-Kontext (Soma weiß was AN/AUS ist)
"""

from __future__ import annotations

import asyncio
from typing import Optional

import httpx
import structlog

from shared.resilience import SomaCircuitBreaker, SomaRetryLogic
from brain_core.config import settings

logger = structlog.get_logger("soma.ha_bridge")

# Sync-Intervall in Sekunden
HA_SYNC_INTERVAL = 30.0


class HomeAssistantBridge:
    """
    Home Assistant API Client.
    Synchronisiert Entitäten, führt Service-Calls aus und
    speist Status-Updates ins Action-Awareness System ein.
    """

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._cb = SomaCircuitBreaker(name="homeassistant", failure_threshold=3)
        self._retry = SomaRetryLogic(max_retries=2, base_delay=1.0)
        self._entities_cache: dict[str, dict] = {}
        self._sync_task: Optional[asyncio.Task] = None
        self._connected: bool = False

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
                self._connected = True
                # Initial sync
                await self.sync_entities()
                # Periodischen Sync starten
                self._sync_task = asyncio.create_task(self._periodic_sync())
            else:
                logger.warning("ha_bridge_auth_failed", status=resp.status_code)
        except Exception as exc:
            logger.error("ha_bridge_connect_failed", error=str(exc))

    async def disconnect(self) -> None:
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()
        self._connected = False

    async def _periodic_sync(self) -> None:
        """Periodischer State-Sync: Alle 30s HA-Status abrufen → Action Awareness."""
        while self._connected:
            try:
                await asyncio.sleep(HA_SYNC_INTERVAL)
                await self.sync_entities()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("ha_periodic_sync_error", error=str(exc))

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
        
        Nach dem Call wird der Entity-Cache aktualisiert und
        die Aktion im Action-Awareness System registriert.
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

        # Action-Awareness: Aktion registrieren
        try:
            from brain_core.action_awareness import record_action
            record_action(
                action_type="ha_call",
                params={"domain": domain, "service": service, "entity_id": entity_id, **(data or {})},
                entity_id=entity_id,
                result=f"{domain}.{service} → {entity_id}",
                success=True,
            )
        except Exception:
            pass

        # Entity-Cache sofort aktualisieren (best-effort)
        asyncio.create_task(self._refresh_entity_state(entity_id))

        return result

    async def _refresh_entity_state(self, entity_id: str) -> None:
        """Aktualisiere den Cache eines einzelnen Entity nach Service-Call."""
        try:
            if not self._client:
                return
            resp = await self._client.get(f"/api/states/{entity_id}")
            if resp.status_code == 200:
                state = resp.json()
                self._entities_cache[entity_id] = state
        except Exception:
            pass

    async def sync_entities(self) -> dict[str, dict]:
        """Sync und Cache aller HA-Entitäten + Update Action Awareness."""
        states = await self.get_states()
        self._entities_cache = {s["entity_id"]: s for s in states}
        logger.info("ha_entities_synced", count=len(self._entities_cache))

        # Action-Awareness: Bulk-Update der Gerätestatus
        try:
            from brain_core.action_awareness import update_ha_states
            update_ha_states(states)
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("ha_awareness_update_failed", error=str(exc))

        return self._entities_cache

    def get_cached_entity(self, entity_id: str) -> Optional[dict]:
        return self._entities_cache.get(entity_id)

    def get_cached_entities_by_domain(self, domain: str) -> dict[str, dict]:
        """Hole alle Entities einer Domain aus dem Cache."""
        return {
            eid: state
            for eid, state in self._entities_cache.items()
            if eid.startswith(f"{domain}.")
        }

