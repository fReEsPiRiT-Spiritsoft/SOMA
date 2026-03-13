"""
SOMA-AI Toolset — Tool-Definitionen fuer den LangGraph Agent
================================================================
Der Agent (agency.py) bekommt keine rohen Module —
er bekommt sauber definierte Tools mit:
  - Name + Beschreibung (fuer LLM-Tool-Calling)
  - Input-Schema (Pydantic)
  - Ausfuehrungsfunktion (async)
  - Automatischer Policy-Check eingebaut

Jedes Tool ist ein Wrapper um die echten Module:
  terminal   → shell_execute, read_file, write_file, search
  browser    → navigate, screenshot
  bluetooth  → scan, connect, read, write
  filesystem → scan, find, get_tree
  memory     → remember, recall, diary
  ha_call    → smart home steuerung

Non-Negotiable:
  - Jedes Tool geht durch PolicyEngine
  - Jedes Tool ist async
  - Jedes Tool returned ein strukturiertes Ergebnis
  - Kein Tool darf den Event-Loop blockieren
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Optional

import structlog

from executive_arm.policy_engine import PolicyEngine
from executive_arm.terminal import SecureTerminal
from executive_arm.filesystem_map import FilesystemMap
from executive_arm.browser import HeadlessBrowser
from executive_arm.bluetooth import BLEManager

logger = structlog.get_logger("soma.executive.toolset")


# ── Tool Result ──────────────────────────────────────────────────────────

@dataclass
class ToolResult:
    """Standardisiertes Ergebnis eines Tool-Aufrufs."""
    tool_name: str
    success: bool
    output: str              # Menschenlesbarer Output fuer LLM
    data: dict = field(default_factory=dict)  # Strukturierte Daten
    error: str = ""
    was_allowed: bool = True
    policy_message: str = ""

    def to_llm_string(self) -> str:
        """Formatiere fuer LLM-Kontext."""
        if not self.was_allowed:
            return f"[TOOL:{self.tool_name}] VERWEIGERT: {self.policy_message}"
        if self.error:
            return f"[TOOL:{self.tool_name}] FEHLER: {self.error}"
        return f"[TOOL:{self.tool_name}] {self.output}"


# ── Tool Definition ──────────────────────────────────────────────────────

@dataclass
class ToolDefinition:
    """
    Beschreibung eines Tools fuer das LLM (Tool-Calling).
    Wird dem Agent als verfuegbare Aktion praesentiert.
    """
    name: str
    description: str           # Fuer LLM — was kann dieses Tool?
    parameters: dict           # JSON-Schema der Parameter
    execute_fn: Callable[..., Awaitable[ToolResult]] | None = None
    category: str = "general"  # terminal, browser, bluetooth, filesystem, memory
    risk_hint: str = "safe"    # safe, low, medium, high


# ══════════════════════════════════════════════════════════════════════════
#  TOOLSET — Alle Tools die der Agent nutzen kann
# ══════════════════════════════════════════════════════════════════════════

class Toolset:
    """
    Sammlung aller verfuegbaren Tools fuer den LangGraph Agent.
    
    Registriert, verwaltet und fuehrt Tools aus.
    Jedes Tool hat:
      - Einen Namen (fuer Tool-Calling)
      - Eine Beschreibung (fuer LLM)
      - Ein Input-Schema (JSON-Schema)
      - Eine execute()-Funktion
    
    Usage:
        toolset = Toolset(policy_engine, terminal, filesystem, browser, ble)
        tools = toolset.get_tool_descriptions()   # → fuer LLM System-Prompt
        result = await toolset.execute("shell_execute", {"command": "ls"})
    """

    def __init__(
        self,
        policy_engine: PolicyEngine,
        terminal: SecureTerminal,
        filesystem: FilesystemMap,
        browser: Optional[HeadlessBrowser] = None,
        ble: Optional[BLEManager] = None,
    ):
        self._policy = policy_engine
        self._terminal = terminal
        self._filesystem = filesystem
        self._browser = browser
        self._ble = ble

        # ── Tool Registry ────────────────────────────────────────────
        self._tools: dict[str, ToolDefinition] = {}

        # ── Stats ────────────────────────────────────────────────────
        self._total_calls: int = 0
        self._calls_by_tool: dict[str, int] = {}

        # Tools registrieren
        self._register_all_tools()

        logger.info(
            "toolset_initialized",
            tools_registered=len(self._tools),
            tools=list(self._tools.keys()),
        )

    # ══════════════════════════════════════════════════════════════════
    #  TOOL REGISTRATION
    # ══════════════════════════════════════════════════════════════════

    def _register_all_tools(self) -> None:
        """Registriere alle verfuegbaren Tools."""

        # ── Terminal Tools ───────────────────────────────────────────
        self._register(ToolDefinition(
            name="shell_execute",
            description=(
                "Fuehre ein Shell-Command aus. Nutze dies fuer: "
                "Dateien anzeigen, Programme starten, System-Informationen lesen, "
                "Dateien kopieren/verschieben. Alle Commands werden sicherheitsgeprueft."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Das Shell-Command (z.B. 'ls -la', 'cat file.py', 'grep pattern *.py')",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Warum dieses Command noetig ist",
                    },
                },
                "required": ["command"],
            },
            execute_fn=self._exec_shell,
            category="terminal",
            risk_hint="medium",
        ))

        self._register(ToolDefinition(
            name="read_file",
            description=(
                "Lese den Inhalt einer Datei. Besser als 'cat' wenn du nur "
                "den Dateiinhalt brauchst."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Pfad zur Datei (relativ zum SOMA-Root oder absolut)",
                    },
                },
                "required": ["filepath"],
            },
            execute_fn=self._exec_read_file,
            category="terminal",
            risk_hint="safe",
        ))

        self._register(ToolDefinition(
            name="write_file",
            description=(
                "Schreibe Inhalt in eine Datei. Erstellt automatisch ein Backup (.bak) "
                "bei Kern-Dateien. Nutze dies fuer Code-Aenderungen, Config-Updates, etc."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Pfad zur Datei",
                    },
                    "content": {
                        "type": "string",
                        "description": "Der neue Dateiinhalt",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Warum diese Aenderung noetig ist",
                    },
                },
                "required": ["filepath", "content"],
            },
            execute_fn=self._exec_write_file,
            category="terminal",
            risk_hint="high",
        ))

        self._register(ToolDefinition(
            name="search_files",
            description=(
                "Suche nach einem Pattern in Dateien (wie grep). "
                "Nützlich um Code-Stellen zu finden."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Suchbegriff oder Regex",
                    },
                    "directory": {
                        "type": "string",
                        "description": "Verzeichnis zum Suchen (default: SOMA-Root)",
                        "default": ".",
                    },
                    "file_type": {
                        "type": "string",
                        "description": "Dateityp-Filter (z.B. '*.py', '*.yml')",
                        "default": "*.py",
                    },
                },
                "required": ["pattern"],
            },
            execute_fn=self._exec_search,
            category="terminal",
            risk_hint="safe",
        ))

        # ── Filesystem Tools ─────────────────────────────────────────
        self._register(ToolDefinition(
            name="filesystem_scan",
            description=(
                "Scanne das SOMA-Dateisystem und zeige die Struktur. "
                "Gibt dir eine Uebersicht ueber alle Module, Plugins, Configs."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "max_lines": {
                        "type": "integer",
                        "description": "Maximale Zeilen in der Ausgabe",
                        "default": 60,
                    },
                },
            },
            execute_fn=self._exec_fs_scan,
            category="filesystem",
            risk_hint="safe",
        ))

        self._register(ToolDefinition(
            name="filesystem_find",
            description=(
                "Finde Dateien nach Name/Pattern im SOMA-Verzeichnis."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Dateiname oder Glob-Pattern (z.B. '*.py', 'config*')",
                    },
                },
                "required": ["pattern"],
            },
            execute_fn=self._exec_fs_find,
            category="filesystem",
            risk_hint="safe",
        ))

        # ── Browser Tools (nur wenn verfuegbar) ─────────────────────
        if self._browser is not None:
            self._register(ToolDefinition(
                name="browser_navigate",
                description=(
                    "Oeffne eine Webseite und extrahiere den Text-Inhalt. "
                    "Nutze dies um Informationen aus dem Internet zu lesen. "
                    "KEINE Formulare ausfuellen, KEINE Logins."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Die URL zum Oeffnen",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Warum diese Seite gelesen werden soll",
                        },
                    },
                    "required": ["url"],
                },
                execute_fn=self._exec_browser_navigate,
                category="browser",
                risk_hint="low",
            ))

            self._register(ToolDefinition(
                name="browser_screenshot",
                description=(
                    "Mache einen Screenshot einer Webseite. "
                    "Nützlich fuer visuelle Informationen."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Die URL fuer den Screenshot",
                        },
                    },
                    "required": ["url"],
                },
                execute_fn=self._exec_browser_screenshot,
                category="browser",
                risk_hint="safe",
            ))

        # ── Bluetooth Tools (nur wenn verfuegbar) ────────────────────
        if self._ble is not None:
            self._register(ToolDefinition(
                name="ble_scan",
                description=(
                    "Scanne nach Bluetooth-Geraeten in der Naehe. "
                    "Zeigt Name, Adresse und Signalstaerke."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "duration": {
                            "type": "number",
                            "description": "Scan-Dauer in Sekunden",
                            "default": 10,
                        },
                    },
                },
                execute_fn=self._exec_ble_scan,
                category="bluetooth",
                risk_hint="safe",
            ))

            self._register(ToolDefinition(
                name="ble_connect",
                description="Verbinde mit einem BLE-Geraet ueber seine MAC-Adresse.",
                parameters={
                    "type": "object",
                    "properties": {
                        "address": {
                            "type": "string",
                            "description": "MAC-Adresse des Geraets (z.B. 'AA:BB:CC:DD:EE:FF')",
                        },
                    },
                    "required": ["address"],
                },
                execute_fn=self._exec_ble_connect,
                category="bluetooth",
                risk_hint="low",
            ))

    def _register(self, tool: ToolDefinition) -> None:
        """Registriere ein einzelnes Tool."""
        self._tools[tool.name] = tool

    # ══════════════════════════════════════════════════════════════════
    #  EXECUTE — Tool ausfuehren
    # ══════════════════════════════════════════════════════════════════

    async def execute(
        self,
        tool_name: str,
        arguments: dict,
        agent_goal: str = "",
    ) -> ToolResult:
        """
        Fuehre ein Tool anhand seines Namens aus.
        
        Args:
            tool_name: Name des Tools (z.B. "shell_execute")
            arguments: Parameter als Dict
            agent_goal: Uebergeordnetes Ziel (fuer Audit)
        
        Returns:
            ToolResult mit Ergebnis
        """
        self._total_calls += 1
        self._calls_by_tool[tool_name] = self._calls_by_tool.get(tool_name, 0) + 1

        tool = self._tools.get(tool_name)
        if tool is None:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output="",
                error=f"Unbekanntes Tool: {tool_name}. Verfuegbar: {list(self._tools.keys())}",
            )

        if tool.execute_fn is None:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output="",
                error=f"Tool '{tool_name}' hat keine Ausfuehrungsfunktion",
            )

        try:
            result = await tool.execute_fn(
                agent_goal=agent_goal,
                **arguments,
            )
            return result
        except TypeError as exc:
            # Falsche Parameter
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output="",
                error=f"Falsche Parameter fuer '{tool_name}': {exc}",
            )
        except Exception as exc:
            logger.error("tool_execution_error", tool=tool_name, error=str(exc))
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output="",
                error=str(exc),
            )

    # ══════════════════════════════════════════════════════════════════
    #  TOOL DESCRIPTIONS — Fuer LLM System-Prompt
    # ══════════════════════════════════════════════════════════════════

    def get_tool_descriptions(self) -> str:
        """
        Generiere LLM-lesbaren Text ueber verfuegbare Tools.
        Wird in den System-Prompt des Agents eingebaut.
        """
        lines: list[str] = [
            "VERFUEGBARE WERKZEUGE:",
            "Du kannst folgende Tools aufrufen um Aktionen auszufuehren.",
            "Jedes Tool wird sicherheitsgeprueft bevor es ausgefuehrt wird.",
            "",
        ]

        by_category: dict[str, list[ToolDefinition]] = {}
        for tool in self._tools.values():
            by_category.setdefault(tool.category, []).append(tool)

        cat_labels = {
            "terminal": "🖥️ Terminal & Dateien",
            "filesystem": "📁 Dateisystem",
            "browser": "🌐 Web-Browser",
            "bluetooth": "📶 Bluetooth",
            "memory": "💾 Gedaechtnis",
            "general": "⚙️ Allgemein",
        }

        for cat, tools in by_category.items():
            label = cat_labels.get(cat, cat)
            lines.append(f"── {label} ──")
            for tool in tools:
                risk = {"safe": "🟢", "low": "🔵", "medium": "🟡", "high": "🟠"}.get(
                    tool.risk_hint, "⚪"
                )
                lines.append(f"  {risk} {tool.name}: {tool.description}")

                # Parameter kurz listen
                props = tool.parameters.get("properties", {})
                required = set(tool.parameters.get("required", []))
                for pname, pinfo in props.items():
                    req = " [PFLICHT]" if pname in required else ""
                    desc = pinfo.get("description", "")
                    lines.append(f"      - {pname}: {desc}{req}")
            lines.append("")

        return "\n".join(lines)

    def get_tool_schemas(self) -> list[dict]:
        """
        Gib Tool-Schemas als JSON zurueck (fuer LLM Tool-Calling API).
        
        Kompatibel mit OpenAI Function-Calling Format.
        """
        schemas = []
        for tool in self._tools.values():
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            })
        return schemas

    # ══════════════════════════════════════════════════════════════════
    #  TOOL IMPLEMENTATIONS
    # ══════════════════════════════════════════════════════════════════

    async def _exec_shell(
        self,
        command: str,
        reason: str = "",
        agent_goal: str = "",
    ) -> ToolResult:
        result = await self._terminal.execute(
            command=command,
            reason=reason,
            agent_goal=agent_goal,
        )
        if not result.was_allowed:
            return ToolResult(
                tool_name="shell_execute",
                success=False,
                output="",
                was_allowed=False,
                policy_message=result.policy_message,
            )
        return ToolResult(
            tool_name="shell_execute",
            success=result.exit_code == 0,
            output=result.stdout or result.stderr,
            data={"exit_code": result.exit_code, "duration_ms": result.duration_ms},
            error=result.stderr if result.exit_code != 0 else "",
        )

    async def _exec_read_file(
        self,
        filepath: str,
        agent_goal: str = "",
    ) -> ToolResult:
        result = await self._terminal.read_file(filepath)
        if not result.was_allowed:
            return ToolResult(
                tool_name="read_file",
                success=False,
                output="",
                was_allowed=False,
                policy_message=result.policy_message,
            )
        return ToolResult(
            tool_name="read_file",
            success=result.exit_code == 0,
            output=result.stdout,
            error=result.stderr if result.exit_code != 0 else "",
        )

    async def _exec_write_file(
        self,
        filepath: str,
        content: str,
        reason: str = "",
        agent_goal: str = "",
    ) -> ToolResult:
        result = await self._terminal.write_file(
            filepath=filepath,
            content=content,
            reason=reason,
            agent_goal=agent_goal,
        )
        if not result.was_allowed:
            return ToolResult(
                tool_name="write_file",
                success=False,
                output="",
                was_allowed=False,
                policy_message=result.policy_message,
            )
        return ToolResult(
            tool_name="write_file",
            success=result.exit_code == 0,
            output=result.stdout,
            data={"backup_path": result.backup_path},
            error=result.stderr if result.exit_code != 0 else "",
        )

    async def _exec_search(
        self,
        pattern: str,
        directory: str = ".",
        file_type: str = "*.py",
        agent_goal: str = "",
    ) -> ToolResult:
        result = await self._terminal.search_in_files(pattern, directory, file_type)
        return ToolResult(
            tool_name="search_files",
            success=result.exit_code == 0,
            output=result.stdout or "Keine Treffer",
            error=result.stderr if result.exit_code != 0 and result.exit_code != 1 else "",
        )

    async def _exec_fs_scan(
        self,
        max_lines: int = 60,
        agent_goal: str = "",
    ) -> ToolResult:
        if not self._filesystem.node_count:
            await self._filesystem.scan()
        context = self._filesystem.to_llm_context(max_lines=max_lines)
        return ToolResult(
            tool_name="filesystem_scan",
            success=True,
            output=context,
            data=self._filesystem.stats,
        )

    async def _exec_fs_find(
        self,
        pattern: str,
        agent_goal: str = "",
    ) -> ToolResult:
        if not self._filesystem.node_count:
            await self._filesystem.scan()
        nodes = self._filesystem.find(pattern)
        if not nodes:
            return ToolResult(
                tool_name="filesystem_find",
                success=True,
                output=f"Keine Dateien gefunden fuer Pattern '{pattern}'",
            )
        lines = [f"Gefunden ({len(nodes)} Treffer):"]
        for n in nodes[:30]:
            kind = "📁" if n.is_dir else "📄"
            lines.append(f"  {kind} {n.path} [{n.category.value}]")
        return ToolResult(
            tool_name="filesystem_find",
            success=True,
            output="\n".join(lines),
        )

    async def _exec_browser_navigate(
        self,
        url: str,
        reason: str = "",
        agent_goal: str = "",
    ) -> ToolResult:
        if not self._browser:
            return ToolResult(
                tool_name="browser_navigate",
                success=False,
                output="",
                error="Browser nicht verfuegbar",
            )
        result = await self._browser.navigate(url, reason, agent_goal)
        if not result.was_allowed:
            return ToolResult(
                tool_name="browser_navigate",
                success=False,
                output="",
                was_allowed=False,
                policy_message=result.policy_message,
            )
        return ToolResult(
            tool_name="browser_navigate",
            success=not result.error,
            output=f"Titel: {result.title}\n\n{result.text_content}",
            data={"url": result.url, "status": result.status_code},
            error=result.error,
        )

    async def _exec_browser_screenshot(
        self,
        url: str,
        agent_goal: str = "",
    ) -> ToolResult:
        if not self._browser:
            return ToolResult(
                tool_name="browser_screenshot",
                success=False,
                output="",
                error="Browser nicht verfuegbar",
            )
        result = await self._browser.screenshot(url, agent_goal=agent_goal)
        return ToolResult(
            tool_name="browser_screenshot",
            success=not result.error,
            output=f"Screenshot: {result.screenshot_path}" if result.screenshot_path else "",
            data={"path": result.screenshot_path, "url": result.url},
            error=result.error,
        )

    async def _exec_ble_scan(
        self,
        duration: float = 10,
        agent_goal: str = "",
    ) -> ToolResult:
        if not self._ble:
            return ToolResult(
                tool_name="ble_scan",
                success=False,
                output="",
                error="Bluetooth nicht verfuegbar",
            )
        result = await self._ble.scan(duration=duration, agent_goal=agent_goal)
        if not result.was_allowed:
            return ToolResult(
                tool_name="ble_scan",
                success=False,
                output="",
                was_allowed=False,
                policy_message=result.policy_message,
            )
        if result.devices:
            lines = [f"BLE-Geraete gefunden ({len(result.devices)}):"]
            for d in result.devices[:20]:
                lines.append(f"  📶 {d.name} ({d.address}) RSSI: {d.rssi}dBm")
            return ToolResult(
                tool_name="ble_scan",
                success=True,
                output="\n".join(lines),
            )
        return ToolResult(
            tool_name="ble_scan",
            success=True,
            output="Keine BLE-Geraete in Reichweite gefunden",
        )

    async def _exec_ble_connect(
        self,
        address: str,
        agent_goal: str = "",
    ) -> ToolResult:
        if not self._ble:
            return ToolResult(
                tool_name="ble_connect",
                success=False,
                output="",
                error="Bluetooth nicht verfuegbar",
            )
        result = await self._ble.connect(address, agent_goal=agent_goal)
        return ToolResult(
            tool_name="ble_connect",
            success=result.success,
            output=result.data,
            error=result.error,
        )

    # ══════════════════════════════════════════════════════════════════
    #  STATS
    # ══════════════════════════════════════════════════════════════════

    @property
    def stats(self) -> dict:
        return {
            "total_calls": self._total_calls,
            "by_tool": self._calls_by_tool,
            "registered_tools": len(self._tools),
            "tool_names": list(self._tools.keys()),
        }

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())
