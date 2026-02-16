"""
SOMA-AI Audio Router (Virtual Patchbay)
========================================
Dynamisches Routing: Mic-Nodes ↔ Speaker-Nodes.
Gesteuert durch PresenceManager (wer ist wo?).

Datenfluss:
  PresenceEvent ──► AudioRouter.on_presence_change()
                        │
                        ├─ Alte Route deaktivieren
                        ├─ Neue Route: Mics(new_room) → Speakers(new_room)
                        └─ PatchRoute published via MQTT
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Optional, Callable, Awaitable

import structlog

from shared.audio_types import PatchRoute, PresenceEvent, NodeType

logger = structlog.get_logger("soma.audio_router")


class AudioRouter:
    """
    Virtual Patchbay: Verwaltet Mic → Speaker Verbindungen.
    Die Hardware-Registry kommt aus Django SSOT (via REST API).
    """

    def __init__(self):
        self._routes: dict[str, PatchRoute] = {}  # route_id → PatchRoute
        self._room_routes: dict[str, list[str]] = {}  # room_id → [route_ids]
        self._hardware_cache: dict[str, dict] = {}  # node_id → node_info
        self._on_route_change: Optional[
            Callable[[PatchRoute], Awaitable[None]]
        ] = None
        self._lock = asyncio.Lock()

    def set_route_callback(
        self, callback: Callable[[PatchRoute], Awaitable[None]]
    ) -> None:
        """Callback wenn Route sich ändert (z.B. MQTT publish)."""
        self._on_route_change = callback

    # ── Hardware Registry Sync ───────────────────────────────────────────

    async def sync_hardware(self, nodes: list[dict]) -> None:
        """
        Sync Hardware-Nodes von Django SSOT.
        Wird periodisch oder bei Discovery aufgerufen.
        """
        async with self._lock:
            self._hardware_cache.clear()
            for node in nodes:
                self._hardware_cache[node["node_id"]] = node
            logger.info("hardware_synced", node_count=len(nodes))

    def get_room_nodes(
        self,
        room_id: str,
        node_type: Optional[NodeType] = None,
    ) -> list[dict]:
        """Alle Nodes eines Raums, optional gefiltert nach Typ."""
        return [
            node
            for node in self._hardware_cache.values()
            if node.get("room_id") == room_id
            and (node_type is None or node.get("node_type") == node_type.value)
        ]

    # ── Route Management ─────────────────────────────────────────────────

    async def create_route(
        self,
        room_id: str,
        source_node_id: str,
        target_node_ids: list[str],
        session_id: Optional[str] = None,
        priority: int = 5,
    ) -> PatchRoute:
        """Neue Audio-Route erstellen."""
        route = PatchRoute(
            route_id=str(uuid.uuid4()),
            source_node_id=source_node_id,
            target_node_ids=target_node_ids,
            room_id=room_id,
            session_id=session_id,
            priority=priority,
            active=True,
        )

        async with self._lock:
            self._routes[route.route_id] = route
            self._room_routes.setdefault(room_id, []).append(route.route_id)

        logger.info(
            "route_created",
            route_id=route.route_id,
            source=source_node_id,
            targets=target_node_ids,
            room=room_id,
        )

        if self._on_route_change:
            await self._on_route_change(route)

        return route

    async def deactivate_room_routes(self, room_id: str) -> None:
        """Alle Routes eines Raums deaktivieren."""
        async with self._lock:
            route_ids = self._room_routes.get(room_id, [])
            for rid in route_ids:
                route = self._routes.get(rid)
                if route:
                    route.active = False

        logger.info("room_routes_deactivated", room=room_id, count=len(route_ids))

    async def get_active_routes(self, room_id: Optional[str] = None) -> list[PatchRoute]:
        """Aktive Routes abfragen."""
        routes = list(self._routes.values())
        if room_id:
            routes = [r for r in routes if r.room_id == room_id]
        return [r for r in routes if r.active]

    # ── Presence-gesteuerte Automation ───────────────────────────────────

    async def on_presence_change(self, event: PresenceEvent) -> None:
        """
        Callback vom PresenceManager: User hat den Raum gewechselt.
        → Alte Routes deaktivieren, neue Routes für den neuen Raum anlegen.
        """
        # Alte Routes deaktivieren
        if event.from_room:
            remaining_users_old = False  # Würde vom PresenceManager kommen
            if not remaining_users_old:
                await self.deactivate_room_routes(event.from_room)

        # Neue Routes im Zielraum
        mics = self.get_room_nodes(event.to_room, NodeType.MIC)
        speakers = self.get_room_nodes(event.to_room, NodeType.SPK)

        if mics and speakers:
            speaker_ids = [s["node_id"] for s in speakers]
            for mic in mics:
                await self.create_route(
                    room_id=event.to_room,
                    source_node_id=mic["node_id"],
                    target_node_ids=speaker_ids,
                    session_id=None,  # Session wird vom brain_core gesetzt
                )

        logger.info(
            "patchbay_updated",
            user=event.user_id,
            to_room=event.to_room,
            mics=len(mics),
            speakers=len(speakers),
        )

    # ── Cleanup ──────────────────────────────────────────────────────────

    async def cleanup_inactive_routes(self) -> None:
        """Entferne deaktivierte Routes."""
        async with self._lock:
            inactive = [
                rid for rid, r in self._routes.items() if not r.active
            ]
            for rid in inactive:
                route = self._routes.pop(rid)
                room_routes = self._room_routes.get(route.room_id, [])
                if rid in room_routes:
                    room_routes.remove(rid)

            if inactive:
                logger.info("routes_cleaned", count=len(inactive))
