"""
SOMA-AI Voice Micro-Expressions — SOMA hat Tells
===================================================
Echte Menschen verraten sich durch Mikro-Ausdrücke. SOMA auch.

Kurzes Zögern vor unsicheren Aussagen.
Leicht schnelleres Sprechen bei Begeisterung.
Minimale Pause bei Erinnerungsabruf vs. Generierung.
Nie explizit — nur als Rhythmus.

Das macht den Unterschied zwischen "Stimme" und "Persönlichkeit".

Interne Zustände → Prosody-Mapping:
  ┌─────────────────┬───────────────────────────────────────────────┐
  │ Unsicherheit     │ rate=0.88  pitch=-2st   pause=150ms          │
  │ Erinnerungsabruf │ rate=0.94  pause=250ms  (erstes Satz)        │
  │ Begeisterung     │ rate=1.07  pitch=+1.5st volume=+1dB          │
  │ Vorsicht         │ rate=0.92  pitch=-1.5st volume=-0.5dB        │
  │ Systemlast       │ rate=0.95  pitch=-1st   (konzentriert)       │
  │ Neutral          │ rate=1.0   (kein Eingriff)                   │
  └─────────────────┴───────────────────────────────────────────────┘

Konfidenz → Pause-Dauer (vor dem Satz):
  0.9+  → 0ms  |  0.7–0.9 → 80ms  |  0.5–0.7 → 160ms  |  <0.5 → 280ms

Retrieval vs. Generierung:
  Memory-Abruf   → 250ms Pause + rate=0.94 (erstes Satz, "erinnern")
  Live-Generierung → kein Zögern, fließend
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
import structlog

logger = structlog.get_logger("soma.voice.micro")


# ══════════════════════════════════════════════════════════════════════════
#  STATE & DATA MODELS
# ══════════════════════════════════════════════════════════════════════════


class MicroState(str, Enum):
    """Erkannter Mikro-Ausdruck-Zustand."""
    NEUTRAL = "neutral"
    UNCERTAINTY = "uncertainty"
    MILD_UNCERTAINTY = "mild_uncertainty"
    RETRIEVAL = "retrieval"
    ENTHUSIASM = "enthusiasm"
    CAUTION = "caution"
    SYSTEM_STRESS = "system_stress"


@dataclass
class MicroExpression:
    """
    Prosodie-Modifikation für einen einzelnen Satz.

    rate_factor:      Multiplikator auf die Sprechgeschwindigkeit (1.0 = keine Änderung)
    pitch_semitones:  Pitch-Verschiebung in Halbtönen (0.0 = keine Änderung)
    volume_db:        Lautstärke-Änderung in dB (0.0 = keine Änderung)
    pre_pause_ms:     Stille VOR dem Satz in Millisekunden
    post_pause_ms:    Stille NACH dem Satz in Millisekunden
    state:            Erkannter Zustand (für Logging/Debug)
    """
    state: MicroState = MicroState.NEUTRAL
    rate_factor: float = 1.0
    pitch_semitones: float = 0.0
    volume_db: float = 0.0
    pre_pause_ms: int = 0
    post_pause_ms: int = 0

    @property
    def is_neutral(self) -> bool:
        """True wenn keine Modifikation nötig — Fast-Path für TTS."""
        return (
            abs(self.rate_factor - 1.0) < 0.01
            and abs(self.pitch_semitones) < 0.1
            and abs(self.volume_db) < 0.05
            and self.pre_pause_ms == 0
            and self.post_pause_ms == 0
        )

    @classmethod
    def neutral(cls) -> MicroExpression:
        return cls()


@dataclass
class MicroExpressionContext:
    """
    Pipeline-Kontext-Signale für die Mikro-Ausdruck-Erkennung.

    has_memory_retrieval:    Wurde Memory für diese Antwort abgerufen?
    is_first_sentence:       Erster Satz der Antwort? (Retrieval-Pause nur hier)
    system_load:             Aktuelle Systemlast ("idle", "normal", "elevated", "high", "critical")
    consciousness_arousal:   Arousal-Wert aus dem Bewusstsein (0.0–1.0)
    """
    has_memory_retrieval: bool = False
    is_first_sentence: bool = False
    system_load: str = "idle"
    consciousness_arousal: float = 0.0


# ══════════════════════════════════════════════════════════════════════════
#  VORDEFINIERTE EXPRESSIONS (aus der Spezifikation)
# ══════════════════════════════════════════════════════════════════════════

# Starke Unsicherheit: 2+ Marker oder starke Marker
# "Ich bin mir nicht sicher, vielleicht könnte..."
_STRONG_UNCERTAINTY = MicroExpression(
    state=MicroState.UNCERTAINTY,
    rate_factor=0.88,
    pitch_semitones=-2.0,
    pre_pause_ms=280,         # Konfidenz < 0.5
)

# Milde Unsicherheit: 1 schwacher Marker
# "Wahrscheinlich liegt es daran..."
_MILD_UNCERTAINTY = MicroExpression(
    state=MicroState.MILD_UNCERTAINTY,
    rate_factor=0.93,
    pitch_semitones=-1.0,
    pre_pause_ms=80,          # Konfidenz 0.7–0.9
)

# Erinnerungsabruf: Memory wurde genutzt, erster Satz
# Kurze Denkpause — "warte, ich erinnere mich..."
_RETRIEVAL = MicroExpression(
    state=MicroState.RETRIEVAL,
    rate_factor=0.94,
    pre_pause_ms=250,
)

# Begeisterung: Positive, enthusiastische Aussage
# Leicht schneller, Pitch-Range weiter, minimal lauter
_ENTHUSIASM = MicroExpression(
    state=MicroState.ENTHUSIASM,
    rate_factor=1.07,
    pitch_semitones=1.5,
    volume_db=1.0,
)

# Vorsicht/Warnung: Ernste Information
# Verlangsamt, tiefer — wirkt ernst ohne dramatisch
_CAUTION = MicroExpression(
    state=MicroState.CAUTION,
    rate_factor=0.92,
    pitch_semitones=-1.5,
    volume_db=-0.5,
)

# Systemlast/Stress: SOMA ist konzentriert
# Marginale Verlangsamung, minimal monotoner
_SYSTEM_STRESS = MicroExpression(
    state=MicroState.SYSTEM_STRESS,
    rate_factor=0.95,
    pitch_semitones=-1.0,
)


# ══════════════════════════════════════════════════════════════════════════
#  DEUTSCHE MARKER-WÖRTERBÜCHER
# ══════════════════════════════════════════════════════════════════════════

# Starke Unsicherheitsmarker → sofort STRONG_UNCERTAINTY
_STRONG_UNCERTAINTY_MARKERS: list[str] = [
    "ich bin mir nicht sicher",
    "ich bin mir unsicher",
    "schwer zu sagen",
    "nicht ausgeschlossen",
    "unter umständen",
    "ich weiß es nicht genau",
    "keine garantie",
    "ich kann nicht versprechen",
    "schwierig zu beurteilen",
]

# Schwache Unsicherheitsmarker → 1 = MILD, 2+ = STRONG
_UNCERTAINTY_MARKERS: list[str] = [
    "vielleicht",
    "möglicherweise",
    "eventuell",
    "könnte",
    "vermutlich",
    "wahrscheinlich",
    "ich glaube",
    "ich denke",
    "soweit ich weiß",
    "wenn ich mich nicht irre",
    "möglich",
    "denkbar",
    "scheint",
    "anscheinend",
    "tendenziell",
    "im prinzip",
    "quasi",
    "gewissermaßen",
]

# Begeisterungsmarker
_ENTHUSIASM_MARKERS: list[str] = [
    "super",
    "toll",
    "großartig",
    "fantastisch",
    "genial",
    "mega",
    "krass",
    "hammer",
    "klasse",
    "perfekt",
    "wunderbar",
    "cool",
    "nice",
    "prima",
    "spitze",
    "excellent",
    "freue mich",
    "aufregend",
    "begeistert",
    "faszinierend",
    "grandios",
    "hervorragend",
    "ausgezeichnet",
    "brillant",
]

# Vorsicht/Warnungs-Marker
_CAUTION_MARKERS: list[str] = [
    "vorsicht",
    "achtung",
    "warnung",
    "wichtig",
    "aufpassen",
    "gefährlich",
    "risiko",
    "bitte beachte",
    "nicht empfehlenswert",
    "dringend",
    "kritisch",
    "auf keinen fall",
    "unbedingt vermeiden",
    "pass auf",
    "sei vorsichtig",
    "nicht vergessen",
]


# ══════════════════════════════════════════════════════════════════════════
#  MAPPER: Text + Kontext → MicroExpression
# ══════════════════════════════════════════════════════════════════════════


class MicroExpressionMapper:
    """
    Analysiert einen Satz + Pipeline-Kontext und bestimmt die
    passende Mikro-Expression für die Prosodie.

    Priorität:
      1. Memory-Retrieval (erster Satz)  → Denk-Pause
      2. System-Stress (high/critical)   → konzentriert
      3. Starke Unsicherheit             → deutliches Zögern
      4. Vorsicht/Warnung                → ernst, langsam
      5. Milde Unsicherheit              → leichtes Zögern
      6. Begeisterung                    → lebhafter
      7. Neutral                         → kein Eingriff

    Wichtig: Neutral ist der häufigste Fall.
    Nicht jeder Satz braucht ein Signal!
    """

    def detect(
        self,
        sentence: str,
        ctx: MicroExpressionContext,
    ) -> MicroExpression:
        """
        Erkenne den Mikro-Ausdruck für einen Satz.

        Args:
            sentence:  Der zu sprechende Satz
            ctx:       Pipeline-Kontext (Memory, System, Bewusstsein)

        Returns:
            MicroExpression mit Prosodie-Modifikatoren (oder neutral)
        """
        lower = sentence.lower()

        # ── 1. Memory Retrieval: Erster Satz nach Erinnerungsabruf ───
        # "Warte, ich erinnere mich..." → kurze Denkpause am Anfang
        if ctx.has_memory_retrieval and ctx.is_first_sentence:
            logger.debug(
                "micro_retrieval",
                sentence=sentence[:40],
                pause_ms=_RETRIEVAL.pre_pause_ms,
            )
            return _RETRIEVAL

        # ── 2. System-Stress: SOMA ist konzentriert ──────────────────
        if ctx.system_load in ("critical", "high"):
            logger.debug("micro_system_stress", load=ctx.system_load)
            return _SYSTEM_STRESS

        # ── 3. Starke Unsicherheit: sofortige Erkennung ─────────────
        has_strong = any(m in lower for m in _STRONG_UNCERTAINTY_MARKERS)
        if has_strong:
            logger.debug(
                "micro_strong_uncertainty",
                sentence=sentence[:40],
            )
            return _STRONG_UNCERTAINTY

        # ── Text-Marker zählen ───────────────────────────────────────
        uncertainty_count = self._count_markers(lower, _UNCERTAINTY_MARKERS)
        enthusiasm_count = self._count_markers(lower, _ENTHUSIASM_MARKERS)
        caution_count = self._count_markers(lower, _CAUTION_MARKERS)

        # ── 4. Vorsicht/Warnung ──────────────────────────────────────
        if caution_count >= 1:
            logger.debug(
                "micro_caution",
                sentence=sentence[:40],
                markers=caution_count,
            )
            return _CAUTION

        # ── 5. Schwache Unsicherheit (2+ Marker = stark) ────────────
        if uncertainty_count >= 2:
            logger.debug(
                "micro_uncertainty",
                sentence=sentence[:40],
                markers=uncertainty_count,
            )
            return _STRONG_UNCERTAINTY

        # ── 6. Milde Unsicherheit (1 Marker) ────────────────────────
        if uncertainty_count == 1:
            logger.debug(
                "micro_mild_uncertainty",
                sentence=sentence[:40],
            )
            return _MILD_UNCERTAINTY

        # ── 7. Begeisterung (2+ Marker für Subtilität) ──────────────
        # Nur bei mehreren Markern → vermeidet False Positives
        if enthusiasm_count >= 2:
            logger.debug(
                "micro_enthusiasm",
                sentence=sentence[:40],
                markers=enthusiasm_count,
            )
            return _ENTHUSIASM

        # ── 8. Neutral: Kein Signal ─────────────────────────────────
        return MicroExpression.neutral()

    @staticmethod
    def _count_markers(text: str, markers: list[str]) -> int:
        """Zähle wie viele Marker im Text vorkommen."""
        return sum(1 for m in markers if m in text)


# ══════════════════════════════════════════════════════════════════════════
#  AUDIO POST-PROCESSING
# ══════════════════════════════════════════════════════════════════════════


def _pitch_shift(audio: np.ndarray, semitones: float) -> np.ndarray:
    """
    Pitch-Verschiebung um N Halbtöne via Resampling.
    Preserves Duration — nur die Tonhöhe ändert sich.

    Funktioniert über doppelte Interpolation:
      1. Audio auf gestreckte/gestauchte Länge resamplen
      2. Zurück auf Originallänge resamplen
    → Tonhöhe verändert, Dauer bleibt gleich.

    Bei ±2 Halbtönen ist die Qualität exzellent (reine numpy-Interpolation).

    Args:
        audio:     int16 Audio-Array
        semitones: Halbtöne (+1.5 = höher, -2.0 = tiefer)

    Returns:
        Pitch-verschobenes int16 Audio (gleiche Länge)
    """
    if abs(semitones) < 0.1:
        return audio

    n = len(audio)
    if n == 0:
        return audio

    factor = 2.0 ** (semitones / 12.0)
    stretched_len = max(1, int(n / factor))

    # Phase 1: Auf gestreckte Länge interpolieren
    x_orig = np.arange(n)
    x_new = np.linspace(0, n - 1, stretched_len)
    stretched = np.interp(x_new, x_orig, audio.astype(np.float64))

    # Phase 2: Zurück auf Originallänge
    x_back = np.linspace(0, stretched_len - 1, n)
    result = np.interp(x_back, np.arange(stretched_len), stretched)

    return np.clip(result, -32768, 32767).astype(np.int16)


def apply_micro_to_audio(
    audio: np.ndarray,
    micro: MicroExpression,
    sample_rate: int,
) -> np.ndarray:
    """
    Wende alle Mikro-Ausdruck-Effekte auf int16 Audio an.

    HINWEIS: Rate/Speed wird NICHT hier angewandt — das macht Piper
    nativ über SynthesisConfig.length_scale. Hier nur:
      1. Pitch-Shift (numpy Interpolation)
      2. Volume-Anpassung (dB Skalierung)
      3. Stille-Pausen (Pre/Post Silence)

    Args:
        audio:       int16 Audio-Array (Piper Output)
        micro:       MicroExpression mit den Modifikatoren
        sample_rate: Sample-Rate des Audio (z.B. 22050)

    Returns:
        Modifiziertes int16 Audio
    """
    if micro.is_neutral:
        return audio

    # ── 1. Pitch-Shift ──────────────────────────────────────────────
    if abs(micro.pitch_semitones) >= 0.1:
        audio = _pitch_shift(audio, micro.pitch_semitones)

    # ── 2. Volume (dB → linearer Faktor) ────────────────────────────
    if abs(micro.volume_db) > 0.05:
        factor = 10.0 ** (micro.volume_db / 20.0)
        audio = np.clip(
            audio.astype(np.float64) * factor, -32768, 32767
        ).astype(np.int16)

    # ── 3. Stille-Pausen (Pre/Post) ─────────────────────────────────
    parts: list[np.ndarray] = []

    if micro.pre_pause_ms > 0:
        n_silence = int(sample_rate * micro.pre_pause_ms / 1000)
        parts.append(np.zeros(n_silence, dtype=np.int16))

    parts.append(audio)

    if micro.post_pause_ms > 0:
        n_silence = int(sample_rate * micro.post_pause_ms / 1000)
        parts.append(np.zeros(n_silence, dtype=np.int16))

    if len(parts) > 1:
        audio = np.concatenate(parts)

    return audio
