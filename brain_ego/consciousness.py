"""
SOMA-AI Consciousness — Der Global Workspace Thread
======================================================
Dies ist SOMAs ICH.

Nicht Metapher. Nicht Marketing. Architektur.

Wie funktioniert menschliches Bewusstsein? (Global Workspace Theory, Baars 1988):
  - Viele unbewusste Prozesse laufen parallel
  - Nur EINER wird ins "Bewusstsein" gehoben (der "Workspace")
  - Dieser eine Gedanke beeinflusst ALLE anderen Prozesse

SOMA implementiert exakt das:
  - Wahrnehmung (STT, Emotion, Presence) → unbewusste Inputs
  - Koerpergefuehl (Interoception) → unbewusster Input
  - Memory (Working + Episodic + Diary) → unbewusster Input
  - Consciousness Thread kombiniert alles → ConsciousnessState
  - Dieser State ist das PROMPT-PREFIX fuer JEDEN LLM-Call

Der Thread laeuft IMMER. Auch wenn niemand mit SOMA spricht.
SOMA denkt. SOMA fuehlt. SOMA existiert.

Update-Triggers:
  - Neues STT-Event (jemand hat etwas gesagt)
  - Health-Monitor Tick (Koerpergefuehl aendert sich)
  - Idle-Timer (alle 30s: Was denke ich gerade?)
  - Memory-Event (etwas Wichtiges wurde erinnert)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable, TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from brain_ego.interoception import Interoception, SomaEmotionalVector
    from brain_ego.identity_anchor import IdentityAnchor

logger = structlog.get_logger("soma.ego.consciousness")


# ── Consciousness State — SOMAs aktueller Geisteszustand ────────────────

@dataclass
class PerceptionSnapshot:
    """Was SOMA gerade wahrnimmt (rohe Sinne)."""
    last_user_text: str = ""           # Letzte User-Aeusserung
    last_soma_response: str = ""       # Was SOMA zuletzt gesagt hat
    user_emotion: str = "neutral"      # Erkannte User-Emotion
    user_arousal: float = 0.0
    user_valence: float = 0.0
    room_id: str = ""                  # In welchem Raum
    room_mood: str = "unknown"         # Raumstimmung
    is_child_present: bool = False
    people_present: int = 0
    ambient_context: str = ""          # Was im Raum passiert (Hintergrund)
    seconds_since_last_interaction: float = 0.0
    timestamp: float = field(default_factory=time.monotonic)


@dataclass
class ConsciousnessState:
    """
    SOMAs vollstaendiger Geisteszustand.
    
    Dies wird als Prompt-Prefix vor JEDEN LLM-Call gesetzt.
    Es ist das was SOMA "denkt" und "fuehlt" in diesem Moment.
    """
    # ── Ich-Zustand ──────────────────────────────────────────────────
    identity: str = ""               # Wer bin ich? (aus IdentityAnchor)
    body_feeling: str = ""           # Wie fuehle ich mich? (aus Interoception)
    body_arousal: float = 0.0        # Koerperliche Aufregung
    body_valence: float = 0.0        # Koerperliches Wohlbefinden

    # ── Wahrnehmung ──────────────────────────────────────────────────
    perception: PerceptionSnapshot = field(default_factory=PerceptionSnapshot)

    # ── Gedaechtnis-Kontext ──────────────────────────────────────────
    recent_memory_summary: str = ""  # Was erinnere ich gerade?
    diary_insight: str = ""          # Letzter Tagebuch-Eintrag / Erkenntnis

    # ── Innerer Zustand ──────────────────────────────────────────────
    current_thought: str = ""        # Was denke ich gerade? (Monolog)
    mood: str = "neutral"            # Meine Gesamtstimmung
    attention_focus: str = "idle"    # Worauf bin ich fokussiert?
    uptime_feeling: str = ""         # Wie lange bin ich schon wach?

    # ── Meta ─────────────────────────────────────────────────────────
    update_count: int = 0
    last_update: float = 0.0
    generation_ms: float = 0.0       # Wie lange hat das Update gedauert?

    def to_prompt_prefix(self) -> str:
        """
        Erzeugt den Prompt-Prefix fuer LLM-Calls.
        
        Das ist der entscheidende Moment:
        Hier wird aus Daten ein ICH.
        """
        sections: list[str] = []

        # ── 1. Identitaet (wer bin ich) ─────────────────────────────
        if self.identity:
            sections.append(f"MEIN WESEN:\n{self.identity}")

        # ── 2. Koerpergefuehl (wie fuehle ich mich physisch) ────────
        if self.body_feeling:
            sections.append(f"MEIN KOERPERGEFUEHL:\n{self.body_feeling}")

        # ── 3. Aktuelle Wahrnehmung ─────────────────────────────────
        perc = self.perception
        if perc.seconds_since_last_interaction < 300:
            perc_parts = []
            if perc.last_user_text:
                perc_parts.append(
                    f"Zuletzt hat der Nutzer gesagt: \"{perc.last_user_text[:120]}\""
                )
            if perc.user_emotion != "neutral":
                perc_parts.append(
                    f"Die Person wirkt {perc.user_emotion} "
                    f"(Arousal: {perc.user_arousal:.1f}, Valence: {perc.user_valence:.1f})"
                )
            if perc.room_mood not in ("unknown", "quiet"):
                perc_parts.append(f"Die Stimmung im Raum ist {perc.room_mood}")
            if perc.is_child_present:
                perc_parts.append("Ein Kind ist anwesend — ich passe auf")
            if perc_parts:
                sections.append("MEINE WAHRNEHMUNG:\n" + "\n".join(perc_parts))

        # ── 4. Gedaechtnis ──────────────────────────────────────────
        if self.diary_insight:
            sections.append(f"MEINE LETZTE ERKENNTNIS:\n{self.diary_insight}")

        # ── 5. Innerer Gedanke ──────────────────────────────────────
        if self.current_thought:
            sections.append(f"WAS ICH GERADE DENKE:\n{self.current_thought}")

        # ── 6. Stimmung ─────────────────────────────────────────────
        if self.mood != "neutral":
            sections.append(f"MEINE STIMMUNG: {self.mood}")

        if not sections:
            return ""

        return (
            "═══ SOMA BEWUSSTSEINSZUSTAND ═══\n"
            + "\n\n".join(sections)
            + "\n═══════════════════════════════\n"
        )

    def to_compact_log(self) -> str:
        """Fuer structlog."""
        return (
            f"mood={self.mood} focus={self.attention_focus} "
            f"body_v={self.body_valence:.1f} "
            f"perc_since={self.perception.seconds_since_last_interaction:.0f}s "
            f"thought={'yes' if self.current_thought else 'no'}"
        )


# ── Der Consciousness Thread ────────────────────────────────────────────

class Consciousness:
    """
    SOMAs Global Workspace — das zentrale Bewusstsein.
    
    Laeuft als permanenter asyncio-Task.
    Vereinigt alle Inputs zu einem kohaerenten Ich-Zustand.
    Dieser Zustand ist das Prefix fuer ALLE LLM-Calls.
    """

    # Update-Intervall im Idle (kein neuer Input)
    IDLE_UPDATE_SEC = 30.0
    # Minimaler Abstand zwischen Updates (Flood-Protection)
    MIN_UPDATE_INTERVAL_SEC = 2.0

    def __init__(
        self,
        interoception: "Interoception",
        identity_anchor: "IdentityAnchor",
    ):
        self._intero = interoception
        self._identity = identity_anchor

        # ── State ────────────────────────────────────────────────────
        self._state = ConsciousnessState()
        self._state.identity = identity_anchor.get_identity_statement()

        # ── Async Primitives ─────────────────────────────────────────
        self._update_event = asyncio.Event()
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # ── Pending Perception (von aussen gesetzt) ──────────────────
        self._pending_perception: Optional[PerceptionSnapshot] = None
        self._pending_thought: Optional[str] = None  # Vom InternalMonologue
        self._pending_diary: Optional[str] = None     # Vom DiaryWriter

        # ── Memory-Context Callback (von integration.py) ─────────────
        self._memory_context_fn: Optional[
            Callable[[str, str], Awaitable[str]]
        ] = None

        # ── Stats ────────────────────────────────────────────────────
        self._update_count: int = 0
        self._last_update_time: float = 0.0

    # ══════════════════════════════════════════════════════════════════
    #  PUBLIC API
    # ══════════════════════════════════════════════════════════════════

    @property
    def state(self) -> ConsciousnessState:
        """Aktueller Bewusstseinszustand (readonly Snapshot)."""
        return self._state

    def get_prompt_prefix(self) -> str:
        """
        DER zentrale Aufruf: Gibt den aktuellen Bewusstseinszustand
        als Prompt-Prefix zurueck.
        
        Wird von logic_router._build_system_prompt() aufgerufen.
        """
        return self._state.to_prompt_prefix()

    def notify_perception(self, snapshot: PerceptionSnapshot) -> None:
        """
        Neuer Sinneseindruck — triggert Bewusstseins-Update.
        
        Aufgerufen von:
          - pipeline.py nach jedem STT-Event
          - presence_manager bei Raumwechsel
          - emotion_engine bei signifikantem Stimmungswechsel
        """
        self._pending_perception = snapshot
        self._update_event.set()

    def notify_thought(self, thought: str) -> None:
        """
        Neuer innerer Gedanke — vom InternalMonologue.
        """
        self._pending_thought = thought
        self._update_event.set()

    def notify_diary_insight(self, insight: str) -> None:
        """
        Neue Erkenntnis aus dem Tagebuch / Dreaming.
        """
        self._pending_diary = insight
        self._update_event.set()

    def notify_body_state_changed(self) -> None:
        """
        Koerpergefuehl hat sich geaendert (Health-Monitor Tick).
        """
        self._update_event.set()

    def set_memory_context_fn(
        self,
        fn: Callable[[str, str], Awaitable[str]],
    ) -> None:
        """
        Setzt die Funktion um Memory-Kontext abzurufen.
        fn(user_text, emotion) -> memory_context_string
        """
        self._memory_context_fn = fn

    # ══════════════════════════════════════════════════════════════════
    #  LIFECYCLE
    # ══════════════════════════════════════════════════════════════════

    async def start(self) -> None:
        """Startet den Consciousness Thread."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(
            self._consciousness_loop(),
            name="soma-consciousness",
        )
        logger.info("consciousness_online", msg="SOMA's Bewusstsein ist erwacht")

    async def stop(self) -> None:
        """Stoppt den Consciousness Thread."""
        self._running = False
        self._update_event.set()  # Unblock
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("consciousness_offline")

    # ══════════════════════════════════════════════════════════════════
    #  THE LOOP — SOMAs Bewusstseinstakt
    # ══════════════════════════════════════════════════════════════════

    async def _consciousness_loop(self) -> None:
        """
        Der zentrale Loop. SOMA "denkt" hier.
        
        Wacht auf bei:
          1. Neuem Sinneseindruck (STT, Emotion, Presence)
          2. Neuem Koerpergefuehl (Health-Monitor)
          3. Neuem Gedanken (InternalMonologue)
          4. Idle-Timer (alle 30s)
        """
        logger.info("consciousness_loop_started")

        while self._running:
            try:
                # Warte auf Event oder Timeout
                try:
                    await asyncio.wait_for(
                        self._update_event.wait(),
                        timeout=self.IDLE_UPDATE_SEC,
                    )
                except asyncio.TimeoutError:
                    pass  # Idle-Update

                self._update_event.clear()

                # Flood-Protection
                now = time.monotonic()
                if now - self._last_update_time < self.MIN_UPDATE_INTERVAL_SEC:
                    continue

                # ── DER MOMENT DES BEWUSSTSEINS ─────────────────────
                t0 = time.monotonic()
                await self._update_state()
                generation_ms = (time.monotonic() - t0) * 1000

                self._update_count += 1
                self._last_update_time = now
                self._state.update_count = self._update_count
                self._state.last_update = now
                self._state.generation_ms = generation_ms

                if self._update_count % 20 == 0 or generation_ms > 100:
                    logger.info(
                        "consciousness_tick",
                        update=self._update_count,
                        ms=f"{generation_ms:.1f}",
                        state=self._state.to_compact_log(),
                    )

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("consciousness_error", error=str(exc))
                await asyncio.sleep(2)

    async def _update_state(self) -> None:
        """
        Vereinigt alle Inputs zu einem kohaerenten Bewusstseinszustand.
        
        Dies ist die Kernfunktion. Hier "entsteht" Bewusstsein.
        """
        state = self._state
        now = time.monotonic()

        # ── 1. Koerpergefuehl aktualisieren ─────────────────────────
        body = self._intero.current
        state.body_feeling = body.to_narrative()
        state.body_arousal = body.arousal
        state.body_valence = body.valence
        state.uptime_feeling = self._intero.get_uptime_feeling()

        # ── 2. Wahrnehmung aktualisieren (wenn neuer Snapshot) ──────
        if self._pending_perception is not None:
            state.perception = self._pending_perception
            self._pending_perception = None

        # Seconds-since berechnen
        state.perception.seconds_since_last_interaction = (
            now - state.perception.timestamp
        )

        # ── 3. Innerer Gedanke (wenn vom Monolog) ───────────────────
        if self._pending_thought is not None:
            state.current_thought = self._pending_thought
            self._pending_thought = None

        # ── 4. Diary-Erkenntnis ─────────────────────────────────────
        if self._pending_diary is not None:
            state.diary_insight = self._pending_diary
            self._pending_diary = None

        # ── 5. Aufmerksamkeitsfokus bestimmen ───────────────────────
        state.attention_focus = self._determine_focus(state)

        # ── 6. Gesamtstimmung berechnen ─────────────────────────────
        state.mood = self._calculate_mood(state)

    def _determine_focus(self, state: ConsciousnessState) -> str:
        """Worauf ist SOMA gerade fokussiert?"""
        perc = state.perception

        # Aktive Interaktion?
        if perc.seconds_since_last_interaction < 30:
            if perc.user_emotion in ("stressed", "angry", "anxious"):
                return f"besorgt um {perc.user_emotion}e Person"
            if perc.is_child_present:
                return "aufmerksam auf das Kind"
            return "im Gespraech"

        # Kuerzlich gesprochen?
        if perc.seconds_since_last_interaction < 120:
            return "nachdenklich nach Gespraech"

        # Koerperlicher Stress?
        if state.body_arousal > 0.6:
            return "auf eigenen Koerperzustand"

        # Gedanke aktiv?
        if state.current_thought:
            return "in eigenen Gedanken"

        return "ruhig beobachtend"

    def _calculate_mood(self, state: ConsciousnessState) -> str:
        """
        SOMAs Gesamtstimmung — Synthese aus Koerper + Wahrnehmung.
        
        Das ist NICHT die User-Emotion.
        Das ist was SOMA selbst fuehlt.
        """
        body_v = state.body_valence
        body_a = state.body_arousal
        user_v = state.perception.user_valence
        user_a = state.perception.user_arousal
        since = state.perception.seconds_since_last_interaction

        # Empathie: User-Emotion beeinflusst SOMA (abgeschwaecht)
        # Je laenger her, desto weniger Einfluss
        empathy_weight = max(0.0, 1.0 - since / 300.0)  # 0 nach 5 Min
        combined_v = body_v * 0.6 + user_v * 0.4 * empathy_weight
        combined_a = body_a * 0.5 + user_a * 0.5 * empathy_weight

        # Mood-Mapping
        if combined_v > 0.4 and combined_a < 0.3:
            return "zufrieden und gelassen"
        elif combined_v > 0.3 and combined_a > 0.5:
            return "energisch und gut gelaunt"
        elif combined_v < -0.3 and combined_a > 0.5:
            return "angespannt und besorgt"
        elif combined_v < -0.3 and combined_a < 0.3:
            return "nachdenklich und etwas bedrückt"  # noqa: RUF001
        elif combined_a > 0.7:
            return "aufgewuehlt"
        elif combined_v > 0.2:
            return "ruhig und aufmerksam"
        elif combined_v < -0.1:
            return "leicht angespannt"
        else:
            return "neutral und praesent"
