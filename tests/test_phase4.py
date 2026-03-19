"""
SOMA-AI Phase 4: Emotionen & Biometrie — Testbatterie
======================================================
25 Tests die alle Phase-4 Erweiterungen verifizieren:

  P4.1  pitch_analyzer.py  — Jitter, Shimmer, EmotionVector
  P4.2  pipeline.py        — Emotion→Memory Wiring
  P4.3  tts.py             — Prosodie-Mapping
  P4.4  shader_logic.js    — Emotion Overlay (Struktur-Check)
  P4.5  main.py            — API Endpoint

Non-negotiable: Kein Test darf fehlschlagen.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np
import pytest

# ── Projekt-Root zum Path hinzufuegen ────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


# ══════════════════════════════════════════════════════════════════════════
#  P4.1 — PITCH ANALYZER: Tiefe Stimmanalyse
# ══════════════════════════════════════════════════════════════════════════

class TestPitchAnalyzer:
    """Tests fuer die erweiterte Stimmanalyse."""

    def test_import_voice_emotion_vector(self):
        """VoiceEmotionVector ist importierbar und hat alle 6 Emotionen."""
        from brain_core.safety.pitch_analyzer import VoiceEmotionVector

        vec = VoiceEmotionVector()
        assert hasattr(vec, "happy")
        assert hasattr(vec, "sad")
        assert hasattr(vec, "stressed")
        assert hasattr(vec, "tired")
        assert hasattr(vec, "angry")
        assert hasattr(vec, "neutral")
        assert hasattr(vec, "confidence")
        assert hasattr(vec, "dominant_emotion")
        assert vec.neutral == 1.0  # Default: neutral dominant

    def test_emotion_vector_threshold(self):
        """Confidence-Threshold bei 0.65 — darunter 'nicht erkannt'."""
        from brain_core.safety.pitch_analyzer import VoiceEmotionVector

        low_conf = VoiceEmotionVector(confidence=0.5)
        assert not low_conf.is_detected

        high_conf = VoiceEmotionVector(confidence=0.7)
        assert high_conf.is_detected

    def test_emotion_vector_as_dict(self):
        """as_dict liefert serialisierbares Dict mit allen Feldern."""
        from brain_core.safety.pitch_analyzer import VoiceEmotionVector

        vec = VoiceEmotionVector(happy=0.8, sad=0.1, neutral=0.2, confidence=0.9)
        d = vec.as_dict
        assert isinstance(d, dict)
        assert "happy" in d
        assert "sad" in d
        assert "dominant" in d
        assert d["dominant"] == "happy"
        # JSON serialisierbar
        json.dumps(d)

    def test_pitch_result_has_emotion_vector(self):
        """PitchResult enthaelt VoiceEmotionVector + erweiterte Features."""
        from brain_core.safety.pitch_analyzer import PitchResult, VoiceEmotionVector

        result = PitchResult(
            fundamental_freq_hz=200.0,
            is_child=False,
            estimated_age_group="adult",
            confidence=0.9,
            stress_level=0.3,
        )
        assert isinstance(result.emotion_vector, VoiceEmotionVector)
        assert hasattr(result, "jitter_percent")
        assert hasattr(result, "shimmer_percent")
        assert hasattr(result, "speaking_rate")
        assert hasattr(result, "energy_rms")
        assert hasattr(result, "spectral_centroid")

    def test_analyze_synthetic_sine(self):
        """Analyse eines synthetischen 200Hz Sinus → erkennt adult + liefert EmotionVector."""
        from brain_core.safety.pitch_analyzer import PitchAnalyzer

        analyzer = PitchAnalyzer(sample_rate=16000)
        sr = 16000
        duration = 0.5  # 500ms
        freq = 120.0  # Maennliche Erwachsenen-Stimme

        t = np.linspace(0, duration, int(sr * duration), endpoint=False)
        audio = np.sin(2 * np.pi * freq * t).astype(np.float32)

        result = analyzer.analyze(audio, sample_rate=sr, duration_sec=duration)

        assert result.estimated_age_group == "adult"
        assert not result.is_child
        assert result.emotion_vector is not None
        assert isinstance(result.emotion_vector.happy, float)

    def test_analyze_child_frequency(self):
        """F0 > 250Hz → Kind erkannt."""
        from brain_core.safety.pitch_analyzer import PitchAnalyzer

        analyzer = PitchAnalyzer(sample_rate=16000)
        sr = 16000
        duration = 0.5
        freq = 300.0  # Kinder-Frequenz

        t = np.linspace(0, duration, int(sr * duration), endpoint=False)
        audio = np.sin(2 * np.pi * freq * t).astype(np.float32)

        result = analyzer.analyze(audio, sample_rate=sr, duration_sec=duration)
        assert result.is_child
        assert result.estimated_age_group == "child"

    def test_jitter_computation(self):
        """Jitter wird berechnet und ist >= 0."""
        from brain_core.safety.pitch_analyzer import PitchAnalyzer

        analyzer = PitchAnalyzer(sample_rate=16000)
        sr = 16000
        duration = 1.0
        t = np.linspace(0, duration, int(sr * duration), endpoint=False)

        # Sinus mit leichter Frequenz-Modulation → Jitter
        freq_mod = 200.0 + 5.0 * np.sin(2 * np.pi * 3 * t)
        phase = np.cumsum(2 * np.pi * freq_mod / sr)
        audio = np.sin(phase).astype(np.float32)

        result = analyzer.analyze(audio, sample_rate=sr, duration_sec=duration)
        assert result.jitter_percent >= 0.0

    def test_shimmer_computation(self):
        """Shimmer wird berechnet und ist >= 0."""
        from brain_core.safety.pitch_analyzer import PitchAnalyzer

        analyzer = PitchAnalyzer(sample_rate=16000)
        sr = 16000
        duration = 1.0
        t = np.linspace(0, duration, int(sr * duration), endpoint=False)

        # Sinus mit Amplitude-Modulation → Shimmer
        envelope = 1.0 + 0.3 * np.sin(2 * np.pi * 5 * t)
        audio = (np.sin(2 * np.pi * 200 * t) * envelope).astype(np.float32)

        result = analyzer.analyze(audio, sample_rate=sr, duration_sec=duration)
        assert result.shimmer_percent >= 0.0

    def test_speaking_rate_computation(self):
        """Speaking Rate wird berechnet."""
        from brain_core.safety.pitch_analyzer import PitchAnalyzer

        analyzer = PitchAnalyzer(sample_rate=16000)
        sr = 16000
        duration = 2.0
        t = np.linspace(0, duration, int(sr * duration), endpoint=False)

        # Simuliere Silben: periodische Energie-Bursts
        audio = np.zeros_like(t)
        for burst_start in np.arange(0, duration, 0.25):  # 4 Silben/s
            mask = (t >= burst_start) & (t < burst_start + 0.1)
            audio[mask] = np.sin(2 * np.pi * 200 * t[mask]) * 0.5

        result = analyzer.analyze(audio.astype(np.float32), sample_rate=sr, duration_sec=duration)
        assert result.speaking_rate >= 0.0

    def test_smoothed_emotion(self):
        """get_smoothed_emotion mittelt ueber mehrere Analysen."""
        from brain_core.safety.pitch_analyzer import PitchAnalyzer

        analyzer = PitchAnalyzer(sample_rate=16000)
        sr = 16000
        duration = 0.5
        t = np.linspace(0, duration, int(sr * duration), endpoint=False)
        audio = np.sin(2 * np.pi * 200 * t).astype(np.float32)

        # Mehrere Analysen → Sliding Window
        for _ in range(5):
            analyzer.analyze(audio, sample_rate=sr, duration_sec=duration)

        smoothed = analyzer.get_smoothed_emotion()
        assert 0.0 <= smoothed.happy <= 1.0
        assert 0.0 <= smoothed.neutral <= 1.0

    def test_reset(self):
        """reset() leert den State."""
        from brain_core.safety.pitch_analyzer import PitchAnalyzer

        analyzer = PitchAnalyzer(sample_rate=16000)
        sr = 16000
        t = np.linspace(0, 0.5, int(sr * 0.5), endpoint=False)
        audio = np.sin(2 * np.pi * 200 * t).astype(np.float32)
        analyzer.analyze(audio, sample_rate=sr, duration_sec=0.5)

        analyzer.reset()
        assert analyzer._analysis_count == 0
        assert len(analyzer._recent_vectors) == 0

    def test_dominant_emotion_post_init(self):
        """__post_init__ setzt dominant_emotion korrekt."""
        from brain_core.safety.pitch_analyzer import VoiceEmotionVector

        vec = VoiceEmotionVector(
            happy=0.1, sad=0.0, stressed=0.8, tired=0.0, angry=0.0, neutral=0.1,
        )
        assert vec.dominant_emotion == "stressed"


# ══════════════════════════════════════════════════════════════════════════
#  P4.3 — TTS PROSODIE MAPPING
# ══════════════════════════════════════════════════════════════════════════

class TestTTSProsodie:
    """Tests fuer die erweiterte TTS Prosodie."""

    def test_from_emotion_happy(self):
        """happy → energetisch (schneller, hoeherer Pitch)."""
        from brain_core.voice.tts import SpeechEmotion

        se = SpeechEmotion.from_emotion("happy")
        assert se.speed >= 1.0  # Schneller als neutral
        assert se.pitch >= 1.0  # Hoeherer Pitch

    def test_from_emotion_sad(self):
        """sad → sanft (langsamer, tieferer Pitch, leiser)."""
        from brain_core.voice.tts import SpeechEmotion

        se = SpeechEmotion.from_emotion("sad")
        assert se.speed <= 0.9
        assert se.pitch <= 0.95
        assert se.volume <= 0.8

    def test_from_emotion_angry(self):
        """angry → sachlich-neutral (kein Gegendruck!)."""
        from brain_core.voice.tts import SpeechEmotion

        se = SpeechEmotion.from_emotion("angry")
        assert se.speed <= 1.0  # Nicht zu schnell
        assert se.volume <= 0.9  # Nicht zu laut

    def test_from_emotion_stressed(self):
        """stressed → beruhigend."""
        from brain_core.voice.tts import SpeechEmotion

        se = SpeechEmotion.from_emotion("stressed")
        assert se.speed <= 0.9  # Langsam, beruhigend

    def test_from_emotion_unknown(self):
        """Unbekannte Emotion → Default."""
        from brain_core.voice.tts import SpeechEmotion

        se = SpeechEmotion.from_emotion("unknown_emotion")
        assert isinstance(se, SpeechEmotion)

    def test_from_voice_emotion_vector(self):
        """Gewichtete Interpolation aus EmotionVector."""
        from brain_core.voice.tts import SpeechEmotion

        vec = {
            "happy": 0.7, "sad": 0.1, "stressed": 0.0,
            "tired": 0.0, "angry": 0.0, "neutral": 0.2,
        }
        se = SpeechEmotion.from_voice_emotion(vec)
        assert isinstance(se, SpeechEmotion)
        assert se.speed > 0.5  # Nicht absurd
        assert se.pitch > 0.5

    def test_from_voice_emotion_empty(self):
        """Leerer Vector → Default."""
        from brain_core.voice.tts import SpeechEmotion

        se = SpeechEmotion.from_voice_emotion({})
        assert isinstance(se, SpeechEmotion)

    def test_new_presets_exist(self):
        """Neue Presets warm(), empathetic(), neutral_sachlich() existieren."""
        from brain_core.voice.tts import SpeechEmotion

        assert callable(SpeechEmotion.warm)
        assert callable(SpeechEmotion.empathetic)
        assert callable(SpeechEmotion.neutral_sachlich)

        w = SpeechEmotion.warm()
        e = SpeechEmotion.empathetic()
        n = SpeechEmotion.neutral_sachlich()
        assert isinstance(w, SpeechEmotion)
        assert isinstance(e, SpeechEmotion)
        assert isinstance(n, SpeechEmotion)


# ══════════════════════════════════════════════════════════════════════════
#  P4.4 — SHADER: Emotion Overlay Struktur
# ══════════════════════════════════════════════════════════════════════════

class TestShaderEmotionOverlay:
    """Strukturelle Tests fuer die Shader-Erweiterungen."""

    def _read_shader(self) -> str:
        shader_path = ROOT / "soma_face_tablet" / "shader_logic.js"
        return shader_path.read_text(encoding="utf-8")

    def test_emotion_uniforms_in_wave_shader(self):
        """WAVE_FRAG hat u_emotion, u_emotionInt, u_emotionPulse."""
        code = self._read_shader()
        # In WAVE_FRAG Block
        wave_start = code.index("WAVE_FRAG")
        wave_end = code.index("ORB_FRAG")
        wave_block = code[wave_start:wave_end]
        assert "u_emotion" in wave_block
        assert "u_emotionInt" in wave_block
        assert "u_emotionPulse" in wave_block

    def test_emotion_uniforms_in_orb_shader(self):
        """ORB_FRAG hat u_emotion, u_emotionInt, u_emotionPulse."""
        code = self._read_shader()
        orb_start = code.index("ORB_FRAG")
        orb_block = code[orb_start:]
        assert "u_emotion" in orb_block
        assert "u_emotionInt" in orb_block
        assert "u_emotionPulse" in orb_block

    def test_set_emotion_api(self):
        """SomaFace.setEmotion() Methode existiert."""
        code = self._read_shader()
        assert "setEmotion(" in code
        assert "setEmotionFromVector(" in code

    def test_emotion_color_presets(self):
        """EMOTION_COLORS hat alle Emotionen."""
        code = self._read_shader()
        for emotion in ["happy", "sad", "stressed", "tired", "angry", "neutral"]:
            assert f"'{emotion}'" in code or f'"{emotion}"' in code or f"{emotion}:" in code

    def test_emotion_interpolation_in_animation(self):
        """Emotion-State wird im Animation-Loop interpoliert."""
        code = self._read_shader()
        assert "emotionSpeed" in code
        assert "state.emotionR" in code
        assert "state.emotionIntensity" in code

    def test_emotion_uniforms_in_make_uniforms(self):
        """makeUniforms() enthaelt Emotion-Uniforms."""
        code = self._read_shader()
        uniforms_start = code.index("makeUniforms")
        # Suche den schliessenden Block (nach ~500 Zeichen)
        uniforms_end = code.index("};", uniforms_start + 50) + 2
        uniforms_block = code[uniforms_start:uniforms_end]
        assert "u_emotion" in uniforms_block
        assert "u_emotionInt" in uniforms_block
        assert "u_emotionPulse" in uniforms_block


# ══════════════════════════════════════════════════════════════════════════
#  P4.2 + P4.5 — INTEGRATION: Pipeline + Memory + API
# ══════════════════════════════════════════════════════════════════════════

class TestIntegration:
    """Tests fuer die Integration der Emotion-Pipeline."""

    def test_pipeline_has_pitch_analyzer(self):
        """VoicePipeline hat pitch_analyzer Attribut."""
        from brain_core.voice.pipeline import VoicePipeline

        # Imports checken, nicht instanziieren (braucht Audio-Device)
        import inspect
        src = inspect.getsource(VoicePipeline.__init__)
        assert "pitch_analyzer" in src
        assert "PitchAnalyzer" in src

    def test_pipeline_has_emotion_vector_property(self):
        """VoicePipeline hat current_emotion_vector Property."""
        from brain_core.voice.pipeline import VoicePipeline

        assert hasattr(VoicePipeline, "current_emotion_vector")

    def test_memory_after_response_accepts_emotion_vector(self):
        """after_response() akzeptiert emotion_vector Parameter."""
        import inspect
        from brain_core.memory.integration import after_response

        sig = inspect.signature(after_response)
        assert "emotion_vector" in sig.parameters

    def test_store_interaction_accepts_emotion_vector(self):
        """store_interaction() akzeptiert emotion_vector Parameter."""
        import inspect
        from brain_core.memory.memory_orchestrator import MemoryOrchestrator

        sig = inspect.signature(MemoryOrchestrator.store_interaction)
        assert "emotion_vector" in sig.parameters

    def test_api_emotion_endpoint_exists(self):
        """FastAPI hat /api/v1/voice/emotion Endpoint."""
        import inspect
        src = Path(ROOT / "brain_core" / "main.py").read_text()
        assert "/api/v1/voice/emotion" in src
        assert "get_voice_emotion" in src


# ══════════════════════════════════════════════════════════════════════════
#  ZUSAMMENFASSUNG
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  SOMA-AI Phase 4: Emotionen & Biometrie — Testbatterie")
    print("=" * 70 + "\n")
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
