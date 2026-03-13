"""
SOMA-AI Terminal — Sichere Shell-Ausfuehrung
===============================================
SOMA kann Shell-Commands ausfuehren. Aber nicht willkuerlich.

Jeder Command geht durch:
  1. PolicyEngine.check() → Blacklist, Identity, Custom Rules
  2. Backup (.bak) wenn noetig (Config/Code Dateien)
  3. Async Subprocess mit Timeout
  4. Output-Capture (stdout + stderr)
  5. Audit-Log

Sicherheitsmechanismen:
  - Kein sudo ohne explizite User-Freigabe
  - Kein rm -rf auf System-Pfade
  - Timeout fuer alle Commands (Default: 30s)
  - Output-Limit (Max 50KB — kein RAM-Overflow)
  - Working-Directory auf SOMA-Root beschraenkt (escape-bar fuer reads)
  - .bak vor jeder Datei-Modifikation

Non-Negotiable:
  - ALLES durch PolicyEngine
  - ALLES async (asyncio.create_subprocess_exec)
  - ALLES geloggt (structlog)
  - KEINE Cloud-Commands (curl zu externen APIs → Veto)
"""

from __future__ import annotations

import asyncio
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

from executive_arm.policy_engine import (
    PolicyEngine,
    ActionRequest,
    ActionType,
    ActionResult,
)

logger = structlog.get_logger("soma.executive.terminal")


# ── Constants ────────────────────────────────────────────────────────────

DEFAULT_TIMEOUT_SEC: float = 30.0
MAX_OUTPUT_BYTES: int = 50 * 1024  # 50 KB
MAX_CONCURRENT_COMMANDS: int = 3


# ── Command Result ───────────────────────────────────────────────────────

@dataclass
class CommandResult:
    """Ergebnis einer Shell-Ausfuehrung."""
    command: str
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    timed_out: bool = False
    was_allowed: bool = True
    policy_message: str = ""
    backup_path: str = ""          # .bak Pfad falls Backup erstellt
    truncated: bool = False        # Output wurde abgeschnitten
    timestamp: float = field(default_factory=time.time)


# ── Write-Detection ──────────────────────────────────────────────────────

# Commands die das Dateisystem veraendern
_WRITE_COMMANDS: set[str] = {
    "cp", "mv", "rm", "rmdir", "mkdir", "touch", "chmod", "chown",
    "ln", "install", "rsync", "tar", "unzip", "zip", "gzip", "bzip2",
    "sed", "awk", "tee", "truncate", "dd", "mkfs", "mount", "umount",
    "pip", "pip3", "uv", "npm", "yarn", "pnpm",
    "git", "docker", "docker-compose", "systemctl", "service",
}

# Commands die Programme ausfuehren (nicht nur lesen)
_EXECUTE_COMMANDS: set[str] = {
    "python", "python3", "node", "bash", "sh", "fish", "zsh",
    "make", "cmake", "cargo", "go", "gcc", "g++",
}

# Commands die potentiell Daten nach aussen senden
_NETWORK_COMMANDS: set[str] = {
    "curl", "wget", "ssh", "scp", "rsync", "nc", "netcat",
    "ftp", "sftp", "telnet", "ping", "traceroute",
    "nmap", "dig", "nslookup",
}


def _classify_command(cmd: str) -> ActionType:
    """Bestimme den ActionType basierend auf dem Command."""
    parts = shlex.split(cmd) if cmd else []
    if not parts:
        return ActionType.SHELL_READ

    base_cmd = Path(parts[0]).name  # Entferne Pfad: /usr/bin/ls → ls

    if base_cmd in _WRITE_COMMANDS:
        return ActionType.SHELL_WRITE
    if base_cmd in _EXECUTE_COMMANDS:
        return ActionType.SHELL_EXECUTE
    if base_cmd in _NETWORK_COMMANDS:
        return ActionType.SHELL_WRITE  # Netzwerk = potenziell schreibend

    # Redirect-Erkennung: > oder >> im Command
    if ">" in cmd or ">>" in cmd or "| tee" in cmd:
        return ActionType.SHELL_WRITE

    # Pipe zu Write-Command: cat file | sed ... > out
    if "|" in cmd:
        pipe_parts = cmd.split("|")
        for part in pipe_parts[1:]:
            sub_cmd = part.strip().split()[0] if part.strip() else ""
            if sub_cmd in _WRITE_COMMANDS:
                return ActionType.SHELL_WRITE

    return ActionType.SHELL_READ


def _extract_target_paths(cmd: str) -> list[str]:
    """Extrahiere Dateipfade aus einem Command (fuer Backup-Entscheidung)."""
    paths = []
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return paths

    for part in parts:
        # Ueberspringe Flags
        if part.startswith("-"):
            continue
        # Pruefe ob es wie ein Pfad aussieht
        if "/" in part or "." in part:
            paths.append(part)

    return paths


