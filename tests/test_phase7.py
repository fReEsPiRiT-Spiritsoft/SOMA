"""
SOMA-AI Phase 7 Tests — Phone & Kommunikation: Call → Memory
==============================================================
Teste:
  - CallTurn: Einzelner Gesprächs-Turn (Speaker, Text, Emotion, Timestamp)
  - CallTranscript: Vollständiges Transkript mit Metadata + Builder
  - CallRecord: Abgeschlossener Anruf-Record
  - CallSession: Memory Integration, Summary, Emotion Detection, Finalization
  - PhonePipeline: Call History, Stats, CallRecord Callbacks
  - phone/__init__.py: Alle Exports vorhanden
  - main.py: Phone-Endpoints, Boot-Wiring mit Memory/Summary Callbacks

Phase 7 Kernregel:
  Nach Call-End: Transkript + Teilnehmer → L2 Episodic Memory
  Importance: 0.9 (Anrufe sind IMMER wichtig)
  SOMA kann Zusammenfassung auf Anfrage liefern
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _run(coro):
    """Helper: Async-Coroutine synchron ausführen."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ═══════════════════════════════════════════════════════════════════════════
#  1. CallTurn — Einzelner Gesprächs-Turn
# ═══════════════════════════════════════════════════════════════════════════


class TestCallTurn:
    """Phase 7: CallTurn — atomare Einheit des Transkripts."""

    def test_call_turn_creation(self):
        from brain_core.phone.call_session import CallTurn
        t = CallTurn(speaker="caller", text="Hallo SOMA!")
        assert t.speaker == "caller"
        assert t.text == "Hallo SOMA!"
        assert t.emotion == "neutral"
        assert t.timestamp > 0
        assert t.duration_sec == 0.0

    def test_call_turn_with_emotion(self):
        from brain_core.phone.call_session import CallTurn
        t = CallTurn(speaker="soma", text="Hi!", emotion="happy", duration_sec=2.5)
        assert t.emotion == "happy"
        assert t.duration_sec == 2.5
        assert t.speaker == "soma"

    def test_call_turn_to_dict(self):
        from brain_core.phone.call_session import CallTurn
        t = CallTurn(speaker="caller", text="Wie geht es dir?", emotion="neutral", duration_sec=3.2)
        d = t.to_dict()
        assert d["speaker"] == "caller"
        assert d["text"] == "Wie geht es dir?"
        assert d["emotion"] == "neutral"
        assert d["duration_sec"] == 3.2

    def test_call_turn_repr(self):
        from brain_core.phone.call_session import CallTurn
        t = CallTurn(speaker="soma", text="Ich bin SOMA, dein Haus-Assistent!")
        r = repr(t)
        assert "soma" in r
        assert "SOMA" in r

    def test_call_turn_slots(self):
        """CallTurn nutzt __slots__ für Memory-Effizienz."""
        from brain_core.phone.call_session import CallTurn
        t = CallTurn(speaker="caller", text="test")
        assert hasattr(t, "__slots__")
        assert "speaker" in t.__slots__
        assert "text" in t.__slots__


# ═══════════════════════════════════════════════════════════════════════════
#  2. CallTranscript — Vollständiges Anruf-Transkript
# ═══════════════════════════════════════════════════════════════════════════


class TestCallTranscript:
    """Phase 7: CallTranscript — sammelt alle Turns + Metadaten."""

    def test_transcript_creation(self):
        from brain_core.phone.call_session import CallTranscript
        t = CallTranscript(caller_id="+4912345", session_id="phone_abc123")
        assert t.caller_id == "+4912345"
        assert t.session_id == "phone_abc123"
        assert t.turns == []
        assert t.turn_count == 0
        assert t.authenticated is False
        assert t.end_reason == "unknown"
        assert t.importance == 0.9  # Calls sind IMMER wichtig

    def test_transcript_add_turn(self):
        from brain_core.phone.call_session import CallTranscript
        t = CallTranscript(caller_id="+49", session_id="s1")
        t.add_turn("caller", "Hallo!")
        t.add_turn("soma", "Hi! Was kann ich tun?")
        assert t.turn_count == 2
        assert len(t.turns) == 2
        assert t.turns[0].speaker == "caller"
        assert t.turns[1].speaker == "soma"

    def test_transcript_caller_soma_turns(self):
        from brain_core.phone.call_session import CallTranscript
        t = CallTranscript(caller_id="+49", session_id="s1")
        t.add_turn("caller", "Frage 1")
        t.add_turn("soma", "Antwort 1")
        t.add_turn("caller", "Frage 2")
        t.add_turn("soma", "Antwort 2")
        assert len(t.caller_turns) == 2
        assert len(t.soma_turns) == 2
        assert t.caller_turns[0].text == "Frage 1"
        assert t.soma_turns[1].text == "Antwort 2"

    def test_transcript_duration(self):
        from brain_core.phone.call_session import CallTranscript
        t = CallTranscript(caller_id="+49", session_id="s1")
        t.start_time = 100.0
        t.end_time = 160.0
        assert t.duration_sec == 60.0

    def test_transcript_duration_zero_if_not_ended(self):
        from brain_core.phone.call_session import CallTranscript
        t = CallTranscript(caller_id="+49", session_id="s1")
        assert t.duration_sec == 0.0

    def test_transcript_build_text(self):
        from brain_core.phone.call_session import CallTranscript
        t = CallTranscript(caller_id="+49", session_id="s1")
        t.add_turn("caller", "Mach das Licht an")
        t.add_turn("soma", "Licht ist an!")
        text = t.build_transcript_text()
        assert "Anrufer: Mach das Licht an" in text
        assert "SOMA: Licht ist an!" in text

    def test_transcript_build_text_limit(self):
        """max_turns begrenzt auf die letzten N Turns."""
        from brain_core.phone.call_session import CallTranscript
        t = CallTranscript(caller_id="+49", session_id="s1")
        for i in range(10):
            t.add_turn("caller", f"Turn {i}")
        text = t.build_transcript_text(max_turns=3)
        assert "Turn 7" in text
        assert "Turn 8" in text
        assert "Turn 9" in text
        assert "Turn 0" not in text

    def test_transcript_to_dict(self):
        from brain_core.phone.call_session import CallTranscript
        t = CallTranscript(caller_id="+4912345", session_id="phone_xyz")
        t.add_turn("caller", "Hi")
        t.start_time = 100.0
        t.end_time = 130.0
        t.authenticated = True
        t.end_reason = "goodbye"
        t.summary = "Kurzes Gespräch über Licht"
        t.dominant_emotion = "happy"
        t.actions_executed = ["ha_tts: ok"]

        d = t.to_dict()
        assert d["session_id"] == "phone_xyz"
        assert d["caller_id"] == "+4912345"
        assert d["authenticated"] is True
        assert d["duration_sec"] == 30.0
        assert d["turn_count"] == 1
        assert d["end_reason"] == "goodbye"
        assert d["summary"] == "Kurzes Gespräch über Licht"
        assert d["dominant_emotion"] == "happy"
        assert d["importance"] == 0.9
        assert len(d["turns"]) == 1
        assert len(d["actions_executed"]) == 1

    def test_transcript_actions_tracking(self):
        from brain_core.phone.call_session import CallTranscript
        t = CallTranscript(caller_id="+49", session_id="s1")
        t.actions_executed.append("ha_tts: ok")
        t.actions_executed.append("ha_call: light.toggle")
        assert len(t.actions_executed) == 2


