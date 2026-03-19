"""
SOMA Desktop Control — Volume, Brightness, Clipboard, Notifications
====================================================================
Auto-detects the right CLI tool for each operation.
Supports PipeWire/PulseAudio/ALSA, Wayland/X11, KDE/GNOME/etc.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from typing import Optional

import structlog

logger = structlog.get_logger("soma.desktop_control")


# ═══════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════


async def _run(cmd: list[str], timeout: float = 10.0) -> tuple[int, str, str]:
    """Execute command, return (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            proc.returncode or 0,
            stdout.decode("utf-8", errors="replace").strip(),
            stderr.decode("utf-8", errors="replace").strip(),
        )
    except asyncio.TimeoutError:
        return (-1, "", "Timeout")
    except Exception as e:
        return (-1, "", str(e))


async def _run_shell(cmd: str, timeout: float = 10.0) -> tuple[int, str, str]:
    """Execute shell command string, return (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            proc.returncode or 0,
            stdout.decode("utf-8", errors="replace").strip(),
            stderr.decode("utf-8", errors="replace").strip(),
        )
    except asyncio.TimeoutError:
        return (-1, "", "Timeout")
    except Exception as e:
        return (-1, "", str(e))


def _which(name: str) -> bool:
    return shutil.which(name) is not None


# ═══════════════════════════════════════════════════════════════════
#  DESKTOP CONTROL
# ═══════════════════════════════════════════════════════════════════


class DesktopControl:
    """Controls desktop features using auto-detected system tools."""

    # ── VOLUME ──────────────────────────────────────────────────────

    async def set_volume(self, level: int) -> str:
        """Set volume to 0-100%. Auto-detects wpctl/pactl/pamixer/amixer."""
        level = max(0, min(100, level))

        if _which("wpctl"):
            vol = round(level / 100.0, 2)
            rc, out, err = await _run(
                ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", str(vol)]
            )
            if rc == 0:
                return f"Lautstärke auf {level}% gesetzt."

        if _which("pactl"):
            rc, out, err = await _run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"]
            )
            if rc == 0:
                return f"Lautstärke auf {level}% gesetzt."

        if _which("pamixer"):
            rc, out, err = await _run(["pamixer", "--set-volume", str(level)])
            if rc == 0:
                return f"Lautstärke auf {level}% gesetzt."

        if _which("amixer"):
            rc, out, err = await _run(["amixer", "set", "Master", f"{level}%"])
            if rc == 0:
                return f"Lautstärke auf {level}% gesetzt."

        return "Kein Audio-Tool verfügbar (wpctl/pactl/pamixer/amixer)."

    async def get_volume(self) -> str:
        """Get current volume level."""
        if _which("wpctl"):
            rc, out, err = await _run_shell("wpctl get-volume @DEFAULT_AUDIO_SINK@")
            if rc == 0 and out:
                # Parse "Volume: 0.50" → "50%"
                try:
                    parts = out.split()
                    for p in parts:
                        try:
                            vol = float(p)
                            return f"Aktuelle Lautstärke: {int(vol * 100)}%"
                        except ValueError:
                            continue
                except Exception:
                    pass
                return f"Aktuelle Lautstärke: {out}"

        if _which("pamixer"):
            rc, out, err = await _run(["pamixer", "--get-volume"])
            if rc == 0:
                return f"Aktuelle Lautstärke: {out}%"

        if _which("pactl"):
            rc, out, err = await _run_shell(
                "pactl get-sink-volume @DEFAULT_SINK@ | grep -oP '\\d+%' | head -1"
            )
            if rc == 0 and out:
                return f"Aktuelle Lautstärke: {out}"

        if _which("amixer"):
            rc, out, err = await _run_shell(
                "amixer get Master | grep -oP '\\d+%' | head -1"
            )
            if rc == 0 and out:
                return f"Aktuelle Lautstärke: {out}"

        return "Konnte Lautstärke nicht auslesen."

    async def mute_toggle(self, mute: Optional[bool] = None) -> str:
        """Toggle or set mute state. mute=True/False or None for toggle."""
        if _which("wpctl"):
            if mute is None:
                rc, _, _ = await _run(
                    ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"]
                )
            else:
                rc, _, _ = await _run(
                    ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@",
                     "1" if mute else "0"]
                )
            if rc == 0:
                if mute is None:
                    return "Stummschaltung umgeschaltet."
                return "Stummgeschaltet." if mute else "Stummschaltung aufgehoben."

        if _which("pactl"):
            if mute is None:
                rc, _, _ = await _run(
                    ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"]
                )
            else:
                rc, _, _ = await _run(
                    ["pactl", "set-sink-mute", "@DEFAULT_SINK@",
                     "1" if mute else "0"]
                )
            if rc == 0:
                if mute is None:
                    return "Stummschaltung umgeschaltet."
                return "Stummgeschaltet." if mute else "Stummschaltung aufgehoben."

        if _which("amixer"):
            rc, _, _ = await _run(["amixer", "set", "Master", "toggle"])
            if rc == 0:
                return "Stummschaltung umgeschaltet."

        return "Kein Audio-Tool für Mute verfügbar."

    # ── BRIGHTNESS ──────────────────────────────────────────────────

    async def set_brightness(self, level: int) -> str:
        """Set screen brightness 0-100%."""
        level = max(1, min(100, level))

        if _which("brightnessctl"):
            rc, out, err = await _run(["brightnessctl", "set", f"{level}%"])
            if rc == 0:
                return f"Helligkeit auf {level}% gesetzt."

        if _which("xbacklight"):
            rc, out, err = await _run(["xbacklight", "-set", str(level)])
            if rc == 0:
                return f"Helligkeit auf {level}% gesetzt."

        if _which("light"):
            rc, out, err = await _run(["light", "-S", str(level)])
            if rc == 0:
                return f"Helligkeit auf {level}% gesetzt."

        return "Kein Helligkeits-Tool verfügbar (brightnessctl/xbacklight). Evtl. Desktop-PC ohne Helligkeitssteuerung?"

    async def get_brightness(self) -> str:
        """Get current brightness level."""
        if _which("brightnessctl"):
            rc, out, err = await _run_shell("brightnessctl -m | cut -d, -f4")
            if rc == 0 and out:
                return f"Aktuelle Helligkeit: {out}"

        if _which("xbacklight"):
            rc, out, err = await _run(["xbacklight", "-get"])
            if rc == 0 and out:
                return f"Aktuelle Helligkeit: {out}%"

        return "Konnte Helligkeit nicht auslesen."

    # ── CLIPBOARD ──────────────────────────────────────────────────

    async def get_clipboard(self) -> str:
        """Read clipboard contents."""
        if _which("wl-paste"):
            rc, out, err = await _run(["wl-paste", "--no-newline"])
            if rc == 0:
                return out[:2000] if out else "(Zwischenablage ist leer)"

        if _which("xclip"):
            rc, out, err = await _run(["xclip", "-selection", "clipboard", "-o"])
            if rc == 0:
                return out[:2000] if out else "(Zwischenablage ist leer)"

        if _which("xsel"):
            rc, out, err = await _run(["xsel", "--clipboard", "--output"])
            if rc == 0:
                return out[:2000] if out else "(Zwischenablage ist leer)"

        return "Kein Clipboard-Tool verfügbar (wl-paste/xclip/xsel)."

    async def set_clipboard(self, content: str) -> str:
        """Write text to clipboard."""
        if _which("wl-copy"):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "wl-copy",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(
                    proc.communicate(input=content.encode("utf-8")), timeout=5.0
                )
                if proc.returncode == 0:
                    return f"In Zwischenablage kopiert ({len(content)} Zeichen)."
            except Exception as e:
                logger.debug("clipboard_wl_copy_error", error=str(e))

        if _which("xclip"):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "xclip", "-selection", "clipboard",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(
                    proc.communicate(input=content.encode("utf-8")), timeout=5.0
                )
                if proc.returncode == 0:
                    return f"In Zwischenablage kopiert ({len(content)} Zeichen)."
            except Exception as e:
                logger.debug("clipboard_xclip_error", error=str(e))

        return "Kein Clipboard-Tool verfügbar (wl-copy/xclip)."

    # ── NOTIFICATIONS ──────────────────────────────────────────────

    async def send_notification(
        self, title: str, message: str,
        urgency: str = "normal", icon: str = ""
    ) -> str:
        """Send a desktop notification."""
        if _which("notify-send"):
            cmd = ["notify-send", "--urgency", urgency]
            if icon:
                cmd.extend(["--icon", icon])
            cmd.extend([title, message])
            rc, _, err = await _run(cmd)
            if rc == 0:
                return f"Benachrichtigung gesendet: {title}"
            return f"Notification-Fehler: {err}"

        if _which("kdialog"):
            rc, _, err = await _run(
                ["kdialog", "--passivepopup", f"{title}: {message}", "5"]
            )
            if rc == 0:
                return f"Benachrichtigung gesendet: {title}"

        return "Kein Benachrichtigungs-Tool verfügbar (notify-send)."

    # ── SCREEN LOCK ──────────────────────────────────────────────

    async def lock_screen(self) -> str:
        """Lock the screen."""
        if _which("loginctl"):
            rc, _, _ = await _run(["loginctl", "lock-session"])
            if rc == 0:
                return "Bildschirm gesperrt."

        if _which("qdbus"):
            rc, _, _ = await _run_shell(
                "qdbus org.freedesktop.ScreenSaver /ScreenSaver Lock"
            )
            if rc == 0:
                return "Bildschirm gesperrt."

        if _which("xdg-screensaver"):
            rc, _, _ = await _run(["xdg-screensaver", "lock"])
            if rc == 0:
                return "Bildschirm gesperrt."

        return "Konnte Bildschirm nicht sperren."

    # ── WALLPAPER ──────────────────────────────────────────────────

    async def set_wallpaper(self, path: str) -> str:
        """Set desktop wallpaper."""
        path = os.path.expanduser(path)
        if not os.path.isfile(path):
            return f"Datei nicht gefunden: {path}"

        abs_path = os.path.abspath(path)

        # KDE Plasma via qdbus
        if _which("qdbus"):
            script = (
                'var allDesktops = desktops();'
                'for (i=0;i<allDesktops.length;i++) {'
                '  d = allDesktops[i];'
                '  d.wallpaperPlugin = "org.kde.image";'
                '  d.currentConfigGroup = Array("Wallpaper","org.kde.image","General");'
                f'  d.writeConfig("Image","file://{abs_path}");'
                '}'
            )
            rc, _, _ = await _run_shell(
                f"qdbus org.kde.plasmashell /PlasmaShell "
                f"org.kde.PlasmaShell.evaluateScript '{script}'"
            )
            if rc == 0:
                return f"Hintergrund gesetzt: {path}"

        # GNOME/Cinnamon via gsettings
        if _which("gsettings"):
            rc, _, _ = await _run([
                "gsettings", "set", "org.gnome.desktop.background",
                "picture-uri", f"file://{abs_path}"
            ])
            if rc == 0:
                # Also set dark variant
                await _run([
                    "gsettings", "set", "org.gnome.desktop.background",
                    "picture-uri-dark", f"file://{abs_path}"
                ])
                return f"Hintergrund gesetzt: {path}"

        # Sway
        if _which("swaymsg"):
            rc, _, _ = await _run_shell(
                f"swaymsg output '*' bg '{abs_path}' fill"
            )
            if rc == 0:
                return f"Hintergrund gesetzt: {path}"

        # feh (X11 generic)
        if _which("feh"):
            rc, _, _ = await _run(["feh", "--bg-fill", abs_path])
            if rc == 0:
                return f"Hintergrund gesetzt: {path}"

        return "Kein Tool zum Setzen des Hintergrunds verfügbar."

    # ── AUDIO INPUT (Microphone) ────────────────────────────────────

    async def set_mic_volume(self, level: int) -> str:
        """Set microphone volume 0-100%."""
        level = max(0, min(100, level))

        if _which("wpctl"):
            vol = round(level / 100.0, 2)
            rc, _, _ = await _run(
                ["wpctl", "set-volume", "@DEFAULT_AUDIO_SOURCE@", str(vol)]
            )
            if rc == 0:
                return f"Mikrofon-Lautstärke auf {level}% gesetzt."

        if _which("pactl"):
            rc, _, _ = await _run(
                ["pactl", "set-source-volume", "@DEFAULT_SOURCE@", f"{level}%"]
            )
            if rc == 0:
                return f"Mikrofon-Lautstärke auf {level}% gesetzt."

        return "Konnte Mikrofon-Lautstärke nicht setzen."

    async def mute_mic(self, mute: Optional[bool] = None) -> str:
        """Toggle or set microphone mute."""
        if _which("wpctl"):
            if mute is None:
                rc, _, _ = await _run(
                    ["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "toggle"]
                )
            else:
                rc, _, _ = await _run(
                    ["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@",
                     "1" if mute else "0"]
                )
            if rc == 0:
                return "Mikrofon stummgeschaltet." if mute else "Mikrofon aktiviert."

        if _which("pactl"):
            if mute is None:
                rc, _, _ = await _run(
                    ["pactl", "set-source-mute", "@DEFAULT_SOURCE@", "toggle"]
                )
            else:
                rc, _, _ = await _run(
                    ["pactl", "set-source-mute", "@DEFAULT_SOURCE@",
                     "1" if mute else "0"]
                )
            if rc == 0:
                return "Mikrofon stummgeschaltet." if mute else "Mikrofon aktiviert."

        return "Konnte Mikrofon nicht stummschalten."


# ═══════════════════════════════════════════════════════════════════
#  SINGLETON
# ═══════════════════════════════════════════════════════════════════

_instance: Optional[DesktopControl] = None


def get_desktop_control() -> DesktopControl:
    """Get or create DesktopControl singleton."""
    global _instance
    if _instance is None:
        _instance = DesktopControl()
    return _instance