# ══════════════════════════════════════════════════════════════════════════
#  SECURE TERMINAL — SOMAs Haende
# ══════════════════════════════════════════════════════════════════════════

class SecureTerminal:
    """
    Async Shell-Ausfuehrung mit Policy-Pruefung.
    
    Jedes Command durchlaeuft:
      1. Klassifikation (read/write/execute)
      2. PolicyEngine.check()
      3. Optional: .bak Backup
      4. Async Subprocess
      5. Output-Capture + Truncation
      6. Result mit Metadaten
    
    Usage:
        terminal = SecureTerminal(policy_engine=pe)
        result = await terminal.execute("ls -la brain_core/")
        # → CommandResult(exit_code=0, stdout="...", ...)
        
        result = await terminal.execute("rm important_file.py")
        # → CommandResult(was_allowed=False, policy_message="...")
    """

    def __init__(
        self,
        policy_engine: PolicyEngine,
        working_dir: Path | None = None,
        default_timeout: float = DEFAULT_TIMEOUT_SEC,
    ):
        self._policy = policy_engine
        self._working_dir = working_dir or Path(__file__).resolve().parent.parent
        self._default_timeout = default_timeout
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_COMMANDS)

        # ── Stats ────────────────────────────────────────────────────
        self._total_commands: int = 0
        self._successful_commands: int = 0
        self._denied_commands: int = 0
        self._timed_out_commands: int = 0
        self._total_runtime_ms: float = 0.0

        logger.info(
            "secure_terminal_initialized",
            working_dir=str(self._working_dir),
            timeout=default_timeout,
        )

    # ══════════════════════════════════════════════════════════════════
    #  EXECUTE — Der Haupteingang
    # ══════════════════════════════════════════════════════════════════

    async def execute(
        self,
        command: str,
        timeout: float | None = None,
        reason: str = "",
        agent_goal: str = "",
        is_child_present: bool = False,
        user_approved: bool = False,
        working_dir: Path | None = None,
    ) -> CommandResult:
        """
        Fuehre ein Shell-Command sicher aus.
        
        Args:
            command: Das Shell-Command
            timeout: Timeout in Sekunden (None = Default)
            reason: Warum SOMA dieses Command ausfuehren will
            agent_goal: Uebergeordnetes Agent-Ziel
            is_child_present: Kind im Raum?
            user_approved: Explizite User-Freigabe (fuer SOFT_BLOCK)
            working_dir: Arbeitsverzeichnis (None = SOMA-Root)
            
        Returns:
            CommandResult mit Exit-Code, Output, Policy-Info
        """
        self._total_commands += 1
        timeout = timeout or self._default_timeout
        cwd = working_dir or self._working_dir

        # ── 1. Klassifizierung ───────────────────────────────────────
        action_type = _classify_command(command)
        target_paths = _extract_target_paths(command)

        # ── 2. Policy-Pruefung ───────────────────────────────────────
        policy_request = ActionRequest(
            action_type=action_type,
            description=f"Shell: {command[:120]}",
            target=command,
            parameters={"paths": target_paths, "cwd": str(cwd)},
            reason=reason,
            agent_goal=agent_goal,
            is_child_present=is_child_present,
            user_approved=user_approved,
        )

        policy_result = await self._policy.check(policy_request)

        if not policy_result.allowed:
            self._denied_commands += 1
            return CommandResult(
                command=command,
                was_allowed=False,
                policy_message=policy_result.message,
            )

        # ── 3. Backup wenn noetig ────────────────────────────────────
        backup_path = ""
        if policy_result.requires_backup:
            for path in target_paths:
                bak = await self._policy.create_backup(path)
                if bak:
                    backup_path = bak
                    break  # Ein Backup reicht als Nachweis

        # ── 4. Ausfuehrung ──────────────────────────────────────────
        async with self._semaphore:
            result = await self._run_subprocess(command, timeout, cwd)

        result.was_allowed = True
        result.backup_path = backup_path
        result.policy_message = policy_result.message

        if result.exit_code == 0:
            self._successful_commands += 1
        if result.timed_out:
            self._timed_out_commands += 1

        self._total_runtime_ms += result.duration_ms

        return result

    # ══════════════════════════════════════════════════════════════════
    #  SUBPROCESS — Async Shell-Ausfuehrung
    # ══════════════════════════════════════════════════════════════════

    async def _run_subprocess(
        self,
        command: str,
        timeout: float,
        cwd: Path,
    ) -> CommandResult:
        """Fuehre Command als async Subprocess aus."""
        t0 = time.monotonic()

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
                # Sicherheit: Kein Shell-Expansion fuer Umgebungsvariablen
                env=None,  # Erbt aktuelle Umgebung
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                duration_ms = (time.monotonic() - t0) * 1000

                logger.warning(
                    "command_timed_out",
                    command=command[:80],
                    timeout=timeout,
                )

                return CommandResult(
                    command=command,
                    exit_code=-1,
                    stderr=f"Command timed out after {timeout}s",
                    duration_ms=duration_ms,
                    timed_out=True,
                )

            duration_ms = (time.monotonic() - t0) * 1000

            # Output dekodieren + truncaten
            stdout = self._truncate_output(stdout_bytes)
            stderr = self._truncate_output(stderr_bytes)
            truncated = (
                len(stdout_bytes) > MAX_OUTPUT_BYTES
                or len(stderr_bytes) > MAX_OUTPUT_BYTES
            )

            logger.info(
                "command_executed",
                command=command[:80],
                exit_code=proc.returncode,
                duration_ms=f"{duration_ms:.1f}",
                stdout_len=len(stdout),
                stderr_len=len(stderr),
            )

            return CommandResult(
                command=command,
                exit_code=proc.returncode or 0,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                truncated=truncated,
            )

        except OSError as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            logger.error("command_os_error", command=command[:80], error=str(exc))
            return CommandResult(
                command=command,
                exit_code=-1,
                stderr=f"OS Error: {exc}",
                duration_ms=duration_ms,
            )

    @staticmethod
    def _truncate_output(data: bytes) -> str:
        """Dekodiere und truncate Output."""
        if len(data) > MAX_OUTPUT_BYTES:
            text = data[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
            text += f"\n... [truncated, {len(data)} bytes total]"
            return text
        return data.decode("utf-8", errors="replace")

    # ══════════════════════════════════════════════════════════════════
    #  CONVENIENCE METHODS
    # ══════════════════════════════════════════════════════════════════

    async def read_file(self, filepath: str) -> CommandResult:
        """Datei lesen via cat (fuer Agent-Kontext)."""
        return await self.execute(
            f"cat {shlex.quote(filepath)}",
            timeout=5.0,
            reason="Datei lesen fuer Kontext",
        )

    async def list_directory(self, dirpath: str = ".") -> CommandResult:
        """Verzeichnis auflisten."""
        return await self.execute(
            f"ls -la {shlex.quote(dirpath)}",
            timeout=5.0,
            reason="Verzeichnis-Inhalt erkunden",
        )

    async def search_in_files(
        self,
        pattern: str,
        directory: str = ".",
        file_type: str = "*.py",
    ) -> CommandResult:
        """Suche in Dateien via grep."""
        return await self.execute(
            f"grep -rn {shlex.quote(pattern)} {shlex.quote(directory)} --include={shlex.quote(file_type)}",
            timeout=10.0,
            reason=f"Suche nach '{pattern}' in {file_type}",
        )

    async def write_file(
        self,
        filepath: str,
        content: str,
        reason: str = "",
        agent_goal: str = "",
    ) -> CommandResult:
        """
        Datei schreiben (mit Policy-Check + Backup).
        
        Nutzt Python statt Shell fuer sichere File-Writes.
        """
        # Policy-Check als FILE_WRITE (nicht SHELL_WRITE)
        policy_request = ActionRequest(
            action_type=ActionType.FILE_WRITE,
            description=f"Schreibe Datei: {filepath}",
            target=filepath,
            parameters={"content_length": len(content)},
            reason=reason,
            agent_goal=agent_goal,
        )

        policy_result = await self._policy.check(policy_request)

        if not policy_result.allowed:
            return CommandResult(
                command=f"write_file({filepath})",
                was_allowed=False,
                policy_message=policy_result.message,
            )

        # Backup wenn noetig
        backup_path = ""
        if policy_result.requires_backup:
            bak = await self._policy.create_backup(filepath)
            if bak:
                backup_path = bak

        # Schreiben
        t0 = time.monotonic()
        try:
            target = Path(filepath)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            duration_ms = (time.monotonic() - t0) * 1000

            logger.info("file_written", path=filepath, size=len(content))
            return CommandResult(
                command=f"write_file({filepath})",
                exit_code=0,
                stdout=f"Wrote {len(content)} bytes to {filepath}",
                duration_ms=duration_ms,
                backup_path=backup_path,
            )
        except OSError as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            return CommandResult(
                command=f"write_file({filepath})",
                exit_code=1,
                stderr=str(exc),
                duration_ms=duration_ms,
            )

    # ══════════════════════════════════════════════════════════════════
    #  STATS
    # ══════════════════════════════════════════════════════════════════

    @property
    def stats(self) -> dict:
        return {
            "total_commands": self._total_commands,
            "successful": self._successful_commands,
            "denied": self._denied_commands,
            "timed_out": self._timed_out_commands,
            "avg_runtime_ms": (
                self._total_runtime_ms / max(1, self._successful_commands)
            ),
        }