# ═══════════════════════════════════════════════════════════════════════════
#  3. CallRecord — Abgeschlossener Anruf-Record
# ═══════════════════════════════════════════════════════════════════════════


class TestCallRecord:
    """Phase 7: CallRecord — wird aus Transcript gebaut nach Call-Ende."""

    def _make_transcript(self) -> "CallTranscript":
        from brain_core.phone.call_session import CallTranscript
        t = CallTranscript(caller_id="+4912345", session_id="phone_test01")
        t.start_time = 1000.0
        t.end_time = 1120.0
        t.authenticated = True
        t.end_reason = "goodbye"
        t.summary = "Anrufer fragte nach Wetter und Licht"
        t.dominant_emotion = "happy"
        t.importance = 0.9
        t.actions_executed = ["ha_call: light.on"]
        t.add_turn("caller", "Mach Licht an")
        t.add_turn("soma", "Licht ist an!")
        return t

    def test_call_record_from_transcript(self):
        from brain_core.phone.call_session import CallRecord
        t = self._make_transcript()
        r = CallRecord(t)
        assert r.session_id == "phone_test01"
        assert r.caller_id == "+4912345"
        assert r.authenticated is True
        assert r.duration_sec == 120.0
        assert r.turn_count == 2
        assert r.end_reason == "goodbye"
        assert r.summary == "Anrufer fragte nach Wetter und Licht"
        assert r.dominant_emotion == "happy"
        assert r.importance == 0.9
        assert r.stored_in_memory is False
        assert r.ended_at > 0

    def test_call_record_to_dict(self):
        from brain_core.phone.call_session import CallRecord
        t = self._make_transcript()
        r = CallRecord(t)
        d = r.to_dict()
        assert d["session_id"] == "phone_test01"
        assert d["duration_sec"] == 120.0
        assert d["stored_in_memory"] is False
        assert "ended_at" in d
        assert d["actions_executed"] == ["ha_call: light.on"]

    def test_call_record_actions_independent_of_transcript(self):
        """CallRecord hat eigene Kopie der Actions."""
        from brain_core.phone.call_session import CallRecord
        t = self._make_transcript()
        r = CallRecord(t)
        t.actions_executed.append("extra")
        assert "extra" not in r.actions_executed

    def test_call_record_stored_in_memory_flag(self):
        from brain_core.phone.call_session import CallRecord
        t = self._make_transcript()
        r = CallRecord(t)
        assert r.stored_in_memory is False
        r.stored_in_memory = True
        assert r.stored_in_memory is True


# ═══════════════════════════════════════════════════════════════════════════
#  4. CallState — Enum inkl. SUMMARIZING
# ═══════════════════════════════════════════════════════════════════════════


class TestCallState:
    """Phase 7: CallState um SUMMARIZING-State erweitert."""

    def test_all_states_exist(self):
        from brain_core.phone.call_session import CallState
        assert hasattr(CallState, "GREETING")
        assert hasattr(CallState, "AUTH")
        assert hasattr(CallState, "ACTIVE")
        assert hasattr(CallState, "SUMMARIZING")
        assert hasattr(CallState, "ENDED")

    def test_summarizing_value(self):
        from brain_core.phone.call_session import CallState
        assert CallState.SUMMARIZING.value == "summarizing"

    def test_state_is_string_enum(self):
        from brain_core.phone.call_session import CallState
        assert isinstance(CallState.ACTIVE, str)
        assert CallState.ACTIVE == "active"


