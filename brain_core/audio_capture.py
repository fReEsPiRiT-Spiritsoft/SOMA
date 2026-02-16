"""
SOMA-AI Audio Capture Service
===============================
Erfasst Live-Audio von lokalen Mikrofonen (z.B. Focusrite Scarlett + Rode)
und speist die Daten in das SOMA-Nervensystem ein.

Datenfluss:
  Rode Mic → Focusrite Scarlett → PipeWire/ALSA → AudioCapture
    → Amplitude-Analyse (RMS)
    → PresenceManager via MQTT (soma/audio/{node_id})
    → PitchAnalyzer (Child Detection / Stress)
    → Optional: STT Pipeline (zukünftig)

Usage:
  python -m brain_core.audio_capture                    # Auto-Detect
  python -m brain_core.audio_capture --list             # Geräte auflisten
  python -m brain_core.audio_capture --device 0         # Bestimmtes Gerät
  python -m brain_core.audio_capture --device focusrite # Nach Name suchen
"""

from __future__ import annotations

import argparse
import asyncio
import json
import struct
import sys
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import structlog

logger = structlog.get_logger("soma.audio_capture")

# ── Audio Constants ──────────────────────────────────────────────────────

DEFAULT_RATE = 48000       # Focusrite Scarlett default
DEFAULT_CHANNELS = 1       # Mono für Sprache
DEFAULT_CHUNK = 2048       # ~42ms bei 48kHz – gut für Echtzeit
AMPLITUDE_THRESHOLD = 0.01 # Unter diesem RMS = Stille
PUBLISH_INTERVAL = 0.2     # Sekunden zwischen MQTT-Publishes


@dataclass
class AudioDevice:
    """Erkanntes Audio-Eingabegerät."""
    index: int
    name: str
    channels: int
    sample_rate: int
    alsa_name: str = ""
    is_focusrite: bool = False
    is_default: bool = False


@dataclass
class AudioFrame:
    """Ein Audio-Frame mit Metadaten."""
    data: np.ndarray
    rms: float
    peak: float
    timestamp: float
    device_index: int
    channel: int = 0


