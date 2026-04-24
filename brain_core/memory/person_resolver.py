"""
Person Resolver — SOMA kennt die Menschen im Haushalt.
======================================================
Löst Beziehungsreferenzen auf:
  "meine Mutter"     → "Katrin"
  "Sarahs Lieblingslied" → subject="Sarah"
  "mein Bruder"      → "Max" (wenn bekannt)

Baut einen In-Memory-Index aus L3-Fakten:
  - Relationship-Fakten:  Owner → Schwester = Sarah
  - Person-Fakten:        Sarah → Lieblingslied = XYZ

Non-negotiable:
  - Cache mit TTL (60s) — kein DB-Zugriff pro Turn
  - Fallback: Wenn Name nicht auflösbar → subject="Owner"
  - Thread-safe (nur reads nach init)
"""

from __future__ import annotations

import re
import time
import logging
from typing import Optional

logger = logging.getLogger("soma.memory.person_resolver")

# ── Beziehungs-Mapping: Deutsch → Canonical ─────────────────────────────
# "meine Mutter", "meiner Mutter", "Mutter" → role "Mutter"
RELATIONSHIP_PATTERNS: list[tuple[str, str]] = [
    # (regex pattern, canonical role)
    (r"\b(?:meine[rnms]?)\s+Mutter\b", "Mutter"),
    (r"\b(?:meine[rnms]?)\s+Vater\b", "Vater"),
    (r"\b(?:meine[rnms]?)\s+Schwester\b", "Schwester"),
    (r"\b(?:meine[rnms]?)\s+Bruder\b", "Bruder"),
    (r"\b(?:meine[rnms]?)\s+Frau\b", "Partnerin"),
    (r"\b(?:meine[rnms]?)\s+Mann\b", "Partner"),
    (r"\b(?:meine[rnms]?)\s+Freundin\b", "Freundin"),
    (r"\b(?:meine[rnms]?)\s+Freund\b", "Freund"),
    (r"\b(?:meine[rnms]?)\s+Tochter\b", "Tochter"),
    (r"\b(?:meine[rnms]?)\s+Sohn\b", "Sohn"),
    (r"\b(?:meine[rnms]?)\s+Oma\b", "Oma"),
    (r"\b(?:meine[rnms]?)\s+Opa\b", "Opa"),
    (r"\b(?:meine[rnms]?)\s+Katze\b", "Katze"),
    (r"\b(?:meine[rnms]?)\s+Hund\b", "Hund"),
    # Ohne Possessiv — nur wenn vor einem bekannten Keyword
    (r"\bMutter\b", "Mutter"),
    (r"\bVater\b", "Vater"),
    (r"\bSchwester\b", "Schwester"),
    (r"\bBruder\b", "Bruder"),
    (r"\bMama\b", "Mutter"),
    (r"\bPapa\b", "Vater"),
]


class PersonResolver:
    """
    Löst Beziehungsreferenzen in konkreten Personennamen auf.
    Cached Index aus L3-Faktentabelle.
    """

    def __init__(self):
        # role → name Mapping: {"Mutter": "Katrin", "Schwester": "Sarah"}
        self._role_to_name: dict[str, str] = {}
        # name → [facts] — alle bekannten Personen
        self._known_persons: set[str] = set()
        self._cache_ts: float = 0.0
        self._CACHE_TTL: float = 60.0

    async def refresh(self, semantic_memory) -> None:
        """
        Baut den Index aus L3-Fakten neu auf.
        Aufgerufen bei Startup + periodisch.
        """
        now = time.time()
        if (now - self._cache_ts) < self._CACHE_TTL:
            return  # Cache noch frisch

        try:
            if not semantic_memory or not semantic_memory._conn:
                return

            # Alle Relationship-Fakten laden
            rows = semantic_memory._conn.execute(
                "SELECT subject, fact FROM facts "
                "WHERE category = 'relationship' "
                "ORDER BY confidence DESC"
            ).fetchall()

            role_to_name: dict[str, str] = {}
            known_persons: set[str] = set()

            for subject, fact in rows:
                # Format: "Schwester = Sarah" oder "Mutter = Katrin"
                if "=" in fact:
                    parts = fact.split("=", 1)
                    role = parts[0].strip()
                    name = parts[1].strip()
                    if role and name:
                        role_to_name[role] = name
                        known_persons.add(name)

            # Auch alle Subjects die nicht "Owner", "Haushalt", "System", "soma" sind
            all_subjects = semantic_memory._conn.execute(
                "SELECT DISTINCT subject FROM facts "
                "WHERE subject NOT IN ('Owner', 'Haushalt', 'System', 'soma')"
            ).fetchall()
            for (subj,) in all_subjects:
                known_persons.add(subj)

            self._role_to_name = role_to_name
            self._known_persons = known_persons
            self._cache_ts = now

            if role_to_name:
                logger.info(
                    "person_resolver_refreshed",
                    roles=dict(role_to_name),
                    persons=list(known_persons),
                )

        except Exception as e:
            logger.warning(f"person_resolver_refresh_error: {e}")

    def resolve_relationship(self, text: str) -> Optional[str]:
        """
        Löst eine Beziehungsreferenz im Text auf.
        "meine Mutter" → "Katrin" (wenn bekannt).
        Returns: Personenname oder None.
        """
        for pattern, role in RELATIONSHIP_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                name = self._role_to_name.get(role)
                if name:
                    return name
        return None

    def resolve_subject(self, text: str) -> str:
        """
        Bestimmt das beste Subject für einen Fakt.
        
        "Sarahs Lieblingslied ist XYZ" → "Sarah"
        "Meine Mutter mag Pizza" → "Katrin"  
        "Ich mag Katzen" → "Owner"
        """
        # 1. Prüfe auf Beziehungsreferenz
        person = self.resolve_relationship(text)
        if person:
            return person

        # 2. Prüfe ob ein bekannter Name direkt erwähnt wird
        # "Sarah mag Musik" → subject = "Sarah"
        for name in self._known_persons:
            # Possessiv: "Sarahs", "Katrins"
            if re.search(rf"\b{re.escape(name)}s?\b", text, re.IGNORECASE):
                return name

        # 3. Default: Owner (der sprechende Nutzer)
        return "Owner"

    def get_all_person_names(self) -> list[str]:
        """Alle bekannten Personennamen (für Prompt-Kontext)."""
        return sorted(self._known_persons)

    def get_role_mapping(self) -> dict[str, str]:
        """Gibt das role→name Mapping zurück (für Prompt-Kontext)."""
        return dict(self._role_to_name)

    def get_person_for_role(self, role: str) -> Optional[str]:
        """Lookup: Rolle → Name (z.B. "Mutter" → "Katrin")."""
        return self._role_to_name.get(role)

    @property
    def is_populated(self) -> bool:
        return bool(self._role_to_name) or bool(self._known_persons)


# ── Singleton ────────────────────────────────────────────────────────────
_resolver: Optional[PersonResolver] = None


def get_person_resolver() -> PersonResolver:
    global _resolver
    if _resolver is None:
        _resolver = PersonResolver()
    return _resolver
