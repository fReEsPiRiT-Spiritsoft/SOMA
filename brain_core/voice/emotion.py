"""
SOMA-AI Echtzeit Emotion Engine
=================================
Analysiert JEDES Sprach-Segment auf emotionalen Zustand.
Läuft DAUERHAFT im Hintergrund — nicht erst wenn Soma angesprochen wird.

Soma "fühlt" die Atmosphäre im Raum:
  - Jemand ist gestresst → Soma merkt es
  - Streit zwischen Personen → Soma erkennt es
  - Traurigkeit → Soma passt Verhalten an
  - Gute Laune → Soma wird lockerer

Features:
  - Audio-basierte Emotion (kein ML-Model nötig, rein akustisch)
  - Sliding Window über letzte 60 Sekunden
  - Trend-Erkennung (wird es besser oder schlechter?)
  - Multi-Speaker Tracking
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
import structlog

logger = structlog.get_logger("soma.voice.emotion")


class EmotionState(str, Enum):
    """Erkannte Emotions-Zustände."""
    CALM = "calm"
    HAPPY = "happy"
    EXCITED = "excited"
    STRESSED = "stressed"
    ANGRY = "angry"
    SAD = "sad"
    ANXIOUS = "anxious"
    NEUTRAL = "neutral"


class RoomMood(str, Enum):
    """Gesamtstimmung im Raum."""
    PEACEFUL = "peaceful"
    LIVELY = "lively"
    TENSE = "tense"
    ARGUMENT = "argument"
    QUIET = "quiet"
    UNKNOWN = "unknown"


@dataclass
class EmotionReading:
    """Ein einzelner Emotions-Messwert."""
    timestamp: float
    emotion: EmotionState
    confidence: float
    valence: float        # -1 (negativ) bis +1 (positiv)
    arousal: float        # 0 (ruhig) bis 1 (aufgeregt)
    stress_level: float   # 0.0 - 1.0

    # Audio-Features die zur Bestimmung genutzt wurden
    f0: float             # Grundfrequenz
    f0_variance: float    # Pitch-Variabilität
    rms: float            # Lautstärke
    speech_rate: float    # Geschätzte Sprechgeschwindigkeit
    spectral_centroid: float  # Klangfarbe (hell/dunkel)


@dataclass
class RoomAtmosphere:
    """Aktuelle Raumatmosphäre (gleitender Durchschnitt)."""
    mood: RoomMood = RoomMood.UNKNOWN
    avg_valence: float = 0.0
    avg_arousal: float = 0.0
    avg_stress: float = 0.0
    trend: str = "stable"            # "improving", "stable", "worsening"
    speakers_detected: int = 0
    argument_likelihood: float = 0.0  # 0.0 - 1.0
    duration_sec: float = 0.0
    last_update: float = 0.0


class EmotionEngine:
    """
    Echtzeit-Emotionsanalyse auf Audio-Segmenten.
    Trackt die Atmosphäre über Zeit und erkennt kritische Muster.
    """

    # ── Intervention Thresholds ──────────────────────────────────────
    STRESS_THRESHOLD = 0.7        # Ab hier: Soma bietet Hilfe an
    ARGUMENT_THRESHOLD = 0.6      # Ab hier: Streit-Intervention
    SADNESS_DURATION_SEC = 120    # 2 Min traurig → Soma reagiert

    def __init__(self, window_sec: float = 60.0):
        """
        Args:
            window_sec: Zeitfenster für gleitenden Durchschnitt (Default: 60s)
        """
        self._window_sec = window_sec
        self._readings: deque[EmotionReading] = deque()
        self._atmosphere = RoomAtmosphere()
        self._last_f0_values: deque[float] = deque(maxlen=50)
        self._segment_count = 0

        # Tracking für Argument-Detection
        self._rapid_speaker_changes = 0
        self._last_speaker_rms = 0.0
        self._loud_segments_in_row = 0

    def analyze(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        duration_sec: float = 0.0,
    ) -> EmotionReading:
        """
        Analysiere ein Audio-Segment auf Emotion.
        Wird für JEDES Segment aufgerufen — nicht nur wenn Soma angesprochen wird.
        """
        self._segment_count += 1
        now = time.time()

        # ── Audio Features extrahieren ───────────────────────────────
        rms = float(np.sqrt(np.mean(audio ** 2)))
        peak = float(np.max(np.abs(audio)))
        f0 = self._estimate_f0(audio, sample_rate)
        f0_variance = self._calc_f0_variance(f0)
        speech_rate = self._estimate_speech_rate(audio, sample_rate, duration_sec)
        spectral_centroid = self._calc_spectral_centroid(audio, sample_rate)

        # ── Emotion bestimmen ────────────────────────────────────────
        valence, arousal = self._features_to_va(
            f0, f0_variance, rms, speech_rate, spectral_centroid
        )
        stress = self._calc_stress(arousal, f0_variance, rms)
        emotion = self._classify_emotion(valence, arousal, rms, f0)

        reading = EmotionReading(
            timestamp=now,
            emotion=emotion,
            confidence=0.6 + min(self._segment_count, 10) * 0.03,  # Wird sicherer über Zeit
            valence=round(valence, 3),
            arousal=round(arousal, 3),
            stress_level=round(stress, 3),
            f0=round(f0, 1) if f0 else 0.0,
            f0_variance=round(f0_variance, 3),
            rms=round(rms, 4),
            speech_rate=round(speech_rate, 1),
            spectral_centroid=round(spectral_centroid, 1),
        )

        self._readings.append(reading)
        self._cleanup_old_readings(now)
        self._update_atmosphere(now)
        self._detect_argument_pattern(rms)

        logger.debug(
            "emotion_reading",
            emotion=emotion.value,
            valence=reading.valence,
            arousal=reading.arousal,
            stress=reading.stress_level,
            f0=reading.f0,
        )

        return reading

    # ── Audio Feature Extraction ─────────────────────────────────────

    def _estimate_f0(self, audio: np.ndarray, sr: int) -> float:
        """Grundfrequenz via Autokorrelation."""
        if len(audio) < sr * 0.05:  # Min 50ms
            return 0.0

        # Windowed autocorrelation
        frame = audio[:min(len(audio), sr)]  # Max 1s
        frame = frame * np.hanning(len(frame))

        corr = np.correlate(frame, frame, mode="full")
        corr = corr[len(corr) // 2:]

        # Suche erstes lokales Maximum nach dem Nulldurchgang
        min_lag = int(sr / 500)   # Max 500 Hz
        max_lag = int(sr / 60)    # Min 60 Hz

        if max_lag >= len(corr):
            return 0.0

        corr_slice = corr[min_lag:max_lag]
        if len(corr_slice) == 0:
            return 0.0

        peak_idx = np.argmax(corr_slice) + min_lag

        if corr[peak_idx] < 0.2 * corr[0]:
            return 0.0

        f0 = sr / peak_idx
        self._last_f0_values.append(f0)
        return f0

    def _calc_f0_variance(self, current_f0: float) -> float:
        """Pitch-Variabilität über die letzten Messungen."""
        if len(self._last_f0_values) < 3:
            return 0.0
        vals = list(self._last_f0_values)
        return float(np.std(vals) / max(np.mean(vals), 1.0))

    def _estimate_speech_rate(
        self, audio: np.ndarray, sr: int, duration: float
    ) -> float:
        """Grobe Schätzung der Sprechgeschwindigkeit (Silben/Sekunde)."""
        if duration <= 0:
            duration = len(audio) / sr

        # Energiebasierte Silbenerkennung
        frame_len = int(sr * 0.02)  # 20ms Frames
        energy = []
        for i in range(0, len(audio) - frame_len, frame_len):
            frame = audio[i:i + frame_len]
            energy.append(float(np.sum(frame ** 2)))

        if not energy:
            return 0.0

        # Threshold: Peaks über Durchschnitt = Silbe
        threshold = np.mean(energy) * 0.5
        peaks = 0
        above = False
        for e in energy:
            if e > threshold and not above:
                peaks += 1
                above = True
            elif e <= threshold:
                above = False

        return peaks / max(duration, 0.1)

    def _calc_spectral_centroid(self, audio: np.ndarray, sr: int) -> float:
        """Spectral Centroid — hohe Werte = helle Stimme, niedrig = dumpf."""
        fft = np.fft.rfft(audio)
        magnitude = np.abs(fft)
        freqs = np.fft.rfftfreq(len(audio), 1.0 / sr)

        if np.sum(magnitude) == 0:
            return 0.0

        return float(np.sum(freqs * magnitude) / np.sum(magnitude))

    # ── Emotion Classification ───────────────────────────────────────

    def _features_to_va(
        self, f0: float, f0_var: float, rms: float,
        speech_rate: float, spectral_centroid: float,
    ) -> tuple[float, float]:
        """
        Map Audio-Features auf Valence-Arousal Space.
        Russell's Circumplex Model of Affect.

        Valence: -1 (negativ) bis +1 (positiv)
        Arousal:  0 (ruhig)   bis  1 (aufgeregt)
        """
        # Arousal: Lautstärke + Sprechgeschwindigkeit + Pitch-Höhe
        arousal = 0.0
        arousal += min(rms * 8, 0.4)           # Laut = aufgeregt
        arousal += min(speech_rate / 10, 0.3)  # Schnell = aufgeregt
        if f0 > 200:
            arousal += 0.2                      # Hoher Pitch = aufgeregt
        arousal = min(arousal, 1.0)

        # Valence: Pitch-Variabilität (hoch = expressiv/positiv)
        #          Spectral Centroid (hell = positiv)
        #          Moderate Lautstärke = positiv, extrem = negativ
        valence = 0.0
        valence += min(f0_var * 2, 0.3)        # Variable Pitch = expressiv/positiv
        if 500 < spectral_centroid < 2000:
            valence += 0.2                      # Mittlere Helligkeit = positiv
        if rms > 0.15:
            valence -= 0.3                      # Sehr laut = negativ (Schreien)
        elif 0.03 < rms < 0.1:
            valence += 0.2                      # Moderate Lautstärke = positiv

        valence = max(-1.0, min(1.0, valence))

        return valence, arousal

    def _calc_stress(self, arousal: float, f0_var: float, rms: float) -> float:
        """Stress-Level berechnen."""
        stress = 0.0
        stress += arousal * 0.4              # Aufregung → Stress
        stress += min(f0_var * 3, 0.3)       # Instabiler Pitch → Stress
        if rms > 0.12:
            stress += 0.3                     # Laut → Stress
        return min(stress, 1.0)

    def _classify_emotion(
        self, valence: float, arousal: float, rms: float, f0: float,
    ) -> EmotionState:
        """Emotion aus Valence/Arousal ableiten."""
        # Circumplex Mapping
        if arousal > 0.7 and valence < -0.2:
            return EmotionState.ANGRY
        if arousal > 0.6 and valence > 0.2:
            return EmotionState.EXCITED if arousal > 0.7 else EmotionState.HAPPY
        if arousal < 0.3 and valence < -0.2:
            return EmotionState.SAD
        if arousal > 0.5 and abs(valence) < 0.2:
            return EmotionState.STRESSED
        if arousal > 0.4 and valence < -0.1:
            return EmotionState.ANXIOUS
        if arousal < 0.3 and valence > 0.0:
            return EmotionState.CALM
        return EmotionState.NEUTRAL

    # ── Argument Detection ───────────────────────────────────────────

    def _detect_argument_pattern(self, rms: float):
        """
        Streit erkennen:
        - Mehrere laute Segmente hintereinander
        - Schneller Wechsel der Lautstärke (unterschiedliche Sprecher)
        """
        threshold = 0.08  # Laut

        if rms > threshold:
            self._loud_segments_in_row += 1
        else:
            self._loud_segments_in_row = max(0, self._loud_segments_in_row - 1)

        # Schneller RMS-Wechsel = verschiedene Sprecher
        if abs(rms - self._last_speaker_rms) > 0.05:
            self._rapid_speaker_changes += 1
        else:
            self._rapid_speaker_changes = max(0, self._rapid_speaker_changes - 1)

        self._last_speaker_rms = rms

    # ── Atmosphere Tracking ──────────────────────────────────────────

    def _cleanup_old_readings(self, now: float):
        """Alte Readings außerhalb des Zeitfensters entfernen."""
        while self._readings and (now - self._readings[0].timestamp) > self._window_sec:
            self._readings.popleft()

    def _update_atmosphere(self, now: float):
        """Raumatmosphäre aus aktuellen Readings berechnen."""
        if not self._readings:
            return

        readings = list(self._readings)

        avg_v = np.mean([r.valence for r in readings])
        avg_a = np.mean([r.arousal for r in readings])
        avg_s = np.mean([r.stress_level for r in readings])

        # Trend: Vergleiche erste und zweite Hälfte
        mid = len(readings) // 2
        if mid > 0:
            first_v = np.mean([r.valence for r in readings[:mid]])
            second_v = np.mean([r.valence for r in readings[mid:]])
            if second_v - first_v > 0.1:
                trend = "improving"
            elif first_v - second_v > 0.1:
                trend = "worsening"
            else:
                trend = "stable"
        else:
            trend = "stable"

        # Argument Likelihood
        arg_score = 0.0
        if self._loud_segments_in_row > 4:
            arg_score += 0.4
        if self._rapid_speaker_changes > 5:
            arg_score += 0.3
        if avg_a > 0.6 and avg_v < -0.1:
            arg_score += 0.3
        arg_score = min(arg_score, 1.0)

        # Room Mood bestimmen
        if arg_score > self.ARGUMENT_THRESHOLD:
            mood = RoomMood.ARGUMENT
        elif avg_s > self.STRESS_THRESHOLD:
            mood = RoomMood.TENSE
        elif avg_a > 0.5 and avg_v > 0.1:
            mood = RoomMood.LIVELY
        elif avg_a < 0.2:
            mood = RoomMood.QUIET
        else:
            mood = RoomMood.PEACEFUL

        self._atmosphere = RoomAtmosphere(
            mood=mood,
            avg_valence=round(float(avg_v), 3),
            avg_arousal=round(float(avg_a), 3),
            avg_stress=round(float(avg_s), 3),
            trend=trend,
            speakers_detected=min(self._rapid_speaker_changes // 3 + 1, 5),
            argument_likelihood=round(arg_score, 2),
            duration_sec=now - readings[0].timestamp if readings else 0,
            last_update=now,
        )

    @property
    def atmosphere(self) -> RoomAtmosphere:
        return self._atmosphere

    @property
    def should_intervene(self) -> bool:
        """Soll Soma proaktiv eingreifen?"""
        a = self._atmosphere
        return (
            a.argument_likelihood > self.ARGUMENT_THRESHOLD
            or a.avg_stress > self.STRESS_THRESHOLD
            or (a.mood == RoomMood.ARGUMENT)
        )

    @property
    def intervention_reason(self) -> Optional[str]:
        """Warum sollte Soma eingreifen?"""
        a = self._atmosphere
        if a.argument_likelihood > self.ARGUMENT_THRESHOLD:
            return "argument"
        if a.avg_stress > self.STRESS_THRESHOLD:
            return "stress"
        if a.mood == RoomMood.TENSE and a.trend == "worsening":
            return "tension_rising"
        return None

    def get_context_for_llm(self) -> str:
        """
        Generiert Kontext-String für den LLM System-Prompt.
        Wird bei JEDEM Llama-Aufruf mitgegeben.
        """
        a = self._atmosphere
        if a.mood == RoomMood.UNKNOWN:
            return ""

        lines = [f"Aktuelle Raumatmosphäre: {a.mood.value}"]
        lines.append(f"Stimmung: {'positiv' if a.avg_valence > 0 else 'negativ'} ({a.avg_valence:+.2f})")
        lines.append(f"Aktivität: {'hoch' if a.avg_arousal > 0.5 else 'niedrig'}")
        lines.append(f"Stress-Level: {a.avg_stress:.0%}")
        lines.append(f"Trend: {a.trend}")

        if a.argument_likelihood > 0.3:
            lines.append(f"⚠️ Streit-Wahrscheinlichkeit: {a.argument_likelihood:.0%}")

        return "\n".join(lines)

    def reset(self):
        """State zurücksetzen."""
        self._readings.clear()
        self._last_f0_values.clear()
        self._segment_count = 0
        self._rapid_speaker_changes = 0
        self._loud_segments_in_row = 0
        self._atmosphere = RoomAtmosphere()
