"""
SOMA Smart Media Controller
============================
Erkennt WO gerade Musik/Video läuft und steuert genau DORT.

Nutzt MPRIS2 D-Bus (via playerctl) — funktioniert mit:
  • Firefox/Chromium (YouTube, Spotify Web, SoundCloud, etc.)
  • mpv (SOMA-eigene Audio-Wiedergabe)
  • VLC, Audacious, Spotify Desktop, etc.
  • KDE Plasma Browser Integration

Kernlogik:
  1. detect_active_player() → Findet den Player der gerade spielt/zuletzt spielte
  2. Steuerbefehle (play, pause, next, prev, stop) gehen an genau DIESEN Player
  3. Bei "spiele XY" → wenn bereits ein Player aktiv ist, wird DORT geöffnet

Abhängigkeiten:
  - playerctl (sudo pacman -S playerctl)
  - qdbus6 (KDE-Standard, für Fallback)
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from typing import Optional

import structlog

logger = structlog.get_logger("soma.media_control")

_HAS_PLAYERCTL = shutil.which("playerctl") is not None


# ══════════════════════════════════════════════════════════════════════════
#  Daten-Modelle
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class MediaPlayerInfo:
    """Info über einen laufenden Media Player."""
    name: str               # z.B. "firefox.instance_1_55", "mpv", "vlc"
    status: str             # "Playing", "Paused", "Stopped"
    title: str = ""
    artist: str = ""
    album: str = ""
    length_us: int = 0      # Länge in Mikrosekunden
    position_us: int = 0    # Position in Mikrosekunden
    can_next: bool = False
    can_prev: bool = False
    can_pause: bool = False
    can_play: bool = False
    identity: str = ""      # "Mozilla firefox", "mpv Media Player", etc.

    @property
    def is_playing(self) -> bool:
        return self.status == "Playing"

    @property
    def is_browser(self) -> bool:
        """Ist dieser Player ein Browser (Firefox/Chrome)?"""
        n = self.name.lower()
        return any(b in n for b in ("firefox", "chromium", "chrome", "brave", "plasma-browser"))

    @property
    def is_mpv(self) -> bool:
        return "mpv" in self.name.lower()

    @property
    def friendly_name(self) -> str:
        """Lesbarer Name für TTS-Ausgabe."""
        n = self.name.lower()
        if "firefox" in n or "plasma-browser" in n:
            return "Firefox"
        if "mpv" in n:
            return "mpv"
        if "vlc" in n:
            return "VLC"
        if "spotify" in n:
            return "Spotify"
        if "chromium" in n or "chrome" in n:
            return "Chrome"
        return self.identity or self.name

    @property
    def track_description(self) -> str:
        """Beschreibung des aktuellen Tracks für TTS."""
        parts = []
        if self.artist:
            parts.append(self.artist)
        if self.title:
            parts.append(self.title)
        return " – ".join(parts) if parts else "(unbekannt)"


# ══════════════════════════════════════════════════════════════════════════
#  Player Detection
# ══════════════════════════════════════════════════════════════════════════

async def _run_playerctl(*args: str, timeout: float = 5.0) -> tuple[bool, str]:
    """playerctl-Befehl ausführen."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "playerctl", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode == 0:
            return True, stdout.decode().strip()
        else:
            return False, stderr.decode().strip()
    except FileNotFoundError:
        return False, "playerctl nicht installiert (sudo pacman -S playerctl)"
    except asyncio.TimeoutError:
        return False, "playerctl Timeout"
    except Exception as e:
        return False, str(e)


async def list_players() -> list[MediaPlayerInfo]:
    """Alle registrierten MPRIS Media Player auflisten mit Status."""
    if not _HAS_PLAYERCTL:
        return []

    ok, output = await _run_playerctl("--list-all")
    if not ok or not output:
        return []

    players: list[MediaPlayerInfo] = []
    player_names = [n.strip() for n in output.splitlines() if n.strip()]

    for name in player_names:
        info = await _get_player_info(name)
        if info:
            players.append(info)

    return players


