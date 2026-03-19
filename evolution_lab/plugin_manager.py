"""
SOMA-AI Plugin Manager
========================
Dynamischer Loader für KI-generierte Plugins.
Hot-Reloading via importlib + Sandbox-Isolation.
Auto-Dependency-Installation via pip.

Datenfluss:
  SOMA braucht neuen Skill ──► LLM generiert Python-Plugin
       │                              │
       │                    __dependencies__ lesen → pip install
       │                              │
       │                    evolution_lab/sandbox_env/ (Test)
       │                              │
       │                    ✅ Tests bestanden?
       │                              │
       └──────────── evolution_lab/generated_plugins/ (Install)
                              │
                     PluginManager.load_plugin()
                              │
                     brain_core kann Plugin nutzen
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import re
import subprocess
import sys
import asyncio
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass, field

import structlog

from evolution_lab.code_validator import CodeValidator, ValidationReport, Severity
from evolution_lab.sandbox_runner import SandboxRunner, SandboxResult, SandboxMode
from evolution_lab.github_models import GitHubModelsClient

logger = structlog.get_logger("soma.evolution")

PLUGINS_DIR = Path(__file__).parent / "generated_plugins"
SANDBOX_DIR = Path(__file__).parent / "sandbox_env"

# Maximale Retry-Versuche wenn der LLM-generierte Code fehlschlägt
# Phase 5: Erhoeht auf 3 — LLM bekommt Validator + Sandbox Feedback
MAX_GENERATION_RETRIES = 3


@dataclass
class PluginMeta:
    """Metadaten eines geladenen Plugins."""
    name: str
    version: str = "0.1.0"
    author: str = "soma-ai"
    description: str = ""
    module: Optional[Any] = None
    is_loaded: bool = False
    error: Optional[str] = None


class PluginManager:
    """
    Dynamischer Plugin-Loader mit Hot-Reloading.
    Plugins werden in generated_plugins/ gespeichert und bei Bedarf geladen.
    """

    def __init__(self, plugins_dir: Optional[Path] = None):
        self.plugins_dir = plugins_dir or PLUGINS_DIR
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self._plugins: dict[str, PluginMeta] = {}
        self._lock = asyncio.Lock()

    # ── Plugin Discovery ─────────────────────────────────────────────────

    def discover_plugins(self) -> list[str]:
        """Finde alle verfügbaren Plugins (Python-Dateien)."""
        plugins = []
        for f in self.plugins_dir.glob("*.py"):
            if f.name.startswith("_"):
                continue
            plugins.append(f.stem)
        logger.info("plugins_discovered", count=len(plugins), names=plugins)
        return plugins

    # ── Load / Unload / Reload ───────────────────────────────────────────

    async def load_plugin(self, name: str) -> PluginMeta:
        """Lade ein Plugin dynamisch."""
        async with self._lock:
            plugin_path = self.plugins_dir / f"{name}.py"

            if not plugin_path.exists():
                meta = PluginMeta(name=name, error=f"Plugin {name}.py nicht gefunden")
                self._plugins[name] = meta
                return meta

            try:
                spec = importlib.util.spec_from_file_location(
                    f"soma_plugins.{name}",
                    str(plugin_path),
                )
                if not spec or not spec.loader:
                    raise ImportError(f"Cannot load spec for {name}")

                module = importlib.util.module_from_spec(spec)
                sys.modules[f"soma_plugins.{name}"] = module
                spec.loader.exec_module(module)

                # Plugin-Metadaten extrahieren
                meta = PluginMeta(
                    name=name,
                    version=getattr(module, "__version__", "0.1.0"),
                    author=getattr(module, "__author__", "soma-ai"),
                    description=getattr(module, "__description__", ""),
                    module=module,
                    is_loaded=True,
                )

                # Init-Hook aufrufen wenn vorhanden
                if hasattr(module, "on_load"):
                    await module.on_load()

                self._plugins[name] = meta
                logger.info("plugin_loaded", name=name, version=meta.version)
                return meta

            except Exception as exc:
                meta = PluginMeta(name=name, error=str(exc))
                self._plugins[name] = meta
                logger.error("plugin_load_error", name=name, error=str(exc))
                return meta

    async def unload_plugin(self, name: str) -> None:
        """Plugin entladen."""
        async with self._lock:
            meta = self._plugins.get(name)
            if meta and meta.module:
                # Cleanup-Hook
                if hasattr(meta.module, "on_unload"):
                    await meta.module.on_unload()

                # Aus sys.modules entfernen
                key = f"soma_plugins.{name}"
                sys.modules.pop(key, None)

            self._plugins.pop(name, None)
            logger.info("plugin_unloaded", name=name)

    async def reload_plugin(self, name: str) -> PluginMeta:
        """Hot-Reload eines Plugins."""
        await self.unload_plugin(name)
        return await self.load_plugin(name)

    async def load_all(self) -> dict[str, PluginMeta]:
        """Alle verfügbaren Plugins laden."""
        names = self.discover_plugins()
        for name in names:
            await self.load_plugin(name)
        return self._plugins.copy()

    # ── Plugin Execution ─────────────────────────────────────────────────

    async def execute(
        self,
        plugin_name: str,
        function_name: str = "execute",
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Führe eine Funktion eines Plugins aus.
        Standard: plugin.execute()
        """
        meta = self._plugins.get(plugin_name)
        if not meta or not meta.is_loaded or not meta.module:
            raise PluginNotFoundError(f"Plugin '{plugin_name}' nicht geladen")

        func = getattr(meta.module, function_name, None)
        if not func:
            raise PluginError(
                f"Plugin '{plugin_name}' hat keine Funktion '{function_name}'"
            )

        if asyncio.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        else:
            return await asyncio.get_event_loop().run_in_executor(
                None, lambda: func(*args, **kwargs)
            )

    # ── Queries ──────────────────────────────────────────────────────────

    def get_plugin(self, name: str) -> Optional[PluginMeta]:
        return self._plugins.get(name)

    def list_loaded(self) -> list[PluginMeta]:
        return [p for p in self._plugins.values() if p.is_loaded]

    def list_all(self) -> dict[str, PluginMeta]:
        return self._plugins.copy()


