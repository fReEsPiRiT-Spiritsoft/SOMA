"""
Plugin: Media Player – YouTube & lokale Medien
================================================
v1.0 — Öffnet YouTube-Suchen, Spotify-Deep-Links oder lokale Medien.

Aktions-Tag Syntax (LLM → Pipeline):
  [ACTION:open_url url="https://..."]
  [ACTION:youtube query="aligatoah songs"]
  [ACTION:media_play artist="Aligatoah" song="Triebkraft Gegenwart"]

Unterstützte Backends:
  1. xdg-open  → Browser (immer verfügbar)
  2. mpv + yt-dlp → direktes Audio ohne Browser (wenn installiert)

Beispiele:
  - "Soma, starte YouTube mit Aligatoah"
  - "Soma, spiel Triebkraft Gegenwart von Aligatoah"
  - "Soma, öffne Spotify"
"""
__version__ = "1.0.0"
__author__ = "SOMA Evolution Lab"
__description__ = "YouTube / Media Player – öffnet Browser oder spielt direkt via mpv"

import asyncio
import shutil
import subprocess
import urllib.parse
from typing import Optional

import structlog

logger = structlog.get_logger("soma.plugin.media_player")

# ── Backend-Erkennung (beim Import gecacht) ──────────────────────────────
_HAS_MPV = shutil.which("mpv") is not None
_HAS_YT_DLP = shutil.which("yt-dlp") is not None
_HAS_XDG = shutil.which("xdg-open") is not None


def _build_youtube_url(query: str) -> str:
    """Baut eine YouTube-Such-URL."""
    encoded = urllib.parse.quote_plus(query)
    return f"https://www.youtube.com/results?search_query={encoded}"


def _build_youtube_autoplay_url(query: str) -> str:
    """Baut eine YouTube-URL mit Auto-Play (erstes Ergebnis)."""
    encoded = urllib.parse.quote_plus(query)
    return f"https://www.youtube.com/results?search_query={encoded}&autoplay=1"


async def open_url(url: str) -> str:
    """Öffnet eine URL im Standard-Browser via xdg-open."""
    if not _HAS_XDG:
        return "xdg-open nicht verfügbar – kein Browser-Start möglich."

    try:
        proc = await asyncio.create_subprocess_exec(
            "xdg-open", url,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=5.0)
        logger.info("media_url_opened", url=url[:80])
        return f"Geöffnet: {url[:60]}..."
    except asyncio.TimeoutError:
        # xdg-open öffnet Browser und endet sofort – Timeout ist normal
        logger.info("media_url_opened_timeout_ok", url=url[:80])
        return f"Browser geöffnet für: {url[:60]}..."
    except Exception as exc:
        logger.error("media_url_open_failed", error=str(exc))
        return f"Fehler beim Öffnen: {exc}"


async def youtube_search(query: str, use_mpv: bool = False) -> str:
    """
    Sucht auf YouTube und öffnet das Ergebnis.
    
    - Wenn mpv + yt-dlp vorhanden: Direktwiedergabe (kein Browser)
    - Sonst: Browser mit YouTube-Suche
    """
    if use_mpv and _HAS_MPV and _HAS_YT_DLP:
        return await _play_via_mpv(query)
    else:
        url = _build_youtube_autoplay_url(query)
        return await open_url(url)


async def _play_via_mpv(query: str) -> str:
    """Spielt Audio direkt via mpv + yt-dlp (kein Browser nötig)."""
    search_url = f"ytdl://ytsearch1:{query}"
    try:
        proc = await asyncio.create_subprocess_exec(
            "mpv",
            "--no-video",           # nur Audio
            "--ytdl-format=bestaudio",
            "--title=SOMA Music",
            search_url,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        logger.info("media_mpv_started", query=query)
        # Nicht auf Ende warten – läuft im Hintergrund
        asyncio.create_task(_monitor_mpv(proc, query))
        return f"Spiele '{query}' via mpv."
    except Exception as exc:
        logger.error("media_mpv_failed", error=str(exc))
        # Fallback zu Browser
        url = _build_youtube_autoplay_url(query)
        return await open_url(url)


async def _monitor_mpv(proc: asyncio.subprocess.Process, query: str) -> None:
    """Überwacht mpv-Prozess im Hintergrund."""
    await proc.wait()
    logger.info("media_mpv_ended", query=query, returncode=proc.returncode)


async def stop_playback() -> str:
    """Beendet aktive mpv-Instanzen."""
    try:
        result = subprocess.run(
            ["pkill", "-f", "mpv"],
            capture_output=True,
            timeout=3,
        )
        if result.returncode == 0:
            return "Wiedergabe gestoppt."
        return "Keine aktive Wiedergabe gefunden."
    except Exception as exc:
        return f"Stop-Fehler: {exc}"


# ── Plugin-Info (für Plugin Manager) ────────────────────────────────────

def get_info() -> dict:
    return {
        "name": "media_player",
        "version": __version__,
        "description": __description__,
        "backends": {
            "xdg_open": _HAS_XDG,
            "mpv": _HAS_MPV,
            "yt_dlp": _HAS_YT_DLP,
        },
        "capabilities": ["youtube_search", "open_url", "stop_playback"],
    }


async def execute(query: str = "") -> str:
    """Standard-Execute für Plugin-Manager (Infos abrufen)."""
    info = get_info()
    backends = [k for k, v in info["backends"].items() if v]
    return f"Media Player bereit. Backends: {', '.join(backends) or 'keiner'}."
