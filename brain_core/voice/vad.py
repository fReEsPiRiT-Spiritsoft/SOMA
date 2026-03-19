"""
SOMA-AI Voice Activity Detection (VAD)
========================================
Dauerhaftes Zuhören – NICHT in Intervallen.
Der Audio-Stream läuft permanent. VAD entscheidet:
  - Jemand spricht → Segment sammeln
  - Stille → Segment beendet → an STT weiterreichen

Nutzt WebRTC VAD (extrem leichtgewichtig, < 1MB RAM).

Datenfluss:
  Continuous Audio → VAD.feed(chunk)
                       │
                       ├── is_speech=True  → Buffer aufbauen
                       ├── is_speech=False → Nachlauf (300ms grace)
                       └── Segment komplett → callback(audio_segment)
"""

from __future__ import annotations

import collections
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import webrtcvad
import structlog

logger = structlog.get_logger("soma.voice.vad")

# ── VAD Config ───────────────────────────────────────────────────────────

VAD_SAMPLE_RATE = 16000     # WebRTC VAD braucht 16kHz
VAD_FRAME_MS = 30           # 30ms Frames (WebRTC Standard)
VAD_FRAME_BYTES = int(VAD_SAMPLE_RATE * VAD_FRAME_MS / 1000) * 2  # 16-bit PCM
MIN_SPEECH_MS = 400         # Mindestens 400ms Sprache = valides Segment
MAX_SPEECH_SEC = 30         # Max 30s pro Segment (Schutz vor Endlos-Buffer)
SILENCE_GRACE_MS = 500      # 500ms Stille nach letztem Wort → Segment beendet
SILENCE_GRACE_SHORT_MS = 300  # 300ms für kurze Kommandos (< 2s Sprache)
PRE_SPEECH_MS = 200         # 200ms Audio VOR dem Sprechen mitnehmen


@dataclass
class SpeechSegment:
    """Ein erkanntes Sprach-Segment."""
    audio: np.ndarray          # float32 [-1, 1] bei 16kHz mono
    duration_sec: float
    start_time: float          # Unix timestamp
    end_time: float
    rms: float                 # Durchschnittliche Lautstärke
    peak: float                # Maximale Amplitude