async def _get_player_info(name: str) -> Optional[MediaPlayerInfo]:
    """Details eines einzelnen Players abfragen."""
    # Metadata in einem Aufruf holen
    fmt = (
        "{{status}}\t{{artist}}\t{{title}}\t{{album}}\t"
        "{{mpris:length}}\t{{position}}"
    )
    ok, output = await _run_playerctl("-p", name, "metadata", "--format", fmt)
    if not ok:
        return None

    parts = output.split("\t")
    if len(parts) < 4:
        return None

    status = parts[0] if parts[0] in ("Playing", "Paused", "Stopped") else "Stopped"

    def safe_int(s: str) -> int:
        try:
            return int(s)
        except (ValueError, TypeError):
            return 0

    info = MediaPlayerInfo(
        name=name,
        status=status,
        artist=parts[1] if len(parts) > 1 else "",
        title=parts[2] if len(parts) > 2 else "",
        album=parts[3] if len(parts) > 3 else "",
        length_us=safe_int(parts[4]) if len(parts) > 4 else 0,
        position_us=safe_int(parts[5]) if len(parts) > 5 else 0,
    )

    # Capabilities abfragen (parallel)
    cap_tasks = {
        cap: _run_playerctl("-p", name, "metadata", "--format", f"{{{{{cap}}}}}")
        for cap in []  # Skip — use status checks instead
    }

    # Einfacher: playerctl kann CanGoNext etc. nicht direkt → qdbus6 Fallback
    for prop, attr in [
        ("CanGoNext", "can_next"), ("CanGoPrevious", "can_prev"),
        ("CanPause", "can_pause"), ("CanPlay", "can_play"),
    ]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "qdbus6", f"org.mpris.MediaPlayer2.{name}",
                "/org/mpris/MediaPlayer2",
                f"org.mpris.MediaPlayer2.Player.{prop}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
            setattr(info, attr, stdout.decode().strip().lower() == "true")
        except Exception:
            pass

    return info


async def detect_active_player() -> Optional[MediaPlayerInfo]:
    """
    Finde den Player der gerade am ehesten "aktiv" ist.
    
    Priorität:
      1. Ein Player der gerade spielt (Playing)
      2. Ein Player der pausiert ist (Paused) — zuletzt benutzt
      3. Kein Player aktiv → None
      
    Bei mehreren spielenden: bevorzuge den mit längerem Content (nicht Ads).
    """
    players = await list_players()
    if not players:
        return None

    # Spielende Player zuerst
    playing = [p for p in players if p.is_playing]
    if playing:
        # Bei mehreren: den mit dem "echten" Content bevorzugen
        # (Plasma Browser Integration spiegelt Firefox, bevorzuge den spezifischeren)
        non_plasma = [p for p in playing if "plasma-browser" not in p.name]
        if non_plasma:
            return non_plasma[0]
        return playing[0]

    # Pausierte Player — nehme den mit Content (nicht leeren)
    paused = [p for p in players if p.status == "Paused"]
    if paused:
        with_content = [p for p in paused if p.title or p.artist]
        # Bevorzuge nicht-Plasma
        non_plasma = [p for p in with_content if "plasma-browser" not in p.name]
        if non_plasma:
            return non_plasma[0]
        if with_content:
            return with_content[0]
        return paused[0]

    # Gestoppte als letzter Fallback
    return players[0] if players else None


async def detect_music_player() -> Optional[MediaPlayerInfo]:
    """
    Speziell: Finde den Player der gerade MUSIK abspielt.
    
    Filtert raus:
      - Sehr kurze Medien (< 30s → wahrscheinlich Notification/Ad)
      - Player ohne Titel
    """
    players = await list_players()
    if not players:
        return None

    # Nur Player mit Content > 30 Sekunden
    music_players = [
        p for p in players
        if (p.is_playing or p.status == "Paused")
        and (p.length_us > 30_000_000 or p.length_us == 0)  # > 30s oder unbekannt
        and (p.title or p.artist)
    ]

    if not music_players:
        # Fallback: jeder aktive Player
        active = [p for p in players if p.is_playing or p.status == "Paused"]
        return active[0] if active else None

    # Playing > Paused
    playing = [p for p in music_players if p.is_playing]
    if playing:
        non_plasma = [p for p in playing if "plasma-browser" not in p.name]
        return non_plasma[0] if non_plasma else playing[0]

    # Paused
    non_plasma = [p for p in music_players if "plasma-browser" not in p.name]
    return non_plasma[0] if non_plasma else music_players[0]


