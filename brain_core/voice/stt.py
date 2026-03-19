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
        # QUALITÄTS-OPTIMIERUNG:
        #   language="de" FIX: Auto-Detect erkennt kurze deutsche Sätze oft als
        #   Niederländisch/Englisch → Müll-Transkription. Explizit "de" setzen!
        #   beam_size=5: Deutlich bessere Erkennung als greedy (1). Kostet ~200ms
        #   mehr, aber für ein Voice-System ist Qualität > Speed.
        #   condition_on_previous_text=False: Verhindert Whisper-Halluzinations-Loops
        effective_language = self._language or "de"  # SOMA ist primär deutsch
        segments_iter, info = self._model.transcribe(
            audio,
            language=effective_language,
            beam_size=5,              # Qualität! 5 Hypothesen statt greedy
            best_of=3,                # Top-3 Kandidaten vergleichen
            vad_filter=False,         # Externes VAD reicht!
            word_timestamps=False,
            condition_on_previous_text=False,  # Anti-Halluzination
            without_timestamps=True,  # Keine Timestamps nötig = schneller
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

        # ── Whisper-Halluzinations-Filter ─────────────────────────────
        # Whisper halluziniert bei Hintergrundgeräuschen (TV, Radio) typische
        # Broadcast-Textbausteine. Diese sind KEINE echte Sprache.
        full_text = self._filter_hallucinations(full_text)

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
    def _filter_hallucinations(text: str) -> str:
        """
        Filtert typische Whisper-Halluzinationen bei Hintergrundgeräuschen.

        Whisper halluziniert bei TV/Radio-Audio im Hintergrund regelmäßig:
        - Broadcast-Textbausteine ("Copyright WDR", "Untertitel im Auftrag des ZDF")
        - YouTube/Podcast-Phrasen ("Vielen Dank fürs Zuschauen", "Abonniert")
        - Leer-Halluzinationen ("...", "Untertitelung", "SWR")

        Wenn das gesamte Segment ein Halluzinations-Match ist → leerer String.
        Wenn nur Teile matchen → trotzdem leerer String (Segment ist kontaminiert).
        """
        if not text or len(text.strip()) < 3:
            return ""

        t = text.lower().strip()

        # ── Exakte Halluzinations-Phrasen (Case-insensitive Substring) ──
        hallucination_markers = [
            # Deutsche Sender-Tags
            "copyright", "untertitel", "im auftrag des",
            "wdr", "zdf", "ard", "swr", "ndr", "mdr", "rbb",
            "bayerischer rundfunk", "hessischer rundfunk",
            "mitteldeutscher rundfunk", "norddeutscher rundfunk",
            "westdeutscher rundfunk", "südwestrundfunk",
            # YouTube/Podcast-Halluzinationen
            "vielen dank fürs zuschauen", "danke fürs zuschauen",
            "abonniert", "subscribe", "thank you for watching",
            "like and subscribe", "link in der beschreibung",
            # Untertitel-Artefakte
            "untertitelung", "untertitelt von", "übersetzung:",
            "untertitel von", "redaktion:",
            # Musik-/Geräusch-Halluzinationen
            "♪", "♫", "[musik]", "[applaus]", "(musik)",
        ]

        for marker in hallucination_markers:
            if marker in t:
                logger.debug("stt_hallucination_filtered",
                             text=text[:60], marker=marker)
                return ""

        # ── Zu kurze Phrasen die nur Rauschen sind ──
        # Einzelne Wörter wie "Ja.", "Mhm.", "So." sind oft Phantom-Segmente
        words = text.split()
        if len(words) <= 1 and len(text.strip()) <= 4:
            return ""

        return text

    @staticmethod
    def _detect_soma(text: str) -> bool:
        """
        Erkennt "Soma" irgendwo im Text.
        Berücksichtigt typische Whisper-Fehler und deutsche Aussprache.
        ABER: Keine zu breiten Matches die bei TV/Radio triggern!
        """
        t = text.lower()
        # Exakte Matches und typische Whisper-Transkriptionsfehler
        # NICHT enthalten: "sommer", "summer", "summa" (zu viele False Positives!)
        # NICHT enthalten: "So, mal" (triggert bei "So, mal schauen wir...")
        soma_variants = [
            "soma", "sooma", "so ma", "sohma", "somma", "zoma",
            "somar", "soomar", "somah", "sommar",
            "suma", "zooma", "söma", "söhma",
            "hey soma", "hej soma", "hallo soma",
        ]
        return any(variant in t for variant in soma_variants)

    async def shutdown(self):
        """Model entladen."""
        self._model = None
        self._ready = False
        logger.info("stt_shutdown")

    async def transcribe_file(self, filepath: str) -> "TranscriptionResult":
        """
        Transkribiere eine Audio-Datei (WAV, MP3, etc.) direkt via faster-whisper.
        Genutzt vom Phone-Gateway: Asterisk-Aufnahmen landen als WAV-Datei,
        Whisper verarbeitet sie direkt ohne numpy-Konvertierung.

        Args:
            filepath: Absoluter Pfad zur Audio-Datei

        Returns:
            TranscriptionResult (gleiche Struktur wie transcribe())
        """
        await self.initialize()  # Sicherstellen dass Model geladen

        start = time.time()

        segments_iter, info = self._model.transcribe(
            filepath,
            language=self._language,
            beam_size=5,              # Qualität > Speed
            best_of=1,
            vad_filter=True,          # Für Dateien: VAD sinnvoll (längere Aufnahmen)
            vad_parameters=dict(
                min_silence_duration_ms=300,
                speech_pad_ms=100,
            ),
            word_timestamps=False,
            condition_on_previous_text=False,
        )

        text_parts: list[str] = []
        segments: list[dict] = []
        for seg in segments_iter:
            text_parts.append(seg.text.strip())
            segments.append({"start": seg.start, "end": seg.end, "text": seg.text})

        full_text = " ".join(text_parts).strip()
        processing_ms = (time.time() - start) * 1000

        result = TranscriptionResult(
            text=full_text,
            language=info.language or "de",
            confidence=round(info.language_probability or 0.0, 2),
            duration_sec=round(info.duration or 0.0, 2),
            processing_ms=round(processing_ms, 1),
            segments=segments,
            contains_soma=self._detect_soma(full_text),
        )

        if full_text:
            logger.info(
                "stt_file_transcribed",
                file=filepath[-30:],
                text=full_text[:80],
                ms=result.processing_ms,
            )

        return result
