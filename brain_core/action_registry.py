"""
SOMA-AI Action Registry
=========================
Single Source of Truth für alle Action Tags.

Lädt action_registry.json und bietet:
  - generate_prompt_section() → System-Prompt-Text für das LLM
  - get_tag_info(tag_type)    → Metadaten eines Tags
  - validate_tag(tag_type, params) → Prüft ob ein Tag gültig ist
  - get_all_tags()            → Alle registrierten Tags
  - get_nano_capable_tags()   → Tags die Nano instant feuern kann
  - get_reask_tags()          → Tags die Ergebnisse ans LLM zurückgeben

Wird von logic_router.py, action_stream_parser.py und nano_intent.py genutzt.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger("soma.action_registry")

# ── Singleton Registry ───────────────────────────────────────────────────

_REGISTRY: Optional[dict] = None
_REGISTRY_PATH = Path(__file__).parent / "action_registry.json"


def _load_registry() -> dict:
    """Lade die Registry aus der JSON-Datei (cached)."""
    global _REGISTRY
    if _REGISTRY is not None:
        return _REGISTRY

    try:
        with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
            _REGISTRY = json.load(f)
        logger.info("action_registry_loaded", tags=len(get_all_tags()))
    except Exception as exc:
        logger.error("action_registry_load_failed", error=str(exc))
        _REGISTRY = {"categories": {}, "rules": []}

    return _REGISTRY


def reload_registry() -> dict:
    """Force-Reload der Registry (z.B. nach Plugin-Update)."""
    global _REGISTRY
    _REGISTRY = None
    return _load_registry()


# ── Query-API ────────────────────────────────────────────────────────────

def get_all_tags() -> dict[str, dict]:
    """Alle Tags als flaches Dict: {tag_type: tag_info}."""
    registry = _load_registry()
    tags = {}
    for cat_data in registry.get("categories", {}).values():
        for tag_type, tag_info in cat_data.get("tags", {}).items():
            tags[tag_type] = tag_info
    return tags


def get_tag_info(tag_type: str) -> Optional[dict]:
    """Metadaten eines bestimmten Tags."""
    return get_all_tags().get(tag_type)


def get_nano_capable_tags() -> list[str]:
    """Tags die Nano ohne LLM feuern kann."""
    return [t for t, info in get_all_tags().items() if info.get("nano_capable")]


def get_reask_tags() -> set[str]:
    """Tags deren Ergebnisse ans LLM zurückgegeben werden (search, browse, etc.)."""
    return {t for t, info in get_all_tags().items() if info.get("needs_reask")}


def get_tts_confirm_tags() -> set[str]:
    """Tags die eine TTS-Bestätigung brauchen."""
    return {t for t, info in get_all_tags().items() if info.get("tts_confirm")}


def validate_tag(tag_type: str, params: dict) -> tuple[bool, str]:
    """
    Prüfe ob ein Tag-Aufruf gültig ist.

    Returns: (is_valid, error_message)
    """
    info = get_tag_info(tag_type)
    if not info:
        return False, f"Unbekannter Action-Tag: {tag_type}"

    for param_name, param_spec in info.get("params", {}).items():
        if param_spec.get("required") and param_name not in params:
            return False, f"Pflicht-Parameter fehlt: {param_name}"

    return True, ""


# ── System-Prompt Generator ──────────────────────────────────────────────

def generate_prompt_section() -> str:
    """
    Generiere den AKTIONS-SYSTEM Abschnitt des System-Prompts.

    KOMPAKT-FORMAT: Maximale Token-Effizienz.
    Persönlichkeit > Werkzeuge. Tags sind nur eine Referenztabelle.
    """
    registry = _load_registry()
    categories = registry.get("categories", {})
    rules = registry.get("rules", [])

    lines = [
        "",
        "═══ AKTIONS-SYSTEM ═══",
        "Setze [ACTION:...] Tags in deiner Antwort. Tags werden intern ausgeführt, nicht vorgelesen.",
        "Schreibe IMMER eine kurze menschliche Bestätigung VOR oder NACH dem Tag!",
        "",
    ]

    # Kompakte Tag-Referenz: Eine Zeile pro Tag
    for cat_key, cat_data in categories.items():
        icon = cat_data.get("icon", "")
        label = cat_data.get("label", cat_key.upper())
        lines.append(f"{icon} {label}:")

        for tag_type, tag_info in cat_data.get("tags", {}).items():
            desc = tag_info.get("description", "")
            examples = tag_info.get("examples", [])
            # NUR das erste Beispiel, kompakt
            if examples:
                lines.append(f"  {examples[0]}  — {desc}")
            else:
                params = tag_info.get("params", {})
                param_str = " ".join(f'{k}="..."' for k in params)
                lines.append(f"  [ACTION:{tag_type} {param_str}]  — {desc}")

    # Nur die wichtigsten Regeln
    lines.append("")
    key_rules = [r for r in rules if any(w in r.lower() for w in ["niemals", "immer", "bestätigung", "web", "nicht vorgelesen"])]
    if key_rules:
        lines.append("REGELN: " + " | ".join(key_rules[:5]))

    return "\n".join(lines)


# ── Zum Testen ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(generate_prompt_section())
    print(f"\n--- Statistik ---")
    all_tags = get_all_tags()
    print(f"Registrierte Tags: {len(all_tags)}")
    print(f"Nano-fähig:        {len(get_nano_capable_tags())}")
    print(f"Re-Ask (Web etc.): {len(get_reask_tags())}")
    print(f"TTS-Bestätigung:   {len(get_tts_confirm_tags())}")