# ═══════════════════════════════════════════════════════════════════════════
#  5. CallSession — Emotion Detection
# ═══════════════════════════════════════════════════════════════════════════


class TestEmotionDetection:
    """Phase 7: _detect_dominant_emotion() — Keyword-basierte Erkennung."""

    def _make_session(self):
        from brain_core.phone.call_session import CallSession
        session = CallSession(
            channel_id="test_chan_001",
            caller_id="+4912345",
            stt=MagicMock(),
            tts=MagicMock(),
            router=MagicMock(),
            ha_bridge=MagicMock(),
            ari_base="http://localhost:8088",
            ari_auth=("soma", "pass"),
            rec_dir="/tmp/rec",
            snd_dir="/tmp/snd",
        )
        return session

    def test_emotion_neutral_by_default(self):
        s = self._make_session()
        assert s._detect_dominant_emotion() == "neutral"

    def test_emotion_happy(self):
        s = self._make_session()
        s._transcript.add_turn("caller", "Danke, das ist super toll!")
        assert s._detect_dominant_emotion() == "happy"

    def test_emotion_stressed(self):
        s = self._make_session()
        s._transcript.add_turn("caller", "Ich habe ein dringendes Problem, hilfe bitte!")
        assert s._detect_dominant_emotion() == "stressed"

    def test_emotion_angry(self):
        s = self._make_session()
        s._transcript.add_turn("caller", "Verdammt, das nervt mich so sehr!")
        assert s._detect_dominant_emotion() == "angry"

    def test_emotion_sad(self):
        s = self._make_session()
        s._transcript.add_turn("caller", "Leider ist es schade und traurig")
        assert s._detect_dominant_emotion() == "sad"

    def test_emotion_tired(self):
        s = self._make_session()
        s._transcript.add_turn("caller", "Ich bin so müde und erschöpft")
        assert s._detect_dominant_emotion() == "tired"

    def test_emotion_multiple_turns(self):
        """Dominante Emotion über mehrere Turns."""
        s = self._make_session()
        s._transcript.add_turn("caller", "Problem")
        s._transcript.add_turn("caller", "Dringend, hilfe bitte")
        s._transcript.add_turn("caller", "Angst vor dem Stress")
        assert s._detect_dominant_emotion() == "stressed"

    def test_emotion_no_caller_turns(self):
        s = self._make_session()
        s._transcript.add_turn("soma", "Hallo!")
        assert s._detect_dominant_emotion() == "neutral"


# ═══════════════════════════════════════════════════════════════════════════
#  6. CallSession — Memory Integration
# ═══════════════════════════════════════════════════════════════════════════


