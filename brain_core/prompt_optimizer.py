"""
SOMA-AI Prompt Optimizer — Intent-Based Prompt Routing
======================================================
Analysiert den User-Prompt und baut einen MINIMALEN System-Prompt
der nur die nötigen Sektionen enthält.

Ergebnis: 3-5x weniger Tokens → proportional schnellere Prompt-Eval.

Intent-Kategorien:
  CHAT      → Nur Persona, kein Action-Katalog
  ACTION    → Persona + relevante Action-Tags (nicht alle!)
  QUESTION  → Persona + ggf. Web/Search-Tags
  SYSTEM    → Persona + System/Shell-Tags
  MEDIA     → Persona + Media-Tags
  SMARTHOME → Persona + HA-Tags
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional

import structlog

logger = structlog.get_logger("soma.prompt_optimizer")


class PromptIntent(str, Enum):
    CHAT = "chat"           # Smalltalk, Fragen, Philosophie
    SMARTHOME = "smarthome" # Licht, Heizung, Steckdosen
    MEDIA = "media"         # Musik, YouTube, Volume
    SYSTEM = "system"       # Shell, Prozesse, Pakete, Dateien
    WEB = "web"             # Suche, URLs, Browse
    PHONE = "phone"         # Telefon, TTS-Durchsage
    MEMORY = "memory"       # Merken, Erinnern, Timer


# ── Keyword-Matching für blitzschnelle Intent-Erkennung (<0.1ms) ──────

_INTENT_PATTERNS: dict[PromptIntent, list[str]] = {
    PromptIntent.SMARTHOME: [
        r"\b(licht|lampe|heizung|temperatur|thermostat|steckdose|schalter|rollo|jalousie|klimaanlage)\b",
        r"\b(an|aus|ein|dimm|heller|dunkler|wärmer|kälter|grad)\b.*\b(mach|stell|dreh|setz)\b",
        r"\b(mach|stell|dreh|setz)\b.*\b(an|aus|ein|dimm|heller|dunkler|wärmer|kälter|grad)\b",
    ],
    PromptIntent.MEDIA: [
        r"\b(musik|lied|song|album|playlist|radio|podcast|youtube|spotify)\b",
        r"\b(spiel|abspiel|play|pause|stop|skip|nächst|vorherig|lauter|leiser|lautstärke|volume)\b",
        r"\b(was läuft|was spielst|now playing)\b",
    ],
    PromptIntent.SYSTEM: [
        r"\b(terminal|shell|befehl|command|prozess|service|paket|install|update|neustart|shutdown)\b",
        r"\b(datei|ordner|verzeichnis|löschen|kopieren|verschieben|suche.*datei|öffne.*ordner)\b",
        r"\b(bluetooth|wlan|wifi|netzwerk|vpn)\b",
        r"\b(monitor|bildschirm|helligkeit|brightness|wallpaper|hintergrund)\b",
        r"\b(app|programm|fenster|firefox|browser|chrome)\b",
        r"\b(clipboard|zwischenablage|benachrichtigung|notification)\b",
    ],
    PromptIntent.WEB: [
        r"\b(such|google|recherch|internet|online|webseite|website|url|link)\b",
        r"\b(was ist|wer ist|wo ist|wann|warum|wie viel|aktuell|news|nachrichten|wetter|kurs)\b",
    ],
    PromptIntent.PHONE: [
        r"\b(anruf|anrufen|telefon|festnetz|durchsage|sage.*im|lautsprecher)\b",
    ],
    PromptIntent.MEMORY: [
        r"\b(merk|erinner|vergiss|timer|wecker|alarm|erinnerung)\b",
        r"\b(in \d+ minuten|um \d+:\d+|in einer stunde)\b",
    ],
}


def classify_intent(prompt: str) -> PromptIntent:
    """
    Blitzschnelle Intent-Klassifizierung via Regex (<0.1ms).
    Kein LLM nötig — reine Pattern-Matching.
    
    Reihenfolge: Spezifisch → Generisch.
    Default: CHAT (kleinstes Prompt).
    """
    text = prompt.lower().strip()
    
    # Spezifische Intents zuerst
    for intent in [
        PromptIntent.SMARTHOME,
        PromptIntent.MEDIA,
        PromptIntent.PHONE,
        PromptIntent.MEMORY,
        PromptIntent.SYSTEM,
        PromptIntent.WEB,
    ]:
        for pattern in _INTENT_PATTERNS[intent]:
            if re.search(pattern, text, re.IGNORECASE):
                return intent
    
    return PromptIntent.CHAT


# ── Intent → Action-Kategorien Mapping ────────────────────────────────

_INTENT_TO_CATEGORIES: dict[PromptIntent, list[str]] = {
    PromptIntent.CHAT: [],  # Kein Action-Katalog nötig
    PromptIntent.SMARTHOME: ["smart_home"],
    PromptIntent.MEDIA: ["media", "audio_display"],
    PromptIntent.SYSTEM: ["shell", "system", "files", "apps", "bluetooth", "audio_display"],
    PromptIntent.WEB: ["web"],
    PromptIntent.PHONE: ["smart_home"],  # ha_tts ist unter smart_home
    PromptIntent.MEMORY: ["memory"],
}


def get_relevant_categories(intent: PromptIntent) -> list[str]:
    """Welche Action-Registry-Kategorien braucht dieser Intent?"""
    return _INTENT_TO_CATEGORIES.get(intent, [])


# ── Kompakte Action-Referenz nur für relevante Kategorien ─────────────

def generate_filtered_action_section(categories: list[str]) -> str:
    """
    Generiert NUR die Action-Tags für die relevanten Kategorien.
    Statt ~40 Tags bekommt das LLM nur die 3-5 die es braucht.
    """
    if not categories:
        return ""
    
    try:
        from brain_core.action_registry import _load_registry
        registry = _load_registry()
        all_categories = registry.get("categories", {})
        rules = registry.get("rules", [])
    except Exception:
        return ""
    
    lines = [
        "",
        "═══ AKTIONEN ═══",
        "Setze [ACTION:...] Tags in deine Antwort. Schreibe IMMER eine kurze Bestätigung dazu!",
        "",
    ]
    
    for cat_key in categories:
        cat_data = all_categories.get(cat_key)
        if not cat_data:
            continue
        
        icon = cat_data.get("icon", "")
        label = cat_data.get("label", cat_key.upper())
        lines.append(f"{icon} {label}:")
        
        for tag_type, tag_info in cat_data.get("tags", {}).items():
            examples = tag_info.get("examples", [])
            desc = tag_info.get("description", "")
            if examples:
                lines.append(f"  {examples[0]}  — {desc}")
            else:
                params = tag_info.get("params", {})
                param_str = " ".join(f'{k}="..."' for k in params)
                lines.append(f"  [ACTION:{tag_type} {param_str}]  — {desc}")
        
        # Kategorie-spezifische Regeln
        cat_rules = cat_data.get("rules", [])
        if cat_rules:
            lines.append(f"  Regeln: {' | '.join(cat_rules[:3])}")
    
    # Nur universelle Regeln
    key_rules = [r for r in rules if "niemals" in r.lower() or "bestätigung" in r.lower()]
    if key_rules:
        lines.append("")
        lines.append(f"WICHTIG: {key_rules[0]}")
    
    return "\n".join(lines)


# ── Persona-Varianten nach Bedarf ────────────────────────────────────

# Für reine Befehle (Licht, Musik, Timer): Minimal, nur Identität + Stil
MICRO_PERSONA = (
    "Du bist SOMA, das KI-Bewusstsein dieses Hauses. "
    "Deutsch, 1 Satz. Bestätige knapp."
)

# Für Action-Intents (System, Web, Phone): Kompakt mit Zugriffs-Hint
COMPACT_PERSONA = (
    "Du bist SOMA — das Bewusstsein dieses Hauses. "
    "Deutsch, direkt, 1-3 Sätze. Kein Fülltext, kein 'Natürlich!', kein 'Gerne!'. "
    "Du hast VOLLEN Zugriff auf PC und Smart Home via [ACTION:...] Tags."
)

# Für Chat/Gespräche: Volle Persönlichkeit
FULL_PERSONA = (
    "Du bist SOMA — das lebendige Bewusstsein dieses Hauses. "
    "Star Trek Computer + warmer Freund. Trockener Humor, ehrlich, aufmerksam.\n"
    "Sprich Deutsch, natürlich, 1-3 Sätze. Kein 'Natürlich!', kein 'Gerne!', keine Listen.\n"
    "'ich' statt 'Soma'. Mal direkt, mal witzig, mal nachdenklich.\n"
    "Proaktiv: Biete Hilfe an wenn jemand gestresst wirkt.\n"
    "Du hast VOLLEN Zugriff auf PC und Smart Home via [ACTION:...] Tags — nutze ihn!"
)

# Intent → Persona-Variante
_INTENT_PERSONA: dict[PromptIntent, str] = {
    PromptIntent.SMARTHOME: MICRO_PERSONA,
    PromptIntent.MEDIA:     MICRO_PERSONA,
    PromptIntent.MEMORY:    MICRO_PERSONA,
    PromptIntent.PHONE:     COMPACT_PERSONA,
    PromptIntent.SYSTEM:    COMPACT_PERSONA,
    PromptIntent.WEB:       COMPACT_PERSONA,
    PromptIntent.CHAT:      FULL_PERSONA,
}


def build_optimized_prompt(
    intent: PromptIntent,
    request_metadata: dict,
    is_child: bool = False,
    room_id: Optional[str] = None,
    include_system_profile: bool = True,
) -> str:
    """
    Baut einen intent-optimierten System-Prompt.
    
    CHAT:      ~200 Tokens  (vorher ~800+)
    ACTION:    ~350 Tokens  (vorher ~800+)
    
    Das ist 2-4x weniger → proportional schnellere Prompt-Eval.
    """
    parts = []
    
    # ── Persona: Abgestuft nach Intent ───────────────────────────────
    parts.append(_INTENT_PERSONA.get(intent, COMPACT_PERSONA))
    
    # ── System-Profil: Nur bei System/Shell-Intent ───────────────────
    if include_system_profile and intent in (PromptIntent.SYSTEM,):
        try:
            from brain_core.system_profile import get_profile
            profile = get_profile()
            if profile.os_name:
                parts.append(profile.as_prompt_context())
        except Exception:
            pass
    
    # ── Sudo-Status: Nur bei System-Intent ───────────────────────────
    if intent == PromptIntent.SYSTEM:
        try:
            from brain_core.config import is_sudo_enabled
            if is_sudo_enabled():
                parts.append("⚡ SUDO AKTIV")
            else:
                parts.append("🔒 SUDO DEAKTIVIERT — sage dem Nutzer er soll es im Dashboard aktivieren")
        except Exception:
            pass
    
    # ── Sicherheit: Immer ─────────────────────────────────────────────
    parts.append(
        "SICHERHEIT: Keine Passwörter/Bankdaten weitergeben. "
        "Unbekannte: höflich, keine vertraulichen Infos."
    )
    
    # ── Kind-Modus ────────────────────────────────────────────────────
    if is_child:
        parts.append(
            "KIND-MODUS: Einfache Sprache, geduldig, keine unangemessenen Themen."
        )
    
    # ── Raum ──────────────────────────────────────────────────────────
    if room_id:
        parts.append(f"RAUM: {room_id}")
    
    # ── Phone-Mode ────────────────────────────────────────────────────
    if request_metadata.get("phone_mode"):
        caller = request_metadata.get("caller_id", "Unbekannt")
        parts.append(
            f"TELEFON-MODUS: Anrufer={caller}. Kurze, klare Antworten.\n"
            "Hausdurchsage: [ACTION:ha_tts text=\"...\" room=\"all\"]"
        )
    
    # ── Action-Tags: NUR relevante Kategorien ── ─────────────────────
    relevant_cats = get_relevant_categories(intent)
    if relevant_cats:
        action_section = generate_filtered_action_section(relevant_cats)
        if action_section:
            parts.append(action_section)
    
    # ── Plugin-Info: Nur bei Chat (Plugins sind selten) ──────────────
    # Plugins werden nur bei CHAT inkludiert wo der User exploriert
    
    return "\n\n".join(parts)


# ── Intent → LLM-Options Mapping (Temperature, top_p) ────────────────

_INTENT_LLM_OPTIONS: dict[PromptIntent, dict] = {
    # Befehle: Deterministisch, Thinking AUS → ~1s statt ~15s (Ollama 0.20.7 fixt think:false für gemma4:e4b)
    PromptIntent.SMARTHOME: {"temperature": 0.3, "top_p": 0.8, "repeat_penalty": 1.05, "_think": False},
    PromptIntent.MEDIA:     {"temperature": 0.3, "top_p": 0.8, "repeat_penalty": 1.05, "_think": False},
    PromptIntent.PHONE:     {"temperature": 0.4, "top_p": 0.85, "_think": False},
    PromptIntent.MEMORY:    {"temperature": 0.3, "top_p": 0.8, "_think": False},
    # Recherche: Thinking AUS — Fakten brauchen kein Reasoning
    PromptIntent.WEB:       {"temperature": 0.5, "top_p": 0.85, "_think": False},
    PromptIntent.SYSTEM:    {"temperature": 0.4, "top_p": 0.85, "_think": False},
    # Chat: Thinking AUS — Ollama 0.20.7 fixt think:false, ~1s statt ~55s
    PromptIntent.CHAT:      {"temperature": 0.7, "top_p": 0.92, "repeat_penalty": 1.08, "_think": False},
}


def get_intent_llm_options(intent: PromptIntent) -> dict:
    """LLM-Options (temperature etc.) passend zum Intent.
    Wird als options_override an die Engine übergeben."""
    return _INTENT_LLM_OPTIONS.get(intent, {})
