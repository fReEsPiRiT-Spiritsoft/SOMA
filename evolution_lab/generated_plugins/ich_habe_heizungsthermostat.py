"""Plugin: ich_habe_heizungsthermostat"""
__version__ = "0.2.0"
__author__ = "soma-ai"
__description__ = "Steuert ein Bluetooth-Heizungsthermostat in der Küche (BLE/GATT)"

import asyncio
import logging
import re
from typing import Optional

logger = logging.getLogger("soma.plugin.heizungsthermostat")

# ── Konfiguration ────────────────────────────────────────────────────────
# MAC-Adresse wird beim ersten Scan gespeichert.
# Kann manuell überschrieben werden falls nötig.
_cached_mac: Optional[str] = None

# Typische BLE-Namen von Heizungsthermostaten (case-insensitive Suche)
# "Smart Life" ist der App-Name — das BLE-Gerät heißt oft anders!
THERMOSTAT_NAME_PATTERNS = [
    "smart life", "smartlife",
    "comet", "comet blue",          # Eurotronic Comet Blue
    "eq-3", "eq3", "cc-rt",         # eQ-3 / Homematic
    "trv",                          # Generic Thermostatic Radiator Valve
    "ble_thermo", "thermostat",
    "heizung",
    "tuya",                         # Tuya-basierte BLE Geräte
    "moes",                         # MOES BLE Thermostate
    "brt-100",                      # MOES BRT-100
    "sht",                          # Shelly TRV
]

# BLE GATT: Bekannte Characteristic UUIDs für Temperatur-Steuerung
# (Hauptsächlich für Comet Blue / eQ-3 — wird beim Pairing ggf. angepasst)
GATT_TEMP_WRITE_UUID = "47e9ee01-47e9-11e4-8939-164230d1df67"  # Comet Blue
GATT_TEMP_READ_UUID = "47e9ee03-47e9-11e4-8939-164230d1df67"   # Comet Blue

SCAN_TIMEOUT_SEC = 12
CMD_TIMEOUT_SEC = 10


async def on_load() -> None:
    logger.info("Heizungsthermostat-Plugin geladen (v%s)", __version__)


