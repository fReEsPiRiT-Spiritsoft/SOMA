"""
SOMA-AI Action Awareness — Das Kurzzeitgedächtnis
====================================================
Trackt ALLE Aktionen die Soma ausgeführt hat + HA-Gerätestatus.

Damit Soma auf Nachfrage weiß:
  • "Ich habe vor 2 Minuten das Licht im Wohnzimmer angemacht"
  • "Die Heizung läuft seit 15 Minuten auf 22°C"
  • "Ich habe dir um 14:23 einen Timer auf 5 Minuten gestellt"

Features:
  ✅ Action Recording: Jede gefeuerte Aktion wird mit Timestamp gespeichert
  ✅ HA State Tracking: Geräte-Status mit "seit wann" Information
  ✅ Context Injection: Wird als dynamischer Kontext ins LLM injiziert
  ✅ Auto-Cleanup: Alte Einträge werden nach 30 Minuten entfernt
  ✅ Threadsafe: Alles async-safe via simple list/dict
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional
from collections import deque

import structlog

logger = structlog.get_logger("soma.action_awareness")

# ── Konfiguration ────────────────────────────────────────────────────────
MAX_ACTION_HISTORY = 50          # Max Aktionen im Gedächtnis
ACTION_MEMORY_TTL_SECS = 1800   # 30 Minuten
HA_STATE_STALE_SECS = 300       # 5 Minuten → State als "veraltet" markieren
MAX_HA_CONTEXT_ENTITIES = 15    # Max Geräte im LLM-Kontext


@dataclass
class ActionRecord:
    """Eine einzelne ausgeführte Aktion."""
    action_type: str              # z.B. "ha_call", "search", "reminder"
    params: dict                  # Alle Parameter der Aktion
    timestamp: float              # time.time()
    raw_tag: str = ""             # Original [ACTION:...] Tag
    result: str = ""              # Ergebnis/Status
    entity_id: str = ""           # HA Entity (wenn SmartHome)
    success: bool = True


@dataclass
class HADeviceState:
    """Aktueller Status eines HA-Geräts."""
    entity_id: str
    state: str                    # "on", "off", "22.0", etc.
    friendly_name: str = ""
    last_changed: float = 0.0     # time.time() wann zuletzt geändert
    last_updated: float = 0.0     # time.time() wann zuletzt gesynced
    attributes: dict = field(default_factory=dict)

    @property
    def active_since_secs(self) -> float:
        """Wie lange ist der aktuelle State aktiv (in Sekunden)?"""
        if self.last_changed > 0:
            return time.time() - self.last_changed
        return 0.0

    @property
    def active_since_human(self) -> str:
        """Menschenlesbare Dauer seit letzter Änderung."""
        secs = self.active_since_secs
        if secs < 60:
            return f"{int(secs)}s"
        elif secs < 3600:
            return f"{int(secs / 60)} Min"
        elif secs < 86400:
            hours = int(secs / 3600)
            mins = int((secs % 3600) / 60)
            return f"{hours}h {mins}min" if mins else f"{hours}h"
        else:
            return f"{int(secs / 86400)} Tage"


# ═══════════════════════════════════════════════════════════════════════════
#  SINGLETON: Action Memory + HA State Store
# ═══════════════════════════════════════════════════════════════════════════

_action_history: deque[ActionRecord] = deque(maxlen=MAX_ACTION_HISTORY)
_ha_states: dict[str, HADeviceState] = {}


# ── Action Recording ─────────────────────────────────────────────────────

def record_action(
    action_type: str,
    params: dict,
    raw_tag: str = "",
    result: str = "",
    entity_id: str = "",
    success: bool = True,
) -> None:
    """
    Registriere eine ausgeführte Aktion im Kurzzeitgedächtnis.
    
    Wird vom ActionStreamParser/Pipeline aufgerufen NACHDEM eine Aktion
    gefeuert wurde. Soma weiß dann bei Nachfrage was sie getan hat.
    """
    record = ActionRecord(
        action_type=action_type,
        params=params,
        timestamp=time.time(),
        raw_tag=raw_tag,
        result=result,
        entity_id=entity_id,
        success=success,
    )
    _action_history.append(record)

    logger.debug(
        "action_recorded",
        action=action_type,
        entity=entity_id,
        params_preview=str(params)[:80],
    )

    # Wenn es ein HA-Call war → sofort State aktualisieren
    if action_type == "ha_call" and entity_id:
        _update_ha_state_from_action(record)


def _update_ha_state_from_action(record: ActionRecord) -> None:
    """Aktualisiere HA-State basierend auf einer ausgeführten Aktion."""
    entity_id = record.entity_id
    service = record.params.get("service", "")
    
    # State ableiten
    if "turn_on" in service:
        new_state = "on"
    elif "turn_off" in service:
        new_state = "off"
    elif "toggle" in service:
        # Toggle: Aktuellen State umkehren
        current = _ha_states.get(entity_id)
        new_state = "off" if (current and current.state == "on") else "on"
    elif "set_temperature" in service:
        new_state = record.params.get("temperature", "?") + "°C"
    else:
        new_state = "active"

    now = time.time()
    if entity_id in _ha_states:
        old = _ha_states[entity_id]
        if old.state != new_state:
            old.state = new_state
            old.last_changed = now
        old.last_updated = now
    else:
        _ha_states[entity_id] = HADeviceState(
            entity_id=entity_id,
            state=new_state,
            friendly_name=_entity_to_friendly(entity_id),
            last_changed=now,
            last_updated=now,
        )


# ── HA State Sync (von HA-Bridge aufgerufen) ────────────────────────────

def update_ha_states(states: list[dict]) -> None:
    """
    Bulk-Update aller HA-Gerätestatus.
    
    Wird von ha_bridge.sync_entities() aufgerufen.
    Jeder State: {"entity_id": "...", "state": "on", "attributes": {...}, 
                  "last_changed": "2024-01-01T12:00:00"}
    """
    import datetime

    now = time.time()
    updated = 0

    for s in states:
        eid = s.get("entity_id", "")
        if not eid:
            continue

        # Nur relevante Domains tracken
        domain = eid.split(".")[0] if "." in eid else ""
        if domain not in ("light", "switch", "climate", "media_player", 
                          "sensor", "binary_sensor", "cover", "fan"):
            continue

        state_val = s.get("state", "unknown")
        attrs = s.get("attributes", {})
        friendly = attrs.get("friendly_name", eid)

        # last_changed parsen
        last_changed_str = s.get("last_changed", "")
        try:
            if last_changed_str:
                dt = datetime.datetime.fromisoformat(last_changed_str.rstrip("Z"))
                last_changed = dt.timestamp()
            else:
                last_changed = now
        except Exception:
            last_changed = now

        if eid in _ha_states:
            old = _ha_states[eid]
            if old.state != state_val:
                old.last_changed = last_changed
            old.state = state_val
            old.friendly_name = friendly
            old.last_updated = now
            old.attributes = attrs
        else:
            _ha_states[eid] = HADeviceState(
                entity_id=eid,
                state=state_val,
                friendly_name=friendly,
                last_changed=last_changed,
                last_updated=now,
                attributes=attrs,
            )
        updated += 1

    if updated > 0:
        logger.debug("ha_states_bulk_updated", count=updated)


# ── Context Generation (für LLM-Injection) ──────────────────────────────

def get_action_context() -> str:
    """
    Generiere den Action-Awareness Kontext für den LLM System-Prompt.
    
    Enthält die letzten Aktionen die Soma ausgeführt hat,
    damit sie bei Nachfrage Bescheid weiß.
    """
    _cleanup_stale_actions()

    if not _action_history:
        return ""

    now = time.time()
    lines = ["DEINE LETZTEN AKTIONEN (Kurzzeitgedächtnis):"]

    # Letzte 10 Aktionen (neueste zuerst)
    recent = list(_action_history)[-10:]
    recent.reverse()

    for record in recent:
        ago = now - record.timestamp
        ago_str = _format_ago(ago)
        status = "✓" if record.success else "✗"
        
        # Kompakte Beschreibung
        desc = _describe_action(record)
        lines.append(f"  {status} {ago_str}: {desc}")

    lines.append(
        "Wenn der Nutzer fragt was du getan hast, beziehe dich auf diese Aktionen. "
        "SAGE NICHT 'Ich weiß nicht' wenn es hier steht!"
    )

    return "\n".join(lines)


def get_ha_state_context() -> str:
    """
    Generiere den HA-Gerätestatus Kontext für den LLM System-Prompt.
    
    Enthält den aktuellen Status relevanter Smart-Home-Geräte
    mit "seit wann" Information.
    """
    if not _ha_states:
        return ""

    now = time.time()
    lines = ["SMART-HOME STATUS (aktuelle Gerätezustände):"]
    count = 0

    # Sortiere: Aktive Geräte zuerst, dann nach letzter Änderung
    sorted_states = sorted(
        _ha_states.values(),
        key=lambda s: (
            0 if s.state in ("on", "playing") else 1,
            -(s.last_changed or 0),
        ),
    )

    for state in sorted_states:
        if count >= MAX_HA_CONTEXT_ENTITIES:
            break

        # Stale States überspringen (>5min nicht aktualisiert)
        if now - state.last_updated > HA_STATE_STALE_SECS:
            continue

        domain = state.entity_id.split(".")[0] if "." in state.entity_id else ""
        
        # Kompakte Darstellung
        name = state.friendly_name or state.entity_id
        since = state.active_since_human
        
        # Domain-spezifische Details
        extra = ""
        if domain == "climate":
            temp = state.attributes.get("temperature", state.attributes.get("current_temperature", ""))
            if temp:
                extra = f" ({temp}°C)"
        elif domain == "light" and state.state == "on":
            brightness = state.attributes.get("brightness")
            if brightness:
                pct = round(brightness / 255 * 100)
                extra = f" ({pct}%)"
        elif domain == "media_player" and state.state == "playing":
            title = state.attributes.get("media_title", "")
            if title:
                extra = f" ({title})"

        state_display = state.state.upper() if state.state in ("on", "off") else state.state
        lines.append(f"  • {name}: {state_display}{extra} (seit {since})")
        count += 1

    if count == 0:
        return ""

    lines.append(
        "Wenn der Nutzer nach dem Status eines Geräts fragt, nutze diese Info! "
        "Du WEISST wie lange Geräte schon aktiv sind."
    )

    return "\n".join(lines)


# ── Hilfsfunktionen ──────────────────────────────────────────────────────

def _cleanup_stale_actions() -> None:
    """Entferne Aktionen die älter als TTL sind."""
    cutoff = time.time() - ACTION_MEMORY_TTL_SECS
    while _action_history and _action_history[0].timestamp < cutoff:
        _action_history.popleft()


def _format_ago(seconds: float) -> str:
    """Formatiere Zeitdifferenz menschenlesbar."""
    if seconds < 10:
        return "gerade eben"
    elif seconds < 60:
        return f"vor {int(seconds)}s"
    elif seconds < 3600:
        mins = int(seconds / 60)
        return f"vor {mins} Min"
    else:
        hours = int(seconds / 3600)
        return f"vor {hours}h"


def _describe_action(record: ActionRecord) -> str:
    """Erstelle kompakte Beschreibung einer Aktion."""
    t = record.action_type
    p = record.params

    if t == "ha_call":
        domain = p.get("domain", "")
        service = p.get("service", "")
        entity = p.get("entity_id", record.entity_id)
        friendly = _entity_to_friendly(entity)
        
        if "turn_on" in service:
            return f"{friendly} eingeschaltet"
        elif "turn_off" in service:
            return f"{friendly} ausgeschaltet"
        elif "toggle" in service:
            return f"{friendly} umgeschaltet"
        elif "set_temperature" in service:
            temp = p.get("temperature", "?")
            return f"{friendly} auf {temp}°C gestellt"
        else:
            return f"{domain}.{service} → {friendly}"

    elif t == "search":
        query = p.get("query", "?")
        return f"Web-Suche: '{query}'"

    elif t == "reminder":
        mins = p.get("minutes", "?")
        topic = p.get("topic", "Timer")
        return f"Timer: {topic} in {mins} Min"

    elif t == "youtube":
        query = p.get("query", p.get("artist", "?"))
        return f"Musik: '{query}'"

    elif t in ("media_next", "media_prev", "media_pause", "media_resume", "media_stop", "media_toggle"):
        return f"Medien: {t.replace('media_', '')}"

    elif t == "volume":
        level = p.get("level", p.get("action", "?"))
        return f"Lautstärke: {level}"

    elif t == "shell":
        cmd = p.get("command", "?")[:40]
        return f"Shell: {cmd}"

    elif t == "remember":
        content = p.get("content", "?")[:40]
        return f"Gemerkt: {content}"

    elif t == "fetch_url" or t == "browse":
        url = p.get("url", "?")[:40]
        return f"Website: {url}"

    else:
        return f"{t}: {str(p)[:50]}"


def _entity_to_friendly(entity_id: str) -> str:
    """Konvertiere HA Entity-ID in menschenlesbaren Namen."""
    if not entity_id:
        return "Gerät"
    
    # Erst in HA-States schauen
    state = _ha_states.get(entity_id)
    if state and state.friendly_name:
        return state.friendly_name

    # Fallback: Entity-ID aufbereiten
    # "light.wohnzimmer" → "Licht Wohnzimmer"
    parts = entity_id.split(".", 1)
    if len(parts) == 2:
        domain_map = {
            "light": "Licht",
            "switch": "Schalter",
            "climate": "Heizung",
            "media_player": "Medien",
            "sensor": "Sensor",
            "cover": "Rolladen",
            "fan": "Ventilator",
        }
        domain_name = domain_map.get(parts[0], parts[0].title())
        room = parts[1].replace("_", " ").title()
        return f"{domain_name} {room}"
    return entity_id


# ── API für externe Abfragen ────────────────────────────────────────────

def get_recent_actions(limit: int = 20) -> list[dict]:
    """Hole letzte Aktionen als JSON-serialisierbare Dicts."""
    _cleanup_stale_actions()
    recent = list(_action_history)[-limit:]
    recent.reverse()
    return [
        {
            "action_type": r.action_type,
            "params": r.params,
            "timestamp": r.timestamp,
            "result": r.result,
            "entity_id": r.entity_id,
            "success": r.success,
            "ago": _format_ago(time.time() - r.timestamp),
        }
        for r in recent
    ]


def get_device_states() -> dict[str, dict]:
    """Hole alle HA-Gerätestatus als JSON-serialisierbare Dicts."""
    return {
        eid: {
            "entity_id": s.entity_id,
            "state": s.state,
            "friendly_name": s.friendly_name,
            "active_since": s.active_since_human,
            "last_changed": s.last_changed,
        }
        for eid, s in _ha_states.items()
    }
