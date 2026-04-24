"""
Speaker Recognition — Stimmerkennung für SOMA.
================================================
Identifiziert WER spricht, nicht nur WAS gesagt wird.

Architektur (2-stufig):
  Stufe 1 (JETZT): Pitch-basiertes Profiling
    - F0 mean + std als einfacher Voice-Fingerprint
    - Reicht für 2-3 Personen im Haushalt
    - Nutzt bereits vorhandene librosa Features
    
  Stufe 2 (OPTIONAL): Deep Speaker Embedding
    - speechbrain/pyannote für 192d/256d Embeddings
    - Cosine-Similarity für Verifikation
    - Braucht PyTorch (~2GB) — nur wenn User es will

Non-negotiable:
  - NIEMALS Rohdaten persistieren — nur Hashes/Statistiken
  - Privacy-first: Alles lokal, kein Cloud-Vergleich
  - Graceful Degradation: Kein torch? → Pitch-only Profiling
"""

from __future__ import annotations

import time
import json
import logging
import hashlib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("soma.voice.speaker_id")

# ── Konfiguration ────────────────────────────────────────────────────────
PROFILES_PATH = Path("data/speaker_profiles.json")
MIN_ENROLLMENT_SAMPLES = 5       # Mindestens 5 Sprachsegmente für ein Profil
MATCH_THRESHOLD_PITCH = 0.75     # Ähnlichkeits-Schwelle für Pitch-Matching
PROFILE_UPDATE_INTERVAL = 10     # Nur alle N Samples das Profil updaten


@dataclass
class SpeakerProfile:
    """Stimmprofil einer Person."""
    name: str                          # Personenname (z.B. "Patrick", "Sarah")
    pitch_mean: float = 0.0            # Durchschnittliche F0 in Hz
    pitch_std: float = 0.0             # Standardabweichung F0
    energy_mean: float = 0.0           # Durchschnittliche Energie
    spectral_centroid_mean: float = 0.0  # Durchschnittlicher Spectral Centroid
    sample_count: int = 0              # Anzahl gesammelter Samples
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    # Rohwerte für Running-Average
    _pitch_samples: list[float] = field(default_factory=list, repr=False)
    _energy_samples: list[float] = field(default_factory=list, repr=False)

    @property
    def is_enrolled(self) -> bool:
        return self.sample_count >= MIN_ENROLLMENT_SAMPLES

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "pitch_mean": self.pitch_mean,
            "pitch_std": self.pitch_std,
            "energy_mean": self.energy_mean,
            "spectral_centroid_mean": self.spectral_centroid_mean,
            "sample_count": self.sample_count,
            "created_at": self.created_at,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SpeakerProfile":
        return cls(
            name=data["name"],
            pitch_mean=data.get("pitch_mean", 0.0),
            pitch_std=data.get("pitch_std", 0.0),
            energy_mean=data.get("energy_mean", 0.0),
            spectral_centroid_mean=data.get("spectral_centroid_mean", 0.0),
            sample_count=data.get("sample_count", 0),
            created_at=data.get("created_at", time.time()),
            last_seen=data.get("last_seen", time.time()),
        )