async def _run_bt(
    *args: str, timeout: float = CMD_TIMEOUT_SEC,
) -> tuple[int, str, str]:
    """Wrapper für bluetoothctl-Kommandos mit Timeout."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "bluetoothctl", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(
            proc.communicate(), timeout=timeout,
        )
        return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return -1, "", "Timeout"
    except FileNotFoundError:
        return -1, "", "bluetoothctl nicht gefunden. Ist bluez installiert?"
    except Exception as e:
        return -1, "", str(e)


async def _scan_for_thermostat() -> Optional[str]:
    """
    BLE-Scan durchführen und nach Thermostat suchen.
    Gibt die MAC-Adresse zurück oder None.
    """
    global _cached_mac
    if _cached_mac:
        return _cached_mac

    logger.info("Starte BLE-Scan (%ds)...", SCAN_TIMEOUT_SEC)

    # Scan starten (mit Timeout)
    proc = await asyncio.create_subprocess_exec(
        "bluetoothctl", "--timeout", str(SCAN_TIMEOUT_SEC), "scan", "on",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await asyncio.wait_for(proc.communicate(), timeout=SCAN_TIMEOUT_SEC + 5)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass

    # Jetzt alle Geräte auflisten (gepairt + neu entdeckte)
    rc, out, err = await _run_bt("devices")
    if rc != 0:
        logger.error("devices list failed: %s", err)
        return None

    # Geräte nach Thermostat-Namensmustern durchsuchen
    found_devices = []
    for line in out.splitlines():
        line_stripped = line.strip()
        if not line_stripped.startswith("Device "):
            continue
        parts = line_stripped.split(maxsplit=2)
        if len(parts) < 3:
            continue
        mac = parts[1]
        name = parts[2]
        found_devices.append((mac, name))
        name_lower = name.lower()
        for pattern in THERMOSTAT_NAME_PATTERNS:
            if pattern in name_lower:
                logger.info("Thermostat gefunden: %s (%s)", name, mac)
                _cached_mac = mac
                return mac

    if found_devices:
        device_list = ", ".join(f"{n} ({m})" for m, n in found_devices)
        logger.info("Geräte gefunden aber kein Thermostat: %s", device_list)

    return None


async def _find_mac() -> Optional[str]:
    """Versuche MAC zu finden: Cache → Paired → BLE-Scan."""
    global _cached_mac
    if _cached_mac:
        return _cached_mac

    # 1. Bereits gepaarte Geräte prüfen
    rc, out, _ = await _run_bt("devices", "Paired")
    if rc == 0:
        for line in out.splitlines():
            parts = line.strip().split(maxsplit=2)
            if len(parts) < 3:
                continue
            name_lower = parts[2].lower()
            for pattern in THERMOSTAT_NAME_PATTERNS:
                if pattern in name_lower:
                    _cached_mac = parts[1]
                    logger.info("Thermostat (paired): %s (%s)", parts[2], _cached_mac)
                    return _cached_mac

    # 2. BLE-Scan
    return await _scan_for_thermostat()


async def execute(action: str = "status", value: str = None) -> str:
    """
    Haupteinstiegspunkt für SOMA.

    Actions:
      - scan:       BLE-Scan durchführen und alle Geräte listen
      - status:     Verbindungsstatus prüfen
      - connect:    Mit Thermostat verbinden
      - disconnect: Verbindung trennen
      - pair:       Thermostat pairen (erstmalig)
      - set_temp:   Temperatur setzen (value="21.5")
      - set_mac:    MAC-Adresse manuell setzen (value="AA:BB:CC:DD:EE:FF")
    """
    try:
        action = (action or "status").strip().lower()

        if action == "scan":
            return await _action_scan()
        elif action == "set_mac":
            return await _action_set_mac(value)
        elif action == "status":
            return await _action_status()
        elif action == "connect":
            return await _action_connect()
        elif action == "disconnect":
            return await _action_disconnect()
        elif action == "pair":
            return await _action_pair()
        elif action == "set_temp":
            return await _action_set_temp(value)
        else:
            return (
                f"Unbekannte Aktion '{action}'. "
                "Verfügbar: scan, status, connect, disconnect, pair, set_temp, set_mac"
            )
    except Exception as e:
        logger.error("Plugin-Fehler: %s", e)
        return f"Fehler: {e}"


async def _action_scan() -> str:
    """BLE-Scan und alle gefundenen Geräte auflisten."""
    logger.info("BLE-Scan gestartet...")

    proc = await asyncio.create_subprocess_exec(
        "bluetoothctl", "--timeout", str(SCAN_TIMEOUT_SEC), "scan", "on",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await asyncio.wait_for(proc.communicate(), timeout=SCAN_TIMEOUT_SEC + 5)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass

    rc, out, err = await _run_bt("devices")
    if rc != 0:
        return f"Scan fehlgeschlagen: {err}"

    devices = []
    thermostat_mac = None
    for line in out.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) < 3 or not parts[0] == "Device":
            continue
        mac, name = parts[1], parts[2]
        marker = ""
        name_lower = name.lower()
        for pattern in THERMOSTAT_NAME_PATTERNS:
            if pattern in name_lower:
                marker = " ← Thermostat?"
                thermostat_mac = mac
                break
        devices.append(f"  {name} ({mac}){marker}")

    if not devices:
        return "Keine Bluetooth-Geräte in Reichweite gefunden."

    result = f"{len(devices)} Geräte gefunden:\n" + "\n".join(devices)
    if thermostat_mac:
        global _cached_mac
        _cached_mac = thermostat_mac
        result += f"\n\nThermostat erkannt! MAC gespeichert: {thermostat_mac}"
    else:
        result += (
            "\n\nKein Thermostat automatisch erkannt. "
            "Du kannst die MAC manuell setzen mit: set_mac value='AA:BB:CC:DD:EE:FF'"
        )
    return result


async def _action_set_mac(value: str) -> str:
    """MAC-Adresse manuell setzen."""
    global _cached_mac
    if not value:
        if _cached_mac:
            return f"Aktuelle MAC: {_cached_mac}"
        return "Keine MAC gesetzt. Bitte angeben: set_mac value='AA:BB:CC:DD:EE:FF'"

    mac = value.strip().upper()
    if not re.match(r'^([0-9A-F]{2}:){5}[0-9A-F]{2}$', mac):
        return f"Ungültige MAC-Adresse: '{value}'. Format: AA:BB:CC:DD:EE:FF"

    _cached_mac = mac
    return f"MAC-Adresse gesetzt: {mac}"


async def _action_status() -> str:
    """Verbindungsstatus beim Thermostat prüfen."""
    mac = await _find_mac()
    if not mac:
        return (
            "Thermostat nicht gefunden. Bitte zuerst 'scan' ausführen, "
            "oder MAC manuell setzen mit set_mac."
        )

    rc, out, err = await _run_bt("info", mac)
    if rc != 0:
        return f"Status-Abfrage fehlgeschlagen: {err}"

    info_lower = out.lower()
    connected = "connected: yes" in info_lower
    paired = "paired: yes" in info_lower
    name_match = re.search(r'name:\s*(.+)', out, re.IGNORECASE)
    name = name_match.group(1).strip() if name_match else mac

    status_parts = [f"Gerät: {name} ({mac})"]
    status_parts.append(f"Gepairt: {'Ja' if paired else 'Nein'}")
    status_parts.append(f"Verbunden: {'Ja' if connected else 'Nein'}")

    return " | ".join(status_parts)


async def _action_connect() -> str:
    """Mit Thermostat verbinden."""
    mac = await _find_mac()
    if not mac:
        return "Thermostat nicht gefunden. Führe zuerst 'scan' oder 'pair' aus."

    rc, out, err = await _run_bt("connect", mac, timeout=15)
    if rc == 0 and "connection successful" in out.lower():
        return f"Verbunden mit Thermostat ({mac})."
    return f"Verbindung fehlgeschlagen: {(err or out).strip()}"


async def _action_disconnect() -> str:
    """Verbindung trennen."""
    mac = _cached_mac
    if not mac:
        return "Keine MAC bekannt — nichts zu trennen."

    rc, out, err = await _run_bt("disconnect", mac)
    if rc == 0:
        return "Verbindung getrennt."
    return f"Trennen fehlgeschlagen: {err}"


async def _action_pair() -> str:
    """Thermostat pairen (erstmalige Einrichtung)."""
    mac = await _find_mac()
    if not mac:
        return (
            "Thermostat nicht im Scan gefunden. "
            "Stelle sicher dass es eingeschaltet und in Reichweite ist, "
            "dann versuche 'scan' erneut."
        )

    # Schon gepairt?
    rc, out, _ = await _run_bt("info", mac)
    if rc == 0 and "paired: yes" in out.lower():
        return f"Thermostat ({mac}) ist bereits gepairt."

    # Pairing
    rc, out, err = await _run_bt("pair", mac, timeout=20)
    if rc == 0 and "pairing successful" in out.lower():
        # Trust setzen damit Auto-Connect funktioniert
        await _run_bt("trust", mac)
        return f"Thermostat ({mac}) erfolgreich gepairt und vertraut!"
    return f"Pairing fehlgeschlagen: {(err or out).strip()}"


async def _action_set_temp(value: str) -> str:
    """
    Temperatur setzen via BLE GATT Write.

    Unterstützt Comet Blue / eQ-3 / Tuya BLE Thermostate.
    Temperatur als Grad Celsius (z.B. "21.5").
    """
    if not value:
        return "Bitte Temperatur angeben, z.B. set_temp value='21.5'"

    # Temperatur parsen
    try:
        temp = float(value.replace(",", "."))
    except ValueError:
        return f"Ungültige Temperatur: '{value}'. Bitte Zahl angeben (z.B. 21.5)"

    if temp < 4.5 or temp > 30.0:
        return f"Temperatur {temp}°C außerhalb des Bereichs (4.5–30.0°C)."

    mac = await _find_mac()
    if not mac:
        return "Thermostat nicht gefunden. Erst 'scan' oder 'pair' ausführen."

    # Sicherstellen dass verbunden
    rc, out, _ = await _run_bt("info", mac)
    if rc != 0 or "connected: yes" not in out.lower():
        # Verbinden
        rc2, out2, err2 = await _run_bt("connect", mac, timeout=15)
        if rc2 != 0 or "connection successful" not in out2.lower():
            return f"Konnte nicht verbinden: {(err2 or out2).strip()}"
        # Kurz warten bis GATT Services geladen
        await asyncio.sleep(2)

    # Comet Blue: Temperatur = Wert * 2 (halbe Grad Schritte)
    temp_byte = int(temp * 2)
    hex_value = f"0x{temp_byte:02x}"

    # GATT Write versuchen
    rc, out, err = await _run_bt(
        "gatt.write", GATT_TEMP_WRITE_UUID, hex_value,
        timeout=10,
    )

    if rc == 0:
        return f"Temperatur auf {temp}°C gesetzt."

    # Fallback: Über gatttool (falls bluetoothctl GATT nicht kann)
    try:
        proc = await asyncio.create_subprocess_exec(
            "gatttool", "-b", mac, "--char-write-req",
            "-a", "0x0041",  # Typisches Handle für Temperatur
            "-n", f"{temp_byte:02x}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out2, err2 = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            return f"Temperatur auf {temp}°C gesetzt (via gatttool)."
    except (FileNotFoundError, asyncio.TimeoutError):
        pass
    except Exception as gatt_err:
        logger.debug("gatttool fallback failed: %s", gatt_err)

    return (
        f"GATT-Write fehlgeschlagen. Das Thermostat verwendet möglicherweise "
        f"ein anderes Protokoll. Versuche die MAC ({mac}) und den Gerätenamen "
        f"zu prüfen mit 'scan' und 'status'.\n"
        f"Fehler: {(err or out).strip()}"
    )


async def on_unload() -> None:
    """Cleanup: Verbindung trennen falls noch aktiv."""
    if _cached_mac:
        try:
            await _run_bt("disconnect", _cached_mac, timeout=3)
        except Exception:
            pass