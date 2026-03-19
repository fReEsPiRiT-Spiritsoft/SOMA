"""
SOMA File Operations — Read, Write, List, Search, Copy, Move, Delete
=====================================================================
Safe file operations with protection for system-critical paths.
Respects sudo mode for privileged operations.
"""

from __future__ import annotations

import asyncio
import os
import shutil as sh_util
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger("soma.file_ops")


# ═══════════════════════════════════════════════════════════════════
#  SAFETY
# ═══════════════════════════════════════════════════════════════════

# Absolute paths never writable/deletable
PROTECTED_WRITE_PATHS = frozenset({
    "/", "/boot", "/usr", "/bin", "/sbin",
    "/lib", "/lib64", "/dev", "/proc", "/sys",
    "/root", "/var/lib",
})

# Files never writable regardless of location
PROTECTED_FILES = frozenset({
    ".ssh", ".gnupg", "authorized_keys", "id_rsa", "id_ed25519",
    "shadow", "passwd", "sudoers", "fstab", "crypttab",
})


def _expand(path: str) -> str:
    """Expand ~ and env vars, resolve to absolute."""
    return str(Path(os.path.expanduser(os.path.expandvars(path))).resolve())


def _is_protected(path: str, write_mode: bool = False) -> Optional[str]:
    """Check if path is protected. Returns reason string or None if OK."""
    resolved = _expand(path)

    if write_mode:
        # Block writes to critical system directories
        for pp in PROTECTED_WRITE_PATHS:
            if resolved == pp or resolved.startswith(pp + "/"):
                # Allow writes under /etc only with explicit sudo
                if pp == "/etc" or resolved.startswith("/etc/"):
                    continue  # Handled by sudo check in caller
                return f"Geschützter Systempfad: {pp}"

        # Block writes to sensitive files
        name = Path(resolved).name
        if name in PROTECTED_FILES:
            return f"Geschützte Datei: {name}"

    return None


