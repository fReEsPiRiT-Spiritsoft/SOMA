"""Plugin: mausklicks_apps_bildschirm
Wayland-kompatible Maus- & Tastatur-Automation für KDE Plasma 6.

Verwendet ydotool (https://github.com/ReimuNotMoe/ydotool).
Funktioniert auf Wayland UND X11, da ydotool über /dev/uinput arbeitet.

=== SETUP (einmalig) ===
  sudo pacman -S ydotool
  sudo usermod -aG input $USER    # Dann AUSLOGGEN und wieder EINLOGGEN!
  systemctl --user enable --now ydotoold.service
========================

Unterstützte Aktionen:
  left_click, right_click, double_click, middle_click
  move_to(x, y), move_by(dx, dy)
  scroll_up, scroll_down
  type_text(text)
  key_press(key)  — z.B. "enter", "ctrl+c", "alt+F4"
  drag(x1, y1, x2, y2)
  setup_check — Prüft ob alles korrekt installiert ist
"""
__version__ = "1.0.0"
__author__ = "soma-ai"
__description__ = "Wayland-kompatible Maus- & Tastatur-Automation (ydotool)"

import asyncio
import logging
import shutil
import os

logger = logging.getLogger("soma.plugin.mausklicks")

# ydotool Maus-Button-Codes (Linux evdev)
_BTN_LEFT = "0x00"      # BTN_LEFT
_BTN_RIGHT = "0x01"     # BTN_RIGHT
_BTN_MIDDLE = "0x02"    # BTN_MIDDLE


async def on_load() -> None:
    """Plugin geladen — prüfe ydotool-Verfügbarkeit."""
    ydotool_path = shutil.which("ydotool")
    if ydotool_path:
        logger.info("mausklick_plugin_loaded ydotool=%s", ydotool_path)
    else:
        logger.warning(
            "mausklick_plugin_no_ydotool — "
            "Installiere: sudo pacman -S ydotool && "
            "sudo usermod -aG input $USER && "
            "systemctl --user enable --now ydotoold"
        )


