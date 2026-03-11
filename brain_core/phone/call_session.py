"""
SOMA-AI Phone Gateway — Einzelner Anruf (State Machine)
=========================================================
Verwaltet einen eingehenden Festnetz-Anruf von Anfang bis Ende.

State Machine:
  GREETING     → Soma hebt ab, spielt Begrüßung
  AUTH         → Passwort abfragen (max 3 Versuche)
  ACTIVE       → Freies Gespräch mit Soma (LLM)
  ENDED        → Aufgelegt

Features:
  ✅ Passwort-Auth via Sprache
  ✅ Vollständiger LLM-Dialog (Llama 3 mit Phone-Mode-Prompt)
  ✅ "Sag meiner Tochter..." → [ACTION:ha_tts] broadcast über Hauslautsprecher
  ✅ Smart-Home-Steuerung von unterwegs ([ACTION:ha_call])
  ✅ Race-condition-sicher (asyncio.Event für Sync mit ARI-Events)
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

import httpx
import structlog

logger = structlog.get_logger("soma.phone.session")

ACTION_PATTERN = re.compile(r"\[ACTION:(\w+)([^\]]*)\]")
PARAM_PATTERN = re.compile(r'(\w+)="([^"]*)"')


class CallState(str, Enum):
    GREETING = "greeting"
    AUTH = "auth"
    ACTIVE = "active"
    ENDED = "ended"


async def _convert_to_asterisk_wav(src: Path, dst: Path) -> None:
    """
    Konvertiert Piper-TTS-Audio (22050 Hz) in Asterisk-kompatibles WAV.
    Format: 8kHz, mono, PCM 16-bit little-endian.
    Nutzt ffmpeg (auf Debian/Ubuntu standardmäßig verfügbar).
    """
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", str(src),
        "-ar", "8000", "-ac", "1", "-sample_fmt", "s16",
        str(dst),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    if not dst.exists():
        # ffmpeg not available — try sox as fallback
        proc2 = await asyncio.create_subprocess_exec(
            "sox", str(src), "-r", "8000", "-c", "1", "-b", "16", str(dst),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc2.wait()


class CallSession:
    """
    State Machine für einen einzelnen Festnetz-Anruf.

    Wird von PhonePipeline erzeugt und als asyncio.Task ausgeführt.
    ARI-Events (RecordingFinished, PlaybackFinished, Hangup) werden via
    notify_*() Methoden von PhonePipeline an diese Session weitergeleitet.
    """

    # ── Timeouts ──────────────────────────────────────────────────────────
    AUTH_LISTEN_SEC = 10.0       # Wie lange auf Passwort warten
    DIALOG_LISTEN_SEC = 20.0     # Wie lange auf Nutzer-Anfrage warten
    SILENCE_SEC = 2.5            # Stille = Ende der Spracheingabe
    MAX_AUTH_ATTEMPTS = 3

    def __init__(
        self,
        channel_id: str,
        caller_id: str,
        stt,                         # STTEngine
        tts,                         # TTSEngine
        router,                      # LogicRouter
        ha_bridge,                   # HomeAssistantBridge
        ari_base: str,               # http://localhost:8088
        ari_auth: tuple[str, str],   # (user, pass)
        rec_dir: Path,               # dir for incoming recordings
        snd_dir: Path,               # dir for outgoing TTS audio
        broadcast: Optional[Callable] = None,  # Dashboard broadcast
        ha_speaker_entity: str = "media_player.all",  # HA entity for home broadcast
        soma_local_url: str = "http://localhost:8100",  # SOMA's URL (for HA to fetch audio)
    ):
        self._channel_id = channel_id
        self._caller_id = caller_id
        self._stt = stt
        self._tts = tts
        self._router = router
        self._ha = ha_bridge
        self._broadcast = broadcast
        self._ha_speaker_entity = ha_speaker_entity
        self._soma_local_url = soma_local_url

        # ARI HTTP client (dediziert für diese Session)
        self._http = httpx.AsyncClient(
            base_url=ari_base,
            auth=ari_auth,
            timeout=httpx.Timeout(60.0),
        )

        # Dirs
        self._rec_dir = rec_dir
        self._snd_dir = snd_dir

        # ── Async Sync-Events ──────────────────────────────────────────
        # Werden von PhonePipeline.notify_*() gesetzt
        self._hangup = asyncio.Event()
        self._rec_events: dict[str, asyncio.Event] = {}
        self._play_events: dict[str, asyncio.Event] = {}

        # ── Session State ──────────────────────────────────────────────
        self._state = CallState.GREETING
        self._session_id = f"phone_{channel_id[:10]}"

    # ════════════════════════════════════════════════════════════════════
    #  HAUPTLOOP
    # ════════════════════════════════════════════════════════════════════

    async def run(self) -> None:
        """Entry-Point: läuft als asyncio.Task während des Anrufs."""
        try:
            logger.info("call_session_start", channel=self._channel_id[:16], caller=self._caller_id)
            await self._emit("📞 Anruf eingehend von: " + self._caller_id, "PHONE_IN")

            # Anruf annehmen
            await self._answer()
            await asyncio.sleep(0.8)  # kurze Pause nach Answer

            # Auth-Phase
            self._state = CallState.AUTH
            authenticated = await self._auth_loop()

            if not authenticated:
                await asyncio.sleep(1.0)
                await self._hangup_call()
                return

            # Aktive Dialog-Phase
            self._state = CallState.ACTIVE
            await self._emit("🔐 Authentifiziert. Aktiver Anruf.", "PHONE_AUTH")
            await self._dialog_loop()

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("call_session_error", error=str(exc), exc_info=True)
        finally:
            self._state = CallState.ENDED
            await self._http.aclose()
            logger.info("call_session_ended", channel=self._channel_id[:16])

    # ════════════════════════════════════════════════════════════════════
    #  AUTH
    # ════════════════════════════════════════════════════════════════════

    async def _auth_loop(self) -> bool:
        """
        Passwort-Abfrage: Soma fragt nach dem Passwort, Nutzer antwortet per Sprache.
        Max 3 Versuche, dann Auflegen.
        """
        await self._speak("Hier ist Soma. Bitte nennen Sie Ihr Passwort.")

        for attempt in range(self.MAX_AUTH_ATTEMPTS):
            if self._hangup.is_set():
                return False

            text = await self._listen(max_sec=self.AUTH_LISTEN_SEC, silence_sec=2.0)

            if not text:
                continue

            if self._verify_password(text):
                await self._speak("Zugriff gewährt. Hallo! Wie kann ich dir helfen?")
                return True

            remaining = self.MAX_AUTH_ATTEMPTS - 1 - attempt
            if remaining > 0:
                plural = "Versuche" if remaining > 1 else "Versuch"
                await self._speak(f"Falsches Passwort. Noch {remaining} {plural}.")
            else:
                await self._speak("Zu viele Fehlversuche. Auf Wiederhören.")

        return False

    @staticmethod
    def _verify_password(text: str) -> bool:
        """Prüft ob der gesprochene Text das konfigurierte Passwort enthält."""
        from brain_core.config import settings

        if not settings.soma_phone_password:
            return False

        text_lower = text.lower().strip()
        pw_lower = settings.soma_phone_password.lower().strip()

        # SHA-256 Hash-Vergleich wenn hash konfiguriert
        if settings.soma_phone_password_hash:
            spoken_hash = hashlib.sha256(text_lower.encode()).hexdigest()
            return spoken_hash == settings.soma_phone_password_hash

        # Direktvergleich (case-insensitive, auch wenn Passwort im Satz vorkommt)
        return pw_lower in text_lower

    # ════════════════════════════════════════════════════════════════════
    #  DIALOG LOOP
    # ════════════════════════════════════════════════════════════════════

    async def _dialog_loop(self) -> None:
        """Freier Dialog nach erfolgreicher Auth. Soma hört zu und antwortet."""
        while not self._hangup.is_set():
            text = await self._listen(max_sec=self.DIALOG_LISTEN_SEC, silence_sec=self.SILENCE_SEC)

            if not text:
                # Lange Stille → leichter Prompt
                if not self._hangup.is_set():
                    continue
                break

            logger.info("phone_user_said", text=text[:80])
            await self._emit(f"🎤 Anrufer: \"{text[:60]}\"", "PHONE_STT")

            # Auflegungs-Erkennung
            if self._is_goodbye(text):
                await self._speak("Alles klar! Bis bald. Pass auf dich auf!")
                await asyncio.sleep(1.5)
                await self._hangup_call()
                return

            # LLM
            response = await self._ask_llm(text)
            clean_text, _ = await self._dispatch_actions(response)

            if clean_text.strip():
                await self._speak(clean_text)

    # ════════════════════════════════════════════════════════════════════
    #  LLM
    # ════════════════════════════════════════════════════════════════════

    async def _ask_llm(self, text: str) -> str:
        """Text an LogicRouter senden (mit Phone-Mode-Kontext)."""
        from brain_core.logic_router import SomaRequest

        request = SomaRequest(
            prompt=text,
            session_id=self._session_id,
            metadata={
                "source": "phone_call",
                "phone_mode": True,
                "caller_authenticated": True,
                "caller_id": self._caller_id,
                "ha_speaker_entity": self._ha_speaker_entity,
            },
        )

        try:
            response = await self._router.route(request)
            logger.info("phone_llm_response",
                        engine=response.engine_used,
                        text_preview=response.response[:60])
            return response.response
        except Exception as exc:
            logger.error("phone_llm_error", error=str(exc))
            return "Entschuldigung, ich hatte gerade einen Denkfehler."

    # ════════════════════════════════════════════════════════════════════
    #  ACTION DISPATCH (Phone-spezifisch)
    # ════════════════════════════════════════════════════════════════════

    async def _dispatch_actions(self, response_text: str) -> tuple[str, list[str]]:
        """
        Parst [ACTION:...] Tags aus der LLM-Antwort und führt sie aus.
        Phone-Version: kennt zusätzlich ha_tts (broadcast an Hauslautsprecher).
        """
        clean = response_text
        executed: list[str] = []

        for match in ACTION_PATTERN.finditer(response_text):
            action_type = match.group(1).lower()
            params = dict(PARAM_PATTERN.findall(match.group(2)))

            logger.info("phone_action_tag", action=action_type, params=params)
            await self._emit(f"⚡ Phone-ACTION:{action_type} {params}", "PHONE_ACTION")

            try:
                if action_type == "ha_tts":
                    result = await self._action_ha_tts(params)
                    executed.append(f"ha_tts: {result}")

                elif action_type == "ha_call":
                    result = await self._action_ha_call(params)
                    executed.append(f"ha_call: {result}")

                elif action_type == "remember":
                    result = await self._action_remember(params)
                    executed.append(f"remember: {result}")

                else:
                    logger.debug("phone_action_unknown", action=action_type)

            except Exception as exc:
                logger.error("phone_action_error", action=action_type, error=str(exc))

            clean = clean.replace(match.group(0), "")

        clean = re.sub(r" {2,}", " ", clean).strip()
        return clean, executed

    async def _action_ha_tts(self, params: dict) -> str:
        """
        [ACTION:ha_tts text="Mia, dein Papa sagt..." room="all"]

        Generiert TTS-Audio mit Somas Piper-Stimme und spielt es über
        Home-Assistant-connected Lautsprecher im Haus ab.

        Fluss:
          1. Piper TTS → WAV file in data/phone_sounds/
          2. FastAPI /api/v1/audio/{file} → HA kann die Datei abrufen
          3. ha_bridge.call_service(media_player, play_media, entity_id, url)
        """
        text = params.get("text", "")
        room = params.get("room", "all").lower()

        if not text:
            return "Kein Text angegeben."

        # Entity-Mapping
        if room == "all" or room == "alle":
            entity_id = self._ha_speaker_entity  # z.B. "media_player.all"
        else:
            entity_id = f"media_player.{room}"

        # TTS → Datei
        filename = f"broadcast_{uuid.uuid4().hex[:8]}.wav"
        raw_path = self._snd_dir / f"raw_{filename}"
        final_path = self._snd_dir / filename

        await self._tts.speak_to_file(text, raw_path)
        await _convert_to_asterisk_wav(raw_path, final_path)
        raw_path.unlink(missing_ok=True)

        # Media URL (HA ruft diese URL ab)
        media_url = f"{self._soma_local_url}/api/v1/audio/{filename}"

        logger.info("ha_tts_broadcast", room=room, entity=entity_id,
                    url=media_url, text_preview=text[:50])
        await self._emit(
            f"🔊 Hausdurchsage ({room}): \"{text[:50]}\"", "PHONE_BROADCAST"
        )

        try:
            if self._ha and self._ha._client:
                await self._ha.call_service(
                    "media_player", "play_media",
                    entity_id,
                    {
                        "media_content_id": media_url,
                        "media_content_type": "audio/wav",
                    },
                )
                return f"✓ Hausdurchsage in '{room}': {text[:40]}"
            else:
                return "⚠ HA nicht verbunden — Durchsage nicht möglich."
        except Exception as exc:
            return f"HA-Fehler: {str(exc)[:60]}"

    async def _action_ha_call(self, params: dict) -> str:
        """[ACTION:ha_call domain=... service=... entity_id=...]"""
        domain = params.get("domain", "")
        service = params.get("service", "")
        entity_id = params.get("entity_id", "")

        if not all([domain, service, entity_id]):
            return f"Unvollständige HA-Parameter: {params}"

        data: dict = {}
        for key in ("brightness_pct", "brightness", "temperature", "hvac_mode",
                    "volume_level", "color_temp", "media_content_id"):
            if key in params:
                val = params[key]
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    pass
                data[key] = val

        try:
            if self._ha and self._ha._client:
                await self._ha.call_service(domain, service, entity_id, data or None)
                await self._emit(f"🏠 HA: {domain}.{service} → {entity_id}", "PHONE_HA")
                return f"✓ {domain}.{service} → {entity_id}"
            else:
                return "⚠ HA nicht verbunden."
        except Exception as exc:
            return f"HA-Fehler: {str(exc)[:60]}"

    async def _action_remember(self, params: dict) -> str:
        """[ACTION:remember category=... content=...]"""
        from brain_core.memory import get_memory, MemoryCategory

        category = params.get("category", "important")
        content = params.get("content", "")
        if not content:
            return "Kein Inhalt."

        try:
            cat = MemoryCategory(category)
        except ValueError:
            cat = MemoryCategory.IMPORTANT

        get_memory().remember(content, cat, source="phone_call")
        return f"Gemerkt: {content[:40]}"

    # ════════════════════════════════════════════════════════════════════
    #  ARI: SPRECHEN (TTS → Datei → ARI Play)
    # ════════════════════════════════════════════════════════════════════

    async def _speak(self, text: str) -> None:
        """
        Text aussprechen via Piper TTS → ARI.

        Fluss:
          1. TTS generiert WAV (Piper-native sample rate)
          2. ffmpeg/sox konvertiert zu 8kHz mono PCM (Asterisk-kompatibel)
          3. ARI POST /channels/{id}/play → Asterisk spielt Datei ab
          4. Warten auf PlaybackFinished Event
        """
        if self._hangup.is_set():
            return

        filename = f"soma_phone_{uuid.uuid4().hex[:8]}"
        raw_path = self._snd_dir / f"raw_{filename}.wav"
        final_path = self._snd_dir / f"{filename}.wav"

        # TTS generieren
        try:
            await self._tts.speak_to_file(text, raw_path)
        except Exception as exc:
            logger.error("phone_tts_error", error=str(exc))
            return

        # Auf Asterisk-kompatibles Format konvertieren
        await _convert_to_asterisk_wav(raw_path, final_path)
        raw_path.unlink(missing_ok=True)

        if not final_path.exists():
            logger.error("phone_tts_conversion_failed", text=text[:40])
            return

        # ARI: Datei abspielen
        pb_id = uuid.uuid4().hex[:12]
        ev = asyncio.Event()
        self._play_events[pb_id] = ev

        try:
            resp = await self._http.post(
                f"/ari/channels/{self._channel_id}/play/{pb_id}",
                json={"media": f"sound:soma/{filename}"},
            )
            if resp.status_code not in (200, 201):
                logger.warning("ari_play_failed", status=resp.status_code, text=text[:40])
                return

            # Warten auf PlaybackFinished oder Hangup
            await self._wait_first(ev, timeout=60.0)

        except Exception as exc:
            logger.error("phone_play_error", error=str(exc))
        finally:
            self._play_events.pop(pb_id, None)
            final_path.unlink(missing_ok=True)

    # ════════════════════════════════════════════════════════════════════
    #  ARI: ZUHÖREN (ARI Record → STT)
    # ════════════════════════════════════════════════════════════════════

    async def _listen(self, max_sec: float = 15.0, silence_sec: float = 2.5) -> str:
        """
        Caller-Audio aufnehmen und transkribieren.

        Fluss:
          1. ARI POST /channels/{id}/record → Asterisk nimmt auf
          2. Warten auf RecordingFinished Event (Stille oder max_sec)
          3. Whisper-STT auf WAV-Datei
          4. Datei löschen, Text zurückgeben
        """
        if self._hangup.is_set():
            return ""

        rec_name = f"soma_rec_{uuid.uuid4().hex[:10]}"
        ev = asyncio.Event()
        self._rec_events[rec_name] = ev

        try:
            resp = await self._http.post(
                f"/ari/channels/{self._channel_id}/record",
                json={
                    "name": rec_name,
                    "format": "wav",
                    "maxSilenceSeconds": silence_sec,
                    "maxDurationSeconds": max_sec,
                    "beep": False,
                    "terminateOn": "none",
                    "ifExists": "overwrite",
                },
            )
            if resp.status_code not in (200, 201):
                logger.warning("ari_record_failed", status=resp.status_code)
                return ""

            # Warten auf RecordingFinished oder Hangup
            await self._wait_first(ev, timeout=max_sec + 5)

        except Exception as exc:
            logger.error("phone_record_error", error=str(exc))
            return ""
        finally:
            self._rec_events.pop(rec_name, None)

        if self._hangup.is_set():
            return ""

        # STT
        filepath = self._rec_dir / f"{rec_name}.wav"
        if not filepath.exists():
            logger.warning("phone_recording_missing", name=rec_name)
            return ""

        try:
            result = await self._stt.transcribe_file(str(filepath))
            text = result.text.strip()
            if text:
                logger.info("phone_stt_result", text=text[:80])
            return text
        except Exception as exc:
            logger.error("phone_stt_error", error=str(exc))
            return ""
        finally:
            filepath.unlink(missing_ok=True)

    # ════════════════════════════════════════════════════════════════════
    #  ARI: ANRUF VERWALTEN
    # ════════════════════════════════════════════════════════════════════

    async def _answer(self) -> None:
        """Anruf annehmen (ARI)."""
        try:
            await self._http.post(f"/ari/channels/{self._channel_id}/answer")
        except Exception as exc:
            logger.error("ari_answer_failed", error=str(exc))

    async def _hangup_call(self) -> None:
        """Anruf auflegen (ARI)."""
        try:
            await self._http.delete(f"/ari/channels/{self._channel_id}")
        except Exception:
            pass  # Kann schon weg sein

    async def _wait_first(self, event: asyncio.Event, timeout: float) -> None:
        """Warte auf event ODER hangup, mit Timeout."""
        ev_task = asyncio.create_task(event.wait())
        hup_task = asyncio.create_task(self._hangup.wait())
        try:
            done, pending = await asyncio.wait(
                [ev_task, hup_task],
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
        except Exception:
            ev_task.cancel()
            hup_task.cancel()

    # ════════════════════════════════════════════════════════════════════
    #  EVENT CALLBACKS (von PhonePipeline aufgerufen)
    # ════════════════════════════════════════════════════════════════════

    def on_recording_finished(self, name: str) -> None:
        """ARI: RecordingFinished Event."""
        ev = self._rec_events.get(name)
        if ev:
            ev.set()

    def on_playback_finished(self, pb_id: str) -> None:
        """ARI: PlaybackFinished Event."""
        ev = self._play_events.get(pb_id)
        if ev:
            ev.set()

    def on_hangup(self) -> None:
        """Caller hat aufgelegt oder Verbindung getrennt."""
        self._hangup.set()
        # Alle wartenden Events freigeben damit Tasks nicht hängen
        for ev in self._rec_events.values():
            ev.set()
        for ev in self._play_events.values():
            ev.set()

    # ════════════════════════════════════════════════════════════════════
    #  HELPERS
    # ════════════════════════════════════════════════════════════════════

    @staticmethod
    def _is_goodbye(text: str) -> bool:
        """Erkennt Verabschiedungen."""
        t = text.lower()
        goodbyes = [
            "tschüss", "tschüs", "auf wiederhören", "auf wiedersehen",
            "ciao", "bye", "goodbye", "bis bald", "bis später", "mach's gut",
            "ich leg auf", "aufhören", "beenden", "ende", "schluss",
        ]
        return any(g in t for g in goodbyes)

    async def _emit(self, content: str, tag: str = "PHONE") -> None:
        """Dashboard-Event senden."""
        if self._broadcast:
            try:
                await self._broadcast("info", content, tag)
            except Exception:
                pass
