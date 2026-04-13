"""
SOMA-AI Action Registry (Enhanced)
===================================
Single Source of Truth für alle Action Tags.
Inspiriert von Claude Code's Tool Definition Pattern.

Lädt action_registry_enhanced.json (Fallback: action_registry.json)

Bietet:
  - generate_prompt_section() → System-Prompt-Text für das LLM
  - get_tag_info(tag_type)    → Metadaten eines Tags
  - validate_tag(tag_type, params) → Prüft ob ein Tag gültig ist
  - get_all_tags()            → Alle registrierten Tags
  - get_nano_capable_tags()   → Tags die Nano instant feuern kann
  - get_reask_tags()          → Tags die Ergebnisse ans LLM zurückgeben

Erweiterte API (Claude Code Pattern):
  - get_concurrency_safe_tags() → Tags die parallel laufen können
  - get_read_only_tags()        → Lesende Tags ohne Seiteneffekte  
  - get_destructive_tags()      → Gefährliche/destruktive Tags
  - get_tag_retry_policy(tag)   → Retry-Policy für einen Tag
  - get_tag_timeout(tag)        → Timeout in ms
  - search_tags(query)          → Semantische Tag-Suche via search_hint
  - get_global_settings()       → Globale Einstellungen
  - get_do_rules(tag)           → DO-Regeln für einen Tag
  - get_dont_rules(tag)         → DON'T-Regeln für einen Tag

Wird von logic_router.py, action_stream_parser.py, action_executor.py genutzt.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional, Dict, List, Set, Any

import structlog

logger = structlog.get_logger("soma.action_registry")

# ══════════════════════════════════════════════════════════════════════════
# Singleton Registry
# ══════════════════════════════════════════════════════════════════════════

_REGISTRY: Optional[dict] = None
_ENHANCED_REGISTRY_PATH = Path(__file__).parent / "action_registry_enhanced.json"
_FALLBACK_REGISTRY_PATH = Path(__file__).parent / "action_registry.json"


def _load_registry() -> dict:
    """
    Lade die Registry aus der JSON-Datei (cached).
    
    Versucht zuerst action_registry_enhanced.json,
    fällt auf action_registry.json zurück.
    """
    global _REGISTRY
    if _REGISTRY is not None:
        return _REGISTRY

    # Versuche Enhanced Registry zuerst
    registry_path = _ENHANCED_REGISTRY_PATH
    if not registry_path.exists():
        registry_path = _FALLBACK_REGISTRY_PATH
        logger.info("using_fallback_registry", path=str(registry_path))

    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            _REGISTRY = json.load(f)
        
        is_enhanced = "global_settings" in _REGISTRY
        logger.info(
            "action_registry_loaded",
            tags=len(get_all_tags()),
            enhanced=is_enhanced,
            path=str(registry_path)
        )
    except Exception as exc:
        logger.error("action_registry_load_failed", error=str(exc))
        _REGISTRY = {"categories": {}, "rules": [], "global_settings": {}}

    return _REGISTRY


def reload_registry() -> dict:
    """Force-Reload der Registry (z.B. nach Plugin-Update)."""
    global _REGISTRY
    _REGISTRY = None
    return _load_registry()


def is_enhanced_registry() -> bool:
    """Prüft ob die Enhanced Registry geladen ist."""
    registry = _load_registry()
    return "global_settings" in registry


# ══════════════════════════════════════════════════════════════════════════
# Basic Query-API
# ══════════════════════════════════════════════════════════════════════════

def get_all_tags() -> Dict[str, dict]:
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


def get_nano_capable_tags() -> List[str]:
    """Tags die Nano ohne LLM feuern kann."""
    return [t for t, info in get_all_tags().items() if info.get("nano_capable")]


def get_reask_tags() -> Set[str]:
    """Tags deren Ergebnisse ans LLM zurückgegeben werden (search, browse, etc.)."""
    return {t for t, info in get_all_tags().items() if info.get("needs_reask")}


def get_tts_confirm_tags() -> Set[str]:
    """Tags die eine TTS-Bestätigung brauchen."""
    return {t for t, info in get_all_tags().items() if info.get("tts_confirm")}


# ══════════════════════════════════════════════════════════════════════════
# Enhanced Query-API (Claude Code Pattern)
# ══════════════════════════════════════════════════════════════════════════

def get_concurrency_safe_tags() -> List[str]:
    """
    Tags die parallel ausgeführt werden können.
    
    Claude Code Pattern: Concurrency-safe tools laufen parallel,
    andere (z.B. shell, file) seriell.
    """
    return [
        t for t, info in get_all_tags().items()
        if info.get("concurrency_safe", False)
    ]


def get_read_only_tags() -> List[str]:
    """
    Tags die nur lesen und keine Seiteneffekte haben.
    
    Diese können gefahrlos wiederholt oder parallel ausgeführt werden.
    """
    return [
        t for t, info in get_all_tags().items()
        if info.get("is_read_only", False)
    ]


def get_destructive_tags() -> List[str]:
    """
    Tags die destruktive/gefährliche Operationen ausführen.
    
    Diese erfordern besondere Vorsicht:
    - file mit delete
    - system restart/shutdown
    - ha_call für bestimmte Domains
    """
    return [
        t for t, info in get_all_tags().items()
        if info.get("is_destructive", False)
    ]


def get_tag_retry_policy(tag_type: str) -> Dict[str, Any]:
    """
    Retry-Policy für einen Tag.
    
    Returns:
        {
            "max_retries": int,
            "backoff_ms": int,
            "retryable_errors": List[str]
        }
    
    Default: max_retries=1, backoff_ms=500
    """
    info = get_tag_info(tag_type)
    if not info:
        return {"max_retries": 1, "backoff_ms": 500, "retryable_errors": []}
    
    return info.get("retry_policy", {
        "max_retries": 1,
        "backoff_ms": 500,
        "retryable_errors": []
    })


def get_tag_timeout(tag_type: str) -> int:
    """
    Timeout für einen Tag in Millisekunden.
    
    Default: 30000ms (30 Sekunden)
    """
    info = get_tag_info(tag_type)
    if not info:
        return 30000
    return info.get("timeout_ms", 30000)


def get_do_rules(tag_type: str) -> List[str]:
    """
    DO-Regeln für einen Tag (Best Practices).
    
    Claude Code Pattern: Jedes Tool kommt mit do/dont Regeln
    die dem LLM klare Guidance geben.
    """
    info = get_tag_info(tag_type)
    if not info:
        return []
    return info.get("do_rules", [])


def get_dont_rules(tag_type: str) -> List[str]:
    """
    DON'T-Regeln für einen Tag (Was zu vermeiden ist).
    """
    info = get_tag_info(tag_type)
    if not info:
        return []
    return info.get("dont_rules", [])


def get_global_settings() -> Dict[str, Any]:
    """
    Globale Einstellungen aus der Registry.
    
    Enthält z.B.:
    - max_actions_per_turn
    - confirm_destructive
    - debug_logging
    """
    registry = _load_registry()
    return registry.get("global_settings", {})


def search_tags(query: str) -> List[str]:
    """
    Semantische Tag-Suche via search_hint.
    
    Claude Code Pattern: Tools haben search_hints die semantische
    Suche ermöglichen.
    
    Args:
        query: Suchanfrage (z.B. "Licht", "Timer", "Web")
    
    Returns:
        Liste von Tag-Namen, sortiert nach Relevanz
    """
    query_lower = query.lower()
    query_words = set(query_lower.split())
    results = []
    
    for tag_type, info in get_all_tags().items():
        score = 0
        
        # Exakter Match im Tag-Namen
        if query_lower in tag_type.lower():
            score += 10
        
        # Match in Beschreibung
        desc = info.get("description", "").lower()
        if query_lower in desc:
            score += 5
        
        # Match in search_hint (Enhanced)
        search_hint = info.get("search_hint", "").lower()
        hint_words = set(search_hint.split())
        matches = query_words & hint_words
        score += len(matches) * 3
        
        # Partial match in search_hint
        if any(query_lower in w for w in hint_words):
            score += 2
        
        if score > 0:
            results.append((tag_type, score))
    
    # Sortiert nach Score absteigend
    results.sort(key=lambda x: x[1], reverse=True)
    return [t[0] for t in results]


def get_tags_for_domain(domain: str) -> List[str]:
    """
    Tags für eine bestimmte Domain (z.B. "smart_home", "web", "system").
    """
    registry = _load_registry()
    tags = []
    
    for cat_key, cat_data in registry.get("categories", {}).items():
        if domain.lower() in cat_key.lower():
            tags.extend(cat_data.get("tags", {}).keys())
    
    return tags


# ══════════════════════════════════════════════════════════════════════════
# Validation (Enhanced)
# ══════════════════════════════════════════════════════════════════════════

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


def validate_tag_detailed(tag_type: str, params: dict) -> Dict[str, Any]:
    """
    Detaillierte Validierung mit allen Feldern.
    
    Returns:
        {
            "valid": bool,
            "error_message": str | None,
            "error_code": str | None,
            "missing_params": List[str],
            "invalid_params": List[str],
            "warnings": List[str]
        }
    """
    result = {
        "valid": True,
        "error_message": None,
        "error_code": None,
        "missing_params": [],
        "invalid_params": [],
        "warnings": []
    }
    
    info = get_tag_info(tag_type)
    if not info:
        result["valid"] = False
        result["error_message"] = f"Unbekannter Action-Tag: {tag_type}"
        result["error_code"] = "UNKNOWN_TAG"
        return result
    
    # Required params prüfen
    for param_name, param_spec in info.get("params", {}).items():
        if param_spec.get("required") and param_name not in params:
            result["missing_params"].append(param_name)
    
    if result["missing_params"]:
        result["valid"] = False
        result["error_message"] = f"Pflicht-Parameter fehlen: {', '.join(result['missing_params'])}"
        result["error_code"] = "MISSING_PARAMS"
    
    # Typ-Validierung für bekannte Params
    for param_name, value in params.items():
        param_spec = info.get("params", {}).get(param_name, {})
        expected_type = param_spec.get("type")
        
        if expected_type == "string" and not isinstance(value, str):
            result["invalid_params"].append(f"{param_name}: expected string")
        elif expected_type == "number" and not isinstance(value, (int, float)):
            result["invalid_params"].append(f"{param_name}: expected number")
        elif expected_type == "boolean" and not isinstance(value, bool):
            result["invalid_params"].append(f"{param_name}: expected boolean")
    
    if result["invalid_params"] and result["valid"]:
        result["warnings"].append(f"Typ-Warnung: {', '.join(result['invalid_params'])}")
    
    # Destruktive Warnung
    if info.get("is_destructive"):
        result["warnings"].append("Diese Aktion hat destruktive Auswirkungen!")
    
    return result


# ══════════════════════════════════════════════════════════════════════════
# System-Prompt Generator (Enhanced)
# ══════════════════════════════════════════════════════════════════════════

def generate_prompt_section(include_rules: bool = True, verbose: bool = False) -> str:
    """
    Generiere den AKTIONS-SYSTEM Abschnitt des System-Prompts.

    KOMPAKT-FORMAT: Maximale Token-Effizienz.
    Persönlichkeit > Werkzeuge. Tags sind nur eine Referenztabelle.
    
    Args:
        include_rules: Ob DO/DON'T Regeln inkludiert werden sollen
        verbose: Ob ausführliche Beschreibungen inkludiert werden
    """
    registry = _load_registry()
    categories = registry.get("categories", {})
    rules = registry.get("rules", [])
    global_settings = registry.get("global_settings", {})

    lines = [
        "",
        "═══ AKTIONS-SYSTEM ═══",
        "Setze [ACTION:...] Tags in deiner Antwort. Tags werden intern ausgeführt, nicht vorgelesen.",
        "Schreibe IMMER eine kurze menschliche Bestätigung VOR oder NACH dem Tag!",
        "",
    ]
    
    # Globale Limits
    max_actions = global_settings.get("max_actions_per_turn", 10)
    lines.append(f"MAX {max_actions} ACTIONS PRO ANTWORT.")
    lines.append("")

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
            
            # DO/DON'T Regeln (wenn erweiterte Registry)
            if include_rules and is_enhanced_registry():
                do_rules = tag_info.get("do_rules", [])
                dont_rules = tag_info.get("dont_rules", [])
                
                if do_rules and verbose:
                    lines.append(f"    DO: {' | '.join(do_rules[:2])}")
                if dont_rules:
                    lines.append(f"    DON'T: {' | '.join(dont_rules[:2])}")

    # Nur die wichtigsten Regeln
    lines.append("")
    key_rules = [r for r in rules if any(w in r.lower() for w in ["niemals", "immer", "bestätigung", "web", "nicht vorgelesen"])]
    if key_rules:
        lines.append("REGELN: " + " | ".join(key_rules[:5]))

    return "\n".join(lines)


def generate_tool_reference(tag_type: str) -> str:
    """
    Generiert eine vollständige Referenz für einen einzelnen Tag.
    
    Claude Code Pattern: Detaillierte Tool-Dokumentation mit
    do/dont Rules für komplexe Interaktionen.
    """
    info = get_tag_info(tag_type)
    if not info:
        return f"Tag '{tag_type}' nicht gefunden."
    
    lines = [
        f"═══ {tag_type.upper()} ═══",
        f"Beschreibung: {info.get('description', 'N/A')}",
        "",
    ]
    
    # Parameter
    params = info.get("params", {})
    if params:
        lines.append("PARAMETER:")
        for p_name, p_spec in params.items():
            req = "* PFLICHT" if p_spec.get("required") else "  optional"
            lines.append(f"  {req} {p_name}: {p_spec.get('description', '')}")
    
    # Beispiele
    examples = info.get("examples", [])
    if examples:
        lines.append("")
        lines.append("BEISPIELE:")
        for ex in examples[:3]:
            lines.append(f"  {ex}")
    
    # DO-Regeln
    do_rules = info.get("do_rules", [])
    if do_rules:
        lines.append("")
        lines.append("DO:")
        for rule in do_rules:
            lines.append(f"  ✓ {rule}")
    
    # DON'T-Regeln
    dont_rules = info.get("dont_rules", [])
    if dont_rules:
        lines.append("")
        lines.append("DON'T:")
        for rule in dont_rules:
            lines.append(f"  ✗ {rule}")
    
    # Metadaten
    lines.append("")
    lines.append("EIGENSCHAFTEN:")
    lines.append(f"  Timeout:          {info.get('timeout_ms', 30000)}ms")
    lines.append(f"  Concurrency-Safe: {'Ja' if info.get('concurrency_safe') else 'Nein'}")
    lines.append(f"  Read-Only:        {'Ja' if info.get('is_read_only') else 'Nein'}")
    lines.append(f"  Destruktiv:       {'JA!' if info.get('is_destructive') else 'Nein'}")
    
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# Zum Testen
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(generate_prompt_section())
    print(f"\n--- Statistik ---")
    all_tags = get_all_tags()
    print(f"Registrierte Tags:    {len(all_tags)}")
    print(f"Enhanced Registry:    {'Ja' if is_enhanced_registry() else 'Nein'}")
    print(f"Nano-fähig:           {len(get_nano_capable_tags())}")
    print(f"Re-Ask (Web etc.):    {len(get_reask_tags())}")
    print(f"TTS-Bestätigung:      {len(get_tts_confirm_tags())}")
    print(f"Concurrency-Safe:     {len(get_concurrency_safe_tags())}")
    print(f"Read-Only:            {len(get_read_only_tags())}")
    print(f"Destruktiv:           {len(get_destructive_tags())}")
    
    print(f"\n--- Global Settings ---")
    for key, value in get_global_settings().items():
        print(f"  {key}: {value}")
    
    print(f"\n--- Tag-Suche 'licht' ---")
    results = search_tags("licht")
    for tag in results[:5]:
        print(f"  → {tag}")
    
    print(f"\n--- Tool Reference: ha_call ---")
    print(generate_tool_reference("ha_call"))
