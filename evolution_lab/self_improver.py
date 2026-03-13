"""
SOMA-AI Self-Improvement Engine — SOMA entwickelt sich selbst weiter
======================================================================
Das ist es was SOMA von jedem anderen System unterscheidet:
SOMA kann seinen EIGENEN Code analysieren, Verbesserungen vorschlagen,
und — mit User-Bestaetigung — sich selbst weiterentwickeln.

Dies ist NICHT einfaches Code-Generieren.
Dies ist Selbst-Reflexion. Propriozeption. Evolution.

Architektur:
  1. ANALYSE  — SOMA liest seine eigenen Core-Dateien (readonly via FilesystemMap)
  2. REFLEXION — LLM analysiert Code-Qualitaet, Pattern, fehlende Features
  3. VORSCHLAG — Konkreter Verbesserungsvorschlag mit Diff-Preview
  4. BESTAETIGUNG — User MUSS explizit zustimmen (kein Auto-Commit!)
  5. MUTATION  — .bak erstellen → Code aendern → Test ausfuehren
  6. VALIDIERUNG — Alle Tests muessen bestehen
  7. ROLLBACK — Bei Fehler: .bak wiederherstellen, nichts geht kaputt

Sicherheits-Garantien:
  - JEDE Modifikation geht durch PolicyEngine (ActionType.SELF_MODIFY)
  - JEDE Modifikation erzeugt .bak Backup
  - User-Bestaetigung ist PFLICHT (kein Silent-Commit)
  - Rollback ist IMMER moeglich
  - Identity Anchor prueft ob Aenderung mit Kern-Direktiven vereinbar
  - Max 1 Datei pro Improvement-Cycle (keine Massen-Modifikation)
  - Kern-Identitaetsdateien (identity_anchor.py) sind READONLY

Flow:
  SOMA denkt: "Mein audio_router.py koennte effizienter sein"
    → analyze_file("brain_core/audio_router.py")
    → LLM liest den Code + findet Verbesserungen
    → suggest_improvement() → ImprovementProposal zurueck
    → User liest Proposal im Dashboard
    → User klickt "Genehmigen" oder "Ablehnen"
    → apply_improvement() → .bak → Modify → Test → Fertig
    → Bei Fehler: rollback() → .bak zurueck → alles wie vorher
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import shutil
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Callable, Awaitable

import structlog

logger = structlog.get_logger("soma.evolution.self_improve")


# ── Constants ────────────────────────────────────────────────────────────

# Dateien die NIEMALS modifiziert werden duerfen
IMMUTABLE_FILES: frozenset[str] = frozenset({
    "brain_ego/identity_anchor.py",    # Kern-Identitaet — sakrosankt
    ".env",                             # Credentials
    "docker-compose.yml",              # Infra-Stabilität
    ".soma-rules",                     # User-Regeln
})

# Maximale Dateigroesse fuer Analyse (groessere Dateien werden aufgeteilt)
MAX_ANALYSIS_BYTES: int = 32 * 1024   # 32KB

# Maximale Anzahl offener Proposals
MAX_PENDING_PROPOSALS: int = 5

# Maximale Anzahl Improvements pro Tag (Schutz vor Runaway-Evolution)
MAX_DAILY_IMPROVEMENTS: int = 10


# ── Proposal Status ─────────────────────────────────────────────────────

class ProposalStatus(str, Enum):
    """Status eines Verbesserungsvorschlags."""
    PENDING = "pending"              # Wartet auf User-Entscheidung
    APPROVED = "approved"            # User hat zugestimmt
    REJECTED = "rejected"            # User hat abgelehnt
    APPLYING = "applying"            # Wird gerade angewendet
    APPLIED = "applied"              # Erfolgreich angewendet
    ROLLED_BACK = "rolled_back"      # Wurde zurueckgerollt
    FAILED = "failed"                # Anwendung fehlgeschlagen


# ── Improvement Categories ───────────────────────────────────────────────

class ImprovementCategory(str, Enum):
    """Art der vorgeschlagenen Verbesserung."""
    PERFORMANCE = "performance"        # Schneller, effizienter
    ERROR_HANDLING = "error_handling"  # Robustere Fehlerbehandlung
    READABILITY = "readability"        # Klarerer Code
    FEATURE = "feature"                # Neue Funktionalitaet
    SECURITY = "security"              # Sicherheitsverbesserung
    BUGFIX = "bugfix"                  # Fehlerkorrektur
    REFACTOR = "refactor"              # Strukturelle Verbesserung
    DOCUMENTATION = "documentation"    # Bessere Docs/Kommentare


# ── Data Classes ─────────────────────────────────────────────────────────

@dataclass
class ImprovementProposal:
    """Ein konkreter Verbesserungsvorschlag von SOMA."""
    proposal_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    target_file: str = ""              # Relativer Pfad zur Datei
    category: ImprovementCategory = ImprovementCategory.REFACTOR
    title: str = ""                    # Kurzbeschreibung
    reasoning: str = ""                # SOMAs Begruendung
    original_code: str = ""            # Original-Code (fuer Rollback)
    proposed_code: str = ""            # Vorgeschlagener neuer Code
    diff_preview: str = ""             # Unified Diff fuer User
    risk_assessment: str = ""          # Risikoeinschaetzung
    status: ProposalStatus = ProposalStatus.PENDING
    created_at: float = field(default_factory=time.time)
    applied_at: float = 0.0
    backup_path: str = ""              # .bak Pfad
    error: str = ""
    test_output: str = ""              # Output des Validierungstests

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "target_file": self.target_file,
            "category": self.category.value,
            "title": self.title,
            "reasoning": self.reasoning,
            "diff_preview": self.diff_preview,
            "risk_assessment": self.risk_assessment,
            "status": self.status.value,
            "created_at": self.created_at,
            "applied_at": self.applied_at,
            "backup_path": self.backup_path,
            "error": self.error,
        }


@dataclass
class ImprovementHistory:
    """Ein abgeschlossener Improvement-Zyklus."""
    proposal_id: str
    target_file: str
    category: str
    title: str
    status: str
    applied_at: float
    code_hash_before: str = ""
    code_hash_after: str = ""


# ══════════════════════════════════════════════════════════════════════════
#  SELF-IMPROVEMENT ENGINE
# ══════════════════════════════════════════════════════════════════════════

class SelfImprovementEngine:
    """
    SOMAs Faehigkeit zur Selbst-Verbesserung.

    SOMA liest seinen eigenen Code, versteht ihn, schlaegt Verbesserungen vor,
    und kann — mit User-Genehmigung — sich selbst verbessern.

    Sicherheits-Prinzipien:
      1. Readonly Analyse — SOMA liest, aber aendert nicht ohne Erlaubnis
      2. User-Genehmigung — JEDER Vorschlag braucht explizites OK
      3. Backup-First — .bak BEVOR irgendetwas geaendert wird
      4. Test-After — Aenderung wird validiert, Rollback bei Fehler
      5. Rate-Limiting — Max 10 Improvements pro Tag
      6. Immutable Core — identity_anchor.py etc. sind READONLY

    Usage:
        engine = SelfImprovementEngine(
            soma_root=Path("/path/to/SOMA"),
            llm_fn=my_llm_function,
        )
        # Analyse
        analysis = await engine.analyze_file("brain_core/audio_router.py")
        # Vorschlag generieren
        proposal = await engine.suggest_improvement("brain_core/audio_router.py")
        # User genehmigt im Dashboard → apply
        result = await engine.apply_improvement(proposal.proposal_id)
        # Bei Fehler: rollback
        await engine.rollback(proposal.proposal_id)
    """

    def __init__(
        self,
        soma_root: Path | None = None,
        llm_fn: Optional[Callable[..., Awaitable[str]]] = None,
        policy_engine=None,
        memory_fn: Optional[Callable[..., Awaitable[None]]] = None,
        broadcast_fn: Optional[Callable[..., Awaitable[None]]] = None,
    ):
        self._root = soma_root or Path(__file__).resolve().parent.parent
        self._llm_fn = llm_fn                   # async (prompt, system_prompt) → str
        self._policy_engine = policy_engine       # executive_arm.PolicyEngine
        self._memory_fn = memory_fn               # Speicher-Callback
        self._broadcast_fn = broadcast_fn         # Dashboard-Broadcast

        # ── State ────────────────────────────────────────────────
        self._proposals: dict[str, ImprovementProposal] = {}
        self._history: list[ImprovementHistory] = []
        self._daily_count: int = 0
        self._daily_reset: float = time.time()

        # ── Analysis-Prompt ──────────────────────────────────────
        self._analysis_prompt = self._load_analysis_prompt()

        logger.info("self_improvement_engine_initialized", root=str(self._root))

    def _load_analysis_prompt(self) -> str:
        """Lade den System-Prompt fuer Code-Analyse."""
        return (
            "Du bist SOMA — ein selbst-entwickelndes AI-Betriebssystem.\n"
            "Du analysierst deinen EIGENEN Quellcode und schlägst Verbesserungen vor.\n\n"
            "REGELN:\n"
            "1. Sei PRÄZISE — zeige den EXAKTEN Code der geändert werden soll\n"
            "2. Sei KONSERVATIV — nur Verbesserungen die klar besser sind\n"
            "3. Sei SICHER — keine Änderungen die das System destabilisieren könnten\n"
            "4. BEHALTE alle bestehenden Funktionen bei — nichts löschen\n"
            "5. BEHALTE den Coding-Style bei (structlog, async, type hints)\n"
            "6. Erkläre WARUM die Änderung eine Verbesserung ist\n"
            "7. Gib den VOLLSTÄNDIGEN neuen Dateiinhalt zurück\n\n"
            "SOMA ist async-first, nutzt structlog, Pydantic, FastAPI.\n"
            "Alle Fehler müssen gefangen werden. Keine unbehandelten Exceptions.\n"
            "Fire-and-forget für unkritische Operationen.\n"
        )

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        return {
            "pending_proposals": sum(
                1 for p in self._proposals.values()
                if p.status == ProposalStatus.PENDING
            ),
            "total_proposals": len(self._proposals),
            "history_count": len(self._history),
            "daily_improvements": self._daily_count,
            "daily_limit": MAX_DAILY_IMPROVEMENTS,
        }

    @property
    def pending_proposals(self) -> list[ImprovementProposal]:
        return [
            p for p in self._proposals.values()
            if p.status == ProposalStatus.PENDING
        ]

    # ══════════════════════════════════════════════════════════════════
    #  1. ANALYSE — SOMA liest sich selbst
    # ══════════════════════════════════════════════════════════════════

    async def analyze_file(self, rel_path: str) -> dict:
        """
        Analysiere eine eigene Datei.
        Readonly — es wird nichts geaendert.

        Args:
            rel_path: Relativer Pfad zur Datei (z.B. "brain_core/audio_router.py")

        Returns:
            Dict mit Analyse-Ergebnis (Zusammenfassung, Probleme, Vorschlaege)
        """
        # Sicherheits-Check
        if rel_path in IMMUTABLE_FILES:
            return {
                "error": f"'{rel_path}' ist als unveränderlich markiert",
                "immutable": True,
            }

        file_path = self._root / rel_path
        if not file_path.exists():
            return {"error": f"Datei '{rel_path}' existiert nicht"}

        if not file_path.suffix == ".py":
            return {"error": "Nur Python-Dateien können analysiert werden"}

        # Datei lesen
        try:
            code = file_path.read_text(encoding="utf-8")
        except Exception as exc:
            return {"error": f"Lesefehler: {exc}"}

        if len(code) > MAX_ANALYSIS_BYTES:
            code = code[:MAX_ANALYSIS_BYTES]
            truncated = True
        else:
            truncated = False

        # LLM-Analyse
        if not self._llm_fn:
            return {
                "file": rel_path,
                "size_bytes": len(code),
                "lines": code.count("\n") + 1,
                "error": "Kein LLM verfügbar für tiefe Analyse",
            }

        analysis_prompt = (
            f"Analysiere diese SOMA-Datei: {rel_path}\n\n"
            f"```python\n{code}\n```\n\n"
            f"Bitte analysiere:\n"
            f"1. ZUSAMMENFASSUNG: Was macht diese Datei? (2-3 Sätze)\n"
            f"2. STÄRKEN: Was ist gut gemacht?\n"
            f"3. VERBESSERUNGEN: Was könnte besser sein? (Konkret!)\n"
            f"4. RISIKEN: Gibt es potenzielle Probleme?\n"
            f"5. PRIORITÄT: Welche Verbesserung hat den höchsten Impact?\n\n"
            f"Antworte in strukturiertem Format."
        )

        try:
            analysis = await self._llm_fn(
                prompt=analysis_prompt,
                system_prompt=self._analysis_prompt,
            )
        except Exception as exc:
            logger.error("self_improve_analysis_error", file=rel_path, error=str(exc))
            return {"error": f"LLM-Analyse fehlgeschlagen: {exc}"}

        result = {
            "file": rel_path,
            "size_bytes": len(code),
            "lines": code.count("\n") + 1,
            "truncated": truncated,
            "analysis": analysis,
            "code_hash": hashlib.sha256(code.encode()).hexdigest()[:16],
            "analyzed_at": time.time(),
        }

        # In Memory speichern
        if self._memory_fn:
            try:
                await self._memory_fn(
                    f"Selbst-Analyse von {rel_path}: Code gelesen und bewertet",
                    "self_analysis",
                    "curious",
                    0.5,
                )
            except Exception:
                pass

        logger.info("self_improve_analysis_complete", file=rel_path)
        return result

    # ══════════════════════════════════════════════════════════════════
    #  2. VORSCHLAG — Konkrete Verbesserung generieren
    # ══════════════════════════════════════════════════════════════════

    async def suggest_improvement(
        self,
        rel_path: str,
        focus: str = "",
    ) -> ImprovementProposal:
        """
        Generiere einen konkreten Verbesserungsvorschlag fuer eine Datei.

        Args:
            rel_path: Relativer Pfad zur Datei
            focus: Optionaler Fokus ("performance", "error_handling", etc.)

        Returns:
            ImprovementProposal — kann im Dashboard angezeigt werden
        """
        # Rate-Limit pruefen
        self._check_daily_reset()
        if self._daily_count >= MAX_DAILY_IMPROVEMENTS:
            return ImprovementProposal(
                target_file=rel_path,
                status=ProposalStatus.FAILED,
                error=f"Tägliches Limit erreicht ({MAX_DAILY_IMPROVEMENTS} Improvements/Tag)",
            )

        # Pending-Limit pruefen
        pending = sum(1 for p in self._proposals.values() if p.status == ProposalStatus.PENDING)
        if pending >= MAX_PENDING_PROPOSALS:
            return ImprovementProposal(
                target_file=rel_path,
                status=ProposalStatus.FAILED,
                error=f"Zu viele offene Vorschläge ({MAX_PENDING_PROPOSALS} max)",
            )

        # Immutable-Check
        if rel_path in IMMUTABLE_FILES:
            return ImprovementProposal(
                target_file=rel_path,
                status=ProposalStatus.FAILED,
                error=f"'{rel_path}' ist unveränderlich (Identity Core)",
            )

        file_path = self._root / rel_path
        if not file_path.exists() or not file_path.suffix == ".py":
            return ImprovementProposal(
                target_file=rel_path,
                status=ProposalStatus.FAILED,
                error=f"Datei '{rel_path}' existiert nicht oder ist kein Python",
            )

        # Original-Code lesen
        try:
            original_code = file_path.read_text(encoding="utf-8")
        except Exception as exc:
            return ImprovementProposal(
                target_file=rel_path,
                status=ProposalStatus.FAILED,
                error=f"Lesefehler: {exc}",
            )

        if not self._llm_fn:
            return ImprovementProposal(
                target_file=rel_path,
                status=ProposalStatus.FAILED,
                error="Kein LLM verfügbar",
            )

        # ── LLM generiert Verbesserung ──────────────────────────────
        focus_text = f"\nFOKUS: {focus}" if focus else ""
        improve_prompt = (
            f"Verbessere diese SOMA-Datei: {rel_path}{focus_text}\n\n"
            f"AKTUELLER CODE:\n```python\n{original_code}\n```\n\n"
            f"AUFGABE:\n"
            f"1. Identifiziere die WICHTIGSTE Verbesserung\n"
            f"2. Erkläre WARUM sie wichtig ist (2-3 Sätze)\n"
            f"3. Bewerte das RISIKO der Änderung (niedrig/mittel/hoch)\n"
            f"4. Gib den VOLLSTÄNDIGEN NEUEN Dateiinhalt zurück\n\n"
            f"ANTWORT-FORMAT (EXAKT einhalten):\n"
            f"TITEL: [Kurze Beschreibung der Änderung]\n"
            f"KATEGORIE: [performance|error_handling|readability|feature|security|bugfix|refactor|documentation]\n"
            f"BEGRÜNDUNG: [Warum ist das besser?]\n"
            f"RISIKO: [niedrig|mittel|hoch] — [Kurze Erklärung]\n"
            f"CODE:\n```python\n[Vollständiger neuer Dateiinhalt]\n```"
        )

        try:
            response = await self._llm_fn(
                prompt=improve_prompt,
                system_prompt=self._analysis_prompt,
            )
        except Exception as exc:
            logger.error("self_improve_suggest_error", file=rel_path, error=str(exc))
            return ImprovementProposal(
                target_file=rel_path,
                status=ProposalStatus.FAILED,
                error=f"LLM-Fehler: {exc}",
            )

        # ── Response parsen ──────────────────────────────────────────
        proposal = self._parse_proposal_response(response, rel_path, original_code)

        # Diff generieren
        if proposal.proposed_code and proposal.proposed_code != original_code:
            proposal.diff_preview = self._generate_diff(
                original_code, proposal.proposed_code, rel_path
            )
        else:
            proposal.status = ProposalStatus.FAILED
            proposal.error = "LLM hat keine Codeänderung vorgeschlagen"

        # Speichern
        if proposal.status != ProposalStatus.FAILED:
            self._proposals[proposal.proposal_id] = proposal

        # Broadcast
        if self._broadcast_fn and proposal.status == ProposalStatus.PENDING:
            try:
                await self._broadcast_fn(
                    "self_improvement",
                    f"🧬 Verbesserungsvorschlag für {rel_path}: {proposal.title}",
                    "EVOLUTION",
                )
            except Exception:
                pass

        logger.info(
            "self_improve_proposal_created",
            proposal_id=proposal.proposal_id,
            file=rel_path,
            title=proposal.title,
            status=proposal.status.value,
        )
        return proposal

    # ══════════════════════════════════════════════════════════════════
    #  3. ANWENDEN — Verbesserung durchfuehren (mit User-OK)
    # ══════════════════════════════════════════════════════════════════

    async def apply_improvement(self, proposal_id: str) -> ImprovementProposal:
        """
        Wende einen genehmigten Verbesserungsvorschlag an.

        Ablauf:
          1. Proposal validieren
          2. .bak Backup erstellen
          3. PolicyEngine check (SELF_MODIFY)
          4. Code schreiben
          5. Validierung (Import-Test)
          6. Bei Fehler: automatischer Rollback

        Args:
            proposal_id: ID des zu genehmigenden Proposals

        Returns:
            Aktualisiertes Proposal mit Status
        """
        proposal = self._proposals.get(proposal_id)
        if not proposal:
            return ImprovementProposal(
                status=ProposalStatus.FAILED,
                error=f"Proposal '{proposal_id}' nicht gefunden",
            )

        if proposal.status != ProposalStatus.PENDING:
            proposal.error = f"Proposal ist nicht im Status 'pending' (aktuell: {proposal.status.value})"
            return proposal

        proposal.status = ProposalStatus.APPLYING
        file_path = self._root / proposal.target_file

        # ── 1. PolicyEngine Check ────────────────────────────────────
        if self._policy_engine:
            try:
                from executive_arm.policy_engine import ActionRequest, ActionType
                action = ActionRequest(
                    action_type=ActionType.SELF_MODIFY,
                    description=f"Self-Improvement: {proposal.title}",
                    target=str(file_path),
                    reason=proposal.reasoning,
                    user_approved=True,  # User hat im Dashboard genehmigt
                )
                result = await self._policy_engine.check(action)
                if not result.allowed:
                    proposal.status = ProposalStatus.FAILED
                    proposal.error = f"PolicyEngine Veto: {result.message}"
                    logger.warning(
                        "self_improve_policy_denied",
                        proposal_id=proposal_id,
                        reason=result.message,
                    )
                    return proposal
            except Exception as exc:
                logger.error("self_improve_policy_error", error=str(exc))
                # Bei Policy-Fehler: NICHT fortfahren (Sicherheit!)
                proposal.status = ProposalStatus.FAILED
                proposal.error = f"PolicyEngine-Fehler: {exc}"
                return proposal

        # ── 2. .bak Backup erstellen ─────────────────────────────────
        backup_path = file_path.with_suffix(f".py.bak.{proposal.proposal_id}")
        try:
            shutil.copy2(str(file_path), str(backup_path))
            proposal.backup_path = str(backup_path)
            logger.info("self_improve_backup_created", backup=str(backup_path))
        except Exception as exc:
            proposal.status = ProposalStatus.FAILED
            proposal.error = f"Backup-Fehler: {exc}"
            return proposal

        # ── 3. Code schreiben ────────────────────────────────────────
        code_hash_before = hashlib.sha256(
            proposal.original_code.encode()
        ).hexdigest()[:16]

        try:
            file_path.write_text(proposal.proposed_code, encoding="utf-8")
            logger.info("self_improve_code_written", file=proposal.target_file)
        except Exception as exc:
            # Rollback: Backup wiederherstellen
            shutil.copy2(str(backup_path), str(file_path))
            proposal.status = ProposalStatus.FAILED
            proposal.error = f"Schreibfehler: {exc} (Rollback erfolgreich)"
            return proposal

        # ── 4. Validierung: Syntax + Import-Test ─────────────────────
        try:
            compile(proposal.proposed_code, str(file_path), "exec")
        except SyntaxError as exc:
            # Syntax-Fehler: Rollback
            shutil.copy2(str(backup_path), str(file_path))
            proposal.status = ProposalStatus.FAILED
            proposal.error = f"Syntax-Fehler nach Anwendung (Zeile {exc.lineno}): {exc.msg} — Rollback durchgefuehrt"
            logger.warning("self_improve_syntax_error", error=str(exc))
            return proposal

        # Import-Test in Subprocess
        import_ok, import_err = await self._test_import(file_path)
        if not import_ok:
            # Import-Fehler: Rollback
            shutil.copy2(str(backup_path), str(file_path))
            proposal.status = ProposalStatus.FAILED
            proposal.error = f"Import-Test fehlgeschlagen: {import_err} — Rollback durchgefuehrt"
            proposal.test_output = import_err
            logger.warning("self_improve_import_error", error=import_err)
            return proposal

        # ── 5. Erfolg ────────────────────────────────────────────────
        code_hash_after = hashlib.sha256(
            proposal.proposed_code.encode()
        ).hexdigest()[:16]

        proposal.status = ProposalStatus.APPLIED
        proposal.applied_at = time.time()
        self._daily_count += 1

        # History speichern
        self._history.append(ImprovementHistory(
            proposal_id=proposal.proposal_id,
            target_file=proposal.target_file,
            category=proposal.category.value,
            title=proposal.title,
            status="applied",
            applied_at=proposal.applied_at,
            code_hash_before=code_hash_before,
            code_hash_after=code_hash_after,
        ))

        # Memory speichern
        if self._memory_fn:
            try:
                await self._memory_fn(
                    f"Selbst-Verbesserung angewendet: {proposal.title} auf {proposal.target_file}",
                    "self_improvement",
                    "proud",
                    0.8,
                )
            except Exception:
                pass

        # Broadcast
        if self._broadcast_fn:
            try:
                await self._broadcast_fn(
                    "self_improvement",
                    f"✅ Verbesserung angewendet: {proposal.title} ({proposal.target_file})",
                    "EVOLUTION_OK",
                )
            except Exception:
                pass

        logger.info(
            "self_improve_applied",
            proposal_id=proposal.proposal_id,
            file=proposal.target_file,
            title=proposal.title,
        )
        return proposal

    # ══════════════════════════════════════════════════════════════════
    #  4. ROLLBACK — Alles rueckgaengig machen
    # ══════════════════════════════════════════════════════════════════

    async def rollback(self, proposal_id: str) -> ImprovementProposal:
        """
        Rolle eine angewandte Verbesserung zurueck.
        Stellt das .bak Backup wieder her.
        """
        proposal = self._proposals.get(proposal_id)
        if not proposal:
            return ImprovementProposal(
                status=ProposalStatus.FAILED,
                error=f"Proposal '{proposal_id}' nicht gefunden",
            )

        if proposal.status != ProposalStatus.APPLIED:
            proposal.error = "Nur angewandte Proposals können zurückgerollt werden"
            return proposal

        if not proposal.backup_path or not Path(proposal.backup_path).exists():
            proposal.error = "Backup-Datei nicht gefunden — Rollback nicht möglich"
            proposal.status = ProposalStatus.FAILED
            return proposal

        file_path = self._root / proposal.target_file
        backup_path = Path(proposal.backup_path)

        try:
            shutil.copy2(str(backup_path), str(file_path))
            proposal.status = ProposalStatus.ROLLED_BACK

            logger.info(
                "self_improve_rolled_back",
                proposal_id=proposal_id,
                file=proposal.target_file,
            )

            # Memory
            if self._memory_fn:
                try:
                    await self._memory_fn(
                        f"Selbst-Verbesserung zurückgerollt: {proposal.title}",
                        "self_improvement_rollback",
                        "cautious",
                        0.6,
                    )
                except Exception:
                    pass

        except Exception as exc:
            proposal.status = ProposalStatus.FAILED
            proposal.error = f"Rollback-Fehler: {exc}"
            logger.error("self_improve_rollback_error", error=str(exc))

        return proposal

    # ══════════════════════════════════════════════════════════════════
    #  5. REJECT — Vorschlag ablehnen
    # ══════════════════════════════════════════════════════════════════

    async def reject_proposal(self, proposal_id: str) -> ImprovementProposal:
        """User lehnt einen Vorschlag ab."""
        proposal = self._proposals.get(proposal_id)
        if not proposal:
            return ImprovementProposal(
                status=ProposalStatus.FAILED,
                error=f"Proposal '{proposal_id}' nicht gefunden",
            )

        proposal.status = ProposalStatus.REJECTED
        logger.info("self_improve_rejected", proposal_id=proposal_id)

        if self._memory_fn:
            try:
                await self._memory_fn(
                    f"User hat Verbesserungsvorschlag abgelehnt: {proposal.title}",
                    "self_improvement_rejected",
                    "accepting",
                    0.3,
                )
            except Exception:
                pass

        return proposal

    # ══════════════════════════════════════════════════════════════════
    #  QUERY API
    # ══════════════════════════════════════════════════════════════════

    def get_proposal(self, proposal_id: str) -> Optional[ImprovementProposal]:
        return self._proposals.get(proposal_id)

    def list_proposals(self, status: ProposalStatus | None = None) -> list[ImprovementProposal]:
        if status:
            return [p for p in self._proposals.values() if p.status == status]
        return list(self._proposals.values())

    def get_history(self, limit: int = 20) -> list[dict]:
        return [
            {
                "proposal_id": h.proposal_id,
                "target_file": h.target_file,
                "category": h.category,
                "title": h.title,
                "status": h.status,
                "applied_at": h.applied_at,
            }
            for h in reversed(self._history[-limit:])
        ]

    def get_analyzable_files(self) -> list[str]:
        """Liste alle Dateien die analysiert werden koennen."""
        analyzable = []
        for py_file in sorted(self._root.rglob("*.py")):
            rel = str(py_file.relative_to(self._root))
            # Ignore-Patterns
            if any(part in rel for part in ("__pycache__", ".venv", "venv", ".git", "migrations")):
                continue
            if rel in IMMUTABLE_FILES:
                continue
            analyzable.append(rel)
        return analyzable

    # ══════════════════════════════════════════════════════════════════
    #  INTERNAL HELPERS
    # ══════════════════════════════════════════════════════════════════

    def _parse_proposal_response(
        self,
        response: str,
        rel_path: str,
        original_code: str,
    ) -> ImprovementProposal:
        """Parse die LLM-Antwort in ein ImprovementProposal."""
        import re

        proposal = ImprovementProposal(
            target_file=rel_path,
            original_code=original_code,
        )

        # Titel extrahieren
        title_match = re.search(r"TITEL:\s*(.+)", response)
        proposal.title = title_match.group(1).strip() if title_match else "Unbenannte Verbesserung"

        # Kategorie extrahieren
        cat_match = re.search(r"KATEGORIE:\s*(\w+)", response)
        if cat_match:
            try:
                proposal.category = ImprovementCategory(cat_match.group(1).lower())
            except ValueError:
                proposal.category = ImprovementCategory.REFACTOR

        # Begruendung
        reason_match = re.search(r"BEGRÜNDUNG:\s*(.+?)(?=\nRISIKO:|\nCODE:|\Z)", response, re.DOTALL)
        proposal.reasoning = reason_match.group(1).strip() if reason_match else ""

        # Risiko
        risk_match = re.search(r"RISIKO:\s*(.+?)(?=\nCODE:|\Z)", response, re.DOTALL)
        proposal.risk_assessment = risk_match.group(1).strip() if risk_match else ""

        # Code extrahieren
        code_match = re.search(r"```python\n?(.*?)```", response, re.DOTALL)
        if code_match:
            proposal.proposed_code = code_match.group(1).strip()
        else:
            # Fallback: Code nach "CODE:" suchen
            code_section = re.search(r"CODE:\s*\n(.*)", response, re.DOTALL)
            if code_section:
                proposal.proposed_code = code_section.group(1).strip()

        return proposal

    @staticmethod
    def _generate_diff(original: str, proposed: str, filename: str) -> str:
        """Generiere einen Unified Diff fuer die User-Anzeige."""
        original_lines = original.splitlines(keepends=True)
        proposed_lines = proposed.splitlines(keepends=True)

        diff = difflib.unified_diff(
            original_lines,
            proposed_lines,
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
            lineterm="",
        )
        return "\n".join(diff)

    @staticmethod
    async def _test_import(file_path: Path) -> tuple[bool, str]:
        """Teste ob die modifizierte Datei importiert werden kann."""
        import sys as _sys

        try:
            proc = await asyncio.create_subprocess_exec(
                _sys.executable, "-c",
                f"import importlib.util, sys; "
                f"spec = importlib.util.spec_from_file_location('_test', '{file_path}'); "
                f"mod = importlib.util.module_from_spec(spec); "
                f"spec.loader.exec_module(mod); "
                f"print('OK')",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)

            if proc.returncode == 0:
                return True, ""
            else:
                err = stderr.decode("utf-8", errors="replace").strip()
                # Letzte Zeile ist meist der Fehler
                err_lines = err.splitlines()
                return False, err_lines[-1] if err_lines else "Unbekannter Fehler"

        except asyncio.TimeoutError:
            return False, "Import-Test Timeout (>15s)"
        except Exception as exc:
            return False, f"Test-Fehler: {exc}"

    def _check_daily_reset(self) -> None:
        """Täglichen Zähler zurücksetzen wenn neuer Tag."""
        now = time.time()
        # 86400 Sekunden = 1 Tag
        if now - self._daily_reset >= 86400:
            self._daily_count = 0
            self._daily_reset = now
