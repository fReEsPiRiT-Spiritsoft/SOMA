"""
SOMA-AI Action Result
======================
Strukturierte Ergebnisse von Action-Ausführungen.
Inspiriert von Claude Code's ToolResult<T> Pattern.

Features:
  - Erfolgs/Fehler-Status
  - Große Ergebnisse → Disk (mit Preview)
  - Re-Ask Content für Such-Actions
  - Context-Updates für Folge-Actions
  - TTS-Nachricht
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Callable
from datetime import datetime


@dataclass
class ActionResult:
    """
    Strukturiertes Ergebnis einer Action-Ausführung.
    
    Beispiel:
        result = await execute_action("search", {"query": "wetter berlin"})
        
        if result.success:
            if result.reask_content:
                # Ergebnisse ans LLM für Zusammenfassung
                llm_response = await llm.chat(result.reask_content)
            
            if result.tts_message:
                # Was SOMA sagen soll
                await tts.speak(result.tts_message)
    """
    
    # Core
    success: bool
    data: Any = None
    
    # Error Handling
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    is_retryable: bool = False
    
    # Für große Ergebnisse (wie Claude Code's maxResultSizeChars)
    large_result_path: Optional[Path] = None
    preview: Optional[str] = None  # Kurze Vorschau wenn Ergebnis auf Disk
    
    # Für Re-Ask Tags (search, browse, fetch)
    # Inhalt der ans LLM zurückgeht für Zusammenfassung
    reask_content: Optional[str] = None
    
    # Context-Updates für Folge-Actions
    # Z.B. nach "cd /tmp" → cwd aktualisieren
    context_updates: Optional[dict] = None
    
    # Was SOMA sprechen soll
    tts_message: Optional[str] = None
    
    # Metadata
    action_type: Optional[str] = None
    execution_time_ms: Optional[float] = None
    timestamp: datetime = field(default_factory=datetime.now)

    # ══════════════════════════════════════════════════════════════════════
    # Factory Methods
    # ══════════════════════════════════════════════════════════════════════

    @classmethod
    def success_result(
        cls,
        data: Any = None,
        tts_message: Optional[str] = None,
        **kwargs
    ) -> "ActionResult":
        """Einfaches Erfolgs-Ergebnis."""
        return cls(success=True, data=data, tts_message=tts_message, **kwargs)

    @classmethod
    def error_result(
        cls,
        message: str,
        code: str = "ERROR",
        retryable: bool = False,
        tts_message: Optional[str] = None
    ) -> "ActionResult":
        """Fehler-Ergebnis."""
        return cls(
            success=False,
            error_message=message,
            error_code=code,
            is_retryable=retryable,
            tts_message=tts_message or "Das hat leider nicht geklappt."
        )

    @classmethod
    def from_search(cls, results: list[dict], query: str) -> "ActionResult":
        """Factory für Web-Suche Ergebnisse."""
        if not results:
            return cls(
                success=True,
                data=[],
                tts_message=f"Für '{query}' habe ich leider keine Ergebnisse gefunden."
            )
        
        # Preview: Erste 3 Treffer
        preview_lines = []
        for i, r in enumerate(results[:3], 1):
            title = r.get("title", "Kein Titel")[:60]
            preview_lines.append(f"{i}. {title}")
        preview = "\n".join(preview_lines)
        
        # Full content für LLM Re-Ask
        reask_content = json.dumps(results, ensure_ascii=False, indent=2)
        
        return cls(
            success=True,
            data=results,
            preview=preview,
            reask_content=reask_content,
            tts_message=f"Ich habe {len(results)} Ergebnisse zu '{query}' gefunden."
        )

    @classmethod
    def from_browse(cls, page_content: str, url: str, question: Optional[str] = None) -> "ActionResult":
        """Factory für Browse/Fetch Ergebnisse."""
        MAX_REASK_CHARS = 15000  # ~4k tokens
        
        truncated = page_content[:MAX_REASK_CHARS]
        was_truncated = len(page_content) > MAX_REASK_CHARS
        
        reask_prompt = f"""Webseite: {url}
{"Frage: " + question if question else "Fasse den Inhalt zusammen."}

