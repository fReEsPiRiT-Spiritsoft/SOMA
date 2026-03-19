"""
SOMA-AI Streaming Speech-to-Text
==================================
Echtzeit-Partial-Transkription WÄHREND der User spricht.

Datenfluss:
  VAD speech_start → StreamingSTT.start_stream()
  VAD frame (speech) → StreamingSTT.feed(pcm_float32)
                         │
                         ├── Genug Audio? → Partial Whisper → PartialResult
                         │     └── Early "Soma" detection!
                         │     └── Dashboard: Live-Text
                         │
  VAD segment_done  → StreamingSTT.finalize()
                         └── Letzte Transkription (Whisper schon warm)
                              → TranscriptionResult (wie vorher)

Algorithmus: LocalAgreement
  Partial 1: "Soma mach das"
  Partial 2: "Soma mach das Licht"   → "Soma mach das" bestätigt
  Partial 3: "Soma mach das Licht an" → "Soma mach das Licht" bestätigt
  Final:     Volle Transkription auf gesamtem Audio

Vorteile:
  ✅ ~200-400ms schnellere Antwort (Whisper schon warm)
  ✅ Early Wake-Word: "Soma" erkannt BEVOR User fertig spricht
  ✅ Dashboard zeigt Live-Text während User spricht
  ✅ Keine neuen Dependencies (nutzt vorhandenes faster-whisper)
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable

import numpy as np
import structlog

from brain_core.voice.stt import STTEngine, TranscriptionResult

logger = structlog.get_logger("soma.voice.streaming_stt")

# ── Config ───────────────────────────────────────────────────────────────

# PERFORMANCE-OPTIMIERT: Partials sind teuer (GPU mit Ollama geteilt!)
# → Wenige, gezielte Partials statt permanentes Whisper-Polling
PARTIAL_INTERVAL_SEC = 3.0      # Alle ~3s ein Partial (vorher 1s = CPU-Killer)
MIN_AUDIO_FOR_PARTIAL = 1.5     # Erst ab 1.5s Audio (kurze Befehle: kein Partial nötig)
MAX_PARTIALS = 2                # Max 2 Partials pro Utterance (dann reicht finalize)
PARTIAL_WINDOW_SEC = 4.0        # Nur die letzten 4s transkribieren (nicht das gesamte Audio!)

# Thread-Pool: Whisper blockiert → eigener Thread damit Event-Loop frei bleibt
_whisper_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper_stream")


@dataclass
class PartialResult:
    """Partial-Transkription während der User noch spricht."""
    text: str               # Aktuelle Whisper-Ausgabe (kann sich noch ändern)
    confirmed: str          # Durch LocalAgreement bestätigte Wörter (stabil)
    contains_soma: bool     # "Soma" in irgendeinem Partial erkannt?
    latency_ms: float       # Zeit seit Sprechbeginn
    audio_sec: float        # Bisherige Audio-Länge


class StreamingSTT:
    """
    Streaming-Wrapper um faster-whisper.
    Akkumuliert Audio während VAD Sprache erkennt,
    transkribiert periodisch für Partial-Results.
    """

    def __init__(self, stt: STTEngine):
        self._stt = stt

        # ── Stream State ────────────────────────────────────────────
        self._audio_chunks: list[np.ndarray] = []
        self._total_samples: int = 0
        self._stream_start: float = 0.0
        self._last_partial_at: float = 0.0   # Monotonic time of last partial
        self._is_active: bool = False
        self._partial_running: bool = False

        # ── LocalAgreement State ────────────────────────────────────
        self._prev_text: str = ""
        self._confirmed_text: str = ""
        self._confirmed_word_count: int = 0

        # ── Results ─────────────────────────────────────────────────
        self._soma_detected: bool = False
        self._last_partial: Optional[PartialResult] = None
        self._partial_count: int = 0

    # ══════════════════════════════════════════════════════════════════
    #  STREAM LIFECYCLE
    # ══════════════════════════════════════════════════════════════════

    def start_stream(self):
        """Neuen Stream beginnen (wenn VAD Sprache erkennt)."""
        self._audio_chunks = []
        self._total_samples = 0
        self._stream_start = time.monotonic()
        self._last_partial_at = 0.0
        self._is_active = True
        self._partial_running = False

        # LocalAgreement reset
        self._prev_text = ""
        self._confirmed_text = ""
        self._confirmed_word_count = 0

        # Results reset
        self._soma_detected = False
        self._last_partial = None
        self._partial_count = 0

        logger.debug("stream_started")

    def feed(self, pcm_float32: np.ndarray) -> bool:
        """
        Audio-Frame füttern (float32, 16kHz, 30ms).
        Returns True wenn jetzt ein Partial-Run fällig ist.
        """
        if not self._is_active:
            return False

        self._audio_chunks.append(pcm_float32)
        self._total_samples += len(pcm_float32)

        audio_sec = self._total_samples / 16000
        now = time.monotonic()
        since_last = now - (self._stream_start + self._last_partial_at)

        return (
            audio_sec >= MIN_AUDIO_FOR_PARTIAL
            and since_last >= PARTIAL_INTERVAL_SEC
            and not self._partial_running
            and self._partial_count < MAX_PARTIALS  # CPU-Schutz!
        )

    async def run_partial(self) -> Optional[PartialResult]:
        """
        Partial-Transkription ausführen.
        Läuft in Thread-Pool → blockiert Event-Loop NICHT.
        """
        if not self._is_active or self._partial_running or not self._audio_chunks:
            return None

        self._partial_running = True
        self._partial_count += 1
        self._last_partial_at = time.monotonic() - self._stream_start

        try:
            full_audio = np.concatenate(self._audio_chunks)

            # PERFORMANCE: Nur die letzten N Sekunden transkribieren!
            # Bei 5s Utterance: Partial auf 4s statt 5s = deutlich schneller
            # Finalize macht dann die volle Qualität auf dem gesamten Audio
            max_samples = int(PARTIAL_WINDOW_SEC * 16000)
            if len(full_audio) > max_samples:
                audio = full_audio[-max_samples:]
            else:
                audio = full_audio

            loop = asyncio.get_running_loop()

            # Whisper in Worker-Thread ausführen (blockiert sonst Event-Loop)
            result: TranscriptionResult = await loop.run_in_executor(
                _whisper_pool,
                self._stt.transcribe,
                audio,
                16000,
            )

            if not result.text.strip():
                return None

            current_text = result.text.strip()

            # LocalAgreement: Bestätigte Wörter aktualisieren
            self._update_agreement(current_text)

            # Early Wake-Word Detection
            if not self._soma_detected:
                self._soma_detected = STTEngine._detect_soma(current_text)
                if self._soma_detected:
                    logger.info(
                        "streaming_early_soma",
                        text=current_text[:60],
                        latency_ms=round(self._last_partial_at * 1000),
                    )

            partial = PartialResult(
                text=current_text,
                confirmed=self._confirmed_text,
                contains_soma=self._soma_detected,
                latency_ms=round(self._last_partial_at * 1000, 1),
                audio_sec=round(self._total_samples / 16000, 2),
            )
            self._last_partial = partial

            logger.debug(
                "streaming_partial",
                text=current_text[:60],
                confirmed=self._confirmed_text[:40],
                soma=self._soma_detected,
            )

            return partial

        except Exception as e:
            logger.debug("partial_error", error=str(e))
            return None
        finally:
            self._partial_running = False

    async def finalize(self) -> Optional[TranscriptionResult]:
        """
        Finale Transkription wenn VAD das Segment beendet.
        Nutzt das GESAMTE akkumulierte Audio für beste Qualität.
        Läuft in Thread-Pool → non-blocking.

        Falls ein Partial gerade läuft, warten wir kurz darauf.
        """
        if not self._is_active:
            return None

        self._is_active = False

        if not self._audio_chunks:
            return None

        # Falls Partial gerade läuft → kurz warten (max 500ms)
        wait_start = time.monotonic()
        while self._partial_running and (time.monotonic() - wait_start) < 0.5:
            await asyncio.sleep(0.02)

        audio = np.concatenate(self._audio_chunks)
        audio_sec = len(audio) / 16000

        # Zu kurz? VAD sollte das eigentlich filtern, aber sicher ist sicher
        if audio_sec < 0.3:
            self._audio_chunks = []
            return None

        try:
            loop = asyncio.get_running_loop()
            result: TranscriptionResult = await loop.run_in_executor(
                _whisper_pool,
                self._stt.transcribe,
                audio,
                16000,
            )

            # Soma-Detection aus Partials übernehmen (falls Final es verpasst)
            if self._soma_detected and not result.contains_soma:
                result.contains_soma = True

            logger.info(
                "streaming_finalized",
                text=result.text[:80] if result.text else "",
                partial_soma=self._soma_detected,
                final_soma=result.contains_soma,
                audio_sec=round(audio_sec, 2),
                ms=result.processing_ms,
            )

            return result

        except Exception as e:
            logger.error("streaming_finalize_error", error=str(e))
            return None
        finally:
            self._audio_chunks = []

    def cancel(self):
        """Stream abbrechen (z.B. TTS Self-Mute)."""
        self._is_active = False
        self._audio_chunks = []
        self._partial_running = False

    # ══════════════════════════════════════════════════════════════════
    #  LOCAL AGREEMENT
    # ══════════════════════════════════════════════════════════════════

    def _update_agreement(self, current_text: str):
        """
        LocalAgreement-Algorithmus:
        Vergleicht aktuelle mit vorheriger Transkription.
        Wörter die in beiden als Prefix identisch sind → bestätigt.

        Beispiel:
          Prev: "Soma mach das Licht"
          Curr: "Soma mach das Licht an"
          → "Soma mach das Licht" wird bestätigt (4 Wörter Prefix-Match)
        """
        if not self._prev_text:
            self._prev_text = current_text
            return

        prev_words = self._prev_text.split()
        curr_words = current_text.split()

        # Längster gemeinsamer Wort-Prefix
        agreed = 0
        for pw, cw in zip(prev_words, curr_words):
            # Case-insensitive Vergleich (Whisper capitalisiert inkonsistent)
            if pw.lower().rstrip(".,!?") == cw.lower().rstrip(".,!?"):
                agreed += 1
            else:
                break

        # Bestätigter Text wächst nur (wird nie kleiner)
        if agreed > self._confirmed_word_count:
            self._confirmed_word_count = agreed
            self._confirmed_text = " ".join(curr_words[:agreed])

        self._prev_text = current_text

    # ══════════════════════════════════════════════════════════════════
    #  PROPERTIES
    # ══════════════════════════════════════════════════════════════════

    @property
    def is_active(self) -> bool:
        """Läuft gerade ein Stream?"""
        return self._is_active

    @property
    def soma_detected(self) -> bool:
        """Wurde 'Soma' in irgendeinem Partial erkannt?"""
        return self._soma_detected

    @property
    def confirmed_text(self) -> str:
        """Durch LocalAgreement bestätigter Text."""
        return self._confirmed_text

    @property
    def last_partial(self) -> Optional[PartialResult]:
        """Letztes Partial-Result."""
        return self._last_partial

    @property
    def audio_duration(self) -> float:
        """Bisherige Audio-Dauer in Sekunden."""
        return self._total_samples / 16000 if self._total_samples else 0.0