# ── Plugin-Generierung (für Evolution Lab) ───────────────────────────────

class PluginGenerator:
    """
    Generiert Plugin-Code via LLM und testet in Sandbox.
    Vollständiger Flow: Beschreibung → LLM → Deps installieren → Sandbox → Install → Load

    NEU:
      - Liest __dependencies__ = ["package1", "package2"] aus generiertem Code
      - Installiert fehlende Packages via pip automatisch
      - Bei Fehler: LLM bekommt Fehlermeldung und darf Code korrigieren (max 2 Retries)
    """

    def __init__(self, manager: PluginManager, heavy_engine=None):
        self.manager = manager
        self.sandbox_dir = SANDBOX_DIR
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        self._heavy_engine = heavy_engine  # LLM für Code-Generierung (Fallback)
        self._prompt_template = self._load_prompt_template()
        # Phase 5: Code-Validator (Forbidden Patterns + AST + Black)
        self._validator = CodeValidator(format_code=True)
        # Phase 5: Sandbox-Runner (Docker mit Subprocess-Fallback)
        self._sandbox = SandboxRunner(sandbox_dir=self.sandbox_dir)
        # Status-Tracking für Dashboard
        self.last_generation: dict = {}

        # ── GitHub Models API (primärer Code-Generator) ───────────────────
        # Nutzt o1/o4-mini/GPT-4o für deutlich bessere Code-Qualität.
        # Fallback auf lokales LLM wenn kein Token konfiguriert.
        self._github_client: Optional[GitHubModelsClient] = None
        self._init_github_client()

    def _load_prompt_template(self) -> str:
        prompt_path = Path(__file__).parent / "prompts" / "plugin_generator.txt"
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8")
        return "Schreib ein Python-Plugin gemäß SOMA-Plugin-Standard."

    def _init_github_client(self) -> None:
        """GitHub Models Client initialisieren falls Token vorhanden."""
        try:
            from brain_core.config import settings
            token = settings.github_token
            model = settings.github_models_model
            if token and token.strip():
                self._github_client = GitHubModelsClient(
                    token=token.strip(),
                    model=model or "o4-mini",
                )
                logger.info(
                    "plugin_gen_using_github_models",
                    model=model,
                    info="Plugin-Code wird via GitHub Models API generiert",
                )
            else:
                logger.info(
                    "plugin_gen_using_local_llm",
                    info="Kein GITHUB_TOKEN — Plugin-Code wird lokal generiert",
                )
        except Exception as exc:
            logger.warning("github_models_init_failed", error=str(exc))
            self._github_client = None

    def _get_code_engine(self):
        """
        Gibt den besten verfügbaren Code-Generator zurück.
        Priorität: GitHub Models API > Lokales LLM (Qwen3)
        """
        if self._github_client:
            return self._github_client, "GitHub Models"
        if self._heavy_engine:
            return self._heavy_engine, "Lokales LLM"
        return None, "Keins"

    # ── Dependency Management ────────────────────────────────────────────

    @staticmethod
    def _extract_dependencies(code: str) -> list[str]:
        """Liest __dependencies__ = [...] aus dem Plugin-Code."""
        match = re.search(
            r'__dependencies__\s*=\s*\[([^\]]*)\]',
            code,
        )
        if not match:
            return []
        raw = match.group(1)
        # Parse die einzelnen Strings: "pkg", 'pkg', 'pkg>=1.0'
        deps = re.findall(r'''["']([^"']+)["']''', raw)
        return [d.strip() for d in deps if d.strip()]

    @staticmethod
    async def _install_dependencies(
        deps: list[str],
        emit=None,
    ) -> tuple[bool, list[str], list[str]]:
        """
        Installiert fehlende Packages via pip.

        Returns: (all_ok, installed_list, failed_list)
        """
        if not deps:
            return True, [], []

        installed = []
        failed = []

        for dep in deps:
            # Package-Name ohne Version-Constraint für den Import-Check
            pkg_name = re.split(r'[><=!~]', dep)[0].strip()

            # Prüfen ob schon importierbar
            try:
                importlib.import_module(pkg_name.replace("-", "_"))
                if emit:
                    await emit(f"  📦 {pkg_name} — bereits vorhanden")
                installed.append(dep)
                continue
            except ImportError:
                pass

            # pip install ausführen
            if emit:
                await emit(f"  📥 Installiere: {dep} ...")
            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "pip", "install", "--quiet", dep,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

                if proc.returncode == 0:
                    installed.append(dep)
                    if emit:
                        await emit(f"  ✅ {dep} installiert")
                    logger.info("dep_installed", package=dep)
                else:
                    err = stderr.decode().strip().split('\n')[-1] if stderr else "unbekannt"
                    failed.append(dep)
                    if emit:
                        await emit(f"  ❌ {dep} fehlgeschlagen: {err}")
                    logger.warning("dep_install_failed", package=dep, error=err)
            except asyncio.TimeoutError:
                failed.append(dep)
                if emit:
                    await emit(f"  ⏰ {dep} — Timeout bei Installation")
            except Exception as exc:
                failed.append(dep)
                if emit:
                    await emit(f"  ❌ {dep} — Fehler: {exc}")

        return len(failed) == 0, installed, failed

    # ── Main Generation Flow ─────────────────────────────────────────────

    async def generate_from_description(
        self,
        name: str,
        description: str,
        broadcast_callback=None,
    ) -> tuple[bool, str, str]:
        """
        Kompletter Flow: Beschreibung → LLM → Deps → Test → Install → Load
        Bei Fehler: LLM bekommt Feedback und darf korrigieren (max Retries).

        Returns: (success, message, generated_code)
        """
        async def _emit(msg: str, tag: str = "EVOLUTION"):
            logger.info("evolution_step", step=msg)
            self.last_generation["last_log"] = msg
            if broadcast_callback:
                try:
                    await broadcast_callback("evolution", msg, tag, {"plugin": name})
                except Exception:
                    pass

        await _emit(f"🧬 Starte Plugin-Generierung: '{name}'")
        self.last_generation = {
            "name": name,
            "description": description,
            "status": "generating",
            "code": "",
            "error": None,
        }

        # ── Code-Engine wählen: GitHub Models (primär) > Lokales LLM ─────
        code_engine, engine_name = self._get_code_engine()
        if not code_engine:
            return False, "Kein Code-Generator verfügbar (weder GitHub Models noch lokales LLM)", ""

        await _emit(f"⚙️ Code-Engine: {engine_name}")

        # Alte Session clearen (nur relevant für lokales LLM)
        code_engine.drop_session(f"evolution_{name}")

        # ── LLM generiert den Code (mit Retry-Loop) ──────────────────────
        system_prompt = self._prompt_template
        user_prompt = (
            f"Schreibe ein SOMA-Plugin mit dem Namen '{name}'.\n"
            f"Funktion: {description}\n\n"
            f"WICHTIG: Antworte NUR mit dem Python-Code.\n"
            f"Kein erklärender Text davor oder danach.\n"
            f"Kein Markdown.\n"
            f"Nur reiner, lauffähiger Python-Code.\n"
            f"Beginne direkt mit dem Docstring oder Import."
        )

        last_error = ""
        code = ""

        for attempt in range(1, MAX_GENERATION_RETRIES + 2):  # 1 initial + N retries
            if attempt == 1:
                await _emit(f"🧠 LLM generiert Code für: {description}")
                prompt = user_prompt
            else:
                await _emit(f"🔄 Retry {attempt - 1}/{MAX_GENERATION_RETRIES} — LLM korrigiert den Code...")
                prompt = (
                    f"Der vorherige Code für das Plugin '{name}' hat einen Fehler:\n\n"
                    f"FEHLER: {last_error}\n\n"
                    f"Ursprüngliche Aufgabe: {description}\n\n"
                    f"Bitte korrigiere den Code. Beachte:\n"
                    f"- Wenn ein Package nicht existiert, nutze eine Alternative oder "
                    f"Standard-Bibliothek (subprocess, os, asyncio.create_subprocess_exec).\n"
                    f"- Deklariere alle externen Abhängigkeiten in __dependencies__ = [...].\n"
                    f"- Antworte NUR mit dem korrigierten Python-Code, kein Markdown."
                )

            try:
                raw_code = await code_engine.generate(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    session_id=f"evolution_{name}",
                    options_override={"temperature": 0.1, "top_p": 0.95},
                )
            except Exception as exc:
                # ── Fallback: Wenn GitHub Models fehlschlägt, lokales LLM probieren
                if code_engine is self._github_client and self._heavy_engine:
                    await _emit(f"⚠️ {engine_name} Fehler: {exc} — Fallback auf lokales LLM...")
                    try:
                        raw_code = await self._heavy_engine.generate(
                            prompt=prompt,
                            system_prompt=system_prompt,
                            session_id=f"evolution_{name}",
                            options_override={"temperature": 0.1, "top_p": 0.95},
                        )
                    except Exception as fallback_exc:
                        msg = f"Beide Engines fehlgeschlagen. GitHub: {exc} | Lokal: {fallback_exc}"
                        await _emit(f"❌ {msg}")
                        self.last_generation["status"] = "failed"
                        self.last_generation["error"] = msg
                        return False, msg, ""
                else:
                    msg = f"LLM-Fehler: {exc}"
                    await _emit(f"❌ {msg}")
                    self.last_generation["status"] = "failed"
                    self.last_generation["error"] = msg
                    return False, msg, ""

            code = self._extract_code(raw_code)
            self.last_generation["code"] = code
            await _emit(f"✅ Code generiert ({len(code)} Zeichen)")

            # ── Phase 5: Code-Validierung (Forbidden Patterns + AST) ──
            await _emit("🛡️ Sicherheits-Validierung (Forbidden Patterns + AST)...")
            report = self._validator.validate(code, check_structure=True)

            if not report.is_safe:
                last_error = (
                    f"Code-Sicherheitsvalidierung fehlgeschlagen:\n"
                    f"{report.error_summary}\n"
                    f"Entferne alle gefährlichen Patterns und nutze sichere Alternativen."
                )
                await _emit(f"⚠️ Sicherheits-Check fehlgeschlagen: {report.critical_count} kritisch, {report.high_count} hoch")
                if attempt <= MAX_GENERATION_RETRIES:
                    continue
                else:
                    self.last_generation["status"] = "failed"
                    self.last_generation["error"] = last_error
                    return False, last_error, code

            if not report.is_valid_structure:
                last_error = (
                    f"Plugin-Struktur ungültig:\n"
                    f"{report.error_summary}\n"
                    f"Stelle sicher: __version__, __author__, __description__ und async def execute() existieren."
                )
                await _emit(f"⚠️ Struktur-Check fehlgeschlagen")
                if attempt <= MAX_GENERATION_RETRIES:
                    continue
                else:
                    self.last_generation["status"] = "failed"
                    self.last_generation["error"] = last_error
                    return False, last_error, code

            # Black-formatierten Code verwenden wenn verfuegbar
            if report.formatted_code:
                code = report.formatted_code
                self.last_generation["code"] = code
                await _emit("🎨 Code mit Black formatiert")

            # ── Dependencies installieren ────────────────────────────────
            deps = self._extract_dependencies(code)
            if deps:
                await _emit(f"📦 Dependencies erkannt: {', '.join(deps)}")
                all_ok, installed, failed = await self._install_dependencies(deps, emit=_emit)
                if not all_ok:
                    last_error = (
                        f"Folgende Packages konnten NICHT installiert werden: "
                        f"{', '.join(failed)}. "
                        f"Diese Packages existieren vermutlich nicht auf PyPI. "
                        f"Nutze Alternativen oder die Python Standard-Bibliothek."
                    )
                    await _emit(f"⚠️ {last_error}")
                    if attempt <= MAX_GENERATION_RETRIES:
                        continue  # LLM nochmal ran lassen
                    else:
                        self.last_generation["status"] = "failed"
                        self.last_generation["error"] = last_error
                        return False, last_error, code

            # ── Syntax + Sandbox Test (Phase 5: Echte Isolation) ───────
            await _emit("🔒 Sandbox-Test (isolierte Ausführung)...")
            sandbox_result = await self._sandbox.run(
                code=code,
                plugin_name=name,
                deps=deps if deps else None,
            )

            if sandbox_result.success:
                # Sandbox bestanden → In generated_plugins/ installieren
                await _emit(f"✅ Sandbox-Test bestanden ({sandbox_result.mode.value}, {sandbox_result.duration_sec:.1f}s)")
                plugin_path = self.manager.plugins_dir / f"{name}.py"
                plugin_path.write_text(code, encoding="utf-8")
                logger.info("plugin_installed", name=name, path=str(plugin_path))

                meta = await self.manager.load_plugin(name)
                if not meta.is_loaded:
                    last_error = f"Installiert aber Lade-Fehler: {meta.error}"
                    await _emit(f"⚠️ {last_error}")
                    if attempt <= MAX_GENERATION_RETRIES:
                        continue
                    else:
                        self.last_generation["status"] = "failed"
                        self.last_generation["error"] = last_error
                        return False, last_error, code

                self.last_generation["status"] = "installed"
                await _emit(f"🚀 Plugin '{name}' installiert und geladen!", "EVOLUTION_OK")
                code_engine.drop_session(f"evolution_{name}")
                return True, f"Plugin '{name}' erfolgreich generiert, validiert, getestet und geladen.", code
            else:
                last_error = sandbox_result.feedback_for_llm or sandbox_result.error
                await _emit(f"⚠️ Sandbox-Test fehlgeschlagen: {sandbox_result.error}")
                if attempt <= MAX_GENERATION_RETRIES:
                    continue  # Nächster Versuch
                else:
                    self.last_generation["status"] = "failed"
                    self.last_generation["error"] = sandbox_result.error
                    await _emit(f"❌ Plugin fehlgeschlagen nach {MAX_GENERATION_RETRIES} Retries: {sandbox_result.error}")
                    code_engine.drop_session(f"evolution_{name}")
                    return False, sandbox_result.error, code

        # Sollte nicht erreicht werden, aber sicherheitshalber
        return False, last_error, code

    async def test_and_install(
        self,
        name: str,
        code: str,
    ) -> tuple[bool, str]:
        """
        1. Code in Sandbox schreiben
        2. Syntax-Check
        3. Import-Test in isoliertem Namespace
        4. Bei Erfolg: In generated_plugins/ installieren + laden

        Returns: (success, message)
        """
        sandbox_path = self.sandbox_dir / f"{name}.py"
        plugin_path = self.manager.plugins_dir / f"{name}.py"

        # 1. In Sandbox schreiben
        sandbox_path.write_text(code, encoding="utf-8")

        # 2. Syntax-Check
        try:
            compile(code, str(sandbox_path), "exec")
        except SyntaxError as exc:
            msg = f"Syntax-Fehler in Zeile {exc.lineno}: {exc.msg}"
            logger.warning("plugin_syntax_error", name=name, error=msg)
            return False, msg

        # 3. Import-Test in isoliertem Sub-Prozess (sicherer als exec im Hauptprozess)
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c",
                f"import importlib.util, sys; "
                f"spec = importlib.util.spec_from_file_location('_test', '{sandbox_path}'); "
                f"mod = importlib.util.module_from_spec(spec); "
                f"spec.loader.exec_module(mod); "
                f"print('OK')",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

            if proc.returncode != 0:
                err = stderr.decode().strip().split('\n')[-1] if stderr else "Unbekannter Fehler"
                msg = f"Runtime-Fehler: {err}"
                logger.warning("plugin_runtime_error", name=name, error=msg)
                return False, msg

        except asyncio.TimeoutError:
            return False, "Sandbox-Test Timeout (>30s)"
        except Exception as exc:
            msg = f"Runtime-Fehler: {exc}"
            logger.warning("plugin_runtime_error", name=name, error=msg)
            return False, msg

        # 4. In generated_plugins/ installieren
        plugin_path.write_text(code, encoding="utf-8")
        logger.info("plugin_installed", name=name, path=str(plugin_path))

        # 5. Direkt laden
        meta = await self.manager.load_plugin(name)
        if not meta.is_loaded:
            return False, f"Installiert aber Lade-Fehler: {meta.error}"

        return True, f"Plugin '{name}' erfolgreich generiert, getestet und geladen."

    @staticmethod
    def _extract_code(raw: str) -> str:
        """Extrahiert reinen Python-Code aus LLM-Antwort (entfernt Markdown etc.)."""
        # Primär: Markdown-Code-Block (```python ... ``` oder ``` ... ```)
        match = re.search(r"```(?:python)?\n?(.*?)```", raw, re.DOTALL)
        if match:
            return match.group(1).strip()

        # Fallback: Kein Markdown-Block → Text-Präfix abschneiden, Code sammeln
        # Erkennt alle üblichen Python-Zeilenanfänge
        CODE_STARTS = (
            "import ", "from ", "async def ", "def ", "class ",
            "#", '"""', "'''", "__", "@",
        )
        lines = raw.strip().splitlines()
        code_lines: list[str] = []
        in_code = False
        for line in lines:
            stripped = line.strip()
            if not in_code and stripped.startswith(CODE_STARTS):
                in_code = True
            if in_code:
                code_lines.append(line)
        return "\n".join(code_lines) if code_lines else raw.strip()


# ── Exceptions ───────────────────────────────────────────────────────────

class PluginNotFoundError(Exception):
    pass


class PluginError(Exception):
    pass
