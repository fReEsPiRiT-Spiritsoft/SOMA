#!/usr/bin/env python3
"""
SOMA Quick Voice Test — Sprich mit der KI!
============================================
Einfacher Test: Aufnehmen → STT → Antwort → TTS

Usage:
  python quick_voice_test.py           # Einmaliger Test
  python quick_voice_test.py --loop    # Konversations-Schleife
"""

import asyncio
import sys
import numpy as np
from pathlib import Path

# Projekt-Root zu Path hinzufügen
sys.path.insert(0, str(Path(__file__).parent))


async def record_audio(duration: float = 5.0) -> tuple[np.ndarray, int]:
    """Nimm Audio auf via PipeWire/ALSA."""
    print(f"\n  🔴 AUFNAHME ({duration}s) — Sprich jetzt!")
    
    sample_rate = 48000
    
    proc = await asyncio.create_subprocess_exec(
        "arecord", "-D", "hw:0,0",  # Focusrite
        "-f", "S32_LE", "-r", str(sample_rate), "-c", "2",
        "-t", "raw", "-d", str(int(duration)),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=duration + 5)
    except asyncio.TimeoutError:
        proc.kill()
        print("  ⚠️ Timeout bei Aufnahme")
        return np.array([]), 16000
    
    if proc.returncode != 0 or len(stdout) < 1000:
        print("  ⚠️ Focusrite direkt fehlgeschlagen, nutze default...")
        proc = await asyncio.create_subprocess_exec(
            "arecord", "-D", "default",
            "-f", "S16_LE", "-r", "16000", "-c", "1",
            "-t", "raw", "-d", str(int(duration)),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=duration + 5)
        audio = np.frombuffer(stdout, dtype=np.int16).astype(np.float32) / 32768.0
        return audio, 16000
    
    # S32_LE stereo → mono float32
    raw = np.frombuffer(stdout, dtype=np.int32).astype(np.float32) / 2147483648.0
    audio = raw[0::2]  # Linker Kanal (Mic1)
    
    # Resample 48kHz → 16kHz
    from scipy.signal import resample_poly
    audio_16k = resample_poly(audio, 1, 3)
    
    rms = np.sqrt(np.mean(audio_16k**2))
    print(f"  ⏹️ Aufnahme fertig. RMS: {rms:.4f}")
    
    # Boost wenn zu leise
    if rms < 0.01 and rms > 0.0001:
        boost = 0.05 / rms
        audio_16k = np.clip(audio_16k * boost, -1.0, 1.0)
        print(f"  📢 Audio geboostet (x{boost:.1f})")
    
    return audio_16k.astype(np.float32), 16000


async def transcribe(audio: np.ndarray, sample_rate: int) -> str:
    """STT via faster-whisper."""
    from brain_core.voice.stt import STTEngine
    
    print("  🧠 Transkribiere...")
    
    stt = STTEngine(model_size="small", device="auto")
    await stt.initialize()
    
    result = stt.transcribe(audio, sample_rate=sample_rate)
    
    await stt.shutdown()
    
    print(f"  📝 Erkannt: \"{result.text}\"")
    print(f"     Sprache: {result.language}, Soma: {'✅' if result.contains_soma else '❌'}")
    
    return result.text


