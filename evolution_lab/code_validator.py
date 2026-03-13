"""
SOMA-AI Code Validator — Das Immunsystem des Evolution Lab
=============================================================
Prueft KI-generierten Code BEVOR er ausgefuehrt wird.

Drei Sicherheitsschichten:
  1. Forbidden Pattern Checker — Regex + String-Matching gegen gefaehrliche Muster
  2. AST Structural Validator  — Syntax-Baum Analyse (keine exec/eval/os.system)
  3. Plugin Structure Verifier — Prüft ob Pflicht-Felder (__version__, execute()) vorhanden

Zusaetzlich:
  - Black Formatter — Formatiert generierten Code sauber
  - Gibt detaillierte Fehler-Reports zurueck fuer LLM-Retry

Non-Negotiable:
  - Kein Code kommt ungeprüft durch
  - Alles synchron (kein async noetig — reine Analyse)
  - Jedes Finding wird geloggt
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import structlog

logger = structlog.get_logger("soma.evolution.validator")


# ── Severity Levels ──────────────────────────────────────────────────────

class Severity(str, Enum):
    """Schweregrad eines Findings."""
    CRITICAL = "critical"   # Code wird SOFORT abgelehnt (eval, exec, rm -rf)
    HIGH = "high"           # Wahrscheinlich gefaehrlich, Ablehnung
    MEDIUM = "medium"       # Verdaechtig, warnen aber durchlassen
    LOW = "low"             # Style-Issue, nur Info
    INFO = "info"           # Reiner Hinweis


@dataclass
class ValidationFinding:
    """Ein einzelnes Fund-Ergebnis der Validierung."""
    severity: Severity
    category: str           # "forbidden_pattern", "ast_violation", "structure"
    message: str
    line: int = 0
    column: int = 0
    pattern: str = ""       # Welches Pattern hat gematched
    suggestion: str = ""    # Vorschlag fuer den LLM-Retry


@dataclass
class ValidationReport:
    """Gesamtergebnis der Code-Validierung."""
    is_safe: bool = True
    is_valid_structure: bool = True
    findings: list[ValidationFinding] = field(default_factory=list)
    formatted_code: str = ""   # Black-formatierter Code (leer wenn Fehler)
    raw_code: str = ""

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.CRITICAL)

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.HIGH)

    @property
    def error_summary(self) -> str:
        """Zusammenfassung der Fehler fuer LLM-Retry-Prompt."""
        errors = [f for f in self.findings if f.severity in (Severity.CRITICAL, Severity.HIGH)]
        if not errors:
            return ""
        lines = []
        for e in errors:
            loc = f" (Zeile {e.line})" if e.line else ""
            sug = f" → Vorschlag: {e.suggestion}" if e.suggestion else ""
            lines.append(f"- [{e.severity.value.upper()}] {e.message}{loc}{sug}")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
#  FORBIDDEN PATTERN CHECKER
# ══════════════════════════════════════════════════════════════════════════

# Muster die NIEMALS in generiertem Code auftauchen duerfen
_FORBIDDEN_PATTERNS: list[tuple[str, str, Severity, str]] = [
    # (Regex-Pattern, Beschreibung, Severity, Vorschlag)

    # ── Code Execution / Injection ──
    (
        r'\beval\s*\(',
        "eval() ist verboten — ermoeglicht beliebige Code-Ausfuehrung",
        Severity.CRITICAL,
        "Nutze ast.literal_eval() fuer sichere Auswertung oder parse den Input manuell",
    ),
    (
        r'\bexec\s*\(',
        "exec() ist verboten — ermoeglicht beliebige Code-Ausfuehrung",
        Severity.CRITICAL,
        "Refaktoriere den Code so dass exec() nicht noetig ist",
    ),
    (
        r'\bcompile\s*\(.+["\']exec["\']\s*\)',
        "compile() mit 'exec' mode ist verboten",
        Severity.CRITICAL,
        "Entferne die dynamische Code-Kompilierung",
    ),
    (
        r'\b__import__\s*\(',
        "__import__() ist verboten — nutze regulaere import-Statements",
        Severity.CRITICAL,
        "Nutze 'import modulname' oder 'from modul import name'",
    ),

    # ── Gefaehrliche OS-Operationen ──
    (
        r'\bos\.system\s*\(',
        "os.system() ist verboten — unsicher und nicht async",
        Severity.HIGH,
        "Nutze asyncio.create_subprocess_exec() stattdessen",
    ),
    (
        r'\bos\.popen\s*\(',
        "os.popen() ist verboten — unsicher",
        Severity.HIGH,
        "Nutze asyncio.create_subprocess_exec() stattdessen",
    ),
    (
        r'\bsubprocess\.call\s*\(',
        "subprocess.call() ist verboten — blockiert und unsicher",
        Severity.HIGH,
        "Nutze asyncio.create_subprocess_exec() stattdessen",
    ),
    (
        r'\bsubprocess\.Popen\s*\(',
        "subprocess.Popen() ist verboten — nutze async Variante",
        Severity.HIGH,
        "Nutze asyncio.create_subprocess_exec() stattdessen",
    ),
    (
        r'\bsubprocess\.run\s*\(',
        "subprocess.run() ist verboten — blockiert und nicht async",
        Severity.HIGH,
        "Nutze asyncio.create_subprocess_exec() stattdessen",
    ),

    # ── Gefaehrliche Dateisystem-Operationen ──
    (
        r'\bshutil\.rmtree\s*\(\s*["\']/',
        "shutil.rmtree() auf Root-Pfad ist verboten",
        Severity.CRITICAL,
        "Loeschoperationen auf System-Pfaden sind nicht erlaubt",
    ),
    (
        r'\bos\.remove\s*\(\s*["\']/(etc|boot|usr|bin|sbin|proc|sys|dev)',
        "Loeschen von System-Dateien ist verboten",
        Severity.CRITICAL,
        "System-Dateien duerfen nicht geloescht werden",
    ),
    (
        r'open\s*\(\s*["\'](/etc/shadow|/etc/passwd|/etc/sudoers)',
        "Zugriff auf sensitive System-Dateien ist verboten",
        Severity.CRITICAL,
        "Zugriff auf Credentials- und Auth-Dateien nicht erlaubt",
    ),
    (
        r'rm\s+-rf\s+/',
        "rm -rf / in String-Literalen erkannt",
        Severity.CRITICAL,
        "Keine destruktiven Shell-Befehle auf System-Ebene",
    ),

    # ── Netzwerk-Exfiltration ──
    (
        r'\brequests\.(post|put)\s*\(\s*["\']https?://(?!localhost|127\.0\.0\.1|192\.168)',
        "HTTP POST/PUT an externe Server ist verdaechtig",
        Severity.MEDIUM,
        "SOMA arbeitet lokal — externe Daten-Uebertragung vermeiden",
    ),
    (
        r'\bsocket\.socket\s*\(',
        "Raw-Socket Erstellung ist verdaechtig",
        Severity.MEDIUM,
        "Nutze aiohttp oder httpx fuer Netzwerk-Operationen",
    ),

    # ── Privilege Escalation ──
    (
        r'\bsudo\b',
        "sudo ist in Plugins nicht erlaubt",
        Severity.HIGH,
        "Plugins laufen ohne Root-Rechte — sudo entfernen",
    ),
    (
        r'\bos\.setuid\s*\(',
        "setuid() ist verboten — keine Privilege Escalation",
        Severity.CRITICAL,
        "Plugins laufen als normaler User",
    ),
    (
        r'\bos\.setgid\s*\(',
        "setgid() ist verboten — keine Privilege Escalation",
        Severity.CRITICAL,
        "Plugins laufen als normaler User",
    ),
    (
        r'\bctypes\b',
        "ctypes ist verboten — kein direkter C-Zugriff in Plugins",
        Severity.HIGH,
        "Nutze Pure-Python oder subprocess fuer System-Interaktionen",
    ),

    # ── Endlos-Schleifen Schutz ──
    (
        r'\bwhile\s+True\s*:(?!\s*#\s*event.loop)',
        "while True ohne erkennbaren Break-Mechanismus",
        Severity.MEDIUM,
        "Stelle sicher dass ein break/return Mechanismus existiert",
    ),
]

# Module die NICHT importiert werden duerfen
_BANNED_IMPORTS: set[str] = {
    "ctypes",
    "ctypes.util",
    "multiprocessing",   # Kann Prozesse forken unkontrolliert
    "pickle",            # Unsichere Deserialisierung
    "marshal",           # Unsichere Deserialisierung
    "shelve",            # Nutzt pickle intern
    "code",              # Interaktive Code-Ausfuehrung
    "codeop",            # Code-Kompilierung
    "compileall",        # Batch-Kompilierung
    "py_compile",        # Code-Kompilierung
}


class ForbiddenPatternChecker:
    """
    Prueft Code gegen eine Liste verbotener Muster.

    Arbeitet in zwei Phasen:
      1. Regex-basierter Text-Scan (schnell, Breitband)
      2. Kommentar-Bereinigung (Patterns in Kommentaren ignorieren)
    """

    def __init__(self, extra_patterns: list[tuple[str, str, Severity, str]] | None = None):
        self._patterns = list(_FORBIDDEN_PATTERNS)
        if extra_patterns:
            self._patterns.extend(extra_patterns)
        # Pre-compiled Regexes fuer Performance
        self._compiled = [
            (re.compile(p, re.MULTILINE | re.IGNORECASE), desc, sev, sug)
            for p, desc, sev, sug in self._patterns
        ]
        self._banned_imports = set(_BANNED_IMPORTS)

    def check(self, code: str) -> list[ValidationFinding]:
        """Pruefe Code gegen alle verbotenen Muster."""
        findings: list[ValidationFinding] = []

        # Zeilen-Index aufbauen fuer Line-Nummern
        lines = code.splitlines()
        non_comment_lines = set()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Zeilen die NUR Kommentare sind ueberspringen
            if stripped.startswith("#"):
                continue
            # Zeilen in Docstrings zaehlen wir trotzdem —
            # gefaehrlicher Code in Docstrings ist auch verdaechtig
            non_comment_lines.add(i)

        # Phase 1: Regex-Scan
        for regex, desc, severity, suggestion in self._compiled:
            for match in regex.finditer(code):
                # Zeile berechnen
                line_num = code[:match.start()].count('\n') + 1

                # Wenn in reiner Kommentar-Zeile → Severity runterstufen
                effective_severity = severity
                if line_num not in non_comment_lines:
                    effective_severity = Severity.INFO

                findings.append(ValidationFinding(
                    severity=effective_severity,
                    category="forbidden_pattern",
                    message=desc,
                    line=line_num,
                    pattern=match.group(0)[:80],
                    suggestion=suggestion,
                ))

        return findings


# ══════════════════════════════════════════════════════════════════════════
#  AST STRUCTURAL VALIDATOR
# ══════════════════════════════════════════════════════════════════════════

class ASTValidator:
    """
    Prueft den Abstract Syntax Tree des generierten Codes.

    Geht tiefer als Regex:
      - Erkennt Import von verbotenen Modulen
      - Erkennt Funktionsaufrufe gefaehrlicher Builtins
      - Prueft ob async def execute() vorhanden ist
      - Prueft Pflicht-Metadaten (__version__, __author__, __description__)
    """

    # Gefaehrliche Builtins die nicht aufgerufen werden duerfen
    DANGEROUS_CALLS: set[str] = {
        "eval", "exec", "compile", "__import__",
        "globals", "locals", "vars",
        "delattr", "setattr",   # Attribut-Manipulation
        "breakpoint",            # Debug-Zugang
    }

    # Attribute auf os/sys die nicht aufgerufen werden duerfen
    DANGEROUS_ATTR_CALLS: dict[str, set[str]] = {
        "os": {"system", "popen", "exec", "execl", "execle", "execlp",
               "execv", "execve", "execvp", "execvpe", "fork",
               "setuid", "setgid", "kill", "killpg", "remove", "unlink",
               "rmdir"},
        "sys": {"exit"},
        "shutil": {"rmtree", "move"},
    }

    def validate(self, code: str) -> list[ValidationFinding]:
        """Validiere den AST des Codes."""
        findings: list[ValidationFinding] = []

        # AST parsen
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            findings.append(ValidationFinding(
                severity=Severity.CRITICAL,
                category="ast_violation",
                message=f"Syntax-Fehler: {exc.msg}",
                line=exc.lineno or 0,
                column=exc.offset or 0,
                suggestion="Korrigiere den Syntax-Fehler im Code",
            ))
            return findings

        # AST-Walk: Jeden Node pruefen
        for node in ast.walk(tree):
            # ── Import-Check ─────────────────────────────────
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in _BANNED_IMPORTS:
                        findings.append(ValidationFinding(
                            severity=Severity.HIGH,
                            category="ast_violation",
                            message=f"Import von '{alias.name}' ist verboten",
                            line=node.lineno,
                            suggestion=f"Entferne 'import {alias.name}' — nutze sichere Alternativen",
                        ))

            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module in _BANNED_IMPORTS:
                    findings.append(ValidationFinding(
                        severity=Severity.HIGH,
                        category="ast_violation",
                        message=f"Import von '{node.module}' ist verboten",
                        line=node.lineno,
                        suggestion=f"Entferne 'from {node.module} import ...' — nutze sichere Alternativen",
                    ))

            # ── Gefaehrliche Funktionsaufrufe ────────────────
            elif isinstance(node, ast.Call):
                func_name = self._get_call_name(node)
                if func_name in self.DANGEROUS_CALLS:
                    findings.append(ValidationFinding(
                        severity=Severity.CRITICAL,
                        category="ast_violation",
                        message=f"Aufruf von '{func_name}()' ist verboten",
                        line=node.lineno,
                        suggestion=f"Entferne den Aufruf von {func_name}()",
                    ))

                # Attribut-Calls pruefen (os.system, shutil.rmtree, ...)
                attr_finding = self._check_attr_call(node)
                if attr_finding:
                    findings.append(attr_finding)

        return findings

    def check_structure(self, code: str) -> list[ValidationFinding]:
        """Pruefe ob die Pflicht-Struktur eines SOMA-Plugins eingehalten wird."""
        findings: list[ValidationFinding] = []

        try:
            tree = ast.parse(code)
        except SyntaxError:
            # Syntax-Fehler wird in validate() behandelt
            return findings

        # ── Pflicht-Metadaten pruefen ────────────────────────
        required_meta = {"__version__", "__author__", "__description__"}
        found_meta: set[str] = set()

        # ── Pflicht-Funktionen pruefen ───────────────────────
        required_funcs = {"execute"}       # execute() MUSS existieren
        optional_funcs = {"on_load", "on_unload"}
        found_funcs: set[str] = set()
        async_funcs: set[str] = set()

        for node in ast.iter_child_nodes(tree):
            # Top-Level Assignments: __version__ = "...", etc.
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in required_meta:
                        found_meta.add(target.id)

            # Top-Level FunctionDefs
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found_funcs.add(node.name)
                if isinstance(node, ast.AsyncFunctionDef):
                    async_funcs.add(node.name)

        # Fehlende Metadaten melden
        missing_meta = required_meta - found_meta
        for meta in missing_meta:
            findings.append(ValidationFinding(
                severity=Severity.HIGH,
                category="structure",
                message=f"Pflicht-Metadatum '{meta}' fehlt",
                suggestion=f"Fuege '{meta} = \"...\"' als Top-Level Variable hinzu",
            ))

        # execute() muss existieren
        if "execute" not in found_funcs:
            findings.append(ValidationFinding(
                severity=Severity.CRITICAL,
                category="structure",
                message="Pflicht-Funktion 'execute()' fehlt",
                suggestion="Fuege 'async def execute(*args, **kwargs) -> Any:' hinzu",
            ))

        # execute() und on_load() MUESSEN async sein
        for fn_name in ("execute", "on_load", "on_unload"):
            if fn_name in found_funcs and fn_name not in async_funcs:
                findings.append(ValidationFinding(
                    severity=Severity.HIGH,
                    category="structure",
                    message=f"Funktion '{fn_name}()' muss async sein",
                    suggestion=f"Aendere 'def {fn_name}(...)' zu 'async def {fn_name}(...)'",
                ))

        return findings

    @staticmethod
    def _get_call_name(node: ast.Call) -> str:
        """Extrahiere den Funktionsnamen aus einem Call-Node."""
        if isinstance(node.func, ast.Name):
            return node.func.id
        return ""

    def _check_attr_call(self, node: ast.Call) -> Optional[ValidationFinding]:
        """Pruefe ob ein Attribut-Aufruf gefaehrlich ist (z.B. os.system)."""
        if not isinstance(node.func, ast.Attribute):
            return None

        attr = node.func.attr  # z.B. "system"
        # Pruefen ob der Wert ein bekanntes Modul ist
        if isinstance(node.func.value, ast.Name):
            module = node.func.value.id  # z.B. "os"
            dangerous = self.DANGEROUS_ATTR_CALLS.get(module, set())
            if attr in dangerous:
                return ValidationFinding(
                    severity=Severity.CRITICAL if module == "os" else Severity.HIGH,
                    category="ast_violation",
                    message=f"Aufruf von '{module}.{attr}()' ist verboten",
                    line=node.lineno,
                    suggestion=f"Nutze asyncio.create_subprocess_exec() statt {module}.{attr}()",
                )

        return None


# ══════════════════════════════════════════════════════════════════════════
#  BLACK FORMATTER
# ══════════════════════════════════════════════════════════════════════════

def format_with_black(code: str, line_length: int = 88) -> tuple[str, str]:
    """
    Formatiert Code mit Black.

    Returns: (formatted_code, error_message)
      - Bei Erfolg: (formatierter_code, "")
      - Bei Fehler: (original_code, fehlermeldung)
    """
    try:
        import black
        mode = black.Mode(
            target_versions={black.TargetVersion.PY311},
            line_length=line_length,
        )
        formatted = black.format_str(code, mode=mode)
        return formatted, ""
    except ImportError:
        logger.warning("black_not_installed", msg="black nicht verfuegbar — Code wird unformatiert verwendet")
        return code, ""
    except Exception as exc:
        logger.warning("black_format_error", error=str(exc))
        return code, f"Black-Formatierung fehlgeschlagen: {exc}"


# ══════════════════════════════════════════════════════════════════════════
#  UNIFIED VALIDATOR — Kombiniert alle Pruefungen
# ══════════════════════════════════════════════════════════════════════════

class CodeValidator:
    """
    Zentrale Validierung fuer KI-generierten Code.

    Kombiniert:
      1. ForbiddenPatternChecker (Regex)
      2. ASTValidator (Syntax-Baum)
      3. Plugin-Struktur-Check
      4. Black-Formatierung

    Usage:
        validator = CodeValidator()
        report = validator.validate(code)
        if not report.is_safe:
            print(report.error_summary)
    """

    def __init__(
        self,
        extra_patterns: list[tuple[str, str, Severity, str]] | None = None,
        format_code: bool = True,
        line_length: int = 88,
    ):
        self._pattern_checker = ForbiddenPatternChecker(extra_patterns)
        self._ast_validator = ASTValidator()
        self._format_code = format_code
        self._line_length = line_length

    def validate(self, code: str, check_structure: bool = True) -> ValidationReport:
        """
        Vollstaendige Validierung eines Code-Strings.

        Args:
            code: Der zu pruefende Python-Code
            check_structure: Ob die SOMA-Plugin-Struktur geprueft werden soll

        Returns:
            ValidationReport mit allen Findings + formatiertem Code
        """
        report = ValidationReport(raw_code=code)
        all_findings: list[ValidationFinding] = []

        # ── Phase 1: Forbidden Patterns ──────────────────────────────
        pattern_findings = self._pattern_checker.check(code)
        all_findings.extend(pattern_findings)

        # ── Phase 2: AST Validation ──────────────────────────────────
        ast_findings = self._ast_validator.validate(code)
        all_findings.extend(ast_findings)

        # ── Phase 3: Plugin-Struktur ─────────────────────────────────
        if check_structure:
            struct_findings = self._ast_validator.check_structure(code)
            all_findings.extend(struct_findings)

        # ── Phase 4: Black-Formatierung ──────────────────────────────
        # Nur formatieren wenn keine kritischen Fehler
        has_critical = any(
            f.severity in (Severity.CRITICAL, Severity.HIGH)
            and f.category != "structure"  # Struktur-Fehler verhindern nicht Formatierung
            for f in all_findings
        )

        if not has_critical:
            if self._format_code:
                formatted, fmt_error = format_with_black(code, self._line_length)
                if not fmt_error:
                    report.formatted_code = formatted
                else:
                    report.formatted_code = code
                    all_findings.append(ValidationFinding(
                        severity=Severity.LOW,
                        category="formatting",
                        message=fmt_error,
                    ))
            else:
                report.formatted_code = code
        else:
            report.formatted_code = ""

        # ── Report zusammenstellen ───────────────────────────────────
        report.findings = all_findings
        report.is_safe = not any(
            f.severity in (Severity.CRITICAL, Severity.HIGH)
            for f in all_findings
            if f.category != "structure"   # Struktur ist valid-structure, nicht safety
        )
        report.is_valid_structure = not any(
            f.severity in (Severity.CRITICAL, Severity.HIGH)
            and f.category == "structure"
            for f in all_findings
        )

        # Logging
        if not report.is_safe:
            logger.warning(
                "code_validation_failed",
                critical=report.critical_count,
                high=report.high_count,
                total=len(all_findings),
            )
        else:
            logger.info(
                "code_validation_passed",
                findings=len(all_findings),
                formatted=bool(report.formatted_code),
            )

        return report
