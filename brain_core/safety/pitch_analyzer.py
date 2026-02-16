"""
SOMA-AI Pitch Analyzer
========================
Erkennt Alter und Geschlecht anhand der Stimm-Frequenz.
Triggert den Child-Safe Mode wenn ein Kind erkannt wird.

Datenfluss:
  AudioChunkMeta ──► PitchAnalyzer.analyze()
                          │
                          ├─ F0 (Grundfrequenz) extrahieren
                          ├─ Gegen bekannte Profile matchen
                          └─ is_child? ──► prompt_injector
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import structlog

logger = structlog.get_logger("soma.safety.pitch")


@dataclass
class PitchResult:
    """Ergebnis der Stimmanalyse."""
    fundamental_freq_hz: float
    is_child: bool
    estimated_age_group: str  # "child" | "teen" | "adult"
    confidence: float
    stress_level: float  # 0.0 (ruhig) – 1.0 (gestresst)


# ── Frequenzbereiche für Altersgruppen ───────────────────────────────────
# Kinder (< 12):       250 – 400 Hz
# Jugendliche (12-18): 150 – 300 Hz
# Erwachsene (> 18):   85  – 255 Hz (männlich 85-180, weiblich 165-255)

CHILD_F0_MIN = 250.0
CHILD_F0_MAX = 450.0
TEEN_F0_MIN = 150.0
TEEN_F0_MAX = 300.0
ADULT_F0_MAX = 255.0


class PitchAnalyzer:
    """
    Stimmfrequenz-Analyse für Alterserkennung.
    Nutzt einfache Autokorrelation – kein ML-Modell nötig.
    """

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self._profiles: dict[str, list[float]] = {}

    def analyze(
        self,
        audio_data: np.ndarray,
        sample_rate: Optional[int] = None,
    ) -> PitchResult:
        """
        Analysiere Audio-Daten und bestimme Altersgruppe.

        Args:
            audio_data: PCM Audio als numpy array (mono, float32)
            sample_rate: Sample Rate (default: 16000)
        """
        sr = sample_rate or self.sample_rate

        # Grundfrequenz via Autokorrelation
        f0 = self._estimate_f0(audio_data, sr)

        # Stress-Level via Jitter/Shimmer Approximation
        stress = self._estimate_stress(audio_data, sr)

        # Altersgruppe bestimmen
        if f0 >= CHILD_F0_MIN:
            age_group = "child"
            is_child = True
            confidence = min(1.0, (f0 - CHILD_F0_MIN) / (CHILD_F0_MAX - CHILD_F0_MIN))
        elif f0 >= TEEN_F0_MIN and f0 < CHILD_F0_MIN:
            age_group = "teen"
            is_child = False
            confidence = 0.7
        else:
            age_group = "adult"
            is_child = False
            confidence = 0.9

        result = PitchResult(
            fundamental_freq_hz=round(f0, 2),
            is_child=is_child,
            estimated_age_group=age_group,
            confidence=round(confidence, 2),
            stress_level=round(stress, 2),
        )

        if is_child:
            logger.info(
                "child_detected",
                f0=result.fundamental_freq_hz,
                confidence=result.confidence,
            )

        return result

    @staticmethod
    def _estimate_f0(audio: np.ndarray, sr: int) -> float:
        """
        Grundfrequenz via Autokorrelation.
        Leichtgewichtig, kein ML.
        """
        if len(audio) < sr // 10:  # Mindestens 100ms Audio
            return 0.0

        # Normalisieren
        audio = audio.astype(np.float64)
        audio = audio - np.mean(audio)

        # Autokorrelation
        corr = np.correlate(audio, audio, mode="full")
        corr = corr[len(corr) // 2:]

        # Suche erstes Minimum nach dem Peak, dann erstes Maximum danach
        # Das gibt uns die Periode
        min_lag = sr // 500  # Max 500 Hz
        max_lag = sr // 50   # Min 50 Hz

        if max_lag > len(corr):
            max_lag = len(corr) - 1

        corr_segment = corr[min_lag:max_lag]
        if len(corr_segment) == 0:
            return 0.0

        peak_idx = np.argmax(corr_segment) + min_lag

        if peak_idx > 0:
            f0 = sr / peak_idx
            return f0

        return 0.0

    @staticmethod
    def _estimate_stress(audio: np.ndarray, sr: int) -> float:
        """
        Grobe Stress-Schätzung basierend auf:
        - Höhere Energie = höherer Stress
        - Mehr Varianz in der Amplitude = höherer Stress
        """
        if len(audio) == 0:
            return 0.0

        rms = np.sqrt(np.mean(audio ** 2))
        std = np.std(audio)

        # Normalisiere auf 0-1 Bereich (empirische Schwellwerte)
        energy_stress = min(1.0, rms / 0.3)
        variability_stress = min(1.0, std / 0.2)

        return (energy_stress * 0.6 + variability_stress * 0.4)

    def register_voice_profile(
        self, user_id: str, f0_samples: list[float]
    ) -> None:
        """Stimmprofil für späteren Vergleich registrieren."""
        self._profiles[user_id] = f0_samples
        logger.info("voice_profile_registered", user_id=user_id, samples=len(f0_samples))
