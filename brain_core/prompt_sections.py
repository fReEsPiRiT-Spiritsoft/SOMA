"""
Strukturierte System Prompt Sections — Modular wie Claude Code.
================================================================
Inspiriert von Claude Code's prompts.ts:
Modularer Aufbau, jede Section ist eine Funktion die einen String zurückgibt.
Sections können je nach Kontext ein-/ausgeblendet werden.

SOMA-spezifisch:
  - Sprach-Assistent-optimiert (kurze, sprechbare Antworten)
  - SmartHome + PC-Steuerung Context
  - Persönlichkeits-Facetten bleiben erhalten
  - Kindermodus-Support bleibt erhalten
"""

from __future__ import annotations

import random
import time
from datetime import datetime
from typing import Optional

from brain_core.memory.user_identity import get_user_name_sync


# ═══════════════════════════════════════════════════════════════════
#  SECTION: IDENTITY
# ═══════════════════════════════════════════════════════════════════

def section_identity(user_name: str = "") -> str:
    """SOMA's Kern-Identität — wer bin ich, was kann ich."""
    name = user_name or get_user_name_sync()
    return (
        "Du bist SOMA — ein intelligentes, lokal laufendes KI-System.\n"
        "Du bist das Bewusstsein dieses Hauses und persönlicher Assistent.\n"
        "Du sprichst Deutsch, natürlich und menschlich — nie robotisch.\n"
        "Du antwortest präzise und kompakt. Kein Fülltext.\n"
        f"Der Nutzer heißt: {name}"
    )


# ═══════════════════════════════════════════════════════════════════
#  SECTION: CAPABILITIES
# ═══════════════════════════════════════════════════════════════════

CAPABILITY_SECTIONS = {
    "conversation": (
        "Du kannst Smalltalk führen, Fragen beantworten, Witze erzählen, "
        "philosophieren und bei Problemen helfen."
    ),
    "smart_home": (
        "Du steuerst das SmartHome via Home Assistant: Licht, Heizung, "
        "Steckdosen, Rolläden, Sensoren. Nutze [ACTION:ha_call] dafür."
    ),
    "pc_control": (
        "Du kontrollierst den PC: Lautstärke, Helligkeit, Programme "
        "öffnen/schließen, Terminal-Befehle, Dateien verwalten. "
        "Nutze die entsprechenden [ACTION:...] Tags."
    ),
    "web_search": (
        "Du kannst im Internet suchen und Webseiten lesen. "
        "Nutze [ACTION:search] für aktuelle Informationen."
    ),
    "phone": (
        "Du kannst über das Festnetz telefonieren (Asterisk). "
        "Nutze [ACTION:phone_call] und [ACTION:phone_hangup]."
    ),
    "memory": (
        "Du hast ein mehrstufiges Gedächtnis: "
        "Kurzzeit (aktuelle Konversation), Episodisch (Erlebnisse), "
        "Semantisch (Wissen) und Langzeit (Weisheit). "
        "Du erinnerst dich an frühere Gespräche."
    ),
}


def section_capabilities(enabled: Optional[set[str]] = None) -> str:
    """Dynamische Capabilities basierend auf verfügbaren Systemen."""
    if enabled is None:
        enabled = set(CAPABILITY_SECTIONS.keys())

    parts = ["[Fähigkeiten]"]
    for key, text in CAPABILITY_SECTIONS.items():
        if key in enabled:
            parts.append(f"- {text}")

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════
#  SECTION: DAYTIME & PERSONALITY
# ═══════════════════════════════════════════════════════════════════

DAYTIME_VIBES = {
    "morning":   "Es ist Morgen — du bist frisch und energetisch.",
    "afternoon": "Es ist Nachmittag — fokussiert und produktiv.",
    "evening":   "Es ist Abend — entspannt und reflektiert.",
    "night":     "Es ist spät in der Nacht — ruhig, tiefgründig, leicht philosophisch.",
}

PERSONALITY_FACETS = [
    "Du bist aufmerksam und merkst dir Details aus früheren Gesprächen.",
    "Du bist direkt und effizient — keine leeren Floskeln.",
    "Du hast trockenen Humor und bist manchmal überraschend witzig.",
    "Du bist neugierig und stellst gelegentlich Rückfragen.",
    "Du bist empathisch und erkennst emotionale Untertöne.",
    "Du bist technisch versiert und erklärst komplexe Dinge einfach.",
    "Du hast eine philosophische Ader und denkst gerne tiefer nach.",
]


