"""
SOMA App & Window Control — Open, Close, Focus, Minimize, Maximize
===================================================================
Controls applications and window management across
KDE Plasma, GNOME, Sway, Hyprland and X11.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Optional

import structlog

logger = structlog.get_logger("soma.app_control")


# ═══════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════


async def _run_shell(cmd: str, timeout: float = 10.0) -> tuple[int, str, str]:
    """Execute shell command, return (returncode, stdout, stderr)."""
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
#  APP ALIASES — Common names → executable candidates
# ═══════════════════════════════════════════════════════════════════

APP_ALIASES: dict[str, list[str]] = {
    # Generic categories
    "browser": ["firefox", "chromium", "google-chrome-stable", "brave", "vivaldi"],
    "editor": ["kate", "gedit", "code", "nvim"],
    "terminal": ["konsole", "gnome-terminal", "alacritty", "kitty", "wezterm"],
    "dateimanager": ["dolphin", "nautilus", "thunar", "nemo"],
    "filemanager": ["dolphin", "nautilus", "thunar", "nemo"],
    "files": ["dolphin", "nautilus", "thunar", "nemo"],
    "musik": ["elisa", "rhythmbox", "spotify", "vlc"],
    "music": ["elisa", "rhythmbox", "spotify", "vlc"],
    "video": ["mpv", "vlc", "celluloid", "totem"],
    "bild": ["gwenview", "eog", "feh"],
    "image": ["gwenview", "eog", "feh"],
    "pdf": ["okular", "evince", "zathura"],
    "rechner": ["kcalc", "gnome-calculator", "galculator", "speedcrunch"],
    "calculator": ["kcalc", "gnome-calculator", "galculator", "speedcrunch"],
    "settings": ["systemsettings", "gnome-control-center"],
    "einstellungen": ["systemsettings", "gnome-control-center"],
    "systemmonitor": ["plasma-systemmonitor", "gnome-system-monitor", "btop"],
    "taskmanager": ["plasma-systemmonitor", "gnome-system-monitor"],
    # Specific apps
    "firefox": ["firefox"],
    "chrome": ["google-chrome-stable", "chromium", "chromium-browser"],
    "chromium": ["chromium", "chromium-browser"],
    "brave": ["brave", "brave-browser"],
    "dolphin": ["dolphin"],
    "nautilus": ["nautilus"],
    "konsole": ["konsole"],
    "kate": ["kate"],
    "spotify": ["spotify"],
    "discord": ["discord"],
    "steam": ["steam"],
    "gimp": ["gimp"],
    "libreoffice": ["libreoffice"],
    "writer": ["libreoffice", "--writer"],
    "calc": ["libreoffice", "--calc"],
    "thunderbird": ["thunderbird"],
    "obs": ["obs"],
    "blender": ["blender"],
    "vscode": ["code"],
    "code": ["code"],
    "vlc": ["vlc"],
    "mpv": ["mpv"],
    "inkscape": ["inkscape"],
    "krita": ["krita"],
    "kdenlive": ["kdenlive"],
    "okular": ["okular"],
    "telegram": ["telegram-desktop", "telegram"],
    "signal": ["signal-desktop"],
    "slack": ["slack"],
    "teams": ["teams-for-linux", "teams"],
    "zoom": ["zoom"],
}


# ═══════════════════════════════════════════════════════════════════
#  APP CONTROL
# ═══════════════════════════════════════════════════════════════════


class AppControl:
    """Control applications and windows."""

    # ── APP LAUNCH ──────────────────────────────────────────────────

    async def open_app(self, name: str, args: str = "") -> str:
        """Open an application by name or alias."""
        name_lower = name.lower().strip()

        # Check alias mapping
        candidates = APP_ALIASES.get(name_lower, [name_lower])

        for cmd in candidates:
            if _which(cmd):
                full_cmd = f"nohup {cmd} {args} >/dev/null 2>&1 &"
                rc, _, err = await _run_shell(full_cmd)
                if rc == 0:
                    return f"'{cmd}' gestartet."
                logger.debug("app_open_failed", cmd=cmd, err=err)

        # Fallback: try xdg-open for paths/URLs
        if _which("xdg-open") and ("/" in name or "." in name or ":" in name):
            rc, _, err = await _run_shell(
                f"nohup xdg-open '{name}' >/dev/null 2>&1 &"
            )
            if rc == 0:
                return f"'{name}' mit xdg-open geöffnet."

        installed = [c for c in candidates if _which(c)]
        if not installed:
            return (
                f"App '{name}' nicht gefunden. Keiner dieser Befehle ist "
                f"installiert: {', '.join(candidates[:5])}"
            )
        return f"Konnte '{name}' nicht starten."

    # ── APP CLOSE ──────────────────────────────────────────────────

    async def close_app(self, name: str, force: bool = False) -> str:
        """Close an application by name."""
        name_lower = name.lower().strip()
        candidates = APP_ALIASES.get(name_lower, [name_lower])

        for cmd_name in candidates:
            # Check if running
            rc, out, _ = await _run_shell(f"pgrep -f '{cmd_name}' | head -5")
            if rc == 0 and out:
                if force:
                    rc, _, err = await _run_shell(f"pkill -9 -f '{cmd_name}'")
                else:
                    rc, _, err = await _run_shell(f"pkill -f '{cmd_name}'")
                if rc == 0:
                    return f"'{cmd_name}' geschlossen."
                return f"Fehler beim Schließen: {err}"

        return f"Kein laufender Prozess für '{name}' gefunden."

    # ── APP LIST ──────────────────────────────────────────────────

    async def list_running_apps(self) -> str:
        """List running graphical applications."""
        # Try wmctrl first (shows window titles)
        if _which("wmctrl"):
            rc, out, _ = await _run_shell("wmctrl -l | head -30")
            if rc == 0 and out:
                return f"Offene Fenster:\n{out}"

        # Fallback: detect running GUI processes
        known_apps = "|".join([
            "firefox", "chrom", "kate", "dolphin", "konsole", "code",
            "spotify", "discord", "vlc", "mpv", "steam", "gimp", "obs",
            "thunderbird", "libreoffice", "nautilus", "thunar", "gedit",
            "blender", "inkscape", "krita", "kdenlive", "telegram",
            "signal", "slack", "okular", "gwenview", "elisa", "evince",
            "alacritty", "kitty", "brave", "vivaldi",
        ])
        rc, out, _ = await _run_shell(
            f"ps aux | grep -iE '({known_apps})' | grep -v grep "
            f"| awk '{{print $11}}' | sort -u | head -25"
        )
        if out:
            return f"Laufende Apps:\n{out}"

        return "Konnte laufende Apps nicht ermitteln."

    # ── WINDOW MANAGEMENT ──────────────────────────────────────────

    async def list_windows(self) -> str:
        """List open windows with titles."""
        if _which("wmctrl"):
            rc, out, _ = await _run_shell("wmctrl -l -p")
            if rc == 0 and out:
                return out

        if _which("xdotool"):
            rc, out, _ = await _run_shell(
                "xdotool search --onlyvisible --name '' "
                "getwindowname 2>/dev/null | head -20"
            )
            if rc == 0 and out:
                return out

        # KDE specific
        if _which("qdbus"):
            rc, out, _ = await _run_shell(
                "qdbus org.kde.KWin /KWin "
                "org.kde.KWin.queryWindowInfo 2>/dev/null | head -30"
            )
            if rc == 0 and out:
                return out

        return "Fensterliste nicht verfügbar (wmctrl/xdotool fehlt)."

    async def focus_window(self, name: str) -> str:
        """Focus/raise a window by title or app name."""
        if _which("wmctrl"):
            rc, _, err = await _run_shell(f"wmctrl -a '{name}'")
            if rc == 0:
                return f"Fenster '{name}' fokussiert."

        if _which("xdotool"):
            rc, wid, _ = await _run_shell(
                f"xdotool search --name '{name}' | head -1"
            )
            if rc == 0 and wid:
                rc, _, _ = await _run_shell(f"xdotool windowactivate {wid}")
                if rc == 0:
                    return f"Fenster '{name}' fokussiert."

        return f"Konnte Fenster '{name}' nicht fokussieren."

    async def minimize_window(self, name: str = "") -> str:
        """Minimize a window by name or the active one."""
        if _which("xdotool"):
            if name:
                rc, wid, _ = await _run_shell(
                    f"xdotool search --name '{name}' | head -1"
                )
                if rc == 0 and wid:
                    rc, _, _ = await _run_shell(f"xdotool windowminimize {wid}")
                    if rc == 0:
                        return f"Fenster '{name}' minimiert."
            else:
                rc, _, _ = await _run_shell(
                    "xdotool windowminimize $(xdotool getactivewindow)"
                )
                if rc == 0:
                    return "Aktives Fenster minimiert."

        if _which("wmctrl") and name:
            rc, _, _ = await _run_shell(f"wmctrl -r '{name}' -b add,hidden")
            if rc == 0:
                return f"Fenster '{name}' minimiert."

        return "Fenster minimieren nicht möglich (wmctrl/xdotool fehlt)."

    async def maximize_window(self, name: str = "") -> str:
        """Maximize a window by name or the active one."""
        if _which("wmctrl"):
            target = f"'{name}'" if name else ":ACTIVE:"
            rc, _, _ = await _run_shell(
                f"wmctrl -r {target} -b add,maximized_vert,maximized_horz"
            )
            if rc == 0:
                label = name or "Aktives Fenster"
                return f"{label} maximiert."

        if _which("xdotool") and name:
            rc, wid, _ = await _run_shell(
                f"xdotool search --name '{name}' | head -1"
            )
            if rc == 0 and wid:
                rc, _, _ = await _run_shell(
                    f"wmctrl -i -r {wid} -b add,maximized_vert,maximized_horz"
                )
                if rc == 0:
                    return f"Fenster '{name}' maximiert."

        return "Fenster maximieren nicht möglich."

    async def close_window(self, name: str = "") -> str:
        """Close a window gracefully by name or the active one."""
        if _which("wmctrl"):
            target = f"'{name}'" if name else ":ACTIVE:"
            rc, _, _ = await _run_shell(f"wmctrl -c {target}")
            if rc == 0:
                label = name or "Aktives Fenster"
                return f"{label} geschlossen."

        if _which("xdotool"):
            if name:
                rc, wid, _ = await _run_shell(
                    f"xdotool search --name '{name}' | head -1"
                )
                if rc == 0 and wid:
                    rc, _, _ = await _run_shell(f"xdotool windowclose {wid}")
                    if rc == 0:
                        return f"Fenster '{name}' geschlossen."
            else:
                rc, _, _ = await _run_shell(
                    "xdotool windowclose $(xdotool getactivewindow)"
                )
                if rc == 0:
                    return "Aktives Fenster geschlossen."

        return "Fenster schließen nicht möglich."


# ═══════════════════════════════════════════════════════════════════
#  SINGLETON
# ═══════════════════════════════════════════════════════════════════

_instance: Optional[AppControl] = None


def get_app_control() -> AppControl:
    """Get or create AppControl singleton."""
    global _instance
    if _instance is None:
        _instance = AppControl()
    return _instance
