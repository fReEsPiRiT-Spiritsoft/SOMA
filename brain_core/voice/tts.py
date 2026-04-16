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
    """
    Emotionale Modulation der Sprachausgabe.

    Phase 4: Erweitert mit from_emotion() — automatisches Prosodie-Mapping
    basierend auf dem emotionalen Zustand des Nutzers.

    Somas Stimme reagiert empathisch:
      - User happy    → Soma wird lebhafter (schneller, hoeherer Pitch)
      - User sad      → Soma wird sanfter (langsamer, tieferer Pitch, leiser)
      - User stressed → Soma wird beruhigend (ruhig, kurz, Pausen)
      - User angry    → Soma bleibt sachlich-neutral (kein Gegendruck!)
      - User tired    → Soma wird ruhig und warm (langsam, leise)
    """
    speed: float = 1.0        # 0.5 = langsam (beruhigend), 1.5 = schnell (Standard: natürlich)
    pitch: float = 1.0        # 0.8 = tief (ernst), 1.2 = hoch (froehlich)
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

    # ── Phase 4: Emotion-State Auto-Mapping ─────────────────────────

    @classmethod
    def warm(cls) -> SpeechEmotion:
        """Warm und freundlich (guter Zustand des Users)."""
        return cls(speed=1.0, pitch=1.02, volume=0.95)

    @classmethod
    def empathetic(cls) -> SpeechEmotion:
        """Empathisch (User ist muede/erschoepft)."""
        return cls(speed=0.82, pitch=0.92, volume=0.75)

    @classmethod
    def neutral_sachlich(cls) -> SpeechEmotion:
        """Sachlich-neutral (User ist wuetend → kein Gegendruck!)."""
        return cls(speed=0.95, pitch=0.98, volume=0.85)

    @classmethod
    def from_emotion(cls, emotion_state: str) -> SpeechEmotion:
        """
        Phase 4: Automatisches Prosodie-Mapping.

        Reagiert empathisch auf den emotionalen Zustand des Nutzers:
          happy/excited → lebhafter, hoeherer Pitch (Mitfreude)
          sad           → sanfter, langsamer, leiser (Trost)
          stressed      → beruhigend, ruhig (Deeskalation)
          angry         → sachlich-neutral (kein Gegendruck!)
          anxious       → warm, ruhig, reassuring
          tired         → empathisch, leise, langsam
          calm          → warm, normal
          neutral       → Standard

        Args:
            emotion_state: EmotionState value string
        """
        _map = {
            "happy": cls.energetic,
            "excited": cls.energetic,
            "sad": cls.gentle,
            "stressed": cls.calm,
            "angry": cls.neutral_sachlich,
            "anxious": cls.calm,
            "calm": cls.warm,
            "neutral": lambda: cls(speed=1.0, pitch=1.0, volume=0.95),
        }
        factory = _map.get(emotion_state, lambda: cls())
        return factory()

    @classmethod
    def from_voice_emotion(
        cls,
        emotion_vector: dict,
    ) -> SpeechEmotion:
        """
        Phase 4: Feingranulares Mapping aus VoiceEmotionVector.

        Statt diskreter Zuweisung: gewichtete Interpolation
        zwischen Prosodie-Presets basierend auf dem Emotion-Mix.

        Args:
            emotion_vector: Dict mit {happy, sad, stressed, tired, angry, neutral}
        """
        if not emotion_vector:
            return cls()

        # Presets als Zahlenwerte
        presets = {
            "happy":    {"speed": 1.1,  "pitch": 1.05, "volume": 1.0},
            "sad":      {"speed": 0.8,  "pitch": 0.9,  "volume": 0.7},
            "stressed": {"speed": 0.85, "pitch": 0.95, "volume": 0.9},
            "tired":    {"speed": 0.82, "pitch": 0.92, "volume": 0.75},
            "angry":    {"speed": 0.95, "pitch": 0.98, "volume": 0.85},
            "neutral":  {"speed": 1.0,  "pitch": 1.0,  "volume": 0.95},
        }

        speed = 0.0
        pitch = 0.0
        volume = 0.0
        total_weight = 0.0

        for emo, preset in presets.items():
            w = float(emotion_vector.get(emo, 0.0))
            if w > 0.05:  # Nur relevante Emotionen
                speed += preset["speed"] * w
                pitch += preset["pitch"] * w
                volume += preset["volume"] * w
                total_weight += w

        if total_weight < 0.1:
            return cls()

        return cls(
            speed=round(speed / total_weight, 2),
            pitch=round(pitch / total_weight, 2),
            volume=round(volume / total_weight, 2),
        )


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
        micro=None,
    ):
        """
        Text aussprechen (non-blocking, queued).

        Args:
            text: Auszusprechender Text
            emotion: Emotionale Modulation
            priority: True = An Anfang der Queue (z.B. für Interventionen)
            micro: MicroExpression für subtile Prosodie-Tells (None = neutral)
        """
        if not text.strip():
            return

        item = (text, emotion or SpeechEmotion(), micro)

        if priority:
            # Priority: Item vorne einfügen UND Worker aufwecken
            # asyncio.Queue.put() weckt get()-Wartende auf, appendleft nicht!
            # Lösung: Alle Items aus Queue holen, neues vorne, alle wieder rein
            temp_items = []
            while not self._speak_queue.empty():
                try:
                    temp_items.append(self._speak_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            # Neues Item zuerst
            await self._speak_queue.put(item)
            # Dann die alten wieder
            for old_item in temp_items:
                await self._speak_queue.put(old_item)
        else:
            await self._speak_queue.put(item)

        logger.debug("tts_queued", text=text[:50], priority=priority, queue_size=self._speak_queue.qsize())

    async def _speak_worker(self):
        """Background-Worker: Spricht Sätze aus der Queue nacheinander."""
        while True:
            try:
                text, emotion, micro = await self._speak_queue.get()
                self._speaking = True
                self._speaking_since = time.monotonic()

                await self._synthesize_and_play(text, emotion, micro)

                self._speaking = False
                self._speak_queue.task_done()

            except asyncio.CancelledError:
                self._speaking = False
                break
            except Exception as e:
                logger.error("tts_speak_error", error=str(e))
                self._speaking = False

    async def _synthesize_and_play(self, text: str, emotion: SpeechEmotion, micro=None):
        """Text → WAV → Abspielen. Optional mit Micro-Expression."""
        start = time.monotonic()

        if self._piper:
            await self._speak_piper(text, emotion, micro)
        else:
            await self._speak_espeak(text, emotion)

        elapsed = (time.monotonic() - start) * 1000
        logger.info("tts_spoken", text=text[:60], ms=round(elapsed, 0))

    async def _speak_piper(self, text: str, emotion: SpeechEmotion, micro=None):
        """Synthese via Piper (neue API mit SynthesisConfig) + Micro-Expressions."""
        import concurrent.futures
        from piper.config import SynthesisConfig
        
        # Rate: Emotion-Basis × Micro-Expression-Faktor
        # Micro-Expression wird nativ via length_scale angewandt (kein Post-Processing).
        effective_speed = emotion.speed * (micro.rate_factor if micro else 1.0)
        
        # SynthesisConfig für emotionale Modulation
        syn_config = SynthesisConfig(
            length_scale=1.0 / effective_speed,  # Piper: length_scale ist invers
            noise_scale=0.667,                   # Standard-Wert
            noise_w_scale=0.8,                   # Standard-Wert
            volume=emotion.volume,
        )
        
        # Piper-Synthese in Thread-Executor auslagern (blocking CPU-Code)
        # → Event Loop bleibt frei → asyncio.sleep-Timer (z.B. Reminder) feuern pünktlich
        piper_instance = self._piper
        loop = asyncio.get_event_loop()

        def _synthesize_blocking():
            audio_chunks = []
            for chunk in piper_instance.synthesize(text, syn_config=syn_config):
                int16_audio = (chunk.audio_float_array * 32767).astype(np.int16)
                audio_chunks.append(int16_audio)
            return audio_chunks

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            audio_chunks = await loop.run_in_executor(executor, _synthesize_blocking)
        
        if not audio_chunks:
            return
        
        # Alle Chunks zusammenfügen
        all_audio = np.concatenate(audio_chunks)
        
        # ── Micro-Expression Audio Post-Processing ──────────────────
        # Pitch-Shift, Volume-Anpassung, Stille-Pausen.
        # Rate wird oben nativ via SynthesisConfig.length_scale gehandhabt.
        if micro and not micro.is_neutral:
            from brain_core.voice.micro_expressions import apply_micro_to_audio
            all_audio = apply_micro_to_audio(
                all_audio, micro, self._piper.config.sample_rate,
            )
        
        # In WAV-Buffer schreiben
        audio_buffer = io.BytesIO()
        with wave.open(audio_buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self._piper.config.sample_rate)
            wav.writeframes(all_audio.tobytes())

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
        """
        Watchdog: 60s Limit — lange Antworten können 10-15s Sprechzeit haben.
        5s war zu kurz → bei langen Antworten wurde _speaking fälschlich
        zurückgesetzt, Mikro ging auf und STT hörte Somas eigene Stimme.
        """
        if self._speaking and hasattr(self, '_speaking_since'):
            stuck_seconds = time.monotonic() - self._speaking_since
            if stuck_seconds > 60:
                logger.warning("tts_speaking_stuck_reset",
                               stuck_s=round(stuck_seconds, 1),
                               msg="Watchdog: 60s Limit erreicht → Reset")
                self._speaking = False
        return self._speaking

    @property
    def queue_size(self) -> int:
        return self._speak_queue.qsize()

    async def stop_speaking(self):
        """Aktuelle Sprachausgabe abbrechen."""
        # Flag sofort zurücksetzen — verhindert Permanent-Self-Mute
        self._speaking = False

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

    async def speak_to_file(self, text: str, output_path: Path) -> None:
        """
        Synthese direkt in eine WAV-Datei — KEIN Abspielen.
        Genutzt vom Phone-Gateway: TTS-Audio wird in Datei geschrieben,
        dann von Asterisk über ARI abgespielt.

        Args:
            text:        Auszusprechender Text
            output_path: Ziel-Datei (wird überschrieben)
        """
        if not self._ready:
            await self.initialize()

        if self._piper:
            await self._piper_to_file(text, output_path)
        else:
            await self._espeak_to_file(text, output_path)

    async def _piper_to_file(self, text: str, output_path: Path) -> None:
        """Piper-Synthese → WAV-Datei (ohne aplay)."""
        import concurrent.futures
        from piper.config import SynthesisConfig

        syn_config = SynthesisConfig(
            length_scale=1.0 / 1.0,  # Neutrale Geschwindigkeit
            noise_scale=0.667,
            noise_w_scale=0.8,
            volume=1.0,
        )

        piper_instance = self._piper
        loop = asyncio.get_event_loop()

        def _synthesize() -> list:
            chunks = []
            for chunk in piper_instance.synthesize(text, syn_config=syn_config):
                int16 = (chunk.audio_float_array * 32767).astype(np.int16)
                chunks.append(int16)
            return chunks

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            chunks = await loop.run_in_executor(ex, _synthesize)

        if not chunks:
            logger.warning("piper_to_file_empty", text=text[:40])
            return

        all_audio = np.concatenate(chunks)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_path), "wb") as wav_out:
            wav_out.setnchannels(1)
            wav_out.setsampwidth(2)
            wav_out.setframerate(self._piper.config.sample_rate)
            wav_out.writeframes(all_audio.tobytes())

        logger.debug("tts_to_file", path=str(output_path), text=text[:40])

    async def _espeak_to_file(self, text: str, output_path: Path) -> None:
        """espeak-ng → WAV-Datei (Fallback wenn Piper nicht verfügbar)."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            "espeak-ng", "-v", "de", "-w", str(output_path), text,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
