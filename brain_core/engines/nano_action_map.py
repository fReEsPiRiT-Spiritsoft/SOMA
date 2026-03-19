"""
SOMA-AI Nano Action Map
=========================
Mapping: ParsedIntent → [ACTION:...] String.
Wandelt Nano-Intent-Ergebnisse in sofort ausführbare Action Tags um.
Kein LLM nötig — pure Python, <1ms.
"""

from __future__ import annotations

from typing import Optional
from brain_core.engines.nano_intent import ParsedIntent

import structlog

logger = structlog.get_logger("soma.nano_action_map")


# ── Room → Entity Mapping (HA Convention) ────────────────────────────────
# Fallback-Mapping wenn HA-Bridge nicht verfügbar. Wird durch echte
# HA-Entity-Registry überschrieben wenn vorhanden.

ROOM_LIGHT_ENTITIES = {
    "wohnzimmer": "light.wohnzimmer",
    "schlafzimmer": "light.schlafzimmer",
    "küche": "light.kuche",
    "kueche": "light.kuche",
    "bad": "light.bad",
    "badezimmer": "light.bad",
    "flur": "light.flur",
    "büro": "light.buero",
    "buero": "light.buero",
    "kinderzimmer": "light.kinderzimmer",
    "esszimmer": "light.esszimmer",
    "keller": "light.keller",
    "garage": "light.garage",
}

ROOM_CLIMATE_ENTITIES = {
    "wohnzimmer": "climate.wohnzimmer",
    "schlafzimmer": "climate.schlafzimmer",
    "küche": "climate.kuche",
    "kueche": "climate.kuche",
    "bad": "climate.bad",
    "badezimmer": "climate.bad",
    "kinderzimmer": "climate.kinderzimmer",
    "büro": "climate.buero",
    "buero": "climate.buero",
}


def intent_to_action_tag(intent: ParsedIntent) -> Optional[str]:
    """
    Wandle einen ParsedIntent in einen fertigen [ACTION:...] Tag um.

    Returns: Fertiger Action-Tag String oder None wenn kein Mapping möglich.
    """
    if intent.intent == "light_control":
        return _map_light_control(intent)
    elif intent.intent == "light_brightness_compound":
        return _map_light_brightness_compound(intent)
    elif intent.intent == "brightness_set":
        return _map_brightness_set(intent)
    elif intent.intent == "brightness_control":
        return _map_brightness_control(intent)
    elif intent.intent == "thermostat_control":
        return _map_thermostat_control(intent)
    elif intent.intent == "media_control":
        return _map_media_control(intent)
    elif intent.intent == "timer_set":
        return _map_timer(intent)
    elif intent.intent == "time_query":
        return None  # Plugin-basiert, kein direkter Action Tag
    elif intent.intent == "media_next":
        return '[ACTION:media_next]'
    elif intent.intent == "media_prev":
        return '[ACTION:media_prev]'
    elif intent.intent == "media_pause":
        return '[ACTION:media_pause]'
    elif intent.intent == "media_resume":
        return '[ACTION:media_resume]'
    elif intent.intent == "media_stop":
        return '[ACTION:media_stop]'
    elif intent.intent == "volume_control":
        return _map_volume_control(intent)
    return None


def _map_light_control(intent: ParsedIntent) -> Optional[str]:
    """Light on/off → [ACTION:ha_call ...]"""
    action = (intent.action or "").lower()
    room = (intent.room or "").lower()

    entity = ROOM_LIGHT_ENTITIES.get(room, f"light.{room}" if room else "light.wohnzimmer")

    if action in ("an", "ein", "on"):
        return f'[ACTION:ha_call domain="light" service="turn_on" entity_id="{entity}"]'
    elif action in ("aus", "off"):
        return f'[ACTION:ha_call domain="light" service="turn_off" entity_id="{entity}"]'
    elif action == "default":
        # Kein Aktionswort → toggle
        return f'[ACTION:ha_call domain="light" service="toggle" entity_id="{entity}"]'
    return None


