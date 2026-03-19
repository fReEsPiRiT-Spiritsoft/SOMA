"""Plugin: Systemauslastungsanalyse"""
__version__ = "0.1.0"
__author__ = "soma-ai"
__description__ = "Analysiert CPU- und RAM-Auslastung und listet Top-Prozesse bei hoher Last"

import asyncio
import logging

logger = logging.getLogger("soma.plugin.anhand_systemauslastung_dann")

async def on_load() -> None:
    logger.info("anhand_systemauslastung_dann geladen")

async def _read_cpu_stats() -> tuple:
    try:
        with open("/proc/stat", "r") as f:
            line = f.readline().split()
        values = list(map(int, line[1:]))
        idle = values[3] + values[4]
        total = sum(values)
        return idle, total
    except Exception as e:
        logger.error(f"Fehler beim Lesen von /proc/stat: {e}")
        raise

async def _read_mem_stats() -> tuple:
    try:
        total = avail = 0
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) * 1024
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) * 1024
                if total and avail:
                    break
        return total, avail
    except Exception as e:
        logger.error(f"Fehler beim Lesen von /proc/meminfo: {e}")
        raise

async def execute(threshold_cpu: float = 80.0) -> str:
    try:
        idle1, total1 = await _read_cpu_stats()
        await asyncio.sleep(0.5)
        idle2, total2 = await _read_cpu_stats()
        idle_delta = idle2 - idle1
        total_delta = total2 - total1
        cpu_usage = 100.0 * (1 - idle_delta / total_delta) if total_delta > 0 else 0.0

        total_mem, avail_mem = await _read_mem_stats()
        used_mem = total_mem - avail_mem
        mem_usage = 100.0 * used_mem / total_mem if total_mem > 0 else 0.0

        result = f"CPU-Auslastung: {cpu_usage:.1f}%  RAM-Auslastung: {mem_usage:.1f}%"
        if cpu_usage >= threshold_cpu:
            proc = await asyncio.create_subprocess_exec(
                "ps", "-eo", "pid,comm,%cpu,%mem", "--sort=-%cpu",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                lines = stdout.decode().strip().splitlines()
                top_procs = "\n".join(lines[:6])
                result += f"\nHohe CPU-Auslastung erkannt, Top-Prozesse:\n{top_procs}"
            else:
                err = stderr.decode().strip()
                result += f"\nFehler beim Abrufen der Prozesse: {err}"
        return result
    except FileNotFoundError:
        return "Benötigte Systembefehle oder Dateien nicht gefunden."
    except Exception as e:
        logger.error(f"Fehler in execute: {e}")
        return f"Fehler: {e}"

async def on_unload() -> None:
    logger.info("anhand_systemauslastung_dann entladen")