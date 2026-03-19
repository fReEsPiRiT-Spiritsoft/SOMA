"""
SOMA Bluetooth Audio — Classic Bluetooth via bluetoothctl
==========================================================
Verwaltet klassische Bluetooth-Geräte (Lautsprecher, Kopfhörer, Controller)
über bluetoothctl. Für BLE-Sensoren siehe bluetooth.py (bleak).

Multi-Step-Flow:
  1. ensure_powered() — Adapter einschalten + Service sicherstellen
  2. scan()           — Geräte suchen (10s Scan)
  3. list_devices()   — Alle sichtbaren/bekannten Geräte auflisten
  4. pair(mac)        — Pairen
  5. connect(mac)     — Verbinden
  6. disconnect(mac)  — Trennen
  7. remove(mac)      — Entkoppeln

Hinweis: bluetoothctl ist interaktiv — wir nutzen timeout + stdin-close
         um nicht hängen zu bleiben.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Optional

import structlog

logger = structlog.get_logger("soma.bluetooth_audio")

# Timeout für bluetoothctl-Befehle
CMD_TIMEOUT = 15.0
SCAN_DURATION = 10.0


@dataclass
class BTDevice:
    """Erkanntes Bluetooth-Gerät."""
    mac: str
    name: str
    paired: bool = False
    connected: bool = False
    trusted: bool = False
    rssi: Optional[int] = None
    icon: str = ""           # z.B. "audio-card", "input-gaming"

    def display(self, index: int = 0) -> str:
        """Human-readable Darstellung für Sprachausgabe."""
        status = []
        if self.connected:
            status.append("verbunden")
        elif self.paired:
            status.append("gepairt")
        label = f" ({', '.join(status)})" if status else ""
        icon = "🔊" if "audio" in self.icon else "🎮" if "gaming" in self.icon or "input" in self.icon else "📱"
        if index > 0:
            return f"{index}. {icon} {self.name}{label}"
        return f"{icon} {self.name} [{self.mac}]{label}"


async def _bt_cmd(cmd: str, timeout: float = CMD_TIMEOUT) -> str:
    """
    Führt einen bluetoothctl-Befehl aus und gibt stdout zurück.
    Nutzt echo + pipe um interactive mode zu umgehen.
    """
    full_cmd = f"echo -e '{cmd}\\nquit' | bluetoothctl 2>/dev/null"
    try:
        proc = await asyncio.create_subprocess_shell(
            full_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        output = stdout.decode("utf-8", errors="replace")
        # ANSI escape codes entfernen
        output = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', output)
        return output.strip()
    except asyncio.TimeoutError:
        logger.warning("bt_cmd_timeout", cmd=cmd[:40])
        try:
            proc.kill()
        except Exception:
            pass
        return ""
    except Exception as exc:
        logger.error("bt_cmd_error", cmd=cmd[:40], error=str(exc))
        return ""


async def _bt_scan_background(duration: float = SCAN_DURATION) -> str:
    """
    Startet einen Scan für `duration` Sekunden und gibt danach
    die Liste der gefundenen Geräte zurück.
    """
    full_cmd = (
        f"bluetoothctl --timeout {int(duration)} scan on 2>/dev/null & "
        f"sleep {int(duration)} && "
        f"bluetoothctl scan off 2>/dev/null; "
        f"bluetoothctl devices 2>/dev/null"
    )
    try:
        proc = await asyncio.create_subprocess_shell(
            full_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=duration + 10
        )
        output = stdout.decode("utf-8", errors="replace")
        output = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', output)
        return output.strip()
    except asyncio.TimeoutError:
        logger.warning("bt_scan_timeout")
        return await _bt_cmd("devices")
    except Exception as exc:
        logger.error("bt_scan_error", error=str(exc))
        return ""


def _parse_device_line(line: str) -> Optional[BTDevice]:
    """Parst eine 'Device XX:XX:XX:XX:XX:XX Name' Zeile."""
    m = re.match(r'Device\s+([0-9A-Fa-f:]{17})\s+(.+)', line.strip())
    if m:
        return BTDevice(mac=m.group(1), name=m.group(2).strip())
    return None


async def _enrich_device(dev: BTDevice) -> BTDevice:
    """Holt Detail-Infos für ein Gerät (paired/connected/icon)."""
    info = await _bt_cmd(f"info {dev.mac}")
    if "Paired: yes" in info:
        dev.paired = True
    if "Connected: yes" in info:
        dev.connected = True
    if "Trusted: yes" in info:
        dev.trusted = True
    icon_match = re.search(r'Icon:\s+(\S+)', info)
    if icon_match:
        dev.icon = icon_match.group(1)
    rssi_match = re.search(r'RSSI:\s+(-?\d+)', info)
    if rssi_match:
        dev.rssi = int(rssi_match.group(1))
    return dev


# ═══════════════════════════════════════════════════════════════════
#  PUBLIC API
# ═══════════════════════════════════════════════════════════════════

async def ensure_powered() -> str:
    """Stellt sicher dass der Bluetooth-Adapter eingeschaltet ist."""
    output = await _bt_cmd("show")
    if "Powered: yes" in output:
        logger.info("bt_already_powered")
        return "Bluetooth ist bereits eingeschaltet."

    # Adapter einschalten
    await _bt_cmd("power on")
    # Verify
    output = await _bt_cmd("show")
    if "Powered: yes" in output:
        logger.info("bt_powered_on")
        return "Bluetooth wurde eingeschaltet."
    else:
        logger.error("bt_power_on_failed")
        return "Fehler: Bluetooth konnte nicht eingeschaltet werden. Ist der bluetooth-Service aktiv?"


async def scan_devices(duration: float = SCAN_DURATION) -> list[BTDevice]:
    """
    Scannt nach Bluetooth-Geräten und gibt eine angereicherte Liste zurück.
    """
    # Sicherstellen dass BT an ist
    await ensure_powered()

    logger.info("bt_scan_start", duration=duration)
    raw = await _bt_scan_background(duration)

    # Alle Geräte parsen
    devices: list[BTDevice] = []
    seen_macs: set[str] = set()
    for line in raw.splitlines():
        dev = _parse_device_line(line)
        if dev and dev.mac not in seen_macs:
            seen_macs.add(dev.mac)
            devices.append(dev)

    # Detail-Infos holen (parallel)
    if devices:
        enriched = await asyncio.gather(*[_enrich_device(d) for d in devices])
        devices = list(enriched)

    # Sortieren: verbundene zuerst, dann gepaarte, dann nach Name
    devices.sort(key=lambda d: (not d.connected, not d.paired, d.name.lower()))

    logger.info("bt_scan_complete", found=len(devices))
    return devices


async def list_known_devices() -> list[BTDevice]:
    """Listet alle bekannten (gepairten) Geräte auf — ohne neuen Scan."""
    raw = await _bt_cmd("devices")

    devices: list[BTDevice] = []
    seen_macs: set[str] = set()
    for line in raw.splitlines():
        dev = _parse_device_line(line)
        if dev and dev.mac not in seen_macs:
            seen_macs.add(dev.mac)
            devices.append(dev)

    if devices:
        enriched = await asyncio.gather(*[_enrich_device(d) for d in devices])
        devices = list(enriched)

    devices.sort(key=lambda d: (not d.connected, not d.paired, d.name.lower()))
    return devices


async def pair_device(mac: str) -> str:
    """Pairt mit einem Gerät."""
    logger.info("bt_pair_start", mac=mac)
    output = await _bt_cmd(f"pair {mac}", timeout=20.0)
    if "Pairing successful" in output or "Already paired" in output:
        # Auch gleich trusten damit Auto-Connect klappt
        await _bt_cmd(f"trust {mac}")
        logger.info("bt_pair_success", mac=mac)
        return "Pairing erfolgreich."
    elif "not available" in output.lower():
        return "Gerät nicht erreichbar. Ist es im Pairing-Modus?"
    else:
        logger.warning("bt_pair_result", mac=mac, output=output[:200])
        return f"Pairing-Ergebnis: {output[:150]}"


async def connect_device(mac: str) -> str:
    """Verbindet mit einem (ggf. schon gepairten) Gerät."""
    logger.info("bt_connect_start", mac=mac)

    # Prüfe ob schon verbunden
    info = await _bt_cmd(f"info {mac}")
    if "Connected: yes" in info:
        name_match = re.search(r'Name:\s+(.+)', info)
        name = name_match.group(1).strip() if name_match else mac
        return f"{name} ist bereits verbunden."

    # Wenn noch nicht gepairt → erst pairen
    if "Paired: yes" not in info:
        pair_result = await pair_device(mac)
        if "erfolgreich" not in pair_result.lower() and "Already" not in pair_result:
            return f"Pairing fehlgeschlagen: {pair_result}"

    # Verbinden
    output = await _bt_cmd(f"connect {mac}", timeout=20.0)
    if "Connection successful" in output or "Connected: yes" in output:
        # Trust für Auto-Reconnect
        await _bt_cmd(f"trust {mac}")
        name_match = re.search(r'Name:\s+(.+)', info) if info else None
        name = name_match.group(1).strip() if name_match else mac
        logger.info("bt_connect_success", mac=mac)
        return f"Verbunden mit {name}!"
    elif "not available" in output.lower():
        return "Gerät nicht erreichbar. Bitte nochmal einschalten."
    else:
        logger.warning("bt_connect_result", mac=mac, output=output[:200])
        return f"Verbindungsversuch: {output[:150]}"


async def disconnect_device(mac: str) -> str:
    """Trennt die Verbindung zu einem Gerät."""
    output = await _bt_cmd(f"disconnect {mac}")
    if "Successful" in output or "not connected" in output.lower():
        logger.info("bt_disconnect", mac=mac)
        return "Bluetooth-Verbindung getrennt."
    return f"Trennung: {output[:100]}"


async def remove_device(mac: str) -> str:
    """Entfernt ein gepairtes Gerät komplett."""
    output = await _bt_cmd(f"remove {mac}")
    if "removed" in output.lower() or "not available" in output.lower():
        logger.info("bt_removed", mac=mac)
        return "Gerät wurde entkoppelt und entfernt."
    return f"Entfernung: {output[:100]}"


def find_device_by_name(
    devices: list[BTDevice], search: str
) -> Optional[BTDevice]:
    """
    Findet ein Gerät per (Teil-)Name — case-insensitive.
    Unterstützt auch Nummer-Auswahl ("1", "2", ...).
    """
    search_lower = search.strip().lower()

    # Nummer-Auswahl: "1", "2", etc.
    if search_lower.isdigit():
        idx = int(search_lower) - 1
        if 0 <= idx < len(devices):
            return devices[idx]
        return None

    # Name-Match: erst exakt, dann enthält
    for dev in devices:
        if dev.name.lower() == search_lower:
            return dev
    for dev in devices:
        if search_lower in dev.name.lower():
            return dev
    # MAC-Match
    for dev in devices:
        if search_lower in dev.mac.lower():
            return dev

    return None


async def full_connect_flow(device_name: str) -> str:
    """
    Kompletter Verbindungsablauf: Scan → Find → Pair → Connect.
    Für den Fall dass der Nutzer direkt sagt "verbinde mit JBL".
    """
    # 1. Scan
    devices = await scan_devices()
    if not devices:
        return "Keine Bluetooth-Geräte gefunden. Ist das Gerät eingeschaltet und im Pairing-Modus?"

    # 2. Find
    target = find_device_by_name(devices, device_name)
    if not target:
        # Gerät nicht gefunden → Liste zurückgeben
        device_list = "\n".join(d.display(i + 1) for i, d in enumerate(devices))
        return (
            f"Ich habe '{device_name}' nicht gefunden. "
            f"Diese Geräte sind verfügbar:\n{device_list}\n"
            f"Welches Gerät soll ich verbinden?"
        )

    # 3. Connect (pair + connect)
    return await connect_device(target.mac)