async def generate_response(text: str) -> str:
    """Generiere Antwort via Ollama (oder Fallback)."""
    import httpx
    
    if not text.strip():
        return "Ich habe nichts verstanden. Kannst du das wiederholen?"
    
    # Soma aus dem Text entfernen
    import re
    prompt = re.sub(r'\b(?:hey\s+)?(?:soma|sooma|sohma)\b', '', text, flags=re.IGNORECASE).strip()
    
    if not prompt:
        return "Ja, ich höre?"
    
    print(f"  🤖 Frage an LLM: \"{prompt}\"")
    
    # Versuche Ollama
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": "llama3:latest",
                    "prompt": f"""Du bist Soma, ein freundliches, intelligentes Smart-Home-KI-System. 
Du bist lokal, privat und hilfst den Bewohnern.
Dein Charakter: nervy-cool, effizient, leicht frech aber herzlich.
Antworte KURZ (max 2-3 Sätze) und auf Deutsch.

Nutzer: {prompt}

Soma:""",
                    "stream": False,
                    "options": {"num_predict": 150, "temperature": 0.7}
                }
            )
            if response.status_code == 200:
                data = response.json()
                answer = data.get("response", "").strip()
                if answer:
                    print(f"  💬 Antwort: \"{answer[:100]}...\"")
                    return answer
    except Exception as e:
        print(f"  ⚠️ Ollama nicht verfügbar: {e}")
    
    # Fallback: Einfache Antworten
    prompt_lower = prompt.lower()
    
    if any(w in prompt_lower for w in ["hallo", "hi", "hey", "guten"]):
        return "Hallo! Ich bin Soma, dein intelligentes Zuhause. Was kann ich für dich tun?"
    if any(w in prompt_lower for w in ["wetter", "temperatur", "warm", "kalt"]):
        return "Das Wetter ist gerade angenehm. Soll ich das Licht anpassen?"
    if any(w in prompt_lower for w in ["licht", "lampe", "hell", "dunkel"]):
        return "Ich würde das Licht anpassen, aber die Smart-Home-Verbindung ist noch nicht eingerichtet."
    if any(w in prompt_lower for w in ["musik", "song", "spotify"]):
        return "Musik wäre schön! Die Musiksteuerung kommt bald."
    if any(w in prompt_lower for w in ["wer bist", "was bist", "kannst du"]):
        return "Ich bin Soma, dein lokales, privates Smart-Home-System. Ich höre dir zu und helfe wo ich kann."
    if any(w in prompt_lower for w in ["danke", "super", "toll"]):
        return "Gerne! Ich bin immer für dich da."
    if any(w in prompt_lower for w in ["test", "funktioniert"]):
        return "Ja, ich funktioniere! Sprache rein, Antwort raus. Alles lokal."
    
    return f"Ich habe gehört: {prompt}. Mein Denkvermögen wird noch klüger!"


async def speak(text: str):
    """TTS via Piper."""
    from brain_core.voice.tts import TTSEngine, SpeechEmotion
    
    print(f"  🔊 Spreche: \"{text[:60]}...\"")
    
    tts = TTSEngine(voice="de_DE-thorsten-high")
    await tts.initialize()
    
    await tts.speak(text, SpeechEmotion())
    
    # Warten bis fertig
    while tts.is_speaking or tts.queue_size > 0:
        await asyncio.sleep(0.1)
    
    await tts.shutdown()
    print("  ✅ Gesprochen!")


async def conversation_loop():
    """Hauptschleife: Hören → Verstehen → Antworten."""
    print("\n" + "="*60)
    print(" SOMA VOICE TEST — Sprich mit der KI!")
    print("="*60)
    print("\n  Drücke Enter um zu sprechen, 'q' zum Beenden.\n")
    
    while True:
        user_input = input("  [Enter] = Aufnehmen, [q] = Beenden: ").strip().lower()
        
        if user_input == 'q':
            print("\n  👋 Auf Wiedersehen!\n")
            break
        
        try:
            # 1. Aufnehmen
            audio, sr = await record_audio(duration=5.0)
            
            if len(audio) < 1000:
                print("  ❌ Keine Audio-Daten. Mikrofon OK?")
                continue
            
            # 2. Transkribieren
            text = await transcribe(audio, sr)
            
            if not text.strip():
                print("  ❌ Nichts erkannt. Sprich lauter oder näher am Mikrofon.")
                continue
            
            # 3. Antwort generieren
            response = await generate_response(text)
            
            # 4. Antwort sprechen
            await speak(response)
            
        except Exception as e:
            print(f"  ❌ Fehler: {e}")
            import traceback
            traceback.print_exc()
        
        print()


async def single_test():
    """Einmaliger Test ohne Schleife."""
    print("\n" + "="*60)
    print(" SOMA SINGLE VOICE TEST")
    print("="*60)
    
    # 1. Aufnehmen
    audio, sr = await record_audio(duration=5.0)
    
    if len(audio) < 1000:
        print("  ❌ Keine Audio-Daten.")
        return
    
    # 2. Transkribieren
    text = await transcribe(audio, sr)
    
    # 3. Antwort generieren
    response = await generate_response(text)
    
    # 4. Antwort sprechen
    await speak(response)
    
    print("\n  ✅ Test abgeschlossen!\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="SOMA Voice Test")
    parser.add_argument("--single", "-s", action="store_true", help="Einmaliger Test")
    parser.add_argument("--loop", "-l", action="store_true", help="Konversations-Schleife")
    args = parser.parse_args()
    
    if args.single:
        asyncio.run(single_test())
    elif args.loop:
        asyncio.run(conversation_loop())
    else:
        # Default: Single Test
        asyncio.run(single_test())


if __name__ == "__main__":
    main()
