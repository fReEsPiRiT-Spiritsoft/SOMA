#!/usr/bin/env python3
"""
Live Audio Meter für Focusrite Scarlett Solo
Zeigt den aktuellen Pegel des XLR-Eingangs (Mic1) an.
"""

import asyncio
import numpy as np
import sys


async def live_meter():
    print("🎤 LIVE AUDIO METER — Focusrite Mic1 (XLR)")
    print("   Drücke Ctrl+C zum Beenden")
    print("   💡 Drehe den GAIN-Regler am Focusrite hoch!")
    print()
    
    proc = await asyncio.create_subprocess_exec(
        "arecord", "-D", "hw:0,0", "-f", "S32_LE", "-r", "48000", "-c", "2", "-t", "raw",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    
    chunk_size = 48000 * 4 * 2 // 10  # 100ms bei 48kHz stereo S32
    max_rms = 0.0
    
    try:
        while True:
            data = await proc.stdout.read(chunk_size)
            if not data:
                break
            raw = np.frombuffer(data, dtype=np.int32).astype(np.float32) / 2147483648.0
            audio = raw[0::2]  # Mic1 = linker Kanal
            rms = np.sqrt(np.mean(audio**2))
            peak = np.max(np.abs(audio))
            max_rms = max(max_rms, rms)
            
            bars = int(rms * 400)
            bar = "█" * min(bars, 50)
            
            if peak > 0.95:
                status = " 🔴 CLIP!"
            elif rms > 0.05:
                status = " ✅ GOOD"
            elif rms > 0.01:
                status = " ⚠️  LOW"
            else:
                status = " ❌ SILENT"
            
            sys.stdout.write(f"\r  [{bar:<50}] RMS: {rms:.4f} Peak: {peak:.4f}{status}  ")
            sys.stdout.flush()
            
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        await proc.wait()
        print(f"\n\n  📊 Max RMS während Session: {max_rms:.4f}")
        if max_rms < 0.005:
            print("  ❌ Kein Signal! Mikrofon angeschlossen? Phantom Power an?")
        elif max_rms < 0.02:
            print("  ⚠️ Signal sehr schwach. GAIN am Focusrite erhöhen!")
        else:
            print("  ✅ Signal OK!")
        print()


if __name__ == "__main__":
    asyncio.run(live_meter())