async def _run(args: list[str]) -> tuple[bool, str]:
    """ydotool-Befehl ausführen."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ydotool", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        if proc.returncode == 0:
            return True, stdout.decode().strip()
        else:
            err = stderr.decode().strip() or stdout.decode().strip()
            return False, f"ydotool Fehler (rc={proc.returncode}): {err}"
    except FileNotFoundError:
        return False, (
            "ydotool nicht gefunden! Installiere mit:\n"
            "  sudo pacman -S ydotool\n"
            "  sudo usermod -aG input $USER\n"
            "  systemctl --user enable --now ydotoold"
        )
    except asyncio.TimeoutError:
        return False, "ydotool Timeout (>10s)"
    except Exception as e:
        return False, f"Fehler: {e}"


async def execute(
    action: str = "left_click",
    x: int = 0, y: int = 0,
    dx: int = 0, dy: int = 0,
    x1: int = 0, y1: int = 0,
    x2: int = 0, y2: int = 0,
    text: str = "",
    key: str = "",
    amount: int = 3,
    **kwargs,
) -> str:
    """
    Maus- & Tastatur-Aktionen ausführen.

    Aktionen:
      left_click     — Linksklick an aktueller Position
      right_click    — Rechtsklick
      middle_click   — Mittelklick
      double_click   — Doppelklick (links)
      move_to        — Maus auf absolute Position (x, y) bewegen
      move_by        — Maus relativ bewegen (dx, dy)
      scroll_up      — Nach oben scrollen (amount=Zeilen)
      scroll_down    — Nach unten scrollen (amount=Zeilen)
      type_text      — Text tippen (text="Hello World")
      key_press      — Taste drücken (key="enter", "ctrl+c", "alt+F4")
      drag           — Drag von (x1,y1) nach (x2,y2)
      click_at       — Maus bewegen UND klicken (x, y)
      setup_check    — Prüft Installation
    """
    action = action.lower().strip()

    # ── Setup-Check ───────────────────────────────────────────────────
    if action == "setup_check":
        return await _setup_check()

    # ── Klicks ────────────────────────────────────────────────────────
    if action == "left_click":
        ok, msg = await _run(["click", _BTN_LEFT])
        return "Linksklick ausgeführt" if ok else msg

    if action == "right_click":
        ok, msg = await _run(["click", _BTN_RIGHT])
        return "Rechtsklick ausgeführt" if ok else msg

    if action == "middle_click":
        ok, msg = await _run(["click", _BTN_MIDDLE])
        return "Mittelklick ausgeführt" if ok else msg

    if action == "double_click":
        ok1, _ = await _run(["click", _BTN_LEFT])
        await asyncio.sleep(0.05)
        ok2, msg = await _run(["click", _BTN_LEFT])
        return "Doppelklick ausgeführt" if (ok1 and ok2) else msg

    # ── Klick an Position ─────────────────────────────────────────────
    if action == "click_at":
        ok1, msg1 = await _run(["mousemove", "--absolute", "-x", str(x), "-y", str(y)])
        if not ok1:
            return msg1
        await asyncio.sleep(0.05)
        ok2, msg2 = await _run(["click", _BTN_LEFT])
        return f"Klick auf ({x}, {y}) ausgeführt" if ok2 else msg2

    # ── Mausbewegung ──────────────────────────────────────────────────
    if action == "move_to":
        ok, msg = await _run(["mousemove", "--absolute", "-x", str(x), "-y", str(y)])
        return f"Maus auf ({x}, {y}) bewegt" if ok else msg

    if action == "move_by":
        ok, msg = await _run(["mousemove", "-x", str(dx), "-y", str(dy)])
        return f"Maus um ({dx}, {dy}) verschoben" if ok else msg

    # ── Scrollen ──────────────────────────────────────────────────────
    if action == "scroll_up":
        ok, msg = await _run(["mousemove", "-w", "-y", str(-abs(amount))])
        return f"Nach oben gescrollt ({amount} Zeilen)" if ok else msg

    if action == "scroll_down":
        ok, msg = await _run(["mousemove", "-w", "-y", str(abs(amount))])
        return f"Nach unten gescrollt ({amount} Zeilen)" if ok else msg

    # ── Tastatur ──────────────────────────────────────────────────────
    if action == "type_text":
        if not text:
            return "Kein Text angegeben (text='...')"
        ok, msg = await _run(["type", "--clearmodifiers", "--", text])
        return f"Text getippt: '{text[:50]}'" if ok else msg

    if action == "key_press":
        if not key:
            return "Keine Taste angegeben (key='...')"
        ok, msg = await _run(["key", key])
        return f"Taste gedrückt: {key}" if ok else msg

    # ── Drag & Drop ───────────────────────────────────────────────────
    if action == "drag":
        # Zum Startpunkt bewegen
        ok, msg = await _run(["mousemove", "--absolute", "-x", str(x1), "-y", str(y1)])
        if not ok:
            return f"Drag Startposition fehlgeschlagen: {msg}"
        await asyncio.sleep(0.1)
        # Maus-Down (button down flag: 0x80 = 128)
        ok, msg = await _run(["click", _BTN_LEFT, "-D", "128"])
        if not ok:
            return f"Drag Down fehlgeschlagen: {msg}"
        await asyncio.sleep(0.1)
        # Zum Endpunkt bewegen
        ok, msg = await _run(["mousemove", "--absolute", "-x", str(x2), "-y", str(y2)])
        if not ok:
            return f"Drag Bewegung fehlgeschlagen: {msg}"
        await asyncio.sleep(0.1)
        # Maus-Up (button up flag: 0x00 = 0)
        ok, msg = await _run(["click", _BTN_LEFT, "-D", "0"])
        return f"Drag von ({x1},{y1}) nach ({x2},{y2}) ausgeführt" if ok else msg

    return (
        f"Unbekannte Aktion: '{action}'. "
        "Verfügbar: left_click, right_click, double_click, middle_click, "
        "click_at, move_to, move_by, scroll_up, scroll_down, "
        "type_text, key_press, drag, setup_check"
    )


async def _setup_check() -> str:
    """Prüfe ob ydotool korrekt installiert und nutzbar ist."""
    lines: list[str] = []

    # 1) Binary vorhanden?
    path = shutil.which("ydotool")
    if path:
        lines.append(f"✓ ydotool gefunden: {path}")
    else:
        lines.append("✗ ydotool NICHT gefunden!")
        lines.append("  → sudo pacman -S ydotool")
        return "\n".join(lines)

    # 2) Daemon läuft?
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "--user", "is-active", "ydotoold",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        status = stdout.decode().strip()
        if status == "active":
            lines.append("✓ ydotoold Daemon läuft")
        else:
            lines.append(f"✗ ydotoold Daemon: {status}")
            lines.append("  → systemctl --user enable --now ydotoold")
    except Exception:
        lines.append("✗ ydotoold Status konnte nicht geprüft werden")

    # 3) uinput-Zugriff?
    uinput_writable = os.access("/dev/uinput", os.W_OK)
    if uinput_writable:
        lines.append("✓ /dev/uinput beschreibbar")
    else:
        lines.append("✗ /dev/uinput NICHT beschreibbar")
        lines.append("  → sudo usermod -aG input $USER  (dann ausloggen!)")

    # 4) Quick-Test
    ok, msg = await _run(["mousemove", "-x", "0", "-y", "0"])
    if ok:
        lines.append("✓ ydotool Testbefehl erfolgreich")
    else:
        lines.append(f"✗ ydotool Test fehlgeschlagen: {msg}")

    return "\n".join(lines)


async def on_unload() -> None:
    """Plugin entladen."""
    pass