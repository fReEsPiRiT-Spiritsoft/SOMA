"""
Grep Tool — Ripgrep-powered Code & Datei-Suche.
=================================================
Inspiriert von Claude Code's GrepTool.ts:
Fortgeschrittene Dateisuche mit ripgrep (rg) als Backend.
Unterstützt Regex, Context-Lines, Multiline, Pagination.

SOMA-spezifisch:
  - Action-Tag: [ACTION:grep_search]
  - Nutzt vorhandenen SecureTerminal für sichere Ausführung
  - Ergebnis wird in Context injiziert für Re-Ask
  - Head-Limit default 250 (Context sparen)
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger("soma.grep_tool")

# ── Konfiguration ────────────────────────────────────────────────────────

DEFAULT_HEAD_LIMIT: int = 250
RG_TIMEOUT_SEC: float = 15.0

# Version Control Dirs die immer excluded werden
VCS_EXCLUDES = [".git", ".svn", ".hg", ".bzr", "__pycache__", "node_modules", ".venv"]


@dataclass
class GrepResult:
    """Ergebnis einer Grep-Suche."""
    mode: str = "files_with_matches"
    num_files: int = 0
    filenames: list[str] = field(default_factory=list)
    content: str = ""
    num_lines: int = 0
    num_matches: int = 0
    applied_limit: Optional[int] = None
    applied_offset: Optional[int] = None
    error: str = ""
    duration_ms: float = 0.0


def _has_ripgrep() -> bool:
    """Prüfe ob ripgrep installiert ist."""
    return shutil.which("rg") is not None


async def grep_search(
    pattern: str,
    path: str = ".",
    glob: Optional[str] = None,
    output_mode: str = "files_with_matches",
    context_before: int = 0,
    context_after: int = 0,
    context: int = 0,
    line_numbers: bool = True,
    case_insensitive: bool = True,
    file_type: Optional[str] = None,
    head_limit: int = DEFAULT_HEAD_LIMIT,
    offset: int = 0,
    multiline: bool = False,
) -> GrepResult:
    """
    Ripgrep-powered Suche.

    Args:
        pattern: Regex-Pattern
        path: Verzeichnis oder Datei (default: cwd)
        glob: Glob-Filter (z.B. "*.py", "*.{ts,tsx}")
        output_mode: "content" | "files_with_matches" | "count"
        context_before: Zeilen vor Match (-B)
        context_after: Zeilen nach Match (-A)
        context: Zeilen vor und nach Match (-C)
        line_numbers: Zeilennummern anzeigen
        case_insensitive: Case-insensitive suchen
        file_type: rg --type (z.B. "py", "js")
        head_limit: Max Ergebnisse (0 = unlimited)
        offset: Skip erste N Ergebnisse
        multiline: Multiline-Modus

    Returns:
        GrepResult mit Ergebnissen
    """
    import time
    start = time.monotonic()

    if not _has_ripgrep():
        return GrepResult(error="ripgrep (rg) nicht installiert. Installiere mit: pacman -S ripgrep")

    # Pfad auflösen
    search_path = Path(path).expanduser().resolve()
    if not search_path.exists():
        return GrepResult(error=f"Pfad existiert nicht: {path}")

    # Kommando aufbauen
    cmd = ["rg", "--color=never"]

    # Output Mode
    if output_mode == "files_with_matches":
        cmd.append("-l")
    elif output_mode == "count":
        cmd.append("-c")
    # "content" = default rg output

    # Optionen
    if case_insensitive:
        cmd.append("-i")
    if line_numbers and output_mode == "content":
        cmd.append("-n")
    if multiline:
        cmd.extend(["-U", "--multiline-dotall"])
    if file_type:
        cmd.extend(["--type", file_type])
    if glob:
        cmd.extend(["--glob", glob])

    # Context Lines
    if context > 0:
        cmd.extend(["-C", str(context)])
    else:
        if context_before > 0:
            cmd.extend(["-B", str(context_before)])
        if context_after > 0:
            cmd.extend(["-A", str(context_after)])

    # VCS Excludes
    for exc in VCS_EXCLUDES:
        cmd.extend(["--glob", f"!{exc}"])

    # Pattern und Pfad
    cmd.append(pattern)
    cmd.append(str(search_path))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=RG_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            proc.kill()
            return GrepResult(
                error=f"Suche timeout nach {RG_TIMEOUT_SEC}s",
                duration_ms=(time.monotonic() - start) * 1000,
            )

        duration_ms = (time.monotonic() - start) * 1000

        if proc.returncode not in (0, 1):  # 1 = no matches (normal)
            error = stderr.decode("utf-8", errors="replace").strip()
            return GrepResult(error=error or "ripgrep Fehler", duration_ms=duration_ms)

        output = stdout.decode("utf-8", errors="replace")
        lines = output.strip().split("\n") if output.strip() else []

        # Offset + Head Limit anwenden
        total = len(lines)
        if offset > 0:
            lines = lines[offset:]
        applied_limit = None
        if head_limit > 0 and len(lines) > head_limit:
            lines = lines[:head_limit]
            applied_limit = head_limit

        # Ergebnis aufbereiten
        result = GrepResult(
            mode=output_mode,
            duration_ms=round(duration_ms, 1),
            applied_limit=applied_limit,
            applied_offset=offset if offset > 0 else None,
        )

        if output_mode == "files_with_matches":
            result.filenames = [l.strip() for l in lines if l.strip()]
            result.num_files = len(result.filenames)

        elif output_mode == "count":
            result.num_matches = 0
            for line in lines:
                if ":" in line:
                    try:
                        result.num_matches += int(line.split(":")[-1])
                        result.filenames.append(line.split(":")[0])
                    except ValueError:
                        pass
            result.num_files = len(result.filenames)

        else:  # content
            result.content = "\n".join(lines)
            result.num_lines = len(lines)
            # Unique files aus Content
            seen_files = set()
            for line in lines:
                if ":" in line:
                    fname = line.split(":")[0]
                    if fname and not fname.startswith("-"):
                        seen_files.add(fname)
            result.filenames = sorted(seen_files)
            result.num_files = len(result.filenames)

        logger.debug(
            "grep_search_done",
            pattern=pattern,
            mode=output_mode,
            files=result.num_files,
            ms=round(duration_ms),
        )

        return result

    except FileNotFoundError:
        return GrepResult(error="ripgrep (rg) nicht gefunden")
    except Exception as exc:
        return GrepResult(
            error=str(exc),
            duration_ms=(time.monotonic() - start) * 1000,
        )


def format_grep_result(result: GrepResult, max_chars: int = 5000) -> str:
    """
    Formatiere GrepResult für LLM-Context-Injection.
    Kürzt auf max_chars für Token-Sparsamkeit.
    """
    if result.error:
        return f"Grep-Fehler: {result.error}"

    parts = []

    if result.mode == "files_with_matches":
        parts.append(f"Gefunden in {result.num_files} Dateien:")
        for f in result.filenames:
            parts.append(f"  {f}")

    elif result.mode == "count":
        parts.append(f"{result.num_matches} Treffer in {result.num_files} Dateien:")
        for f in result.filenames:
            parts.append(f"  {f}")

    else:  # content
        parts.append(f"{result.num_lines} Zeilen in {result.num_files} Dateien:")
        parts.append(result.content)

    text = "\n".join(parts)

    # Limit info
    if result.applied_limit:
        text += f"\n[Ergebnisse limitiert auf {result.applied_limit}]"

    # Kürzen
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[gekürzt]"

    return text
