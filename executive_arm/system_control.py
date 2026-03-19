"""
SOMA System Control — Processes, Services, Packages, Network, Power
====================================================================
Full system control with dynamic tool detection and sudo support.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Optional

import structlog

logger = structlog.get_logger("soma.system_control")


# ═══════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════


async def _run_shell(cmd: str, timeout: float = 15.0) -> tuple[int, str, str]:
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


# Power actions that always require explicit sudo
DANGEROUS_POWER_ACTIONS = {"shutdown", "reboot", "poweroff", "halt"}


# ═══════════════════════════════════════════════════════════════════
#  SYSTEM CONTROL
# ═══════════════════════════════════════════════════════════════════


class SystemControl:
    """System-level controls: processes, services, packages, network, power."""

    def __init__(self, sudo_enabled: bool = False):
        self.sudo_enabled = sudo_enabled

    # ── PROCESSES ──────────────────────────────────────────────────

    async def process_list(self, filter_text: str = "", top_n: int = 20) -> str:
        """List running processes, optionally filtered."""
        if filter_text:
            rc, out, err = await _run_shell(
                f"ps aux | head -1 && "
                f"ps aux | grep -i '{filter_text}' | grep -v grep | head -n {top_n}"
            )
        else:
            rc, out, err = await _run_shell(
                f"ps aux --sort=-%cpu | head -n {top_n + 1}"
            )

        if rc == 0 and out:
            return out
        return f"Prozessliste nicht verfügbar: {err}"

    async def process_kill(
        self, target: str, force: bool = False
    ) -> str:
        """Kill a process by PID or name."""
        if not target:
            return "Kein Prozess angegeben."

        signal = "-9" if force else "-15"

        if target.isdigit():
            rc, _, err = await _run_shell(f"kill {signal} {target}")
            if rc == 0:
                return f"Prozess {target} beendet."
            if "Operation not permitted" in err and self.sudo_enabled:
                rc, _, err = await _run_shell(f"sudo kill {signal} {target}")
                if rc == 0:
                    return f"Prozess {target} mit sudo beendet."
            return f"Fehler: {err}"
        else:
            rc, _, err = await _run_shell(f"pkill {signal} -f '{target}'")
            if rc == 0:
                return f"Prozess '{target}' beendet."
            if "Operation not permitted" in (err or "") and self.sudo_enabled:
                rc, _, err = await _run_shell(
                    f"sudo pkill {signal} -f '{target}'"
                )
                if rc == 0:
                    return f"Prozess '{target}' mit sudo beendet."
            return f"Konnte '{target}' nicht beenden: {err or 'Prozess nicht gefunden'}"

    # ── SERVICES ──────────────────────────────────────────────────

    async def service_status(self, name: str) -> str:
        """Get status of a systemd service."""
        if not _which("systemctl"):
            return "systemctl nicht verfügbar."

        rc, out, err = await _run_shell(
            f"systemctl status '{name}' 2>&1 | head -20"
        )
        return out if out else f"Service-Status nicht verfügbar: {err}"

    async def service_control(self, name: str, action: str) -> str:
        """Start/stop/restart/enable/disable a systemd service."""
        valid = ("start", "stop", "restart", "enable", "disable", "reload")
        if action not in valid:
            return (
                f"Ungültige Aktion: {action}. "
                f"Erlaubt: {'/'.join(valid)}"
            )

        if not _which("systemctl"):
            return "systemctl nicht verfügbar."

        # First try as user service
        rc, out, err = await _run_shell(
            f"systemctl --user {action} '{name}' 2>&1"
        )
        if rc == 0:
            return f"Service '{name}' {action}: OK (user)"

        # Need sudo for system services
        if self.sudo_enabled:
            rc, out, err = await _run_shell(
                f"sudo systemctl {action} '{name}' 2>&1"
            )
            if rc == 0:
                return f"Service '{name}' {action}: OK (system, sudo)"
            return f"Service-Fehler: {err}"

        return (
            f"Service '{name}' {action} benötigt sudo "
            f"(deaktiviert). Fehler: {err}"
        )

    async def service_list(self, filter_text: str = "") -> str:
        """List systemd services."""
        if filter_text:
            rc, out, _ = await _run_shell(
                f"systemctl list-units --type=service --no-pager "
                f"| grep -i '{filter_text}' | head -25"
            )
        else:
            rc, out, _ = await _run_shell(
                "systemctl list-units --type=service --state=running "
                "--no-pager | head -30"
            )
        return out if out else "Keine Services gefunden."

    # ── PACKAGES ──────────────────────────────────────────────────

    def _detect_pkg_mgr(self) -> str:
        """Detect primary package manager."""
        for pm in ["pacman", "apt", "dnf", "zypper", "emerge"]:
            if _which(pm):
                return pm
        return ""

    def _detect_aur_helper(self) -> str:
        """Detect AUR helper (Arch-based)."""
        for h in ["paru", "yay", "pikaur", "trizen"]:
            if _which(h):
                return h
        return ""

    async def package_search(self, query: str) -> str:
        """Search for available packages."""
        pm = self._detect_pkg_mgr()

        cmd_map = {
            "pacman": f"pacman -Ss '{query}' 2>/dev/null | head -30",
            "apt": f"apt search '{query}' 2>/dev/null | head -30",
            "dnf": f"dnf search '{query}' 2>/dev/null | head -30",
            "zypper": f"zypper search '{query}' 2>/dev/null | head -30",
        }

        cmd = cmd_map.get(pm)
        if not cmd:
            return "Kein Paketmanager erkannt."

        rc, out, err = await _run_shell(cmd, timeout=30.0)
        return out if out else f"Keine Pakete gefunden für: {query}"

    async def package_install(self, name: str) -> str:
        """Install a package (requires sudo)."""
        if not self.sudo_enabled:
            return (
                f"Paket-Installation benötigt Sudo-Modus "
                f"(deaktiviert). Paket: {name}"
            )

        pm = self._detect_pkg_mgr()
        aur = self._detect_aur_helper()

        cmd_map = {
            "pacman": f"sudo pacman -S --noconfirm '{name}'",
            "apt": f"sudo apt install -y '{name}'",
            "dnf": f"sudo dnf install -y '{name}'",
            "zypper": f"sudo zypper install -y '{name}'",
        }

        cmd = cmd_map.get(pm)
        if not cmd:
            return "Kein Paketmanager erkannt."

        logger.info("package_install", package=name, pm=pm)
        rc, out, err = await _run_shell(cmd, timeout=120.0)
        if rc == 0:
            return f"Paket '{name}' installiert."

        # Try AUR helper for Arch if main repo failed
        if pm == "pacman" and aur:
            logger.info("package_install_aur", package=name, helper=aur)
            rc, out, err = await _run_shell(
                f"{aur} -S --noconfirm '{name}'", timeout=180.0
            )
            if rc == 0:
                return f"Paket '{name}' aus AUR installiert."

        return f"Installation fehlgeschlagen: {(err or out)[:500]}"

    async def package_remove(self, name: str) -> str:
        """Remove a package (requires sudo)."""
        if not self.sudo_enabled:
            return (
                f"Paket-Entfernung benötigt Sudo-Modus "
                f"(deaktiviert). Paket: {name}"
            )

        pm = self._detect_pkg_mgr()

        cmd_map = {
            "pacman": f"sudo pacman -R --noconfirm '{name}'",
            "apt": f"sudo apt remove -y '{name}'",
            "dnf": f"sudo dnf remove -y '{name}'",
            "zypper": f"sudo zypper remove -y '{name}'",
        }

        cmd = cmd_map.get(pm)
        if not cmd:
            return "Kein Paketmanager erkannt."

        rc, out, err = await _run_shell(cmd, timeout=60.0)
        if rc == 0:
            return f"Paket '{name}' entfernt."
        return f"Entfernung fehlgeschlagen: {(err or out)[:500]}"

    async def package_list_installed(self, filter_text: str = "") -> str:
        """List installed packages."""
        pm = self._detect_pkg_mgr()

        if pm == "pacman":
            if filter_text:
                rc, out, _ = await _run_shell(
                    f"pacman -Q | grep -i '{filter_text}' | head -30"
                )
            else:
                rc, count, _ = await _run_shell("pacman -Q | wc -l")
                _, recent, _ = await _run_shell(
                    "expac --timefmt='%Y-%m-%d' '%l\\t%n' | sort | tail -15"
                )
                if not recent:
                    _, recent, _ = await _run_shell("pacman -Q | tail -15")
                return (
                    f"Installierte Pakete: {count}\n"
                    f"Zuletzt installiert:\n{recent}"
                )
        elif pm == "apt":
            if filter_text:
                rc, out, _ = await _run_shell(
                    f"apt list --installed 2>/dev/null "
                    f"| grep -i '{filter_text}' | head -30"
                )
            else:
                rc, out, _ = await _run_shell(
                    "dpkg -l | tail -n +6 | wc -l"
                )
                return f"Installierte Pakete: {out}"
        elif pm == "dnf":
            if filter_text:
                rc, out, _ = await _run_shell(
                    f"dnf list installed | grep -i '{filter_text}' | head -30"
                )
            else:
                rc, out, _ = await _run_shell(
                    "dnf list installed | wc -l"
                )
                return f"Installierte Pakete: {out}"
        else:
            return "Paketmanager nicht unterstützt für Auflistung."

        return out if out else "Keine Pakete gefunden."

    # ── NETWORK ──────────────────────────────────────────────────

    async def network_info(self) -> str:
        """Get network interface information."""
        parts = []

        # IP addresses
        rc, out, _ = await _run_shell("ip -br addr | head -10")
        if out:
            parts.append(f"Interfaces:\n{out}")

        # Default route
        rc, out, _ = await _run_shell("ip route | grep default | head -3")
        if out:
            parts.append(f"Standard-Route: {out}")

        # DNS
        rc, out, _ = await _run_shell(
            "cat /etc/resolv.conf | grep nameserver | head -3"
        )
        if out:
            parts.append(f"DNS: {out}")

        # Public IP (quick, non-blocking)
        rc, out, _ = await _run_shell(
            "curl -s --max-time 3 ifconfig.me 2>/dev/null"
        )
        if rc == 0 and out and len(out) < 50:
            parts.append(f"Öffentliche IP: {out}")

        return "\n\n".join(parts) if parts else "Netzwerk-Info nicht verfügbar."

    async def wifi_list(self) -> str:
        """List available WiFi networks."""
        if _which("nmcli"):
            rc, out, err = await _run_shell(
                "nmcli -t -f SSID,SIGNAL,SECURITY device wifi list "
                "2>/dev/null | head -20"
            )
            if rc == 0 and out:
                return f"Verfügbare WLANs:\n{out}"

        if _which("iwctl"):
            rc, out, err = await _run_shell(
                "iwctl station wlan0 get-networks 2>/dev/null | head -20"
            )
            if rc == 0 and out:
                return f"Verfügbare WLANs:\n{out}"

        return "WLAN-Scan nicht möglich (nmcli/iwctl nicht verfügbar)."

    async def wifi_connect(self, ssid: str, password: str = "") -> str:
        """Connect to a WiFi network."""
        if not _which("nmcli"):
            return "nmcli nicht verfügbar für WLAN-Verbindung."

        if password:
            rc, out, err = await _run_shell(
                f"nmcli device wifi connect '{ssid}' password '{password}'"
            )
        else:
            rc, out, err = await _run_shell(
                f"nmcli device wifi connect '{ssid}'"
            )

        if rc == 0:
            return f"Mit WLAN '{ssid}' verbunden."
        return f"WLAN-Verbindung fehlgeschlagen: {err}"

    # ── DISK ──────────────────────────────────────────────────

    async def disk_usage(self) -> str:
        """Get disk usage overview."""
        rc, out, _ = await _run_shell(
            "df -h --output=source,size,used,avail,pcent,target "
            "-x tmpfs -x devtmpfs 2>/dev/null"
        )
        if rc == 0 and out:
            return out

        rc, out, _ = await _run_shell("df -h | head -15")
        return out if out else "Festplatten-Info nicht verfügbar."

    async def memory_usage(self) -> str:
        """Get RAM usage."""
        rc, out, _ = await _run_shell("free -h")
        return out if out else "Speicher-Info nicht verfügbar."

    # ── SYSTEM INFO ──────────────────────────────────────────────

    async def sysinfo(self) -> str:
        """Comprehensive system information."""
        # Try fastfetch/neofetch first
        for tool in ["fastfetch", "neofetch"]:
            if _which(tool):
                flag = "--stdout" if tool == "fastfetch" else "--stdout"
                rc, out, _ = await _run_shell(
                    f"{tool} {flag} 2>/dev/null", timeout=10.0
                )
                if rc == 0 and out:
                    return out[:3000]

        # Manual collection
        parts = []

        rc, out, _ = await _run_shell("uname -a")
        if out:
            parts.append(f"System: {out}")

        rc, out, _ = await _run_shell("uptime -p")
        if out:
            parts.append(f"Uptime: {out}")

        rc, out, _ = await _run_shell("free -h | head -2")
        if out:
            parts.append(f"RAM:\n{out}")

        rc, out, _ = await _run_shell(
            "cat /proc/cpuinfo | grep 'model name' | head -1 | cut -d: -f2"
        )
        if out:
            parts.append(f"CPU:{out.strip()}")

        rc, out, _ = await _run_shell(
            "nproc"
        )
        if out:
            parts.append(f"CPU-Kerne: {out}")

        # GPU
        if _which("nvidia-smi"):
            rc, out, _ = await _run_shell(
                "nvidia-smi --query-gpu=name,memory.total,memory.used "
                "--format=csv,noheader 2>/dev/null"
            )
            if out:
                parts.append(f"GPU: {out}")
        elif _which("lspci"):
            rc, out, _ = await _run_shell("lspci | grep -i 'vga\\|3d'")
            if out:
                parts.append(f"GPU: {out}")

        rc, out, _ = await _run_shell("df -h / | tail -1")
        if out:
            parts.append(f"Disk (/): {out}")

        return "\n".join(parts) if parts else "System-Info nicht verfügbar."

    # ── POWER ──────────────────────────────────────────────────

    async def power_action(self, action: str) -> str:
        """System power actions: lock, suspend, hibernate, reboot, shutdown."""
        action = action.lower().strip()

        if action == "lock":
            if _which("loginctl"):
                rc, _, _ = await _run_shell("loginctl lock-session")
                if rc == 0:
                    return "Bildschirm gesperrt."
            if _which("qdbus"):
                rc, _, _ = await _run_shell(
                    "qdbus org.freedesktop.ScreenSaver /ScreenSaver Lock"
                )
                if rc == 0:
                    return "Bildschirm gesperrt."
            if _which("xdg-screensaver"):
                rc, _, _ = await _run_shell("xdg-screensaver lock")
                if rc == 0:
                    return "Bildschirm gesperrt."
            return "Bildschirm-Sperre nicht möglich."

        elif action in ("suspend", "sleep"):
            # Many systems allow suspend without sudo via polkit
            rc, _, err = await _run_shell("systemctl suspend")
            if rc == 0:
                return "System wird in Ruhezustand versetzt."
            if self.sudo_enabled:
                rc, _, err = await _run_shell("sudo systemctl suspend")
                if rc == 0:
                    return "System wird in Ruhezustand versetzt (sudo)."
            return f"Suspend fehlgeschlagen: {err}"

        elif action == "hibernate":
            if not self.sudo_enabled:
                # Try without sudo first (polkit may allow it)
                rc, _, err = await _run_shell("systemctl hibernate")
                if rc == 0:
                    return "System wird in Tiefschlaf versetzt."
                return "Hibernate benötigt möglicherweise Sudo-Modus."
            rc, _, err = await _run_shell("sudo systemctl hibernate")
            if rc == 0:
                return "System wird in Tiefschlaf versetzt."
            return f"Hibernate-Fehler: {err}"

        elif action in DANGEROUS_POWER_ACTIONS:
            if not self.sudo_enabled:
                return (
                    f"'{action}' benötigt Sudo-Modus "
                    f"(deaktiviert aus Sicherheit). "
                    f"Aktiviere Sudo im Dashboard um fortzufahren."
                )

            if action in ("shutdown", "poweroff", "halt"):
                rc, _, err = await _run_shell("systemctl poweroff")
                if rc == 0:
                    return "System wird heruntergefahren..."
                return f"Shutdown-Fehler: {err}"

            elif action == "reboot":
                rc, _, err = await _run_shell("systemctl reboot")
                if rc == 0:
                    return "System wird neu gestartet..."
                return f"Reboot-Fehler: {err}"

        else:
            return (
                f"Unbekannte Power-Aktion: {action}. "
                f"Erlaubt: lock / suspend / hibernate / reboot / shutdown"
            )

        return f"Power-Aktion '{action}' konnte nicht ausgeführt werden."

    # ── UPTIME ──────────────────────────────────────────────────

    async def uptime(self) -> str:
        """Get system uptime."""
        rc, out, _ = await _run_shell("uptime -p")
        return out if out else "Uptime nicht verfügbar."


# ═══════════════════════════════════════════════════════════════════
#  SINGLETON
# ═══════════════════════════════════════════════════════════════════

_instance: Optional[SystemControl] = None


def get_system_control(sudo: bool = False) -> SystemControl:
    """Get or create SystemControl singleton."""
    global _instance
    if _instance is None or _instance.sudo_enabled != sudo:
        _instance = SystemControl(sudo_enabled=sudo)
    return _instance
