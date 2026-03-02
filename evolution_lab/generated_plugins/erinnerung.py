"""
Plugin: Erinnerung / Reminder
==============================
Soma kann Erinnerungen setzen und dich zu bestimmten Zeiten benachrichtigen.

Beispiele:
  - "Soma, erinnere mich um 15:30 an den Termin"
  - "Soma, Erinnerung um 16 Uhr: Müll rausbringen"
  - "Soma, in 10 Minuten: Wasser kochen"
"""
__version__ = "1.0.0"
__author__ = "SOMA Evolution Lab"
__description__ = "Setzt Erinnerungen und benachrichtigt zur angegebenen Zeit"

import asyncio
import re
from datetime import datetime, timedelta
from typing import Optional, Callable, Awaitable
import structlog

logger = structlog.get_logger("soma.plugin.erinnerung")

# Globale Erinnerungsliste (persistent während Runtime)
_reminders: list[dict] = []
_reminder_tasks: dict[str, asyncio.Task] = {}
_speak_callback: Optional[Callable[[str], Awaitable[None]]] = None


def set_speak_callback(callback: Callable[[str], Awaitable[None]]) -> None:
    """Setzt die TTS-Callback-Funktion für Benachrichtigungen."""
    global _speak_callback
    _speak_callback = callback
    logger.info("reminder_speak_callback_set")


async def on_load():
    """Plugin geladen."""
    logger.info("erinnerung_plugin_loaded", active_reminders=len(_reminders))


def parse_time(text: str) -> Optional[datetime]:
    """
    Extrahiert eine Uhrzeit aus dem Text.
    
    Unterstützt:
      - "um 15:30"
      - "um 15 Uhr 30"
      - "um 16 Uhr"
      - "in 10 Minuten"
      - "in einer Stunde"
    """
    text = text.lower()
    now = datetime.now()
    
    # "um HH:MM" oder "um HH Uhr MM"
    match = re.search(r'um\s+(\d{1,2})[:\s]?(?:uhr\s*)?(\d{0,2})', text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2)) if match.group(2) else 0
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        # Wenn Zeit schon vorbei, auf morgen setzen
        if target <= now:
            target += timedelta(days=1)
        return target
    
    # "in X Minuten"
    match = re.search(r'in\s+(\d+)\s*min', text)
    if match:
        minutes = int(match.group(1))
        return now + timedelta(minutes=minutes)
    
    # "in X Sekunden"
    match = re.search(r'in\s+(\d+)\s*sek', text)
    if match:
        seconds = int(match.group(1))
        return now + timedelta(seconds=seconds)
    
    # "in einer Stunde" / "in X Stunden"
    match = re.search(r'in\s+(?:einer?|(\d+))\s*stunde', text)
    if match:
        hours = int(match.group(1)) if match.group(1) else 1
        return now + timedelta(hours=hours)
    
    return None


def parse_topic(text: str) -> str:
    """Extrahiert das Thema/die Nachricht der Erinnerung."""
    text = text.lower()
    
    # Entferne Zeit-Patterns
    text = re.sub(r'um\s+\d{1,2}[:\s]?(?:uhr\s*)?\d{0,2}', '', text)
    text = re.sub(r'in\s+\d+\s*min\w*', '', text)
    text = re.sub(r'in\s+\d+\s*sek\w*', '', text)
    text = re.sub(r'in\s+(?:einer?|\d+)\s*stunde\w*', '', text)
    
    # Entferne Trigger-Wörter
    text = re.sub(r'erinner\w*\s*(mich|uns)?', '', text)
    text = re.sub(r'erinnerung', '', text)
    text = re.sub(r'heute', '', text)
    text = re.sub(r'soma|sommer', '', text)
    text = re.sub(r'an\s+(das|den|die|das)?', ' ', text)  # "an den Termin" -> "Termin"
    text = re.sub(r'[:,\.]', '', text)
    
    # Bereinigen
    text = ' '.join(text.split())
    
    return text.strip() if text.strip() else "Erinnerung"


async def _reminder_worker(reminder_id: str, target_time: datetime, topic: str):
    """Background-Worker der auf die Zeit wartet und dann benachrichtigt."""
    now = datetime.now()
    wait_seconds = (target_time - now).total_seconds()
    
    if wait_seconds <= 0:
        logger.warning("reminder_already_past", id=reminder_id, target=target_time.isoformat())
        return
    
    logger.info(
        "reminder_scheduled",
        id=reminder_id,
        target=target_time.strftime("%H:%M"),
        wait_seconds=round(wait_seconds),
        topic=topic,
    )
    
    await asyncio.sleep(wait_seconds)
    
    # Zeit ist da! Benachrichtigen
    message = f"Hey! Erinnerung: {topic}"
    logger.info("reminder_triggered", id=reminder_id, message=message)
    
    if _speak_callback:
        try:
            await _speak_callback(message)
        except Exception as e:
            logger.error("reminder_speak_failed", error=str(e))
    
    # Aus Liste entfernen
    global _reminders
    _reminders = [r for r in _reminders if r["id"] != reminder_id]
    if reminder_id in _reminder_tasks:
        del _reminder_tasks[reminder_id]


