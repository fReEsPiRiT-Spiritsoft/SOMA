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
from brain_core.memory import get_memory, MemoryCategory

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
        self.ambient = AmbientIntelligence(emotion_engine=self.emotion)
        self.memory = get_memory()  # Persistent Memory System

        # ── External References ─────────────────────────────────────
        self._logic_router = logic_router
        self._audio_device = audio_device
        self._broadcast = broadcast_callback

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
            
            # Dashboard: Emotion Event
            await self._emit(
                "emotion",
                f"Emotion: {emotion_reading.emotion.value} | Stress: {emotion_reading.stress_level:.0%}",
                "VAD",
                {"emotion": emotion_reading.emotion.value, "stress": emotion_reading.stress_level}
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
        
        # ── Memory: Automatisch wichtige Infos speichern ─────────────
        should_save, category, extracted = self.memory.should_remember(prompt)
        if should_save and category and extracted:
            self.memory.remember(extracted, category, source="voice_conversation")
            logger.info("memory_auto_saved", category=category, content_preview=extracted[:50])
            await self._emit("memory", f"💾 Gemerkt: {extracted[:50]}...", "MEMORY")
        
        await self._emit("llm", f"🧠 Denke nach: \"{prompt}\"", "LLM", {"prompt": prompt})

        # ── An Brain Core senden ─────────────────────────────────────
        if self._logic_router:
            from brain_core.logic_router import SomaRequest, SomaResponse

            # Emotionaler Kontext für den System-Prompt
            emotion_context = self.emotion.get_context_for_llm()

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
                },
            )
            
            # Conversation History für Dashboard
            self._conversation_history.append({
                "role": "user",
                "text": prompt,
                "timestamp": datetime.now().isoformat(),
            })

            response = await self._logic_router.route(request)

            # ── ACTION-Tag Dispatch ──────────────────────────────────
            # Parst [ACTION:...] Tags aus der LLM-Antwort, führt sie aus
            # und gibt den bereinigten Text zurück (ohne Tags).
            clean_response, action_log = await self._execute_action_tags(response.response)

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
                f"💬 Antwort ({response.engine_used}): \"{clean_response[:100]}...\"" if len(clean_response) > 100 else f"💬 Antwort ({response.engine_used}): \"{clean_response}\"",
                response.engine_used.upper(),
                {"response": clean_response, "engine": response.engine_used, "latency_ms": response.latency_ms,
                 "actions_executed": action_log}
            )

            # Antwort aussprechen (bereinigt, ohne Tags)
            await self._emit("tts", f"🔊 Spreche: \"{clean_response[:50]}...\"" if len(clean_response) > 50 else f"🔊 Spreche: \"{clean_response}\"", "TTS")
            speech_emotion = self._select_speech_emotion(emotion_reading)
            await self.tts.speak(clean_response, speech_emotion)

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
        await self._emit("tts", f"🔔 Autonom: \"{text[:60]}\"", "PROACTIVE")
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

                else:
                    logger.warning("action_tag_unknown", action=action_type)

            except Exception as exc:
                logger.error("action_tag_exec_error", action=action_type, error=str(exc))

            # Tag aus dem Text entfernen
            clean_text = clean_text.replace(match.group(0), "")

        # Leerzeichen normalisieren
        clean_text = re.sub(r' {2,}', ' ', clean_text).strip()
        return clean_text, executed

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
        """Speichert eine Info via ACTION-Tag direkt im Memory."""
        category = params.get("category", "important")
        content  = params.get("content", "")
        if not content:
            return "Kein Inhalt zum Merken angegeben."

        from brain_core.memory import MemoryCategory
        try:
            cat = MemoryCategory(category)
        except ValueError:
            cat = MemoryCategory.IMPORTANT

        self.memory.remember(content, cat, source="llm_action_tag")
        logger.info("action_remember_saved", category=category, content_preview=content[:60])
        return f"Gemerkt in '{category}': {content[:60]}"

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

            try:
                response_text = await engine.generate(
                    prompt=intervention.prompt,
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
        
        # Whisper transkribiert manchmal "Plugin" als "Pluggen", "Plug-in", etc.
        plugin_words = ["plugin", "pluggen", "plug-in", "plug in", "plagin", "erweiterung", "modul", "fähigkeit"]
        action_words = ["schreib", "erstell", "bau", "mach", "entwickl", "programmier", "code", "erzeug", "generier"]
        
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
        """Wähle TTS-Emotion basierend auf erkannter User-Emotion."""
        emotion = emotion_reading.emotion

        if emotion in (EmotionState.SAD, EmotionState.ANXIOUS):
            return SpeechEmotion.gentle()
        if emotion in (EmotionState.ANGRY, EmotionState.STRESSED):
            return SpeechEmotion.calm()
        if emotion in (EmotionState.HAPPY, EmotionState.EXCITED):
            return SpeechEmotion.energetic()
        return SpeechEmotion()  # Neutral

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
        return s
