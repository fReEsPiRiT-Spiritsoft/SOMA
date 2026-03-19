"""
SOMA-AI Voice Pipeline
========================
Dauerhaftes Zuhören, Emotion-Tracking, STT/TTS – wie ZORA oder KITT.

Module:
  vad.py      — Continuous Voice Activity Detection (WebRTC VAD)
  stt.py      — Speech-to-Text (faster-whisper / CTranslate2)
  tts.py      — Text-to-Speech (Piper TTS, deutsche Stimme)
  emotion.py  — Echtzeit Emotionsanalyse (Valence/Arousal/Stress)
  ambient.py  — Proaktive Intelligenz (Streit, Stress, Tipps)
  pipeline.py — Hauptschleife: verbindet alles zu EINEM lebendigen System
"""

from brain_core.voice.pipeline import VoicePipeline
from brain_core.voice.vad import ContinuousVAD, SpeechSegment
from brain_core.voice.stt import STTEngine, TranscriptionResult
from brain_core.voice.tts import TTSEngine, SpeechEmotion
from brain_core.voice.micro_expressions import (
    MicroExpressionMapper,
    MicroExpression,
    MicroExpressionContext,
    MicroState,
)
from brain_core.voice.emotion import EmotionEngine, EmotionState, RoomMood, RoomAtmosphere
from brain_core.voice.ambient import AmbientIntelligence, Intervention, InterventionType

__all__ = [
    "VoicePipeline",
    "ContinuousVAD",
    "SpeechSegment",
    "STTEngine",
    "TranscriptionResult",
    "TTSEngine",
    "SpeechEmotion",
    "MicroExpressionMapper",
    "MicroExpression",
    "MicroExpressionContext",
    "MicroState",
    "EmotionEngine",
    "EmotionState",
    "RoomMood",
    "RoomAtmosphere",
    "AmbientIntelligence",
    "Intervention",
    "InterventionType",
]
