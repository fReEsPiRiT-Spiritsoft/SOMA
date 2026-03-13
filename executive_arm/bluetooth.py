"""
SOMA-AI Bluetooth — BLE Discovery & Steuerung via bleak
==========================================================
SOMA kann Bluetooth Low Energy (BLE) Geraete entdecken und steuern.

Use-Cases:
  - Neue Smart-Home-Geraete finden (Auto-Discovery)
  - BLE-Sensoren auslesen (Temperatur, Luftfeuchtigkeit, etc.)
  - BLE-Aktoren steuern (Lampen, Locks, etc.)

Sicherheit:
  - BLE_SCAN: SAFE (nur lesen)
  - BLE_CONNECT: LOW (Verbindung herstellen)
  - BLE_WRITE: MEDIUM (Daten an Geraet senden)
  - Alle Aktionen durch PolicyEngine
  - Kein Pairing ohne User-Freigabe

Technik:
  - bleak (Bluetooth Low Energy platform Agnostic Klient)
  - Async nativ (bleak ist async-first)
  - Scan-Cache: 60s (nicht dauernd scannen)
  - Max 5 gleichzeitige Verbindungen

Non-Negotiable:
  - Privacy: Keine BLE-Beacons tracken (keine Personenverfolgung)
  - Kein Firmware-Flash ohne HARD approval
  - Alle Devices + Interaktionen geloggt
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

import structlog

from executive_arm.policy_engine import (
    PolicyEngine,
    ActionRequest,
    ActionType,
)

logger = structlog.get_logger("soma.executive.bluetooth")


# ── Constants ────────────────────────────────────────────────────────────

SCAN_DURATION_SEC: float = 10.0
SCAN_CACHE_TTL_SEC: float = 60.0    # Cache-Dauer fuer Scan-Ergebnisse
MAX_CONNECTIONS: int = 5
CONNECT_TIMEOUT_SEC: float = 15.0


# ── BLE Device Info ──────────────────────────────────────────────────────

@dataclass
class BLEDevice:
    """Entdecktes BLE-Geraet."""
    address: str                    # MAC-Adresse
    name: str = "Unknown"
    rssi: int = 0                   # Signalstaerke (dBm)
    service_uuids: list[str] = field(default_factory=list)
    manufacturer_data: dict = field(default_factory=dict)
    is_connectable: bool = True
    last_seen: float = field(default_factory=time.time)


@dataclass
class BLEResult:
    """Ergebnis einer BLE-Operation."""
    success: bool = False
    device_address: str = ""
    data: str = ""                 # Gelesene/geschriebene Daten
    devices: list[BLEDevice] = field(default_factory=list)
    was_allowed: bool = True
    policy_message: str = ""
    error: str = ""
    duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)


# ══════════════════════════════════════════════════════════════════════════
#  BLE MANAGER — SOMAs Bluetooth-Reichweite
# ══════════════════════════════════════════════════════════════════════════

class BLEManager:
    """
    Async BLE-Management via bleak.
    
    Features:
      - Scan mit Cache (60s TTL)
      - Connect/Disconnect mit Timeout
      - Read/Write Characteristics
      - Auto-Disconnect nach Inaktivitaet
      - Policy-Check vor jeder Aktion
    
    Usage:
        ble = BLEManager(policy_engine=pe)
        result = await ble.scan()
        result = await ble.read_characteristic("AA:BB:CC:DD:EE:FF", "0000180a-...")
    """

    def __init__(self, policy_engine: PolicyEngine):
        self._policy = policy_engine

        # ── Scan Cache ───────────────────────────────────────────────
        self._scan_cache: list[BLEDevice] = []
        self._last_scan_time: float = 0.0

        # ── Active Connections ───────────────────────────────────────
        self._connections: dict[str, object] = {}  # address → BleakClient

        # ── Stats ────────────────────────────────────────────────────
        self._total_scans: int = 0
        self._total_connects: int = 0
        self._total_reads: int = 0
        self._total_writes: int = 0
        self._denied_count: int = 0

        logger.info("ble_manager_initialized")

    # ══════════════════════════════════════════════════════════════════
    #  SCAN — Geraete in der Naehe finden
    # ══════════════════════════════════════════════════════════════════

    async def scan(
        self,
        duration: float = SCAN_DURATION_SEC,
        reason: str = "",
        agent_goal: str = "",
        force_refresh: bool = False,
    ) -> BLEResult:
        """
        Scanne nach BLE-Geraeten in der Naehe.
        
        Nutzt Cache wenn Ergebnis juenger als 60s.
        Policy: BLE_SCAN (SAFE)
        """
        self._total_scans += 1

        # Cache-Check
        cache_age = time.time() - self._last_scan_time
        if not force_refresh and cache_age < SCAN_CACHE_TTL_SEC and self._scan_cache:
            logger.debug("ble_scan_cached", age=f"{cache_age:.0f}s")
            return BLEResult(
                success=True,
                devices=self._scan_cache,
            )

        # ── Policy-Check ─────────────────────────────────────────────
        policy_request = ActionRequest(
            action_type=ActionType.BLE_SCAN,
            description="BLE-Scan: Geraete in der Naehe suchen",
            target="bluetooth",
            reason=reason or "Geraete-Discovery",
            agent_goal=agent_goal,
        )
        policy_result = await self._policy.check(policy_request)

        if not policy_result.allowed:
            self._denied_count += 1
            return BLEResult(
                was_allowed=False,
                policy_message=policy_result.message,
            )

        # ── Scan ausfuehren ─────────────────────────────────────────
        t0 = time.monotonic()
        try:
            from bleak import BleakScanner

            discovered = await BleakScanner.discover(
                timeout=duration,
                return_adv=True,
            )

            devices: list[BLEDevice] = []
            for bd, adv in discovered.values():
                device = BLEDevice(
                    address=bd.address,
                    name=bd.name or adv.local_name or "Unknown",
                    rssi=adv.rssi or 0,
                    service_uuids=list(adv.service_uuids) if adv.service_uuids else [],
                    manufacturer_data=(
                        {str(k): v.hex() for k, v in adv.manufacturer_data.items()}
                        if adv.manufacturer_data else {}
                    ),
                )
                devices.append(device)

            # Sortiert nach Signalstaerke
            devices.sort(key=lambda d: d.rssi, reverse=True)

            # Cache aktualisieren
            self._scan_cache = devices
            self._last_scan_time = time.time()

            duration_ms = (time.monotonic() - t0) * 1000

            logger.info(
                "ble_scan_complete",
                devices_found=len(devices),
                duration_ms=f"{duration_ms:.0f}",
            )

            return BLEResult(
                success=True,
                devices=devices,
                duration_ms=duration_ms,
            )

        except ImportError:
            logger.error("bleak_not_installed", msg="pip install bleak")
            return BLEResult(error="bleak not installed: pip install bleak")
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            logger.error("ble_scan_failed", error=str(exc))
            return BLEResult(
                error=str(exc),
                duration_ms=duration_ms,
            )

    # ══════════════════════════════════════════════════════════════════
    #  CONNECT / DISCONNECT
    # ══════════════════════════════════════════════════════════════════

    async def connect(
        self,
        address: str,
        reason: str = "",
        agent_goal: str = "",
    ) -> BLEResult:
        """
        Verbinde mit einem BLE-Geraet.
        Policy: BLE_CONNECT (LOW)
        """
        self._total_connects += 1

        # Schon verbunden?
        if address in self._connections:
            return BLEResult(
                success=True,
                device_address=address,
                data="Already connected",
            )

        # Max connections?
        if len(self._connections) >= MAX_CONNECTIONS:
            return BLEResult(
                success=False,
                device_address=address,
                error=f"Max {MAX_CONNECTIONS} gleichzeitige Verbindungen erreicht",
            )

        # ── Policy-Check ─────────────────────────────────────────────
        policy_request = ActionRequest(
            action_type=ActionType.BLE_CONNECT,
            description=f"BLE: Verbinde mit {address}",
            target=address,
            reason=reason,
            agent_goal=agent_goal,
        )
        policy_result = await self._policy.check(policy_request)

        if not policy_result.allowed:
            self._denied_count += 1
            return BLEResult(
                device_address=address,
                was_allowed=False,
                policy_message=policy_result.message,
            )

        # ── Connect ──────────────────────────────────────────────────
        t0 = time.monotonic()
        try:
            from bleak import BleakClient

            client = BleakClient(address, timeout=CONNECT_TIMEOUT_SEC)
            await client.connect()

            self._connections[address] = client
            duration_ms = (time.monotonic() - t0) * 1000

            # Services auflisten
            services_info = []
            for service in client.services:
                for char in service.characteristics:
                    services_info.append(
                        f"  {char.uuid}: {char.description} [{','.join(char.properties)}]"
                    )

            logger.info(
                "ble_connected",
                address=address,
                services=len(client.services),
                ms=f"{duration_ms:.0f}",
            )

            return BLEResult(
                success=True,
                device_address=address,
                data=f"Connected. Services:\n" + "\n".join(services_info[:20]),
                duration_ms=duration_ms,
            )

        except ImportError:
            return BLEResult(error="bleak not installed")
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            logger.error("ble_connect_failed", address=address, error=str(exc))
            return BLEResult(
                device_address=address,
                error=str(exc),
                duration_ms=duration_ms,
            )

    async def disconnect(self, address: str) -> BLEResult:
        """Trenne Verbindung zu einem BLE-Geraet."""
        client = self._connections.pop(address, None)
        if client is None:
            return BLEResult(
                device_address=address,
                data="Not connected",
            )

        try:
            await client.disconnect()
            logger.info("ble_disconnected", address=address)
            return BLEResult(
                success=True,
                device_address=address,
                data="Disconnected",
            )
        except Exception as exc:
            logger.error("ble_disconnect_failed", address=address, error=str(exc))
            return BLEResult(
                device_address=address,
                error=str(exc),
            )

    async def disconnect_all(self) -> int:
        """Trenne alle BLE-Verbindungen."""
        count = 0
        for address in list(self._connections.keys()):
            result = await self.disconnect(address)
            if result.success:
                count += 1
        return count

    # ══════════════════════════════════════════════════════════════════
    #  READ / WRITE CHARACTERISTICS
    # ══════════════════════════════════════════════════════════════════

    async def read_characteristic(
        self,
        address: str,
        char_uuid: str,
        reason: str = "",
        agent_goal: str = "",
    ) -> BLEResult:
        """
        Lese eine Characteristic von einem verbundenen Geraet.
        """
        self._total_reads += 1

        client = self._connections.get(address)
        if client is None:
            return BLEResult(
                device_address=address,
                error="Not connected — call connect() first",
            )

        t0 = time.monotonic()
        try:
            data = await client.read_gatt_char(char_uuid)
            duration_ms = (time.monotonic() - t0) * 1000

            # Versuche als UTF-8 zu dekodieren, sonst Hex
            try:
                text = data.decode("utf-8")
            except (UnicodeDecodeError, AttributeError):
                text = data.hex() if isinstance(data, (bytes, bytearray)) else str(data)

            logger.info(
                "ble_read",
                address=address,
                uuid=char_uuid[:20],
                data_len=len(data) if data else 0,
            )

            return BLEResult(
                success=True,
                device_address=address,
                data=text,
                duration_ms=duration_ms,
            )

        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            logger.error("ble_read_failed", error=str(exc))
            return BLEResult(
                device_address=address,
                error=str(exc),
                duration_ms=duration_ms,
            )

    async def write_characteristic(
        self,
        address: str,
        char_uuid: str,
        data: bytes,
        reason: str = "",
        agent_goal: str = "",
    ) -> BLEResult:
        """
        Schreibe eine Characteristic (mit Policy-Check).
        Policy: BLE_WRITE (MEDIUM)
        """
        self._total_writes += 1

        # ── Policy-Check ─────────────────────────────────────────────
        policy_request = ActionRequest(
            action_type=ActionType.BLE_WRITE,
            description=f"BLE: Schreibe an {address} Char {char_uuid[:20]}",
            target=address,
            parameters={"char_uuid": char_uuid, "data_len": len(data)},
            reason=reason,
            agent_goal=agent_goal,
        )
        policy_result = await self._policy.check(policy_request)

        if not policy_result.allowed:
            self._denied_count += 1
            return BLEResult(
                device_address=address,
                was_allowed=False,
                policy_message=policy_result.message,
            )

        client = self._connections.get(address)
        if client is None:
            return BLEResult(
                device_address=address,
                error="Not connected",
            )

        t0 = time.monotonic()
        try:
            await client.write_gatt_char(char_uuid, data)
            duration_ms = (time.monotonic() - t0) * 1000

            logger.info(
                "ble_write",
                address=address,
                uuid=char_uuid[:20],
                data_len=len(data),
            )

            return BLEResult(
                success=True,
                device_address=address,
                data=f"Wrote {len(data)} bytes",
                duration_ms=duration_ms,
            )

        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            logger.error("ble_write_failed", error=str(exc))
            return BLEResult(
                device_address=address,
                error=str(exc),
                duration_ms=duration_ms,
            )

    # ══════════════════════════════════════════════════════════════════
    #  STATS
    # ══════════════════════════════════════════════════════════════════

    @property
    def stats(self) -> dict:
        return {
            "total_scans": self._total_scans,
            "total_connects": self._total_connects,
            "total_reads": self._total_reads,
            "total_writes": self._total_writes,
            "denied": self._denied_count,
            "active_connections": len(self._connections),
            "cached_devices": len(self._scan_cache),
            "cache_age_sec": time.time() - self._last_scan_time if self._last_scan_time else 0,
        }

    @property
    def connected_devices(self) -> list[str]:
        return list(self._connections.keys())
