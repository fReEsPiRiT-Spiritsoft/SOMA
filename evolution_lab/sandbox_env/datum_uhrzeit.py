"""Plugin: Datum und Uhrzeit"""
__version__ = "0.1.0"
__author__ = "soma-ai"
__description__ = "Zeigt das aktuelle Datum und die Uhrzeit an"

import datetime
from structlog import get_logger

logger = get_logger("soma.plugin.datetime")

async def on_load():
    logger.info("datetime_plugin_loaded")

async def execute() -> str:
    now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    return f"Das aktuelle Datum ist: {now}"