async def _run_shell(cmd: str, timeout: float = 15.0) -> tuple[int, str, str]:
    """Execute shell command, return (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            proc.returncode or 0,
            stdout.decode("utf-8", errors="replace").strip(),
            stderr.decode("utf-8", errors="replace").strip(),
        )
    except asyncio.TimeoutError:
        return (-1, "", "Timeout")
    except Exception as e:
        return (-1, "", str(e))


# ═══════════════════════════════════════════════════════════════════
#  FILE OPERATIONS
# ═══════════════════════════════════════════════════════════════════


class FileOperations:
    """Safe file operations with protection checks."""

    def __init__(self, sudo_enabled: bool = False):
        self.sudo_enabled = sudo_enabled

    # ── READ ────────────────────────────────────────────────────────

    async def read_file(self, path: str, max_lines: int = 200) -> str:
        """Read a file's content. Returns content or error message."""
        resolved = _expand(path)

        if not os.path.exists(resolved):
            return f"Datei nicht gefunden: {path}"

        if os.path.isdir(resolved):
            return f"Das ist ein Verzeichnis, kein File. Nutze file action='list' stattdessen."

        # Size check — refuse huge files
        try:
            size = os.path.getsize(resolved)
            if size > 1_000_000:
                return (
                    f"Datei zu groß ({size:,} Bytes). "
                    f"Nutze [ACTION:shell command=\"head -100 '{path}'\"] für die ersten Zeilen."
                )
        except OSError:
            pass

        try:
            with open(resolved, "r", errors="replace") as f:
                lines = []
                for i, line in enumerate(f):
                    if i >= max_lines:
                        lines.append(
                            f"\n... (Abgeschnitten nach {max_lines} Zeilen, "
                            f"Datei hat mehr)"
                        )
                        break
                    lines.append(line.rstrip())
            content = "\n".join(lines)
            return content if content else "(Datei ist leer)"

        except PermissionError:
            if self.sudo_enabled:
                rc, out, err = await _run_shell(
                    f"sudo cat '{resolved}' | head -n {max_lines}"
                )
                if rc == 0:
                    return out
                return f"Auch mit sudo nicht lesbar: {err}"
            return f"Keine Leseberechtigung: {path} (Sudo-Modus ist deaktiviert)"

        except Exception as e:
            return f"Lesefehler: {e}"

    # ── WRITE ────────────────────────────────────────────────────────

    async def write_file(
        self, path: str, content: str, append: bool = False
    ) -> str:
        """Write or append to a file."""
        resolved = _expand(path)

        reason = _is_protected(resolved, write_mode=True)
        if reason:
            return f"Schreibvorgang blockiert: {reason}"

        mode = "a" if append else "w"
        action_word = "angehängt an" if append else "geschrieben nach"

        try:
            Path(resolved).parent.mkdir(parents=True, exist_ok=True)
            with open(resolved, mode) as f:
                f.write(content)
            return f"Erfolgreich {action_word}: {path} ({len(content)} Zeichen)"

        except PermissionError:
            if self.sudo_enabled:
                # Write via tee with sudo
                escaped = content.replace("'", "'\\''")
                op = "-a" if append else ""
                rc, _, err = await _run_shell(
                    f"echo '{escaped}' | sudo tee {op} '{resolved}' > /dev/null"
                )
                if rc == 0:
                    return f"Mit sudo {action_word}: {path}"
                return f"Auch mit sudo nicht schreibbar: {err}"
            return f"Keine Schreibberechtigung: {path} (Sudo-Modus deaktiviert)"

        except Exception as e:
            return f"Schreibfehler: {e}"

    # ── LIST ────────────────────────────────────────────────────────

    async def list_dir(
        self, path: str = ".", show_hidden: bool = False,
        details: bool = True
    ) -> str:
        """List directory contents."""
        resolved = _expand(path)

        if not os.path.isdir(resolved):
            return f"Verzeichnis nicht gefunden: {path}"

        flags = "-la" if (show_hidden and details) else (
            "-a" if show_hidden else ("-l" if details else "")
        )
        rc, out, err = await _run_shell(
            f"ls {flags} '{resolved}' 2>&1 | head -80"
        )
        if rc == 0 and out:
            return out
        return err or f"Konnte Verzeichnis nicht auflisten: {path}"

    # ── SEARCH ────────────────────────────────────────────────────────

    async def search_files(
        self, pattern: str, path: str = "~", max_results: int = 30
    ) -> str:
        """
        Search for files by name pattern.
        
        Supports:
          - Glob patterns: *.pdf, BeamNG*
          - Plain names: "BeamNG" → finds *BeamNG* (case-insensitive)
          - Partial match: "beam" → finds *beam* (case-insensitive)
        """
        resolved = _expand(path)

        if not os.path.isdir(resolved):
            return f"Suchpfad nicht gefunden: {path}"

        # Clean up the pattern — strip glob chars for the base search
        clean_pattern = pattern.replace("*", "").replace("?", "").strip()
        if not clean_pattern:
            return "Kein Suchmuster angegeben."

        # Primary search: case-insensitive name match
        rc, out, err = await _run_shell(
            f"find '{resolved}' -maxdepth 5 -iname '*{clean_pattern}*' "
            f"-not -path '*/\\.*' 2>/dev/null | head -n {max_results}",
            timeout=20.0,
        )
        if rc == 0 and out:
            count = out.count("\n") + 1
            return f"{count} Ergebnis(se):\n{out}"

        # Fallback: try locate if available (much faster, broader)
        rc2, out2, _ = await _run_shell(
            f"locate -i '{clean_pattern}' 2>/dev/null | "
            f"grep -i '{resolved}' | head -n {max_results}",
            timeout=10.0,
        )
        if rc2 == 0 and out2:
            count = out2.count("\n") + 1
            return f"{count} Ergebnis(se) (locate):\n{out2}"

        return f"Keine Dateien gefunden für: {pattern}"

    async def search_content(
        self, text: str, path: str = ".",
        file_pattern: str = "", max_results: int = 20
    ) -> str:
        """Search file contents (grep)."""
        resolved = _expand(path)

        include = f"--include='{file_pattern}'" if file_pattern else ""
        rc, out, err = await _run_shell(
            f"grep -rni {include} '{text}' '{resolved}' 2>/dev/null "
            f"| head -n {max_results}",
            timeout=20.0,
        )
        if rc == 0 and out:
            count = out.count("\n") + 1
            return f"{count} Treffer:\n{out}"
        return f"Kein Inhalt gefunden für: '{text}'"

    # ── COPY ────────────────────────────────────────────────────────

    async def copy_file(self, source: str, dest: str) -> str:
        """Copy file or directory."""
        src = _expand(source)
        dst = _expand(dest)

        reason = _is_protected(dst, write_mode=True)
        if reason:
            return f"Kopieren blockiert: {reason}"

        if not os.path.exists(src):
            return f"Quelle nicht gefunden: {source}"

        try:
            if os.path.isdir(src):
                sh_util.copytree(src, dst)
            else:
                Path(dst).parent.mkdir(parents=True, exist_ok=True)
                sh_util.copy2(src, dst)
            return f"Kopiert: {source} → {dest}"

        except PermissionError:
            if self.sudo_enabled:
                flag = "-r" if os.path.isdir(src) else ""
                rc, _, err = await _run_shell(f"sudo cp {flag} '{src}' '{dst}'")
                if rc == 0:
                    return f"Mit sudo kopiert: {source} → {dest}"
                return f"Kopierfehler mit sudo: {err}"
            return "Keine Berechtigung zum Kopieren (Sudo deaktiviert)"

        except Exception as e:
            return f"Kopierfehler: {e}"

    # ── MOVE / RENAME ────────────────────────────────────────────────

    async def move_file(self, source: str, dest: str) -> str:
        """Move/rename file or directory."""
        src = _expand(source)
        dst = _expand(dest)

        for check_path in [src, dst]:
            reason = _is_protected(check_path, write_mode=True)
            if reason:
                return f"Verschieben blockiert: {reason}"

        if not os.path.exists(src):
            return f"Quelle nicht gefunden: {source}"

        try:
            Path(dst).parent.mkdir(parents=True, exist_ok=True)
            sh_util.move(src, dst)
            return f"Verschoben: {source} → {dest}"

        except PermissionError:
            if self.sudo_enabled:
                rc, _, err = await _run_shell(f"sudo mv '{src}' '{dst}'")
                if rc == 0:
                    return f"Mit sudo verschoben: {source} → {dest}"
                return f"Fehler: {err}"
            return "Keine Berechtigung (Sudo deaktiviert)"

        except Exception as e:
            return f"Fehler beim Verschieben: {e}"

    # ── DELETE ────────────────────────────────────────────────────────

    async def delete_file(self, path: str) -> str:
        """Delete a file or directory."""
        resolved = _expand(path)

        reason = _is_protected(resolved, write_mode=True)
        if reason:
            return f"Löschen blockiert: {reason}"

        if not os.path.exists(resolved):
            return f"Nicht gefunden: {path}"

        try:
            if os.path.isdir(resolved):
                items = list(Path(resolved).iterdir())
                if len(items) > 50:
                    return (
                        f"Verzeichnis hat {len(items)} Einträge — zu viele für sicheres Löschen. "
                        f"Nutze [ACTION:shell command=\"rm -r '{path}'\"] wenn du sicher bist."
                    )
                sh_util.rmtree(resolved)
                return f"Verzeichnis gelöscht: {path}"
            else:
                os.remove(resolved)
                return f"Datei gelöscht: {path}"

        except PermissionError:
            if self.sudo_enabled:
                flag = "-rf" if os.path.isdir(resolved) else "-f"
                rc, _, err = await _run_shell(f"sudo rm {flag} '{resolved}'")
                if rc == 0:
                    return f"Mit sudo gelöscht: {path}"
                return f"Fehler: {err}"
            return "Keine Berechtigung (Sudo deaktiviert)"

        except Exception as e:
            return f"Löschfehler: {e}"

    # ── INFO ────────────────────────────────────────────────────────

    async def file_info(self, path: str) -> str:
        """Get detailed file information (stat + file type)."""
        resolved = _expand(path)

        if not os.path.exists(resolved):
            return f"Nicht gefunden: {path}"

        rc, out, err = await _run_shell(
            f"stat '{resolved}' 2>&1 && echo '---' && file '{resolved}' 2>&1"
        )
        return out if out else f"Info-Fehler: {err}"

    # ── CREATE DIRECTORY ────────────────────────────────────────────

    async def create_dir(self, path: str) -> str:
        """Create directory (including parents)."""
        resolved = _expand(path)

        reason = _is_protected(resolved, write_mode=True)
        if reason:
            return f"Erstellen blockiert: {reason}"

        try:
            Path(resolved).mkdir(parents=True, exist_ok=True)
            return f"Verzeichnis erstellt: {path}"

        except PermissionError:
            if self.sudo_enabled:
                rc, _, err = await _run_shell(f"sudo mkdir -p '{resolved}'")
                if rc == 0:
                    return f"Mit sudo erstellt: {path}"
                return f"Fehler: {err}"
            return "Keine Berechtigung (Sudo deaktiviert)"

        except Exception as e:
            return f"Fehler: {e}"

    # ── DISK USAGE ────────────────────────────────────────────────

    async def disk_usage(self, path: str = "~") -> str:
        """Get disk usage for a path."""
        resolved = _expand(path)
        rc, out, err = await _run_shell(f"du -sh '{resolved}' 2>/dev/null")
        return out if out else f"Konnte Speicherverbrauch nicht ermitteln: {err}"


# ═══════════════════════════════════════════════════════════════════
#  SINGLETON
# ═══════════════════════════════════════════════════════════════════

_instance: Optional[FileOperations] = None


def get_file_operations(sudo: bool = False) -> FileOperations:
    """Get or create FileOperations singleton."""
    global _instance
    if _instance is None or _instance.sudo_enabled != sudo:
        _instance = FileOperations(sudo_enabled=sudo)
    return _instance
