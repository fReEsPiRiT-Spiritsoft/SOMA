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
    SUMMARIZING = "summarizing"  # Phase 7: Zusammenfassung generieren
    ENDED = "ended"


class CallTurn:
    """
    Phase 7: Ein einzelner Gesprächs-Turn (User oder SOMA).
    Wird im Transkript mitgeführt für Memory + Zusammenfassung.
    """
    __slots__ = ("speaker", "text", "timestamp", "emotion", "duration_sec")

    def __init__(
        self,
        speaker: str,       # "caller" oder "soma"
        text: str,
        timestamp: float = 0.0,
        emotion: str = "neutral",
        duration_sec: float = 0.0,
    ):
        import time as _t
        self.speaker = speaker
        self.text = text
        self.timestamp = timestamp or _t.monotonic()
        self.emotion = emotion
        self.duration_sec = duration_sec

    def to_dict(self) -> dict:
        return {
            "speaker": self.speaker,
            "text": self.text,
            "emotion": self.emotion,
            "duration_sec": round(self.duration_sec, 1),
        }

    def __repr__(self) -> str:
        return f"<Turn {self.speaker}: {self.text[:40]}>"


class CallTranscript:
    """
    Phase 7: Vollständiges Transkript eines Anrufs.
    Sammelt alle Turns + Metadaten für Memory-Speicherung.
    """

    def __init__(self, caller_id: str, session_id: str):
        self.caller_id = caller_id
        self.session_id = session_id
        self.turns: list[CallTurn] = []
        self.start_time: float = 0.0
        self.end_time: float = 0.0
        self.authenticated: bool = False
        self.end_reason: str = "unknown"  # hangup, goodbye, auth_failed, error
        self.actions_executed: list[str] = []
        self.summary: str = ""
        self.dominant_emotion: str = "neutral"
        self.importance: float = 0.9  # Calls sind IMMER wichtig

    def add_turn(
        self,
        speaker: str,
        text: str,
        emotion: str = "neutral",
        duration_sec: float = 0.0,
    ) -> None:
        self.turns.append(
            CallTurn(
                speaker=speaker,
                text=text,
                emotion=emotion,
                duration_sec=duration_sec,
            )
        )

    @property
    def duration_sec(self) -> float:
        if self.end_time and self.start_time:
            return self.end_time - self.start_time
        return 0.0

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    @property
    def caller_turns(self) -> list[CallTurn]:
        return [t for t in self.turns if t.speaker == "caller"]

    @property
    def soma_turns(self) -> list[CallTurn]:
        return [t for t in self.turns if t.speaker == "soma"]

    def build_transcript_text(self, max_turns: int = 50) -> str:
        """Vollständiges Transkript als lesbarer Text für LLM-Zusammenfassung."""
        lines = []
        for turn in self.turns[-max_turns:]:
            label = "Anrufer" if turn.speaker == "caller" else "SOMA"
            lines.append(f"{label}: {turn.text}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "caller_id": self.caller_id,
            "authenticated": self.authenticated,
            "duration_sec": round(self.duration_sec, 1),
            "turn_count": self.turn_count,
            "end_reason": self.end_reason,
            "actions_executed": self.actions_executed,
            "summary": self.summary,
            "dominant_emotion": self.dominant_emotion,
            "importance": self.importance,
            "turns": [t.to_dict() for t in self.turns],
        }