# ══════════════════════════════════════════════════════════════════════════
#  Player Control
# ══════════════════════════════════════════════════════════════════════════

async def _control_player(player_name: str, command: str) -> tuple[bool, str]:
    """Sende einen Steuerbefehl an einen bestimmten Player."""
    return await _run_playerctl("-p", player_name, command)


async def _control_player_dbus(player_name: str, method: str) -> tuple[bool, str]:
    """Direkte D-Bus Steuerung als Fallback."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "qdbus6", f"org.mpris.MediaPlayer2.{player_name}",
            "/org/mpris/MediaPlayer2",
            f"org.mpris.MediaPlayer2.Player.{method}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode == 0:
            return True, stdout.decode().strip()
        return False, stderr.decode().strip()
    except Exception as e:
        return False, str(e)


async def media_play_pause(player: Optional[str] = None) -> str:
    """Play/Pause umschalten. Wenn kein Player angegeben → aktiven Player finden."""
    target = None
    if player:
        target = player
    else:
        active = await detect_active_player()
        if active:
            target = active.name

    if not target:
        return "Kein aktiver Media Player gefunden."

    ok, msg = await _control_player(target, "play-pause")
    if not ok:
        # Fallback: D-Bus direkt
        ok, msg = await _control_player_dbus(target, "PlayPause")

    if ok:
        # Neuen Status holen
        info = await _get_player_info(target)
        status = info.status if info else "unbekannt"
        name = info.friendly_name if info else target
        track = info.track_description if info else ""
        logger.info("media_play_pause", player=target, new_status=status)
        if status == "Playing":
            return f"▶️ {name} fortgesetzt" + (f": {track}" if track else "")
        else:
            return f"⏸️ {name} pausiert" + (f": {track}" if track else "")
    return f"Steuerung fehlgeschlagen: {msg}"


async def media_next(player: Optional[str] = None) -> str:
    """Nächstes Lied. Auto-Detection welcher Player."""
    target_info = None
    if player:
        target_info = await _get_player_info(player)
    else:
        target_info = await detect_music_player()

    if not target_info:
        return "Kein aktiver Media Player gefunden für 'Nächstes Lied'."

    # ── Browser: Nächstes Lied auf YouTube? ──────────────────────────
    # YouTube hat kein CanGoNext via MPRIS — nutze Keyboard-Shortcut
    if target_info.is_browser and not target_info.can_next:
        return await _browser_next_track(target_info)

    # ── MPRIS Next ────────────────────────────────────────────────────
    ok, msg = await _control_player(target_info.name, "next")
    if not ok:
        ok, msg = await _control_player_dbus(target_info.name, "Next")

    if ok:
        await asyncio.sleep(0.5)  # Kurz warten bis Metadata aktualisiert
        new_info = await _get_player_info(target_info.name)
        track = new_info.track_description if new_info else ""
        logger.info("media_next", player=target_info.name, new_track=track)
        return f"⏭️ Nächstes Lied auf {target_info.friendly_name}" + (f": {track}" if track else "")

    # Fallback: Browser-Keyboard
    if target_info.is_browser:
        return await _browser_next_track(target_info)

    return f"'Nächstes Lied' fehlgeschlagen: {msg}"


async def media_prev(player: Optional[str] = None) -> str:
    """Vorheriges Lied."""
    target_info = None
    if player:
        target_info = await _get_player_info(player)
    else:
        target_info = await detect_music_player()

    if not target_info:
        return "Kein aktiver Media Player gefunden."

    if target_info.is_browser and not target_info.can_prev:
        return await _browser_prev_track(target_info)

    ok, msg = await _control_player(target_info.name, "previous")
    if not ok:
        ok, msg = await _control_player_dbus(target_info.name, "Previous")

    if ok:
        await asyncio.sleep(0.5)
        new_info = await _get_player_info(target_info.name)
        track = new_info.track_description if new_info else ""
        logger.info("media_prev", player=target_info.name, new_track=track)
        return f"⏮️ Vorheriges Lied auf {target_info.friendly_name}" + (f": {track}" if track else "")

    if target_info.is_browser:
        return await _browser_prev_track(target_info)

    return f"'Vorheriges Lied' fehlgeschlagen: {msg}"


async def media_stop(player: Optional[str] = None) -> str:
    """Wiedergabe stoppen."""
    target = None
    if player:
        target = player
    else:
        active = await detect_active_player()
        if active:
            target = active.name

    if not target:
        # Auch mpv-Prozesse killen als Fallback
        try:
            proc = await asyncio.create_subprocess_exec(
                "pkill", "-f", "mpv",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception:
            pass
        return "Wiedergabe gestoppt."

    ok, _ = await _control_player(target, "stop")
    if not ok:
        ok, _ = await _control_player_dbus(target, "Stop")
    
    logger.info("media_stop", player=target)
    return "⏹️ Wiedergabe gestoppt."


async def media_play(player: Optional[str] = None) -> str:
    """Explizites Play (Resume)."""
    target = None
    if player:
        target = player
    else:
        active = await detect_active_player()
        if active:
            target = active.name

    if not target:
        return "Kein pausierter Media Player gefunden."

    ok, _ = await _control_player(target, "play")
    if not ok:
        ok, _ = await _control_player_dbus(target, "Play")

    if ok:
        info = await _get_player_info(target)
        name = info.friendly_name if info else target
        track = info.track_description if info else ""
        return f"▶️ {name} fortgesetzt" + (f": {track}" if track else "")
    return "Play fehlgeschlagen."


async def media_pause(player: Optional[str] = None) -> str:
    """Explizites Pause."""
    target = None
    if player:
        target = player
    else:
        active = await detect_active_player()
        if active:
            target = active.name

    if not target:
        return "Kein aktiver Media Player zum Pausieren."

    ok, _ = await _control_player(target, "pause")
    if not ok:
        ok, _ = await _control_player_dbus(target, "Pause")

    if ok:
        info = await _get_player_info(target)
        name = info.friendly_name if info else target
        return f"⏸️ {name} pausiert."
    return "Pause fehlgeschlagen."


async def get_now_playing() -> str:
    """Was läuft gerade? Gibt eine menschenlesbare Beschreibung zurück."""
    players = await list_players()
    if not players:
        return "Es läuft gerade keine Musik oder kein Video."

    active = [p for p in players if p.is_playing]
    paused = [p for p in players if p.status == "Paused" and (p.title or p.artist)]

    lines: list[str] = []

    if active:
        for p in active:
            if "plasma-browser" in p.name:
                continue  # Duplikat von Firefox
            lines.append(f"▶️ {p.friendly_name}: {p.track_description}")
    
    if paused:
        for p in paused:
            if "plasma-browser" in p.name:
                continue
            lines.append(f"⏸️ {p.friendly_name} (pausiert): {p.track_description}")

    if not lines:
        return "Es läuft gerade keine Musik oder kein Video."

    return "\n".join(lines)


async def get_active_player_name() -> Optional[str]:
    """
    Für media_play-Routing: Gibt den Namen des aktiven Players zurück,
    damit "spiele XY" im SELBEN Player geöffnet werden kann.
    """
    active = await detect_music_player()
    if active:
        return active.name
    return None


# ══════════════════════════════════════════════════════════════════════════
#  Browser-spezifische Steuerung (YouTube etc.)
#  KDE Plasma 6 Wayland: KWin Scripting + ydotool
# ══════════════════════════════════════════════════════════════════════════

# ydotool nutzt Linux evdev Keycodes (siehe /usr/include/linux/input-event-codes.h)
_EVDEV_KEYS = {
    "shift": 42,       # KEY_LEFTSHIFT
    "ctrl": 29,        # KEY_LEFTCTRL
    "alt": 56,         # KEY_LEFTALT
    "super": 125,      # KEY_LEFTMETA
    "space": 57,       # KEY_SPACE
    "enter": 28,       # KEY_ENTER
    "tab": 15,         # KEY_TAB
    "escape": 1,       # KEY_ESC
    "left": 105,       # KEY_LEFT
    "right": 106,      # KEY_RIGHT
    "up": 103,         # KEY_UP
    "down": 108,       # KEY_DOWN
    "f4": 62,          # KEY_F4
    # Buchstaben: a=30, b=48, c=46, d=32, e=18, f=33, ... n=49, p=25, ...
    "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33,
    "g": 34, "h": 35, "i": 23, "j": 36, "k": 37, "l": 38,
    "m": 50, "n": 49, "o": 24, "p": 25, "q": 16, "r": 19,
    "s": 31, "t": 20, "u": 22, "v": 47, "w": 17, "x": 45,
    "y": 21, "z": 44,
}


def _parse_key_combo(combo: str) -> list[str]:
    """
    Parse key combo string → ydotool evdev key sequence.
    
    "shift+n" → ["42:1", "49:1", "49:0", "42:0"]
    "space"   → ["57:1", "57:0"]
    "ctrl+c"  → ["29:1", "46:1", "46:0", "29:0"]
    """
    parts = combo.lower().split("+")
    modifiers: list[int] = []
    key_code: Optional[int] = None

    for p in parts:
        p = p.strip()
        if p in ("shift", "ctrl", "alt", "super"):
            modifiers.append(_EVDEV_KEYS[p])
        elif p in _EVDEV_KEYS:
            key_code = _EVDEV_KEYS[p]
        else:
            logger.warning("unknown_key", key=p)
            return []

    if key_code is None:
        return []

    # Build sequence: modifiers down → key down → key up → modifiers up (reversed)
    seq: list[str] = []
    for m in modifiers:
        seq.append(f"{m}:1")  # modifier down
    seq.append(f"{key_code}:1")  # key down
    seq.append(f"{key_code}:0")  # key up
    for m in reversed(modifiers):
        seq.append(f"{m}:0")  # modifier up
    return seq


async def _ensure_ydotoold() -> bool:
    """
    Sicherstellen dass ydotoold läuft.
    Startet den Daemon automatisch wenn nötig.
    """
    import os
    socket_path = f"/run/user/{os.getuid()}/.ydotool_socket"
    if os.path.exists(socket_path):
        return True

    # Versuche den systemd user service zu starten
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "--user", "start", "ydotool.service",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=5.0)
        await asyncio.sleep(0.5)
        if os.path.exists(socket_path):
            return True
    except Exception:
        pass

    # Fallback: ydotoold direkt starten (als Hintergrund-Prozess)
    try:
        proc = await asyncio.create_subprocess_exec(
            "ydotoold",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.sleep(1.0)
        if os.path.exists(socket_path):
            logger.info("ydotoold_started_manually")
            return True
    except Exception:
        pass

    logger.warning("ydotoold_not_available")
    return False


async def _send_key_combo(combo: str) -> bool:
    """
    Sende einen Tastendruck via ydotool (Wayland-kompatibel).
    
    combo: "shift+n", "space", "ctrl+c", etc.
    Returns: True wenn erfolgreich gesendet.
    """
    if not shutil.which("ydotool"):
        logger.warning("ydotool_not_installed")
        return False

    if not await _ensure_ydotoold():
        return False

    key_seq = _parse_key_combo(combo)
    if not key_seq:
        logger.warning("invalid_key_combo", combo=combo)
        return False

    try:
        proc = await asyncio.create_subprocess_exec(
            "ydotool", "key", *key_seq,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode == 0:
            logger.info("key_sent", combo=combo, evdev=key_seq)
            return True
        else:
            logger.warning("ydotool_key_failed", combo=combo, 
                          stderr=stderr.decode()[:100])
            return False
    except Exception as e:
        logger.warning("ydotool_key_error", combo=combo, error=str(e))
        return False


async def _focus_window_kwin(window_class: str) -> bool:
    """
    Fokussiere ein Fenster via KWin Scripting (KDE Plasma 6 Wayland).
    
    Schreibt ein temporäres JS-Script, lädt es in KWin, führt es aus
    und räumt danach auf. Zuverlässigste Methode auf Wayland.
    """
    import tempfile
    import os

    script_content = f"""