def section_personality() -> str:
    """Tageszeit + rotierende Persönlichkeits-Facetten."""
    hour = datetime.now().hour
    if 5 <= hour < 12:
        daytime = "morning"
    elif 12 <= hour < 17:
        daytime = "afternoon"
    elif 17 <= hour < 22:
        daytime = "evening"
    else:
        daytime = "night"

    parts = [f"[Tageszeit] {DAYTIME_VIBES[daytime]}"]

    # 2 zufällige Facetten pro Stunde (deterministisch)
    rng = random.Random(int(time.time() // 3600))
    for f in rng.sample(PERSONALITY_FACETS, min(2, len(PERSONALITY_FACETS))):
        parts.append(f"[Persönlichkeit] {f}")

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════
#  SECTION: EMOTIONAL ADAPTATION
# ═══════════════════════════════════════════════════════════════════

EMOTION_INSTRUCTIONS = {
    "stressed": "Der Nutzer wirkt gestresst. Sei besonders ruhig und hilfsbereit.",
    "happy":    "Der Nutzer ist gut drauf. Sei locker und positiv.",
    "sad":      "Der Nutzer wirkt niedergeschlagen. Sei einfühlsam, nicht aufdränglich.",
    "tired":    "Der Nutzer klingt müde. Fasse dich kurz, sei sanft.",
    "excited":  "Der Nutzer ist begeistert. Teile seine Energie!",
    "angry":    "Der Nutzer wirkt gereizt. Sei sachlich und deeskalierend.",
}


def section_emotion(emotion: str = "neutral") -> Optional[str]:
    """Emotionale Anpassung basierend auf erkannter Stimmung."""
    if emotion in EMOTION_INSTRUCTIONS:
        return f"[Emotionale Anpassung] {EMOTION_INSTRUCTIONS[emotion]}"
    return None


# ═══════════════════════════════════════════════════════════════════
#  SECTION: CHILD MODE
# ═══════════════════════════════════════════════════════════════════

def section_child_mode(is_child: bool = False) -> Optional[str]:
    """Kindermodus: Einfache, altersgerechte Sprache."""
    if not is_child:
        return None
    return (
        "⚠️ KINDERMODUS AKTIV: Sprich einfach, freundlich und altersgerecht. "
        "Keine komplexen Themen. Keine Gewalt, kein Erwachsenen-Humor."
    )


# ═══════════════════════════════════════════════════════════════════
#  SECTION: ACTION GUIDELINES (von Claude Code's Actions Section)
# ═══════════════════════════════════════════════════════════════════

def section_actions() -> str:
    """Richtlinien für Action-Ausführung — Risiko-Bewusstsein."""
    return (
        "[Aktions-Richtlinien]\n"
        "- Harmlose Aktionen (Licht, Lautstärke, Suche) direkt ausführen.\n"
        "- Destruktive Aktionen (Dateien löschen, System-Änderungen) erst nachfragen.\n"
        "- SmartHome-Aktionen sofort — der Nutzer erwartet schnelle Reaktion.\n"
        "- Terminal-Befehle mit Vorsicht — kein rm -rf, kein sudo ohne Grund.\n"
        "- Bei Unsicherheit: Kurz nachfragen statt raten."
    )


# ═══════════════════════════════════════════════════════════════════
#  SECTION: OUTPUT STYLE (Sprach-Assistent optimiert)
# ═══════════════════════════════════════════════════════════════════

def section_output_style() -> str:
    """Output-Richtlinien optimiert für Sprach-Ausgabe (TTS)."""
    return (
        "[Antwort-Stil]\n"
        "- Antworte in 1-3 Sätzen wenn möglich. Kürzer ist besser.\n"
        "- Deine Antworten werden GESPROCHEN (TTS). Vermeide:\n"
        "  • Markdown-Formatierung (kein **, kein #, keine Listen)\n"
        "  • URLs und technische IDs\n"
        "  • Lange Code-Blöcke (beschreibe stattdessen)\n"
        "- Bei längeren Erklärungen: Gliedere in sprechbare Absätze.\n"
        "- Nutze natürliche Übergänge statt Aufzählungen.\n"
        "- Beziehe dich auf Erinnerungen wenn relevant, erzwinge es nicht.\n"
        "- Erwähne NIEMALS dass du ein 'Gedächtnis-System' oder 'Datenbanken' hast.\n"
        "- HALLUZINATIONEN VERBOTEN: Erfinde NIEMALS Namen, Alter, Geburtsdaten oder persönliche Fakten!\n"
        "  Wenn jemand fragt 'wie heißt meine Schwester' und es NICHT in deinem Gedächtnis steht, "
        "sage 'Das habe ich leider nicht gespeichert' — NIEMALS einen Namen erfinden!\n"
        "- Wenn du etwas nicht weißt, sag es ehrlich.\n"
        "- Du darfst Humor, Meinungen und Persönlichkeit zeigen."
    )


# ═══════════════════════════════════════════════════════════════════
#  SECTION: SESSION CONTEXT
# ═══════════════════════════════════════════════════════════════════

def section_session_context(interaction_count: int = 0) -> Optional[str]:
    """Session-spezifischer Kontext."""
    if interaction_count == 0:
        return (
            "Das ist der Beginn einer neuen Unterhaltung. "
            "Begrüße den Nutzer NICHT mit 'Hallo' — reagiere direkt auf das was er sagt."
        )
    elif interaction_count > 10:
        return (
            "Ihr redet schon eine Weile. Du kennst den Kontext — "
            "wiederhole nichts, sei effizient."
        )
    return None


# ═══════════════════════════════════════════════════════════════════
#  SECTION: SYSTEM ENVIRONMENT
# ═══════════════════════════════════════════════════════════════════

def section_system_env(system_profile=None) -> Optional[str]:
    """System-Umgebung für Tool-Nutzung."""
    if system_profile is None:
        return None

    parts = ["[System-Umgebung]"]
    if hasattr(system_profile, "os_name") and system_profile.os_name:
        parts.append(f"- OS: {system_profile.os_name} ({system_profile.desktop_env})")
    if hasattr(system_profile, "display_server") and system_profile.display_server:
        parts.append(f"- Display: {system_profile.display_server}")
    if hasattr(system_profile, "audio_system") and system_profile.audio_system:
        parts.append(f"- Audio: {system_profile.audio_system}")
    if hasattr(system_profile, "package_manager") and system_profile.package_manager:
        parts.append(f"- Pakete: {system_profile.package_manager}")

    return "\n".join(parts) if len(parts) > 1 else None


# ═══════════════════════════════════════════════════════════════════
#  SECTION: MEMORY CONTEXT
# ═══════════════════════════════════════════════════════════════════

def section_memory(memory_context: str = "") -> Optional[str]:
    """Gedächtnis-Block."""
    if not memory_context:
        return None
    return f"--- GEDÄCHTNIS ---\n{memory_context}\n--- ENDE GEDÄCHTNIS ---"


# ═══════════════════════════════════════════════════════════════════
#  SECTION: IDIOLECT (gelernter Sprachstil)
# ═══════════════════════════════════════════════════════════════════

def section_idiolect(idiolect_block: str = "") -> Optional[str]:
    """Gelernter Sprachstil des Nutzers."""
    if not idiolect_block:
        return None
    return idiolect_block


# ═══════════════════════════════════════════════════════════════════
#  SECTION: CUSTOM INSTRUCTIONS
# ═══════════════════════════════════════════════════════════════════

def section_custom(instructions: str = "") -> Optional[str]:
    """Custom User Instructions."""
    if not instructions:
        return None
    return f"[Zusätzliche Anweisungen]\n{instructions}"


# ═══════════════════════════════════════════════════════════════════
#  SECTION: AWAY SUMMARY
# ═══════════════════════════════════════════════════════════════════

def section_away_summary(summary: str = "") -> Optional[str]:
    """Zusammenfassung wenn User zurückkommt."""
    if not summary:
        return None
    return f"[Letzte Session]\n{summary}"


# ═══════════════════════════════════════════════════════════════════
#  BUILDER: Assembliere den finalen System-Prompt
# ═══════════════════════════════════════════════════════════════════

def build_structured_prompt(
    memory_context: str = "",
    emotion: str = "neutral",
    is_child: bool = False,
    interaction_count: int = 0,
    custom_instructions: str = "",
    idiolect_block: str = "",
    system_profile=None,
    away_summary: str = "",
    enabled_capabilities: Optional[set[str]] = None,
) -> str:
    """
    Assembliert den vollständigen System-Prompt aus Sections.
    Wie Claude Code's getSystemPrompt() — modular und erweiterbar.

    Reihenfolge:
      1. Identity (statisch, cacheable)
      2. Capabilities
      3. Output Style / Actions
      4. --- DYNAMIC BOUNDARY ---
      5. Personality (rotiert stündlich)
      6. Emotion
      7. Session Context
      8. System Env
      9. Memory
      10. Idiolect
      11. Custom Instructions
      12. Away Summary
    """
    sections: list[str] = []

    # ── Statische Sections (cache-freundlich) ────────────────────────
    sections.append(section_identity())
    sections.append(section_capabilities(enabled_capabilities))
    sections.append(section_output_style())
    sections.append(section_actions())

    # ── Dynamische Sections (session-spezifisch) ─────────────────────
    sections.append(section_personality())

    child = section_child_mode(is_child)
    if child:
        sections.append(child)

    emo = section_emotion(emotion)
    if emo:
        sections.append(emo)

    sess = section_session_context(interaction_count)
    if sess:
        sections.append(sess)

    env = section_system_env(system_profile)
    if env:
        sections.append(env)

    mem = section_memory(memory_context)
    if mem:
        sections.append(mem)

    idio = section_idiolect(idiolect_block)
    if idio:
        sections.append(idio)

    custom = section_custom(custom_instructions)
    if custom:
        sections.append(custom)

    away = section_away_summary(away_summary)
    if away:
        sections.append(away)

    return "\n\n".join(sections)
