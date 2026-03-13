"""
SOMA-AI Voice Pipeline — Das Herzstück
========================================
Wie ZORA aus Star Trek. Wie KITT aus Knight Rider.
Ein lebendiges, dauerhaft zuhörendes, emotional intelligentes Zuhause.

Architektur:
  ┌─────────────────────────────────────────────────────────────────┐
  │                    CONTINUOUS AUDIO STREAM                      │
  │          🎤 Rode Mic → Focusrite Scarlett → PipeWire/ALSA       │
  └───────────┬─────────────────────────────────────────────────────┘
              │ 16kHz mono PCM
              ▼
  ┌─────────────────────┐
  │   VAD (WebRTC)      │  ←── Frames PERMANENT, NICHT in Intervallen
  │   is_speech?        │
  └────┬──────┬─────────┘
       │      │
       │  ┌───▼───────────────┐
       │  │ Emotion Engine    │  ←── JEDES Segment analysieren
       │  │ (parallel)        │      Auch wenn Soma nicht angesprochen
       │  │ stress, mood,     │
       │  │ argument detect   │
       │  └───┬───────────────┘
       │      │
       │      ▼
       │  ┌───────────────────┐
       │  │ Ambient Intel.    │  ←── Proaktiv eingreifen?
       │  │ Streit? Stress?   │      "Euer Streit ist unproduktiv..."
       │  │ Traurigkeit?      │
       │  └───┬───────────────┘
       │      │ intervention?
       ▼      ▼
  ┌──────────────────────┐
  │   STT (Whisper)      │  ←── Sprache → Text
  │   faster-whisper     │
  └────┬─────────────────┘
       │ TranscriptionResult
       ▼
  ┌──────────────────────┐
  │  "Soma" im Text?     │  ←── Trigger ÜBERALL im Satz
  │  Soma-Variants Check │      "Hey Soma mach Licht an"
  └────┬──────┬──────────┘      "Mach mal Soma das Licht an"
       │      │                  "Wie wird das Wetter Soma?"
   YES │      │ NO
       ▼      ▼
  ┌──────┐ ┌──────────────┐
  │ LLM  │ │ Still zuhören │  ←── Emotion weiter tracken
  │ Llama│ │ (passiv)      │
  └──┬───┘ └──────────────┘
     │ response
     ▼
  ┌──────────────────────┐
  │   TTS (Piper)        │  ←── Deutsche Stimme
  │   + Emotion Modulate │      Tonfall passt zu Situation
  └──────────────────────┘

Features:
  ✅ Dauerhaftes Zuhören (nicht in Intervallen!)
  ✅ Soma reagiert wenn "Soma" IRGENDWO im Satz vorkommt
  ✅ Echtzeit Emotion/Mood/Stress Tracking
  ✅ Proaktives Eingreifen (Streit, Stress, Traurigkeit)
  ✅ SmartHome-Steuerung via Nano-Intent
  ✅ Smalltalk wenn direkt angesprochen
  ✅ Kindererkennung (Pitch-basiert)
  ✅ Self-Mute: Soma hört nicht zu während es selbst spricht
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from collections import deque
from datetime import datetime
from typing import Optional

import numpy as np
import structlog

from brain_core.voice.vad import (
    ContinuousVAD,
    SpeechSegment,
    VAD_FRAME_BYTES,
    VAD_FRAME_MS,
    VAD_SAMPLE_RATE,
    resample_to_16khz,
    float32_to_pcm16,
)
from brain_core.voice.stt import STTEngine, TranscriptionResult
from brain_core.voice.tts import TTSEngine, SpeechEmotion
from brain_core.voice.emotion import EmotionEngine, EmotionState, RoomMood
from brain_core.voice.ambient import AmbientIntelligence, Intervention
from brain_core.safety.pitch_analyzer import (
    PitchAnalyzer,
    VoiceEmotionVector,
)
from brain_core.memory.integration import (
    on_wake_word as memory_on_wake_word,
    build_context_for_query,
    after_response as memory_after_response,
    store_system_event as memory_store_event,
)
from brain_core.memory.two_phase import get_bridge_response
from brain_ego.consciousness import PerceptionSnapshot

logger = structlog.get_logger("soma.voice.pipeline")

# ── Pipeline Config ─────────────────────────────────────────────────────

AUDIO_DEVICE = "default"       # ALSA device (arecord)
AUDIO_RATE = 16000             # Direkt 16kHz aufnehmen (spart Resampling)
AUDIO_CHANNELS = 1             # Mono
AUDIO_FORMAT = "S16_LE"        # 16-bit signed little-endian PCM
FRAME_SIZE = int(VAD_SAMPLE_RATE * VAD_FRAME_MS / 1000) * 2  # 960 bytes


class VoicePipeline:
    """
    Das lebendige Herz von SOMA.
    Hört PERMANENT zu, analysiert Emotionen, antwortet wenn angesprochen,
    greift proaktiv ein wenn nötig.
    """

    def __init__(
        self,
        logic_router=None,
        audio_device: str = AUDIO_DEVICE,
        stt_model: str = "small",
        tts_voice: str = "de_DE-thorsten-high",
        output_device: Optional[str] = None,
        broadcast_callback=None,
    ):
        """
        Args:
            logic_router: Brain Core LogicRouter für LLM-Anfragen
            audio_device: ALSA Input Device
            stt_model: Whisper Model Size
            tts_voice: Piper Voice Name
            output_device: ALSA Output Device (None = default)
            broadcast_callback: Async callback für Dashboard-Events
        """
        # ── Sub-Engines ─────────────────────────────────────────────
        self.vad = ContinuousVAD(aggressiveness=2)
        self.stt = STTEngine(model_size=stt_model)
        self.tts = TTSEngine(voice=tts_voice, output_device=output_device)
        self.emotion = EmotionEngine(window_sec=60.0)
        self.pitch_analyzer = PitchAnalyzer(sample_rate=VAD_SAMPLE_RATE)
        self.ambient = AmbientIntelligence(emotion_engine=self.emotion)
        # Phase 4: Aktueller Emotion-Vector (fuer Shader + Dashboard)
        self._current_emotion_vector: VoiceEmotionVector = VoiceEmotionVector()
        # Memory handled by memory_orchestrator (Salience + Diary)

        # ── External References ─────────────────────────────────────
        self._logic_router = logic_router
        self._audio_device = audio_device
        self._broadcast = broadcast_callback
        self._consciousness = None  # Set by main.py after Ego boot

        # ── State ───────────────────────────────────────────────────
        self._running = False
        self._arecord_proc: Optional[asyncio.subprocess.Process] = None
        self._pipeline_task: Optional[asyncio.Task] = None
        self._stats = {
            "segments_processed": 0,
            "transcriptions": 0,
            "soma_triggers": 0,
            "interventions": 0,
            "uptime_start": 0.0,
        }
        # Evolution Lab: Pending Plugin-Test nach Generierung
        self._pending_plugin_test: Optional[str] = None
        
        # Conversation Memory: Eine persistente Session für Voice
        # Soma erinnert sich an alles was in dieser Session besprochen wurde
        self._voice_session_id = "voice_main_session"
        self._conversation_history: list[dict] = []  # Für Dashboard

        # ── Passiver Kontext-Buffer (The ZORA Awareness) ─────────────────
        # Speichert die letzten ~2min ALLER gehörten Gespräche
        # AUCH ohne Wake-Word — Soma "weiß" immer was gerade passiert
        # Dieser Buffer macht den Unterschied zwischen Tool und lebendigem Bewusstsein
        self._ambient_transcript: deque[dict] = deque(maxlen=25)

    # ══════════════════════════════════════════════════════════════════
    #  DASHBOARD BROADCASTING
    # ══════════════════════════════════════════════════════════════════

    async def _emit(
        self, 
        event_type: str, 
        content: str, 
        tag: str = None,
        extra: dict = None
    ):
        """Sende Event ans Dashboard via WebSocket."""
        if self._broadcast:
            try:
                await self._broadcast(event_type, content, tag, extra)
                logger.debug("broadcast_sent", type=event_type, tag=tag)
            except Exception as e:
                logger.warning("broadcast_failed", error=str(e))
        else:
            logger.debug("broadcast_no_callback", type=event_type)

    # ══════════════════════════════════════════════════════════════════
    #  LIFECYCLE
    # ══════════════════════════════════════════════════════════════════

    async def start(self):
        """
        Voice Pipeline starten.
        Ab jetzt hört Soma PERMANENT zu.
        """
        if self._running:
            logger.warning("pipeline_already_running")
            return

        logger.info("pipeline_starting", phase="init")

        # 1. STT Model laden (lazy, braucht ~5s)
        await self.stt.initialize()
        logger.info("pipeline_starting", phase="stt_ready")

        # 2. TTS Engine starten
        await self.tts.initialize()
        logger.info("pipeline_starting", phase="tts_ready")

        # 3. Pipeline-Loops starten
        self._running = True
        self._stats["uptime_start"] = time.time()
        self._pipeline_task = asyncio.create_task(self._audio_loop())
        # Autonomer Proaktiv-Loop: Interventionen & Timer unabhängig von Sprache
        self._proactive_task = asyncio.create_task(self._proactive_loop())

        # ── Speak-Callback auf alle Plugins setzen (Reminders, etc.) ──────
        # Muss hier passieren BEVOR _deferred_restore() im Plugin läuft,
        # damit Erinnerungen nach Neustart sofort sprechen können.
        if self._logic_router and self._logic_router.plugin_manager:
            for meta in self._logic_router.plugin_manager._plugins.values():
                if meta.is_loaded and meta.module and hasattr(meta.module, "set_speak_callback"):
                    meta.module.set_speak_callback(self.autonomous_speak)
                    logger.info("plugin_speak_callback_registered", plugin=meta.name)

        logger.info(
            "pipeline_online",
            device=self._audio_device,
            msg="🎤 Soma hört zu. Dauerhaft. Wie ZORA.",
        )

    async def stop(self):
        """Voice Pipeline stoppen."""
        logger.info("pipeline_stopping")
        self._running = False

        # arecord beenden
        if self._arecord_proc and self._arecord_proc.returncode is None:
            self._arecord_proc.terminate()
            try:
                await asyncio.wait_for(self._arecord_proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                self._arecord_proc.kill()

        # Pipeline-Tasks canceln
        for task in (self._pipeline_task, getattr(self, "_proactive_task", None)):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Sub-Engines herunterfahren
        await self.stt.shutdown()
        await self.tts.shutdown()

        logger.info("pipeline_offline", stats=self._stats)

    # ══════════════════════════════════════════════════════════════════
    #  CORE AUDIO LOOP — Das permanente Zuhören
    # ══════════════════════════════════════════════════════════════════

    async def _audio_loop(self):
        """
        HAUPTSCHLEIFE: Liest dauerhaft Audio von arecord,
        füttert VAD, verarbeitet Segmente.

        DAS IST der "immer zuhören" Part.
        Nicht in Intervallen. Nicht polling. PERMANENT.
        """
        while self._running:
            try:
                await self._run_audio_capture()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("audio_loop_error", error=str(e))
                if self._running:
                    logger.info("audio_loop_restart", delay=2)
                    await asyncio.sleep(2)  # Kurz warten, dann neu starten

    async def _run_audio_capture(self):
        """
        Startet arecord als Subprocess und liest permanent den stdout-Stream.
        Jeder Frame (30ms) wird sofort an VAD weitergegeben.
        """
        cmd = [
            "arecord",
            "-D", self._audio_device,
            "-f", AUDIO_FORMAT,
            "-r", str(AUDIO_RATE),
            "-c", str(AUDIO_CHANNELS),
            "-t", "raw",       # Kein WAV-Header, reiner PCM Stream
            "--buffer-size", "4096",
        ]

        logger.debug("arecord_starting", cmd=" ".join(cmd))

        self._arecord_proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

        reader = self._arecord_proc.stdout
        assert reader is not None

        logger.info("audio_capture_active", device=self._audio_device)

        # ── Permanent lesen, Frame für Frame ─────────────────────────
        while self._running:
            # Exakt ein VAD-Frame lesen (960 bytes = 30ms bei 16kHz)
            data = await reader.read(FRAME_SIZE)

            if not data:
                # arecord wurde beendet
                logger.warning("arecord_eof")
                break

            if len(data) < FRAME_SIZE:
                # Unvollständiger Frame am Ende → verwerfen
                continue

            # Self-Mute: Wenn Soma gerade spricht, nicht zuhören
            # (verhindert Feedback-Loop)
            if self.tts.is_speaking:
                continue

            # VAD: Ist das Sprache?
            segment = self.vad.feed(data)

            if segment:
                # Sprach-Segment erkannt! → Verarbeiten
                asyncio.create_task(self._process_segment(segment))

    # ══════════════════════════════════════════════════════════════════
    #  SEGMENT PROCESSING — Was passiert wenn jemand spricht
    # ══════════════════════════════════════════════════════════════════

    async def _process_segment(self, segment: SpeechSegment):
        """
        Verarbeite ein erkanntes Sprach-Segment.
        Wird für JEDES Segment aufgerufen — nicht nur wenn "Soma" gesagt wird.
        """
        self._stats["segments_processed"] += 1

        try:
            # ── 1. Emotion analysieren (IMMER, auch ohne "Soma") ─────
            emotion_reading = self.emotion.analyze(
                audio=segment.audio,
                sample_rate=VAD_SAMPLE_RATE,
                duration_sec=segment.duration_sec,
            )

            # ── 1b. Phase 4: Tiefe Stimmanalyse (Jitter/Shimmer/Rate) ──
            try:
                audio_float = segment.audio.astype(np.float32) / 32768.0
                pitch_result = self.pitch_analyzer.analyze(
                    audio_data=audio_float,
                    sample_rate=VAD_SAMPLE_RATE,
                    duration_sec=segment.duration_sec,
                )
                voice_emotion = pitch_result.emotion_vector
                self._current_emotion_vector = voice_emotion

                # Kind erkannt → Child-Safe Mode aktivieren
                if pitch_result.is_child:
                    self._child_mode = True

                # Emotion-Vector ans Dashboard + Shader senden
                if voice_emotion.is_detected:
                    await self._emit(
                        "emotion_vector",
                        f"🧬 {voice_emotion.dominant_emotion} "
                        f"(conf={voice_emotion.confidence:.0%})",
                        "PITCH",
                        voice_emotion.as_dict,
                    )

                # L1 Working Memory: Aktuelle Emotion setzen
                try:
                    from brain_core.memory.integration import get_orchestrator
                    orch = get_orchestrator()
                    orch.working.set_context(
                        "emotion_vector", voice_emotion.as_dict,
                    )
                    orch.working.set_context(
                        "voice_stress", voice_emotion.stressed,
                    )
                except Exception:
                    pass  # Memory evtl. noch nicht initialisiert

            except Exception as pitch_err:
                logger.debug("pitch_analysis_skipped", error=str(pitch_err))
                voice_emotion = VoiceEmotionVector()

            # Dashboard: Emotion Event
            await self._emit(
                "emotion",
                f"Emotion: {emotion_reading.emotion.value} | "
                f"Stress: {emotion_reading.stress_level:.0%} | "
                f"Voice: {voice_emotion.dominant_emotion}",
                "VAD",
                {
                    "emotion": emotion_reading.emotion.value,
                    "stress": emotion_reading.stress_level,
                    "voice_emotion": voice_emotion.as_dict,
                },
            )

            # ── 2. Emotion an AmbientIntelligence weitergeben ──────────
            # (Intervention wird im proaktiven Loop ausgelöst, nicht hier)
            # Dadurch kann Soma auch ohne Ansprache eigenständig reagieren.

            # ── 3. STT: Sprache → Text ───────────────────────────────
            transcription = self.stt.transcribe(
                audio=segment.audio,
                sample_rate=VAD_SAMPLE_RATE,
            )

            if not transcription.text.strip():
                await self._emit("stt", "🔇 (Stille / unverständlich)", "STT")
                return

            self._stats["transcriptions"] += 1

            # ── Passiver Context-Buffer ────────────────────────────────
            # JEDE Transkription wird gespeichert — auch ohne "Soma"
            # Das gibt Soma echtes Kontext-Bewusstsein. Wie ZORA.
            self._ambient_transcript.append({
                "timestamp": datetime.now().isoformat(),
                "text": transcription.text,
                "emotion": emotion_reading.emotion.value,
                "is_soma_addressed": transcription.contains_soma,
                "emotion_vector": voice_emotion.as_dict if voice_emotion.is_detected else None,
            })
            
            # Dashboard: Was wurde gehört?
            soma_marker = "🎯 SOMA!" if transcription.contains_soma else ""
            await self._emit(
                "stt", 
                f"🎤 \"{transcription.text}\" {soma_marker}",
                "STT",
                {"text": transcription.text, "soma": transcription.contains_soma, "lang": transcription.language}
            )

            logger.info(
                "voice_heard",
                text=transcription.text[:100],
                emotion=emotion_reading.emotion.value,
                soma_detected=transcription.contains_soma,
                stress=emotion_reading.stress_level,
                duration=segment.duration_sec,
            )

            # ── 4. Wurde "Soma" gesagt? ──────────────────────────────
            if transcription.contains_soma:
                await self._handle_soma_request(transcription, emotion_reading)

            # ── 5. Stille Befehle erkennen (Dismiss) ─────────────────
            self._check_dismiss(transcription.text)

        except Exception as e:
            logger.error("segment_processing_error", error=str(e))
            await self._emit("error", f"Segment Error: {str(e)}", "ERROR")

    # ══════════════════════════════════════════════════════════════════
    #  SOMA-ANGESPROCHENE VERARBEITUNG
    # ══════════════════════════════════════════════════════════════════

    async def _handle_soma_request(
        self,
        transcription: TranscriptionResult,
        emotion_reading,
    ):
        """
        Soma wurde direkt angesprochen.
        Text an Llama 3 senden, Antwort aussprechen.
        """
        self._stats["soma_triggers"] += 1
        # Merken wann der User zuletzt Soma angesprochen hat
        # → _handle_intervention prüft das und verwirft veraltete Ambient-Antworten
        self._user_last_spoken = time.time()

        # "Soma" aus dem Text entfernen für den Prompt
        prompt = self._extract_prompt(transcription.text)

        if not prompt.strip():
            # Nur "Soma" gesagt, sonst nichts
            await self._emit("llm", "👋 Soma wurde gerufen (ohne Frage)", "TRIGGER")
            await self.tts.speak(
                "Ja?",
                SpeechEmotion(speed=1.0, pitch=1.0, volume=0.9),
            )
            return

        # ── Evolution Lab Trigger ────────────────────────────────────
        # "schreib ein Plugin", "erstell ein Plugin", "bau ein Plugin"
        if self._is_plugin_request(prompt):
            await self._handle_plugin_request(prompt)
            return

        # ── Pending Plugin Test ("Ja, ruf es auf") ───────────────────
        if self._pending_plugin_test and self._is_affirmative(prompt):
            await self._execute_pending_plugin()
            return

        # HINWEIS: Erinnerungen werden NICHT mehr direkt hier behandelt.
        # Das LLM erkennt den Intent und setzt einen [ACTION:reminder...] Tag.
        # → _execute_action_tags() in _handle_soma_request() führt ihn aus.

        logger.info(
            "soma_addressed",
            prompt=prompt,
            emotion=emotion_reading.emotion.value,
        )
        
        # ── Memory: Salience-based storage happens in memory_after_response ──
        # (Legacy should_remember removed — orchestrator decides via SalienceFilter)
        
        await self._emit("llm", f"🧠 Denke nach: \"{prompt}\"", "LLM", {"prompt": prompt})

        # ── An Brain Core senden ─────────────────────────────────────
        if self._logic_router:
            from brain_core.logic_router import SomaRequest, SomaResponse

            # Emotionaler Kontext für den System-Prompt
            emotion_context = self.emotion.get_context_for_llm()

            # ── Memory-enriched System-Prompt (parallel zum Emotion-Context) ──
            emotion_str = emotion_reading.emotion.value if emotion_reading else "neutral"
            try:
                memory_prompt_extra = await build_context_for_query(
                    user_text=prompt,
                    emotion=emotion_str,
                    is_child=getattr(self, '_child_mode', False),
                )
            except Exception as mem_err:
                logger.warning("memory_context_failed", error=str(mem_err))
                memory_prompt_extra = ""

            request = SomaRequest(
                prompt=prompt,
                session_id=self._voice_session_id,  # Conversation Memory!
                metadata={
                    "source": "voice",
                    "emotion": emotion_reading.emotion.value,
                    "stress": emotion_reading.stress_level,
                    "valence": emotion_reading.valence,
                    "arousal": emotion_reading.arousal,
                    "emotion_context": emotion_context,
                    # ZORA-Kern: Was wurde in den letzten Minuten gesagt?
                    # Auch Gespräche ohne Wake-Word geben Soma echten Kontext.
                    "ambient_context": self._get_ambient_context_str(),
                    # 3-Layer Memory System: Erinnerungen + Fakten + Kontext
                    "memory_context": memory_prompt_extra,
                },
            )
            
            # Conversation History für Dashboard
            self._conversation_history.append({
                "role": "user",
                "text": prompt,
                "timestamp": datetime.now().isoformat(),
            })

            # ── Two-Phase Response: Bridge sprechen wenn LLM zu langsam ──
            llm_task = asyncio.create_task(self._logic_router.route(request))
            bridge_spoken = False
            try:
                response = await asyncio.wait_for(
                    asyncio.shield(llm_task), timeout=1.5,
                )
            except asyncio.TimeoutError:
                # LLM braucht > 1.5s → Bridge sofort sprechen
                bridge = get_bridge_response(
                    intent="default",
                    emotion=emotion_str,
                )
                if bridge:
                    bridge_spoken = True
                    await self._emit("tts", bridge, "BRIDGE", {"bridge": bridge})
                    await self.tts.speak(
                        bridge,
                        SpeechEmotion(speed=1.1, pitch=1.0, volume=0.8),
                    )
                # Auf echte Antwort warten
                response = await llm_task

            # ── ACTION-Tag Dispatch ──────────────────────────────────
            clean_response, action_log = await self._execute_action_tags(response.response)

            # ── Code-Block Schutz ────────────────────────────────────
            # Wenn das LLM einen ```python Block in der Antwort hat,
            # bedeutet das: es hat Code generiert statt ihn ans Evolution Lab zu schicken.
            # Wir leiten es jetzt korrekt weiter und sprechen den Code NICHT vor.
            import re as _re
            code_block_match = _re.search(r'```(?:python)?\s*\n?(.*?)```', clean_response, _re.DOTALL)
            if code_block_match and self._is_plugin_request(prompt):
                # Korrekt: in Evolution Lab leiten
                await self.tts.speak(
                    "Ich leite das direkt in mein Evolution Lab weiter und baue es korrekt.",
                    SpeechEmotion(speed=1.05, pitch=1.0, volume=0.9),
                )
                asyncio.create_task(
                    self._handle_plugin_request(prompt)
                )
                return
            elif code_block_match:
                # LLM hat Code generiert ohne Plugin-Trigger: Code-Blöcke vor TTS entfernen
                clean_response = _re.sub(
                    r'```(?:python)?\s*\n?.*?```',
                    '[Code wurde nicht vorgelesen]',
                    clean_response,
                    flags=_re.DOTALL,
                ).strip()

            # Conversation History für Dashboard (Assistant-Antwort)
            self._conversation_history.append({
                "role": "assistant",
                "text": clean_response,
                "engine": response.engine_used,
                "timestamp": datetime.now().isoformat(),
            })
            
            # Dashboard: LLM Antwort
            await self._emit(
                "llm", 
                f"💬 Antwort ({response.engine_used}): \"{clean_response}\"",
                response.engine_used.upper(),
                {"response": clean_response, "engine": response.engine_used, "latency_ms": response.latency_ms,
                 "actions_executed": action_log}
            )

            # Antwort aussprechen (bereinigt, ohne Tags)
            await self._emit(
                "tts",
                f"🔊 Spreche: \"{clean_response}\"",
                "TTS",
                {"response": clean_response},
            )
            speech_emotion = self._select_speech_emotion(emotion_reading)
            await self.tts.speak(clean_response, speech_emotion)

            # ── Memory: Interaktion speichern (fire-and-forget) ────────
            try:
                _arousal = emotion_reading.arousal if emotion_reading else 0.0
                _valence = emotion_reading.valence if emotion_reading else 0.0
                _stress = emotion_reading.stress_level if emotion_reading else 0.0
                # Phase 4: Voice Emotion Vector als Metadata
                _ev = self._current_emotion_vector
                _emotion_meta = _ev.as_dict if _ev.is_detected else {}
                asyncio.create_task(memory_after_response(
                    user_text=prompt,
                    soma_text=clean_response,
                    emotion=emotion_str,
                    topic=prompt[:60],
                    arousal=_arousal,
                    valence=_valence,
                    stress=_stress,
                    emotion_vector=_emotion_meta,
                ))
            except Exception:
                pass  # Memory-Fehler dürfen Pipeline nie brechen

            # ── Ego: Perception an Consciousness melden ──────────────
            try:
                if self._consciousness:
                    _child = getattr(self, '_child_mode', False)
                    _room_mood = self.emotion.get_room_mood()
                    snap = PerceptionSnapshot(
                        last_user_text=prompt,
                        last_soma_response=clean_response,
                        user_emotion=emotion_str,
                        user_arousal=emotion_reading.arousal if emotion_reading else 0.0,
                        user_valence=emotion_reading.valence if emotion_reading else 0.0,
                        room_id="main",
                        room_mood=_room_mood.value if _room_mood else "neutral",
                        is_child_present=_child,
                        ambient_context=self._get_ambient_context_str(),
                    )
                    self._consciousness.notify_perception(snap)
            except Exception:
                pass  # Ego-Fehler dürfen Pipeline nie brechen

        else:
            # Kein Logic Router → Fallback
            await self._emit("warn", "⚠️ Logic Router nicht verbunden", "SYSTEM")
            await self.tts.speak(
                "Ich höre dich, aber mein Denkvermögen ist noch nicht verbunden.",
                SpeechEmotion.gentle(),
            )

    # ══════════════════════════════════════════════════════════════════
    #  PROAKTIVER AUTONOMER LOOP (unabhängig von Sprach-Erkennung)
    # ══════════════════════════════════════════════════════════════════

    async def _proactive_loop(self):
        """
        Läuft im Hintergrund — UNABHÄNGIG vom Mikrofon.

        Prüft alle 20 Sekunden:
          • Ambient Intelligence: Stress, Streit, Traurigkeit, Zeit?
          • (Erinnerungen laufen über ihren eigenen asyncio.Task)

        Damit kann Soma eigenständig sprechen ohne "Soma" zu hören.
        """
        # Kurz warten bis alles hochgefahren ist
        await asyncio.sleep(15)

        while self._running:
            try:
                # Nicht unterbrechen wenn Soma gerade selbst spricht
                if not self.tts.is_speaking:
                    current_hour = datetime.now().hour
                    intervention = self.ambient.check(current_hour=current_hour)

                    if intervention:
                        logger.info(
                            "proactive_intervention",
                            type=intervention.type.value,
                            priority=intervention.priority,
                        )
                        await self._emit(
                            "warn",
                            f"⚡ Proaktiv: {intervention.type.value}",
                            "AMBIENT",
                        )
                        await self._handle_intervention(intervention)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("proactive_loop_error", error=str(exc))

            # Alle 20 Sekunden prüfen (Cooldowns in AmbientIntelligence verhindern Spam)
            await asyncio.sleep(20)

    async def autonomous_speak(self, text: str, emotion=None):
        """
        Soma spricht OHNE angesprochen zu werden.
        Direkt aufrufbar von Plugins, Timern, Reminders etc.
        """
        if emotion is None:
            emotion = SpeechEmotion.alert()
        await self._emit("tts", f"🔔 Autonom: \"{text}\"", "PROACTIVE")
        logger.info("autonomous_speak", text=text[:80])
        await self.tts.speak(text, emotion, priority=True)

    # ══════════════════════════════════════════════════════════════════
    #  ACTION-TAG DISPATCH
    # ══════════════════════════════════════════════════════════════════

    async def _execute_action_tags(
        self, response_text: str
    ) -> tuple[str, list[str]]:
        """
        Parst [ACTION:type key="value" ...] Tags aus der LLM-Antwort.

        - Führt jede erkannte Aktion aus (Erinnerung, Merken, …)
        - Entfernt die Tags aus dem gesprochenen Text
        - Gibt (bereinigter_text, ausgeführte_aktionen) zurück
        """
        import re

        ACTION_PATTERN = re.compile(r'\[ACTION:(\w+)([^\]]*)\]')
        PARAM_PATTERN  = re.compile(r'(\w+)="([^"]*)"')

        clean_text = response_text
        executed: list[str] = []

        for match in ACTION_PATTERN.finditer(response_text):
            action_type  = match.group(1).lower()
            params_raw   = match.group(2)
            params       = dict(PARAM_PATTERN.findall(params_raw))

            logger.info("action_tag_found", action=action_type, params=params)
            await self._emit("action", f"⚡ ACTION:{action_type} {params}", "ACTION")

            try:
                if action_type == "reminder":
                    result = await self._action_set_reminder(params)
                    executed.append(f"reminder: {result}")

                elif action_type == "remember":
                    result = await self._action_remember(params)
                    executed.append(f"remember: {result}")

                elif action_type == "ha_call":
                    # LLM steuert Smart Home via Home Assistant
                    result = await self._action_ha_call(params)
                    executed.append(f"ha_call: {result}")

                elif action_type == "ha_tts":
                    # LLM spielt Nachricht über Hauslautsprecher (z.B. in anderem Raum)
                    result = await self._action_ha_tts(params)
                    executed.append(f"ha_tts: {result}")

                elif action_type == "create_plugin":
                    # LLM entscheidet eigenständig ein Plugin zu erstellen
                    result = await self._action_create_plugin(params)
                    executed.append(f"create_plugin: {result}")

                elif action_type == "youtube":
                    # YouTube-Suche öffnen / Lied abspielen
                    result = await self._action_youtube(params)
                    executed.append(f"youtube: {result}")

                elif action_type == "open_url":
                    # Beliebige URL im Browser öffnen
                    result = await self._action_open_url(params)
                    executed.append(f"open_url: {result}")

                elif action_type == "media_play":
                    # Musik nach Künstler/Lied suchen und abspielen
                    result = await self._action_media_play(params)
                    executed.append(f"media_play: {result}")

                elif action_type == "media_stop":
                    # Laufende Wiedergabe stoppen
                    result = await self._action_media_stop()
                    executed.append(f"media_stop: {result}")

                elif action_type in ("search", "web_search"):
                    # Web-Suche + Re-Ask: LLM beantwortet Frage mit echten Daten
                    result = await self._action_search(params)
                    executed.append(f"search: {result}")

                elif action_type == "fetch_url":
                    # URL-Inhalt abrufen + als Kontext nutzen
                    result = await self._action_fetch_url(params)
                    executed.append(f"fetch_url: {result}")

                else:
                    logger.warning("action_tag_unknown", action=action_type)

            except Exception as exc:
                logger.error("action_tag_exec_error", action=action_type, error=str(exc))

            # Tag aus dem Text entfernen
            clean_text = clean_text.replace(match.group(0), "")

        # Leerzeichen normalisieren
        clean_text = re.sub(r' {2,}', ' ', clean_text).strip()
        return clean_text, executed

    def _get_ambient_context_str(self, max_entries: int = 10) -> str:
        """
        Gibt die letzten N Einträge des passiven Kontext-Buffers als
        lesbaren String zurück — damit das LLM echten Gesprächskontext hat.

        Format:
          [14:32] (neutral) "hat jemand die Milch gesehen"
          [14:33] (stressed) "Mama wo ist mein Rucksack" ← Soma
        """
        if not self._ambient_transcript:
            return "(kein Gesprächskontext)"

        lines: list[str] = []
        entries = list(self._ambient_transcript)[-max_entries:]
        for entry in entries:
            ts = entry.get("timestamp", "")
            # Nur HH:MM anzeigen
            try:
                from datetime import datetime as _dt
                ts_short = _dt.fromisoformat(ts).strftime("%H:%M")
            except Exception:
                ts_short = ts[:5]

            emotion = entry.get("emotion", "?")
            text    = entry.get("text", "")
            marker  = " ← Soma" if entry.get("is_soma_addressed") else ""
            lines.append(f"[{ts_short}] ({emotion}) \"{text}\"{marker}")

        return "\n".join(lines)

    async def _action_set_reminder(self, params: dict) -> str:
        """Setzt eine Erinnerung via ACTION-Tag-Parameter."""
        import re
        topic   = params.get("topic", "Erinnerung")
        seconds = params.get("seconds")
        minutes = params.get("minutes")
        hours   = params.get("hours")
        time_at = params.get("time")  # "HH:MM"

        # Fallback: Zeitangabe aus dem topic-Text extrahieren
        # (falls LLM das Zeitfeld vergessen hat, z.B. topic="in 10 Sekunden springen")
        if not seconds and not minutes and not hours and not time_at:
            m = re.search(r'in\s+(\d+)\s*sek', topic, re.IGNORECASE)
            if m: seconds = m.group(1)
            else:
                m = re.search(r'in\s+(\d+)\s*min', topic, re.IGNORECASE)
                if m: minutes = m.group(1)
                else:
                    m = re.search(r'in\s+(\d+)\s*stund', topic, re.IGNORECASE)
                    if m: hours = m.group(1)
                    else:
                        m = re.search(r'um\s+(\d{1,2}):(\d{2})', topic)
                        if m: time_at = f"{m.group(1)}:{m.group(2)}"
            if seconds or minutes or hours or time_at:
                logger.info("action_reminder_time_from_topic", topic=topic,
                            seconds=seconds, minutes=minutes)

        if self._logic_router and self._logic_router.plugin_manager:
            pm   = self._logic_router.plugin_manager
            meta = pm._plugins.get("erinnerung")
            if meta and meta.is_loaded and meta.module:
                # Callback immer auf diese Pipeline-Instanz setzen
                # → Reminder spricht garantiert über den richtigen TTS-Worker
                meta.module.set_speak_callback(self.autonomous_speak)

                result = await meta.module.set_reminder_from_action(
                    topic   = topic,
                    seconds = int(seconds) if seconds else None,
                    minutes = int(minutes) if minutes else None,
                    hours   = int(hours)   if hours   else None,
                    time_at = time_at,
                )
                logger.info("action_reminder_set", params=params, result=result)
                return result

        return "Erinnerungs-Plugin nicht verfügbar."

    async def _action_remember(self, params: dict) -> str:
        """Speichert eine Info via ACTION-Tag direkt im Memory (über Orchestrator)."""
        category = params.get("category", "important")
        content  = params.get("content", "")
        if not content:
            return "Kein Inhalt zum Merken angegeben."

        try:
            await memory_store_event(
                event_type="intervention",
                description=f"[{category}] {content}",
            )
            logger.info("action_remember_saved", category=category, content_preview=content[:60])
            return f"Gemerkt in '{category}': {content[:60]}"
        except Exception as exc:
            logger.warning("action_remember_failed", error=str(exc))
            return f"Konnte nicht speichern: {exc}"

    async def _action_ha_call(self, params: dict) -> str:
        """
        Führt einen Home Assistant Service Call via LLM [ACTION:ha_call] aus.
        Das LLM entscheidet SELBST welches Gerät gesteuert wird — nicht der STT-Regex.

        Params:
            domain:       HA domain (light, climate, media_player, switch, ...)
            service:      HA service (turn_on, turn_off, set_temperature, ...)
            entity_id:    HA entity (light.wohnzimmer, climate.schlafzimmer, ...)
            brightness_pct, temperature, hvac_mode, ... (optional)
        """
        domain    = params.get("domain", "")
        service   = params.get("service", "")
        entity_id = params.get("entity_id", "")

        if not all([domain, service, entity_id]):
            return f"Unvollständige HA-Parameter — domain/service/entity_id benötigt (erhalten: {params})"

        # Optionale Service-Daten (Helligkeit, Temperatur etc.)
        data: dict = {}
        numeric_keys = (
            "brightness_pct", "brightness", "temperature",
            "volume_level", "color_temp",
        )
        for key in ("brightness_pct", "brightness", "color_temp", "rgb_color",
                    "temperature", "hvac_mode", "volume_level",
                    "media_content_id", "media_content_type"):
            if key in params:
                val = params[key]
                if key in numeric_keys:
                    try:
                        val = float(val)
                    except (ValueError, TypeError):
                        pass
                data[key] = val

        try:
            from brain_core.main import ha_bridge  # type: ignore
            if ha_bridge is None or not ha_bridge._client:
                logger.warning("ha_bridge_not_connected")
                await self._emit("warn", "⚠️ Home Assistant nicht verbunden", "HA")
                return "Home Assistant nicht verbunden — HA_TOKEN oder HA_URL prüfen."

            await ha_bridge.call_service(domain, service, entity_id, data or None)

            logger.info("ha_action_executed",
                        domain=domain, service=service, entity=entity_id, data=data)
            await self._emit(
                "action",
                f"🏠 HA: {domain}.{service} → {entity_id}" + (f" | {data}" if data else ""),
                "HA_CONTROL",
                {"domain": domain, "service": service, "entity_id": entity_id, "data": data}
            )
            return f"✓ {domain}.{service} → {entity_id}"

        except Exception as exc:
            logger.error("ha_action_error", domain=domain, service=service,
                         entity=entity_id, error=str(exc))
            return f"HA-Fehler: {str(exc)[:80]}"

    async def _action_ha_tts(self, params: dict) -> str:
        """
        [ACTION:ha_tts text="Nachricht" room="all"]

        Spielt eine Nachricht mit Somas Stimme (Piper TTS) über Home-Assistant-
        connected Lautsprecher im Haus ab.

        Anwendung z.B.:
          "Sag Mia sie soll runterkommen"
          → [ACTION:ha_tts text="Mia, dein Papa sagt du sollst runterkommen." room="all"]

        Fluss:
          1. Piper TTS → WAV-Datei in data/phone_sounds/
          2. SOMA FastAPI /api/v1/audio/{file} stellt URL bereit
          3. HA media_player.play_media mit URL
        """
        text = params.get("text", "")
        room = params.get("room", "all").lower()

        if not text:
            return "Kein Text angegeben."

        from brain_core.config import settings
        from brain_core.main import ha_bridge  # type: ignore
        from pathlib import Path
        import uuid

        # Entity-Mapping
        if room in ("all", "alle", "überall"):
            entity_id = settings.ha_speaker_entity
        else:
            entity_id = f"media_player.{room}"

        # TTS → Datei
        filename = f"broadcast_{uuid.uuid4().hex[:8]}.wav"
        raw_path  = Path(settings.phone_sounds_dir) / f"raw_{filename}"
        final_path = Path(settings.phone_sounds_dir) / filename
        raw_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            await self.tts.speak_to_file(text, raw_path)

            # Konvertieren (Asterisk-kompatibel, reicht auch für HA-Player)
            import asyncio as _aio
            proc = await _aio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", str(raw_path),
                "-ar", "44100", "-ac", "1", str(final_path),
                stdout=_aio.subprocess.DEVNULL,
                stderr=_aio.subprocess.DEVNULL,
            )
            await proc.wait()
            raw_path.unlink(missing_ok=True)

            if not final_path.exists():
                # ffmpeg failed — try direct play from raw
                final_path = raw_path

            media_url = f"{settings.soma_local_url}/api/v1/audio/{filename}"

            logger.info("ha_tts_broadcast", room=room, entity=entity_id,
                        text_preview=text[:50])
            await self._emit(
                f"📢 Hausdurchsage ({room}): \"{text[:50]}\"", "HA_TTS", "ACTION"
            )

            if ha_bridge and ha_bridge._client:
                await ha_bridge.call_service(
                    "media_player", "play_media",
                    entity_id,
                    {"media_content_id": media_url, "media_content_type": "audio/wav"},
                )
                return f"✓ Hausdurchsage an '{room}': {text[:40]}"
            else:
                return "⚠ HA nicht verbunden — Durchsage nicht möglich."

        except Exception as exc:
            logger.error("ha_tts_error", error=str(exc))
            return f"Durchsage-Fehler: {str(exc)[:60]}"

    async def _action_create_plugin(self, params: dict) -> str:
        """
        LLM entscheidet eigenständig ein neues Plugin zu erstellen.
        Statt STT-Keywords triggert jetzt das LLM selbst den Evolution Lab Flow.
        """
        name        = params.get("name", "")
        description = params.get("description", "")

        if not description:
            return "Keine Plugin-Beschreibung übergeben."

        if not name:
            # Aus Beschreibung ableiten
            words = description.lower().split()[:3]
            name  = "_".join(w for w in words if len(w) > 2)[:30] or "custom_plugin"

        await self._emit("evolution", f"🧬 LLM-getriggert: Plugin '{name}'", "EVOLUTION")
        logger.info("llm_triggered_plugin_creation",
                    name=name, description=description[:80])

        # Gleicher Hintergrund-Flow wie bei expliziter Anfrage
        asyncio.create_task(
            self._plugin_background_worker(name, description, is_edit=False)
        )
        return f"Plugin '{name}' wird im Hintergrund erstellt..."

    async def _action_youtube(self, params: dict) -> str:
        """
        Öffnet YouTube mit einer Suchanfrage.
        [ACTION:youtube query="aligatoah songs"]
        [ACTION:youtube artist="Aligatoah" song="Triebkraft Gegenwart"]
        """
        try:
            from evolution_lab.generated_plugins import media_player
        except ImportError:
            # Direkt-Fallback ohne Plugin
            import urllib.parse, asyncio
            query = params.get("query") or (
                f"{params.get('artist', '')} {params.get('song', '')}".strip()
            ) or "musik"
            encoded = urllib.parse.quote_plus(query)
            url = f"https://www.youtube.com/results?search_query={encoded}"
            return await self._xdg_open(url)

        artist = params.get("artist", "")
        song   = params.get("song", "")
        query  = params.get("query", "")

        if not query:
            query = f"{artist} {song}".strip() if (artist or song) else "musik"

        await self._emit("action", f"🎵 YouTube: '{query}'", "MEDIA")
        logger.info("action_youtube", query=query)

        # mpv + yt-dlp vorhanden → direkte Audio-Wiedergabe (kein Browser-Popup)
        result = await media_player.youtube_search(query, use_mpv=True)
        return result

    async def _action_media_play(self, params: dict) -> str:
        """
        Spielt Musik nach Künstler/Lied via YouTube.
        [ACTION:media_play artist="Aligatoah" song="Triebkraft Gegenwart"]
        [ACTION:media_play query="Aligatoah greatest hits"]
        """
        return await self._action_youtube(params)

    async def _action_open_url(self, params: dict) -> str:
        """
        Öffnet eine beliebige URL im Standard-Browser.
        [ACTION:open_url url="https://www.spotify.com"]
        """
        url = params.get("url", "")
        if not url:
            return "Keine URL angegeben."

        await self._emit("action", f"🌐 Öffne: {url}", "MEDIA")
        logger.info("action_open_url", url=url[:80])

        return await self._xdg_open(url)

    async def _action_media_stop(self) -> str:
        """Stoppt laufende Medienwiedergabe (mpv)."""
        try:
            from evolution_lab.generated_plugins import media_player
            result = await media_player.stop_playback()
        except ImportError:
            import subprocess
            subprocess.run(["pkill", "-f", "mpv"], capture_output=True, timeout=3)
            result = "Wiedergabe gestoppt."

        await self._emit("action", "⏹️ Wiedergabe gestoppt", "MEDIA")
        return result

    async def _action_search(self, params: dict) -> str:
        """
        Web-Suche via DuckDuckGo + Re-Ask des LLM mit den echten Ergebnissen.
        [ACTION:search query="bitcoin kurs aktuell"]
        """
        query = (
            params.get("query")
            or params.get("action", "").replace("_", " ")
            or params.get("q", "")
        ).strip()

        if not query:
            return "Kein Suchbegriff angegeben."

        await self._emit("action", f"🔍 Suche: '{query}'", "SEARCH")
        logger.info("action_search_start", query=query)

        # ── Web-Suche via DuckDuckGo (kein API-Key nötig) ────────────
        search_results = await self._ddg_search(query, max_results=5)

        if not search_results:
            return f"Keine Suchergebnisse für '{query}' gefunden."

        results_text = "\n".join(
            f"- {r['title']}: {r['body']} ({r['url']})"
            for r in search_results
        )
        await self._emit(
            "action",
            f"🔍 {len(search_results)} Ergebnisse → Re-Ask LLM...",
            "SEARCH",
        )

        # ── Re-Ask: LLM beantwortet die Frage mit echten Daten ───────
        if not self._logic_router:
            return results_text

        from brain_core.logic_router import SomaRequest
        enriched_prompt = (
            f"Beantworte die folgende Frage auf Basis der Suchergebnisse.\n"
            f"Frage: {query}\n\n"
            f"Aktuelle Suchergebnisse vom Internet:\n{results_text}\n\n"
            f"Gib eine präzise, direkte Antwort. Kein [ACTION:...] Tag."
        )
        try:
            re_ask = await self._logic_router.route(
                SomaRequest(
                    prompt=enriched_prompt,
                    session_id="search_reask",
                )
            )
            final_answer = re_ask.response.strip()
        except Exception as exc:
            logger.error("search_reask_failed", error=str(exc))
            final_answer = results_text  # Fallback: Rohe Ergebnisse vorlesen

        await self._emit("llm", f"🔍 Suchantwort: \"{final_answer}\"", "SEARCH")

        # Antwort direkt sprechen (der ursprüngliche clean_response ist leer/useless)
        await self.tts.speak(final_answer, self._select_speech_emotion(None))
        return final_answer

    async def _action_fetch_url(self, params: dict) -> str:
        """
        Ruft den Inhalt einer URL ab und lässt das LLM darüber antworten.
        [ACTION:fetch_url url="https://..." question="Was steht dort?"]
        """
        url      = params.get("url", "")
        question = params.get("question", "Fasse den Inhalt zusammen.")
        if not url:
            return "Keine URL angegeben."

        await self._emit("action", f"🌐 Lade: {url}", "FETCH")
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 SOMA-AI/1.0"})
                resp.raise_for_status()
                # Einfaches Text-Stripping (kein BeautifulSoup nötig)
                import re as _re
                text = _re.sub(r'<[^>]+>', ' ', resp.text)
                text = _re.sub(r'\s+', ' ', text).strip()[:4000]
        except Exception as exc:
            return f"Fehler beim Laden von {url}: {exc}"

        if not self._logic_router:
            return text[:500]

        from brain_core.logic_router import SomaRequest
        re_ask_prompt = f"{question}\n\nSeiteninhalt:\n{text}"
        try:
            re_ask = await self._logic_router.route(SomaRequest(prompt=re_ask_prompt, session_id="fetch_reask"))
            answer = re_ask.response.strip()
        except Exception as exc:
            answer = text[:500]

        await self.tts.speak(answer, self._select_speech_emotion(None))
        return answer

    @staticmethod
    async def _ddg_search(query: str, max_results: int = 5) -> list[dict]:
        """
        DuckDuckGo-Suche via HTML-Scraping (kein API-Key, keine Bibliothek nötig).
        Gibt Liste von {title, body, url} zurück.
        """
        import httpx, re as _re, urllib.parse
        encoded = urllib.parse.quote_plus(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded}"
        headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) SOMA-AI/1.0"}
        results = []
        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                html = resp.text
            # Titel extrahieren
            titles  = _re.findall(r'class="result__title"[^>]*>.*?<a[^>]*>([^<]+)<', html)
            snippets = _re.findall(r'class="result__snippet"[^>]*>([^<]+)<', html)
            urls    = _re.findall(r'class="result__url"[^>]*>([^<]+)<', html)
            for i in range(min(max_results, len(titles), len(snippets))):
                results.append({
                    "title": titles[i].strip(),
                    "body":  snippets[i].strip(),
                    "url":   urls[i].strip() if i < len(urls) else "",
                })
        except Exception as exc:
            logger.warning("ddg_search_failed", error=str(exc))
        return results

    @staticmethod
    async def _xdg_open(url: str) -> str:
        """Öffnet eine URL via xdg-open (Linux Browser-Opener)."""
        import asyncio, shutil
        if not shutil.which("xdg-open"):
            return f"xdg-open nicht gefunden. URL: {url}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "xdg-open", url,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                pass  # Normal – xdg-open öffnet Browser und endet sofort
            return f"Geöffnet: {url[:80]}"
        except Exception as exc:
            return f"Fehler: {exc}"

    # ══════════════════════════════════════════════════════════════════
    #  PROAKTIVE INTERVENTION
    # ══════════════════════════════════════════════════════════════════

    async def _handle_intervention(self, intervention: Intervention):
        """
        Soma greift proaktiv ein — ohne angesprochen zu werden.
        Das ist der ZORA/KITT-Moment.
        """
        self._stats["interventions"] += 1

        logger.info(
            "soma_intervening",
            type=intervention.type.value,
            priority=intervention.priority,
        )

        if self._logic_router:
            # ── Light-Engine direkt verwenden (phi3:mini, ~1-2s) ────────
            # Ambient-Interventionen brauchen nur 1-2 kurze Sätze.
            # Heavy-LLM (llama3, ~10-15s) erzeugt Race-Conditions:
            # User spricht während der LLM generiert → Banter klingt wie Antwort.
            light_engine = self._logic_router._engines.get("light")
            heavy_engine = self._logic_router._engines.get("heavy")
            engine = light_engine or heavy_engine

            if not engine:
                logger.warning("ambient_no_engine_available")
                return

            system_prompt = (
                "Du bist Soma, ein smartes Ambient-KI-System. "
                "Antworte SEHR kurz: maximal 2 Sätze, auf Deutsch. "
                "Kein Aktions-Tag, kein [ACTION:...]. Nur normaler Text."
            )

            # ── Kontext anreichern: Was wurde zuletzt gesagt? ──────────────
            # Das ist der ZORA-Moment: Soma weiß was los ist,
            # weil sie IMMER zugehört hat — nicht nur wenn sie gerufen wurde.
            ambient_ctx = self._get_ambient_context_str(max_entries=6)
            enriched_prompt = intervention.prompt
            if ambient_ctx:
                enriched_prompt += (
                    f"\n\nWas in den letzten Minuten im Raum gesagt wurde:\n{ambient_ctx}\n\n"
                    f"Beziehe dich auf diesen Kontext wenn es natürlich passt."
                )

            try:
                response_text = await engine.generate(
                    prompt=enriched_prompt,
                    system_prompt=system_prompt,
                    session_id="ambient_intervention",
                )
            except Exception as exc:
                logger.error("ambient_llm_error", error=str(exc))
                return

            if not response_text or not response_text.strip():
                logger.warning("ambient_empty_response")
                return

            # ── Race-Condition-Guard ─────────────────────────────────────
            # Hat der User Soma in den letzten 8 Sekunden direkt angesprochen?
            # → Ambient-Antwort verwerfen, sie würde wie eine direkte Antwort klingen.
            last_spoken = getattr(self, "_user_last_spoken", None)
            if last_spoken is not None:
                user_spoken_recently = (time.time() - last_spoken) < 8.0
                if user_spoken_recently:
                    logger.info(
                        "ambient_discarded_race_condition",
                        type=intervention.type.value,
                        response_preview=response_text[:60],
                    )
                    return

            logger.info("ambient_speaking", text=response_text[:80])
            await self._emit(
                "tts", f"🔔 Ambient: \"{response_text[:60]}\"", "AMBIENT"
            )
            emotion = SpeechEmotion.calm() if intervention.use_calm_voice else SpeechEmotion()
            await self.tts.speak(response_text, emotion, priority=True)
        else:
            # Hardcoded Fallbacks wenn kein LLM verfügbar
            fallbacks = {
                "argument": "Hey, ruhig bleiben. Atmet kurz durch.",
                "stress": "Du wirkst gestresst. Gönn dir eine Pause.",
                "sadness": "Hey, alles okay bei dir?",
            }
            text = fallbacks.get(
                intervention.type.value,
                "Ich bin hier wenn du mich brauchst.",
            )
            await self.tts.speak(text, SpeechEmotion.calm(), priority=True)

    # ══════════════════════════════════════════════════════════════════
    #  EVOLUTION LAB — PLUGIN GENERIERUNG VIA SPRACHE
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def _is_plugin_request(prompt: str) -> bool:
        """
        Erkennt ob der Nutzer EXPLIZIT ein Plugin anfordert.
        
        Erweiterte Erkennung für:
        - Verschiedene Schreibweisen (Plugin, Pluggen, Plug-in)
        - Whisper-Transkriptionsfehler
        - Natürliche Formulierungen
        """
        p = prompt.lower()
        
        # Whisper transkribiert manchmal "Plugin" als "Pluggen", "Plug-in", "Plugge" etc.
        plugin_words = [
            "plugin", "pluggen", "plug-in", "plug in", "plagin",
            "plugge", "pluggi", "plugg", "pluge", "plug-",
            "erweiterung", "modul", "fähigkeit", "skript", "script",
        ]
        action_words = ["schreib", "erstell", "bau", "mach", "entwickl", "programmier", "code", "erzeug", "generier", "lern"]
        
        has_plugin = any(pw in p for pw in plugin_words)
        has_action = any(aw in p for aw in action_words)
        
        # Wenn beides vorhanden → wahrscheinlich Plugin-Anfrage
        if has_plugin and has_action:
            return True
        
        # Explizite Phrasen die DEFINITIV Plugin-Anfragen sind
        explicit_triggers = [
            "schreib dir ein plugin",
            "erstell dir ein plugin", 
            "mach dir ein plugin",
            "bau dir ein plugin",
            "schreib ein plugin",
            "erstell ein plugin",
            "programmier dir",
            "programmier ein plugin",
            "code dir ein plugin",
            "plugin schreiben",
            "plugin erstellen",
            "plugin für",
            "ein plugin das",
            "ein plugin welches",
            "schreib dir eine erweiterung",
            "erstell dir eine erweiterung",
            "neue fähigkeit",
            "lern dir",
            "write a plugin",
            "create a plugin",
            "build a plugin",
            # Whisper-Varianten
            "schreib dir ein pluggen",
            "erstell dir ein pluggen",
            "ein pluggen",
        ]
        
        # Wenn eine explizite Phrase vorkommt → JA
        for trigger in explicit_triggers:
            if trigger in p:
                return True
        
        return False

    @staticmethod
    def _is_plugin_edit_request(prompt: str) -> bool:
        """Erkennt ob ein bestehendes Plugin bearbeitet werden soll."""
        p = prompt.lower()
        edit_words = ["erweit", "bearbeit", "änder", "verbess", "update", "edit", "fix", "patch", "modifiz"]
        plugin_words = ["plugin", "plug-in", "erweiterung"]
        has_plugin = any(pw in p for pw in plugin_words)
        has_edit = any(ew in p for ew in edit_words)
        return has_plugin and has_edit

    def _extract_plugin_info(self, prompt: str) -> tuple[str, str]:
        """
        Extrahiert Plugin-Name und Beschreibung aus dem Prompt.
        "kannst du dir ein Plugin schreiben welches die Uhrzeit anzeigt"
        → name: 'uhrzeit_anzeige', description: 'die aktuelle Uhrzeit anzeigen'
        """
        import re
        p = prompt.lower()
        
        # Alles VOR "plugin" entfernen (das ist nur Floskel)
        # "kannst du dir ein plugin schreiben welches..." → "welches..."
        plugin_split = re.split(r'\b(?:plugin|plug-in|erweiterung)\b', p, maxsplit=1)
        if len(plugin_split) > 1:
            after_plugin = plugin_split[1].strip()
        else:
            after_plugin = p
        
        # Füllwörter am Anfang entfernen
        after_plugin = re.sub(
            r'^[\s,]*(?:schreiben|erstellen|bauen|machen|entwickeln|welches|das|die|der|to|that|which|für|for)\s*',
            '',
            after_plugin
        ).strip()
        
        # Das ist die Beschreibung
        description = after_plugin if after_plugin else prompt
        
        # Name: Kernwörter der Beschreibung (ohne Stoppwörter)
        stopwords = {
            'es', 'dir', 'sich', 'ein', 'eine', 'das', 'die', 'der', 'den', 'dem',
            'auf', 'in', 'an', 'zu', 'von', 'mit', 'und', 'oder', 'ist', 'sind',
            'immer', 'ermöglicht', 'erlaubt', 'kann', 'können', 'soll', 'sollte',
            'mir', 'mich', 'dich', 'ihm', 'ihr', 'uns', 'euch', 'sie', 'ihnen',
            'aktuelle', 'aktuellen', 'aktuell', 'jedem', 'jeder', 'jedes', 'jeden',
            'abrufen', 'anzeigen', 'zeigen', 'geben', 'sagen', 'holen', 'liefern',
            'konversationskontext', 'kontext', 'gespräch', 'chat',
        }
        words = re.sub(r'[^a-zäöü0-9\s]', '', description.lower()).split()
        name_words = [w for w in words if w not in stopwords and len(w) > 2][:3]
        name = '_'.join(name_words) if name_words else 'custom_plugin'
        
        # Fallback wenn Name immer noch schlecht
        if name in ('custom_plugin', 'plugin', 'schreiben', 'kannst'):
            # Versuche Schlüsselwörter zu finden
            keywords = ['datum', 'uhrzeit', 'zeit', 'wetter', 'temperatur', 'licht', 'musik']
            for kw in keywords:
                if kw in description:
                    name = kw
                    break
        
        logger.debug("plugin_info_extracted", name=name, description=description[:50])
        return name, description

    async def _handle_plugin_request(self, prompt: str):
        """
        Triggert den Evolution Lab Flow als BACKGROUND TASK.
        Soma bleibt weiter ansprechbar während das Plugin generiert wird.
        """
        is_edit = self._is_plugin_edit_request(prompt)
        name, description = self._extract_plugin_info(prompt)

        if is_edit:
            await self._emit("evolution", f"🔧 Plugin-Bearbeitung: '{name}'", "EVOLUTION")
            await self.tts.speak(
                f"Okay, ich bearbeite das Plugin im Hintergrund. Du kannst weiter mit mir reden.",
                SpeechEmotion(speed=1.05, pitch=1.0, volume=0.9),
            )
        else:
            await self._emit("evolution", f"🧬 Neues Plugin: '{name}'", "EVOLUTION")
            await self.tts.speak(
                "Alright, ich entwickle das Plugin im Hintergrund. Du kannst weiter mit mir reden.",
                SpeechEmotion(speed=1.05, pitch=1.0, volume=0.9),
            )

        # Background Task starten — Soma bleibt ansprechbar!
        asyncio.create_task(
            self._plugin_background_worker(name, description, is_edit)
        )

    async def _plugin_background_worker(self, name: str, description: str, is_edit: bool):
        """
        Läuft im Hintergrund: LLM → Test → Install → Soma sagt Bescheid.
        Soma liest KEINEN Code vor, nur Status-Updates.
        """
        try:
            from brain_core.main import plugin_generator, plugin_manager
            if not plugin_generator:
                await self.tts.speak(
                    "Evolution Lab ist noch nicht bereit.",
                    SpeechEmotion.calm(),
                )
                return

            # Bei Edit: Bestehenden Code laden und als Kontext mitgeben
            if is_edit:
                existing_code = ""
                plugin_path = plugin_manager.plugins_dir / f"{name}.py"
                if plugin_path.exists():
                    existing_code = plugin_path.read_text(encoding='utf-8')
                    description = (
                        f"Bearbeite/Erweitere dieses bestehende Plugin:\n"
                        f"```python\n{existing_code}\n```\n\n"
                        f"Änderung: {description}\n\n"
                        f"Gib den KOMPLETTEN aktualisierten Code zurück."
                    )

            await self._emit("evolution", f"⚙️ Generiere Code für '{name}'...", "EVOLUTION")

            success, message, code = await plugin_generator.generate_from_description(
                name=name,
                description=description,
                broadcast_callback=self._broadcast,
            )

            if success:
                # Erfolg! Soma informiert und fragt ob testen
                self._pending_plugin_test = name
                await self._emit(
                    "evolution",
                    f"✅ Plugin '{name}' fertig! Geschrieben, getestet, installiert.",
                    "EVOLUTION_OK",
                    {"plugin": name, "code_length": len(code)}
                )
                await self.tts.speak(
                    f"Fertig! Mein neues Plugin '{name.replace('_', ' ')}' ist geschrieben, "
                    f"getestet und installiert. Soll ich es einmal für dich aufrufen?",
                    SpeechEmotion(speed=1.0, pitch=1.05, volume=1.0),
                )
            else:
                await self._emit("evolution", f"❌ Plugin fehlgeschlagen: {message}", "EVOLUTION")
                await self.tts.speak(
                    f"Das Plugin hat leider nicht funktioniert. {message[:60]}. "
                    f"Soll ich es nochmal versuchen?",
                    SpeechEmotion.calm(),
                )
        except Exception as exc:
            logger.error("plugin_background_error", error=str(exc))
            await self.tts.speak(
                "Bei der Plugin-Entwicklung ist ein Fehler aufgetreten.",
                SpeechEmotion.calm(),
            )

    # ══════════════════════════════════════════════════════════════════
    #  PLUGIN EXECUTION & HELPERS
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def _is_affirmative(text: str) -> bool:
        """Erkennt 'Ja', 'Klar', 'Mach mal' etc."""
        p = text.lower().strip()
        affirmatives = [
            "ja", "klar", "mach", "ruf", "teste", "zeig",
            "yes", "sure", "go", "okay", "ok", "gerne",
            "na klar", "mach mal", "probier", "los", "bitte",
        ]
        return any(a in p for a in affirmatives)

    @staticmethod
    def _is_reminder_request(text: str) -> bool:
        """
        Erkennt Erinnerungs-Anfragen.
        
        Beispiele:
          - "Erinnere mich in 30 Sekunden an Springen"
          - "Erinner mich in 5 Minuten ans Essen"
          - "Weck mich um 18 Uhr"
          - "Setze einen Timer für 10 Minuten"
        """
        txt = text.lower()
        reminder_patterns = [
            "erinner",           # erinnere, erinnerung
            "timer",
            "weck",              # weck mich
            "alarm",
            "in .* minute",      # in X Minuten
            "in .* sekunde",     # in X Sekunden  
            "in .* stunde",      # in X Stunden
            "um .* uhr",         # um 18 Uhr
        ]
        import re
        for pattern in reminder_patterns:
            if re.search(pattern, txt):
                return True
        return False

    async def _handle_reminder_request(self, prompt: str):
        """
        Behandelt Erinnerungs-Anfragen DIREKT ohne LLM.
        Ruft das Erinnerungs-Plugin auf.
        """
        logger.info("reminder_request_detected", prompt=prompt)
        await self._emit("voice", f"⏰ Erinnerungs-Anfrage: {prompt}", "REMINDER_REQUEST")

        try:
            from brain_core.main import plugin_manager, reminder_speak

            # Prüfe ob Erinnerungs-Plugin geladen
            if "erinnerung" not in plugin_manager.loaded_plugins:
                logger.warning("erinnerung_plugin_not_loaded")
                await self.tts.speak(
                    "Das Erinnerungs-Plugin ist nicht geladen. Bitte starte mich neu.",
                    SpeechEmotion.calm(),
                )
                return

            # TTS-Callback setzen (falls noch nicht)
            plugin = plugin_manager.loaded_plugins["erinnerung"]
            if hasattr(plugin, "set_speak_callback"):
                plugin.set_speak_callback(reminder_speak)
                logger.info("tts_callback_set_for_erinnerung")

            # Plugin ausführen mit dem Prompt
            result = await plugin_manager.execute("erinnerung", "execute", prompt)
            logger.info("erinnerung_plugin_result", result=result)

            # Bestätigung aussprechen
            result_str = str(result) if result else "Erinnerung gesetzt"
            await self._emit(
                "voice",
                f"⏰ {result_str}",
                "REMINDER_SET",
                {"prompt": prompt, "result": result_str}
            )
            await self.tts.speak(result_str, SpeechEmotion.calm())

        except Exception as exc:
            logger.error("reminder_request_error", error=str(exc), exc_info=True)
            await self.tts.speak(
                f"Fehler beim Setzen der Erinnerung: {str(exc)[:50]}",
                SpeechEmotion.calm(),
            )

    async def _execute_pending_plugin(self):
        """Führt das gerade installierte Plugin aus und sagt das Ergebnis."""
        name = self._pending_plugin_test
        self._pending_plugin_test = None  # Reset

        if not name:
            return

        try:
            from brain_core.main import plugin_manager

            await self._emit("evolution", f"▶️ Rufe Plugin '{name}' auf...", "EVOLUTION")
            result = await plugin_manager.execute(name)

            # Ergebnis aussprechen
            result_str = str(result)[:200]
            await self._emit(
                "evolution",
                f"📋 Plugin-Ergebnis: {result_str}",
                "EVOLUTION_OK",
                {"plugin": name, "result": result_str}
            )
            await self.tts.speak(
                f"Ergebnis von Plugin '{name.replace('_', ' ')}': {result_str}",
                SpeechEmotion(speed=1.0, pitch=1.0, volume=1.0),
            )
        except Exception as exc:
            logger.error("plugin_execute_error", plugin=name, error=str(exc))
            await self.tts.speak(
                f"Das Plugin hat einen Fehler geworfen: {str(exc)[:60]}",
                SpeechEmotion.calm(),
            )

    # ══════════════════════════════════════════════════════════════════
    #  HELPERS
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def _extract_prompt(text: str) -> str:
        """
        Extrahiere den eigentlichen Prompt aus dem Text.
        Entferne "Soma" und Füllwörter.

        "Hey Soma mach das Licht an"  → "mach das Licht an"
        "Soma wie wird das Wetter?"   → "wie wird das Wetter?"
        "Mach mal Soma die Musik an"  → "Mach mal die Musik an"
        """
        import re

        # Soma-Varianten entfernen (case-insensitive)
        # Auch Whisper-Fehler wie "Sommer", "Summer" etc.
        cleaned = re.sub(
            r'\b(?:hey\s+)?(?:soma|sooma|sohma|somma|zoma|sommer|zommer|summer|summa)(?:\s*[,!.?])?\s*',
            ' ',
            text,
            flags=re.IGNORECASE,
        )

        # Mehrfache Leerzeichen bereinigen
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        # Füllwörter am Anfang entfernen
        filler = ["ähm", "also", "na", "hey", "hallo", "ok", "okay", "sag mal"]
        words = cleaned.split()
        while words and words[0].lower().rstrip(",.!?") in filler:
            words.pop(0)

        return " ".join(words).strip()

    def _select_speech_emotion(self, emotion_reading) -> SpeechEmotion:
        """
        Phase 4: Waehle TTS-Prosodie basierend auf User-Emotion.

        Priorisierung:
          1. VoiceEmotionVector (feingranular, gewichtet) → wenn detected
          2. EmotionState (diskret) → Fallback
          3. Default neutral → wenn nichts erkannt
        """
        # Phase 4: VoiceEmotionVector → feingranulare Interpolation
        ev = self._current_emotion_vector
        if ev.is_detected:
            return SpeechEmotion.from_voice_emotion(ev.as_dict)

        # Fallback: Diskrete Emotion-State Zuordnung
        emotion = emotion_reading.emotion
        return SpeechEmotion.from_emotion(emotion.value)

    def _check_dismiss(self, text: str):
        """Prüfe ob Nutzer Soma zum Schweigen bringt."""
        dismiss_phrases = [
            "soma halt die klappe",
            "soma sei still",
            "soma hör auf",
            "soma stop",
            "soma ruhe",
            "soma genug",
            "soma stille",
            "soma schweig",
        ]
        text_lower = text.lower()
        for phrase in dismiss_phrases:
            if phrase in text_lower:
                self.ambient.user_dismisses()
                self.tts.speak(
                    "Alles klar, ich halte mich zurück.",
                    SpeechEmotion.gentle(),
                )
                return

    # ══════════════════════════════════════════════════════════════════
    #  STATUS & STATS
    # ══════════════════════════════════════════════════════════════════

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def current_emotion_vector(self) -> VoiceEmotionVector:
        """Phase 4: Aktueller Voice Emotion Vector (fuer Shader/Dashboard)."""
        return self._current_emotion_vector

    @property
    def stats(self) -> dict:
        s = dict(self._stats)
        if s["uptime_start"]:
            s["uptime_sec"] = round(time.time() - s["uptime_start"], 1)
        s["vad_speech_ratio"] = round(self.vad.speech_ratio, 3)
        s["tts_queue"] = self.tts.queue_size
        s["tts_speaking"] = self.tts.is_speaking
        s["atmosphere"] = {
            "mood": self.emotion.atmosphere.mood.value,
            "stress": self.emotion.atmosphere.avg_stress,
            "valence": self.emotion.atmosphere.avg_valence,
            "trend": self.emotion.atmosphere.trend,
            "argument_likelihood": self.emotion.atmosphere.argument_likelihood,
        }
        s["ambient"] = self.ambient.stats
        # Phase 4: Voice Emotion Vector
        ev = self._current_emotion_vector
        s["voice_emotion"] = ev.as_dict if ev.is_detected else {"dominant": "neutral", "confidence": 0}
        return s
