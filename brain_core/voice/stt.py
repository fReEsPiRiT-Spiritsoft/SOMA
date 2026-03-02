"""
SOMA-AI Speech-to-Text Engine (faster-whisper)
================================================
Wandelt Sprach-Segmente in Text um.
Nutzt faster-whisper (CTranslate2) — GPU-beschleunigt, kein PyTorch nötig.

Datenfluss:
  SpeechSegment.audio → STTEngine.transcribe()
                           │
                           ├── Whisper Model (small/medium)
                           ├── Sprache erkannt → TranscriptionResult
                           └── Sprache + detected_language

Features:
  - Automatische Spracherkennung (Deutsch/Englisch)
  - GPU-Beschleunigung (CUDA) wenn verfügbar
  - Batch-fähig für Deferred Processing
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
import structlog

logger = structlog.get_logger("soma.voice.stt")


@dataclass
class TranscriptionResult:
    """Ergebnis einer Transkription."""
    text: str
    language: str
    confidence: float
    duration_sec: float
    processing_ms: float
    segments: list[dict]    # Whisper-Segmente mit Timestamps
    contains_soma: bool     # "Soma" im Text erkannt?


class STTEngine:
    """
    Speech-to-Text via faster-whisper.
    Lädt das Model lazy beim ersten Aufruf.
    """

    def __init__(
        self,
        model_size: str = "small",
        device: str = "auto",
        compute_type: str = "auto",
        language: Optional[str] = None,
    ):
        """
        Args:
            model_size: "tiny", "base", "small", "medium", "large-v3"
                       "small" = guter Kompromiss (~461MB VRAM, schnell)
            device: "cuda", "cpu", "auto"
            compute_type: "float16", "int8", "auto"
            language: None = Auto-Detect, "de" = nur Deutsch
        """
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._language = language
        self._model = None
        self._ready = False

    async def initialize(self):
        """Model laden (lazy, beim ersten Start)."""
        if self._ready:
            return

        from faster_whisper import WhisperModel

        # Device bestimmen
        device = self._device
        compute = self._compute_type

        if device == "auto":
            try:
                import ctranslate2
                if "cuda" in ctranslate2.get_supported_compute_types("cuda"):
                    device = "cuda"
                    compute = "float16" if compute == "auto" else compute
                else:
                    device = "cpu"
                    compute = "int8" if compute == "auto" else compute
            except Exception:
                device = "cpu"
                compute = "int8" if compute == "auto" else compute

        logger.info(
            "stt_loading_model",
            model=self._model_size,
            device=device,
            compute_type=compute,
        )

        start = time.monotonic()
        self._model = WhisperModel(
            self._model_size,
            device=device,
            compute_type=compute,
            download_root=".models/whisper",
        )
        elapsed = (time.monotonic() - start) * 1000

        logger.info("stt_model_loaded", elapsed_ms=round(elapsed, 0))
        self._ready = True

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
    ) -> TranscriptionResult:
        """
        Transkribiere ein Audio-Segment.

        Args:
            audio: float32 numpy array [-1, 1]
            sample_rate: Sample-Rate (muss 16kHz sein für Whisper)
        """
        if not self._ready or not self._model:
            raise RuntimeError("STT Engine nicht initialisiert — await initialize() zuerst")

        start = time.monotonic()
        duration = len(audio) / sample_rate

        # Whisper erwartet float32 16kHz
        segments_iter, info = self._model.transcribe(
            audio,
            language=self._language,
            beam_size=3,
            best_of=3,
            vad_filter=True,          # Internes VAD für bessere Segmentierung
            vad_parameters=dict(
                min_silence_duration_ms=500,
                speech_pad_ms=200,
            ),
            word_timestamps=False,
            condition_on_previous_text=True,
        )

        # Segmente sammeln
        segments = []
        text_parts = []
        for seg in segments_iter:
            segments.append({
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": seg.text.strip(),
            })
            text_parts.append(seg.text.strip())

        full_text = " ".join(text_parts).strip()
        processing_ms = (time.monotonic() - start) * 1000

        # "Soma" erkennen (case-insensitive, überall im Text)
        contains_soma = self._detect_soma(full_text)

        result = TranscriptionResult(
            text=full_text,
            language=info.language or "de",
            confidence=round(info.language_probability or 0.0, 2),
            duration_sec=round(duration, 2),
            processing_ms=round(processing_ms, 1),
            segments=segments,
            contains_soma=contains_soma,
        )

        if full_text:
            logger.info(
                "stt_transcribed",
                text=full_text[:80],
                lang=result.language,
                soma=contains_soma,
                ms=result.processing_ms,
            )

        return result

    @staticmethod
    def _detect_soma(text: str) -> bool:
        """
        Erkennt "Soma" irgendwo im Text.
        Berücksichtigt typische Whisper-Fehler und deutsche Aussprache.
        """
        t = text.lower()
        # Exakte Matches und typische Whisper-Transkriptionsfehler
        soma_variants = [
            "soma", "sooma", "so ma", "sohma", "somma", "zoma",
            "sommer", "zommer", "summer", "summa",  # Sehr häufige Whisper-Fehler!
            "somar", "soomar", "somah", "sommar",
            "suma", "zooma", "söma", "söhma",
            "hey soma", "hey sommer", "hej soma",
            "soma!", "sommer!", "hey,", "hallo soma",
        ]
        return any(variant in t for variant in soma_variants)

    async def shutdown(self):
        """Model entladen."""
        self._model = None
        self._ready = False
        logger.info("stt_shutdown")
