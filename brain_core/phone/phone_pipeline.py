"""
SOMA-AI Phone Gateway — Asterisk ARI Client
=============================================
Verbindet SOMA mit dem Asterisk-Gateway über die ARI WebSocket API.
Wartet auf eingehende Anrufe und startet für jeden Anruf eine CallSession.

Architektur:
  Externer Anrufer
       │ PSTN
  Vodafone Station ←── Asterisk SIP Registration (pjsip.conf)
       │ SIP INVITE
  Asterisk (Docker)
       │ ARI WebSocket Events
  PhonePipeline  ◄── _connect_ari() WebSocket loop
       │
       ├── StasisStart    → neue CallSession starten
       ├── RecordingFinished → session.on_recording_finished()
       ├── PlaybackFinished  → session.on_playback_finished()
       └── ChannelHangup     → session.on_hangup()
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Callable, Optional

import structlog

from brain_core.config import settings
from brain_core.phone.call_session import CallSession

logger = structlog.get_logger("soma.phone")

ARI_APP_NAME = "soma-phone"


class PhonePipeline:
    """
    ARI WebSocket Client — verwaltet alle aktiven Anrufe.
    Reconnect-sicher: Bei Verbindungsverlust wird automatisch neu verbunden.
    """

    def __init__(
        self,
        stt_engine,
        tts_engine,
        logic_router,
        ha_bridge,
        broadcast_callback: Optional[Callable] = None,
    ):
        self._stt = stt_engine
        self._tts = tts_engine
        self._router = logic_router
        self._ha = ha_bridge
        self._broadcast = broadcast_callback

        self._sessions: dict[str, CallSession] = {}
        self._running = False
        self._ws_task: Optional[asyncio.Task] = None

        # Shared dirs für Recordings und TTS-Sounds
        self._rec_dir = Path(settings.phone_recordings_dir)
        self._snd_dir = Path(settings.phone_sounds_dir)

        # ARI base URL (für HTTP calls aus CallSession)
        self._ari_base = (
            f"http://{settings.asterisk_host}:{settings.asterisk_ari_port}"
        )
        self._ari_auth = (settings.asterisk_ari_user, settings.asterisk_ari_pass)

    async def start(self) -> None:
        """Phone Pipeline starten."""
        self._rec_dir.mkdir(parents=True, exist_ok=True)
        self._snd_dir.mkdir(parents=True, exist_ok=True)

        self._running = True
        self._ws_task = asyncio.create_task(self._ws_loop())
        logger.info("phone_pipeline_starting",
                    asterisk=settings.asterisk_host,
                    port=settings.asterisk_ari_port)

    async def stop(self) -> None:
        """Phone Pipeline stoppen."""
        self._running = False
        # Alle aktiven Sessions beenden
        for session in list(self._sessions.values()):
            session.on_hangup()
        self._sessions.clear()
        # WS-Task canceln
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        logger.info("phone_pipeline_stopped")

    # ════════════════════════════════════════════════════════════════════
    #  ARI WebSocket LOOP (mit Reconnect)
    # ════════════════════════════════════════════════════════════════════

    async def _ws_loop(self) -> None:
        """Dauerschleife: verbindet ARI WebSocket, reconnectet bei Fehler."""
        while self._running:
            try:
                await self._connect_ari()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if self._running:
                    logger.warning(
                        "ari_connection_lost",
                        error=str(exc)[:80],
                        retry_in_sec=20,
                    )
                    await asyncio.sleep(20)

    async def _connect_ari(self) -> None:
        """
        WebSocket zu Asterisk ARI aufbauen.
        Empfängt Events und dispatcht sie an aktive CallSessions.
        """
        try:
            import websockets
        except ImportError:
            logger.error(
                "websockets_not_installed",
                hint="pip install websockets",
            )
            await asyncio.sleep(60)
            return

        url = (
            f"ws://{settings.asterisk_host}:{settings.asterisk_ari_port}"
            f"/ari/events?app={ARI_APP_NAME}&subscribeAll=true"
            f"&api_key={settings.asterisk_ari_user}:{settings.asterisk_ari_pass}"
        )

        logger.info("ari_connecting", url=url[:60] + "...")

        async with websockets.connect(url, ping_interval=30, ping_timeout=10) as ws:
            logger.info("ari_connected")
            if self._broadcast:
                await self._broadcast(
                    "info", "📞 Phone Gateway (Asterisk ARI) verbunden", "PHONE"
                )

            async for raw_msg in ws:
                if not self._running:
                    break
                try:
                    event = json.loads(raw_msg)
                    await self._dispatch(event)
                except json.JSONDecodeError:
                    pass
                except Exception as exc:
                    logger.error("ari_dispatch_error", error=str(exc))

    # ════════════════════════════════════════════════════════════════════
    #  EVENT DISPATCH
    # ════════════════════════════════════════════════════════════════════

    async def _dispatch(self, event: dict) -> None:
        """Verteilt ARI Events an die richtige CallSession."""
        etype = event.get("type", "")

        # Channel-ID aus verschiedenen Event-Strukturen extrahieren
        channel = event.get("channel") or {}
        channel_id = channel.get("id") or event.get("channel_id", "")

        logger.debug("ari_event", type=etype, channel_id=channel_id[:16] if channel_id else "")

        if etype == "StasisStart":
            # Neuer Anruf kommt rein!
            await self._on_new_call(channel_id, channel)

        elif etype in ("StasisEnd", "ChannelDestroyed", "ChannelHangupRequest"):
            if channel_id in self._sessions:
                self._sessions[channel_id].on_hangup()
                # Session erst nach kurzer Pause entfernen (laufende Tasks abschließen)
                await asyncio.sleep(0.5)
                self._sessions.pop(channel_id, None)

        elif etype == "RecordingFinished":
            rec_name = (event.get("recording") or {}).get("name", "")
            for sess in list(self._sessions.values()):
                sess.on_recording_finished(rec_name)

        elif etype == "PlaybackFinished":
            pb_id = (event.get("playback") or {}).get("id", "")
            for sess in list(self._sessions.values()):
                sess.on_playback_finished(pb_id)

    async def _on_new_call(self, channel_id: str, channel: dict) -> None:
        """Eingehenden Anruf annehmen → CallSession starten."""
        if not channel_id:
            logger.warning("ari_stasis_no_channel_id")
            return

        caller = channel.get("caller", {})
        caller_id = caller.get("number") or caller.get("name") or "Unbekannt"

        # Soma hat eine lokale URL (HA braucht sie zum Abspielen von TTS-Audio)
        soma_url = getattr(settings, "soma_local_url",
                           f"http://localhost:{settings.brain_core_port}")

        session = CallSession(
            channel_id=channel_id,
            caller_id=caller_id,
            stt=self._stt,
            tts=self._tts,
            router=self._router,
            ha=self._ha,
            ari_base=self._ari_base,
            ari_auth=self._ari_auth,
            rec_dir=self._rec_dir,
            snd_dir=self._snd_dir,
            broadcast=self._broadcast,
            ha_speaker_entity=getattr(settings, "ha_speaker_entity", "media_player.all"),
            soma_local_url=soma_url,
        )

        self._sessions[channel_id] = session
        asyncio.create_task(session.run())

    # ════════════════════════════════════════════════════════════════════
    #  STATUS
    # ════════════════════════════════════════════════════════════════════

    @property
    def active_calls(self) -> int:
        return len(self._sessions)

    @property
    def is_running(self) -> bool:
        return self._running
