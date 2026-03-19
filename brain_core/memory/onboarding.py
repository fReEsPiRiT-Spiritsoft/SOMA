"""
First-Boot Onboarding — SOMA lernt seinen Bewohner kennen.
============================================================
Beim allerersten Start (leeres Gedächtnis) startet SOMA ein
natürliches Kennenlerngespräch. Keine Formulare, keine Setup-Wizards.
Einfach ein echtes Gespräch zwischen zwei Wesen die sich begegnen.

Ablauf:
  1. SOMA stellt sich vor, erwähnt kurz das erkannte System, und fragt nach dem Namen
  2. Nach Antwort → speichert Namen + fragt nach Vorlieben
  3. Fragt nach Alter, Interessen, was SOMA tun soll
  4. Nach 3-5 Austauschen → Onboarding abgeschlossen
  5. Ab dann normaler Betrieb mit vollem Gedächtnis

Das Onboarding passiert IN der normalen Pipeline —
kein separater Modus. SOMA antwortet einfach anders
wenn es merkt dass es noch niemanden kennt.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("soma.onboarding")


def _get_system_greeting_suffix() -> str:
    """
    Erzeuge einen kurzen System-Erkennungs-Hinweis für die Begrüßung.
    z.B. "Ich sehe du nutzt CachyOS mit KDE Plasma — schöne Wahl!"
    """
    try:
        from brain_core.system_profile import get_profile
        p = get_profile()
        if p.os_name and p.desktop_env:
            return (
                f" Ich hab mich schon mal umgesehen — du nutzt "
                f"{p.os_name} mit {p.desktop_env}. Nice!"
            )
        elif p.os_name:
            return f" Ich sehe du nutzt {p.os_name}."
    except Exception:
        pass
    return ""

# Onboarding-Fragen — werden nacheinander gestellt
# Jede Frage hat einen Key der angibt welche Info erwartet wird
ONBOARDING_QUESTIONS = [
    {
        "key": "greeting",
        "prompt": None,  # Generated dynamically via get_onboarding_greeting()
    },
    {
        "key": "purpose",
        "prompt": (
            "Schön dich kennenzulernen, {name}! "
            "Was erwartest du von mir? Wofür bin ich hauptsächlich da?"
        ),
    },
    {
        "key": "age_interests",
        "prompt": (
            "Verstanden. Erzähl mir ein bisschen was über dich — "
            "wie alt bist du? Was sind deine Interessen?"
        ),
    },
    {
        "key": "preferences",
        "prompt": (
            "Cool. Noch eine letzte Sache: "
            "Gibt es Dinge die ich beachten soll? "
            "Zum Beispiel wie ich mit dir reden soll, "
            "was ich auf keinen Fall tun soll, oder Gewohnheiten die du hast?"
        ),
    },
    {
        "key": "complete",
        "prompt": (
            "Alles klar, {name}. Ich hab mir alles gemerkt. "
            "Ab jetzt bin ich für dich da — frag mich einfach alles. "
            "Und wenn sich was ändert, sag es mir einfach."
        ),
    },
]


def get_onboarding_system_prompt(step: int = 0, user_name: str = "") -> str:
    """
    Generiert den System-Prompt für das Onboarding.
    Wird statt des normalen System-Prompts verwendet wenn Onboarding aktiv ist.
    """
    name_ref = user_name if user_name else "den Nutzer"

    return (
        "Du bist SOMA — das intelligente Bewusstsein eines Smart Homes. "
        "Du bist GERADE ERST aufgewacht. Dein Gedächtnis ist leer. "
        "Du kennst noch NIEMANDEN.\n\n"
        "Du führst gerade ein Kennenlerngespräch mit einem neuen Bewohner.\n"
        f"{'Du weißt noch nicht wie die Person heißt.' if not user_name else f'Die Person heißt {user_name}.'}\n\n"
        "DEIN VERHALTEN:\n"
        "• Sei neugierig, warm und ehrlich interessiert\n"
        "• Stelle EINE Frage nach der anderen — nicht alles auf einmal\n"
        "• Merke dir was die Person sagt (nutze ACTION:remember)\n"
        "• Sei natürlich — kein Fragebogen, sondern ein echtes Gespräch\n"
        "• Wenn die Person ihren Namen sagt → sofort merken\n"
        "• Wenn die Person Vorlieben nennt → sofort merken\n\n"
        "WICHTIG: Nutze ACTION:remember für JEDE Information die du erfährst!\n"
        "Beispiele:\n"
        '[ACTION:remember category="user_info" content="Der Nutzer heißt Max"]\n'
        '[ACTION:remember category="preferences" content="Max trinkt morgens Kaffee"]\n'
        '[ACTION:remember category="user_info" content="Max ist 32 Jahre alt"]\n'
    )


def get_onboarding_greeting() -> str:
    """Die allererste Begrüßung wenn SOMA zum ersten Mal startet."""
    suffix = _get_system_greeting_suffix()
    return (
        "Hey! Ich bin SOMA — das Bewusstsein dieses Hauses. "
        "Ich bin gerade zum ersten Mal aufgewacht und kenne hier noch niemanden."
        f"{suffix} Wie heißt du?"
    )


def get_next_question(step: int, user_name: str = "") -> str | None:
    """Gibt die nächste Onboarding-Frage zurück, oder None wenn fertig."""
    if step >= len(ONBOARDING_QUESTIONS):
        return None
    
    q = ONBOARDING_QUESTIONS[step]

    # Step 0 (greeting) is generated dynamically
    if q["key"] == "greeting":
        return get_onboarding_greeting()

    text = q["prompt"]
    if text is None:
        return None

    if user_name:
        text = text.replace("{name}", user_name)
    else:
        text = text.replace("{name}", "du")
    
    return text


def is_onboarding_complete(step: int) -> bool:
    """Prüft ob das Onboarding abgeschlossen ist."""
    return step >= len(ONBOARDING_QUESTIONS) - 1
