"""
Plugin: Erinnerung / Reminder
==============================
v2.0 — Memory-First: Erinnerungen leben im 3-Layer Memory System.

✅ Überlebt Brain Core Neustarts (persistent in SQLite L3 Semantic)
✅ SOMA kennt alle Termine (L2 Episodic + L3 Semantic)
✅ Erscheint automatisch im Gesprächs-Kontext beim Nachfragen
✅ Background Consolidation destilliert Gewohnheiten aus Terminen

Beispiele:
  - "Soma, erinnere mich um 15:30 an den Termin"
  - "Soma, Erinnerung um 16 Uhr: Müll rausbringen"
  - "Soma, in 10 Minuten: Wasser kochen"
"""
__version__ = "2.0.0"
__author__ = "SOMA Evolution Lab"
__description__ = "Erinnerungen persistent im Memory System — überleben Neustarts"

import asyncio
import json
import re
from datetime import datetime, timedelta
from typing import Optional, Callable, Awaitable
import structlog

logger = structlog.get_logger("soma.plugin.erinnerung")

# ── Memory-Kategorie in L3 ────────────────────────────────────────────────
_REMINDER_CATEGORY = "reminder"

# ── Laufzeit-State (kein SSOT — nur für asyncio Task-Management) ─────────
_reminder_tasks: dict[str, asyncio.Task] = {}
_speak_callback: Optional[Callable[[str], Awaitable[None]]] = None


# ── Memory-Helpers ────────────────────────────────────────────────────────

async def _get_orch():
    """Gibt den MemoryOrchestrator zurück — None wenn noch nicht bereit."""
    try:
        from brain_core.memory.integration import get_orchestrator
        return get_orchestrator()
    except Exception:
        return None


async def _save_to_memory(reminder_id: str, topic: str, target_time: datetime):
    """Speichert Erinnerung als Fakt in L3 (SSOT)."""
    orch = await _get_orch()
    if orch is None:
        return
    payload = json.dumps(
        {"topic": topic, "time": target_time.isoformat()},
        ensure_ascii=False,
    )
    await orch.semantic.learn_fact(
        category=_REMINDER_CATEGORY,
        subject=reminder_id,
        fact=payload,
        confidence=0.99,  # Erinnerungen sind immer gesichert
    )


async def _delete_from_memory(reminder_id: str):
    """Löscht eine erledigte/abgebrochene Erinnerung aus L3."""
    orch = await _get_orch()
    if orch is None:
        return
    try:
        await orch.semantic.forget_fact(reminder_id)
    except Exception as e:
        logger.warning("reminder_memory_delete_failed", id=reminder_id, error=str(e))


async def _get_all_from_memory() -> list[dict]:
    """Lädt alle aktiven Erinnerungen direkt aus L3 — Memory ist die einzige Quelle."""
    orch = await _get_orch()
    if orch is None:
        return []
    try:
        facts = await orch.semantic.get_facts_by_category(_REMINDER_CATEGORY)
        result = []
        for f in facts:
            try:
                data = json.loads(f.fact)
                data["id"] = f.subject
                result.append(data)
            except (json.JSONDecodeError, KeyError):
                continue
        return result
    except Exception as e:
        logger.warning("reminders_load_failed", error=str(e))
        return []


async def _record_event(event: str, topic: str):
    """Schreibt ein Reminder-Event als Episode in L2 — für Kontext und Recall."""
    orch = await _get_orch()
    if orch is None:
        return
    asyncio.create_task(
        orch.store_interaction(
            user_text=f"Reminder {event}: {topic}",
            soma_text=f"reminder_{event}",
            emotion="neutral",
            intent=f"reminder_{event}",
            topic="reminder",
        )
    )


# ── Plugin Lifecycle ─────────────────────────────────────────────────────

def set_speak_callback(callback: Callable[[str], Awaitable[None]]) -> None:
    """Setzt die TTS-Callback-Funktion für Benachrichtigungen."""
    global _speak_callback
    _speak_callback = callback
    logger.info("reminder_speak_callback_set")


async def on_load():
    """Plugin geladen — stellt nach Neustart alle Erinnerungen aus Memory wieder ein."""
    logger.info("erinnerung_plugin_v2_loaded")
    asyncio.create_task(_deferred_restore())


async def _deferred_restore():
    """Wartet bis Memory bereit ist (Brain Core Boot), dann alle Reminders aus L3 neu einplanen."""
    for _ in range(15):  # max 30s warten
        await asyncio.sleep(2)
        orch = await _get_orch()
        if orch is None:
            continue
        # Memory ist bereit
        reminders = await _get_all_from_memory()
        now = datetime.now()
        rescheduled = 0
        expired = 0
        for r in reminders:
            try:
                target = datetime.fromisoformat(r["time"])
                rid = r["id"]
                if target <= now:
                    # Abgelaufen während Neustart → aus Memory löschen
                    asyncio.create_task(_delete_from_memory(rid))
                    expired += 1
                    continue
                if rid in _reminder_tasks:
                    continue
                # _speak_callback wurde von der Pipeline vor _deferred_restore gesetzt
                task = asyncio.create_task(
                    _reminder_worker(rid, target, r["topic"], speak_fn=_speak_callback)
                )
                _reminder_tasks[rid] = task
                rescheduled += 1
            except Exception as e:
                logger.warning("reminder_reschedule_failed", id=r.get("id"), error=str(e))
        logger.info(
            "reminders_restored_from_memory",
            rescheduled=rescheduled,
            expired_cleaned=expired,
        )
        return
    logger.warning("deferred_restore_gave_up", reason="Memory not ready after 30s")


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


