"""
User Identity — Dynamische Nutzer-Erkennung statt Hardcoding.
==============================================================
SOMA kennt seinen Nutzer nicht von Geburt an.
Wie ein echtes Bewusstsein: Es muss FRAGEN, LERNEN, ERINNERN.

Beim ersten Start weiß SOMA nichts — und beginnt ein Kennenlerngespräch.
Nach dem Onboarding zieht SOMA den Namen aus dem Gedächtnis (L3 Facts).
Fällt das Gedächtnis aus → Fallback "du" (höflich, natürlich).

Dieses Modul ist das EINZIGE das den Nutzernamen auflöst.
Alle anderen Module importieren `get_user_name()` von hier.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger("soma.memory.user_identity")

# ── Cache (pro Session, nicht persistent) ────────────────────────────────
_cached_user_name: Optional[str] = None
_onboarding_done: Optional[bool] = None


async def get_user_name() -> str:
    """
    Gibt den Namen des Hauptnutzers zurück.
    
    Priorität:
      1. Session-Cache (sofort, 0ms)
      2. L3 Semantic Memory → Fakt mit category='knowledge', subject enthält 'name'
      3. Fallback: "du" (natürlich, nicht robotisch)
    """
    global _cached_user_name

    if _cached_user_name is not None:
        return _cached_user_name

    # Versuche aus dem Gedächtnis zu laden
    name = await _load_name_from_memory()
    if name:
        _cached_user_name = name
        logger.info("user_identity_resolved", name=name, source="memory")
        return name

    # Kein Name bekannt → höfliches "du"
    logger.info("user_identity_unknown", fallback="du")
    return "du"


def get_user_name_sync() -> str:
    """
    Synchrone Version — für Stellen die nicht async sein können.
    Nutzt nur den Cache. Wenn leer → "du".
    """
    return _cached_user_name or "du"


async def set_user_name(name: str):
    """Wird nach dem Onboarding oder nach ACTION:remember aufgerufen."""
    global _cached_user_name
    _cached_user_name = name
    logger.info("user_identity_set", name=name)


def invalidate_cache():
    """Cache leeren — z.B. nach Memory-Wipe."""
    global _cached_user_name, _onboarding_done
    _cached_user_name = None
    _onboarding_done = None


async def is_onboarding_needed() -> bool:
    """
    Prüft ob SOMA seinen Nutzer schon kennt.
    True = erstes Kennenlernen nötig.
    """
    global _onboarding_done

    if _onboarding_done is not None:
        return not _onboarding_done

    name = await _load_name_from_memory()
    if name:
        _onboarding_done = True
        return False

    # Prüfe ob es ÜBERHAUPT Fakten gibt
    has_any = await _has_any_user_facts()
    if has_any:
        _onboarding_done = True
        return False

    _onboarding_done = False
    return True


async def complete_onboarding():
    """Markiert Onboarding als abgeschlossen."""
    global _onboarding_done
    _onboarding_done = True


# ── Interne Helfer ───────────────────────────────────────────────────────

async def _load_name_from_memory() -> Optional[str]:
    """Sucht den Nutzernamen im Langzeit-Gedächtnis."""
    try:
        from brain_core.memory.integration import get_orchestrator
        orch = get_orchestrator()
        
        # Strategie 1: Suche nach explizitem Namens-Fakt
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(
            None, _query_name_facts, orch.semantic,
        )
        if rows:
            # Nimm den Fakt mit höchster Konfidenz
            best = rows[0]
            name = _extract_name_from_fact(best[3])  # fact column
            if name:
                return name

        return None
    except Exception as e:
        logger.debug(f"name_lookup_failed: {e}")
        return None


def _query_name_facts(semantic) -> list:
    """Synchron — wird in Executor ausgeführt."""
    if not semantic._conn:
        return []
    # Suche nach Fakten die einen Namen enthalten
    return semantic._conn.execute(
        """SELECT id, category, subject, fact, confidence 
           FROM facts 
           WHERE (
               fact LIKE '%heißt%' OR fact LIKE '%heiße%' OR 
               fact LIKE '%Name ist%' OR fact LIKE '%name ist%' OR
               fact LIKE '%ich bin%' OR
               category = 'knowledge' AND fact LIKE '%Name%'
           )
           AND subject != 'SOMA'
           ORDER BY confidence DESC
           LIMIT 5""",
    ).fetchall()


def _extract_name_from_fact(fact_text: str) -> Optional[str]:
    """Extrahiert einen Namen aus einem Fakt-String."""
    import re
    
    # Muster: "Der Nutzer heißt Max", "Ich heiße Lisa", "Name ist Patrick"
    patterns = [
        r'(?:heißt|heiße|heisst|heisse)\s+(\w+)',
        r'(?:Name ist|name ist)\s+(\w+)',
        r'(?:Ich bin|ich bin)\s+(\w+)',
        r'(?:Nutzer heißt|User heißt|Bewohner heißt)\s+(\w+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, fact_text)
        if match:
            name = match.group(1).strip()
            # Filter: Keine generischen Wörter
            generics = {
                'der', 'die', 'das', 'ein', 'eine', 'dein', 'mein',
                'soma', 'system', 'nutzer', 'user', 'bewohner',
            }
            if name.lower() not in generics and len(name) > 1:
                return name.capitalize()

    return None


async def _has_any_user_facts() -> bool:
    """Prüft ob es überhaupt Fakten über einen Nutzer gibt."""
    try:
        from brain_core.memory.integration import get_orchestrator
        orch = get_orchestrator()
        loop = asyncio.get_event_loop()
        
        count = await loop.run_in_executor(
            None, _count_user_facts, orch.semantic,
        )
        return count > 0
    except Exception:
        return False


def _count_user_facts(semantic) -> int:
    """Zählt Nicht-SOMA Fakten."""
    if not semantic._conn:
        return 0
    row = semantic._conn.execute(
        "SELECT COUNT(*) FROM facts WHERE subject != 'SOMA'",
    ).fetchone()
    return row[0] if row else 0
