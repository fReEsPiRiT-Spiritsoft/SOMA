"""
SOMA-AI Voice Pipeline — Das Herzstück
========================================
Wie ZORA aus Star Trek. Wie KITT aus Knight Rider.
Ein lebendiges, dauerhaft zuhörendes, emotional intelligentes Zuhause.

Architektur:
  ┌─────────────────────────────────────────────────────────────────┐
  │                    CONTINUOUS AUDIO STREAM                      │
  │          🎤 Rode Mic → Focusrite Scarlett → PipeWire/ALSA       │
  └───────────┬─────────────────────────────────────────────────────┘
              │ 16kHz mono PCM
              ▼
  ┌─────────────────────┐
  │   VAD (WebRTC)      │  ←── Frames PERMANENT, NICHT in Intervallen
  │   is_speech?        │
  └────┬──────┬─────────┘
       │      │
       │  ┌───▼───────────────┐
       │  │ Emotion Engine    │  ←── JEDES Segment analysieren
       │  │ (parallel)        │      Auch wenn Soma nicht angesprochen
       │  │ stress, mood,     │
       │  │ argument detect   │
       │  └───┬───────────────┘
       │      │
       │      ▼
       │  ┌───────────────────┐
       │  │ Ambient Intel.    │  ←── Proaktiv eingreifen?
       │  │ Streit? Stress?   │      "Euer Streit ist unproduktiv..."
       │  │ Traurigkeit?      │
       │  └───┬───────────────┘
       │      │ intervention?
       ▼      ▼
  ┌──────────────────────┐
  │   STT (Whisper)      │  ←── Sprache → Text
  │   faster-whisper     │
  └────┬─────────────────┘
       │ TranscriptionResult
       ▼
  ┌──────────────────────┐
  │  "Soma" im Text?     │  ←── Trigger ÜBERALL im Satz
  │  Soma-Variants Check │      "Hey Soma mach Licht an"
  └────┬──────┬──────────┘      "Mach mal Soma das Licht an"
       │      │                  "Wie wird das Wetter Soma?"
   YES │      │ NO
       ▼      ▼
  ┌──────┐ ┌──────────────┐
  │ LLM  │ │ Still zuhören │  ←── Emotion weiter tracken
  │ Llama│ │ (passiv)      │
  └──┬───┘ └──────────────┘
     │ response
     ▼
  ┌──────────────────────┐
  │   TTS (Piper)        │  ←── Deutsche Stimme
  │   + Emotion Modulate │      Tonfall passt zu Situation
  └──────────────────────┘

Features:
  ✅ Dauerhaftes Zuhören (nicht in Intervallen!)
  ✅ Soma reagiert wenn "Soma" IRGENDWO im Satz vorkommt
  ✅ Echtzeit Emotion/Mood/Stress Tracking
  ✅ Proaktives Eingreifen (Streit, Stress, Traurigkeit)
  ✅ SmartHome-Steuerung via Nano-Intent
  ✅ Smalltalk wenn direkt angesprochen
  ✅ Kindererkennung (Pitch-basiert)
  ✅ Self-Mute: Soma hört nicht zu während es selbst spricht
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from datetime import datetime
from typing import Optional

import numpy as np
import structlog

from brain_core.voice.vad import (
    ContinuousVAD,
    SpeechSegment,
    VAD_FRAME_BYTES,
    VAD_FRAME_MS,
    VAD_SAMPLE_RATE,
    resample_to_16khz,
    float32_to_pcm16,
)
from brain_core.voice.stt import STTEngine, TranscriptionResult
from brain_core.voice.tts import TTSEngine, SpeechEmotion
from brain_core.voice.emotion import EmotionEngine, EmotionState, RoomMood
from brain_core.voice.ambient import AmbientIntelligence, Intervention

logger = structlog.get_logger("soma.voice.pipeline")

# ── Pipeline Config ─────────────────────────────────────────────────────

AUDIO_DEVICE = "default"       # ALSA device (arecord)
AUDIO_RATE = 16000             # Direkt 16kHz aufnehmen (spart Resampling)
AUDIO_CHANNELS = 1             # Mono
AUDIO_FORMAT = "S16_LE"        # 16-bit signed little-endian PCM
FRAME_SIZE = int(VAD_SAMPLE_RATE * VAD_FRAME_MS / 1000) * 2  # 960 bytes


class VoicePipeline:
    """
    Das lebendige Herz von SOMA.
    Hört PERMANENT zu, analysiert Emotionen, antwortet wenn angesprochen,
    greift proaktiv ein wenn nötig.
    """

    def __init__(
        self,
        logic_router=None,
        audio_device: str = AUDIO_DEVICE,
        stt_model: str = "small",
        tts_voice: str = "de_DE-thorsten-high",
        output_device: Optional[str] = None,
    ):
        """
        Args:
            logic_router: Brain Core LogicRouter für LLM-Anfragen
            audio_device: ALSA Input Device
            stt_model: Whisper Model Size
            tts_voice: Piper Voice Name
            output_device: ALSA Output Device (None = default)
        """
        # ── Sub-Engines ─────────────────────────────────────────────
        self.vad = ContinuousVAD(aggressiveness=2)
        self.stt = STTEngine(model_size=stt_model)
        self.tts = TTSEngine(voice=tts_voice, output_device=output_device)
        self.emotion = EmotionEngine(window_sec=60.0)
        self.ambient = AmbientIntelligence(emotion_engine=self.emotion)

        # ── External References ─────────────────────────────────────
        self._logic_router = logic_router
        self._audio_device = audio_device

        # ── State ───────────────────────────────────────────────────
        self._running = False
        self._arecord_proc: Optional[asyncio.subprocess.Process] = None
        self._pipeline_task: Optional[asyncio.Task] = None
        self._stats = {
            "segments_processed": 0,
            "transcriptions": 0,
            "soma_triggers": 0,
            "interventions": 0,
            "uptime_start": 0.0,
        }

    # ══════════════════════════════════════════════════════════════════
    #  LIFECYCLE
    # ══════════════════════════════════════════════════════════════════

    async def start(self):
        """
        Voice Pipeline starten.
        Ab jetzt hört Soma PERMANENT zu.
        """
        if self._running:
            logger.warning("pipeline_already_running")
            return

        logger.info("pipeline_starting", phase="init")

        # 1. STT Model laden (lazy, braucht ~5s)
        await self.stt.initialize()
        logger.info("pipeline_starting", phase="stt_ready")

        # 2. TTS Engine starten
        await self.tts.initialize()
        logger.info("pipeline_starting", phase="tts_ready")

        # 3. Pipeline-Loop starten
        self._running = True
        self._stats["uptime_start"] = time.time()
        self._pipeline_task = asyncio.create_task(self._audio_loop())

        logger.info(
            "pipeline_online",
            device=self._audio_device,
            msg="🎤 Soma hört zu. Dauerhaft. Wie ZORA.",
        )

    async def stop(self):
        """Voice Pipeline stoppen."""
        logger.info("pipeline_stopping")
        self._running = False

        # arecord beenden
        if self._arecord_proc and self._arecord_proc.returncode is None:
            self._arecord_proc.terminate()
            try:
                await asyncio.wait_for(self._arecord_proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                self._arecord_proc.kill()

        # Pipeline-Task canceln
        if self._pipeline_task:
            self._pipeline_task.cancel()
            try:
                await self._pipeline_task
            except asyncio.CancelledError:
                pass

        # Sub-Engines herunterfahren
        await self.stt.shutdown()
        await self.tts.shutdown()

        logger.info("pipeline_offline", stats=self._stats)

    # ══════════════════════════════════════════════════════════════════
    #  CORE AUDIO LOOP — Das permanente Zuhören
    # ══════════════════════════════════════════════════════════════════

    async def _audio_loop(self):
        """
        HAUPTSCHLEIFE: Liest dauerhaft Audio von arecord,
        füttert VAD, verarbeitet Segmente.

        DAS IST der "immer zuhören" Part.
        Nicht in Intervallen. Nicht polling. PERMANENT.
        """
        while self._running:
            try:
                await self._run_audio_capture()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("audio_loop_error", error=str(e))
                if self._running:
                    logger.info("audio_loop_restart", delay=2)
                    await asyncio.sleep(2)  # Kurz warten, dann neu starten

    async def _run_audio_capture(self):
        """
        Startet arecord als Subprocess und liest permanent den stdout-Stream.
        Jeder Frame (30ms) wird sofort an VAD weitergegeben.
        """
        cmd = [
            "arecord",
            "-D", self._audio_device,
            "-f", AUDIO_FORMAT,
            "-r", str(AUDIO_RATE),
            "-c", str(AUDIO_CHANNELS),
            "-t", "raw",       # Kein WAV-Header, reiner PCM Stream
            "--buffer-size", "4096",
        ]

        logger.debug("arecord_starting", cmd=" ".join(cmd))

        self._arecord_proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

        reader = self._arecord_proc.stdout
        assert reader is not None

        logger.info("audio_capture_active", device=self._audio_device)

        # ── Permanent lesen, Frame für Frame ─────────────────────────
        while self._running:
            # Exakt ein VAD-Frame lesen (960 bytes = 30ms bei 16kHz)
            data = await reader.read(FRAME_SIZE)

            if not data:
                # arecord wurde beendet
                logger.warning("arecord_eof")
                break

            if len(data) < FRAME_SIZE:
                # Unvollständiger Frame am Ende → verwerfen
                continue

            # Self-Mute: Wenn Soma gerade spricht, nicht zuhören
            # (verhindert Feedback-Loop)
            if self.tts.is_speaking:
                continue

            # VAD: Ist das Sprache?
            segment = self.vad.feed(data)

            if segment:
                # Sprach-Segment erkannt! → Verarbeiten
                asyncio.create_task(self._process_segment(segment))

    # ══════════════════════════════════════════════════════════════════
    #  SEGMENT PROCESSING — Was passiert wenn jemand spricht
    # ══════════════════════════════════════════════════════════════════

    async def _process_segment(self, segment: SpeechSegment):
        """
        Verarbeite ein erkanntes Sprach-Segment.
        Wird für JEDES Segment aufgerufen — nicht nur wenn "Soma" gesagt wird.
        """
        self._stats["segments_processed"] += 1

        try:
            # ── 1. Emotion analysieren (IMMER, auch ohne "Soma") ─────
            emotion_reading = self.emotion.analyze(
                audio=segment.audio,
                sample_rate=VAD_SAMPLE_RATE,
                duration_sec=segment.duration_sec,
            )

            # ── 2. Proaktive Intervention prüfen ─────────────────────
            current_hour = datetime.now().hour
            intervention = self.ambient.check(current_hour=current_hour)

            if intervention:
                # Soma greift ein! (z.B. "Euer Streit ist unproduktiv...")
                await self._handle_intervention(intervention)
                # Nach Intervention trotzdem STT machen (falls Soma angesprochen)

            # ── 3. STT: Sprache → Text ───────────────────────────────
            transcription = self.stt.transcribe(
                audio=segment.audio,
                sample_rate=VAD_SAMPLE_RATE,
            )

            if not transcription.text.strip():
                return

            self._stats["transcriptions"] += 1

            logger.info(
                "voice_heard",
                text=transcription.text[:100],
                emotion=emotion_reading.emotion.value,
                soma_detected=transcription.contains_soma,
                stress=emotion_reading.stress_level,
                duration=segment.duration_sec,
            )

            # ── 4. Wurde "Soma" gesagt? ──────────────────────────────
            if transcription.contains_soma:
                await self._handle_soma_request(transcription, emotion_reading)

            # ── 5. Stille Befehle erkennen (Dismiss) ─────────────────
            self._check_dismiss(transcription.text)

        except Exception as e:
            logger.error("segment_processing_error", error=str(e))

    # ══════════════════════════════════════════════════════════════════
    #  SOMA-ANGESPROCHENE VERARBEITUNG
    # ══════════════════════════════════════════════════════════════════

    async def _handle_soma_request(
        self,
        transcription: TranscriptionResult,
        emotion_reading,
    ):
        """
        Soma wurde direkt angesprochen.
        Text an Llama 3 senden, Antwort aussprechen.
        """
        self._stats["soma_triggers"] += 1

        # "Soma" aus dem Text entfernen für den Prompt
        prompt = self._extract_prompt(transcription.text)

        if not prompt.strip():
            # Nur "Soma" gesagt, sonst nichts
            await self.tts.speak(
                "Ja?",
                SpeechEmotion(speed=1.0, pitch=1.0, volume=0.9),
            )
            return

        logger.info(
            "soma_addressed",
            prompt=prompt,
            emotion=emotion_reading.emotion.value,
        )

        # ── An Brain Core senden ─────────────────────────────────────
        if self._logic_router:
            from brain_core.logic_router import SomaRequest, SomaResponse

            # Emotionaler Kontext für den System-Prompt
            emotion_context = self.emotion.get_context_for_llm()

            request = SomaRequest(
                prompt=prompt,
                metadata={
                    "source": "voice",
                    "emotion": emotion_reading.emotion.value,
                    "stress": emotion_reading.stress_level,
                    "valence": emotion_reading.valence,
                    "arousal": emotion_reading.arousal,
                    "emotion_context": emotion_context,
                },
            )

            response = await self._logic_router.route(request)

            # Antwort aussprechen
            speech_emotion = self._select_speech_emotion(emotion_reading)
            await self.tts.speak(response.response, speech_emotion)

        else:
            # Kein Logic Router → Fallback
            await self.tts.speak(
                "Ich höre dich, aber mein Denkvermögen ist noch nicht verbunden.",
                SpeechEmotion.gentle(),
            )

    # ══════════════════════════════════════════════════════════════════
    #  PROAKTIVE INTERVENTION
    # ══════════════════════════════════════════════════════════════════

    async def _handle_intervention(self, intervention: Intervention):
        """
        Soma greift proaktiv ein — ohne angesprochen zu werden.
        Das ist der ZORA/KITT-Moment.
        """
        self._stats["interventions"] += 1

        logger.info(
            "soma_intervening",
            type=intervention.type.value,
            priority=intervention.priority,
        )

        if self._logic_router:
            from brain_core.logic_router import SomaRequest

            request = SomaRequest(
                prompt=intervention.prompt,
                metadata={
                    "source": "ambient_intervention",
                    "intervention_type": intervention.type.value,
                    "emotion_context": intervention.emotion_context,
                },
            )

            response = await self._logic_router.route(request)

            # Mit passendem Tonfall aussprechen
            emotion = SpeechEmotion.calm() if intervention.use_calm_voice else SpeechEmotion()
            await self.tts.speak(
                response.response,
                emotion,
                priority=True,  # Intervention hat Vorrang in der TTS-Queue
            )
        else:
            # Hardcoded Fallbacks wenn kein LLM verfügbar
            fallbacks = {
                "argument": "Hey, ruhig bleiben. Atmet kurz durch.",
                "stress": "Du wirkst gestresst. Gönn dir eine Pause.",
                "sadness": "Hey, alles okay bei dir?",
            }
            text = fallbacks.get(
                intervention.type.value,
                "Ich bin hier wenn du mich brauchst.",
            )
            await self.tts.speak(text, SpeechEmotion.calm(), priority=True)

    # ══════════════════════════════════════════════════════════════════
    #  HELPERS
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def _extract_prompt(text: str) -> str:
        """
        Extrahiere den eigentlichen Prompt aus dem Text.
        Entferne "Soma" und Füllwörter.

        "Hey Soma mach das Licht an"  → "mach das Licht an"
        "Soma wie wird das Wetter?"   → "wie wird das Wetter?"
        "Mach mal Soma die Musik an"  → "Mach mal die Musik an"
        """
        import re

        # Soma-Varianten entfernen (case-insensitive)
        cleaned = re.sub(
            r'\b(?:hey\s+)?(?:soma|sooma|sohma|somma|zoma)(?:\s*[,!.?])?\s*',
            ' ',
            text,
            flags=re.IGNORECASE,
        )

        # Mehrfache Leerzeichen bereinigen
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        # Füllwörter am Anfang entfernen
        filler = ["ähm", "also", "na", "hey", "hallo", "ok", "okay", "sag mal"]
        words = cleaned.split()
        while words and words[0].lower().rstrip(",.!?") in filler:
            words.pop(0)

        return " ".join(words).strip()

    def _select_speech_emotion(self, emotion_reading) -> SpeechEmotion:
        """Wähle TTS-Emotion basierend auf erkannter User-Emotion."""
        emotion = emotion_reading.emotion

        if emotion in (EmotionState.SAD, EmotionState.ANXIOUS):
            return SpeechEmotion.gentle()
        if emotion in (EmotionState.ANGRY, EmotionState.STRESSED):
            return SpeechEmotion.calm()
        if emotion in (EmotionState.HAPPY, EmotionState.EXCITED):
            return SpeechEmotion.energetic()
        return SpeechEmotion()  # Neutral

    def _check_dismiss(self, text: str):
        """Prüfe ob Nutzer Soma zum Schweigen bringt."""
        dismiss_phrases = [
            "soma halt die klappe",
            "soma sei still",
            "soma hör auf",
            "soma stop",
            "soma ruhe",
            "soma genug",
            "soma stille",
            "soma schweig",
        ]
        text_lower = text.lower()
        for phrase in dismiss_phrases:
            if phrase in text_lower:
                self.ambient.user_dismisses()
                self.tts.speak(
                    "Alles klar, ich halte mich zurück.",
                    SpeechEmotion.gentle(),
                )
                return

    # ══════════════════════════════════════════════════════════════════
    #  STATUS & STATS
    # ══════════════════════════════════════════════════════════════════

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict:
        s = dict(self._stats)
        if s["uptime_start"]:
            s["uptime_sec"] = round(time.time() - s["uptime_start"], 1)
        s["vad_speech_ratio"] = round(self.vad.speech_ratio, 3)
        s["tts_queue"] = self.tts.queue_size
        s["tts_speaking"] = self.tts.is_speaking
        s["atmosphere"] = {
            "mood": self.emotion.atmosphere.mood.value,
            "stress": self.emotion.atmosphere.avg_stress,
            "valence": self.emotion.atmosphere.avg_valence,
            "trend": self.emotion.atmosphere.trend,
            "argument_likelihood": self.emotion.atmosphere.argument_likelihood,
        }
        s["ambient"] = self.ambient.stats
        return s