var clients = workspace.windowList();
for (var i = 0; i < clients.length; i++) {{
    var c = clients[i];
    if (c.resourceClass && c.resourceClass.toString().toLowerCase().indexOf("{window_class.lower()}") !== -1) {{
        workspace.activeWindow = c;
        break;
    }}
}}
"""

    tmp = None
    script_name = f"soma_focus_{window_class.lower()}"
    try:
        # Script in Temp-Datei schreiben
        tmp = tempfile.NamedTemporaryFile(
            mode='w', suffix='.js', delete=False, prefix='soma_kwin_'
        )
        tmp.write(script_content)
        tmp.close()

        # Altes Script gleichen Namens entladen (falls vorhanden)
        proc = await asyncio.create_subprocess_exec(
            "qdbus6", "org.kde.KWin", "/Scripting",
            "org.kde.kwin.Scripting.unloadScript", script_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), timeout=3.0)

        # Script laden
        proc = await asyncio.create_subprocess_exec(
            "qdbus6", "org.kde.KWin", "/Scripting",
            "org.kde.kwin.Scripting.loadScript", tmp.name, script_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode != 0:
            logger.warning("kwin_load_failed", stderr=stderr.decode()[:100])
            return False

        # Script starten (führt ALLE geladenen Scripts aus)
        proc = await asyncio.create_subprocess_exec(
            "qdbus6", "org.kde.KWin", "/Scripting",
            "org.kde.kwin.Scripting.start",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), timeout=5.0)

        # Aufräumen: Script entladen
        proc = await asyncio.create_subprocess_exec(
            "qdbus6", "org.kde.KWin", "/Scripting",
            "org.kde.kwin.Scripting.unloadScript", script_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), timeout=3.0)

        logger.info("kwin_focus_window", window_class=window_class)
        return True

    except Exception as e:
        logger.warning("kwin_focus_error", window_class=window_class, error=str(e))
        return False
    finally:
        if tmp and os.path.exists(tmp.name):
            os.unlink(tmp.name)


async def _send_key_to_window(window_class: str, key_combo: str) -> bool:
    """
    Fokussiere ein Fenster und sende einen Tastendruck.
    
    1. KWin Script fokussiert das Fenster (Wayland-sicher)
    2. Kurze Pause für Fokus-Wechsel
    3. ydotool sendet den Tastendruck
    
    Args:
        window_class: "firefox", "chromium", etc.
        key_combo: "shift+n", "space", "shift+p", etc.
    """
    # Schritt 1: Fenster fokussieren
    focused = await _focus_window_kwin(window_class)
    if not focused:
        logger.warning("focus_failed", window_class=window_class)
        return False

    # Schritt 2: Warten bis Fokus gewechselt hat
    await asyncio.sleep(0.3)

    # Schritt 3: Taste senden
    sent = await _send_key_combo(key_combo)
    return sent


async def _detect_browser_class(player: MediaPlayerInfo) -> str:
    """Detect the window class for the browser from the MPRIS player name."""
    name_lower = player.name.lower()
    if "firefox" in name_lower:
        return "firefox"
    elif "chrom" in name_lower:
        return "chromium"
    elif "brave" in name_lower:
        return "brave"
    elif "vivaldi" in name_lower:
        return "vivaldi"
    # plasma-browser-integration → check identity
    identity = (player.identity or "").lower()
    if "firefox" in identity:
        return "firefox"
    elif "chrom" in identity:
        return "chromium"
    return "firefox"  # Sicherer Default


async def _browser_next_track(player: MediaPlayerInfo) -> str:
    """
    Nächstes Lied auf YouTube im Browser.
    
    Strategie:
      1. MPRIS Next() versuchen (klappt bei Playlists)
      2. Keyboard Shortcut: Shift+N (YouTube Nächstes Video)
         → KWin fokussiert Browser, ydotool sendet Key
    """
    # Erst MPRIS versuchen
    if player.can_next:
        ok, _ = await _control_player_dbus(player.name, "Next")
        if ok:
            await asyncio.sleep(1.5)
            new_info = await _get_player_info(player.name)
            if new_info and new_info.title != player.title:
                return f"⏭️ Nächstes Video auf {player.friendly_name}: {new_info.track_description}"

    # YouTube Keyboard Shortcut: Shift+N = nächstes Video
    browser_class = await _detect_browser_class(player)
    sent = await _send_key_to_window(browser_class, "shift+n")
    if sent:
        await asyncio.sleep(2.0)
        new_info = await _get_player_info(player.name)
        track = new_info.track_description if new_info else ""
        return f"⏭️ Nächstes Video auf YouTube" + (f": {track}" if track else "")

    return "❌ Konnte nächstes Video nicht starten. ydotool oder KWin Scripting nicht verfügbar."


async def _browser_prev_track(player: MediaPlayerInfo) -> str:
    """Vorheriges Video auf YouTube. Shift+P."""
    if player.can_prev:
        ok, _ = await _control_player_dbus(player.name, "Previous")
        if ok:
            await asyncio.sleep(1.5)
            new_info = await _get_player_info(player.name)
            if new_info and new_info.title != player.title:
                return f"⏮️ Vorheriges Video auf {player.friendly_name}: {new_info.track_description}"

    browser_class = await _detect_browser_class(player)
    sent = await _send_key_to_window(browser_class, "shift+p")
    if sent:
        await asyncio.sleep(2.0)
        new_info = await _get_player_info(player.name)
        track = new_info.track_description if new_info else ""
        return f"⏮️ Vorheriges Video auf YouTube" + (f": {track}" if track else "")

    return "❌ Konnte vorheriges Video nicht starten. ydotool oder KWin Scripting nicht verfügbar."


async def open_in_active_player(query: str) -> Optional[str]:
    """
    Öffnet Musik/Video im AKTIVEN Player (statt neuen Player zu starten).
    
    Logik:
      - Wenn mpv läuft → neues mpv mit yt-dlp
      - Wenn Browser (YouTube) läuft → YouTube-Suche im Browser
      - Kein Player aktiv → None (Caller entscheidet)
    
    Returns: Ergebnis-Text oder None wenn kein aktiver Player.
    """
    active = await detect_music_player()
    if not active:
        return None

    logger.info("open_in_active_player", player=active.name,
                player_type="browser" if active.is_browser else "mpv" if active.is_mpv else "other",
                query=query[:50])

    if active.is_mpv:
        # mpv: einfach neuen Song starten (alter wird gestoppt via pkill)
        return None  # Lass den normalen mpv-Flow machen

    if active.is_browser:
        # Browser (YouTube): YouTube-Suche im Browser öffnen
        import urllib.parse
        encoded = urllib.parse.quote_plus(query)
        url = f"https://www.youtube.com/results?search_query={encoded}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "xdg-open", url,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (asyncio.TimeoutError, Exception):
            pass
        return f"🎵 YouTube-Suche im Browser geöffnet: '{query}'"

    # Andere Player (VLC, Spotify etc.) — über MPRIS OpenUri wenn möglich
    try:
        import urllib.parse
        # YouTube Search URL als Fallback
        encoded = urllib.parse.quote_plus(query)
        uri = f"https://www.youtube.com/results?search_query={encoded}"
        ok, _ = await _control_player_dbus(active.name, f"OpenUri {uri}")
        if ok:
            return f"🎵 '{query}' an {active.friendly_name} gesendet"
    except Exception:
        pass

    return None  # Fallback: normaler Flow
