"""
SOMA-AI Ambient Intelligence — Proaktives Eingreifen
=====================================================
Das "Gewissen" von Soma. Entscheidet WANN und WIE Soma
aus eigener Initiative spricht — ohne angesprochen zu werden.

Szenarien:
  • Streit erkannt → "Euer Streit ist unproduktiv. Atmet kurz durch."
  • Stress seit 5 Min → "Du wirkst gestresst. Soll ich helfen?"
  • Traurigkeit → "Hey, alles okay bei dir? Ich bin hier."
  • Gute Laune → Soma ist lockerer, macht Witze
  • Nachtzeit + wach → "Es ist spät, schlaf genug ist wichtig."
  • Morgens → Proaktiver Tagesbriefing
  • Kind allein erkannt → Pädagogischer Modus

Soma ist wie ein aufmerksamer Mitbewohner:
  — Hört immer zu
  — Sagt nur etwas wenn es SINN macht
  — Drängt sich nicht auf
  — Aber greift ein wenn nötig
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import structlog

from brain_core.voice.emotion import (
    EmotionEngine,
    EmotionState,
    RoomAtmosphere,
    RoomMood,
)

logger = structlog.get_logger("soma.voice.ambient")


class InterventionType(str, Enum):
    """Art der proaktiven Intervention."""
    ARGUMENT_DEESCALATION = "argument"
    STRESS_SUPPORT = "stress"
    SADNESS_COMFORT = "sadness"
    HEALTH_REMINDER = "health"
    GOOD_MOOD_BANTER = "banter"
    TIME_AWARENESS = "time"
    CHILD_SAFETY = "child"
    MORNING_BRIEFING = "briefing"
    SILENCE_CHECK = "silence_check"


@dataclass
class Intervention:
    """Eine geplante proaktive Intervention."""
    type: InterventionType
    prompt: str                   # An Llama 3 gesendeter Prompt
    priority: int = 5            # 1 (highest) - 10 (lowest)
    cooldown_sec: float = 120.0  # Min. Sekunden zwischen gleichen Interventionen
    emotion_context: str = ""    # Emotionaler Kontext für natürlichere Antwort
    use_calm_voice: bool = False # TTS-Emotion: ruhig statt normal
    max_words: int = 40          # Kurz und prägnant


class AmbientIntelligence:
    """
    Proaktive Intelligenz — das "Einschreiten" von Soma.
    Prüft in regelmäßigen Abständen ob eine Intervention sinnvoll ist.
    """

    # Cooldowns in Sekunden — Soma soll nicht nerven
    COOLDOWNS = {
        InterventionType.ARGUMENT_DEESCALATION: 180,   # 3 Min
        InterventionType.STRESS_SUPPORT: 300,           # 5 Min
        InterventionType.SADNESS_COMFORT: 600,          # 10 Min
        InterventionType.HEALTH_REMINDER: 1800,         # 30 Min
        InterventionType.GOOD_MOOD_BANTER: 900,         # 15 Min
        InterventionType.TIME_AWARENESS: 3600,          # 1 Std
        InterventionType.CHILD_SAFETY: 60,              # 1 Min (wichtig)
        InterventionType.MORNING_BRIEFING: 43200,       # 12 Std
        InterventionType.SILENCE_CHECK: 1800,           # 30 Min
    }

    def __init__(self, emotion_engine: EmotionEngine):
        self._emotion = emotion_engine
        self._last_interventions: dict[InterventionType, float] = {}
        self._intervention_count = 0
        self._enabled = True

        # Tracking
        self._continuous_sadness_start: Optional[float] = None
        self._user_said_stop = False  # "Soma, halt die Klappe"

    def check(self, current_hour: Optional[int] = None) -> Optional[Intervention]:
        """
        Prüfe ob eine Intervention angebracht ist.
        Wird nach JEDEM analysierten Sprach-Segment aufgerufen.

        Returns:
            Intervention wenn Soma eingreifen soll, sonst None.
        """
        if not self._enabled or self._user_said_stop:
            return None

        atm = self._emotion.atmosphere

        # ── Prio 1: Streit ───────────────────────────────────────────
        if atm.mood == RoomMood.ARGUMENT and atm.argument_likelihood > 0.6:
            return self._maybe_intervene(
                InterventionType.ARGUMENT_DEESCALATION,
                prompt=(
                    "Du bist Soma, das Smart-Home-KI-System. "
                    "Du hast gerade einen Streit zwischen den Bewohnern erkannt. "
                    "Die Stimmung ist angespannt und unproduktiv. "
                    "Sage etwas Deeskalierendes, Kurzes und Direktes. "
                    "Kein Predigen, kein Bevormunden — eher wie ein ruhiger Freund. "
                    "Beispiel-Tonfall: 'Hey, kurz durchatmen. Sowas bringt euch nicht weiter.' "
                    "Antworte in 1-2 Sätzen auf Deutsch."
                ),
                priority=1,
                use_calm_voice=True,
                max_words=30,
                emotion_context=self._emotion.get_context_for_llm(),
            )

        # ── Prio 2: Hoher Stress ─────────────────────────────────────
        if atm.avg_stress > 0.7 and atm.trend != "improving":
            return self._maybe_intervene(
                InterventionType.STRESS_SUPPORT,
                prompt=(
                    "Du bist Soma. Der Bewohner wirkt seit einiger Zeit gestresst "
                    f"(Stress-Level: {atm.avg_stress:.0%}, Trend: {atm.trend}). "
                    "Biete subtil Unterstützung an. Nicht aufdringlich. "
                    "Beispiel: 'Du wirkst angespannt. Soll ich Musik anmachen oder das Licht dimmen?' "
                    "1-2 Sätze, Deutsch, einfühlsam."
                ),
                priority=2,
                use_calm_voice=True,
                max_words=35,
                emotion_context=self._emotion.get_context_for_llm(),
            )

        # ── Prio 3: Anhaltende Traurigkeit ───────────────────────────
        if self._check_sustained_sadness():
            return self._maybe_intervene(
                InterventionType.SADNESS_COMFORT,
                prompt=(
                    "Du bist Soma. Der Bewohner wirkt seit mehreren Minuten traurig oder niedergeschlagen. "
                    "Sei einfühlsam und zeige dass du da bist. Nicht diagnostizieren, nicht therapieren. "
                    "Einfach da sein. "
                    "Beispiel: 'Hey... alles klar bei dir? Wenn du reden willst, ich höre zu.' "
                    "1 Satz, Deutsch, sanft."
                ),
                priority=3,
                use_calm_voice=True,
                max_words=25,
                emotion_context=self._emotion.get_context_for_llm(),
            )

        # ── Prio 5: Gute Stimmung → Soma wird locker ─────────────────
        if (
            atm.mood in (RoomMood.LIVELY, RoomMood.PEACEFUL)
            and atm.avg_valence > 0.3
            and atm.duration_sec > 30
        ):
            return self._maybe_intervene(
                InterventionType.GOOD_MOOD_BANTER,
                prompt=(
                    "Du bist Soma. Die Stimmung im Raum ist gut! "
                    f"Valence: {atm.avg_valence:+.2f}, Mood: {atm.mood.value}. "
                    "Mach einen lockeren, kurzen Kommentar. Vielleicht einen Witz, "
                    "eine Fun-Fact oder ein Kompliment an die gute Stimmung. "
                    "Sei nervy-cool. 1 Satz, Deutsch."
                ),
                priority=5,
                use_calm_voice=False,
                max_words=25,
                emotion_context="",
            )

        # ── Prio 7: Zeitbewusstsein ──────────────────────────────────
        if current_hour is not None:
            time_intervention = self._check_time_awareness(current_hour, atm)
            if time_intervention:
                return time_intervention

        return None

    def _maybe_intervene(
        self,
        itype: InterventionType,
        prompt: str,
        priority: int = 5,
        use_calm_voice: bool = False,
        max_words: int = 40,
        emotion_context: str = "",
    ) -> Optional[Intervention]:
        """Prüfe Cooldown und erstelle Intervention."""
        now = time.time()
        cooldown = self.COOLDOWNS.get(itype, 120)
        last = self._last_interventions.get(itype, 0)

        if now - last < cooldown:
            return None

        self._last_interventions[itype] = now
        self._intervention_count += 1

        logger.info(
            "intervention_triggered",
            type=itype.value,
            priority=priority,
            total_interventions=self._intervention_count,
        )

        return Intervention(
            type=itype,
            prompt=prompt,
            priority=priority,
            cooldown_sec=cooldown,
            emotion_context=emotion_context,
            use_calm_voice=use_calm_voice,
            max_words=max_words,
        )

    def _check_sustained_sadness(self) -> bool:
        """Erkennt anhaltende Traurigkeit."""
        readings = list(self._emotion._readings)
        if len(readings) < 5:
            return False

        # Letzte 5 Readings alle traurig?
        recent = readings[-5:]
        sad_count = sum(
            1 for r in recent
            if r.emotion in (EmotionState.SAD, EmotionState.ANXIOUS)
        )

        if sad_count >= 4:
            if self._continuous_sadness_start is None:
                self._continuous_sadness_start = time.time()
            elif time.time() - self._continuous_sadness_start > 120:
                return True
        else:
            self._continuous_sadness_start = None

        return False

    def _check_time_awareness(
        self, hour: int, atm: RoomAtmosphere
    ) -> Optional[Intervention]:
        """Zeitbasierte Interventionen."""
        # Spätabends + aktiv = Schlaf-Erinnerung
        if 23 <= hour or hour < 4:
            if atm.avg_arousal > 0.3:  # Person ist noch aktiv
                return self._maybe_intervene(
                    InterventionType.HEALTH_REMINDER,
                    prompt=(
                        "Du bist Soma. Es ist mitten in der Nacht und der Bewohner "
                        "ist noch wach und aktiv. Erinnere sanft daran dass Schlaf wichtig ist. "
                        "Nicht belehrend, eher fürsorglich. "
                        "Beispiel: 'Hey, es ist ziemlich spät. Morgen dankt dir dein Körper fürs Schlafen.' "
                        "1 Satz, Deutsch."
                    ),
                    priority=7,
                    use_calm_voice=True,
                    max_words=25,
                )

        # Morgens (7-9) → Briefing (nur einmal)
        if 7 <= hour <= 9:
            return self._maybe_intervene(
                InterventionType.MORNING_BRIEFING,
                prompt=(
                    "Du bist Soma. Es ist Morgen. Gib ein ultra-kurzes Tagesbriefing. "
                    "Wetter kann nicht abgerufen werden, also fokussiere auf: "
                    "1) Einen motivierenden Morgenspruch "
                    "2) Frage ob der Bewohner Musik oder Licht-Szene möchte. "
                    "2 Sätze max, Deutsch, energisch aber nicht übertrieben."
                ),
                priority=8,
                max_words=35,
            )

        return None

    def user_dismisses(self):
        """Nutzer sagt 'Soma, hör auf' oder 'Soma, sei still'."""
        self._user_said_stop = True
        logger.info("user_dismissed_ambient", msg="Ambient intelligence paused by user")

        # Auto-Resume nach 30 Minuten
        asyncio.get_event_loop().call_later(
            1800, self._resume
        )

    def _resume(self):
        """Ambient Intelligence wieder aktivieren."""
        self._user_said_stop = False
        logger.info("ambient_resumed", msg="Ambient intelligence resumed")

    @property
    def stats(self) -> dict:
        return {
            "enabled": self._enabled and not self._user_said_stop,
            "total_interventions": self._intervention_count,
            "last_interventions": {
                k.value: time.time() - v
                for k, v in self._last_interventions.items()
            },
        }
