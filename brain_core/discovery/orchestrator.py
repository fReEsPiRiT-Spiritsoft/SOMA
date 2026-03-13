"""
SOMA-AI Discovery Orchestrator — Phase 6
==========================================
Koordiniert ALLE Hardware-Discovery-Quellen zu einer einheitlichen Registry.

┌────────────────────────────────────────────────────────────────────┐
│                     DiscoveryOrchestrator                          │
│                                                                    │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐   │
│  │ MQTTListener │  │ MDNSScanner  │  │ HomeAssistantBridge   │   │
│  │  (Hello-Pkts)│  │ (Zeroconf)   │  │ (REST API Sync)       │   │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬────────────┘   │
│         └──────────┬───────┴─────────────────────┘                │
│                    ▼                                               │
│         ┌──────────────────────────┐                              │
│         │ Unified Device Registry  │                              │
│         │ (DiscoveredDevice SSOT)  │                              │
│         └──────────┬───────────────┘                              │
│                    ▼                                               │
│         ┌──────────────────────────┐                              │
│         │ Event System             │                              │
│         │  on_device_discovered    │                              │
│         │  on_device_lost          │                              │
│         │  on_ha_entity_synced     │                              │
│         └──────────────────────────┘                              │
└────────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Optional, Callable, Awaitable

import structlog

from shared.audio_types import (
    DiscoveredDevice,
    DeviceStatus,
    HardwareHello,
    NodeType,
    ProtocolType,
)
from shared.resilience import SomaCircuitBreaker
from brain_core.config import settings
from brain_core.discovery.mqtt_listener import MQTTListener
from brain_core.discovery.mDNS_scanner import MDNSScanner
from brain_core.discovery.ha_bridge import HomeAssistantBridge

logger = structlog.get_logger("soma.discovery")


class DiscoveryOrchestrator:
    """
    Zero-Config Hardware Onboarding: Koordiniert MQTT + mDNS + HA Bridge.

    Features:
      - Automatische Registrierung neuer Hardware via MQTT-Hello
      - mDNS Service-Discovery für IP-basierte Geräte
      - Home Assistant Entitäten-Sync (bidirektional)
      - Unified Device Registry (SSOT für alle Hardware)
      - Device Health Tracking (last_seen, online/offline)
      - Periodic Cleanup für verschwundene Geräte
    """

    def __init__(
        self,
        mqtt_listener: MQTTListener,
        mdns_scanner: MDNSScanner,
        ha_bridge: HomeAssistantBridge,
        device_timeout: float = 600.0,       # 10 Min bis offline
        ha_sync_interval: float = 300.0,      # HA-Sync alle 5 Min
        cleanup_interval: float = 120.0,      # Cleanup alle 2 Min
    ):
        self._mqtt = mqtt_listener
        self._mdns = mdns_scanner
        self._ha = ha_bridge
        self._device_timeout = device_timeout
        self._ha_sync_interval = ha_sync_interval
        self._cleanup_interval = cleanup_interval

        # Unified Device Registry
        self._devices: dict[str, DiscoveredDevice] = {}
        self._lock = asyncio.Lock()

        # Background Tasks
        self._tasks: list[asyncio.Task] = []
        self._running = False

        # Stats
        self._mqtt_discoveries: int = 0
        self._mdns_discoveries: int = 0
        self._ha_syncs: int = 0

        # Callbacks
        self._on_device_discovered: Callable[[DiscoveredDevice], Awaitable[None]] | None = None
        self._on_device_lost: Callable[[DiscoveredDevice], Awaitable[None]] | None = None

    def set_callbacks(
        self,
        on_discovered: Callable[[DiscoveredDevice], Awaitable[None]] | None = None,
        on_lost: Callable[[DiscoveredDevice], Awaitable[None]] | None = None,
    ) -> None:
        """Callbacks für Device-Events setzen."""
        self._on_device_discovered = on_discovered
        self._on_device_lost = on_lost

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Alle Discovery-Services starten."""
        if self._running:
            return
        self._running = True

        # MQTT Hello-Callback verdrahten
        self._mqtt.set_hello_callback(self._on_mqtt_hello)

        # mDNS Discovery-Callback verdrahten
        self._mdns.set_callback(self._on_mdns_discovered)

        # MQTT Listener starten
        try:
            await self._mqtt.start()
            logger.info("discovery_mqtt_started")
        except Exception as exc:
            logger.warning("discovery_mqtt_failed", error=str(exc))

        # mDNS Scanner starten
        try:
            await self._mdns.start()
            logger.info("discovery_mdns_started")
        except Exception as exc:
            logger.warning("discovery_mdns_failed", error=str(exc))

        # Initiale HA-Sync
        try:
            await self._sync_ha_entities()
            logger.info("discovery_ha_initial_sync_done")
        except Exception as exc:
            logger.warning("discovery_ha_sync_failed", error=str(exc))

        # Background Tasks starten
        self._tasks.append(
            asyncio.create_task(self._periodic_ha_sync(), name="discovery-ha-sync")
        )
        self._tasks.append(
            asyncio.create_task(self._periodic_cleanup(), name="discovery-cleanup")
        )

        logger.info(
            "discovery_orchestrator_started",
            devices=len(self._devices),
        )

    async def stop(self) -> None:
        """Alle Services stoppen."""
        self._running = False

        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

        await self._mqtt.stop()
        await self._mdns.stop()

        logger.info("discovery_orchestrator_stopped")

    # ── MQTT Hello Handler ───────────────────────────────────────────────

    async def _on_mqtt_hello(self, hello: HardwareHello) -> None:
        """
        Neues Gerät hat sich via MQTT gemeldet → In Registry aufnehmen.
        """
        async with self._lock:
            self._mqtt_discoveries += 1
            existing = self._devices.get(hello.node_id)

            device = DiscoveredDevice(
                device_id=hello.node_id,
                name=hello.node_id,
                device_type=hello.node_type,
                protocol=hello.protocol,
                status=DeviceStatus.ONLINE,
                ip_address=hello.ip_address,
                capabilities=hello.capabilities,
                firmware_version=hello.firmware_version,
                discovered_at=existing.discovered_at if existing else datetime.utcnow(),
                last_seen=datetime.utcnow(),
            )

            is_new = hello.node_id not in self._devices
            self._devices[hello.node_id] = device

            if is_new:
                logger.info(
                    "device_discovered_mqtt",
                    node_id=hello.node_id,
                    node_type=hello.node_type.value,
                    capabilities=hello.capabilities,
                )
                if self._on_device_discovered:
                    await self._on_device_discovered(device)
            else:
                logger.debug(
                    "device_heartbeat_mqtt",
                    node_id=hello.node_id,
                )

    # ── mDNS Discovery Handler ───────────────────────────────────────────

    async def _on_mdns_discovered(self, service_info: dict) -> None:
        """
        Neues Gerät via mDNS/Zeroconf entdeckt → In Registry aufnehmen.
        """
        async with self._lock:
            self._mdns_discoveries += 1

            # Device-ID aus Service-Name ableiten
            name = service_info.get("name", "unknown")
            device_id = f"mdns_{name}"

            # Device-Type aus Service-Type erraten
            service_type = service_info.get("service_type", "")
            if "_soma._tcp" in service_type:
                device_type = NodeType.MIC  # Default für SOMA-Nodes
            elif "_esphomelib._tcp" in service_type:
                device_type = NodeType.TAB
            else:
                device_type = NodeType.MIC

            existing = self._devices.get(device_id)
            properties = service_info.get("properties", {})

            device = DiscoveredDevice(
                device_id=device_id,
                name=name,
                device_type=device_type,
                protocol=ProtocolType.MDNS,
                status=DeviceStatus.ONLINE,
                ip_address=service_info.get("ip_address"),
                port=service_info.get("port"),
                capabilities=list(properties.keys()),
                properties=properties,
                discovered_at=existing.discovered_at if existing else datetime.utcnow(),
                last_seen=datetime.utcnow(),
            )

            is_new = device_id not in self._devices
            self._devices[device_id] = device

            if is_new:
                logger.info(
                    "device_discovered_mdns",
                    device_id=device_id,
                    name=name,
                    ip=device.ip_address,
                    port=device.port,
                )
                if self._on_device_discovered:
                    await self._on_device_discovered(device)

    # ── Home Assistant Sync ──────────────────────────────────────────────

    async def _sync_ha_entities(self) -> int:
        """
        HA-Entitäten synchronisieren → als Devices in die Registry.
        Returns: Anzahl synchronisierter Entitäten.
        """
        try:
            entities = await self._ha.sync_entities()
        except Exception as exc:
            logger.warning("ha_sync_error", error=str(exc))
            return 0

        count = 0
        async with self._lock:
            for entity_id, state in entities.items():
                device_id = f"ha_{entity_id}"

                # Device-Type aus HA-Domain ableiten
                domain = entity_id.split(".")[0] if "." in entity_id else "unknown"
                if domain in ("media_player", "tts"):
                    device_type = NodeType.SPK
                elif domain in ("sensor", "binary_sensor"):
                    device_type = NodeType.MIC
                else:
                    device_type = NodeType.TAB

                existing = self._devices.get(device_id)
                ha_state = state.get("state", "unknown")

                device = DiscoveredDevice(
                    device_id=device_id,
                    name=state.get("attributes", {}).get(
                        "friendly_name", entity_id
                    ),
                    device_type=device_type,
                    protocol=ProtocolType.HA,
                    status=(
                        DeviceStatus.ONLINE
                        if ha_state not in ("unavailable", "unknown")
                        else DeviceStatus.OFFLINE
                    ),
                    room_id=state.get("attributes", {}).get("room"),
                    capabilities=[domain],
                    properties={
                        "entity_id": entity_id,
                        "state": ha_state,
                        "domain": domain,
                    },
                    discovered_at=(
                        existing.discovered_at if existing else datetime.utcnow()
                    ),
                    last_seen=datetime.utcnow(),
                )

                is_new = device_id not in self._devices
                self._devices[device_id] = device
                count += 1

                if is_new and self._on_device_discovered:
                    await self._on_device_discovered(device)

        self._ha_syncs += 1
        logger.info("ha_entities_synced", count=count)
        return count

    async def _periodic_ha_sync(self) -> None:
        """Periodischer HA-Sync (alle ha_sync_interval Sekunden)."""
        while self._running:
            try:
                await asyncio.sleep(self._ha_sync_interval)
                await self._sync_ha_entities()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("periodic_ha_sync_error", error=str(exc))
                await asyncio.sleep(30.0)

    # ── Device Health & Cleanup ──────────────────────────────────────────

    async def _periodic_cleanup(self) -> None:
        """Periodisches Cleanup: Markiere verschwundene Geräte als offline."""
        while self._running:
            try:
                await asyncio.sleep(self._cleanup_interval)
                await self._check_device_health()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("periodic_cleanup_error", error=str(exc))
                await asyncio.sleep(30.0)

    async def _check_device_health(self) -> None:
        """Prüfe alle Devices auf Timeout → markiere als offline."""
        async with self._lock:
            now = datetime.utcnow()
            for device in self._devices.values():
                if device.status == DeviceStatus.ONLINE:
                    age = (now - device.last_seen).total_seconds()
                    if age > self._device_timeout:
                        device.status = DeviceStatus.OFFLINE
                        logger.info(
                            "device_offline",
                            device_id=device.device_id,
                            last_seen_ago_sec=round(age),
                        )
                        if self._on_device_lost:
                            await self._on_device_lost(device)

    # ── Device Registry Queries ──────────────────────────────────────────

    def get_all_devices(self) -> list[DiscoveredDevice]:
        """Alle registrierten Geräte."""
        return list(self._devices.values())

    def get_device(self, device_id: str) -> DiscoveredDevice | None:
        """Ein bestimmtes Gerät."""
        return self._devices.get(device_id)

    def get_online_devices(self) -> list[DiscoveredDevice]:
        """Alle Online-Geräte."""
        return [d for d in self._devices.values() if d.is_online]

    def get_devices_by_room(self, room_id: str) -> list[DiscoveredDevice]:
        """Alle Geräte in einem Raum."""
        return [d for d in self._devices.values() if d.room_id == room_id]

    def get_devices_by_type(self, device_type: NodeType) -> list[DiscoveredDevice]:
        """Alle Geräte eines Typs."""
        return [d for d in self._devices.values() if d.device_type == device_type]

    def get_devices_by_protocol(self, protocol: ProtocolType) -> list[DiscoveredDevice]:
        """Alle Geräte eines Protokolls."""
        return [d for d in self._devices.values() if d.protocol == protocol]

    async def assign_room(self, device_id: str, room_id: str) -> DiscoveredDevice | None:
        """Gerät einem Raum zuweisen."""
        async with self._lock:
            device = self._devices.get(device_id)
            if device:
                device.room_id = room_id
                logger.info(
                    "device_room_assigned",
                    device_id=device_id,
                    room=room_id,
                )
            return device

    async def force_scan(self) -> dict:
        """
        Manuelle Scan-Aufforderung: mDNS + HA sofort synchronisieren.
        """
        results = {"mdns": {}, "ha": 0}

        # mDNS-Scan
        try:
            mdns_devices = await self._mdns.scan_once()
            results["mdns"] = {
                "found": len(mdns_devices),
                "devices": list(mdns_devices.keys()),
            }
        except Exception as exc:
            results["mdns"] = {"error": str(exc)}

        # HA-Sync
        try:
            ha_count = await self._sync_ha_entities()
            results["ha"] = ha_count
        except Exception as exc:
            results["ha"] = {"error": str(exc)}

        logger.info("force_scan_complete", results=results)
        return results

    # ── Statistics ───────────────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        """Discovery-Statistiken."""
        online = len(self.get_online_devices())
        total = len(self._devices)
        by_protocol = {}
        for p in ProtocolType:
            count = len(self.get_devices_by_protocol(p))
            if count > 0:
                by_protocol[p.value] = count

        return {
            "total_devices": total,
            "online_devices": online,
            "offline_devices": total - online,
            "by_protocol": by_protocol,
            "mqtt_discoveries": self._mqtt_discoveries,
            "mdns_discoveries": self._mdns_discoveries,
            "ha_syncs": self._ha_syncs,
            "is_running": self._running,
        }