Inhalt{"(gekürzt)" if was_truncated else ""}:
{truncated}
"""
        
        return cls(
            success=True,
            data={"url": url, "content_length": len(page_content)},
            preview=f"Seite geladen: {url} ({len(page_content)} Zeichen)",
            reask_content=reask_prompt,
            tts_message=f"Ich habe die Seite {url.split('/')[2]} geladen."
        )

    @classmethod
    def from_ha_call(cls, domain: str, service: str, entity_id: str) -> "ActionResult":
        """Factory für Home Assistant Calls."""
        friendly_name = entity_id.split(".")[-1].replace("_", " ").title()
        
        action_descriptions = {
            ("light", "turn_on"): f"{friendly_name} eingeschaltet",
            ("light", "turn_off"): f"{friendly_name} ausgeschaltet",
            ("switch", "turn_on"): f"{friendly_name} eingeschaltet",
            ("switch", "turn_off"): f"{friendly_name} ausgeschaltet",
            ("climate", "set_temperature"): f"Temperatur für {friendly_name} eingestellt",
            ("media_player", "media_play"): f"{friendly_name} spielt ab",
            ("media_player", "media_pause"): f"{friendly_name} pausiert",
        }
        
        description = action_descriptions.get(
            (domain, service),
            f"{service} für {friendly_name}"
        )
        
        return cls(
            success=True,
            data={"domain": domain, "service": service, "entity_id": entity_id},
            tts_message=f"Erledigt. {description}."
        )

    @classmethod
    def from_reminder(cls, minutes: int, topic: str) -> "ActionResult":
        """Factory für Timer/Reminder."""
        if minutes < 60:
            time_str = f"{minutes} Minute{'n' if minutes != 1 else ''}"
        else:
            hours = minutes // 60
            mins = minutes % 60
            time_str = f"{hours} Stunde{'n' if hours != 1 else ''}"
            if mins:
                time_str += f" und {mins} Minute{'n' if mins != 1 else ''}"
        
        return cls(
            success=True,
            data={"minutes": minutes, "topic": topic},
            tts_message=f"Alles klar, ich erinnere dich in {time_str} an: {topic}"
        )

    # ══════════════════════════════════════════════════════════════════════
    # Large Result Handling
    # ══════════════════════════════════════════════════════════════════════

    @classmethod
    def with_large_result(
        cls,
        data: Any,
        storage_dir: Path,
        max_inline_chars: int = 5000,
        tts_message: Optional[str] = None
    ) -> "ActionResult":
        """
        Für große Ergebnisse: Speichere auf Disk, gib Preview zurück.
        
        Wie Claude Code's Tool Result Storage:
        - Ergebnis > max_inline_chars → Datei schreiben
        - Preview + Pfad zurückgeben
        """
        content = json.dumps(data, ensure_ascii=False, indent=2) if not isinstance(data, str) else data
        
        if len(content) <= max_inline_chars:
            return cls(success=True, data=data, tts_message=tts_message)
        
        # Auf Disk speichern
        storage_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path = storage_dir / f"result_{timestamp}.json"
        
        file_path.write_text(content, encoding="utf-8")
        
        # Preview generieren
        preview = content[:500] + f"\n\n... ({len(content)} Zeichen total, gespeichert in {file_path})"
        
        return cls(
            success=True,
            data=data,
            large_result_path=file_path,
            preview=preview,
            tts_message=tts_message
        )

    # ══════════════════════════════════════════════════════════════════════
    # Utility Methods
    # ══════════════════════════════════════════════════════════════════════

    def with_context_update(self, key: str, value: Any) -> "ActionResult":
        """Fügt ein Context-Update hinzu."""
        if self.context_updates is None:
            self.context_updates = {}
        self.context_updates[key] = value
        return self

    def with_execution_time(self, ms: float) -> "ActionResult":
        """Setzt die Ausführungszeit."""
        self.execution_time_ms = ms
        return self

    def to_tool_result(self) -> dict:
        """
        Konvertiert zu einem Format das ans LLM geht.
        Ähnlich Claude Code's mapToolResultToToolResultBlockParam.
        """
        if not self.success:
            return {
                "type": "tool_result",
                "is_error": True,
                "content": f"Error: {self.error_message}"
            }
        
        # Wenn Re-Ask Content vorhanden → das geht ans LLM
        if self.reask_content:
            return {
                "type": "tool_result",
                "is_error": False,
                "content": self.reask_content
            }
        
        # Preview für große Ergebnisse
        if self.large_result_path:
            return {
                "type": "tool_result",
                "is_error": False,
                "content": self.preview
            }
        
        # Normales Ergebnis
        if self.data is not None:
            content = json.dumps(self.data, ensure_ascii=False) if not isinstance(self.data, str) else self.data
            return {
                "type": "tool_result",
                "is_error": False,
                "content": content
            }
        
        return {
            "type": "tool_result",
            "is_error": False,
            "content": "OK"
        }

    def __str__(self) -> str:
        if self.success:
            return f"ActionResult(success, tts='{self.tts_message}')"
        return f"ActionResult(error: {self.error_message})"

        # Preview generieren
        preview = content[:500] + f"\n\n... ({len(content)} Zeichen total, gespeichert in {file_path})"
        
        return cls(
            success=True,
            data=data,
            large_result_path=file_path,
            preview=preview,
            tts_message=tts_message
        )

    # ══════════════════════════════════════════════════════════════════════
    # Utility Methods