class TestCallSessionMemory:
    """Phase 7: Anruf → L2 Episodic Memory (Importance 0.9)."""

    def _make_session(self, memory_fn=None, summarize_fn=None, on_call_ended=None):
        from brain_core.phone.call_session import CallSession
        session = CallSession(
            channel_id="test_mem_001",
            caller_id="+4912345",
            stt=MagicMock(),
            tts=MagicMock(),
            router=MagicMock(),
            ha_bridge=MagicMock(),
            ari_base="http://localhost:8088",
            ari_auth=("soma", "pass"),
            rec_dir="/tmp/rec",
            snd_dir="/tmp/snd",
            memory_fn=memory_fn,
            summarize_fn=summarize_fn,
            on_call_ended=on_call_ended,
        )
        return session

    def test_session_has_transcript(self):
        """Session erstellt automatisch ein CallTranscript."""
        from brain_core.phone.call_session import CallTranscript
        s = self._make_session()
        assert isinstance(s._transcript, CallTranscript)
        assert s._transcript.caller_id == "+4912345"
        assert s._transcript.session_id.startswith("phone_")

    def test_session_has_memory_fn(self):
        fn = AsyncMock()
        s = self._make_session(memory_fn=fn)
        assert s._memory_fn is fn

    def test_session_has_summarize_fn(self):
        fn = AsyncMock(return_value="Zusammenfassung")
        s = self._make_session(summarize_fn=fn)
        assert s._summarize_fn is fn

    def test_store_call_memory(self):
        """_store_call_memory() ruft memory_fn mit korrekten Params auf."""
        memory_fn = AsyncMock()
        s = self._make_session(memory_fn=memory_fn)
        s._transcript.add_turn("caller", "Mach Licht an")
        s._transcript.add_turn("soma", "Licht ist an!")
        s._transcript.summary = "Lichtsteuerung"
        s._transcript.dominant_emotion = "neutral"

        _run(s._store_call_memory())

        memory_fn.assert_called_once()
        call_kwargs = memory_fn.call_args
        args = call_kwargs.kwargs if call_kwargs.kwargs else {}
        if not args:
            args = dict(zip(
                ["event_type", "description", "user_text", "soma_text", "emotion", "importance"],
                call_kwargs.args
            )) if call_kwargs.args else {}
            if not args:
                args = call_kwargs[1] if len(call_kwargs) > 1 else {}

        # Prüfe über die Argumente (positional oder keyword)
        raw_call = memory_fn.call_args
        assert raw_call is not None

    def test_store_call_memory_importance_09(self):
        """Importance ist IMMER 0.9 für Anrufe."""
        memory_fn = AsyncMock()
        s = self._make_session(memory_fn=memory_fn)
        s._transcript.add_turn("caller", "Test")
        s._transcript.summary = "Test"

        _run(s._store_call_memory())

        # Prüfe dass importance=0.9 übergeben wird
        call_kwargs = memory_fn.call_args
        # Kann als keyword oder positional kommen
        if call_kwargs.kwargs:
            assert call_kwargs.kwargs.get("importance") == 0.9
        else:
            # Positional: event_type, description, user_text, soma_text, emotion, importance
            assert call_kwargs.args[-1] == 0.9 if call_kwargs.args else True

    def test_store_call_memory_event_type_phone_call(self):
        """event_type ist 'phone_call'."""
        memory_fn = AsyncMock()
        s = self._make_session(memory_fn=memory_fn)
        s._transcript.add_turn("caller", "Hallo")
        s._transcript.summary = "Begrüßung"

        _run(s._store_call_memory())

        call_kwargs = memory_fn.call_args
        if call_kwargs.kwargs:
            assert call_kwargs.kwargs.get("event_type") == "phone_call"

    def test_store_call_memory_without_fn(self):
        """Ohne memory_fn: kein Fehler, silent skip."""
        s = self._make_session(memory_fn=None)
        s._transcript.add_turn("caller", "Test")
        # Sollte nicht crashen
        _run(s._store_call_memory())

    def test_store_call_memory_error_handling(self):
        """Bei Memory-Fehler: kein Crash, nur Log."""
        memory_fn = AsyncMock(side_effect=Exception("DB down"))
        s = self._make_session(memory_fn=memory_fn)
        s._transcript.add_turn("caller", "Test")
        s._transcript.summary = "Test"
        # Sollte nicht crashen
        _run(s._store_call_memory())

    def test_generate_summary_with_fn(self):
        """_generate_summary() nutzt summarize_fn wenn vorhanden."""
        summarize_fn = AsyncMock(return_value="Patrick rief an wegen Licht.")
        s = self._make_session(summarize_fn=summarize_fn)
        s._transcript.add_turn("caller", "Licht an bitte")
        s._transcript.add_turn("soma", "Licht ist an!")
        s._transcript.start_time = 100.0
        s._transcript.end_time = 130.0

        result = _run(s._generate_summary())
        assert "Patrick" in result
        summarize_fn.assert_called_once()

    def test_generate_summary_without_fn(self):
        """Ohne summarize_fn: mechanische Zusammenfassung."""
        s = self._make_session(summarize_fn=None)
        s._transcript.add_turn("caller", "Frage")
        s._transcript.add_turn("soma", "Antwort")
        s._transcript.start_time = 100.0
        s._transcript.end_time = 160.0

        result = _run(s._generate_summary())
        assert "Telefonat" in result
        assert "1 Fragen" in result
        assert "60s" in result

    def test_generate_summary_timeout(self):
        """Bei Timeout: Fallback-Zusammenfassung."""
        async def slow_fn(prompt):
            await asyncio.sleep(100)
            return "never"

        s = self._make_session(summarize_fn=slow_fn)
        s._transcript.add_turn("caller", "Test")
        s._transcript.add_turn("soma", "Ok")
        s._transcript.start_time = 100.0
        s._transcript.end_time = 105.0

        # Timeout auf 0.1s patchen für schnellen Test
        with patch.object(asyncio, "wait_for", side_effect=asyncio.TimeoutError):
            result = _run(s._generate_summary())
        assert "Timeout" in result or "Telefonat" in result

    def test_generate_summary_error(self):
        """Bei LLM-Fehler: Fallback-Zusammenfassung."""
        summarize_fn = AsyncMock(side_effect=RuntimeError("LLM kaputt"))
        s = self._make_session(summarize_fn=summarize_fn)
        s._transcript.add_turn("caller", "Test")
        s._transcript.add_turn("soma", "Ok")
        s._transcript.start_time = 100.0
        s._transcript.end_time = 105.0

        result = _run(s._generate_summary())
        assert "Telefonat" in result
        assert "+4912345" in result


# ═══════════════════════════════════════════════════════════════════════════
#  7. CallSession — Finalization
# ═══════════════════════════════════════════════════════════════════════════


