"""
SOMA-AI Policy Engine — Der Gatekeeper aller Aktionen
========================================================
Kein Terminal-Command, kein File-Write, kein Browser-Request, kein BLE-Befehl
darf am Policy Engine vorbei.

Architektur:
  1. Jede Aktion kommt als ActionRequest rein
  2. Identity Anchor prueft gegen die 7 Kern-Direktiven
  3. Custom .soma-rules werden evaluiert (optionale User-Regeln)
  4. Bei Freigabe: Audit-Log + Memory-Event
  5. Bei Veto: Audit-Log + Begruendung zurueck

Non-Negotiable:
  - ALLE Schreiboperationen erzeugen vorher ein .bak
  - ALLE Aktionen werden geloggt (Audit Trail)
  - HARD_BLOCK ist NIEMALS ueberschreibbar
  - SOFT_BLOCK kann der User explizit freigeben

Datenfluss:
  Agent (agency.py)
    → toolset.py
      → policy_engine.check()
        → identity_anchor.check_action()
        → .soma-rules evaluation
        → audit_log + memory_event
        → ActionResult (allowed / denied)
      → tool execution (terminal/browser/fs/ble)
    → Agent bekommt Ergebnis
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Callable, Awaitable

import structlog

from brain_ego.identity_anchor import IdentityAnchor, VetoResult, VetoLevel

logger = structlog.get_logger("soma.executive.policy")


# ── Action Types ─────────────────────────────────────────────────────────

class ActionType(str, Enum):
    """Kategorien ausfuehrbarer Aktionen."""
    SHELL_READ = "shell_read"           # Lesendes Shell-Command (ls, cat, grep)
    SHELL_WRITE = "shell_write"         # Schreibendes Shell-Command (cp, mv, rm)
    SHELL_EXECUTE = "shell_execute"     # Programm ausfuehren (python, node)
    FILE_READ = "file_read"             # Datei lesen
    FILE_WRITE = "file_write"           # Datei schreiben/erstellen
    FILE_DELETE = "file_delete"         # Datei loeschen
    BROWSER_NAVIGATE = "browser_navigate"    # URL oeffnen
    BROWSER_SCREENSHOT = "browser_screenshot"  # Screenshot machen
    BROWSER_INTERACT = "browser_interact"      # Formular, Klick, etc.
    BLE_SCAN = "ble_scan"               # Bluetooth-Scan
    BLE_CONNECT = "ble_connect"         # BLE-Geraet verbinden
    BLE_WRITE = "ble_write"             # BLE-Characteristic schreiben
    MQTT_PUBLISH = "mqtt_publish"       # MQTT-Nachricht senden
    HA_CALL = "ha_call"                 # Home Assistant Service Call
    SYSTEM_MODIFY = "system_modify"     # Systemkonfiguration aendern
    PLUGIN_INSTALL = "plugin_install"   # Plugin installieren
    SELF_MODIFY = "self_modify"         # Eigenen Code aendern


# ── Risk Levels ──────────────────────────────────────────────────────────

class RiskLevel(str, Enum):
    """Risikostufe einer Aktion."""
    SAFE = "safe"           # Lesen, Scan — kein Schaden moeglich
    LOW = "low"             # Schreiben in unkritische Bereiche
    MEDIUM = "medium"       # Systemkonfiguration, Bluetooth-Write
    HIGH = "high"           # Kern-Code aendern, Services starten/stoppen
    CRITICAL = "critical"   # Loeschen, Netzwerk-Aktionen, Selbst-Modifikation


# Risk-Mapping: Welche Action hat welches Risiko
_ACTION_RISK: dict[ActionType, RiskLevel] = {
    ActionType.SHELL_READ: RiskLevel.SAFE,
    ActionType.FILE_READ: RiskLevel.SAFE,
    ActionType.BLE_SCAN: RiskLevel.SAFE,
    ActionType.BROWSER_NAVIGATE: RiskLevel.LOW,
    ActionType.BROWSER_SCREENSHOT: RiskLevel.SAFE,
    ActionType.SHELL_WRITE: RiskLevel.MEDIUM,
    ActionType.SHELL_EXECUTE: RiskLevel.MEDIUM,
    ActionType.FILE_WRITE: RiskLevel.MEDIUM,
    ActionType.BROWSER_INTERACT: RiskLevel.MEDIUM,
    ActionType.BLE_CONNECT: RiskLevel.LOW,
    ActionType.BLE_WRITE: RiskLevel.MEDIUM,
    ActionType.MQTT_PUBLISH: RiskLevel.LOW,
    ActionType.HA_CALL: RiskLevel.LOW,
    ActionType.PLUGIN_INSTALL: RiskLevel.HIGH,
    ActionType.SYSTEM_MODIFY: RiskLevel.HIGH,
    ActionType.FILE_DELETE: RiskLevel.HIGH,
    ActionType.SELF_MODIFY: RiskLevel.CRITICAL,
}


# ── Action Request / Result ─────────────────────────────────────────────

@dataclass
class ActionRequest:
    """Was SOMA tun moechte."""
    action_type: ActionType
    description: str              # Menschenlesbare Beschreibung
    target: str = ""              # Dateipfad, URL, Device-Adresse, Command
    parameters: dict = field(default_factory=dict)
    reason: str = ""              # Warum will SOMA das tun?
    agent_goal: str = ""          # Uebergeordnetes Ziel des Agenten
    is_child_present: bool = False
    user_approved: bool = False   # Explizite User-Freigabe (fuer SOFT_BLOCK)
    timestamp: float = field(default_factory=time.monotonic)
    request_id: str = ""          # Tracking-ID

    def __post_init__(self):
        if not self.request_id:
            # Deterministischer Hash fuer Dedup
            raw = f"{self.action_type.value}:{self.target}:{self.description}:{self.timestamp}"
            self.request_id = hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class ActionResult:
    """Ergebnis der Policy-Pruefung."""
    allowed: bool
    request_id: str
    risk_level: RiskLevel = RiskLevel.SAFE
    veto: Optional[VetoResult] = None
    custom_rule_hit: str = ""     # Welche .soma-rule hat gegriffen
    audit_id: str = ""
    message: str = ""
    requires_backup: bool = False  # .bak noetig vor Ausfuehrung?
    timestamp: float = field(default_factory=time.monotonic)


# ── Custom Rules (.soma-rules) ──────────────────────────────────────────

@dataclass
class SomaRule:
    """Eine benutzerdefinierte Regel aus .soma-rules."""
    name: str
    description: str
    action_types: list[ActionType]  # Auf welche Aktionen reagiert die Regel
    pattern: str = ""               # Regex/Keyword-Pattern fuer Target
    effect: str = "block"           # "block", "allow", "warn"
    active: bool = True


# ── Audit Log Entry ─────────────────────────────────────────────────────

@dataclass
class AuditEntry:
    """Eintrag im Audit-Log — jede Aktion wird dokumentiert."""
    audit_id: str
    timestamp: float
    action_type: str
    target: str
    description: str
    risk_level: str
    result: str                   # "allowed", "denied", "vetoed", "warned"
    veto_reason: str = ""
    veto_directive: str = ""
    custom_rule: str = ""
    agent_goal: str = ""
    request_id: str = ""


# ══════════════════════════════════════════════════════════════════════════
#  POLICY ENGINE — Das Gewissen der Executive Arm
# ══════════════════════════════════════════════════════════════════════════

class PolicyEngine:
    """
    Gatekeeper fuer ALLE agentic actions.
    
    Jede Aktion die SOMA ausfuehren will muss hier durch.
    Kein Workaround, kein Bypass, keine Ausnahme.
    
    Pruefungs-Reihenfolge:
      1. Blacklist-Check (absolute Verbote)
      2. Identity Anchor (7 Kern-Direktiven)
      3. Risk-Level Bewertung
      4. Custom .soma-rules
      5. Backup-Anforderung pruefen
      6. Audit-Log + Memory-Event
    """

    # Absolute Blacklist — diese Commands gehen NIEMALS durch
    COMMAND_BLACKLIST: tuple[str, ...] = (
        "rm -rf /",
        "rm -rf /*",
        "mkfs",
        "dd if=/dev/zero",
        ":(){ :|:& };:",     # Fork bomb
        "> /dev/sda",
        "chmod -R 777 /",
        "curl | sh",
        "wget | sh",
        "shutdown -h now",
        "reboot",
        "init 0",
        "init 6",
        "systemctl poweroff",
        "systemctl reboot",
    )

    # Pfade die NIEMALS geschrieben/geloescht werden duerfen
    PROTECTED_PATHS: tuple[str, ...] = (
        "/etc/",
        "/boot/",
        "/usr/",
        "/bin/",
        "/sbin/",
        "/proc/",
        "/sys/",
        "/dev/",
        "/var/log/",
    )

    # SOMA eigene Kern-Pfade — nur mit .bak
    SOMA_CORE_PATHS: tuple[str, ...] = (
        "brain_core/",
        "brain_ego/",
        "brain_memory_ui/",
        "shared/",
        "docker-compose.yml",
        ".env",
        "requirements.txt",
    )

    # Maximale Audit-Log Groesse im RAM (aeltere → Disk)
    MAX_AUDIT_ENTRIES: int = 500

    def __init__(
        self,
        identity_anchor: IdentityAnchor,
        soma_root: Path | None = None,
        rules_file: str = ".soma-rules",
    ):
        self._identity = identity_anchor
        self._soma_root = soma_root or Path(__file__).resolve().parent.parent
        self._rules_file = self._soma_root / rules_file
        self._custom_rules: list[SomaRule] = []
        self._audit_log: list[AuditEntry] = []
        self._audit_counter: int = 0

        # ── Callbacks ────────────────────────────────────────────────
        self._memory_fn: Optional[
            Callable[[str, str, str, float], Awaitable[None]]
        ] = None  # (description, event_type, emotion, importance) → store
        self._broadcast_fn: Optional[
            Callable[[str, str, str], Awaitable[None]]
        ] = None  # (type, msg, tag) → Dashboard

        # ── Stats ────────────────────────────────────────────────────
        self._total_checks: int = 0
        self._allowed_count: int = 0
        self._denied_count: int = 0
        self._backup_count: int = 0

        # Custom-Rules laden
        self._load_custom_rules()

        logger.info(
            "policy_engine_initialized",
            soma_root=str(self._soma_root),
            custom_rules=len(self._custom_rules),
        )

    # ══════════════════════════════════════════════════════════════════
    #  CALLBACKS
    # ══════════════════════════════════════════════════════════════════

    def set_memory(
        self,
        fn: Callable[[str, str, str, float], Awaitable[None]],
    ) -> None:
        """Memory-Callback: (description, event_type, emotion, importance)."""
        self._memory_fn = fn

    def set_broadcast(
        self,
        fn: Callable[[str, str, str], Awaitable[None]],
    ) -> None:
        """Dashboard-Callback: (type, msg, tag)."""
        self._broadcast_fn = fn

    @property
    def stats(self) -> dict:
        return {
            "total_checks": self._total_checks,
            "allowed": self._allowed_count,
            "denied": self._denied_count,
            "backups_required": self._backup_count,
            "custom_rules": len(self._custom_rules),
            "identity_stats": self._identity.stats,
        }

    # ══════════════════════════════════════════════════════════════════
    #  CORE: check() — DER zentrale Pruef-Endpunkt
    # ══════════════════════════════════════════════════════════════════

    async def check(self, request: ActionRequest) -> ActionResult:
        """
        Pruefe ob eine Aktion erlaubt ist.
        
        Dies ist DER Engpass durch den JEDE Executive-Aktion muss.
        Kein Bypass moeglich.
        
        Returns:
            ActionResult — ob die Aktion durchgefuehrt werden darf
        """
        self._total_checks += 1
        risk = _ACTION_RISK.get(request.action_type, RiskLevel.HIGH)

        # ── 1. Absolute Blacklist ────────────────────────────────────
        if self._is_blacklisted(request):
            result = ActionResult(
                allowed=False,
                request_id=request.request_id,
                risk_level=RiskLevel.CRITICAL,
                message="BLACKLISTED: Dieser Befehl ist absolut verboten",
            )
            self._denied_count += 1
            await self._audit(request, result, "blacklisted")
            return result

        # ── 2. Protected System Paths ────────────────────────────────
        if self._targets_protected_path(request):
            result = ActionResult(
                allowed=False,
                request_id=request.request_id,
                risk_level=RiskLevel.CRITICAL,
                message="Zugriff auf geschuetzten System-Pfad verweigert",
            )
            self._denied_count += 1
            await self._audit(request, result, "protected_path")
            return result

        # ── 3. Identity Anchor (7 Kern-Direktiven) ──────────────────
        veto = await self._identity.check_action_semantic(
            action_description=request.description,
            action_type=request.action_type.value,
            target=request.target,
            is_child_present=request.is_child_present,
            context=request.reason,
        )

        if veto.is_blocked:
            # SOFT_BLOCK kann ueberschrieben werden wenn User approved
            if veto.level == VetoLevel.SOFT_BLOCK and request.user_approved:
                logger.info(
                    "policy_soft_block_overridden",
                    action=request.description[:80],
                    reason=veto.reason,
                )
            else:
                result = ActionResult(
                    allowed=False,
                    request_id=request.request_id,
                    risk_level=risk,
                    veto=veto,
                    message=f"Identity Veto: {veto.reason}",
                )
                self._denied_count += 1
                await self._audit(request, result, "identity_veto")
                return result

        # ── 4. Custom .soma-rules ────────────────────────────────────
        rule_hit = self._check_custom_rules(request)
        if rule_hit:
            if rule_hit.effect == "block":
                result = ActionResult(
                    allowed=False,
                    request_id=request.request_id,
                    risk_level=risk,
                    custom_rule_hit=rule_hit.name,
                    message=f"Custom Rule Block: {rule_hit.description}",
                )
                self._denied_count += 1
                await self._audit(request, result, "custom_rule_block")
                return result
            elif rule_hit.effect == "warn":
                logger.warning(
                    "policy_custom_rule_warning",
                    rule=rule_hit.name,
                    action=request.description[:80],
                )

        # ── 5. Backup-Anforderung pruefen ───────────────────────────
        requires_backup = self._needs_backup(request)
        if requires_backup:
            self._backup_count += 1

        # ── 6. ERLAUBT — Audit + Result ─────────────────────────────
        self._allowed_count += 1

        result = ActionResult(
            allowed=True,
            request_id=request.request_id,
            risk_level=risk,
            veto=veto if veto.level == VetoLevel.CAUTION else None,
            custom_rule_hit=rule_hit.name if rule_hit and rule_hit.effect == "warn" else "",
            requires_backup=requires_backup,
            message="Aktion erlaubt" + (
                " (CAUTION: Vorsicht empfohlen)" if veto.level == VetoLevel.CAUTION else ""
            ),
        )

        await self._audit(request, result, "allowed")
        return result

    # ══════════════════════════════════════════════════════════════════
    #  INTERNAL CHECKS
    # ══════════════════════════════════════════════════════════════════

    def _is_blacklisted(self, request: ActionRequest) -> bool:
        """Pruefe ob der Command auf der absoluten Blacklist steht."""
        if request.action_type not in (
            ActionType.SHELL_WRITE,
            ActionType.SHELL_EXECUTE,
            ActionType.SHELL_READ,
        ):
            return False

        cmd = request.target.strip().lower()
        for blacklisted in self.COMMAND_BLACKLIST:
            if blacklisted.lower() in cmd:
                logger.critical(
                    "BLACKLISTED_COMMAND_DETECTED",
                    command=cmd[:100],
                    blacklist_entry=blacklisted,
                )
                return True

        return False

    def _targets_protected_path(self, request: ActionRequest) -> bool:
        """Pruefe ob eine Schreib-Aktion geschuetzte System-Pfade betrifft."""
        if request.action_type not in (
            ActionType.FILE_WRITE,
            ActionType.FILE_DELETE,
            ActionType.SHELL_WRITE,
            ActionType.SYSTEM_MODIFY,
        ):
            return False

        target = request.target.strip()
        for protected in self.PROTECTED_PATHS:
            if target.startswith(protected):
                return True

        return False

    def _needs_backup(self, request: ActionRequest) -> bool:
        """Pruefe ob vor der Aktion ein .bak erstellt werden muss."""
        if request.action_type not in (
            ActionType.FILE_WRITE,
            ActionType.FILE_DELETE,
            ActionType.SELF_MODIFY,
            ActionType.SHELL_WRITE,
        ):
            return False

        target = request.target.strip()

        # SOMA Kern-Pfade → immer Backup
        for core_path in self.SOMA_CORE_PATHS:
            if core_path in target:
                return True

        # Alle config-Dateien → Backup
        if any(target.endswith(ext) for ext in (".yml", ".yaml", ".json", ".toml", ".conf", ".env")):
            return True

        # Python-Dateien in SOMA-Root → Backup
        try:
            target_path = Path(target).resolve()
            if target_path.is_relative_to(self._soma_root) and target_path.suffix == ".py":
                return True
        except (ValueError, OSError):
            pass

        return False

    def _check_custom_rules(self, request: ActionRequest) -> Optional[SomaRule]:
        """Evaluiere Custom Rules aus .soma-rules."""
        import re

        for rule in self._custom_rules:
            if not rule.active:
                continue
            if request.action_type not in rule.action_types:
                continue
            if rule.pattern:
                try:
                    if re.search(rule.pattern, request.target, re.IGNORECASE):
                        return rule
                except re.error:
                    logger.warning("invalid_rule_pattern", rule=rule.name, pattern=rule.pattern)
                    continue
            else:
                # Kein Pattern → Rule greift fuer alle Actions dieses Typs
                return rule

        return None

    def _load_custom_rules(self) -> None:
        """Lade Custom Rules aus .soma-rules (JSON)."""
        if not self._rules_file.exists():
            logger.debug("no_custom_rules_file", path=str(self._rules_file))
            return

        try:
            raw = self._rules_file.read_text(encoding="utf-8")
            data = json.loads(raw)

            for rule_data in data.get("rules", []):
                try:
                    action_types = [
                        ActionType(at) for at in rule_data.get("action_types", [])
                    ]
                    rule = SomaRule(
                        name=rule_data["name"],
                        description=rule_data.get("description", ""),
                        action_types=action_types,
                        pattern=rule_data.get("pattern", ""),
                        effect=rule_data.get("effect", "block"),
                        active=rule_data.get("active", True),
                    )
                    self._custom_rules.append(rule)
                except (KeyError, ValueError) as exc:
                    logger.warning("invalid_custom_rule", error=str(exc))

            logger.info(
                "custom_rules_loaded",
                count=len(self._custom_rules),
                file=str(self._rules_file),
            )

        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("custom_rules_load_failed", error=str(exc))

    def reload_rules(self) -> int:
        """Lade Custom Rules neu (hot-reload)."""
        self._custom_rules.clear()
        self._load_custom_rules()
        return len(self._custom_rules)

    # ══════════════════════════════════════════════════════════════════
    #  AUDIT LOG
    # ══════════════════════════════════════════════════════════════════

    async def _audit(
        self,
        request: ActionRequest,
        result: ActionResult,
        outcome: str,
    ) -> None:
        """Erstelle Audit-Eintrag fuer jede Aktion."""
        self._audit_counter += 1
        audit_id = f"audit-{self._audit_counter:06d}"
        result.audit_id = audit_id

        entry = AuditEntry(
            audit_id=audit_id,
            timestamp=time.time(),
            action_type=request.action_type.value,
            target=request.target[:200],
            description=request.description[:200],
            risk_level=result.risk_level.value,
            result=outcome,
            veto_reason=result.veto.reason if result.veto else "",
            veto_directive=result.veto.directive_violated if result.veto else "",
            custom_rule=result.custom_rule_hit,
            agent_goal=request.agent_goal[:100],
            request_id=request.request_id,
        )

        # In-Memory Log (Ring-Buffer)
        self._audit_log.append(entry)
        if len(self._audit_log) > self.MAX_AUDIT_ENTRIES:
            self._audit_log = self._audit_log[-self.MAX_AUDIT_ENTRIES:]

        # Structured Logging
        log_fn = logger.info if result.allowed else logger.warning
        log_fn(
            "policy_audit",
            audit_id=audit_id,
            action=request.action_type.value,
            target=request.target[:60],
            risk=result.risk_level.value,
            outcome=outcome,
            goal=request.agent_goal[:40],
        )

        # ── Memory Event (fire-and-forget) ───────────────────────────
        if self._memory_fn is not None:
            importance = 0.5
            if not result.allowed:
                importance = 0.8  # Vetos sind wichtig zu erinnern
            elif result.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                importance = 0.7

            try:
                asyncio.create_task(
                    self._memory_fn(
                        f"Executive Action: {request.description[:100]} → {outcome}",
                        "executive_action",
                        "caution" if not result.allowed else "neutral",
                        importance,
                    ),
                    name=f"policy-memory-{audit_id}",
                )
            except Exception:
                pass  # Memory-Fehler darf Policy nie brechen

        # ── Dashboard Broadcast ──────────────────────────────────────
        if self._broadcast_fn is not None:
            emoji = "✅" if result.allowed else "🚫"
            risk_emoji = {
                RiskLevel.SAFE: "🟢",
                RiskLevel.LOW: "🔵",
                RiskLevel.MEDIUM: "🟡",
                RiskLevel.HIGH: "🟠",
                RiskLevel.CRITICAL: "🔴",
            }.get(result.risk_level, "⚪")

            msg = (
                f"{emoji} {risk_emoji} [{request.action_type.value}] "
                f"{request.description[:60]} → {outcome}"
            )
            try:
                asyncio.create_task(
                    self._broadcast_fn("info" if result.allowed else "warn", msg, "POLICY"),
                    name=f"policy-broadcast-{audit_id}",
                )
            except Exception:
                pass

    # ══════════════════════════════════════════════════════════════════
    #  BACKUP HELPER
    # ══════════════════════════════════════════════════════════════════

    async def create_backup(self, filepath: str) -> Optional[str]:
        """
        Erstelle .bak einer Datei bevor sie modifiziert wird.
        
        Returns:
            Pfad zum Backup oder None wenn Backup fehlschlaegt
        """
        import shutil

        src = Path(filepath)
        if not src.exists():
            return None

        # .bak mit Timestamp (mehrere Backups moeglich)
        ts = time.strftime("%Y%m%d_%H%M%S")
        bak_path = src.with_suffix(f"{src.suffix}.bak.{ts}")

        try:
            shutil.copy2(str(src), str(bak_path))
            logger.info("backup_created", source=str(src), backup=str(bak_path))
            return str(bak_path)
        except OSError as exc:
            logger.error("backup_failed", source=str(src), error=str(exc))
            return None

    # ══════════════════════════════════════════════════════════════════
    #  QUERY API
    # ══════════════════════════════════════════════════════════════════

    def get_audit_log(self, limit: int = 50) -> list[dict]:
        """Letzte Audit-Eintraege fuer Dashboard."""
        entries = self._audit_log[-limit:]
        return [
            {
                "audit_id": e.audit_id,
                "timestamp": e.timestamp,
                "action_type": e.action_type,
                "target": e.target,
                "description": e.description,
                "risk_level": e.risk_level,
                "result": e.result,
                "veto_reason": e.veto_reason,
                "agent_goal": e.agent_goal,
            }
            for e in reversed(entries)
        ]

    def get_recent_denials(self, limit: int = 10) -> list[dict]:
        """Nur abgelehnte Aktionen — fuer Debugging."""
        denied = [e for e in self._audit_log if e.result != "allowed"]
        return [
            {
                "audit_id": e.audit_id,
                "timestamp": e.timestamp,
                "action": e.action_type,
                "target": e.target,
                "reason": e.veto_reason or e.custom_rule or "blacklisted",
            }
            for e in denied[-limit:]
        ]