async def _reminder_worker(
    reminder_id: str,
    target_time: datetime,
    topic: str,
    speak_fn=None,
):
    """Background-Worker: wartet, spricht die Benachrichtigung, räumt Memory auf."""
    now = datetime.now()
    wait_seconds = (target_time - now).total_seconds()

    if wait_seconds <= 0:
        logger.warning("reminder_already_past", id=reminder_id)
        await _delete_from_memory(reminder_id)
        _reminder_tasks.pop(reminder_id, None)
        return

    logger.info(
        "reminder_scheduled",
        id=reminder_id,
        target=target_time.strftime("%H:%M"),
        wait_seconds=round(wait_seconds),
        topic=topic,
    )

    try:
        await asyncio.sleep(wait_seconds)
    except asyncio.CancelledError:
        logger.info("reminder_cancelled_during_sleep", id=reminder_id)
        return

    # Zeit ist da → Benachrichtigen
    message = f"Hey! Erinnerung: {topic}"
    logger.info("reminder_triggered", id=reminder_id, topic=topic)

    # Immer aktuellsten Callback nehmen: Argument > Modul-Global > Fallback
    cb = speak_fn or _speak_callback
    if cb is None:
        # Letzter Ausweg: Pipeline-Instanz direkt fragen
        try:
            from brain_core.main import get_pipeline
            pipeline = get_pipeline()
            if pipeline:
                cb = pipeline.autonomous_speak
                logger.warning("reminder_using_pipeline_fallback")
        except Exception:
            pass

    if cb:
        try:
            logger.info("reminder_speaking", id=reminder_id, topic=topic)
            await cb(message)
        except Exception as e:
            logger.error("reminder_speak_failed", error=str(e), id=reminder_id)
    else:
        logger.error(
            "reminder_no_callback", id=reminder_id,
            msg="Kein TTS-Callback — ist die Pipeline gestartet? Prüfe Pipeline.start()"
        )

    # ── Memory aufräumen + Event aufzeichnen ──────────────────────────────
    await _delete_from_memory(reminder_id)        # aus L3 löschen
    await _record_event("triggered", topic)        # Episode in L2 schreiben
    _reminder_tasks.pop(reminder_id, None)


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


async def _create_reminder(target_time: datetime, topic: str) -> str:
    """Legt Reminder in L3 (SSOT) an, schreibt Episode in L2, startet asyncio Task."""
    reminder_id = f"rem_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(_reminder_tasks)}"

    # 1. In L3 Semantic Memory speichern (überlebt Neustarts)
    await _save_to_memory(reminder_id, topic, target_time)

    # 2. Episode in L2 schreiben (SOMA "erinnert sich" dass du das gesagt hast)
    await _record_event("set", f"{topic} um {target_time.strftime('%H:%M')} Uhr")

    # 3. asyncio Task starten
    captured_callback = _speak_callback
    task = asyncio.create_task(
        _reminder_worker(reminder_id, target_time, topic, speak_fn=captured_callback)
    )
    _reminder_tasks[reminder_id] = task

    def _on_done(t: asyncio.Task):
        if not t.cancelled() and t.exception() is not None:
            logger.error("reminder_task_crashed", id=reminder_id, error=str(t.exception()))
    task.add_done_callback(_on_done)

    time_str = target_time.strftime("%H:%M")
    logger.info("reminder_created", id=reminder_id, at=time_str, topic=topic)
    return f"Alles klar! Ich erinnere dich um {time_str} Uhr: {topic}"


async def list_reminders() -> str:
    """Listet alle Erinnerungen direkt aus L3 Memory — immer aktuell, auch nach Neustart."""
    reminders = await _get_all_from_memory()
    if not reminders:
        return "Du hast keine aktiven Erinnerungen."
    now = datetime.now()
    active = [
        r for r in reminders
        if datetime.fromisoformat(r["time"]) > now
    ]
    if not active:
        return "Alle Erinnerungen sind bereits abgelaufen."
    lines = ["Deine Erinnerungen:"]
    for r in sorted(active, key=lambda x: x["time"]):
        t = datetime.fromisoformat(r["time"]).strftime("%H:%M")
        lines.append(f"  • {t} Uhr: {r['topic']}")
    return "\n".join(lines)


async def cancel_reminder(topic_match: str) -> str:
    """Löscht eine Erinnerung aus Memory und bricht den asyncio Task ab."""
    reminders = await _get_all_from_memory()
    topic_lower = topic_match.lower()
    to_cancel = [r for r in reminders if topic_lower in r["topic"].lower()]

    if not to_cancel:
        return f"Keine Erinnerung mit '{topic_match}' gefunden."

    for r in to_cancel:
        rid = r["id"]
        if rid in _reminder_tasks:
            _reminder_tasks[rid].cancel()
            _reminder_tasks.pop(rid, None)
        await _delete_from_memory(rid)

    await _record_event("cancelled", to_cancel[0]["topic"])
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
    
    reminders = await _get_all_from_memory()
    now = datetime.now()
    active_count = len([r for r in reminders if datetime.fromisoformat(r["time"]) > now])
    return f"Aktive Erinnerungen: {active_count}. Sage 'Erinnere mich um [Zeit] an [Thema]' um eine neue zu setzen."