class SpeakerRecognition:
    """
    Leichtgewichtige Speaker Recognition auf Basis von Pitch-Features.
    Kein PyTorch nötig — nutzt vorhandene librosa Features.
    """

    def __init__(self):
        self._profiles: dict[str, SpeakerProfile] = {}  # name → profile
        self._current_speaker: Optional[str] = None
        self._loaded = False

    def load_profiles(self) -> None:
        """Lade gespeicherte Profile von Disk."""
        if self._loaded:
            return
        try:
            if PROFILES_PATH.exists():
                data = json.loads(PROFILES_PATH.read_text())
                for entry in data.get("profiles", []):
                    profile = SpeakerProfile.from_dict(entry)
                    self._profiles[profile.name] = profile
                logger.info(
                    "speaker_profiles_loaded",
                    count=len(self._profiles),
                    names=list(self._profiles.keys()),
                )
        except Exception as e:
            logger.warning(f"speaker_profiles_load_error: {e}")
        self._loaded = True

    def save_profiles(self) -> None:
        """Persistiere Profile auf Disk."""
        try:
            PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "profiles": [p.to_dict() for p in self._profiles.values()],
                "saved_at": time.time(),
            }
            PROFILES_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.warning(f"speaker_profiles_save_error: {e}")

    def enroll_sample(
        self,
        speaker_name: str,
        pitch_hz: float,
        energy: float = 0.0,
        spectral_centroid: float = 0.0,
    ) -> SpeakerProfile:
        """
        Füge ein Audio-Sample zum Profil einer Person hinzu.
        Wird während des Gesprächs aufgerufen wenn der Sprecher bekannt ist.
        """
        if speaker_name not in self._profiles:
            self._profiles[speaker_name] = SpeakerProfile(name=speaker_name)

        profile = self._profiles[speaker_name]

        if pitch_hz > 50:  # Sinnvoller F0-Wert
            profile._pitch_samples.append(pitch_hz)
            # Running average — maximal 100 Samples behalten
            if len(profile._pitch_samples) > 100:
                profile._pitch_samples = profile._pitch_samples[-100:]
            profile.pitch_mean = float(np.mean(profile._pitch_samples))
            profile.pitch_std = float(np.std(profile._pitch_samples))

        if energy > 0:
            profile._energy_samples.append(energy)
            if len(profile._energy_samples) > 100:
                profile._energy_samples = profile._energy_samples[-100:]
            profile.energy_mean = float(np.mean(profile._energy_samples))

        if spectral_centroid > 0:
            profile.spectral_centroid_mean = (
                profile.spectral_centroid_mean * 0.9 + spectral_centroid * 0.1
            )

        profile.sample_count += 1
        profile.last_seen = time.time()

        # Periodisch speichern
        if profile.sample_count % PROFILE_UPDATE_INTERVAL == 0:
            self.save_profiles()

        return profile

    def identify_speaker(
        self,
        pitch_hz: float,
        energy: float = 0.0,
    ) -> tuple[Optional[str], float]:
        """
        Identifiziere den Sprecher anhand von Audio-Features.
        
        Returns:
            (speaker_name, confidence) — None wenn nicht erkannt.
        """
        if not self._profiles or pitch_hz < 50:
            return None, 0.0

        best_match: Optional[str] = None
        best_score = 0.0

        for name, profile in self._profiles.items():
            if not profile.is_enrolled:
                continue

            # Pitch-Distanz (normalisiert)
            if profile.pitch_std > 0:
                pitch_dist = abs(pitch_hz - profile.pitch_mean) / (profile.pitch_std + 1e-6)
                pitch_score = max(0.0, 1.0 - pitch_dist / 3.0)
            else:
                pitch_score = 0.0

            # Energy als sekundäres Signal
            energy_score = 0.0
            if energy > 0 and profile.energy_mean > 0:
                energy_ratio = min(energy, profile.energy_mean) / max(energy, profile.energy_mean)
                energy_score = energy_ratio

            # Gewichtetes Score: 80% Pitch + 20% Energy
            score = 0.80 * pitch_score + 0.20 * energy_score

            if score > best_score:
                best_score = score
                best_match = name

        if best_score >= MATCH_THRESHOLD_PITCH:
            self._current_speaker = best_match
            return best_match, best_score

        return None, best_score

    @property
    def current_speaker(self) -> Optional[str]:
        return self._current_speaker

    @current_speaker.setter
    def current_speaker(self, name: Optional[str]):
        self._current_speaker = name

    def get_enrolled_speakers(self) -> list[str]:
        """Alle enrollten Speaker-Namen."""
        return [
            name for name, p in self._profiles.items()
            if p.is_enrolled
        ]

    def get_profile(self, name: str) -> Optional[SpeakerProfile]:
        return self._profiles.get(name)

    @property
    def is_available(self) -> bool:
        """True wenn mindestens ein Sprecher enrolled ist."""
        return any(p.is_enrolled for p in self._profiles.values())


# ── Singleton ────────────────────────────────────────────────────────────
_speaker_recognition: Optional[SpeakerRecognition] = None


def get_speaker_recognition() -> SpeakerRecognition:
    global _speaker_recognition
    if _speaker_recognition is None:
        _speaker_recognition = SpeakerRecognition()
        _speaker_recognition.load_profiles()
    return _speaker_recognition
