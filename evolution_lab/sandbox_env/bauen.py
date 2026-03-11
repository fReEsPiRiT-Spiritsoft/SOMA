"""Plugin: Bauen"""
__version__ = "0.1.0"
__author__ = "soma-ai"
__description__ = "Baut ein neues SOMA-Projekt"

import asyncio
import subprocess
import structlog

logger = structlog.get_logger("soma.plugin.bauen")

__dependencies__ = ["aiohttp"]

async def on_load():
    logger.info("bauen_plugin_loaded")

async def execute(path: str) -> str:
    try:
        if not path:
            return "Bitte einen Ordnerpfad angeben."
        proc = await asyncio.create_subprocess_exec(
            "cargo", "new", path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            return f"Neues Projekt {path} erfolgreich erstellt."
        else:
            return f"Fehler: {stdout.decode().strip()}"
    except Exception as e:
        return f"Fehler: {e}"