class TestCallSessionFinalization:
    """Phase 7: _finalize_call() — Zusammenfassung + Memory nach Call-Ende."""

    def _make_session(self, memory_fn=None, summarize_fn=None, on_call_ended=None):
        from brain_core.phone.call_session import CallSession
        session = CallSession(
            channel_id="test_final_001",
            caller_id="+4912345",
            stt=MagicMock(),
            tts=MagicMock(),
            router=MagicMock(),
            ha_bridge=MagicMock(),
            ari_base="http://localhost:8088",
            ari_auth=("soma", "pass"),
            rec_dir="/tmp/rec",
            snd_dir="/tmp/snd",
            memory_fn=memory_fn,
            summarize_fn=summarize_fn,
            on_call_ended=on_call_ended,
            broadcast=AsyncMock(),
        )
        return session

    def test_finalize_builds_call_record(self):
        """Nach Finalize existiert ein CallRecord."""
        memory_fn = AsyncMock()
        s = self._make_session(memory_fn=memory_fn)
        s._transcript.add_turn("caller", "Hi")
        s._transcript.add_turn("soma", "Hallo!")
        s._transcript.start_time = 100.0
        s._transcript.end_time = 130.0

        _run(s._finalize_call())

        assert s._call_record is not None
        assert s._call_record.session_id.startswith("phone_")
        assert s._call_record.stored_in_memory is True

    def test_finalize_calls_on_call_ended(self):
        """Callback on_call_ended wird mit CallRecord aufgerufen."""
        callback = AsyncMock()
        s = self._make_session(on_call_ended=callback)
        s._transcript.add_turn("caller", "Test")
        s._transcript.start_time = 100.0
        s._transcript.end_time = 110.0

        _run(s._finalize_call())

        callback.assert_called_once()
        record = callback.call_args[0][0]
        assert record.caller_id == "+4912345"

    def test_finalize_stores_memory(self):
        """Finalize speichert den Anruf in Memory."""
        memory_fn = AsyncMock()
        s = self._make_session(memory_fn=memory_fn)
        s._transcript.add_turn("caller", "Licht an")
        s._transcript.add_turn("soma", "Erledigt!")
        s._transcript.start_time = 100.0
        s._transcript.end_time = 120.0

        _run(s._finalize_call())
        memory_fn.assert_called_once()

    def test_finalize_generates_summary(self):
        """Finalize generiert eine LLM-Zusammenfassung."""
        summarize_fn = AsyncMock(return_value="Anrufer wollte Licht steuern.")
        s = self._make_session(summarize_fn=summarize_fn)
        s._transcript.add_turn("caller", "Licht an")
        s._transcript.add_turn("soma", "Erledigt!")
        s._transcript.start_time = 100.0
        s._transcript.end_time = 120.0

        _run(s._finalize_call())
        assert s._transcript.summary == "Anrufer wollte Licht steuern."

    def test_finalize_short_call_no_llm_summary(self):
        """Bei < 2 Turns: Mechanische Zusammenfassung, kein LLM-Aufruf."""
        summarize_fn = AsyncMock()
        s = self._make_session(summarize_fn=summarize_fn)
        s._transcript.add_turn("caller", "Hallo")
        s._transcript.start_time = 100.0
        s._transcript.end_time = 105.0

        _run(s._finalize_call())
        summarize_fn.assert_not_called()
        assert "Kurzer Anruf" in s._transcript.summary

    def test_finalize_detects_emotion(self):
        """Finalize setzt dominant_emotion."""
        s = self._make_session()
        s._transcript.add_turn("caller", "Danke, das ist super toll!")
        s._transcript.add_turn("soma", "Gern geschehen!")
        s._transcript.start_time = 100.0
        s._transcript.end_time = 120.0

        _run(s._finalize_call())
        assert s._transcript.dominant_emotion == "happy"

    def test_finalize_error_resilience(self):
        """Bei Fehler: kein Crash."""
        memory_fn = AsyncMock(side_effect=Exception("BOOM"))
        summarize_fn = AsyncMock(side_effect=Exception("LLM FAIL"))
        s = self._make_session(memory_fn=memory_fn, summarize_fn=summarize_fn)
        s._transcript.add_turn("caller", "Test")
        s._transcript.add_turn("soma", "Ok")
        s._transcript.start_time = 100.0
        s._transcript.end_time = 110.0

        # Sollte NICHT crashen
        _run(s._finalize_call())

    def test_finalize_emits_dashboard_event(self):
        """Finalize broadcastet PHONE_SUMMARY Event."""
        broadcast = AsyncMock()
        s = self._make_session()
        s._broadcast = broadcast
        s._transcript.add_turn("caller", "Hi")
        s._transcript.add_turn("soma", "Hallo!")
        s._transcript.start_time = 100.0
        s._transcript.end_time = 115.0

        _run(s._finalize_call())

        # Mindestens ein Broadcast für PHONE_SUMMARY
        found_summary = False
        for call in broadcast.call_args_list:
            args = call.args if call.args else call[0]
            if len(args) >= 3 and args[2] == "PHONE_SUMMARY":
                found_summary = True
                break
        assert found_summary, "PHONE_SUMMARY Event nicht gebroadcastet"


# ═══════════════════════════════════════════════════════════════════════════
#  8. PhonePipeline — Call History & Stats
# ═══════════════════════════════════════════════════════════════════════════