class AudioDeviceDetector:
    """
    Erkennt Audio-Eingabegeräte via PipeWire/PulseAudio (pactl)
    und ALSA (arecord). Kein pyaudio nötig!
    """

    @staticmethod
    def list_devices() -> list[AudioDevice]:
        """Erkenne alle verfügbaren Capture-Devices."""
        import subprocess

        devices: list[AudioDevice] = []

        # ── ALSA devices via arecord ─────────────────────────────────
        try:
            result = subprocess.run(
                ["arecord", "-l"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                card_idx = 0
                for line in result.stdout.splitlines():
                    if line.startswith("Karte ") or line.startswith("card "):
                        # Parse: "Karte 0: USB [Scarlett Solo USB], Gerät 0: ..."
                        parts = line.split(":")
                        try:
                            card_num = int(parts[0].split()[-1])
                        except (ValueError, IndexError):
                            card_num = card_idx

                        name = line.split("[")[1].split("]")[0] if "[" in line else parts[1].strip()
                        is_focusrite = any(k in line.lower() for k in [
                            "focusrite", "scarlett", "clarett", "saffire"
                        ])

                        devices.append(AudioDevice(
                            index=card_num,
                            name=name,
                            channels=2,  # Standard, wird unten korrigiert
                            sample_rate=DEFAULT_RATE,
                            alsa_name=f"hw:{card_num},0",
                            is_focusrite=is_focusrite,
                        ))
                        card_idx += 1
        except (subprocess.TimeoutExpired, FileNotFoundError):
            logger.warning("arecord_not_found")

        # ── PipeWire Sources via pactl ───────────────────────────────
        try:
            result = subprocess.run(
                ["pactl", "list", "sources", "short"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 2 and "input" in parts[1].lower():
                        pw_name = parts[1]
                        # Matche mit ALSA device
                        for dev in devices:
                            if dev.is_focusrite and "focusrite" in pw_name.lower():
                                if "Mic1" in pw_name:
                                    dev.name = f"{dev.name} (Mic1/XLR)"
                                    dev.channels = 1
                                elif "Mic2" in pw_name:
                                    # Zweiter Eingang separat
                                    devices.append(AudioDevice(
                                        index=dev.index + 100,
                                        name=f"{dev.name.split(' (')[0]} (Mic2/Instrument)",
                                        channels=1,
                                        sample_rate=dev.sample_rate,
                                        alsa_name=pw_name,
                                        is_focusrite=True,
                                    ))
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        # ── Markiere Default ─────────────────────────────────────────
        if devices:
            # Focusrite bevorzugen
            for dev in devices:
                if dev.is_focusrite and "Mic1" in dev.name:
                    dev.is_default = True
                    break
            else:
                devices[0].is_default = True

        return devices

    @staticmethod
    def get_default_device() -> Optional[AudioDevice]:
        """Gibt das bevorzugte Capture-Device zurück (Focusrite Mic1)."""
        devices = AudioDeviceDetector.list_devices()
        for dev in devices:
            if dev.is_default:
                return dev
        return devices[0] if devices else None

    @staticmethod
    def find_device(query: str) -> Optional[AudioDevice]:
        """Suche Gerät nach Name oder Index."""
        devices = AudioDeviceDetector.list_devices()
        # Per Index
        try:
            idx = int(query)
            for dev in devices:
                if dev.index == idx:
                    return dev
        except ValueError:
            pass
        # Per Name (case-insensitive substring)
        query_lower = query.lower()
        for dev in devices:
            if query_lower in dev.name.lower() or query_lower in dev.alsa_name.lower():
                return dev
        return None


class AudioCapture:
    """
    Echtzeit-Audio-Capture via ALSA subprocess (arecord).
    Kein pyaudio/portaudio nötig — nutzt direkt arecord via PipeWire.

    Sendet Audio-Metriken (RMS, Peak, F0) an den Brain Core
    via MQTT-Topic: soma/audio/{node_id}
    """

    def __init__(
        self,
        device: AudioDevice,
        node_id: str = "mic_dev_rode_01",
        room_id: str = "arbeitszimmer",
        sample_rate: int = DEFAULT_RATE,
        channels: int = DEFAULT_CHANNELS,
        chunk_size: int = DEFAULT_CHUNK,
    ):
        self.device = device
        self.node_id = node_id
        self.room_id = room_id
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self._process: Optional[asyncio.subprocess.Process] = None
        self._running = False
        self._mqtt_client = None
        self._stats = {
            "frames_captured": 0,
            "silence_frames": 0,
            "voice_frames": 0,
            "peak_rms": 0.0,
            "start_time": 0.0,
        }

    async def start(
        self,
        on_audio: Optional[callable] = None,
        mqtt_host: str = "localhost",
        mqtt_port: int = 1883,
    ):
        """
        Starte Audio-Capture als async subprocess.
        Audio wird via arecord gelesen (PipeWire-kompatibel).
        """
        import aiomqtt

        self._running = True
        self._stats["start_time"] = time.monotonic()

        # MQTT verbinden
        try:
            self._mqtt_client = aiomqtt.Client(
                hostname=mqtt_host,
                port=mqtt_port,
                identifier=f"soma-audio-{self.node_id}",
            )
            await self._mqtt_client.__aenter__()
            logger.info("mqtt_connected", node_id=self.node_id)
        except Exception as e:
            logger.warning("mqtt_connect_failed", error=str(e))
            self._mqtt_client = None

        # ── Capture-Kommando bestimmen ────────────────────────────────
        # Priorität: 1) arecord via PipeWire (universell)
        #            2) parec (PulseAudio/PipeWire native)
        #
        # Focusrite Scarlett: Nativ S32_LE/2ch.
        # Wir nutzen `arecord -D default` — PipeWire konvertiert
        # automatisch Format und Channels via ALSA-Plugin.

        if self.device.is_focusrite:
            # Focusrite: Raw ALSA mit nativen Settings, dann Software-Konvertierung
            cmd = [
                "arecord",
                "-D", f"hw:{self.device.index},0",
                "-f", "S32_LE",          # Focusrite nativ: 32-bit
                "-r", str(self.sample_rate),
                "-c", "2",               # Focusrite Solo: stereo capture
                "-t", "raw",
                "--buffer-size", str(self.chunk_size * 4),
            ]
            self._native_format = "s32"
            self._native_channels = 2
        else:
            # Andere Geräte: Via PipeWire default (automatische Konvertierung)
            cmd = [
                "arecord",
                "-D", "default",
                "-f", "S16_LE",
                "-r", str(self.sample_rate),
                "-c", str(self.channels),
                "-t", "raw",
                "--buffer-size", str(self.chunk_size * 4),
            ]
            self._native_format = "s16"
            self._native_channels = self.channels

        logger.info(
            "capture_starting",
            device=self.device.name,
            alsa=self.device.alsa_name,
            rate=self.sample_rate,
            channels=self.channels,
            cmd=" ".join(cmd),
        )

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.error("arecord_not_installed")
            raise RuntimeError("arecord nicht gefunden — installiere: sudo pacman -S alsa-utils")

        # Bytes pro Chunk hängen vom Format ab
        if self._native_format == "s32":
            bytes_per_sample = 4
        else:
            bytes_per_sample = 2
        bytes_per_chunk = self.chunk_size * self._native_channels * bytes_per_sample
        last_publish = 0.0

        logger.info("capture_running", node_id=self.node_id, room=self.room_id)

        try:
            while self._running and self._process.returncode is None:
                data = await self._process.stdout.read(bytes_per_chunk)
                if not data:
                    break

                # PCM → numpy float32 [-1.0, 1.0]
                if self._native_format == "s32":
                    # S32_LE: 32-bit signed → float32
                    raw_samples = np.frombuffer(data, dtype=np.int32).astype(np.float32) / 2147483648.0
                    # Stereo → Mono: Kanal 1 = XLR/Rode Mic
                    if self._native_channels == 2:
                        samples = raw_samples[0::2]  # Nur linker Kanal (Mic1/XLR)
                    else:
                        samples = raw_samples
                else:
                    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0

                # Metriken berechnen
                rms = float(np.sqrt(np.mean(samples ** 2)))
                peak = float(np.max(np.abs(samples)))
                self._stats["frames_captured"] += 1
                self._stats["peak_rms"] = max(self._stats["peak_rms"], rms)

                if rms < AMPLITUDE_THRESHOLD:
                    self._stats["silence_frames"] += 1
                else:
                    self._stats["voice_frames"] += 1

                frame = AudioFrame(
                    data=samples,
                    rms=rms,
                    peak=peak,
                    timestamp=time.time(),
                    device_index=self.device.index,
                )

                # Callback (für lokale Verarbeitung)
                if on_audio and rms >= AMPLITUDE_THRESHOLD:
                    try:
                        await on_audio(frame)
                    except Exception as e:
                        logger.warning("audio_callback_error", error=str(e))

                # MQTT publish (gedrosselt)
                now = time.monotonic()
                if now - last_publish >= PUBLISH_INTERVAL:
                    last_publish = now
                    await self._publish_metrics(rms, peak, frame.timestamp)

        except asyncio.CancelledError:
            logger.info("capture_cancelled")
        finally:
            await self.stop()

    async def _publish_metrics(self, rms: float, peak: float, timestamp: float):
        """Sende Audio-Metriken via MQTT an den Brain Core."""
        payload = {
            "node_id": self.node_id,
            "room_id": self.room_id,
            "rms": round(rms, 6),
            "peak": round(peak, 6),
            "is_voice": rms >= AMPLITUDE_THRESHOLD,
            "sample_rate": self.sample_rate,
            "timestamp": timestamp,
        }

        if self._mqtt_client:
            try:
                await self._mqtt_client.publish(
                    f"soma/audio/{self.node_id}",
                    json.dumps(payload),
                    qos=0,  # Fire-and-forget für Audio-Metriken
                )
            except Exception as e:
                logger.debug("mqtt_publish_failed", error=str(e))

    async def stop(self):
        """Audio-Capture stoppen."""
        self._running = False
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=3)
            except asyncio.TimeoutError:
                self._process.kill()
            logger.info("capture_stopped", node_id=self.node_id)

        if self._mqtt_client:
            try:
                await self._mqtt_client.__aexit__(None, None, None)
            except Exception:
                pass

        # Stats ausgeben
        elapsed = time.monotonic() - self._stats["start_time"]
        total = self._stats["frames_captured"]
        logger.info(
            "capture_stats",
            duration_sec=round(elapsed, 1),
            total_frames=total,
            voice_frames=self._stats["voice_frames"],
            silence_frames=self._stats["silence_frames"],
            voice_ratio=round(self._stats["voice_frames"] / max(total, 1), 2),
            peak_rms=round(self._stats["peak_rms"], 4),
        )

    def get_stats(self) -> dict:
        """Aktuelle Capture-Statistiken."""
        return dict(self._stats)


class AudioCaptureWithAnalysis(AudioCapture):
    """
    Erweiterte Capture mit Live Pitch-Analyse.
    Erkennt: Kinderstimme, Stress-Level, Grundfrequenz.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pitch_buffer: list[float] = []
        self._pitch_window = 10  # Letzte 10 F0-Werte mitteln

    async def _analyze_frame(self, frame: AudioFrame):
        """Pitch-Analyse auf dem Audio-Frame."""
        from brain_core.safety.pitch_analyzer import PitchAnalyzer

        if frame.rms < AMPLITUDE_THRESHOLD * 2:
            return  # Zu leise für Pitch-Analyse

        f0 = PitchAnalyzer.estimate_f0(
            frame.data,
            self.sample_rate,
        )

        if f0 and 50 < f0 < 600:
            self._pitch_buffer.append(f0)
            if len(self._pitch_buffer) > self._pitch_window:
                self._pitch_buffer.pop(0)

            avg_f0 = np.mean(self._pitch_buffer)
            is_child = PitchAnalyzer.is_child_voice(avg_f0)
            stress = PitchAnalyzer.estimate_stress(frame.data)

            # Erweiterte MQTT-Payload
            if self._mqtt_client:
                analysis = {
                    "node_id": self.node_id,
                    "room_id": self.room_id,
                    "f0": round(float(f0), 1),
                    "f0_avg": round(float(avg_f0), 1),
                    "is_child": is_child,
                    "stress_level": round(stress, 2),
                    "timestamp": frame.timestamp,
                }
                try:
                    await self._mqtt_client.publish(
                        f"soma/audio/{self.node_id}/analysis",
                        json.dumps(analysis),
                        qos=0,
                    )
                except Exception:
                    pass


# ── CLI ──────────────────────────────────────────────────────────────────

def print_devices():
    """Zeige alle erkannten Audio-Geräte."""
    devices = AudioDeviceDetector.list_devices()
    if not devices:
        print("❌ Keine Audio-Eingabegeräte gefunden.")
        return

    print("\n🎤 Erkannte Audio-Eingabegeräte:\n")
    print(f"  {'Idx':>4}  {'Name':<40}  {'ALSA/PW':>30}  {'Rate':>6}  {'Ch':>3}")
    print(f"  {'─'*4}  {'─'*40}  {'─'*30}  {'─'*6}  {'─'*3}")

    for dev in devices:
        marker = " ★" if dev.is_default else "  "
        focusrite = " 🎚️" if dev.is_focusrite else ""
        print(f"{marker}{dev.index:>4}  {dev.name:<40}  {dev.alsa_name:>30}  {dev.sample_rate:>6}  {dev.channels:>3}{focusrite}")

    print(f"\n  ★ = Standard-Gerät  🎚️ = Focusrite Interface\n")


async def run_capture(args):
    """Hauptfunktion: Audio-Capture starten."""

    # Device finden
    if args.device:
        device = AudioDeviceDetector.find_device(args.device)
        if not device:
            print(f"❌ Gerät '{args.device}' nicht gefunden.")
            print_devices()
            return
    else:
        device = AudioDeviceDetector.get_default_device()
        if not device:
            print("❌ Kein Audio-Eingabegerät gefunden.")
            return

    print(f"\n🎤 Starte Capture: {device.name}")
    print(f"   ALSA: {device.alsa_name}")
    print(f"   Rate: {device.sample_rate} Hz, Channels: {device.channels}")
    print(f"   Node: {args.node_id} / Room: {args.room_id}")
    print(f"   MQTT: {args.mqtt_host}:{args.mqtt_port}")
    print(f"\n   Drücke Ctrl+C zum Stoppen.\n")

    # Capture mit oder ohne Pitch-Analyse
    if args.analyze:
        capture = AudioCaptureWithAnalysis(
            device=device,
            node_id=args.node_id,
            room_id=args.room_id,
            sample_rate=args.rate,
            channels=device.channels,
        )
        on_audio = capture._analyze_frame
    else:
        capture = AudioCapture(
            device=device,
            node_id=args.node_id,
            room_id=args.room_id,
            sample_rate=args.rate,
            channels=device.channels,
        )
        on_audio = None

    # Live-Level-Meter im Terminal
    if args.meter:
        async def level_meter(frame: AudioFrame):
            bars = int(frame.rms * 200)
            bar_str = "█" * min(bars, 50)
            voice = "🗣️" if frame.rms >= AMPLITUDE_THRESHOLD else "  "
            sys.stdout.write(f"\r  {voice} [{bar_str:<50}] RMS: {frame.rms:.4f}  Peak: {frame.peak:.4f}  ")
            sys.stdout.flush()
            if on_audio:
                await on_audio(frame)

        callback = level_meter
    else:
        callback = on_audio

    try:
        await capture.start(
            on_audio=callback,
            mqtt_host=args.mqtt_host,
            mqtt_port=args.mqtt_port,
        )
    except KeyboardInterrupt:
        print("\n")
        await capture.stop()


def main():
    parser = argparse.ArgumentParser(
        description="SOMA-AI Audio Capture – Rode/Focusrite Integration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  python -m brain_core.audio_capture --list
  python -m brain_core.audio_capture --meter
  python -m brain_core.audio_capture --device focusrite --analyze --meter
  python -m brain_core.audio_capture --device 0 --room wohnzimmer
        """,
    )
    parser.add_argument("--list", action="store_true", help="Zeige alle Audio-Geräte")
    parser.add_argument("--device", "-d", type=str, default=None, help="Gerät (Index oder Name)")
    parser.add_argument("--node-id", type=str, default="mic_dev_rode_01", help="SOMA Node-ID")
    parser.add_argument("--room", "--room-id", dest="room_id", type=str, default="arbeitszimmer", help="Raum-ID")
    parser.add_argument("--rate", "-r", type=int, default=DEFAULT_RATE, help="Sample-Rate in Hz")
    parser.add_argument("--meter", "-m", action="store_true", help="Live Level-Meter im Terminal")
    parser.add_argument("--analyze", "-a", action="store_true", help="Pitch-Analyse aktivieren (Child/Stress)")
    parser.add_argument("--mqtt-host", type=str, default="localhost", help="MQTT Broker Host")
    parser.add_argument("--mqtt-port", type=int, default=1883, help="MQTT Broker Port")

    args = parser.parse_args()

    if args.list:
        print_devices()
        return

    asyncio.run(run_capture(args))


if __name__ == "__main__":
    main()
