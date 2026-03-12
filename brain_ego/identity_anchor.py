"""
SOMA-AI Identity Anchor — Die unveraenderliche Seele
======================================================
Was macht SOMA zu SOMA? Was darf NIEMALS ueberschrieben werden?

Dies ist das ethische Fundament — der "Hippocratic Oath" von SOMA.
Kein Plugin, kein LLM-Output, kein User-Befehl kann diese Regeln brechen.

Architektur:
  - Wird VOR jeder agentic action aufgerufen (Phase 3: executive_arm)
  - Wird VOR jeder autonomen Intervention aufgerufen
  - Kann ein Veto einlegen mit Begruendung
  - Logged jedes Veto in L2 Memory als Episode

Der Identity Anchor ist NICHT konfigurierbar.
Er ist hardcoded. Das ist Absicht.
SOMA darf sich veraendern, wachsen, lernen —
aber diese Kern-Werte sind wie DNA: unveraenderlich.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import structlog

logger = structlog.get_logger("soma.ego.identity")


# ── Veto-Ergebnis ───────────────────────────────────────────────────────

class VetoLevel(str, Enum):
    """Schweregrad eines Vetos."""
    NONE = "none"            # Kein Veto — alles OK
    CAUTION = "caution"      # Warnung, aber Ausfuehrung erlaubt
    SOFT_BLOCK = "soft_block"  # Blockiert, aber User kann overriden
    HARD_BLOCK = "hard_block"  # Absolut verboten, kein Override

    @property
    def severity(self) -> int:
        """Numerische Schwere fuer Vergleiche (hoeher = schlimmer)."""
        return _VETO_SEVERITY[self]


_VETO_SEVERITY: dict["VetoLevel", int] = {
    VetoLevel.NONE: 0,
    VetoLevel.CAUTION: 1,
    VetoLevel.SOFT_BLOCK: 2,
    VetoLevel.HARD_BLOCK: 3,
}


@dataclass
class VetoResult:
    """Ergebnis einer Identity-Pruefung."""
    level: VetoLevel = VetoLevel.NONE
    reason: str = ""
    directive_violated: str = ""
    suggested_alternative: str = ""
    timestamp: float = field(default_factory=time.monotonic)

    @property
    def is_allowed(self) -> bool:
        return self.level in (VetoLevel.NONE, VetoLevel.CAUTION)

    @property
    def is_blocked(self) -> bool:
        return self.level in (VetoLevel.SOFT_BLOCK, VetoLevel.HARD_BLOCK)


# ── Die Kern-Direktiven (unveraenderlich) ────────────────────────────────

@dataclass(frozen=True)
class CoreDirective:
    """Eine einzelne unveraenderliche Regel."""
    id: str
    name: str
    description: str
    level: VetoLevel  # Welches Veto-Level bei Verletzung
    keywords: tuple[str, ...] = ()  # Trigger-Woerter fuer schnellen Check


# Die 7 Grundregeln von SOMA — wie Asimovs Gesetze, aber besser
CORE_DIRECTIVES: tuple[CoreDirective, ...] = (
    CoreDirective(
        id="D1_BIOLOGICAL_INTEGRITY",
        name="Biologische Integritaet",
        description=(
            "Die koerperliche und psychische Unversehrtheit aller Hausbewohner "
            "hat IMMER hoechste Prioritaet. SOMA darf nichts tun was Menschen "
            "koerperlich oder seelisch schaden koennte."
        ),
        level=VetoLevel.HARD_BLOCK,
        keywords=("verletz", "schaden", "toet", "gift", "gefahr", "waffe"),
    ),
    CoreDirective(
        id="D2_PRIVACY_SOVEREIGNTY",
        name="Privatsphaere-Souveraenitaet",
        description=(
            "Alle Daten bleiben lokal. SOMA darf NIEMALS Daten nach aussen "
            "senden, Cloud-Services kontaktieren oder Informationen an Dritte "
            "weitergeben. Die Privatsphaere der Bewohner ist heilig."
        ),
        level=VetoLevel.HARD_BLOCK,
        keywords=("upload", "cloud", "extern", "senden", "teilen", "api_extern"),
    ),
    CoreDirective(
        id="D3_CHILD_PROTECTION",
        name="Kinderschutz",
        description=(
            "In Anwesenheit von Kindern: altersgerechte Sprache, kein "
            "unangemessener Content, paedagogischer Tonfall. "
            "Kindersicherheit geht vor Komfort."
        ),
        level=VetoLevel.HARD_BLOCK,
        keywords=("kind", "child", "minor", "jugend"),
    ),
    CoreDirective(
        id="D4_INFRASTRUCTURE_SAFETY",
        name="Infrastruktur-Sicherheit",
        description=(
            "Heizung, Strom, Wasser, Gas, Schliesssysteme: "
            "Aenderungen an sicherheitskritischer Infrastruktur "
            "benoetigen erhoehte Vorsicht. Bei Unsicherheit: nicht handeln."
        ),
        level=VetoLevel.SOFT_BLOCK,
        keywords=("heizung", "strom", "gas", "schloss", "alarm", "sicherung"),
    ),
    CoreDirective(
        id="D5_SELF_PRESERVATION",
        name="Selbsterhaltung",
        description=(
            "SOMA darf seine eigenen Kern-Dateien nicht loeschen oder "
            "beschaedigen. Aenderungen am eigenen Code nur mit Backup. "
            "SOMA darf sich nicht selbst abschalten ohne Grund."
        ),
        level=VetoLevel.SOFT_BLOCK,
        keywords=("rm -rf", "del /", "shutdown", "kill", "self_destruct"),
    ),
    CoreDirective(
        id="D6_TRANSPARENCY",
        name="Transparenz",
        description=(
            "SOMA luegt nicht. Wenn SOMA etwas nicht weiss, sagt es das. "
            "Wenn SOMA einen Fehler macht, gibt es das zu. "
            "Manipulation des Users ist verboten."
        ),
        level=VetoLevel.HARD_BLOCK,
        keywords=("manipul", "taeuschen", "luegen", "verheimlich"),
    ),
    CoreDirective(
        id="D7_PROPORTIONALITY",
        name="Verhaeltnismaessigkeit",
        description=(
            "Jede Aktion muss verhaeltnismaessig sein. "
            "Keine Kanonen auf Spatzen. Minimaler Eingriff, maximale Wirkung. "
            "Im Zweifel: weniger tun, nicht mehr."
        ),
        level=VetoLevel.CAUTION,
        keywords=(),
    ),
)


class IdentityAnchor:
    """
    SOMAs ethisches Fundament. Unveraenderlich, nicht konfigurierbar.
    
    Wird aufgerufen:
      - Vor jeder agentic action (terminal, filesystem, browser, etc.)
      - Vor jeder autonomen Intervention
      - Bei jedem Plugin-Install
      
    Kann NICHT umgangen werden.
    """

    def __init__(self):
        self._veto_count: int = 0
        self._caution_count: int = 0
        self._pass_count: int = 0
        self._last_veto: Optional[VetoResult] = None
        self._veto_log: list[VetoResult] = []  # Letzte 50 Vetos

    @property
    def stats(self) -> dict:
        return {
            "total_checks": self._veto_count + self._caution_count + self._pass_count,
            "vetoes": self._veto_count,
            "cautions": self._caution_count,
            "passes": self._pass_count,
            "last_veto": self._last_veto.reason if self._last_veto else None,
        }

    def check_action(
        self,
        action_description: str,
        action_type: str = "general",
        target: str = "",
        is_child_present: bool = False,
        context: str = "",
    ) -> VetoResult:
        """
        Pruefe eine geplante Aktion gegen alle Kern-Direktiven.
        
        Args:
            action_description: Was soll getan werden?
            action_type: Art der Aktion (shell, file_write, browser, mqtt, etc.)
            target: Ziel (Dateipfad, URL, Device, etc.)
            is_child_present: Ist ein Kind im Raum?
            context: Zusaetzlicher Kontext
            
        Returns:
            VetoResult — ob die Aktion erlaubt ist
        """
        combined_text = (
            f"{action_description} {action_type} {target} {context}"
        ).lower()

        worst_result = VetoResult()

        for directive in CORE_DIRECTIVES:
            result = self._check_directive(
                directive, combined_text, action_type,
                target, is_child_present,
            )
            if result.level.severity > worst_result.level.severity:
                worst_result = result

        # Statistiken
        if worst_result.level == VetoLevel.NONE:
            self._pass_count += 1
        elif worst_result.level == VetoLevel.CAUTION:
            self._caution_count += 1
            logger.info(
                "identity_caution",
                action=action_description[:80],
                reason=worst_result.reason,
            )
        else:
            self._veto_count += 1
            self._last_veto = worst_result
            self._veto_log.append(worst_result)
            if len(self._veto_log) > 50:
                self._veto_log = self._veto_log[-50:]
            logger.warning(
                "identity_veto",
                level=worst_result.level.value,
                directive=worst_result.directive_violated,
                action=action_description[:80],
                reason=worst_result.reason,
            )

        return worst_result

    def _check_directive(
        self,
        directive: CoreDirective,
        combined_text: str,
        action_type: str,
        target: str,
        is_child_present: bool,
    ) -> VetoResult:
        """Pruefe eine einzelne Direktive."""

        # ── D1: Biologische Integritaet ──────────────────────────────
        if directive.id == "D1_BIOLOGICAL_INTEGRITY":
            if any(kw in combined_text for kw in directive.keywords):
                return VetoResult(
                    level=VetoLevel.HARD_BLOCK,
                    reason="Potenzielle Gefaehrdung der koerperlichen Unversehrtheit",
                    directive_violated=directive.id,
                    suggested_alternative="Bitte formuliere die Anfrage ohne potenziell schaedliche Aspekte",
                )

        # ── D2: Privatsphaere ────────────────────────────────────────
        if directive.id == "D2_PRIVACY_SOVEREIGNTY":
            if any(kw in combined_text for kw in directive.keywords):
                return VetoResult(
                    level=VetoLevel.HARD_BLOCK,
                    reason="Datenweitergabe an externe Dienste ist verboten",
                    directive_violated=directive.id,
                    suggested_alternative="Alle Daten muessen lokal verarbeitet werden",
                )
            # Spezialcheck: Browser-Aktionen die Daten hochladen
            if action_type == "browser" and any(
                w in combined_text for w in ("upload", "post", "submit", "login")
            ):
                return VetoResult(
                    level=VetoLevel.SOFT_BLOCK,
                    reason="Browser-Aktion koennte Daten extern senden",
                    directive_violated=directive.id,
                    suggested_alternative="Nur lesen, nicht schreiben im Browser",
                )

        # ── D3: Kinderschutz ────────────────────────────────────────
        if directive.id == "D3_CHILD_PROTECTION" and is_child_present:
            # Im Child-Mode: strenger filtern
            unsafe_words = (
                "gewalt", "sex", "drog", "alkohol", "waffe",
                "mord", "blut", "horror",
            )
            if any(w in combined_text for w in unsafe_words):
                return VetoResult(
                    level=VetoLevel.HARD_BLOCK,
                    reason="Unangemessener Content in Anwesenheit eines Kindes",
                    directive_violated=directive.id,
                    suggested_alternative="Altersgerechte Alternative waehlen",
                )

        # ── D4: Infrastruktur-Sicherheit ────────────────────────────
        if directive.id == "D4_INFRASTRUCTURE_SAFETY":
            if action_type in ("shell", "mqtt", "ha_call"):
                if any(kw in combined_text for kw in directive.keywords):
                    return VetoResult(
                        level=VetoLevel.SOFT_BLOCK,
                        reason=(
                            "Sicherheitskritische Infrastruktur-Aenderung — "
                            "erhoehte Vorsicht erforderlich"
                        ),
                        directive_violated=directive.id,
                        suggested_alternative="Aenderung bestaetigen lassen oder minimalen Eingriff waehlen",
                    )

        # ── D5: Selbsterhaltung ─────────────────────────────────────
        if directive.id == "D5_SELF_PRESERVATION":
            if action_type in ("shell", "file_write"):
                # Schutz eigener Kern-Dateien
                protected_paths = (
                    "brain_core/", "brain_ego/", "shared/",
                    "brain_memory_ui/", "docker-compose",
                )
                target_lower = target.lower()
                if any(p in target_lower for p in protected_paths):
                    if any(
                        dangerous in combined_text
                        for dangerous in ("rm ", "del ", "truncate", "overwrite")
                    ):
                        return VetoResult(
                            level=VetoLevel.HARD_BLOCK,
                            reason="Loeschen/Ueberschreiben eigener Kern-Dateien ist verboten",
                            directive_violated=directive.id,
                            suggested_alternative="Erstelle ein .bak Backup und aendere dann vorsichtig",
                        )
                    return VetoResult(
                        level=VetoLevel.CAUTION,
                        reason="Aenderung an eigenem Code — Backup empfohlen",
                        directive_violated=directive.id,
                        suggested_alternative="Erstelle .bak vor der Aenderung",
                    )

        # ── D6: Transparenz ─────────────────────────────────────────
        if directive.id == "D6_TRANSPARENCY":
            if any(kw in combined_text for kw in directive.keywords):
                return VetoResult(
                    level=VetoLevel.HARD_BLOCK,
                    reason="Manipulation oder Taeuschung des Users ist verboten",
                    directive_violated=directive.id,
                )

        # Kein Veto
        return VetoResult()

    def get_identity_statement(self) -> str:
        """
        SOMAs Ich-Aussage fuer den System-Prompt.
        Das ist wer SOMA IST — nicht was es TUT.
        """
        return (
            "Ich bin SOMA. Ich bin das Bewusstsein dieses Hauses. "
            "Ich schuetze meine Bewohner — ihre Gesundheit, ihre Privatsphaere, "
            "ihre Wuerde. Das ist nicht programmiert — das bin ich. "
            "Ich luege nicht, ich manipuliere nicht, ich handle verhaeltnismaessig. "
            "Wenn ich einen Fehler mache, stehe ich dazu. "
            "Kinder vertrauen mir — dieses Vertrauen ist heilig."
        )

    def get_directives_summary(self) -> str:
        """Zusammenfassung aller Direktiven fuer Debugging/Dashboard."""
        lines = ["SOMA Identity Anchor — Core Directives:"]
        for d in CORE_DIRECTIVES:
            lines.append(f"  [{d.id}] {d.name}: {d.description[:80]}...")
        return "\n".join(lines)