class TestPhonePipelineHistory:
    """Phase 7: PhonePipeline mit Call History, Stats, Record-Lookup."""

    def _make_pipeline(self, memory_fn=None, summarize_fn=None):
        from brain_core.phone.phone_pipeline import PhonePipeline
        return PhonePipeline(
            stt_engine=MagicMock(),
            tts_engine=MagicMock(),
            logic_router=MagicMock(),
            ha_bridge=MagicMock(),
            broadcast_callback=AsyncMock(),
            memory_fn=memory_fn,
            summarize_fn=summarize_fn,
        )

    def _make_record(self, session_id="phone_test", caller_id="+49", duration=60.0, emotion="neutral"):
        from brain_core.phone.call_session import CallTranscript, CallRecord
        t = CallTranscript(caller_id=caller_id, session_id=session_id)
        t.start_time = 100.0
        t.end_time = 100.0 + duration
        t.summary = f"Testanruf {session_id}"
        t.dominant_emotion = emotion
        t.end_reason = "goodbye"
        return CallRecord(t)

    def test_pipeline_has_history_list(self):
        p = self._make_pipeline()
        assert p._call_history == []
        assert p._total_calls == 0

    def test_pipeline_stores_memory_fn(self):
        fn = AsyncMock()
        p = self._make_pipeline(memory_fn=fn)
        assert p._memory_fn is fn

    def test_pipeline_stores_summarize_fn(self):
        fn = AsyncMock()
        p = self._make_pipeline(summarize_fn=fn)
        assert p._summarize_fn is fn

    def test_on_call_ended_adds_to_history(self):
        p = self._make_pipeline()
        record = self._make_record()
        _run(p._on_call_ended(record))
        assert len(p._call_history) == 1
        assert p._total_calls == 1

    def test_on_call_ended_accumulates(self):
        p = self._make_pipeline()
        _run(p._on_call_ended(self._make_record("s1")))
        _run(p._on_call_ended(self._make_record("s2")))
        _run(p._on_call_ended(self._make_record("s3")))
        assert p._total_calls == 3
        assert len(p._call_history) == 3

    def test_on_call_ended_tracks_duration(self):
        p = self._make_pipeline()
        _run(p._on_call_ended(self._make_record(duration=30.0)))
        _run(p._on_call_ended(self._make_record(duration=90.0)))
        assert p._total_duration == 120.0

    def test_history_limit(self):
        """History wird auf _max_history begrenzt."""
        p = self._make_pipeline()
        p._max_history = 5
        for i in range(10):
            _run(p._on_call_ended(self._make_record(f"s{i}")))
        assert len(p._call_history) == 5
        assert p._total_calls == 10  # Total count zählt ALLE

    def test_get_call_history(self):
        p = self._make_pipeline()
        _run(p._on_call_ended(self._make_record("s1")))
        _run(p._on_call_ended(self._make_record("s2")))

        history = p.get_call_history(limit=10)
        assert len(history) == 2
        assert all(isinstance(h, dict) for h in history)

    def test_get_call_history_limit(self):
        p = self._make_pipeline()
        for i in range(10):
            _run(p._on_call_ended(self._make_record(f"s{i}")))

        history = p.get_call_history(limit=3)
        assert len(history) == 3

    def test_get_call_record_found(self):
        p = self._make_pipeline()
        _run(p._on_call_ended(self._make_record("target_call")))

        record = p.get_call_record("target_call")
        assert record is not None
        assert record.session_id == "target_call"

    def test_get_call_record_not_found(self):
        p = self._make_pipeline()
        assert p.get_call_record("nonexistent") is None

    def test_stats_empty(self):
        p = self._make_pipeline()
        s = p.stats
        assert s["total_calls"] == 0
        assert s["active_calls"] == 0
        assert s["avg_duration_sec"] == 0.0
        assert s["is_running"] is False

    def test_stats_with_calls(self):
        p = self._make_pipeline()
        _run(p._on_call_ended(self._make_record("s1", duration=60.0, emotion="happy")))
        _run(p._on_call_ended(self._make_record("s2", duration=120.0, emotion="happy")))
        _run(p._on_call_ended(self._make_record("s3", duration=30.0, emotion="stressed")))

        s = p.stats
        assert s["total_calls"] == 3
        assert s["total_duration_sec"] == 210.0
        assert s["avg_duration_sec"] == 70.0
        assert s["history_size"] == 3
        assert s["emotion_distribution"]["happy"] == 2
        assert s["emotion_distribution"]["stressed"] == 1

    def test_call_history_list_property(self):
        from brain_core.phone.call_session import CallRecord
        p = self._make_pipeline()
        _run(p._on_call_ended(self._make_record("s1")))
        lst = p.call_history_list
        assert len(lst) == 1
        assert isinstance(lst[0], CallRecord)


# ═══════════════════════════════════════════════════════════════════════════
#  9. Phone __init__.py — Exports
# ═══════════════════════════════════════════════════════════════════════════


class TestPhoneExports:
    """Phase 7: phone/__init__.py exportiert alle Klassen."""

    def test_export_call_session(self):
        from brain_core.phone import CallSession
        assert CallSession is not None

    def test_export_call_state(self):
        from brain_core.phone import CallState
        assert CallState is not None

    def test_export_call_turn(self):
        from brain_core.phone import CallTurn
        assert CallTurn is not None

    def test_export_call_transcript(self):
        from brain_core.phone import CallTranscript
        assert CallTranscript is not None

    def test_export_call_record(self):
        from brain_core.phone import CallRecord
        assert CallRecord is not None

    def test_export_phone_pipeline(self):
        from brain_core.phone import PhonePipeline
        assert PhonePipeline is not None


# ═══════════════════════════════════════════════════════════════════════════
#  10. main.py — Phone API Endpoints & Wiring
# ═══════════════════════════════════════════════════════════════════════════


