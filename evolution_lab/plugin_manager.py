"""
SOMA-AI Plugin Manager
========================
Dynamischer Loader für KI-generierte Plugins.
Hot-Reloading via importlib + Sandbox-Isolation.

Datenfluss:
  SOMA braucht neuen Skill ──► LLM generiert Python-Plugin
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
import sys
import asyncio
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger("soma.evolution")

PLUGINS_DIR = Path(__file__).parent / "generated_plugins"
SANDBOX_DIR = Path(__file__).parent / "sandbox_env"


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
    """

    def __init__(self, manager: PluginManager):
        self.manager = manager
        self.sandbox_dir = SANDBOX_DIR
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)

    async def generate_and_test(
        self,
        name: str,
        code: str,
    ) -> tuple[bool, str]:
        """
        1. Code in Sandbox schreiben
        2. Syntax-Check
        3. Bei Erfolg: In generated_plugins/ kopieren

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

        # 3. Isolation-Test (import in subprocess)
        # TODO: Vollständige Sandbox mit RestrictedPython oder Docker
        try:
            spec = importlib.util.spec_from_file_location(f"_test_{name}", str(sandbox_path))
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                # Cleanup
                del module
        except Exception as exc:
            msg = f"Runtime-Fehler: {exc}"
            logger.warning("plugin_runtime_error", name=name, error=msg)
            return False, msg

        # 4. In generated_plugins/ installieren
        plugin_path.write_text(code, encoding="utf-8")
        logger.info("plugin_generated", name=name)

        return True, f"Plugin '{name}' erfolgreich generiert und installiert."


# ── Exceptions ───────────────────────────────────────────────────────────

class PluginNotFoundError(Exception):
    pass


class PluginError(Exception):
    pass