class ContinuousVAD:
    """
    Dauerhafter Voice Activity Detector.
    Hört UNUNTERBROCHEN zu und extrahiert Sprach-Segmente.
    Kein Wake-Word nötig — jedes Segment wird analysiert.
    """

    def __init__(
        self,
        aggressiveness: int = 2,
        on_segment: Optional[Callable] = None,
        on_speech_start: Optional[Callable] = None,
        on_speech_end: Optional[Callable] = None,
    ):
        """
        Args:
            aggressiveness: 0-3, wie aggressiv Nicht-Sprache gefiltert wird.
                           2 = guter Balance für Wohnraum.
            on_segment: Callback wenn ein komplettes Segment fertig ist.
            on_speech_start: Callback wenn Sprache beginnt.
            on_speech_end: Callback wenn Sprache endet.
        """
        self._vad = webrtcvad.Vad(aggressiveness)
        self.on_segment = on_segment
        self.on_speech_start = on_speech_start
        self.on_speech_end = on_speech_end

        # State
        self._is_speaking = False
        self._speech_buffer: list[bytes] = []
        self._speech_start_time = 0.0
        self._last_speech_time = 0.0

        # Ring-Buffer für Pre-Speech Audio (300ms vor Sprechbeginn mitnehmen)
        pre_frames = int(PRE_SPEECH_MS / VAD_FRAME_MS)
        self._pre_buffer: collections.deque = collections.deque(maxlen=pre_frames)

        # Statistiken
        self._total_frames = 0
        self._speech_frames = 0

    def feed(self, pcm_16khz_16bit: bytes) -> Optional[SpeechSegment]:
        """
        Füttere VAD mit 16kHz 16-bit PCM Audio.
        Returns SpeechSegment wenn ein Segment abgeschlossen ist, sonst None.

        MUSS mit exakt VAD_FRAME_BYTES (960 bytes = 30ms bei 16kHz) gefüttert werden.
        """
        self._total_frames += 1
        now = time.time()

        try:
            is_speech = self._vad.is_speech(pcm_16khz_16bit, VAD_SAMPLE_RATE)
        except Exception:
            return None

        if is_speech:
            self._speech_frames += 1
            self._last_speech_time = now

            if not self._is_speaking:
                # ── Sprache beginnt ──────────────────────────────────
                self._is_speaking = True
                self._speech_start_time = now
                self._speech_buffer = list(self._pre_buffer)  # Pre-speech Audio
                if self.on_speech_start:
                    self.on_speech_start()

            self._speech_buffer.append(pcm_16khz_16bit)

            # Overflow-Schutz
            max_frames = int(MAX_SPEECH_SEC * 1000 / VAD_FRAME_MS)
            if len(self._speech_buffer) >= max_frames:
                return self._finalize_segment(now)

        else:
            self._pre_buffer.append(pcm_16khz_16bit)

            if self._is_speaking:
                # Noch im Grace-Window?
                self._speech_buffer.append(pcm_16khz_16bit)
                silence_ms = (now - self._last_speech_time) * 1000

                # Adaptive Grace: Kurze Befehle (< 2s) → schnellerer Cutoff
                speech_duration_ms = (now - self._speech_start_time) * 1000
                grace = SILENCE_GRACE_SHORT_MS if speech_duration_ms < 2000 else SILENCE_GRACE_MS

                if silence_ms >= grace:
                    # ── Stille lang genug → Segment fertig ───────────
                    return self._finalize_segment(now)

        return None

    def _finalize_segment(self, end_time: float) -> Optional[SpeechSegment]:
        """Sprach-Segment abschließen und zurückgeben."""
        self._is_speaking = False

        if self.on_speech_end:
            self.on_speech_end()

        if not self._speech_buffer:
            return None

        # PCM bytes → float32 numpy
        raw = b"".join(self._speech_buffer)
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

        duration = len(samples) / VAD_SAMPLE_RATE

        # Zu kurz? → Geräusch, kein Sprechen
        if duration < MIN_SPEECH_MS / 1000:
            self._speech_buffer.clear()
            return None

        rms = float(np.sqrt(np.mean(samples ** 2)))
        peak = float(np.max(np.abs(samples)))

        segment = SpeechSegment(
            audio=samples,
            duration_sec=round(duration, 2),
            start_time=self._speech_start_time,
            end_time=end_time,
            rms=round(rms, 4),
            peak=round(peak, 4),
        )

        self._speech_buffer.clear()

        logger.debug(
            "speech_segment",
            duration=segment.duration_sec,
            rms=segment.rms,
        )

        if self.on_segment:
            self.on_segment(segment)

        return segment

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    @property
    def speech_ratio(self) -> float:
        """Anteil der Frames mit Sprache (0.0 - 1.0)."""
        if self._total_frames == 0:
            return 0.0
        return self._speech_frames / self._total_frames

    def reset(self):
        """State zurücksetzen."""
        self._is_speaking = False
        self._speech_buffer.clear()
        self._pre_buffer.clear()
        self._total_frames = 0
        self._speech_frames = 0


def resample_to_16khz(audio: np.ndarray, source_rate: int) -> np.ndarray:
    """Resample Audio auf 16kHz für WebRTC VAD."""
    if source_rate == VAD_SAMPLE_RATE:
        return audio
    # Einfaches Resampling via numpy interpolation
    duration = len(audio) / source_rate
    target_samples = int(duration * VAD_SAMPLE_RATE)
    indices = np.linspace(0, len(audio) - 1, target_samples)
    return np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)


def float32_to_pcm16(audio: np.ndarray) -> bytes:
    """float32 [-1,1] → 16-bit PCM bytes für WebRTC VAD."""
    clipped = np.clip(audio, -1.0, 1.0)
    pcm = (clipped * 32767).astype(np.int16)
    return pcm.tobytes()
