"""
Plugin: Kannst-Plugin-Schreiben
"""

__version__ = "0.1.0"
__author__ = "soma-ai"
__description__ = "Ergänzt SOMA-AI um die Möglichkeit, Plugins in datetime.py zu erzeugen"

import datetime
import structlog

logger = structlog.get_logger("soma.plugin.kannst")

async def on_load():
    logger.info("kannst_plugin_loaded")

async def execute() -> str:
    return "Ich kann es!"