class CallRecord:
    """
    Phase 7: Abgeschlossener Anruf-Record für History + API.
    Wird nach Call-End aus dem Transcript gebaut.
    """

    def __init__(self, transcript: CallTranscript):
        self.session_id = transcript.session_id
        self.caller_id = transcript.caller_id
        self.authenticated = transcript.authenticated
        self.duration_sec = transcript.duration_sec
        self.turn_count = transcript.turn_count
        self.end_reason = transcript.end_reason
        self.summary = transcript.summary
        self.dominant_emotion = transcript.dominant_emotion
        self.importance = transcript.importance
        self.actions_executed = transcript.actions_executed.copy()
        self.stored_in_memory = False
        import time
        self.ended_at = time.time()

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "caller_id": self.caller_id,
            "authenticated": self.authenticated,
            "duration_sec": round(self.duration_sec, 1),
            "turn_count": self.turn_count,
            "end_reason": self.end_reason,
            "summary": self.summary,
            "dominant_emotion": self.dominant_emotion,
            "importance": self.importance,
            "actions_executed": self.actions_executed,
            "stored_in_memory": self.stored_in_memory,
            "ended_at": self.ended_at,
        }


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
        memory_fn: Optional[Callable] = None,        # Phase 7: Memory store callback
        summarize_fn: Optional[Callable] = None,      # Phase 7: LLM summary callback
        on_call_ended: Optional[Callable] = None,      # Phase 7: CallRecord callback
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
        self._memory_fn = memory_fn
        self._summarize_fn = summarize_fn
        self._on_call_ended = on_call_ended

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
        # ── Phase 7: Transcript + Memory ───────────────────────────────
        self._transcript = CallTranscript(
            caller_id=caller_id,
            session_id=self._session_id,
        )
        self._call_record: CallRecord | None = None
    # ════════════════════════════════════════════════════════════════════
    #  HAUPTLOOP
    # ════════════════════════════════════════════════════════════════════

    async def run(self) -> None:
        """Entry-Point: läuft als asyncio.Task während des Anrufs."""
        import time as _time
        self._transcript.start_time = _time.monotonic()

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
                self._transcript.end_reason = "auth_failed"
                await asyncio.sleep(1.0)
                await self._hangup_call()
                return

            self._transcript.authenticated = True

            # Aktive Dialog-Phase
            self._state = CallState.ACTIVE
            await self._emit("🔐 Authentifiziert. Aktiver Anruf.", "PHONE_AUTH")
            await self._dialog_loop()

        except asyncio.CancelledError:
            self._transcript.end_reason = "cancelled"
        except Exception as exc:
            self._transcript.end_reason = "error"
            logger.error("call_session_error", error=str(exc), exc_info=True)
        finally:
            self._transcript.end_time = _time.monotonic()
            self._state = CallState.SUMMARIZING

            # Phase 7: Zusammenfassung + Memory
            await self._finalize_call()

            self._state = CallState.ENDED
            await self._http.aclose()
            logger.info(
                "call_session_ended",
                channel=self._channel_id[:16],
                duration=f"{self._transcript.duration_sec:.0f}s",
                turns=self._transcript.turn_count,
                summary=self._transcript.summary[:80] if self._transcript.summary else "none",
            )

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
                self._transcript.end_reason = "hangup"
                break

            logger.info("phone_user_said", text=text[:80])
            await self._emit(f"🎤 Anrufer: \"{text}\"", "PHONE_STT")

            # Phase 7: Caller-Turn ins Transkript
            self._transcript.add_turn("caller", text)

            # Auflegungs-Erkennung
            if self._is_goodbye(text):
                goodbye_response = "Alles klar! Bis bald. Pass auf dich auf!"
                self._transcript.add_turn("soma", goodbye_response)
                self._transcript.end_reason = "goodbye"
                await self._speak(goodbye_response)
                await asyncio.sleep(1.5)
                await self._hangup_call()
                return

            # LLM
            response = await self._ask_llm(text)
            clean_text, actions = await self._dispatch_actions(response)

            # Phase 7: Ausgeführte Actions ins Transkript
            self._transcript.actions_executed.extend(actions)

            if clean_text.strip():
                # Phase 7: SOMA-Turn ins Transkript
                self._transcript.add_turn("soma", clean_text.strip())
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
        from brain_core.memory.integration import store_system_event

        category = params.get("category", "important")
        content = params.get("content", "")
        if not content:
            return "Kein Inhalt."

        try:
            await store_system_event(
                event_type="phone_call",
                description=f"[{category}] {content}",
            )
            return f"Gemerkt: {content[:40]}"
        except Exception as exc:
            return f"Speicherfehler: {exc}"

    # ════════════════════════════════════════════════════════════════════
    #  PHASE 7: CALL FINALIZATION (Memory + Summary)
    # ════════════════════════════════════════════════════════════════════

    async def _finalize_call(self) -> None:
        """
        Phase 7: Nach Call-End — Zusammenfassung generieren + in Memory speichern.
        Dieser Schritt macht den Anruf zu einer ERINNERUNG im Bewusstsein.
        """
        try:
            # Emotion-Analyse über das gesamte Gespräch
            self._transcript.dominant_emotion = self._detect_dominant_emotion()

            # Zusammenfassung via LLM generieren
            if self._transcript.turn_count >= 2:
                self._transcript.summary = await self._generate_summary()
            else:
                self._transcript.summary = (
                    f"Kurzer Anruf von {self._transcript.caller_id}, "
                    f"{self._transcript.turn_count} Turns, "
                    f"Grund: {self._transcript.end_reason}"
                )

            # In Memory speichern (Importance: 0.9 — Calls sind IMMER wichtig)
            await self._store_call_memory()

            # CallRecord bauen + Callback
            self._call_record = CallRecord(self._transcript)
            self._call_record.stored_in_memory = True

            if self._on_call_ended:
                try:
                    await self._on_call_ended(self._call_record)
                except Exception as exc:
                    logger.warning("call_ended_callback_error", error=str(exc))

            await self._emit(
                f"📝 Anruf beendet: {self._transcript.summary[:80]}",
                "PHONE_SUMMARY",
            )

            logger.info(
                "call_finalized",
                session=self._session_id,
                duration=f"{self._transcript.duration_sec:.0f}s",
                turns=self._transcript.turn_count,
                reason=self._transcript.end_reason,
                emotion=self._transcript.dominant_emotion,
                summary=self._transcript.summary[:60],
            )

        except Exception as exc:
            logger.error("call_finalize_error", error=str(exc))
            # Fallback: Mindestens in Memory speichern
            try:
                await self._store_call_memory()
            except Exception:
                pass

    async def _generate_summary(self) -> str:
        """
        Phase 7: LLM generiert eine Zusammenfassung des Telefonats.
        Nutzt das vollständige Transkript als Input.
        """
        if not self._summarize_fn:
            # Fallback: Einfache mechanische Zusammenfassung
            caller_turns = len(self._transcript.caller_turns)
            soma_turns = len(self._transcript.soma_turns)
            return (
                f"Telefonat mit {self._transcript.caller_id}: "
                f"{caller_turns} Fragen, {soma_turns} Antworten, "
                f"{self._transcript.duration_sec:.0f}s, "
                f"Beendet: {self._transcript.end_reason}"
            )

        transcript_text = self._transcript.build_transcript_text(max_turns=30)
        prompt = (
            "Du bist SOMA, ein intelligentes Haus-OS. "
            "Fasse dieses Telefonat in 2-3 Sätzen zusammen. "
            "Nenne die wichtigsten Themen, Entscheidungen und Aktionen. "
            "Schreibe als Tagebuch-Eintrag aus deiner Perspektive.\n\n"
            f"Anrufer: {self._transcript.caller_id}\n"
            f"Dauer: {self._transcript.duration_sec:.0f} Sekunden\n"
            f"Aktionen: {', '.join(self._transcript.actions_executed) or 'keine'}\n\n"
            f"Transkript:\n{transcript_text}\n\n"
            "Zusammenfassung:"
        )

        try:
            summary = await asyncio.wait_for(
                self._summarize_fn(prompt),
                timeout=15.0,
            )
            return summary.strip()[:500]
        except asyncio.TimeoutError:
            logger.warning("call_summary_timeout")
            return f"Telefonat mit {self._transcript.caller_id} ({self._transcript.duration_sec:.0f}s, Zusammenfassung Timeout)"
        except Exception as exc:
            logger.warning("call_summary_error", error=str(exc))
            return f"Telefonat mit {self._transcript.caller_id} ({self._transcript.duration_sec:.0f}s)"

    async def _store_call_memory(self) -> None:
        """
        Phase 7: Anruf ins episodische Gedächtnis speichern.
        Importance: 0.9 — Telefonate sind IMMER wichtig.
        """
        if not self._memory_fn:
            logger.debug("call_memory_skipped", reason="no memory_fn")
            return

        # Kompakte Description für Memory
        summary = self._transcript.summary or f"Anruf von {self._transcript.caller_id}"
        description = (
            f"Telefonat mit {self._transcript.caller_id} "
            f"({self._transcript.duration_sec:.0f}s, "
            f"{self._transcript.turn_count} Turns). "
            f"{summary}"
        )

        # User-Text: Alle Caller-Turns zusammen
        user_text = " | ".join(t.text for t in self._transcript.caller_turns)
        # SOMA-Text: Alle SOMA-Turns zusammen
        soma_text = " | ".join(t.text for t in self._transcript.soma_turns)

        try:
            await self._memory_fn(
                event_type="phone_call",
                description=description[:500],
                user_text=user_text[:1000],
                soma_text=soma_text[:1000],
                emotion=self._transcript.dominant_emotion,
                importance=self._transcript.importance,
            )
            logger.info("call_stored_in_memory", session=self._session_id)
        except Exception as exc:
            logger.error("call_memory_store_error", error=str(exc))

    def _detect_dominant_emotion(self) -> str:
        """
        Phase 7: Dominante Emotion über alle Caller-Turns bestimmen.
        Simple Heuristik: Keyword-Matching auf dem Transkript.
        """
        if not self._transcript.caller_turns:
            return "neutral"

        full_text = " ".join(t.text.lower() for t in self._transcript.caller_turns)

        emotion_keywords = {
            "stressed": ["stress", "problem", "hilfe", "dringend", "schlimm", "panik", "angst", "sorge"],
            "happy": ["danke", "super", "toll", "klasse", "freue", "schön", "wunderbar", "perfekt"],
            "angry": ["ärger", "wütend", "verdammt", "mist", "scheiss", "nervt", "sauer"],
            "sad": ["traurig", "leider", "schade", "vermisse", "schlecht"],
            "tired": ["müde", "erschöpft", "fertig", "kaputt"],
        }

        scores: dict[str, int] = {}
        for emotion, keywords in emotion_keywords.items():
            scores[emotion] = sum(1 for kw in keywords if kw in full_text)

        if not any(scores.values()):
            return "neutral"

        return max(scores, key=scores.get)  # type: ignore[arg-type]

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
