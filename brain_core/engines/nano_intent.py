"""
SOMA-AI Nano Intent Engine
============================
Lokale Python-basierte Intent-Erkennung.
Kein LLM benötigt – Regex + Pattern Matching für Device-Control.
Wird bei HIGH Load oder für einfache Commands genutzt.

Datenfluss:
  "Licht an im Wohnzimmer" ──► NanoIntentEngine.generate()
                                     │
                                     ├─ Intent-Parsing (Regex)
                                     ├─ Slot-Extraction (Raum, Gerät, Aktion)
                                     └─ Action-Response (kein LLM!)
"""

from __future__ import annotations

import re
from typing import Optional
from dataclasses import dataclass

import structlog

from brain_core.engines.base_engine import BaseEngine

logger = structlog.get_logger("soma.engine.nano")


@dataclass
class ParsedIntent:
    """Erkannter Intent mit extrahierten Slots."""
    intent: str
    device: Optional[str] = None
    room: Optional[str] = None
    action: Optional[str] = None
    value: Optional[str] = None
    confidence: float = 0.0


# ── Intent Patterns ──────────────────────────────────────────────────────

INTENT_PATTERNS: list[tuple[str, re.Pattern, dict]] = [
    # Licht
    (
        "light_control",
        re.compile(
            r"(?P<action>mach|schalte?|turn|switch)?\s*"
            r"(?:das?\s+)?(?P<device>licht|lampe|light|lamp)"
            r"(?:\s+(?P<action2>an|aus|ein|on|off))?"
            r"(?:\s+(?:im?|in|für)\s+(?:der?n?\s+)?(?P<room>\w+))?",
            re.IGNORECASE,
        ),
        {"domain": "light"},
    ),
    # Helligkeit
    (
        "brightness_control",
        re.compile(
            r"(?:mach|stell)?\s*(?:das?\s+)?(?:licht|lampe)\s+"
            r"(?P<action>heller|dunkler|brighter|dimmer)"
            r"(?:\s+(?:im?|in)\s+(?:der?n?\s+)?(?P<room>\w+))?",
            re.IGNORECASE,
        ),
        {"domain": "light"},
    ),
    # Heizung / Temperatur
    (
        "thermostat_control",
        re.compile(
            r"(?:mach|stell|dreh)?\s*(?:die?\s+)?"
            r"(?P<device>heizung|thermostat|heating|temperature|temperatur)\s*"
            r"(?P<action>an|aus|auf|hoch|runter|wärmer|kälter|on|off)?"
            r"(?:\s+(?P<value>\d+)\s*(?:grad|°|degrees)?)?"
            r"(?:\s+(?:im?|in)\s+(?:der?n?\s+)?(?P<room>\w+))?",
            re.IGNORECASE,
        ),
        {"domain": "climate"},
    ),
    # Einfache Statusfrage
    (
        "status_query",
        re.compile(
            r"(?:was\s+ist|wie\s+ist|what'?s?\s+(?:is\s+)?the)\s+"
            r"(?:die?\s+)?(?P<device>temperatur|temperature|licht|light|status)"
            r"(?:\s+(?:im?|in)\s+(?:der?n?\s+)?(?P<room>\w+))?",
            re.IGNORECASE,
        ),
        {"domain": "sensor"},
    ),
    # Musik / Audio
    (
        "media_control",
        re.compile(
            r"(?:spiel|play|stopp?|stop|pause|skip|weiter|next)\s*"
            r"(?P<device>musik|music|song|audio)?"
            r"(?:\s+(?:im?|in)\s+(?:der?n?\s+)?(?P<room>\w+))?",
            re.IGNORECASE,
        ),
        {"domain": "media_player"},
    ),
]

# ── Response Templates ───────────────────────────────────────────────────

RESPONSES: dict[str, dict[str, str]] = {
    "light_control": {
        "an": "Licht ist an{room_suffix}. 💡",
        "aus": "Licht ist aus{room_suffix}. 🌙",
        "ein": "Licht ist an{room_suffix}. 💡",
        "on": "Light is on{room_suffix}. 💡",
        "off": "Light is off{room_suffix}. 🌙",
        "default": "Licht wird gesteuert{room_suffix}.",
    },
    "brightness_control": {
        "heller": "Licht wird heller{room_suffix}. ☀️",
        "dunkler": "Licht wird dunkler{room_suffix}. 🌘",
        "brighter": "Light is getting brighter{room_suffix}. ☀️",
        "dimmer": "Light is dimming{room_suffix}. 🌘",
        "default": "Helligkeit angepasst{room_suffix}.",
    },
    "thermostat_control": {
        "default": "Heizung wird gesteuert{room_suffix}. 🌡️",
    },
    "status_query": {
        "default": "Status wird abgerufen{room_suffix}... 📊",
    },
    "media_control": {
        "default": "Audio-Steuerung{room_suffix}. 🎵",
    },
}


class NanoIntentEngine(BaseEngine):
    """
    Regex-basierte Intent-Engine.
    Zero-Latency, Zero-VRAM – pure Python.
    """

    def __init__(self):
        super().__init__(name="nano")

    async def initialize(self) -> None:
        logger.info("nano_engine_init", patterns=len(INTENT_PATTERNS))

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """Parse Intent und generiere direkte Antwort."""
        intent = self.parse_intent(prompt)

        if intent and intent.confidence > 0.5:
            response = self._build_response(intent)
            logger.info(
                "nano_intent_matched",
                intent=intent.intent,
                device=intent.device,
                room=intent.room,
                action=intent.action,
                confidence=intent.confidence,
            )
            return response

        # Kein Intent erkannt → Fallback-Antwort
        logger.info("nano_no_intent", prompt=prompt[:50])
        return (
            "Das habe ich nicht ganz verstanden. "
            "Kannst du es anders formulieren? "
            "Ich kann Licht, Heizung und Musik steuern."
        )

    def parse_intent(self, text: str) -> Optional[ParsedIntent]:
        """Parse Text in einen strukturierten Intent."""
        for intent_name, pattern, meta in INTENT_PATTERNS:
            match = pattern.search(text)
            if match:
                groups = match.groupdict()
                action = (
                    groups.get("action")
                    or groups.get("action2")
                    or "default"
                )
                return ParsedIntent(
                    intent=intent_name,
                    device=groups.get("device"),
                    room=groups.get("room"),
                    action=action.lower() if action else None,
                    value=groups.get("value"),
                    confidence=0.85,
                )
        return None

    @staticmethod
    def _build_response(intent: ParsedIntent) -> str:
        """Baue Antwort aus Templates."""
        templates = RESPONSES.get(intent.intent, {"default": "Erledigt."})
        template = templates.get(intent.action or "default", templates["default"])

        room_suffix = f" im {intent.room.title()}" if intent.room else ""
        return template.format(room_suffix=room_suffix)

    async def health_check(self) -> bool:
        return True  # Immer verfügbar – kein externer Service
