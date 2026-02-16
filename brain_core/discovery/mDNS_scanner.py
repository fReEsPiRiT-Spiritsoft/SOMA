"""
SOMA-AI mDNS Scanner
=====================
Findet IP-basierte Geräte im lokalen Netzwerk via Zeroconf/mDNS.
Registriert gefundene Devices automatisch in der SSOT.
"""

from __future__ import annotations

import asyncio
from typing import Optional, Callable, Awaitable

import structlog

logger = structlog.get_logger("soma.mdns")

# Service-Types die SOMA sucht
SOMA_SERVICE_TYPES = [
    "_soma._tcp.local.",       # Eigene SOMA-Nodes
    "_http._tcp.local.",       # Generische HTTP-Devices
    "_mqtt._tcp.local.",       # MQTT-fähige Geräte
    "_esphomelib._tcp.local.", # ESPHome-Devices
]


class MDNSScanner:
    """
    Async mDNS/Zeroconf Scanner für Hardware-Discovery.
    """

    def __init__(self):
        self._browser = None
        self._zeroconf = None
        self._running = False
        self._discovered: dict[str, dict] = {}
        self._on_discovered: Optional[
            Callable[[dict], Awaitable[None]]
        ] = None

    def set_callback(
        self, callback: Callable[[dict], Awaitable[None]]
    ) -> None:
        self._on_discovered = callback

    async def start(self) -> None:
        """Starte den mDNS Scanner."""
        if self._running:
            return

        try:
            from zeroconf.asyncio import AsyncZeroconf, AsyncServiceBrowser

            self._zeroconf = AsyncZeroconf()
            self._browser = AsyncServiceBrowser(
                self._zeroconf.zeroconf,
                SOMA_SERVICE_TYPES,
                handlers=[self._on_service_state_change],
            )
            self._running = True
            logger.info("mdns_scanner_started", service_types=SOMA_SERVICE_TYPES)

        except ImportError:
            logger.warning("mdns_zeroconf_not_installed")
        except Exception as exc:
            logger.error("mdns_start_error", error=str(exc))

    async def stop(self) -> None:
        if self._browser:
            self._browser.cancel()
        if self._zeroconf:
            await self._zeroconf.async_close()
        self._running = False
        logger.info("mdns_scanner_stopped")

    def _on_service_state_change(self, zeroconf, service_type, name, state_change):
        """Callback von Zeroconf (synchron – dispatche async)."""
        from zeroconf import ServiceStateChange

        if state_change == ServiceStateChange.Added:
            asyncio.ensure_future(
                self._resolve_service(zeroconf, service_type, name)
            )

    async def _resolve_service(self, zeroconf, service_type, name):
        """Resolve Service-Info und registriere."""
        try:
            from zeroconf import ServiceInfo
            import socket

            info = ServiceInfo(service_type, name)
            if await asyncio.get_event_loop().run_in_executor(
                None, info.request, zeroconf, 3000
            ):
                addresses = info.parsed_scoped_addresses()
                device = {
                    "name": name,
                    "service_type": service_type,
                    "ip_address": addresses[0] if addresses else None,
                    "port": info.port,
                    "properties": {
                        k.decode(): v.decode() if isinstance(v, bytes) else v
                        for k, v in info.properties.items()
                    },
                }

                self._discovered[name] = device
                logger.info(
                    "mdns_device_found",
                    name=name,
                    ip=device["ip_address"],
                    port=device["port"],
                )

                if self._on_discovered:
                    await self._on_discovered(device)

        except Exception as exc:
            logger.debug("mdns_resolve_error", name=name, error=str(exc))

    def get_discovered(self) -> dict[str, dict]:
        return self._discovered.copy()

    async def scan_once(self) -> dict[str, dict]:
        """Einmaliger Scan mit Timeout."""
        await self.start()
        await asyncio.sleep(5.0)
        result = self.get_discovered()
        await self.stop()
        return result
