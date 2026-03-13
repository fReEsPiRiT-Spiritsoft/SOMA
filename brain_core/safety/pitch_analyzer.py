"""
SOMA-AI Pitch Analyzer — Phase 4: Tiefe Stimmanalyse
=======================================================
Extrahiert aus der menschlichen Stimme:
  - F0 (Grundfrequenz) → Alter, Geschlecht
  - Jitter (Pitch-Instabilitaet) → Stress, Muedigkeit
  - Shimmer (Amplitude-Instabilitaet) → emotionale Erregung
  - Speaking Rate (Silben/s) → Aufregung, Depression
  - Energy (RMS) → Lautstaerke, Engagement
  - Spectral Centroid → Klangfarbe (hell/dunkel)

Output: VoiceEmotionVector mit 6 Emotionen (float 0-1)
  { happy, sad, stressed, tired, angry, neutral }

Confidence-Threshold: 0.65 — darunter gilt Emotion als "unerkannt"
Child-Detection: F0 > 250Hz → Child-Safe Mode

Non-Negotiable:
  - Kein ML-Modell noetig (reine Signal-Analyse, <5ms)
  - Alles synchron (wird in Thread-Pool aufgerufen)
  - Ergebnis IMMER ein VoiceEmotionVector, nie nur Integer
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import structlog

logger = structlog.get_logger("soma.safety.pitch")


# ── Emotion Vector (Phase 4 Core) ───────────────────────────────────────

@dataclass
class VoiceEmotionVector:
    """
    Emotionaler Zustand extrahiert aus der Stimme.
    Alle Werte 0.0 (nicht vorhanden) bis 1.0 (maximal).
    """
    happy: float = 0.0
    sad: float = 0.0
    stressed: float = 0.0
    tired: float = 0.0
    angry: float = 0.0
    neutral: float = 1.0

    # ── Meta ─────────────────────────────────────────────────────
    confidence: float = 0.0   # Gesamtkonfidenz (0-1)
    dominant_emotion: str = "neutral"

    # ── Raw Features (fuer Dashboard + Debugging) ────────────────
    f0_hz: float = 0.0
    jitter_percent: float = 0.0
    shimmer_percent: float = 0.0
    speaking_rate: float = 0.0  # Silben/s
    energy_rms: float = 0.0
    spectral_centroid_hz: float = 0.0

    # ── Confidence Threshold ─────────────────────────────────────
    THRESHOLD: float = 0.65

    @property
    def is_detected(self) -> bool:
        """Nur wenn Konfidenz ueber Threshold."""
        return self.confidence >= self.THRESHOLD

    @property
    def as_dict(self) -> dict[str, float]:
        """Emotion-Werte als Dict (fuer Memory-Metadata)."""
        return {
            "happy": round(self.happy, 3),
            "sad": round(self.sad, 3),
            "stressed": round(self.stressed, 3),
            "tired": round(self.tired, 3),
            "angry": round(self.angry, 3),
            "neutral": round(self.neutral, 3),
            "confidence": round(self.confidence, 3),
            "dominant": self.dominant_emotion,
        }

    def __post_init__(self):
        # Dominante Emotion bestimmen
        emotions = {
            "happy": self.happy,
            "sad": self.sad,
            "stressed": self.stressed,
            "tired": self.tired,
            "angry": self.angry,
            "neutral": self.neutral,
        }
        self.dominant_emotion = max(emotions, key=emotions.get)


# ── Pitch Result (erweitert) ────────────────────────────────────────────

@dataclass
class PitchResult:
    """Ergebnis der Stimmanalyse (Alter + Emotion)."""
    fundamental_freq_hz: float
    is_child: bool
    estimated_age_group: str  # "child" | "teen" | "adult"
    confidence: float
    stress_level: float  # 0.0 (ruhig) – 1.0 (gestresst)

    # Phase 4: Volles Emotion-Profil
    emotion_vector: VoiceEmotionVector = field(
        default_factory=VoiceEmotionVector,
    )

    # Phase 4: Erweiterte Vocal Features
    jitter_percent: float = 0.0   # Pitch Instabilitaet (%)
    shimmer_percent: float = 0.0  # Amplitude Instabilitaet (%)
    speaking_rate: float = 0.0    # Silben pro Sekunde
    energy_rms: float = 0.0       # Root Mean Square Energy
    spectral_centroid: float = 0.0  # Hz


# ── Frequenzbereiche fuer Altersgruppen ─────────────────────────────────
# Kinder (< 12):       250 – 400 Hz
# Jugendliche (12-18): 150 – 300 Hz
# Erwachsene (> 18):   85  – 255 Hz (maennlich 85-180, weiblich 165-255)

CHILD_F0_MIN = 250.0
CHILD_F0_MAX = 450.0
TEEN_F0_MIN = 150.0
TEEN_F0_MAX = 300.0
ADULT_F0_MAX = 255.0


class PitchAnalyzer:
    """
    Tiefe Stimmanalyse: Alter, Emotion, Stress, Muedigkeit.

    Phase 4 Erweiterungen:
      - Jitter: Periode-zu-Periode Variation der Pitch-Perioden
      - Shimmer: Periode-zu-Periode Variation der Amplitude
      - Speaking Rate: Silben pro Sekunde (energiebasiert)
      - Full EmotionVector: 6 Emotionen aus akustischen Features

    Alles reine Signalverarbeitung — kein ML-Modell, <5ms.
    """

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self._profiles: dict[str, list[float]] = {}

        # Sliding Window fuer zeitliche Glaettung
        self._recent_f0: deque[float] = deque(maxlen=30)
        self._recent_jitter: deque[float] = deque(maxlen=20)
        self._recent_shimmer: deque[float] = deque(maxlen=20)
        self._recent_vectors: deque[VoiceEmotionVector] = deque(maxlen=10)
        self._analysis_count: int = 0

    def analyze(
        self,
        audio_data: np.ndarray,
        sample_rate: Optional[int] = None,
        duration_sec: float = 0.0,
    ) -> PitchResult:
        """
        Analysiere Audio-Daten: Alter + Emotion + Features.

        Args:
            audio_data: PCM Audio als numpy array (mono, float32)
            sample_rate: Sample Rate (default: 16000)
            duration_sec: Dauer des Segments (0 = berechnen)

        Returns:
            PitchResult mit EmotionVector
        """
        sr = sample_rate or self.sample_rate
        self._analysis_count += 1

        if duration_sec <= 0:
            duration_sec = len(audio_data) / sr

        # ── Audio vorbereiten ────────────────────────────────────
        audio = audio_data.astype(np.float64)
        audio = audio - np.mean(audio)

        # ── Feature-Extraktion ───────────────────────────────────
        f0 = self._estimate_f0(audio, sr)
        jitter = self._compute_jitter(audio, sr, f0)
        shimmer = self._compute_shimmer(audio, sr, f0)
        speech_rate = self._estimate_speaking_rate(audio, sr, duration_sec)
        energy = float(np.sqrt(np.mean(audio ** 2)))
        spectral_centroid = self._calc_spectral_centroid(audio, sr)

        # ── History aktualisieren ────────────────────────────────
        if f0 > 0:
            self._recent_f0.append(f0)
        self._recent_jitter.append(jitter)
        self._recent_shimmer.append(shimmer)

        # ── Alter + Kind-Erkennung ───────────────────────────────
        if f0 >= CHILD_F0_MIN:
            age_group = "child"
            is_child = True
            age_conf = min(1.0, (f0 - CHILD_F0_MIN) / (CHILD_F0_MAX - CHILD_F0_MIN))
        elif f0 >= TEEN_F0_MIN and f0 < CHILD_F0_MIN:
            age_group = "teen"
            is_child = False
            age_conf = 0.7
        else:
            age_group = "adult"
            is_child = False
            age_conf = 0.9

        # ── EmotionVector berechnen ──────────────────────────────
        emotion_vector = self._features_to_emotion(
            f0=f0,
            jitter=jitter,
            shimmer=shimmer,
            speech_rate=speech_rate,
            energy=energy,
            spectral_centroid=spectral_centroid,
        )
        self._recent_vectors.append(emotion_vector)

        # ── Stress (aus EmotionVector aggregiert) ────────────────
        stress = max(emotion_vector.stressed, emotion_vector.angry * 0.8)

        result = PitchResult(
            fundamental_freq_hz=round(f0, 2),
            is_child=is_child,
            estimated_age_group=age_group,
            confidence=round(age_conf, 2),
            stress_level=round(stress, 2),
            emotion_vector=emotion_vector,
            jitter_percent=round(jitter * 100, 2),
            shimmer_percent=round(shimmer * 100, 2),
            speaking_rate=round(speech_rate, 1),
            energy_rms=round(energy, 4),
            spectral_centroid=round(spectral_centroid, 1),
        )

        if is_child:
            logger.info(
                "child_detected",
                f0=result.fundamental_freq_hz,
                confidence=result.confidence,
            )

        if emotion_vector.is_detected:
            logger.debug(
                "voice_emotion",
                dominant=emotion_vector.dominant_emotion,
                confidence=emotion_vector.confidence,
                f0=f0,
                jitter=round(jitter, 4),
                shimmer=round(shimmer, 4),
            )

        return result

    # ══════════════════════════════════════════════════════════════════
    #  FEATURE EXTRACTION
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def _estimate_f0(audio: np.ndarray, sr: int) -> float:
        """
        Grundfrequenz via Autokorrelation.
        Leichtgewichtig, kein ML. ~1ms.
        """
        if len(audio) < sr // 10:  # Mindestens 100ms Audio
            return 0.0

        # Normalisieren
        audio_norm = audio - np.mean(audio)

        # Autokorrelation
        corr = np.correlate(audio_norm, audio_norm, mode="full")
        corr = corr[len(corr) // 2:]

        min_lag = sr // 500  # Max 500 Hz
        max_lag = sr // 50   # Min 50 Hz

        if max_lag > len(corr):
            max_lag = len(corr) - 1

        corr_segment = corr[min_lag:max_lag]
        if len(corr_segment) == 0:
            return 0.0

        peak_idx = np.argmax(corr_segment) + min_lag

        # Konfidenz-Check: Peak muss deutlich sein
        if peak_idx > 0 and corr[peak_idx] > 0.2 * corr[0]:
            return sr / peak_idx

        return 0.0

    @staticmethod
    def _compute_jitter(audio: np.ndarray, sr: int, f0: float) -> float:
        """
        Jitter: Periode-zu-Periode Variation der Pitch-Perioden.

        Hoher Jitter = instabiler Pitch = Stress, Muedigkeit, Emotion.
        Normaler Jitter: 0.5-1.0%
        Gestresst:       > 1.5%
        Sehr muede:      > 2.0%

        Methode: Lokale F0-Schaetzung in 20ms-Fenstern,
                 dann relative Perioden-Variation berechnen.
        """
        if f0 <= 0 or len(audio) < sr // 5:
            return 0.0

        # In Frames aufteilen (20ms)
        frame_len = int(sr * 0.02)
        hop = frame_len // 2
        periods: list[float] = []

        for i in range(0, len(audio) - frame_len, hop):
            frame = audio[i:i + frame_len]
            frame = frame - np.mean(frame)
            frame = frame * np.hanning(len(frame))

            corr = np.correlate(frame, frame, mode="full")
            corr = corr[len(corr) // 2:]

            min_lag = max(1, int(sr / 500))
            max_lag = min(int(sr / 60), len(corr) - 1)

            if max_lag <= min_lag:
                continue

            seg = corr[min_lag:max_lag]
            if len(seg) == 0:
                continue

            peak = np.argmax(seg) + min_lag
            if corr[peak] > 0.15 * corr[0] and peak > 0:
                periods.append(peak / sr)

        if len(periods) < 3:
            return 0.0

        # Jitter = mittlere absolute Perioden-Differenz / mittlere Periode
        diffs = [abs(periods[i+1] - periods[i]) for i in range(len(periods) - 1)]
        mean_period = np.mean(periods)
        if mean_period <= 0:
            return 0.0

        jitter = np.mean(diffs) / mean_period
        return float(min(jitter, 0.1))  # Cap bei 10%

    @staticmethod
    def _compute_shimmer(audio: np.ndarray, sr: int, f0: float) -> float:
        """
        Shimmer: Periode-zu-Periode Variation der Amplitude.

        Hoher Shimmer = instabile Lautstaerke = emotionale Erregung.
        Normal:    3-5%
        Erregt:    > 7%
        Weinend:   > 10%

        Methode: RMS-Energie in Pitch-Perioden-grossen Fenstern,
                 dann relative Amplitude-Variation.
        """
        if f0 <= 0 or len(audio) < sr // 5:
            return 0.0

        period_samples = int(sr / f0)
        if period_samples < 4:
            return 0.0

        amplitudes: list[float] = []
        for i in range(0, len(audio) - period_samples, period_samples):
            frame = audio[i:i + period_samples]
            amp = float(np.sqrt(np.mean(frame ** 2)))
            if amp > 1e-6:
                amplitudes.append(amp)

        if len(amplitudes) < 3:
            return 0.0

        # Shimmer = mittlere absolute Amplitude-Differenz / mittlere Amplitude
        diffs = [
            abs(amplitudes[i+1] - amplitudes[i])
            for i in range(len(amplitudes) - 1)
        ]
        mean_amp = np.mean(amplitudes)
        if mean_amp <= 0:
            return 0.0

        shimmer = np.mean(diffs) / mean_amp
        return float(min(shimmer, 0.2))  # Cap bei 20%

    @staticmethod
    def _estimate_speaking_rate(
        audio: np.ndarray,
        sr: int,
        duration_sec: float,
    ) -> float:
        """
        Sprechgeschwindigkeit in Silben pro Sekunde.

        Methode: Energiebasierte Silbenerkennung
        Normal:    3-5 Silben/s
        Aufgeregt: > 6 Silben/s
        Traurig:   < 2 Silben/s
        """
        if duration_sec <= 0.1:
            return 0.0

        # 20ms Frames, RMS pro Frame
        frame_len = int(sr * 0.02)
        energy = []
        for i in range(0, len(audio) - frame_len, frame_len):
            frame = audio[i:i + frame_len]
            energy.append(float(np.sum(frame ** 2)))

        if not energy:
            return 0.0

        # Peaks ueber Median = Silbe
        threshold = np.median(energy) * 1.2
        peaks = 0
        above = False
        for e in energy:
            if e > threshold and not above:
                peaks += 1
                above = True
            elif e <= threshold:
                above = False

        return peaks / duration_sec

    @staticmethod
    def _calc_spectral_centroid(audio: np.ndarray, sr: int) -> float:
        """Spectral Centroid — helle vs. dumpfe Stimme."""
        if len(audio) < 256:
            return 0.0

        fft = np.fft.rfft(audio)
        magnitude = np.abs(fft)
        freqs = np.fft.rfftfreq(len(audio), 1.0 / sr)

        total = np.sum(magnitude)
        if total == 0:
            return 0.0

        return float(np.sum(freqs * magnitude) / total)

    # ══════════════════════════════════════════════════════════════════
    #  EMOTION MAPPING — Features → EmotionVector
    # ══════════════════════════════════════════════════════════════════

    def _features_to_emotion(
        self,
        f0: float,
        jitter: float,
        shimmer: float,
        speech_rate: float,
        energy: float,
        spectral_centroid: float,
    ) -> VoiceEmotionVector:
        """
        Akustische Features → 6-dimensionaler EmotionVector.

        Basiert auf psychoakustischer Forschung:
          - Happy:    hoher F0, hohe Varianz, schnelle Rate, helle Klangfarbe
          - Sad:      niedriger F0, langsame Rate, dumpfe Klangfarbe, niedrige Energie
          - Stressed: hoher Jitter, hohe Energie, schnelle Rate, hoher F0
          - Tired:    hoher Jitter, hoher Shimmer, langsame Rate, niedrige Energie
          - Angry:    sehr hohe Energie, hoher F0, schnelle Rate, hoher Centroid
          - Neutral:  alles im Normalbereich
        """
        # ── F0-Statistik (Trend ueber letzte Segmente) ──────────
        f0_mean = float(np.mean(self._recent_f0)) if self._recent_f0 else f0
        f0_std = float(np.std(self._recent_f0)) if len(self._recent_f0) > 2 else 0.0

        # ── Score-Berechnung ─────────────────────────────────────
        # Happy: hoher F0, variable Pitch, schnell, hell, moderat laut
        happy = 0.0
        if f0 > 170:
            happy += min((f0 - 170) / 150, 0.3)
        if f0_std > 20:
            happy += min(f0_std / 80, 0.25)    # Variable Pitch = expressiv
        if speech_rate > 4.0:
            happy += min((speech_rate - 4.0) / 4.0, 0.2)
        if spectral_centroid > 1200:
            happy += 0.15                        # Helle Stimme
        if 0.03 < energy < 0.12:
            happy += 0.1                         # Moderate Energie
        happy = min(happy, 1.0)

        # Sad: niedriger F0, langsam, dumpf, leise
        sad = 0.0
        if f0 > 0 and f0 < 140:
            sad += min((140 - f0) / 60, 0.3)
        if speech_rate > 0 and speech_rate < 2.5:
            sad += min((2.5 - speech_rate) / 2.0, 0.3)
        if spectral_centroid < 800:
            sad += 0.2                           # Dumpfe Stimme
        if energy < 0.03:
            sad += 0.2                           # Leise
        sad = min(sad, 1.0)

        # Stressed: hoher Jitter, laut, schnell, hoher F0
        stressed = 0.0
        if jitter > 0.012:
            stressed += min((jitter - 0.012) / 0.03, 0.35)
        if energy > 0.08:
            stressed += min((energy - 0.08) / 0.15, 0.25)
        if speech_rate > 5.0:
            stressed += min((speech_rate - 5.0) / 3.0, 0.2)
        if f0 > 180:
            stressed += min((f0 - 180) / 100, 0.2)
        stressed = min(stressed, 1.0)

        # Tired: hoher Jitter + Shimmer, langsam, leise
        tired = 0.0
        if jitter > 0.015:
            tired += min((jitter - 0.015) / 0.025, 0.3)
        if shimmer > 0.06:
            tired += min((shimmer - 0.06) / 0.08, 0.25)
        if speech_rate > 0 and speech_rate < 2.5:
            tired += min((2.5 - speech_rate) / 2.0, 0.25)
        if energy < 0.04:
            tired += 0.2
        tired = min(tired, 1.0)

        # Angry: sehr laut, hoher F0, schnell, hoher Centroid
        angry = 0.0
        if energy > 0.12:
            angry += min((energy - 0.12) / 0.15, 0.35)
        if f0 > 200:
            angry += min((f0 - 200) / 100, 0.25)
        if speech_rate > 5.5:
            angry += min((speech_rate - 5.5) / 3.0, 0.2)
        if spectral_centroid > 1500:
            angry += 0.15
        if shimmer > 0.08:
            angry += 0.1                         # Instabile Amplitude
        angry = min(angry, 1.0)

        # Neutral: Alles was uebrig bleibt
        max_emotion = max(happy, sad, stressed, tired, angry)
        neutral = max(0.0, 1.0 - max_emotion * 1.5)

        # ── Konfidenz ────────────────────────────────────────────
        # Steigt mit Anzahl der Analysen (min 3 fuer stabile Schätzung)
        base_conf = min(self._analysis_count / 5, 0.4)
        # F0 erkannt → hoehere Konfidenz
        f0_conf = 0.3 if f0 > 0 else 0.0
        # Deutliche Emotion → hoehere Konfidenz
        clarity = max_emotion - neutral * 0.5
        emotion_conf = min(max(clarity, 0.0) * 0.5, 0.3)

        confidence = min(base_conf + f0_conf + emotion_conf, 1.0)

        return VoiceEmotionVector(
            happy=round(happy, 3),
            sad=round(sad, 3),
            stressed=round(stressed, 3),
            tired=round(tired, 3),
            angry=round(angry, 3),
            neutral=round(neutral, 3),
            confidence=round(confidence, 3),
            f0_hz=round(f0, 1),
            jitter_percent=round(jitter * 100, 2),
            shimmer_percent=round(shimmer * 100, 2),
            speaking_rate=round(speech_rate, 1),
            energy_rms=round(energy, 4),
            spectral_centroid_hz=round(spectral_centroid, 1),
        )

    # ══════════════════════════════════════════════════════════════════
    #  AGGREGATION — Geglätteter EmotionVector
    # ══════════════════════════════════════════════════════════════════

    def get_smoothed_emotion(self) -> VoiceEmotionVector:
        """
        Gewichteter Durchschnitt der letzten N EmotionVectors.
        Neuere Werte zaehlen staerker (exponential decay).
        Fuer LLM-Kontext und Dashboard.
        """
        if not self._recent_vectors:
            return VoiceEmotionVector()

        vectors = list(self._recent_vectors)
        n = len(vectors)

        # Exponentielles Gewicht: neueste = 1.0, aelteste = ~0.3
        weights = [0.3 + 0.7 * (i / max(n - 1, 1)) for i in range(n)]
        total_w = sum(weights)

        happy = sum(v.happy * w for v, w in zip(vectors, weights)) / total_w
        sad = sum(v.sad * w for v, w in zip(vectors, weights)) / total_w
        stressed = sum(v.stressed * w for v, w in zip(vectors, weights)) / total_w
        tired = sum(v.tired * w for v, w in zip(vectors, weights)) / total_w
        angry = sum(v.angry * w for v, w in zip(vectors, weights)) / total_w
        neutral = sum(v.neutral * w for v, w in zip(vectors, weights)) / total_w
        conf = sum(v.confidence * w for v, w in zip(vectors, weights)) / total_w

        return VoiceEmotionVector(
            happy=round(happy, 3),
            sad=round(sad, 3),
            stressed=round(stressed, 3),
            tired=round(tired, 3),
            angry=round(angry, 3),
            neutral=round(neutral, 3),
            confidence=round(conf, 3),
        )

    # ══════════════════════════════════════════════════════════════════
    #  VOICE PROFILES
    # ══════════════════════════════════════════════════════════════════

    def register_voice_profile(
        self, user_id: str, f0_samples: list[float],
    ) -> None:
        """Stimmprofil fuer spaeteren Vergleich registrieren."""
        self._profiles[user_id] = f0_samples
        logger.info(
            "voice_profile_registered",
            user_id=user_id,
            samples=len(f0_samples),
        )

    def reset(self) -> None:
        """State zuruecksetzen."""
        self._recent_f0.clear()
        self._recent_jitter.clear()
        self._recent_shimmer.clear()
        self._recent_vectors.clear()
        self._analysis_count = 0
