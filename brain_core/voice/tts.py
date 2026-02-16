"""
SOMA-AI Text-to-Speech Engine (Piper)
=======================================
Soma spricht! Deutsche Stimme via Piper TTS.
Piper ist ultra-schnell (~50ms Latenz) und 100% lokal.

Datenfluss:
  Text → PiperTTS.speak()
           │
           ├── Piper Model (ONNX, ~60MB)
           ├── WAV generiert
           └── aplay/pw-play → Lautsprecher

Features:
  - Deutsche Stimme (thorsten, eva, kerstin)
  - Emotions-Modulation: Geschwindigkeit/Pitch anpassen
  - Non-blocking: spricht in Background-Task
  - Queue: mehrere Sätze nacheinander
"""

from __future__ import annotations

import asyncio
import io
import struct
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import structlog

logger = structlog.get_logger("soma.voice.tts")

# ── TTS Config ──────────────────────────────────────────────────────────

MODELS_DIR = Path(".models/piper")
DEFAULT_VOICE = "de_DE-thorsten-high"  # Thorsten: natürliche deutsche Stimme


@dataclass
class SpeechEmotion:
    """Emotionale Modulation der Sprachausgabe."""
    speed: float = 1.0        # 0.5 = langsam (beruhigend), 1.5 = schnell
    pitch: float = 1.0        # 0.8 = tief (ernst), 1.2 = hoch (fröhlich)
    volume: float = 1.0       # 0.5 = leise (nacht), 1.0 = normal

    @classmethod
    def calm(cls) -> SpeechEmotion:
        """Beruhigender Tonfall (z.B. bei Streit-Intervention)."""
        return cls(speed=0.85, pitch=0.95, volume=0.9)

    @classmethod
    def energetic(cls) -> SpeechEmotion:
        """Energisch/Motivierend."""
        return cls(speed=1.1, pitch=1.05, volume=1.0)

    @classmethod
    def gentle(cls) -> SpeechEmotion:
        """Sanft (z.B. nachts, oder bei Traurigkeit)."""
        return cls(speed=0.8, pitch=0.9, volume=0.7)

    @classmethod
    def alert(cls) -> SpeechEmotion:
        """Aufmerksamkeit (z.B. wichtige Info)."""
        return cls(speed=1.0, pitch=1.1, volume=1.0)


