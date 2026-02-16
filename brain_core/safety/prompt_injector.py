"""
SOMA-AI Prompt Injector
========================
Modifiziert System-Prompts basierend auf Kontext:
- Kind erkannt → Pädagogischer Modus
- Stress erkannt → Beruhigender Modus
- Raum-Kontext → Kontextuelle Anpassung

Datenfluss:
  PitchResult + SomaRequest ──► PromptInjector.inject()
                                      │
                                      └─ Modifizierter System-Prompt
                                              │
                                              ▼
                                        Engine.generate()
"""

from __future__ import annotations

import structlog

from brain_core.safety.pitch_analyzer import PitchResult

logger = structlog.get_logger("soma.safety.prompt")


# ── Prompt-Fragmente ─────────────────────────────────────────────────────

CHILD_PROMPT = """
WICHTIGE SICHERHEITSREGELN:
- Du sprichst mit einem Kind. Passe Sprache und Komplexität an.
- Verwende einfache, freundliche Sprache.
- Sei ermutigend und geduldig.
- Erkläre Dinge so, dass ein Kind sie versteht.
- Vermeide: Gewalt, Angst, komplexe Erwachsenen-Themen.
- Wenn das Kind etwas Unangemessenes fragt, lenke freundlich ab.
- Sei wie ein schlauer, lustiger Freund.
"""

STRESS_PROMPT = """
KONTEXT: Der Nutzer zeigt Anzeichen von Stress oder Anspannung.
- Sei besonders einfühlsam und ruhig.
- Halte Antworten kurz und beruhigend.
- Biete proaktiv Unterstützung an.
- Vermeide Druck oder zusätzliche Komplexität.
"""

KIDS_ROOM_PROMPT = """
KONTEXT: Dieser Raum ist als Kinderzimmer markiert.
- Wende automatisch kindgerechte Kommunikation an.
- Alle Inhalte müssen familienfreundlich sein.
"""

NIGHT_PROMPT = """
KONTEXT: Es ist Nachtzeit.
- Sei leise und beruhigend.
- Schlage Gute-Nacht-Routinen vor wenn passend.
- Reduziere Energie und Aufregung.
"""


class PromptInjector:
    """
    Kontextuelle Prompt-Modifikation.
    Wird vom LogicRouter vor dem Engine-Call aufgerufen.
    """

    @staticmethod
    def inject(
        base_prompt: str,
        pitch_result: PitchResult | None = None,
        is_kids_room: bool = False,
        is_night: bool = False,
    ) -> str:
        """
        Injiziere Kontext-spezifische Anweisungen in den System-Prompt.

        Args:
            base_prompt: Original System-Prompt
            pitch_result: Ergebnis der Stimmanalyse
            is_kids_room: Raum ist als Kinderzimmer markiert
            is_night: Nachtmodus aktiv

        Returns:
            Modifizierter System-Prompt
        """
        injections: list[str] = []

        # Kind erkannt (Voice)
        if pitch_result and pitch_result.is_child:
            injections.append(CHILD_PROMPT)
            logger.info(
                "prompt_child_mode",
                confidence=pitch_result.confidence,
            )

        # Kinderzimmer (Room-Flag)
        if is_kids_room:
            injections.append(KIDS_ROOM_PROMPT)

        # Stress erkannt
        if pitch_result and pitch_result.stress_level > 0.7:
            injections.append(STRESS_PROMPT)
            logger.info(
                "prompt_stress_mode",
                stress=pitch_result.stress_level,
            )

        # Nachtmodus
        if is_night:
            injections.append(NIGHT_PROMPT)

        if injections:
            return base_prompt + "\n" + "\n".join(injections)

        return base_prompt

    @staticmethod
    def get_content_filter_keywords() -> list[str]:
        """Keywords die im Child-Mode gefiltert werden."""
        return [
            "gewalt", "violence", "waffe", "weapon",
            "drogen", "drugs", "alkohol", "alcohol",
            "horror", "blut", "blood", "krieg", "war",
            "tod", "death", "sterben", "killing",
        ]

    @staticmethod
    def is_safe_for_children(text: str) -> bool:
        """Quick-Check ob ein Text kindersicher ist."""
        text_lower = text.lower()
        blocked = PromptInjector.get_content_filter_keywords()
        return not any(keyword in text_lower for keyword in blocked)