async def set_reminder(text: str) -> str:
    """
    Setzt eine neue Erinnerung basierend auf dem Text.
    
    Args:
        text: Der vollständige Benutzer-Text mit Zeit und Thema
    
    Returns:
        Bestätigungsnachricht
    """
    target_time = parse_time(text)
    if not target_time:
        return "Ich konnte keine Uhrzeit erkennen. Sag zum Beispiel: 'Erinnere mich um 15:30 an den Termin'"
    
    topic = parse_topic(text)
    return await _create_reminder(target_time, topic)


async def set_reminder_from_action(
    topic: str = "Erinnerung",
    seconds: int = None,
    minutes: int = None,
    hours: int = None,
    time_at: str = None,
) -> str:
    """
    Setzt eine Erinnerung direkt aus ACTION-Tag-Parametern.
    Umgeht NLP-Parsing – Werte kommen präzise vom LLM.

    Args:
        topic:   Thema der Erinnerung
        seconds: In wie vielen Sekunden
        minutes: In wie vielen Minuten
        hours:   In wie vielen Stunden
        time_at: Uhrzeitstring "HH:MM"
    """
    from datetime import datetime, timedelta
    now = datetime.now()

    if seconds is not None:
        target_time = now + timedelta(seconds=int(seconds))
    elif minutes is not None:
        target_time = now + timedelta(minutes=int(minutes))
    elif hours is not None:
        target_time = now + timedelta(hours=int(hours))
    elif time_at:
        try:
            h, m = (int(x) for x in time_at.split(":"))
            target_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if target_time <= now:
                target_time += timedelta(days=1)
        except ValueError:
            return f"Ungültige Uhrzeit: {time_at}"
    else:
        return "Keine Zeit angegeben."

    return await _create_reminder(target_time, topic)


async def _create_reminder(target_time, topic: str) -> str:
    """Legt den Reminder-Eintrag an und startet den Background-Task."""
    reminder_id = f"rem_{datetime.now().strftime('%H%M%S')}_{len(_reminders)}"
    
    reminder = {
        "id": reminder_id,
        "time": target_time.isoformat(),
        "topic": topic,
        "created": datetime.now().isoformat(),
    }
    _reminders.append(reminder)
    
    # Background-Task starten
    task = asyncio.create_task(_reminder_worker(reminder_id, target_time, topic))
    _reminder_tasks[reminder_id] = task
    
    time_str = target_time.strftime("%H:%M")
    return f"Alles klar! Ich erinnere dich um {time_str} Uhr: {topic}"


async def list_reminders() -> str:
    """Listet alle aktiven Erinnerungen auf."""
    if not _reminders:
        return "Du hast keine aktiven Erinnerungen."
    
    lines = ["Deine Erinnerungen:"]
    for r in _reminders:
        time = datetime.fromisoformat(r["time"]).strftime("%H:%M")
        lines.append(f"  • {time} Uhr: {r['topic']}")
    
    return "\n".join(lines)


async def cancel_reminder(topic_match: str) -> str:
    """Löscht eine Erinnerung die zum Thema passt."""
    global _reminders
    
    topic_lower = topic_match.lower()
    to_cancel = [r for r in _reminders if topic_lower in r["topic"].lower()]
    
    if not to_cancel:
        return f"Keine Erinnerung mit '{topic_match}' gefunden."
    
    for r in to_cancel:
        rid = r["id"]
        if rid in _reminder_tasks:
            _reminder_tasks[rid].cancel()
            del _reminder_tasks[rid]
        _reminders = [rem for rem in _reminders if rem["id"] != rid]
    
    return f"Erinnerung '{to_cancel[0]['topic']}' gelöscht."


async def execute(text: str = "") -> str:
    """
    Hauptfunktion - erkennt was der User will und führt es aus.
    
    Args:
        text: Der Benutzer-Text (z.B. "erinnere mich um 15:30 an den Termin")
    """
    text_lower = text.lower()
    logger.info("erinnerung_execute_called", text=text)
    
    # Erinnerung setzen
    time_words = ["uhr", "minuten", "minute", "stunde", "sekunde", "sekunden", ":"]
    trigger_words = ["erinner", "um ", "in "]
    
    if any(w in text_lower for w in trigger_words) and any(w in text_lower for w in time_words):
        logger.info("erinnerung_set_reminder_triggered")
        return await set_reminder(text)
    
    # Erinnerungen auflisten
    if any(w in text_lower for w in ["welche erinnerung", "liste", "zeig erinnerung", "meine erinnerung"]):
        return await list_reminders()
    
    # Erinnerung löschen
    if any(w in text_lower for w in ["lösch", "cancel", "entfern", "abbrech"]):
        # Extrahiere was gelöscht werden soll
        topic = re.sub(r'(lösch|cancel|entfern|abbrech)\w*', '', text_lower).strip()
        if topic:
            return await cancel_reminder(topic)
        return "Was soll ich löschen? Sag zum Beispiel: 'Lösche Erinnerung Termin'"
    
    # Fallback: Versuche Erinnerung zu setzen
    if "um" in text_lower or "in" in text_lower:
        logger.info("erinnerung_fallback_set_reminder")
        return await set_reminder(text)
    
    return f"Aktive Erinnerungen: {len(_reminders)}. Sage 'Erinnere mich um [Zeit] an [Thema]' um eine neue zu setzen."
