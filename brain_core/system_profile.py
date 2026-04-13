"""
SOMA System Profile — Dynamic System Detection
================================================
Detects OS, desktop environment, display server, package manager,
audio system, and available CLI tools at boot time.

Used by:
  - logic_router.py: Dynamic system context in LLM prompts
  - executive_arm modules: Auto-select correct tool for each operation
  - onboarding.py: Store system info in long-term memory
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger("soma.system_profile")

# ═══════════════════════════════════════════════════════════════════
#  TOOL DETECTION REGISTRY
# ═══════════════════════════════════════════════════════════════════

TOOL_CATEGORIES: dict[str, list[str]] = {
    "clipboard_copy": ["wl-copy", "xclip", "xsel"],
    "clipboard_paste": ["wl-paste", "xclip", "xsel"],
    "screenshot": ["spectacle", "grim", "scrot", "gnome-screenshot", "flameshot", "maim"],
    "brightness": ["brightnessctl", "xbacklight", "light"],
    "audio_ctl": ["wpctl", "pactl", "pamixer", "amixer"],
    "media_ctl": ["playerctl"],
    "network": ["nmcli", "iwctl", "ip", "ss"],
    "bluetooth": ["bluetoothctl"],
    "file_manager": ["dolphin", "nautilus", "thunar", "nemo", "pcmanfm"],
    "terminal": ["konsole", "gnome-terminal", "alacritty", "kitty", "wezterm", "xterm"],
    "browser": ["firefox", "chromium", "google-chrome-stable", "brave", "vivaldi"],
    "editor": ["kate", "gedit", "nano", "vim", "nvim", "code"],
    "media_player": ["mpv", "vlc", "celluloid", "totem"],
    "system_monitor": ["btop", "htop", "top"],
    "archive": ["tar", "zip", "unzip", "7z"],
    "image_viewer": ["gwenview", "eog", "feh", "sxiv", "imv"],
    "pdf_viewer": ["okular", "evince", "zathura"],
    "notification": ["notify-send", "zenity", "kdialog"],
    "power": ["loginctl", "systemctl"],
    "process": ["ps", "pgrep", "pkill", "kill", "killall"],
    "disk": ["df", "du", "lsblk"],
    "window_mgmt": ["wmctrl", "xdotool", "qdbus", "kdotool", "swaymsg", "hyprctl"],
    "download": ["curl", "wget", "aria2c", "yt-dlp"],
    "ocr": ["tesseract"],
    "opener": ["xdg-open", "kde-open"],
    "package_mgr": ["pacman", "apt", "dnf", "zypper", "emerge"],
    "aur_helper": ["paru", "yay", "pikaur", "trizen"],
}

# ═══════════════════════════════════════════════════════════════════
#  SYSTEM PROFILE DATA
# ═══════════════════════════════════════════════════════════════════


@dataclass
class SystemProfile:
    """Complete detected system profile."""

    # OS
    os_name: str = ""
    os_id: str = ""
    os_version: str = ""
    os_base: str = ""           # "arch", "debian", "fedora", "suse"
    kernel: str = ""
    arch: str = ""
    hostname: str = ""

    # Desktop
    desktop_env: str = ""
    display_server: str = ""    # "wayland" or "x11"

    # Package Manager
    package_manager: str = ""   # "pacman", "apt", "dnf", "zypper"
    aur_helper: str = ""        # "yay", "paru", ""

    # Audio
    audio_system: str = ""      # "pipewire", "pulseaudio", "alsa"

    # Init
    init_system: str = ""       # "systemd", "openrc"

    # Shell & User
    default_shell: str = ""
    username: str = ""
    home_dir: str = ""

    # Available tools per category
    available_tools: dict[str, list[str]] = field(default_factory=dict)

    # XDG User Directories (locale-dependent: Desktop→Schreibtisch etc.)
    xdg_dirs: dict[str, str] = field(default_factory=dict)

    # ── Helpers ──────────────────────────────────────────────────

    def has_tool(self, name: str) -> bool:
        """Check if a specific tool is available on the system."""
        return shutil.which(name) is not None

    def best_tool(self, category: str) -> Optional[str]:
        """Get the first (best) available tool from a category."""
        tools = self.available_tools.get(category, [])
        return tools[0] if tools else None

    def as_prompt_context(self) -> str:
        """Format profile as compact LLM system prompt context."""
        lines = [
            "SYSTEM-PROFIL DES HOST-COMPUTERS:",
            f"• OS: {self.os_name} ({self.os_base})",
            f"• Kernel: {self.kernel} ({self.arch})",
            f"• Desktop: {self.desktop_env} auf {self.display_server.upper()}",
            f"• Audio: {self.audio_system}",
            f"• Paketmanager: {self.package_manager}"
            + (f" + AUR: {self.aur_helper}" if self.aur_helper else ""),
            f"• Shell: {self.default_shell}",
            f"• User: {self.username} | Home: {self.home_dir}",
            f"• Hostname: {self.hostname}",
        ]

        # XDG User Directories (kritisch für Shell-Befehle!)
        if self.xdg_dirs:
            xdg_parts = [f"{k}={v}" for k, v in self.xdg_dirs.items()]
            lines.append(f"• Verzeichnisse: {', '.join(xdg_parts)}")
            lines.append(
                "WICHTIG: Auf diesem System heißt der Desktop-Ordner "
                f"'{self.xdg_dirs.get('DESKTOP', 'Desktop')}', "
                "NICHT 'Desktop'! Benutze IMMER die echten Pfade oben."
            )

        # Key tool availability (compact)
        highlights = []
        for cat in ["audio_ctl", "clipboard_copy", "screenshot", "brightness",
                     "notification", "window_mgmt", "media_ctl", "browser",
                     "terminal", "file_manager"]:
            best = self.best_tool(cat)
            if best:
                highlights.append(f"{cat}={best}")

        if highlights:
            lines.append(f"• Tools: {', '.join(highlights)}")

        return "\n".join(lines)

    def as_onboarding_summary(self) -> str:
        """Human-readable summary for onboarding greeting."""
        parts = [self.os_name]
        if self.desktop_env:
            parts.append(self.desktop_env)
        if self.display_server:
            parts.append(self.display_server.capitalize())
        if self.audio_system:
            parts.append(self.audio_system.capitalize())
        return " mit ".join(parts)

    def as_dict(self) -> dict:
        """Serialize profile to a JSON-safe dictionary."""
        return {
            "os_name": self.os_name,
            "os_id": self.os_id,
            "os_version": self.os_version,
            "os_base": self.os_base,
            "kernel": self.kernel,
            "arch": self.arch,
            "hostname": self.hostname,
            "desktop_env": self.desktop_env,
            "display_server": self.display_server,
            "package_manager": self.package_manager,
            "aur_helper": self.aur_helper,
            "audio_system": self.audio_system,
            "init_system": self.init_system,
            "default_shell": self.default_shell,
            "username": self.username,
            "home_dir": self.home_dir,
            "available_tools": self.available_tools,
            "xdg_dirs": self.xdg_dirs,
        }


# ═══════════════════════════════════════════════════════════════════
#  DETECTION LOGIC
# ═══════════════════════════════════════════════════════════════════


async def _run_cmd(cmd: str, timeout: float = 5.0) -> str:
    """Run shell command and return stdout (empty on error)."""
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def _parse_os_release() -> dict[str, str]:
    """Parse /etc/os-release into a dict."""
    info: dict[str, str] = {}
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if "=" in line:
                    key, _, val = line.strip().partition("=")
                    info[key] = val.strip('"')
    except FileNotFoundError:
        pass
    return info


def _detect_os_base(os_id: str, id_like: str) -> str:
    """Detect the base distribution family."""
    combined = f"{os_id} {id_like}".lower()
    if "arch" in combined:
        return "arch"
    elif "debian" in combined or "ubuntu" in combined:
        return "debian"
    elif "fedora" in combined or "rhel" in combined or "centos" in combined:
        return "fedora"
    elif "suse" in combined or "opensuse" in combined:
        return "suse"
    elif "gentoo" in combined:
        return "gentoo"
    elif "void" in combined:
        return "void"
    elif "nixos" in combined or "nix" in combined:
        return "nix"
    return "unknown"


def _detect_package_manager(os_base: str) -> str:
    """Detect primary package manager."""
    pm_map = {
        "arch": "pacman",
        "debian": "apt",
        "fedora": "dnf",
        "suse": "zypper",
        "gentoo": "emerge",
        "void": "xbps-install",
        "nix": "nix",
    }
    pm = pm_map.get(os_base, "")
    if pm and shutil.which(pm):
        return pm
    # Fallback: check what's available
    for candidate in ["pacman", "apt", "dnf", "zypper", "emerge"]:
        if shutil.which(candidate):
            return candidate
    return ""


def _detect_aur_helper() -> str:
    """Detect AUR helper (Arch-based only)."""
    for helper in ["paru", "yay", "pikaur", "trizen"]:
        if shutil.which(helper):
            return helper
    return ""


async def _detect_audio_system() -> str:
    """Detect audio system."""
    # PipeWire check via pactl
    pw = await _run_cmd("pactl info 2>/dev/null | grep -i 'server name'")
    if "pipewire" in pw.lower():
        return "pipewire"
    if "pulseaudio" in pw.lower():
        return "pulseaudio"

    # wpctl check (WirePlumber = PipeWire)
    wp = await _run_cmd("wpctl status 2>/dev/null | head -1")
    if wp:
        return "pipewire"

    # PulseAudio direct
    if shutil.which("pulseaudio"):
        pa = await _run_cmd("pulseaudio --check 2>&1; echo $?")
        if pa.strip().endswith("0"):
            return "pulseaudio"

    if shutil.which("amixer"):
        return "alsa"

    return "unknown"


def _detect_tools() -> dict[str, list[str]]:
    """Detect all available tools by category."""
    result: dict[str, list[str]] = {}
    for category, candidates in TOOL_CATEGORIES.items():
        found = [t for t in candidates if shutil.which(t)]
        if found:
            result[category] = found
    return result


async def detect_system() -> SystemProfile:
    """
    Run full system detection. Call once at boot.
    Returns a SystemProfile with all detected capabilities.
    """
    logger.info("system_detection_start")

    profile = SystemProfile()

    # ── OS Info ──
    os_info = _parse_os_release()
    profile.os_name = os_info.get("PRETTY_NAME", os_info.get("NAME", platform.system()))
    profile.os_id = os_info.get("ID", "").lower()
    profile.os_version = os_info.get("VERSION_ID", os_info.get("VERSION", ""))
    profile.os_base = _detect_os_base(profile.os_id, os_info.get("ID_LIKE", ""))
    profile.kernel = platform.release()
    profile.arch = platform.machine()
    profile.hostname = platform.node()

    # ── Desktop Environment ──
    de = os.environ.get("XDG_CURRENT_DESKTOP", "")
    session = os.environ.get("DESKTOP_SESSION", "")
    de_lower = f"{de} {session}".lower()

    if "kde" in de_lower or "plasma" in de_lower:
        profile.desktop_env = "KDE Plasma"
    elif "gnome" in de_lower:
        profile.desktop_env = "GNOME"
    elif "xfce" in de_lower:
        profile.desktop_env = "XFCE"
    elif "sway" in de_lower:
        profile.desktop_env = "Sway"
    elif "hyprland" in de_lower:
        profile.desktop_env = "Hyprland"
    elif "cinnamon" in de_lower:
        profile.desktop_env = "Cinnamon"
    elif "mate" in de_lower:
        profile.desktop_env = "MATE"
    elif "lxqt" in de_lower:
        profile.desktop_env = "LXQt"
    elif "budgie" in de_lower:
        profile.desktop_env = "Budgie"
    elif de:
        profile.desktop_env = de
    else:
        profile.desktop_env = session or "unknown"

    # ── Display Server ──
    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if session_type in ("wayland", "x11"):
        profile.display_server = session_type
    elif os.environ.get("WAYLAND_DISPLAY"):
        profile.display_server = "wayland"
    elif os.environ.get("DISPLAY"):
        profile.display_server = "x11"
    else:
        profile.display_server = "unknown"

    # ── Package Manager ──
    profile.package_manager = _detect_package_manager(profile.os_base)
    if profile.os_base == "arch":
        profile.aur_helper = _detect_aur_helper()

    # ── Audio ──
    profile.audio_system = await _detect_audio_system()

    # ── Init System ──
    if shutil.which("systemctl"):
        profile.init_system = "systemd"
    elif shutil.which("rc-service"):
        profile.init_system = "openrc"
    else:
        profile.init_system = "unknown"

    # ── Shell & User ──
    profile.default_shell = os.path.basename(os.environ.get("SHELL", "bash"))
    profile.username = os.environ.get("USER", os.environ.get("LOGNAME", ""))
    profile.home_dir = str(Path.home())

    # ── XDG User Directories ──
    profile.xdg_dirs = await _detect_xdg_dirs()

    # ── Tools ──
    profile.available_tools = _detect_tools()

    logger.info(
        "system_detection_complete",
        os=profile.os_name,
        de=profile.desktop_env,
        display=profile.display_server,
        audio=profile.audio_system,
        pkg=profile.package_manager,
        tools_count=sum(len(v) for v in profile.available_tools.values()),
    )

    return profile


async def _detect_xdg_dirs() -> dict[str, str]:
    """
    Detect XDG user directories (locale-dependent!).
    On German systems: Desktop→Schreibtisch, Documents→Dokumente, etc.
    Uses xdg-user-dir or parses ~/.config/user-dirs.dirs.
    """
    dirs: dict[str, str] = {}
    xdg_keys = [
        "DESKTOP", "DOCUMENTS", "DOWNLOAD", "MUSIC",
        "PICTURES", "VIDEOS", "TEMPLATES", "PUBLICSHARE",
    ]

    # Method 1: xdg-user-dir command (most reliable)
    if shutil.which("xdg-user-dir"):
        for key in xdg_keys:
            path = await _run_cmd(f"xdg-user-dir {key}", timeout=2.0)
            if path and path != str(Path.home()):
                dirs[key] = path
        if dirs:
            return dirs

    # Method 2: Parse ~/.config/user-dirs.dirs
    user_dirs_file = Path.home() / ".config" / "user-dirs.dirs"
    if user_dirs_file.exists():
        try:
            import re
            text = user_dirs_file.read_text()
            for key in xdg_keys:
                match = re.search(
                    rf'^XDG_{key}_DIR="(.+?)"',
                    text,
                    re.MULTILINE,
                )
                if match:
                    val = match.group(1).replace("$HOME", str(Path.home()))
                    if val != str(Path.home()):
                        dirs[key] = val
        except Exception:
            pass

    # Method 3: Fallback — check common German paths
    if not dirs:
        home = Path.home()
        german_map = {
            "DESKTOP": "Schreibtisch",
            "DOCUMENTS": "Dokumente",
            "DOWNLOAD": "Downloads",
            "MUSIC": "Musik",
            "PICTURES": "Bilder",
            "VIDEOS": "Videos",
        }
        for key, name in german_map.items():
            p = home / name
            if p.is_dir():
                dirs[key] = str(p)

    return dirs


def get_xdg_path(key: str) -> str:
    """Get a specific XDG path. Returns empty string if unknown."""
    profile = get_profile()
    return profile.xdg_dirs.get(key.upper(), "")


# ═══════════════════════════════════════════════════════════════════
#  SINGLETON ACCESS
# ═══════════════════════════════════════════════════════════════════

_profile: Optional[SystemProfile] = None


async def init_profile() -> SystemProfile:
    """Initialize system profile (call at boot)."""
    global _profile
    _profile = await detect_system()
    return _profile


def get_profile() -> SystemProfile:
    """
    Get cached profile. Returns minimal profile if not yet detected.
    Safe to call before init_profile().
    """
    global _profile
    if _profile is None:
        _profile = SystemProfile(
            username=os.environ.get("USER", ""),
            home_dir=str(Path.home()),
        )
    return _profile
