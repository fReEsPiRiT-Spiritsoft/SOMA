"""
SOMA-AI Filesystem Map — SOMA kennt seinen eigenen Koerper
=============================================================
Ein Mensch weiss wo seine Haende sind. SOMA weiss wo seine Dateien sind.

Funktionen:
  1. Baut eine LLM-lesbare Baumansicht des SOMA-Verzeichnisses
  2. Beobachtet Aenderungen via watchdog (inotify unter Linux)
  3. Liefert semantischen Kontext fuer den Agent
  4. Erkennt eigene Kern-Module vs. generierte Plugins vs. Daten

Die Map wird einmal beim Start erstellt und dann live aktualisiert.
Sie ist NICHT der Dateiinhalt — sie ist die Struktur.
Wie ein Koerperschema: Du weisst dass du einen Arm hast,
auch ohne ihn anzuschauen.

Non-Negotiable:
  - Liest NIEMALS .env oder Dateien mit Credentials
  - Ignoriert __pycache__, .git, node_modules, venv
  - Max 500 Eintraege (keine Explosion bei grossen Repos)
  - Async-safe, thread-safe
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger("soma.executive.filesystem")


# ── File Categories ──────────────────────────────────────────────────────

class FileCategory(str, Enum):
    """Kategorisierung der SOMA-Dateien."""
    CORE_BRAIN = "core_brain"           # brain_core/
    CORE_EGO = "core_ego"              # brain_ego/
    CORE_MEMORY = "core_memory"         # brain_memory_ui/
    CORE_SHARED = "core_shared"         # shared/
    EXECUTIVE = "executive"             # executive_arm/
    EVOLUTION = "evolution"             # evolution_lab/
    GENERATED_PLUGIN = "generated_plugin"  # evolution_lab/generated_plugins/
    CONFIG = "config"                   # .env, docker-compose.yml, etc.
    DATA = "data"                       # data/
    UI = "ui"                           # soma_face_tablet/, templates/
    INFRA = "infra"                     # mosquitto/, asterisk/
    TEST = "test"                       # tests/, test_*
    DOCS = "docs"                       # README, .md files
    UNKNOWN = "unknown"


# ── Filesystem Node ──────────────────────────────────────────────────────

@dataclass
class FSNode:
    """Ein Knoten im Dateisystem-Baum."""
    name: str
    path: str                      # Relativer Pfad zum SOMA-Root
    is_dir: bool
    category: FileCategory = FileCategory.UNKNOWN
    size_bytes: int = 0
    modified_ts: float = 0.0
    children_count: int = 0        # Nur fuer Verzeichnisse
    description: str = ""          # LLM-lesbare Beschreibung


# ── Path → Category Mapping ─────────────────────────────────────────────

_CATEGORY_MAP: list[tuple[str, FileCategory]] = [
    ("brain_core/", FileCategory.CORE_BRAIN),
    ("brain_ego/", FileCategory.CORE_EGO),
    ("brain_memory_ui/", FileCategory.CORE_MEMORY),
    ("brain_core/memory/", FileCategory.CORE_MEMORY),
    ("shared/", FileCategory.CORE_SHARED),
    ("executive_arm/", FileCategory.EXECUTIVE),
    ("evolution_lab/generated_plugins/", FileCategory.GENERATED_PLUGIN),
    ("evolution_lab/", FileCategory.EVOLUTION),
    ("data/", FileCategory.DATA),
    ("soma_face_tablet/", FileCategory.UI),
    ("mosquitto/", FileCategory.INFRA),
    ("asterisk/", FileCategory.INFRA),
    ("tests/", FileCategory.TEST),
]

# Dateien/Ordner die IMMER ignoriert werden
_IGNORE_PATTERNS: set[str] = {
    "__pycache__",
    ".git",
    ".gitignore",
    "node_modules",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dump.rdb",
    "db.sqlite3",
    "*.pyc",
    "*.pyo",
    "*.egg-info",
}

# Sensitive Dateien — nie den Inhalt zeigen
_SENSITIVE_FILES: set[str] = {
    ".env",
    ".env.local",
    ".env.production",
    "secrets.yml",
    "secrets.json",
}


def _categorize(rel_path: str) -> FileCategory:
    """Bestimme die Kategorie eines Pfades."""
    # Laengste Matches zuerst (generated_plugins vor evolution_lab)
    for prefix, cat in sorted(_CATEGORY_MAP, key=lambda x: -len(x[0])):
        if rel_path.startswith(prefix):
            return cat

    # Datei-basierte Kategorisierung
    name = Path(rel_path).name
    if name.startswith("test_") or name == "conftest.py":
        return FileCategory.TEST
    if name.endswith(".md"):
        return FileCategory.DOCS
    if name in (".env", "docker-compose.yml", "requirements.txt", ".soma-rules"):
        return FileCategory.CONFIG

    return FileCategory.UNKNOWN


def _should_ignore(name: str) -> bool:
    """Pruefe ob eine Datei/Ordner ignoriert werden soll."""
    if name in _IGNORE_PATTERNS:
        return True
    if name.startswith(".") and name not in (".env", ".soma-rules"):
        return True
    # Glob-Patterns
    for pattern in _IGNORE_PATTERNS:
        if pattern.startswith("*") and name.endswith(pattern[1:]):
            return True
    return False


# ══════════════════════════════════════════════════════════════════════════
#  FILESYSTEM MAP — SOMAs Koerperschema
# ══════════════════════════════════════════════════════════════════════════

class FilesystemMap:
    """
    Baut und pflegt eine LLM-lesbare Karte von SOMAs Dateisystem.
    
    Features:
      - Einmaliger Scan beim Start
      - Live-Updates via watchdog (inotify)
      - to_llm_context() → String fuer System-Prompt
      - Kategorisierung: Core / Executive / Plugin / Data / Config
      - Max 500 Eintraege (Schutz vor Explosion)
    
    Usage:
        fs = FilesystemMap(soma_root=Path("/path/to/SOMA"))
        await fs.scan()
        context = fs.to_llm_context()
        # → "SOMA Dateisystem:\n  brain_core/ [CORE_BRAIN] ...\n  ..."
    """

    MAX_ENTRIES: int = 500
    MAX_DEPTH: int = 4    # Maximale Verzeichnistiefe

    def __init__(self, soma_root: Path | None = None):
        self._root = soma_root or Path(__file__).resolve().parent.parent
        self._nodes: dict[str, FSNode] = {}   # rel_path → Node
        self._last_scan: float = 0.0
        self._watcher_task: Optional[asyncio.Task] = None
        self._change_events: list[dict] = []  # Letzte Aenderungen
        self._max_change_events: int = 50

        logger.info("filesystem_map_initialized", root=str(self._root))

    # ══════════════════════════════════════════════════════════════════
    #  SCAN — Erstmaliger Scan
    # ══════════════════════════════════════════════════════════════════

    async def scan(self) -> int:
        """
        Scanne das SOMA-Verzeichnis und baue die Map.
        Laeuft in einem Thread um den Event-Loop nicht zu blockieren.
        
        Returns:
            Anzahl der gescannten Eintraege
        """
        loop = asyncio.get_event_loop()
        count = await loop.run_in_executor(None, self._scan_sync)
        self._last_scan = time.time()
        logger.info("filesystem_scan_complete", entries=count)
        return count

    def _scan_sync(self) -> int:
        """Synchroner Scan (laeuft im Thread-Pool)."""
        self._nodes.clear()
        count = 0

        for item in self._walk(self._root, depth=0):
            if count >= self.MAX_ENTRIES:
                logger.warning("filesystem_map_truncated", max=self.MAX_ENTRIES)
                break

            rel = str(item.relative_to(self._root))
            is_dir = item.is_dir()

            try:
                stat = item.stat()
                size = stat.st_size if not is_dir else 0
                mtime = stat.st_mtime
            except OSError:
                size = 0
                mtime = 0.0

            category = _categorize(rel + ("/" if is_dir else ""))
            children = len(list(item.iterdir())) if is_dir else 0

            node = FSNode(
                name=item.name,
                path=rel,
                is_dir=is_dir,
                category=category,
                size_bytes=size,
                modified_ts=mtime,
                children_count=children,
            )

            self._nodes[rel] = node
            count += 1

        return count

    def _walk(self, directory: Path, depth: int):
        """Rekursiver Walk mit Tiefenbegrenzung und Ignore-Patterns."""
        if depth > self.MAX_DEPTH:
            return

        try:
            entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        except PermissionError:
            return

        for entry in entries:
            if _should_ignore(entry.name):
                continue

            yield entry

            if entry.is_dir():
                yield from self._walk(entry, depth + 1)

    # ══════════════════════════════════════════════════════════════════
    #  WATCHDOG — Live-Updates via inotify
    # ══════════════════════════════════════════════════════════════════

    async def start_watcher(self) -> None:
        """
        Starte den Filesystem-Watcher fuer Live-Updates.
        Nutzt watchdog wenn verfuegbar, sonst polling fallback.
        """
        if self._watcher_task is not None:
            return

        self._watcher_task = asyncio.create_task(
            self._watch_loop(),
            name="soma-fs-watcher",
        )
        logger.info("filesystem_watcher_started")

    async def stop_watcher(self) -> None:
        """Stoppe den Filesystem-Watcher."""
        if self._watcher_task:
            self._watcher_task.cancel()
            try:
                await self._watcher_task
            except asyncio.CancelledError:
                pass
            self._watcher_task = None
            logger.info("filesystem_watcher_stopped")

    async def _watch_loop(self) -> None:
        """
        Watcher-Loop: Versuche watchdog, fallback auf Polling.
        """
        try:
            await self._watch_with_watchdog()
        except ImportError:
            logger.info("watchdog_not_available_using_polling")
            await self._watch_polling()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("filesystem_watcher_error", error=str(exc))

    async def _watch_with_watchdog(self) -> None:
        """Nutze watchdog-Library fuer echte inotify-Events."""
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler

        event_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=100)
        loop = asyncio.get_event_loop()

        class SomaEventHandler(FileSystemEventHandler):
            def on_any_event(self, event):
                if _should_ignore(Path(event.src_path).name):
                    return
                try:
                    loop.call_soon_threadsafe(
                        event_queue.put_nowait,
                        {
                            "type": event.event_type,
                            "path": event.src_path,
                            "is_dir": event.is_directory,
                            "time": time.time(),
                        },
                    )
                except asyncio.QueueFull:
                    pass  # Events droppen statt blockieren

        observer = Observer()
        observer.schedule(
            SomaEventHandler(),
            str(self._root),
            recursive=True,
        )
        observer.start()
        logger.info("watchdog_observer_started")

        try:
            while True:
                event = await event_queue.get()
                self._handle_fs_event(event)
        except asyncio.CancelledError:
            observer.stop()
            observer.join(timeout=3)
            raise

    async def _watch_polling(self) -> None:
        """Fallback: Polling alle 30 Sekunden."""
        while True:
            await asyncio.sleep(30.0)
            await self.scan()

    def _handle_fs_event(self, event: dict) -> None:
        """Verarbeite ein Filesystem-Event."""
        try:
            src_path = Path(event["path"])
            rel = str(src_path.relative_to(self._root))
        except ValueError:
            return  # Pfad ausserhalb von SOMA-Root

        event_type = event["type"]

        if event_type in ("deleted", "moved"):
            self._nodes.pop(rel, None)
        elif event_type in ("created", "modified"):
            if src_path.exists():
                is_dir = src_path.is_dir()
                try:
                    stat = src_path.stat()
                    size = stat.st_size if not is_dir else 0
                    mtime = stat.st_mtime
                except OSError:
                    size = 0
                    mtime = 0.0

                category = _categorize(rel + ("/" if is_dir else ""))
                self._nodes[rel] = FSNode(
                    name=src_path.name,
                    path=rel,
                    is_dir=is_dir,
                    category=category,
                    size_bytes=size,
                    modified_ts=mtime,
                )

        # Event-History
        self._change_events.append({
            "type": event_type,
            "path": rel,
            "time": event["time"],
        })
        if len(self._change_events) > self._max_change_events:
            self._change_events = self._change_events[-self._max_change_events:]

    # ══════════════════════════════════════════════════════════════════
    #  LLM CONTEXT — Menschenlesbare Struktur
    # ══════════════════════════════════════════════════════════════════

    def to_llm_context(self, max_lines: int = 80) -> str:
        """
        Erzeuge LLM-lesbaren Kontext ueber SOMAs Dateisystem.
        
        Wird vom Agent als Orientierung genutzt:
        "Wo bin ich? Was habe ich? Wo liegen meine Dateien?"
        """
        if not self._nodes:
            return "SOMA-Dateisystem: Noch nicht gescannt."

        lines: list[str] = [
            "MEIN DATEISYSTEM (SOMA-Root):",
            f"  Pfad: {self._root}",
            f"  Eintraege: {len(self._nodes)}",
            f"  Letzter Scan: {time.strftime('%H:%M:%S', time.localtime(self._last_scan))}",
            "",
        ]

        # Gruppiere nach Kategorie
        by_category: dict[FileCategory, list[FSNode]] = {}
        for node in self._nodes.values():
            by_category.setdefault(node.category, []).append(node)

        category_labels = {
            FileCategory.CORE_BRAIN: "🧠 Nervensystem (brain_core/)",
            FileCategory.CORE_EGO: "🫀 Ich-Bewusstsein (brain_ego/)",
            FileCategory.CORE_MEMORY: "💾 Gedaechtnis (brain_memory_ui/)",
            FileCategory.CORE_SHARED: "🔗 Geteilter Code (shared/)",
            FileCategory.EXECUTIVE: "🤖 Executive Arm (executive_arm/)",
            FileCategory.EVOLUTION: "🧬 Evolution Lab",
            FileCategory.GENERATED_PLUGIN: "🔌 Generierte Plugins",
            FileCategory.CONFIG: "⚙️ Konfiguration",
            FileCategory.DATA: "📦 Daten",
            FileCategory.UI: "📱 Interface (Tablet/Dashboard)",
            FileCategory.INFRA: "🏗️ Infrastruktur (Docker/MQTT)",
            FileCategory.TEST: "🧪 Tests",
            FileCategory.DOCS: "📖 Dokumentation",
            FileCategory.UNKNOWN: "❓ Sonstiges",
        }

        for cat in FileCategory:
            nodes = by_category.get(cat, [])
            if not nodes:
                continue

            label = category_labels.get(cat, cat.value)
            dirs = [n for n in nodes if n.is_dir]
            files = [n for n in nodes if not n.is_dir]

            lines.append(f"  {label}:")
            for d in sorted(dirs, key=lambda n: n.path):
                lines.append(f"    📁 {d.path}/ ({d.children_count} items)")
            for f in sorted(files, key=lambda n: n.path)[:20]:  # Max 20 files per cat
                size_kb = f.size_bytes / 1024
                lines.append(f"    📄 {f.path} ({size_kb:.0f}KB)")
            if len(files) > 20:
                lines.append(f"    ... und {len(files) - 20} weitere Dateien")
            lines.append("")

            if len(lines) >= max_lines:
                lines.append(f"  [... gekuerzt, {len(self._nodes)} Eintraege gesamt]")
                break

        return "\n".join(lines)

    def to_tree(self, max_depth: int = 3) -> str:
        """Klassische Baumansicht (fuer Dashboard/Debug)."""
        lines: list[str] = [str(self._root)]
        dirs_only = sorted(
            [n for n in self._nodes.values() if n.is_dir],
            key=lambda n: n.path,
        )
        for node in dirs_only:
            depth = node.path.count("/")
            if depth > max_depth:
                continue
            indent = "  " * (depth + 1)
            lines.append(f"{indent}📁 {node.name}/")

        return "\n".join(lines)

    # ══════════════════════════════════════════════════════════════════
    #  QUERY API
    # ══════════════════════════════════════════════════════════════════

    def find(self, pattern: str) -> list[FSNode]:
        """Finde Dateien/Ordner die einem Pattern entsprechen."""
        import fnmatch
        return [
            node for node in self._nodes.values()
            if fnmatch.fnmatch(node.name, pattern) or fnmatch.fnmatch(node.path, pattern)
        ]

    def get_category(self, rel_path: str) -> FileCategory:
        """Kategorie eines Pfades bestimmen."""
        node = self._nodes.get(rel_path)
        if node:
            return node.category
        return _categorize(rel_path)

    def get_recent_changes(self, limit: int = 20) -> list[dict]:
        """Letzte Dateisystem-Aenderungen."""
        return list(reversed(self._change_events[-limit:]))

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def stats(self) -> dict:
        by_cat = {}
        for node in self._nodes.values():
            by_cat[node.category.value] = by_cat.get(node.category.value, 0) + 1
        return {
            "total_nodes": len(self._nodes),
            "last_scan": self._last_scan,
            "by_category": by_cat,
            "recent_changes": len(self._change_events),
        }
