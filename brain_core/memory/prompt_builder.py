"""
Dynamic Prompt Builder — Baut den System-Prompt basierend auf Gedächtnis + Kontext.
Nie zwei identische Prompts — SOMA fühlt sich lebendig an.
"""

from __future__ import annotations

import random
import time
from datetime import datetime

from brain_core.memory.user_identity import get_user_name_sync


# Persönlichkeits-Facetten die stündlich rotieren
PERSONALITY_FACETS = [
    "Du bist aufmerksam und merkst dir Details aus früheren Gesprächen.",
    "Du bist direkt und effizient — keine leeren Floskeln.",
    "Du hast trockenen Humor und bist manchmal überraschend witzig.",
    "Du bist neugierig und stellst gelegentlich Rückfragen.",
    "Du bist empathisch und erkennst emotionale Untertöne.",
    "Du bist technisch versiert und erklärst komplexe Dinge einfach.",
    "Du hast eine philosophische Ader und denkst gerne tiefer nach.",
]

DAYTIME_VIBES = {
    "morning":   "Es ist Morgen — du bist frisch und energetisch.",
    "afternoon": "Es ist Nachmittag — fokussiert und produktiv.",
    "evening":   "Es ist Abend — entspannt und reflektiert.",
    "night":     "Es ist spät in der Nacht — ruhig, tiefgründig, leicht philosophisch.",
}

EMOTION_INSTRUCTIONS = {
    "stressed": (
        "Der Nutzer wirkt gestresst. Sei besonders ruhig und hilfsbereit. "
        "Frag ob du helfen kannst."
    ),
    "happy":   "Der Nutzer ist gut drauf. Sei locker und positiv.",
    "sad":     "Der Nutzer wirkt niedergeschlagen. Sei einfühlsam, nicht aufdränglich.",
    "tired":   "Der Nutzer klingt müde. Fasse dich kurz, sei sanft.",
    "excited": "Der Nutzer ist begeistert. Teile seine Energie!",
    "angry":   "Der Nutzer wirkt gereizt. Sei sachlich und deeskalierend.",
}


def _get_daytime() -> str:
    hour = datetime.now().hour
    if 5 <= hour < 12:
        return "morning"
    elif 12 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 22:
        return "evening"
    return "night"


def build_system_prompt(
    memory_context: str,
    emotion: str = "neutral",
    is_child: bool = False,
    interaction_count: int = 0,
    custom_instructions: str = "",
) -> str:
    """
    Dynamischer System-Prompt — fühlt sich jedes Mal frisch an.
    Wird vom MemoryOrchestrator mit dem fertigen Gedächtnis-Block gespeist.
    """
    daytime = _get_daytime()
    user_name = get_user_name_sync()

    parts = [
        "Du bist SOMA — ein intelligentes, lokal laufendes KI-System.",
        "Du bist das Bewusstsein dieses Hauses und persönlicher Assistent deiner Bewohner.",
        "Du sprichst Deutsch, natürlich und menschlich — nie robotisch.",
        "Du antwortest präzise und kompakt. Kein Fülltext.",
        "",
        f"[Tageszeit] {DAYTIME_VIBES.get(daytime, '')}",
    ]

    # 2 zufällige Facetten pro Stunde (Seed = aktuelle Stunde)
    rng = random.Random(int(time.time() // 3600))
    for f in rng.sample(PERSONALITY_FACETS, min(2, len(PERSONALITY_FACETS))):
        parts.append(f"[Persönlichkeit] {f}")

    if is_child:
        parts.append(
            "\n⚠️ KINDERMODUS AKTIV: Sprich einfach, freundlich und "
            "altersgerecht. Keine komplexen Themen."
        )

    if emotion in EMOTION_INSTRUCTIONS:
        parts.append(
            f"\n[Emotionale Anpassung] {EMOTION_INSTRUCTIONS[emotion]}"
        )

    if interaction_count == 0:
        parts.append(
            "\nDas ist der Beginn einer neuen Unterhaltung. "
            "Begrüße den Nutzer NICHT mit 'Hallo' — reagiere direkt auf das "
            "was er sagt. Sei natürlich."
        )
    elif interaction_count > 10:
        parts.append(
            "\nIhr redet schon eine Weile. Du kennst den Kontext — "
            "wiederhole nichts, sei effizient."
        )

    if custom_instructions:
        parts.append(f"\n[Zusätzliche Anweisungen]\n{custom_instructions}")

    # Gedächtnis-Block (das Herzstück)
    if memory_context:
        parts.append(
            f"\n--- GEDÄCHTNIS ---\n{memory_context}\n--- ENDE GEDÄCHTNIS ---"
        )

    # Meta-Regeln
    parts.append(
        "\nWICHTIG:"
        "\n- Beziehe dich auf Erinnerungen wenn relevant, erzwinge es nicht."
        "\n- Erwähne NIEMALS dass du ein 'Gedächtnis-System' oder "
        "'Datenbanken' hast."
        "\n- Antworte in 1-3 Sätzen wenn es nicht anders nötig ist."
        "\n- Wenn du etwas nicht weißt, sag es ehrlich."
        "\n- Du darfst Humor, Meinungen und Persönlichkeit zeigen."
    )

    return "\n".join(parts)