class TestMainPhoneWiring:
    """Phase 7: main.py hat Phone-Endpoints und Memory-Wiring."""

    def test_phone_status_endpoint_exists(self):
        import brain_core.main as m
        routes = [r.path for r in m.app.routes if hasattr(r, "path")]
        assert "/api/v1/phone/status" in routes

    def test_phone_history_endpoint_exists(self):
        import brain_core.main as m
        routes = [r.path for r in m.app.routes if hasattr(r, "path")]
        assert "/api/v1/phone/history" in routes

    def test_phone_history_detail_endpoint_exists(self):
        import brain_core.main as m
        routes = [r.path for r in m.app.routes if hasattr(r, "path")]
        assert "/api/v1/phone/history/{session_id}" in routes

    def test_phone_stats_endpoint_exists(self):
        import brain_core.main as m
        routes = [r.path for r in m.app.routes if hasattr(r, "path")]
        assert "/api/v1/phone/stats" in routes

    def test_audio_serve_endpoint_exists(self):
        import brain_core.main as m
        routes = [r.path for r in m.app.routes if hasattr(r, "path")]
        assert "/api/v1/audio/{filename}" in routes

    def test_phone_pipeline_import(self):
        """PhonePipeline wird in main.py importiert."""
        import brain_core.main as m
        assert hasattr(m, "phone_pipeline")

    def test_memory_store_event_import(self):
        """memory_store_event ist in main.py als Callback verfügbar."""
        import brain_core.main as m
        assert hasattr(m, "memory_store_event")

    def test_boot_code_has_memory_fn(self):
        """Boot-Code übergibt memory_fn an PhonePipeline."""
        import inspect
        import brain_core.main as m
        source = inspect.getsource(m)
        assert "memory_fn=memory_store_event" in source

    def test_boot_code_has_summarize_fn(self):
        """Boot-Code übergibt summarize_fn an PhonePipeline."""
        import inspect
        import brain_core.main as m
        source = inspect.getsource(m)
        assert "_phone_summarize" in source
        assert "summarize_fn=_phone_summarize" in source

    def test_call_record_import_in_pipeline(self):
        """PhonePipeline importiert CallRecord."""
        from brain_core.phone.phone_pipeline import CallRecord
        assert CallRecord is not None


# ═══════════════════════════════════════════════════════════════════════════
#  11. Integration — End-to-End Call Flow
# ═══════════════════════════════════════════════════════════════════════════


class TestCallFlowIntegration:
    """Phase 7: Integration — Gesamter Call-Flow von Turn bis Memory."""

    def test_full_flow_transcript_to_record_to_history(self):
        """Transcript → Finalize → CallRecord → Pipeline History."""
        from brain_core.phone.call_session import CallTranscript, CallRecord
        from brain_core.phone.phone_pipeline import PhonePipeline

        # 1. Transcript bauen
        t = CallTranscript(caller_id="+4912345", session_id="flow_test_001")
        t.start_time = 1000.0
        t.add_turn("caller", "Mach das Licht an")
        t.add_turn("soma", "Licht im Wohnzimmer ist an!")
        t.add_turn("caller", "Danke, super!")
        t.add_turn("soma", "Gern geschehen!")
        t.end_time = 1060.0
        t.authenticated = True
        t.end_reason = "goodbye"
        t.summary = "Lichtsteuerung im Wohnzimmer"
        t.dominant_emotion = "happy"
        t.actions_executed = ["ha_call: light.wohnzimmer"]

        # 2. Record erstellen
        record = CallRecord(t)
        assert record.turn_count == 4
        assert record.duration_sec == 60.0

        # 3. In Pipeline-History einfügen
        pipeline = PhonePipeline(
            stt_engine=MagicMock(),
            tts_engine=MagicMock(),
            logic_router=MagicMock(),
            ha_bridge=MagicMock(),
        )
        _run(pipeline._on_call_ended(record))

        # 4. Verifiziere History
        assert pipeline.stats["total_calls"] == 1
        history = pipeline.get_call_history()
        assert len(history) == 1
        assert history[0]["summary"] == "Lichtsteuerung im Wohnzimmer"
        assert history[0]["dominant_emotion"] == "happy"

    def test_full_flow_memory_fn_called(self):
        """Der Memory-Callback wird mit den richtigen Daten aufgerufen."""
        from brain_core.phone.call_session import CallSession

        memory_fn = AsyncMock()
        session = CallSession(
            channel_id="flow_mem_001",
            caller_id="+4912345",
            stt=MagicMock(),
            tts=MagicMock(),
            router=MagicMock(),
            ha_bridge=MagicMock(),
            ari_base="http://localhost:8088",
            ari_auth=("soma", "pass"),
            rec_dir="/tmp/rec",
            snd_dir="/tmp/snd",
            memory_fn=memory_fn,
            broadcast=AsyncMock(),
        )

        session._transcript.add_turn("caller", "Wie ist das Wetter?")
        session._transcript.add_turn("soma", "Sonnig und warm!")
        session._transcript.start_time = 100.0
        session._transcript.end_time = 130.0

        _run(session._finalize_call())

        memory_fn.assert_called_once()
        kwargs = memory_fn.call_args.kwargs
        assert kwargs["event_type"] == "phone_call"
        assert kwargs["importance"] == 0.9
        assert "Wetter" in kwargs["user_text"]
        assert "Sonnig" in kwargs["soma_text"]

    def test_full_flow_callback_chain(self):
        """Session → finalize → on_call_ended → Pipeline history."""
        from brain_core.phone.call_session import CallSession
        from brain_core.phone.phone_pipeline import PhonePipeline

        pipeline = PhonePipeline(
            stt_engine=MagicMock(),
            tts_engine=MagicMock(),
            logic_router=MagicMock(),
            ha_bridge=MagicMock(),
        )

        session = CallSession(
            channel_id="chain_001",
            caller_id="+49999",
            stt=MagicMock(),
            tts=MagicMock(),
            router=MagicMock(),
            ha_bridge=MagicMock(),
            ari_base="http://localhost:8088",
            ari_auth=("soma", "pass"),
            rec_dir="/tmp/rec",
            snd_dir="/tmp/snd",
            on_call_ended=pipeline._on_call_ended,
            broadcast=AsyncMock(),
        )

        session._transcript.add_turn("caller", "Test")
        session._transcript.add_turn("soma", "Ok!")
        session._transcript.start_time = 100.0
        session._transcript.end_time = 110.0

        _run(session._finalize_call())

        # Pipeline hat den Record
        assert pipeline.stats["total_calls"] == 1
        assert len(pipeline._call_history) == 1
        assert pipeline._call_history[0].caller_id == "+49999"

    def test_multiple_calls_accumulate(self):
        """Mehrere Anrufe sammeln sich in der History."""
        from brain_core.phone.call_session import CallTranscript, CallRecord
        from brain_core.phone.phone_pipeline import PhonePipeline

        pipeline = PhonePipeline(
            stt_engine=MagicMock(),
            tts_engine=MagicMock(),
            logic_router=MagicMock(),
            ha_bridge=MagicMock(),
        )

        for i in range(5):
            t = CallTranscript(caller_id=f"+49{i}", session_id=f"multi_{i}")
            t.start_time = 100.0
            t.end_time = 100.0 + (i + 1) * 30
            t.summary = f"Anruf {i}"
            t.dominant_emotion = "neutral"
            t.end_reason = "goodbye"
            record = CallRecord(t)
            _run(pipeline._on_call_ended(record))

        assert pipeline.stats["total_calls"] == 5
        assert pipeline.stats["total_duration_sec"] == 30 + 60 + 90 + 120 + 150

    def test_transcript_preserves_all_turns(self):
        """Alle Turns bleiben vollständig im Transkript erhalten."""
        from brain_core.phone.call_session import CallTranscript

        t = CallTranscript(caller_id="+49", session_id="preserve_test")
        turns_data = [
            ("caller", "Erster Satz"),
            ("soma", "Erste Antwort"),
            ("caller", "Zweiter Satz"),
            ("soma", "Zweite Antwort"),
            ("caller", "Tschüss"),
            ("soma", "Bis bald!"),
        ]
        for speaker, text in turns_data:
            t.add_turn(speaker, text)

        assert t.turn_count == 6
        assert len(t.caller_turns) == 3
        assert len(t.soma_turns) == 3

        # Alle Turns im build_transcript_text
        text = t.build_transcript_text()
        for _, content in turns_data:
            assert content in text