def _map_light_brightness_compound(intent: ParsedIntent) -> Optional[str]:
    """Licht an + Helligkeit X% → [ACTION:ha_call ... brightness_pct=...]"""
    room = (intent.room or "").lower()
    value = intent.value or "80"
    entity = ROOM_LIGHT_ENTITIES.get(room, f"light.{room}" if room else "light.wohnzimmer")
    return f'[ACTION:ha_call domain="light" service="turn_on" entity_id="{entity}" brightness_pct="{value}"]'


def _map_brightness_set(intent: ParsedIntent) -> Optional[str]:
    """Helligkeit auf X% → [ACTION:ha_call ... brightness_pct=...]"""
    room = (intent.room or "").lower()
    value = intent.value or "50"
    entity = ROOM_LIGHT_ENTITIES.get(room, f"light.{room}" if room else "light.wohnzimmer")
    return f'[ACTION:ha_call domain="light" service="turn_on" entity_id="{entity}" brightness_pct="{value}"]'


def _map_brightness_control(intent: ParsedIntent) -> Optional[str]:
    """Heller/Dunkler → [ACTION:ha_call ... brightness_pct=...]"""
    action = (intent.action or "").lower()
    room = (intent.room or "").lower()

    entity = ROOM_LIGHT_ENTITIES.get(room, f"light.{room}" if room else "light.wohnzimmer")

    if action in ("heller", "brighter"):
        return f'[ACTION:ha_call domain="light" service="turn_on" entity_id="{entity}" brightness_pct="80"]'
    elif action in ("dunkler", "dimmer"):
        return f'[ACTION:ha_call domain="light" service="turn_on" entity_id="{entity}" brightness_pct="20"]'
    return None


def _map_thermostat_control(intent: ParsedIntent) -> Optional[str]:
    """Heizung an/aus/Temperatur → [ACTION:ha_call ...]"""
    action = (intent.action or "").lower()
    room = (intent.room or "").lower()
    value = intent.value

    entity = ROOM_CLIMATE_ENTITIES.get(room, f"climate.{room}" if room else "climate.wohnzimmer")

    if value:
        return f'[ACTION:ha_call domain="climate" service="set_temperature" entity_id="{entity}" temperature="{value}"]'
    elif action in ("an", "on", "hoch", "wärmer"):
        return f'[ACTION:ha_call domain="climate" service="set_temperature" entity_id="{entity}" temperature="22"]'
    elif action in ("aus", "off", "runter", "kälter"):
        return f'[ACTION:ha_call domain="climate" service="set_temperature" entity_id="{entity}" temperature="18"]'
    return None


def _map_media_control(intent: ParsedIntent) -> Optional[str]:
    """Media play/stop/next → [ACTION:media_*]"""
    action = (intent.action or "").lower()

    if action in ("stopp", "stop"):
        return '[ACTION:media_stop]'
    elif action in ("pause",):
        return '[ACTION:media_pause]'
    elif action in ("skip", "next", "weiter"):
        return '[ACTION:media_next]'
    elif action in ("spiel", "play"):
        return '[ACTION:media_resume]'
    return None


def _map_timer(intent: ParsedIntent) -> Optional[str]:
    """Timer/Erinnerung → [ACTION:reminder ...]"""
    value = intent.value
    topic = intent.device or "Timer"
    if value:
        return f'[ACTION:reminder minutes={value} topic="{topic}"]'
    return None


def _map_volume_control(intent: ParsedIntent) -> Optional[str]:
    """Lauter/Leiser → [ACTION:volume ...]"""
    action = (intent.action or "").lower()
    if action in ("lauter", "louder", "hoch"):
        return '[ACTION:volume level="70"]'
    elif action in ("leiser", "quieter", "runter"):
        return '[ACTION:volume level="30"]'
    elif action in ("mute", "stumm"):
        return '[ACTION:volume action="mute"]'
    return None
