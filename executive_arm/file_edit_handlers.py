"""
SOMA File Edit Handlers — Claude Code "Unified File Editor" Pattern
====================================================================
Handler-Funktionen für präzise Datei-Bearbeitung via ActionExecutor.

Bietet:
  - search_and_replace: Exakten Text ersetzen ohne ganze Datei neu zu schreiben
  - insert_at_line: Text an Zeilennummer einfügen
  - delete_lines: Zeilenbereich löschen

Diese Handler werden vom ActionExecutor aufgerufen wenn die entsprechenden
[ACTION:file_edit ...], [ACTION:file_insert ...], [ACTION:file_delete_lines ...]
Tags im LLM-Output erkannt werden.
"""

from __future__ import annotations

from typing import Dict, Any

import structlog

from brain_core.action_result import ActionResult
from executive_arm.file_operations import get_file_operations
from brain_core.config import is_sudo_enabled

logger = structlog.get_logger("soma.file_edit_handlers")


async def execute_file_edit(action_type: str, params: Dict[str, Any]) -> ActionResult:
    """
    Handler für [ACTION:file_edit path="..." old_text="..." new_text="..."]
    
    Claude Code Pattern: Search-and-Replace statt Datei überschreiben.
    """
    path = params.get("path", "")
    old_text = params.get("old_text", "")
    new_text = params.get("new_text", "")
    expected_count = int(params.get("expected_count", 1))
    dry_run = str(params.get("dry_run", "false")).lower() in ("true", "1", "yes")

    if not path:
        return ActionResult.error_result(
            "Pflicht-Parameter 'path' fehlt",
            code="MISSING_PARAM",
            tts_message="Ich brauche den Dateipfad."
        )

    if not old_text:
        return ActionResult.error_result(
            "Pflicht-Parameter 'old_text' fehlt",
            code="MISSING_PARAM",
            tts_message="Ich brauche den Text der ersetzt werden soll."
        )

    file_ops = get_file_operations(sudo=is_sudo_enabled())
    
    try:
        result = await file_ops.search_and_replace(
            path=path,
            old_text=old_text,
            new_text=new_text,
            expected_count=expected_count,
            dry_run=dry_run,
        )

        if result.startswith("✓") or result.startswith("DRY-RUN"):
            logger.info(
                "file_edit_success",
                path=path,
                dry_run=dry_run,
            )
            return ActionResult(
                success=True,
                data={"path": path, "result": result},
                tts_message=f"Datei bearbeitet: {path}" if not dry_run else "Vorschau erstellt",
                reask_content=result,
            )
        else:
            # Fehler von search_and_replace
            logger.warning("file_edit_failed", path=path, error=result)
            return ActionResult.error_result(
                result,
                code="EDIT_FAILED",
                tts_message="Die Bearbeitung hat nicht geklappt.",
                retryable=True,
            )

    except Exception as e:
        logger.error("file_edit_exception", path=path, error=str(e))
        return ActionResult.error_result(
            f"Fehler bei file_edit: {e}",
            code="EXCEPTION",
            tts_message="Da ist was schiefgelaufen."
        )


async def execute_file_insert(action_type: str, params: Dict[str, Any]) -> ActionResult:
    """
    Handler für [ACTION:file_insert path="..." line_number=N text="..."]
    """
    path = params.get("path", "")
    line_number = params.get("line_number")
    text = params.get("text", "")
    after = str(params.get("after", "true")).lower() in ("true", "1", "yes")

    if not path:
        return ActionResult.error_result(
            "Pflicht-Parameter 'path' fehlt",
            code="MISSING_PARAM"
        )

    if line_number is None:
        return ActionResult.error_result(
            "Pflicht-Parameter 'line_number' fehlt",
            code="MISSING_PARAM"
        )

    try:
        line_number = int(line_number)
    except (TypeError, ValueError):
        return ActionResult.error_result(
            f"Ungültige Zeilennummer: {line_number}",
            code="INVALID_PARAM"
        )

    file_ops = get_file_operations(sudo=is_sudo_enabled())

    try:
        result = await file_ops.insert_at_line(
            path=path,
            line_number=line_number,
            text=text,
            after=after,
        )

        if result.startswith("✓"):
            logger.info("file_insert_success", path=path, line=line_number)
            return ActionResult(
                success=True,
                data={"path": path, "line": line_number},
                tts_message=f"Text eingefügt in Zeile {line_number}",
            )
        else:
            return ActionResult.error_result(
                result,
                code="INSERT_FAILED",
                retryable=True,
            )

    except Exception as e:
        logger.error("file_insert_exception", path=path, error=str(e))
        return ActionResult.error_result(f"Fehler: {e}", code="EXCEPTION")


async def execute_file_delete_lines(action_type: str, params: Dict[str, Any]) -> ActionResult:
    """
    Handler für [ACTION:file_delete_lines path="..." start_line=N end_line=M]
    """
    path = params.get("path", "")
    start_line = params.get("start_line")
    end_line = params.get("end_line")

    if not path:
        return ActionResult.error_result(
            "Pflicht-Parameter 'path' fehlt",
            code="MISSING_PARAM"
        )

    try:
        start_line = int(start_line)
        end_line = int(end_line)
    except (TypeError, ValueError):
        return ActionResult.error_result(
            "Ungültige Zeilennummern",
            code="INVALID_PARAM"
        )

    file_ops = get_file_operations(sudo=is_sudo_enabled())

    try:
        result = await file_ops.delete_lines(
            path=path,
            start_line=start_line,
            end_line=end_line,
        )

        if result.startswith("✓"):
            logger.info("file_delete_lines_success", path=path, start=start_line, end=end_line)
            return ActionResult(
                success=True,
                data={"path": path, "deleted_range": f"{start_line}-{end_line}"},
                tts_message=f"Zeilen {start_line} bis {end_line} gelöscht",
            )
        else:
            return ActionResult.error_result(
                result,
                code="DELETE_FAILED",
                retryable=True,
            )

    except Exception as e:
        logger.error("file_delete_lines_exception", path=path, error=str(e))
        return ActionResult.error_result(f"Fehler: {e}", code="EXCEPTION")


# ═══════════════════════════════════════════════════════════════════
# Handler Registration Helper
# ═══════════════════════════════════════════════════════════════════

def register_file_edit_handlers(executor) -> None:
    """
    Registriert alle File-Edit Handler beim ActionExecutor.
    
    Aufruf in main.py:
        from executive_arm.file_edit_handlers import register_file_edit_handlers
        register_file_edit_handlers(get_executor())
    """
    executor.register_handler("file_edit", execute_file_edit)
    executor.register_handler("file_insert", execute_file_insert)
    executor.register_handler("file_delete_lines", execute_file_delete_lines)
    
    logger.info("file_edit_handlers_registered", handlers=[
        "file_edit", "file_insert", "file_delete_lines"
    ])