class TTSEngine:
    """
    Text-to-Speech via Piper.
    Soma bekommt eine Stimme.
    """

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        output_device: Optional[str] = None,
    ):
        self._voice = voice
        self._output_device = output_device
        self._piper = None
        self._ready = False
        self._speak_queue: asyncio.Queue = asyncio.Queue()
        self._speaking = False
        self._worker_task: Optional[asyncio.Task] = None

    async def initialize(self):
        """Piper TTS Engine laden."""
        if self._ready:
            return

        try:
            from piper import PiperVoice

            models_dir = MODELS_DIR
            models_dir.mkdir(parents=True, exist_ok=True)

            # Model-Pfad (wird automatisch heruntergeladen wenn nötig)
            model_path = models_dir / f"{self._voice}.onnx"
            config_path = models_dir / f"{self._voice}.onnx.json"

            if not model_path.exists():
                logger.info("tts_downloading_voice", voice=self._voice)
                await self._download_voice(self._voice, models_dir)

            self._piper = PiperVoice.load(
                str(model_path),
                config_path=str(config_path),
                use_cuda=False,  # CPU reicht für TTS
            )

            self._ready = True
            logger.info("tts_loaded", voice=self._voice)

            # Background-Worker für die Speak-Queue starten
            self._worker_task = asyncio.create_task(self._speak_worker())

        except Exception as e:
            logger.error("tts_init_failed", error=str(e))
            # Fallback: espeak als Notlösung
            self._piper = None
            self._ready = True  # Wir markieren als "ready" und nutzen espeak-Fallback
            logger.warning("tts_using_espeak_fallback")

    async def _download_voice(self, voice: str, target_dir: Path):
        """Piper Voice Model herunterladen."""
        import httpx

        base_url = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

        # Voice-Pfad-Konvention: de/de_DE/thorsten/high/
        parts = voice.split("-")
        lang = parts[0][:2]  # "de"
        locale = parts[0]    # "de_DE"
        name = parts[1]      # "thorsten"
        quality = parts[2] if len(parts) > 2 else "medium"

        model_url = f"{base_url}/{lang}/{locale}/{name}/{quality}/{voice}.onnx"
        config_url = f"{model_url}.json"

        async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
            # Model
            logger.info("tts_downloading", file=f"{voice}.onnx")
            r = await client.get(model_url)
            r.raise_for_status()
            (target_dir / f"{voice}.onnx").write_bytes(r.content)

            # Config
            r = await client.get(config_url)
            r.raise_for_status()
            (target_dir / f"{voice}.onnx.json").write_bytes(r.content)

        logger.info("tts_downloaded", voice=voice)

    async def speak(
        self,
        text: str,
        emotion: Optional[SpeechEmotion] = None,
        priority: bool = False,
    ):
        """
        Text aussprechen (non-blocking, queued).

        Args:
            text: Auszusprechender Text
            emotion: Emotionale Modulation
            priority: True = An Anfang der Queue (z.B. für Interventionen)
        """
        if not text.strip():
            return

        item = (text, emotion or SpeechEmotion())

        if priority:
            # Priority: neue Queue mit diesem Item vorne
            # (asyncio.Queue hat kein put_front, daher Workaround)
            self._speak_queue._queue.appendleft(item)
            self._speak_queue._unfinished_tasks += 1
        else:
            await self._speak_queue.put(item)

        logger.debug("tts_queued", text=text[:50], priority=priority)

    async def _speak_worker(self):
        """Background-Worker: Spricht Sätze aus der Queue nacheinander."""
        while True:
            try:
                text, emotion = await self._speak_queue.get()
                self._speaking = True

                await self._synthesize_and_play(text, emotion)

                self._speaking = False
                self._speak_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("tts_speak_error", error=str(e))
                self._speaking = False

    async def _synthesize_and_play(self, text: str, emotion: SpeechEmotion):
        """Text → WAV → Abspielen."""
        start = time.monotonic()

        if self._piper:
            await self._speak_piper(text, emotion)
        else:
            await self._speak_espeak(text, emotion)

        elapsed = (time.monotonic() - start) * 1000
        logger.info("tts_spoken", text=text[:60], ms=round(elapsed, 0))

    async def _speak_piper(self, text: str, emotion: SpeechEmotion):
        """Synthese via Piper."""
        # Piper synthetisiert in einen WAV-Buffer
        audio_buffer = io.BytesIO()

        with wave.open(audio_buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self._piper.config.sample_rate)

            self._piper.synthesize(
                text,
                wav,
                length_scale=1.0 / emotion.speed,   # Piper: length_scale ist invers
                sentence_silence=0.3,
            )

        audio_buffer.seek(0)

        # Abspielen via aplay (non-blocking subprocess)
        cmd = ["aplay", "-q"]  # -q = quiet (kein Output)
        if self._output_device:
            cmd.extend(["-D", self._output_device])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate(input=audio_buffer.read())

    async def _speak_espeak(self, text: str, emotion: SpeechEmotion):
        """Fallback: espeak-ng (klingt robotisch, aber funktioniert immer)."""
        speed = int(150 * emotion.speed)
        pitch = int(50 * emotion.pitch)

        proc = await asyncio.create_subprocess_exec(
            "espeak-ng", "-v", "de", "-s", str(speed), "-p", str(pitch), text,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    @property
    def queue_size(self) -> int:
        return self._speak_queue.qsize()

    async def stop_speaking(self):
        """Aktuelle Sprachausgabe abbrechen."""
        # Queue leeren
        while not self._speak_queue.empty():
            try:
                self._speak_queue.get_nowait()
                self._speak_queue.task_done()
            except asyncio.QueueEmpty:
                break

    async def shutdown(self):
        """TTS Engine herunterfahren."""
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        self._piper = None
        self._ready = False
        logger.info("tts_shutdown")
