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
    action_tag: Optional[str] = None  # Fertiger [ACTION:...] String, sofort executable


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
    # Licht AN/AUS + Helligkeit in einem Satz (Compound Command)
    (
        "light_brightness_compound",
        re.compile(
            r"(?:mach|schalte?)?\s*(?:das?\s+)?(?:licht|lampe)\s*"
            r"(?:im?|in)?\s*(?:der?n?\s+)?(?P<room>\w+)?\s*"
            r"(?P<action>an|ein|on)\s+"
            r"(?:und\s+)?(?:stell|mach|setz)?\s*(?:die\s+)?(?:helligkeit\s+)?(?:auf\s+)?"
            r"(?P<value>\d+)\s*(?:prozent|percent|%)?",
            re.IGNORECASE,
        ),
        {"domain": "light"},
    ),
    # Helligkeit auf X% (direkt)
    (
        "brightness_set",
        re.compile(
            r"(?:stell|setz|mach)?\s*(?:die\s+)?(?:helligkeit|licht)\s*"
            r"(?:im?|in)?\s*(?:der?n?\s+)?(?P<room>\w+)?\s*"
            r"(?:auf\s+)?(?P<value>\d+)\s*(?:prozent|percent|%)",
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
    # Musik / Audio — erweiterte Patterns
    # WICHTIG: \b Word Boundaries verhindern False Positives!
    # "spiel" darf NICHT "Beispiel" matchen, "weiter" nicht "Weiterleitung"
    (
        "media_control",
        re.compile(
            r"\b(?P<action>spiel|play|stopp?|stop|pause|skip|weiter|next|nächst)\b"
            r"(?:e?s?\s+(?P<device>lied|song|musik|music|audio|track))?"
            r"(?:\s+(?:im?|in)\s+(?:der?n?\s+)?(?P<room>\w+))?",
            re.IGNORECASE,
        ),
        {"domain": "media_player"},
    ),
    # Timer / Erinnerung
    (
        "timer_set",
        re.compile(
            r"(?:stell|setz|mach)\s*(?:mir\s+)?(?:einen?\s+)?"
            r"(?P<device>timer|wecker|erinnerung|alarm)"
            r"(?:\s+(?:auf|in|für|von))?\s*(?P<value>\d+)\s*"
            r"(?P<action>minute|minuten|sekunde|sekunden|stunde|stunden|min|sec|sek)?",
            re.IGNORECASE,
        ),
        {"domain": "timer"},
    ),
    # Erinnerung v2 ("erinnere mich in 5 minuten")
    (
        "timer_set",
        re.compile(
            r"erinner(?:e|st)?\s+(?:mich\s+)?(?:in\s+)?(?P<value>\d+)\s*"
            r"(?P<action>minute|minuten|sekunde|sekunden|stunde|stunden|min|sec)?"
            r"(?:\s+(?:an\s+)?(?P<device>.+))?",
            re.IGNORECASE,
        ),
        {"domain": "timer"},
    ),
    # Lautstärke
    (
        "volume_control",
        re.compile(
            r"(?:mach|stell|dreh)?\s*(?:die?\s+)?"
            r"(?:lautstärke|volume|ton)?\s*"
            r"(?P<action>lauter|leiser|louder|quieter|mute|stumm|hoch|runter)"
            r"(?:\s+(?:auf\s+)?(?P<value>\d+))?",
            re.IGNORECASE,
        ),
        {"domain": "volume"},
    ),
    # Nächstes/Vorheriges Lied (direkt)
    (
        "media_next",
        re.compile(
            r"\b(?:nächst(?:es|er)?|next|skip)\b\s*(?:lied|song|track|titel)?",
            re.IGNORECASE,
        ),
        {"domain": "media_player"},
    ),
    (
        "media_prev",
        re.compile(
            r"\b(?:vorherig(?:es|er)?|previous|zurück)\b\s*(?:lied|song|track|titel)?",
            re.IGNORECASE,
        ),
        {"domain": "media_player"},
    ),
    # Musik stoppen
    (
        "media_stop",
        re.compile(
            r"(?:\b(?:musik|music|audio)\b)?\s*\b(?:aus|stopp?|stop|aufhören|beenden|halt)\b",
            re.IGNORECASE,
        ),
        {"domain": "media_player"},
    ),
    # Musik pausieren
    (
        "media_pause",
        re.compile(
            r"(?:\b(?:musik|music)\b)?\s*\bpause\b",
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
    "light_brightness_compound": {
        "default": "Licht ist an{room_suffix}, Helligkeit angepasst. 💡",
    },
    "brightness_set": {
        "default": "Helligkeit eingestellt{room_suffix}. 💡",
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
        """Parse Text in einen strukturierten Intent mit fertigem Action Tag.
        
        Heuristik: Lange Sätze (>8 Wörter) ohne Device-Keyword sind
        fast nie simple Device-Commands → Confidence senken.
        """
        from brain_core.engines.nano_action_map import intent_to_action_tag

        word_count = len(text.split())

        for intent_name, pattern, meta in INTENT_PATTERNS:
            match = pattern.search(text)
            if match:
                groups = match.groupdict()
                action = (
                    groups.get("action")
                    or groups.get("action2")
                    or "default"
                )

                # Device-Keyword vorhanden? (lied, musik, licht, heizung, etc.)
                has_device = bool(groups.get("device"))
                has_room = bool(groups.get("room"))

                # Basis-Confidence
                confidence = 0.85

                # Lange Sätze ohne Device-Keyword → wahrscheinlich kein Command
                if word_count > 8 and not has_device:
                    confidence = 0.3  # Unter Threshold → wird ignoriert
                elif word_count > 5 and not has_device and not has_room:
                    confidence = 0.55  # Knapp über Threshold, riskant

                intent = ParsedIntent(
                    intent=intent_name,
                    device=groups.get("device"),
                    room=groups.get("room"),
                    action=action.lower() if action else None,
                    value=groups.get("value"),
                    confidence=confidence,
                )
                # Action Tag generieren
                intent.action_tag = intent_to_action_tag(intent)
                return intent
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