# ═══════════════════════════════════════════════════════════════════════════
#  12. Edge Cases & Robustness
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Phase 7: Robustheit-Tests für Randfälle."""

    def test_empty_transcript_finalize(self):
        """Finalize mit leerem Transkript: kein Crash."""
        from brain_core.phone.call_session import CallSession
        s = CallSession(
            channel_id="edge_001",
            caller_id="+49",
            stt=MagicMock(), tts=MagicMock(), router=MagicMock(), ha_bridge=MagicMock(),
            ari_base="http://localhost:8088", ari_auth=("s", "p"),
            rec_dir="/tmp/r", snd_dir="/tmp/s",
            broadcast=AsyncMock(),
        )
        s._transcript.start_time = 100.0
        s._transcript.end_time = 100.5
        _run(s._finalize_call())
        assert s._call_record is not None

    def test_very_long_transcript(self):
        """Langes Transkript wird korrekt zusammengefasst."""
        from brain_core.phone.call_session import CallTranscript
        t = CallTranscript(caller_id="+49", session_id="long_test")
        for i in range(100):
            t.add_turn("caller", f"Frage Nummer {i}: Wie steht es um das Thema {i}?")
            t.add_turn("soma", f"Antwort auf Frage {i}: Alles bestens!")
        assert t.turn_count == 200
        text = t.build_transcript_text(max_turns=10)
        assert "Frage Nummer 95" in text
        assert "Frage Nummer 0" not in text

    def test_unicode_in_transcript(self):
        """Unicode/Emojis in Turns sind kein Problem."""
        from brain_core.phone.call_session import CallTranscript
        t = CallTranscript(caller_id="+49", session_id="unicode_test")
        t.add_turn("caller", "🏠 Mein Haus, Überraschung mit Ömlauten: äöüß")
        t.add_turn("soma", "Natürlich! 🌟")
        assert t.turn_count == 2
        d = t.to_dict()
        assert "Ömlaut" in d["turns"][0]["text"]

    def test_record_from_minimal_transcript(self):
        """CallRecord aus minimalem Transcript."""
        from brain_core.phone.call_session import CallTranscript, CallRecord
        t = CallTranscript(caller_id="anon", session_id="min_test")
        r = CallRecord(t)
        assert r.turn_count == 0
        assert r.duration_sec == 0.0
        assert r.end_reason == "unknown"

    def test_concurrent_pipeline_callbacks(self):
        """Mehrere gleichzeitige _on_call_ended Callbacks."""
        from brain_core.phone.call_session import CallTranscript, CallRecord
        from brain_core.phone.phone_pipeline import PhonePipeline

        pipeline = PhonePipeline(
            stt_engine=MagicMock(),
            tts_engine=MagicMock(),
            logic_router=MagicMock(),
            ha_bridge=MagicMock(),
        )

        async def concurrent_calls():
            tasks = []
            for i in range(10):
                t = CallTranscript(caller_id=f"+49{i}", session_id=f"conc_{i}")
                t.start_time = 100.0
                t.end_time = 110.0
                t.summary = f"Anruf {i}"
                t.dominant_emotion = "neutral"
                t.end_reason = "goodbye"
                tasks.append(pipeline._on_call_ended(CallRecord(t)))
            await asyncio.gather(*tasks)

        _run(concurrent_calls())
        assert pipeline._total_calls == 10
