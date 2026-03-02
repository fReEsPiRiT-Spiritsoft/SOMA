#!/usr/bin/env python3
"""
SOMA Voice Pipeline Test-Suite
================================
Testet STT, TTS und VAD mit deinem Focusrite Scarlett Solo Setup.

Usage:
  python test_voice.py --list           # Audio-Geräte auflisten
  python test_voice.py --test-tts       # TTS testen (Soma spricht)
  python test_voice.py --test-stt       # STT testen (Aufnahme → Text)
  python test_voice.py --test-vad       # VAD testen (Sprache erkennen)
  python test_voice.py --test-all       # Alles testen
  python test_voice.py --live           # Live Voice Pipeline starten
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

# ══════════════════════════════════════════════════════════════════════════════
#  AUDIO DEVICE DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def list_audio_devices():
    """Zeige alle Audio-Ein-/Ausgabegeräte."""
    print("\n🎤 AUDIO-EINGABEGERÄTE (Capture):\n")
    
    try:
        result = subprocess.run(["arecord", "-l"], capture_output=True, text=True)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("Karte") or line.startswith("card"):
                    is_focusrite = "focusrite" in line.lower() or "scarlett" in line.lower()
                    marker = "  🎚️ " if is_focusrite else "     "
                    print(f"{marker}{line}")
        else:
            print("  ❌ arecord -l fehlgeschlagen")
    except FileNotFoundError:
        print("  ❌ arecord nicht installiert (alsa-utils)")
    
    print("\n🔊 AUDIO-AUSGABEGERÄTE (Playback):\n")
    
    try:
        result = subprocess.run(["aplay", "-l"], capture_output=True, text=True)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("Karte") or line.startswith("card"):
                    is_focusrite = "focusrite" in line.lower() or "scarlett" in line.lower()
                    marker = "  🎚️ " if is_focusrite else "     "
                    print(f"{marker}{line}")
    except FileNotFoundError:
        print("  ❌ aplay nicht installiert")
    
    print("\n🔌 PIPEWIRE SOURCES:\n")
    try:
        result = subprocess.run(["pactl", "list", "sources", "short"], capture_output=True, text=True)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "input" in line.lower() or "mic" in line.lower():
                    is_focusrite = "focusrite" in line.lower()
                    marker = "  🎚️ " if is_focusrite else "     "
                    print(f"{marker}{line}")
    except FileNotFoundError:
        print("  ❌ pactl nicht installiert")
    
    print("\n  🎚️ = Focusrite Scarlett\n")


# ══════════════════════════════════════════════════════════════════════════════
#  TTS TEST
# ══════════════════════════════════════════════════════════════════════════════

async def test_tts():
    """Teste Text-to-Speech: Soma spricht."""
    print("\n🔊 TTS TEST — Soma bekommt eine Stimme\n")
    print("  ⏳ Lade Piper TTS Model...")
    
    try:
        from brain_core.voice.tts import TTSEngine, SpeechEmotion
        
        tts = TTSEngine(voice="de_DE-thorsten-high")
        await tts.initialize()
        
        print("  ✅ TTS Engine geladen\n")
        
        test_phrases = [
            ("Normal", SpeechEmotion(), "Hallo, ich bin Soma, dein intelligentes Zuhause."),
            ("Beruhigend", SpeechEmotion.calm(), "Hey, alles klar bei dir? Atme kurz durch."),
            ("Energisch", SpeechEmotion.energetic(), "Guten Morgen! Zeit für einen produktiven Tag!"),
            ("Sanft", SpeechEmotion.gentle(), "Es ist spät, du solltest langsam schlafen gehen."),
        ]
        
        for name, emotion, text in test_phrases:
            print(f"  🗣️ [{name}] \"{text}\"")
            await tts.speak(text, emotion)
            # Warten bis fertig gesprochen
            while tts.is_speaking or tts.queue_size > 0:
                await asyncio.sleep(0.1)
            await asyncio.sleep(0.5)
        
        await tts.shutdown()
        print("\n  ✅ TTS Test abgeschlossen!\n")
        return True
        
    except ImportError as e:
        print(f"  ❌ Import-Fehler: {e}")
        print("     Installiere: pip install piper-tts")
        return False
    except Exception as e:
        print(f"  ❌ Fehler: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  STT TEST
# ══════════════════════════════════════════════════════════════════════════════

async def test_stt():
    """Teste Speech-to-Text: Aufnahme → Text."""
    print("\n🎤 STT TEST — Whisper Transkription\n")
    print("  ⏳ Lade faster-whisper Model (small)...")
    
    try:
        from brain_core.voice.stt import STTEngine
        
        stt = STTEngine(model_size="small", device="auto")
        await stt.initialize()
        
        print("  ✅ STT Engine geladen\n")
        
        # 5 Sekunden Audio aufnehmen
        print("  🎤 Aufnahme startet in 2 Sekunden...")
        print("     Sprich etwas! (5 Sekunden)\n")
        await asyncio.sleep(2)
        
        duration = 5
        sample_rate = 48000  # Focusrite native
        
        # Aufnahme via arecord — Focusrite Mic1 explizit
        print("  🔴 AUFNAHME LÄUFT (Focusrite Mic1)...")
        
        # Versuche zuerst Focusrite direkt
        proc = await asyncio.create_subprocess_exec(
            "arecord",
            "-D", "hw:0,0",  # Focusrite = Card 0
            "-f", "S32_LE",
            "-r", str(sample_rate),
            "-c", "2",
            "-t", "raw",
            "-d", str(duration),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            print(f"  ⚠️ Focusrite direkt fehlgeschlagen, nutze PipeWire...")
            # Fallback: PipeWire Source
            proc = await asyncio.create_subprocess_exec(
                "parecord",
                "--device=alsa_input.usb-Focusrite_Scarlett_Solo_USB_Y7UTWA5170CE56-00.HiFi__Mic1__source",
                "--rate=16000",
                "--channels=1",
                "--format=s16le",
                "--raw",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.sleep(duration)
            proc.terminate()
            stdout, _ = await proc.communicate()
            audio = np.frombuffer(stdout, dtype=np.int16).astype(np.float32) / 32768.0
            sample_rate = 16000
        else:
            # S32_LE stereo → mono float32 (Kanal 1 = XLR/Mic)
            raw = np.frombuffer(stdout, dtype=np.int32).astype(np.float32) / 2147483648.0
            audio = raw[0::2]  # Nur linker Kanal (Mic1/XLR)
            # Resample 48kHz → 16kHz für Whisper
            from scipy import signal
            audio = signal.resample_poly(audio, 1, 3)  # 48000/16000 = 3
            sample_rate = 16000
        
        print("  ⏹️ Aufnahme beendet\n")
        
        rms = np.sqrt(np.mean(audio**2))
        print(f"  📊 Audio-Länge: {len(audio) / sample_rate:.2f}s, RMS: {rms:.4f}\n")
        
        if rms < 0.005:
            print("  ⚠️ Sehr leises Signal! Gain am Focusrite erhöhen!\n")
        
        # Transkribieren
        print("  ⏳ Transkribiere...")
        result = stt.transcribe(audio, sample_rate=sample_rate)
        
        print(f"\n  📝 ERGEBNIS:")
        print(f"     Text: \"{result.text}\"")
        print(f"     Sprache: {result.language}")
        print(f"     Konfidenz: {result.confidence:.0%}")
        print(f"     Verarbeitung: {result.processing_ms:.0f}ms")
        print(f"     'Soma' erkannt: {'✅ JA' if result.contains_soma else '❌ Nein'}")
        
        await stt.shutdown()
        print("\n  ✅ STT Test abgeschlossen!\n")
        return True
        
    except ImportError as e:
        print(f"  ❌ Import-Fehler: {e}")
        print("     Installiere: pip install faster-whisper")
        return False
    except Exception as e:
        print(f"  ❌ Fehler: {e}")
        import traceback
        traceback.print_exc()
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  VAD TEST
# ══════════════════════════════════════════════════════════════════════════════

async def test_vad():
    """Teste Voice Activity Detection: Sprache erkennen."""
    print("\n🎤 VAD TEST — Sprach-Aktivitäts-Erkennung\n")
    
    try:
        from brain_core.voice.vad import ContinuousVAD, VAD_FRAME_BYTES, VAD_SAMPLE_RATE
        
        vad = ContinuousVAD(aggressiveness=2)
        
        print("  ✅ VAD Engine geladen")
        print("  🎤 Höre 10 Sekunden zu... Sprich wenn du willst!\n")
        
        segments_found = []
        
        def on_segment(seg):
            print(f"     🗣️ Segment erkannt! Dauer: {seg.duration_sec:.2f}s, RMS: {seg.rms:.4f}")
            segments_found.append(seg)
        
        def on_start():
            print("     ▶️ Sprache beginnt...")
        
        def on_end():
            print("     ⏹️ Sprache endet.")
        
        vad.on_segment = on_segment
        vad.on_speech_start = on_start
        vad.on_speech_end = on_end
        
        # 10 Sekunden Audio via arecord
        duration = 10
        
        proc = await asyncio.create_subprocess_exec(
            "arecord",
            "-D", "default",
            "-f", "S16_LE",
            "-r", str(VAD_SAMPLE_RATE),
            "-c", "1",
            "-t", "raw",
            "-d", str(duration),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        
        reader = proc.stdout
        start_time = time.time()
        
        while time.time() - start_time < duration + 1:
            data = await reader.read(VAD_FRAME_BYTES)
            if not data:
                break
            if len(data) == VAD_FRAME_BYTES:
                vad.feed(data)
        
        await proc.wait()
        
        print(f"\n  📊 Ergebnis:")
        print(f"     Segmente erkannt: {len(segments_found)}")
        print(f"     Speech-Ratio: {vad.speech_ratio:.1%}")
        
        print("\n  ✅ VAD Test abgeschlossen!\n")
        return True
        
    except ImportError as e:
        print(f"  ❌ Import-Fehler: {e}")
        print("     Installiere: pip install webrtcvad-wheels")
        return False
    except Exception as e:
        print(f"  ❌ Fehler: {e}")
        import traceback
        traceback.print_exc()
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  LIVE VOICE PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

async def run_live_pipeline():
    """Starte die komplette Voice Pipeline live."""
    print("\n🚀 LIVE VOICE PIPELINE\n")
    print("  Soma hört jetzt DAUERHAFT zu.")
    print("  Sag 'Soma' irgendwo im Satz, um Soma anzusprechen.")
    print("  Drücke Ctrl+C zum Beenden.\n")
    
    try:
        from brain_core.voice.pipeline import VoicePipeline
        
        pipeline = VoicePipeline(
            audio_device="default",
            stt_model="small",
            tts_voice="de_DE-thorsten-high",
        )
        
        await pipeline.start()
        
        # Status-Updates alle 5 Sekunden
        while pipeline.is_running:
            await asyncio.sleep(5)
            stats = pipeline.stats
            print(f"\r  📊 Segmente: {stats['segments_processed']} | "
                  f"Transkr.: {stats['transcriptions']} | "
                  f"Soma-Trigger: {stats['soma_triggers']} | "
                  f"Interventionen: {stats['interventions']} | "
                  f"Mood: {stats['atmosphere']['mood']} | "
                  f"Stress: {stats['atmosphere']['stress']:.0%}", end="")
        
    except KeyboardInterrupt:
        print("\n\n  ⏹️ Beende Pipeline...")
        await pipeline.stop()
        print("  ✅ Pipeline gestoppt.\n")
    except ImportError as e:
        print(f"  ❌ Import-Fehler: {e}")
        return False
    except Exception as e:
        print(f"  ❌ Fehler: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True


# ══════════════════════════════════════════════════════════════════════════════
#  QUICK AUDIO TEST
# ══════════════════════════════════════════════════════════════════════════════

async def test_audio_capture():
    """Schneller Audio-Hardware Test."""
    print("\n🎤 AUDIO HARDWARE TEST\n")
    
    print("  ⏳ Teste Focusrite Scarlett Solo Mic1 (XLR)...")
    
    # 3 Sekunden aufnehmen
    proc = await asyncio.create_subprocess_exec(
        "arecord",
        "-D", "hw:0,0",  # Focusrite = Card 0
        "-f", "S32_LE",
        "-r", "48000",
        "-c", "2",
        "-t", "raw",
        "-d", "3",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    
    stdout, stderr = await proc.communicate()
    
    if proc.returncode != 0:
        print(f"  ❌ arecord fehlgeschlagen: {stderr.decode()}")
        
        # Fallback: default device
        print("  ⏳ Versuche 'default' device...")
        proc = await asyncio.create_subprocess_exec(
            "arecord",
            "-D", "default",
            "-f", "S16_LE",
            "-r", "16000",
            "-c", "1",
            "-t", "raw",
            "-d", "3",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            print(f"  ❌ Auch 'default' fehlgeschlagen: {stderr.decode()}")
            return False
        
        audio = np.frombuffer(stdout, dtype=np.int16).astype(np.float32) / 32768.0
    else:
        # S32_LE stereo → mono float32
        raw = np.frombuffer(stdout, dtype=np.int32).astype(np.float32) / 2147483648.0
        audio = raw[0::2]  # Nur linker Kanal (Mic1/XLR)
    
    rms = np.sqrt(np.mean(audio**2))
    peak = np.max(np.abs(audio))
    
    print(f"\n  📊 Audio-Statistiken:")
    print(f"     Samples: {len(audio)}")
    print(f"     RMS: {rms:.6f}")
    print(f"     Peak: {peak:.6f}")
    
    if rms < 0.001:
        print("\n  ⚠️ Sehr leises Signal! Mikrofon angeschlossen?")
    elif rms > 0.01:
        print("\n  ✅ Gutes Signal erkannt!")
    else:
        print("\n  ⚠️ Leises Signal. Gain erhöhen?")
    
    # Level-Balken
    bar_len = int(rms * 500)
    bar = "█" * min(bar_len, 50)
    print(f"\n  Level: [{bar:<50}]\n")
    
    return True


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="SOMA Voice Pipeline Test-Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--list", action="store_true", help="Audio-Geräte auflisten")
    parser.add_argument("--test-audio", action="store_true", help="Quick Audio-Hardware Test")
    parser.add_argument("--test-tts", action="store_true", help="TTS testen")
    parser.add_argument("--test-stt", action="store_true", help="STT testen")
    parser.add_argument("--test-vad", action="store_true", help="VAD testen")
    parser.add_argument("--test-all", action="store_true", help="Alle Tests")
    parser.add_argument("--live", action="store_true", help="Live Voice Pipeline")
    
    args = parser.parse_args()
    
    if args.list:
        list_audio_devices()
        return
    
    if args.test_audio:
        asyncio.run(test_audio_capture())
        return
    
    if args.test_tts:
        asyncio.run(test_tts())
        return
    
    if args.test_stt:
        asyncio.run(test_stt())
        return
    
    if args.test_vad:
        asyncio.run(test_vad())
        return
    
    if args.test_all:
        print("\n" + "="*60)
        print(" SOMA VOICE PIPELINE — VOLLSTÄNDIGER TEST")
        print("="*60)
        
        asyncio.run(test_audio_capture())
        asyncio.run(test_tts())
        asyncio.run(test_stt())
        asyncio.run(test_vad())
        
        print("\n" + "="*60)
        print(" ALLE TESTS ABGESCHLOSSEN")
        print("="*60 + "\n")
        return
    
    if args.live:
        asyncio.run(run_live_pipeline())
        return
    
    # Default: Hilfe anzeigen
    parser.print_help()
    print("\n💡 Schnellstart:")
    print("   python test_voice.py --list        # Geräte anzeigen")
    print("   python test_voice.py --test-audio  # Hardware testen")
    print("   python test_voice.py --test-all    # Alles testen")
    print("   python test_voice.py --live        # Live Pipeline starten\n")


if __name__ == "__main__":
    main()
