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

logger = structlog.get_logger("soma.evolution")

PLUGINS_DIR = Path(__file__).parent / "generated_plugins"
SANDBOX_DIR = Path(__file__).parent / "sandbox_env"

# Maximale Retry-Versuche wenn der LLM-generierte Code fehlschlägt
MAX_GENERATION_RETRIES = 2


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
        self._heavy_engine = heavy_engine  # LLM für Code-Generierung
        self._prompt_template = self._load_prompt_template()
        # Status-Tracking für Dashboard
        self.last_generation: dict = {}

    def _load_prompt_template(self) -> str:
        prompt_path = Path(__file__).parent / "prompts" / "plugin_generator.txt"
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8")
        return "Schreib ein Python-Plugin gemäß SOMA-Plugin-Standard."

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

        if not self._heavy_engine:
            return False, "Kein LLM verfügbar (heavy_engine fehlt)", ""

        # Alte Session clearen → kein Altlast aus vorherigen Versuchen
        self._heavy_engine.drop_session(f"evolution_{name}")

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
                raw_code = await self._heavy_engine.generate(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    session_id=f"evolution_{name}",
                    options_override={"temperature": 0.1, "top_p": 0.95},
                )
            except Exception as exc:
                msg = f"LLM-Fehler: {exc}"
                await _emit(f"❌ {msg}")
                self.last_generation["status"] = "failed"
                self.last_generation["error"] = msg
                return False, msg, ""

            code = self._extract_code(raw_code)
            self.last_generation["code"] = code
            await _emit(f"✅ Code generiert ({len(code)} Zeichen)")

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

            # ── Syntax + Sandbox Test ────────────────────────────────────
            await _emit("🔍 Syntax-Check & Sandbox-Test...")
            success, message = await self.test_and_install(name, code)

            if success:
                self.last_generation["status"] = "installed"
                await _emit(f"🚀 Plugin '{name}' installiert und geladen!", "EVOLUTION_OK")
                self._heavy_engine.drop_session(f"evolution_{name}")
                return True, message, code
            else:
                last_error = message
                await _emit(f"⚠️ Test fehlgeschlagen: {message}")
                if attempt <= MAX_GENERATION_RETRIES:
                    continue  # Nächster Versuch
                else:
                    self.last_generation["status"] = "failed"
                    self.last_generation["error"] = message
                    await _emit(f"❌ Plugin fehlgeschlagen nach {MAX_GENERATION_RETRIES} Retries: {message}")
                    self._heavy_engine.drop_session(f"evolution_{name}")
                    return False, message, code

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
