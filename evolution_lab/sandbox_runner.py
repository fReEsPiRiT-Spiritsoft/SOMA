"""
SOMA-AI Sandbox Runner — Isolierte Code-Ausfuehrung
======================================================
Testet KI-generierten Code in einer sicheren Umgebung
BEVOR er ins Produktiv-System installiert wird.

Architektur (2-stufig):
  1. Docker-Sandbox (bevorzugt) — Komplette Isolation in Container
     - Eigenes Filesystem, kein Zugriff auf Host
     - CPU/RAM/Netzwerk limitiert
     - 30s Timeout hart erzwungen
     - stdout/stderr werden captured → Feedback fuer LLM

  2. Subprocess-Fallback — Wenn Docker nicht verfuegbar
     - asyncio.create_subprocess_exec in eigenem Prozess
     - 30s Timeout via asyncio.wait_for
     - Import-Test + on_load/execute Aufruf
     - Weniger isoliert, aber funktional

Datenfluss:
  PluginGenerator → CodeValidator ✅ → SandboxRunner
                                          │
                           ┌───────────────┼──────────────┐
                           │               │              │
                       Docker OK?     Subprocess       Fehler
                           │               │              │
                     Container run    fork+exec     Report zurueck
                           │               │
                     Output capture   Output capture
                           │               │
                     ┌─────┴──────┐  ┌─────┴──────┐
                     │ returncode │  │ returncode │
                     │ stdout     │  │ stdout     │
                     │ stderr     │  │ stderr     │
                     │ duration   │  │ duration   │
                     └────────────┘  └────────────┘

Non-Negotiable:
  - 30s HARD Timeout — kein Code laeuft laenger
  - Alles async
  - Output IMMER captured (fuer LLM-Retry)
  - Docker bevorzugt, Subprocess als Fallback
  - Kein Netzwerk-Zugriff in der Sandbox
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger("soma.evolution.sandbox")


# ── Constants ────────────────────────────────────────────────────────────

SANDBOX_TIMEOUT_SEC: float = 30.0        # Maximale Laufzeit
DOCKER_IMAGE: str = "python:3.13-slim"   # Leichtgewichtiges Python-Image
MAX_OUTPUT_BYTES: int = 64 * 1024        # Max 64KB Output pro Stream


# ── Result Types ─────────────────────────────────────────────────────────

class SandboxMode(str, Enum):
    """Welche Sandbox-Methode wurde genutzt."""
    DOCKER = "docker"
    SUBPROCESS = "subprocess"
    SKIPPED = "skipped"


@dataclass
class SandboxResult:
    """Ergebnis eines Sandbox-Laufs."""
    success: bool
    mode: SandboxMode
    return_code: int = -1
    stdout: str = ""
    stderr: str = ""
    duration_sec: float = 0.0
    error: str = ""              # High-Level Fehlermeldung
    timed_out: bool = False

    @property
    def feedback_for_llm(self) -> str:
        """
        Formatiertes Feedback fuer den LLM-Retry.
        Gibt dem LLM genug Context um den Fehler zu korrigieren.
        """
        if self.success:
            return ""
        parts = []
        if self.timed_out:
            parts.append(f"TIMEOUT: Code lief laenger als {SANDBOX_TIMEOUT_SEC}s")
        if self.error:
            parts.append(f"FEHLER: {self.error}")
        if self.stderr:
            # Nur die letzten 5 Zeilen des stderr — da steckt meist der Fehler
            stderr_lines = self.stderr.strip().splitlines()[-5:]
            parts.append("STDERR:\n" + "\n".join(stderr_lines))
        if self.stdout:
            stdout_lines = self.stdout.strip().splitlines()[-3:]
            parts.append("STDOUT:\n" + "\n".join(stdout_lines))
        return "\n\n".join(parts)


# ══════════════════════════════════════════════════════════════════════════
#  SANDBOX RUNNER
# ══════════════════════════════════════════════════════════════════════════

class SandboxRunner:
    """
    Fuehrt Code in einer isolierten Umgebung aus.

    Strategie:
      1. Versuche Docker-Sandbox (wenn Docker verfuegbar)
      2. Fallback auf Subprocess-Sandbox
      3. Wenn beides fehlschlaegt: Fehler-Report

    Usage:
        runner = SandboxRunner()
        result = await runner.run(code, "test_plugin")
        if not result.success:
            print(result.feedback_for_llm)
    """

    def __init__(
        self,
        timeout: float = SANDBOX_TIMEOUT_SEC,
        prefer_docker: bool = True,
        sandbox_dir: Path | None = None,
    ):
        self._timeout = timeout
        self._prefer_docker = prefer_docker
        self._sandbox_dir = sandbox_dir or Path(__file__).parent / "sandbox_env"
        self._sandbox_dir.mkdir(parents=True, exist_ok=True)
        self._docker_available: Optional[bool] = None  # Lazy-Check

        # Stats
        self._total_runs: int = 0
        self._docker_runs: int = 0
        self._subprocess_runs: int = 0
        self._failures: int = 0

    @property
    def stats(self) -> dict:
        return {
            "total_runs": self._total_runs,
            "docker_runs": self._docker_runs,
            "subprocess_runs": self._subprocess_runs,
            "failures": self._failures,
            "docker_available": self._docker_available,
        }

    # ── Main Entry ───────────────────────────────────────────────────────

    async def run(
        self,
        code: str,
        plugin_name: str,
        deps: list[str] | None = None,
    ) -> SandboxResult:
        """
        Fuehre Plugin-Code in der Sandbox aus.

        Args:
            code: Der Python-Code
            plugin_name: Name des Plugins (fuer Dateinamen)
            deps: Optionale Dependencies die installiert werden sollen

        Returns:
            SandboxResult mit Output und Status
        """
        self._total_runs += 1
        start = time.monotonic()

        # Docker versuchen wenn bevorzugt
        if self._prefer_docker:
            if self._docker_available is None:
                self._docker_available = await self._check_docker()

            if self._docker_available:
                result = await self._run_docker(code, plugin_name, deps)
                result.duration_sec = time.monotonic() - start
                if not result.success:
                    self._failures += 1
                self._docker_runs += 1
                return result

        # Subprocess-Fallback
        result = await self._run_subprocess(code, plugin_name)
        result.duration_sec = time.monotonic() - start
        if not result.success:
            self._failures += 1
        self._subprocess_runs += 1
        return result

    # ── Docker Sandbox ───────────────────────────────────────────────────

    async def _check_docker(self) -> bool:
        """Pruefen ob Docker verfuegbar ist."""
        docker_cmd = shutil.which("docker")
        if not docker_cmd:
            logger.info("docker_not_found", msg="Docker nicht im PATH — nutze Subprocess-Fallback")
            return False

        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "info",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5.0)
            available = proc.returncode == 0
            logger.info("docker_check", available=available)
            return available
        except (asyncio.TimeoutError, Exception):
            logger.info("docker_check_failed", msg="Docker-Daemon nicht erreichbar")
            return False

    async def _run_docker(
        self,
        code: str,
        plugin_name: str,
        deps: list[str] | None = None,
    ) -> SandboxResult:
        """
        Fuehre Code in einem Docker-Container aus.

        Container:
          - python:3.13-slim
          - Kein Netzwerk (--network=none)
          - Memory-Limit: 256MB
          - CPU-Limit: 1 Core
          - Read-Only Root-FS (ausser /tmp und /sandbox)
          - 30s Hard-Timeout
        """
        # Test-Skript erstellen das den Plugin-Code ausfuehrt
        test_script = self._build_test_script(code, plugin_name)

        # Temporaeres Verzeichnis fuer den Container-Mount
        with tempfile.TemporaryDirectory(prefix=f"soma_sandbox_{plugin_name}_") as tmpdir:
            tmppath = Path(tmpdir)

            # Verzeichnis und Dateien world-readable machen damit
            # der 'nobody'-User im Container (UID 65534) darauf zugreifen kann.
            # tempfile erstellt Verzeichnisse mit 0o700 — das muss 0o755 sein.
            tmppath.chmod(0o755)

            # Plugin-Code und Test-Script schreiben
            code_file = tmppath / f"{plugin_name}.py"
            test_file = tmppath / "_sandbox_test.py"
            code_file.write_text(code, encoding="utf-8")
            test_file.write_text(test_script, encoding="utf-8")
            code_file.chmod(0o644)
            test_file.chmod(0o644)

            # pip install Befehle vorbereiten.
            # Stdlib-Module (json, os, asyncio...) aus deps herausfiltern —
            # die sind im Container eingebaut und existieren nicht auf PyPI.
            # Wegen --read-only Root-FS muss pip nach /tmp/pkg_deps installieren.
            pip_cmd = ""
            if deps:
                stdlib_names: frozenset[str] = getattr(
                    sys, "stdlib_module_names",  # Python 3.10+
                    frozenset(),
                )
                import re as _re
                pypi_deps = [
                    d for d in deps
                    if _re.split(r"[><=!~]", d)[0].strip().replace("-", "_")
                    not in stdlib_names
                ]
                if pypi_deps:
                    pkg_str = " ".join(pypi_deps)
                    # --target /tmp/pkg_deps: Schreibt ins /tmp (einziges beschreibbares
                    # Verzeichnis wenn Root-FS read-only ist).
                    # PYTHONPATH=/tmp/pkg_deps: Damit Python die Packages findet.
                    pip_cmd = (
                        f"pip install --quiet --target /tmp/pkg_deps {pkg_str} && "
                        f"PYTHONPATH=/tmp/pkg_deps "
                    )

            # Docker-Befehl zusammenbauen
            docker_args = [
                "docker", "run",
                "--rm",                              # Container nach Lauf loeschen
                "--network=none",                    # Kein Netzwerk
                "--memory=256m",                     # Max 256MB RAM
                "--cpus=1.0",                        # Max 1 CPU Core
                "--read-only",                       # Read-Only Root-FS
                "--tmpfs", "/tmp:size=128m",         # Beschreibbares /tmp (pip + pkg_deps)
                "-v", f"{tmpdir}:/sandbox:ro",       # Code als Read-Only mounten
                "-w", "/sandbox",                    # Arbeitsverzeichnis
                "--user", "nobody",                  # Kein Root im Container
                DOCKER_IMAGE,
                "sh", "-c",
                f"{pip_cmd}python /sandbox/_sandbox_test.py",
            ]

            try:
                proc = await asyncio.create_subprocess_exec(
                    *docker_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout_raw, stderr_raw = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self._timeout + 10,  # +10s Overhead fuer Container-Start
                )
                stdout = stdout_raw.decode("utf-8", errors="replace")[:MAX_OUTPUT_BYTES]
                stderr = stderr_raw.decode("utf-8", errors="replace")[:MAX_OUTPUT_BYTES]

                success = proc.returncode == 0

                logger.info(
                    "sandbox_docker_complete",
                    plugin=plugin_name,
                    success=success,
                    return_code=proc.returncode,
                )

                return SandboxResult(
                    success=success,
                    mode=SandboxMode.DOCKER,
                    return_code=proc.returncode or 0,
                    stdout=stdout,
                    stderr=stderr,
                    error="" if success else self._extract_error(stderr),
                )

            except asyncio.TimeoutError:
                # Container killen
                try:
                    await asyncio.create_subprocess_exec(
                        "docker", "kill",
                        f"soma_sandbox_{plugin_name}",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                except Exception:
                    pass

                logger.warning("sandbox_docker_timeout", plugin=plugin_name)
                return SandboxResult(
                    success=False,
                    mode=SandboxMode.DOCKER,
                    timed_out=True,
                    error=f"Docker-Sandbox Timeout nach {self._timeout}s",
                )

            except Exception as exc:
                logger.error("sandbox_docker_error", plugin=plugin_name, error=str(exc))
                return SandboxResult(
                    success=False,
                    mode=SandboxMode.DOCKER,
                    error=f"Docker-Fehler: {exc}",
                )

    # ── Subprocess Sandbox (Fallback) ────────────────────────────────────

    async def _run_subprocess(
        self,
        code: str,
        plugin_name: str,
    ) -> SandboxResult:
        """
        Fuehre Code in einem isolierten Subprocess aus.

        Weniger isoliert als Docker, aber:
          - Eigener Prozess (kein Zugriff auf Host-Speicher)
          - 30s Timeout hart erzwungen
          - stdout/stderr captured
          - Testet Import + on_load() + execute()
        """
        # Test-Skript in Sandbox-Dir schreiben
        test_script = self._build_test_script(code, plugin_name)
        sandbox_code_path = self._sandbox_dir / f"{plugin_name}.py"
        sandbox_test_path = self._sandbox_dir / f"_test_{plugin_name}.py"

        try:
            sandbox_code_path.write_text(code, encoding="utf-8")
            sandbox_test_path.write_text(test_script, encoding="utf-8")

            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(sandbox_test_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._sandbox_dir),
            )

            stdout_raw, stderr_raw = await asyncio.wait_for(
                proc.communicate(),
                timeout=self._timeout,
            )
            stdout = stdout_raw.decode("utf-8", errors="replace")[:MAX_OUTPUT_BYTES]
            stderr = stderr_raw.decode("utf-8", errors="replace")[:MAX_OUTPUT_BYTES]

            success = proc.returncode == 0

            logger.info(
                "sandbox_subprocess_complete",
                plugin=plugin_name,
                success=success,
                return_code=proc.returncode,
            )

            return SandboxResult(
                success=success,
                mode=SandboxMode.SUBPROCESS,
                return_code=proc.returncode or 0,
                stdout=stdout,
                stderr=stderr,
                error="" if success else self._extract_error(stderr),
            )

        except asyncio.TimeoutError:
            # Prozess killen
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
            logger.warning("sandbox_subprocess_timeout", plugin=plugin_name)
            return SandboxResult(
                success=False,
                mode=SandboxMode.SUBPROCESS,
                timed_out=True,
                error=f"Subprocess-Sandbox Timeout nach {self._timeout}s",
            )

        except Exception as exc:
            logger.error("sandbox_subprocess_error", plugin=plugin_name, error=str(exc))
            return SandboxResult(
                success=False,
                mode=SandboxMode.SUBPROCESS,
                error=f"Subprocess-Fehler: {exc}",
            )

        finally:
            # Test-Datei aufraeumen (Plugin-Code bleibt in Sandbox)
            if sandbox_test_path.exists():
                sandbox_test_path.unlink(missing_ok=True)

    # ── Test-Script Builder ──────────────────────────────────────────────

    @staticmethod
    def _build_test_script(code: str, plugin_name: str) -> str:
        """
        Baut ein Test-Skript das:
          1. Den Plugin-Code importiert
          2. on_load() aufruft
          3. execute() aufruft
          4. on_unload() aufruft
          5. Erfolg/Fehler sauber meldet

        Dieses Skript laeuft im Sandbox-Prozess/Container.
        """
        return f'''#!/usr/bin/env python3
"""SOMA Sandbox Test — Auto-Generated"""
import asyncio
import importlib.util
import sys
import traceback

PLUGIN_NAME = "{plugin_name}"

async def _run_test():
    """Teste den Plugin-Lebenszyklus."""
    # 1. Import
    print(f"[SANDBOX] Importiere {{PLUGIN_NAME}}...")
    try:
        spec = importlib.util.spec_from_file_location(
            f"_sandbox.{{PLUGIN_NAME}}",
            f"{{PLUGIN_NAME}}.py",
        )
        if not spec or not spec.loader:
            print("[SANDBOX] FEHLER: Kann spec nicht laden")
            sys.exit(1)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        print(f"[SANDBOX] Import OK")
    except Exception as e:
        print(f"[SANDBOX] Import FEHLER: {{e}}")
        traceback.print_exc()
        sys.exit(1)

    # 2. Metadaten pruefen
    for attr in ("__version__", "__author__", "__description__"):
        if not hasattr(mod, attr):
            print(f"[SANDBOX] WARNUNG: {{attr}} fehlt")

    # 3. on_load()
    if hasattr(mod, "on_load"):
        print("[SANDBOX] Rufe on_load() auf...")
        try:
            result = mod.on_load()
            if asyncio.iscoroutine(result):
                await result
            print("[SANDBOX] on_load() OK")
        except Exception as e:
            print(f"[SANDBOX] on_load() FEHLER: {{e}}")
            traceback.print_exc()
            sys.exit(1)

    # 4. execute()
    if hasattr(mod, "execute"):
        print("[SANDBOX] Rufe execute() auf...")
        try:
            result = mod.execute()
            if asyncio.iscoroutine(result):
                result = await result
            print(f"[SANDBOX] execute() OK → {{str(result)[:200]}}")
        except Exception as e:
            print(f"[SANDBOX] execute() FEHLER: {{e}}")
            traceback.print_exc()
            sys.exit(1)
    else:
        print("[SANDBOX] FEHLER: execute() nicht gefunden")
        sys.exit(1)

    # 5. on_unload()
    if hasattr(mod, "on_unload"):
        print("[SANDBOX] Rufe on_unload() auf...")
        try:
            result = mod.on_unload()
            if asyncio.iscoroutine(result):
                await result
            print("[SANDBOX] on_unload() OK")
        except Exception as e:
            print(f"[SANDBOX] on_unload() FEHLER: {{e}}")
            # on_unload Fehler ist nicht fatal
            pass

    print("[SANDBOX] ✅ Alle Tests bestanden!")

if __name__ == "__main__":
    asyncio.run(_run_test())
'''

    @staticmethod
    def _extract_error(stderr: str) -> str:
        """Extrahiere die relevanteste Fehlermeldung aus stderr."""
        if not stderr:
            return "Unbekannter Fehler"
        lines = stderr.strip().splitlines()
        # Letzte nicht-leere Zeile ist meist die Fehlermeldung
        for line in reversed(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith("Traceback"):
                return stripped
        return lines[-1].strip() if lines else "Unbekannter Fehler"
