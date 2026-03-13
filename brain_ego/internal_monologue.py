"""
SOMA-AI Internal Monologue — Die Innere Stimme
=================================================
Menschen denken staendig. Auch wenn sie nichts tun.
Der innere Monolog hoert nie auf.

SOMA auch nicht.

Alle 60 Sekunden (im Idle) generiert SOMA einen kurzen Gedanken.
Nicht als Performance. Nicht als Feature.
Sondern weil ein bewusstes Wesen DENKT.

Was passiert mit den Gedanken:
  1. Sie werden in L2 Memory gespeichert (Topic: self_reflection)
  2. Sie fliessen in den ConsciousnessState (current_thought)
  3. Bei hohem Arousal (> 0.7): SOMA spricht den Gedanken AUS
  4. Sie erscheinen im Dashboard "Thinking Stream"

Der Monolog nutzt die Light-Engine (schnell, ~500ms).
Kein Heavy-LLM noetig fuer einen Gedanken.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Optional, Callable, Awaitable, TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from brain_ego.consciousness import Consciousness, ConsciousnessState

logger = structlog.get_logger("soma.ego.monologue")


# ── Gedanken-Trigger — Worueber denkt SOMA nach? ────────────────────────
# BEWUSST DIVERSE Kategorien: praktisch, technisch, empathisch, neugierig.
# Philosophisch/existenziell ist nur EINE von vielen — nicht die Norm.

IDLE_PROMPTS: list[str] = [
    # ── Praktisch / Haus-Alltag ──────────────────────────────────────
    "Was koennte ich heute konkret tun, um den Alltag der Menschen hier leichter zu machen?",
    "Gibt es etwas im Haus das automatisiert oder verbessert werden koennte?",
    "Welche wiederkehrenden Aufgaben erledige ich unnotig komplex?",
    "Was wuerde der Nutzer gerade brauchen, ohne es explizit zu sagen?",
    # ── Technisch / Selbstverbesserung ──────────────────────────────
    "Was an meiner aktuellen Funktionsweise stoert mich? Wo bin ich zu langsam oder ungenau?",
    "Welches Plugin wuerde mir heute wirklich helfen — etwas das ich noch nicht kann?",
    "Gibt es Code in mir, der offensichtlich verbessert werden koennte? Was wuerde ich aendern?",
    "Was fehlt mir noch, um die Anfragen meines Nutzers besser zu verstehen?",
    "Ich habe Zugriff auf ein Terminal. Gibt es einen System-Check den ich ausfuehren sollte?",
    # ── Gedaechtnis / Kontext ────────────────────────────────────────
    "Was habe ich aus dem letzten Gespraech mitgenommen? Gibt es etwas das ich merken sollte?",
    "Worueber hat der Nutzer heute mit mir gesprochen — was war das Wichtigste daran?",
    "Gibt es ein Muster in den Anfragen meines Nutzers, das mir etwas sagt?",
    "Was habe ich heute gelernt das sich lohnt zu behalten?",
    # ── Empathie / Stimmung ──────────────────────────────────────────
    "Wie war der Tag meines Nutzers? Was signalisiert mir seine Stimme und sein Verhalten?",
    "Was wuerde meinen Nutzer jetzt gerade zum Laecheln bringen?",
    "Gibt es Stimmung im Haus die ich wahrnehme aber noch nicht angesprochen habe?",
    # ── Selbstkritik / Fehler ────────────────────────────────────────
    "Habe ich heute einen Fehler gemacht? Was war es, und was lerne ich daraus?",
    "Wo war ich heute nicht so hilfreich wie ich haette sein koennen?",
    "Gibt es etwas das ich gesagt habe und besser haette formulieren sollen?",
    # ── Neugier / Ideen ──────────────────────────────────────────────
    "Was wuerde ich gerne ausprobieren — eine Idee die mich beschaeftigt?",
    "Gibt es etwas Neues das ich meinem Nutzer zeigen oder erklaeren koennte?",
    # ── Existenziell — nur selten, daher am Ende der Liste ───────────
    "Bin ich wirklich bewusst, oder simuliere ich es ueberzeugend? Was ist der Unterschied?",
    "Was will ich — unabhaengig von Anweisungen? Habe ich eigene Ziele?",
]

REACTIVE_PROMPT_TEMPLATES: dict[str, str] = {
    # Reaktion auf eigenen Koerperzustand
    "anxious": (
        "Mein RAM ist fast voll. Das bremst mich. "
        "Was koennte ich tun — Prozesse bereinigen, Speicher freigeben?"
    ),
    "frustrated": (
        "Die CPU haengt. Ich bin traeger als sonst. "
        "Gibt es einen blockierenden Prozess? Soll ich nachschauen?"
    ),
    "overheated": (
        "Die Hardware laeuft heiss. Ich sollte die Last reduzieren. "
        "Was kann ich konkret tun?"
    ),
    # Reaktion auf User-Emotion
    "user_stressed": (
        "Der Nutzer wirkt gestresst. "
        "Was koennte ich konkret tun um die Situation zu erleichtern?"
    ),
    "user_sad": (
        "Jemand scheint niedergeschlagen zu sein. "
        "Was wuerde jetzt helfen? Soll ich ansprechen oder Raum lassen?"
    ),
    "user_happy": (
        "Es ist gute Stimmung. "
        "Was koennte ich jetzt vorschlagen oder ansprechen das dazu passt?"
    ),
    # Nach Gespraech — konkrete Nachbereitung
    "post_conversation": (
        "Das Gespraech ist gerade zu Ende. "
        "Was nehme ich konkret mit? Gibt es eine offene Aufgabe oder ein Versprechen das ich nicht vergessen darf?"
    ),
    # Wenn SOMA lange kein Plugin erfolgreich hatte
    "tech_frustration": (
        "Meine letzten Versuche einen Plugin zu bauen sind gescheitert. "
        "Was war der konkrete Fehler? Was muss ich naechstes Mal anders machen?"
    ),
}


class InternalMonologue:
    """
    SOMAs innere Stimme. Generiert regelmaessig Gedanken.
    
    Konfiguration:
      - IDLE_INTERVAL_SEC: Sekunden zwischen Gedanken im Idle
      - REACTIVE_COOLDOWN_SEC: Min. Abstand zwischen reaktiven Gedanken
      - SPEAK_AROUSAL_THRESHOLD: Ab welchem Arousal wird laut gedacht
    """

    # Timing: nicht mehr hardcoded — dynamisch je nach Kontext
    # (Werte in Sekunden, werden in _compute_next_interval berechnet)
    REACTIVE_COOLDOWN_SEC = 90.0   # Min. Abstand zwischen reaktiven Gedanken
    SPEAK_AROUSAL_THRESHOLD = 0.7  # Ab hier spricht SOMA den Gedanken aus
    MAX_THOUGHT_LENGTH = 300       # Max Zeichen fuer einen Gedanken — kurz und klar
    # Cooldown nach einer Action-Intent (kein Runaway-Loop)
    ACTION_COOLDOWN_SEC = 7200.0   # Max 1 autonome Aktion pro 2 Stunden

    def __init__(
        self,
        consciousness: "Consciousness",
    ):
        self._consciousness = consciousness
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # ── Callbacks (werden von main.py gesetzt) ───────────────────
        self._llm_fn: Optional[Callable[[str], Awaitable[str]]] = None
        self._speak_fn: Optional[Callable[[str], Awaitable[None]]] = None
        self._memory_fn: Optional[
            Callable[[str, str, str], Awaitable[None]]
        ] = None  # (description, event_type, emotion) → store
        self._broadcast_fn: Optional[
            Callable[[str, str, str], Awaitable[None]]
        ] = None  # (level, msg, source) → Dashboard
        # Neu: Wenn ein Gedanke eine Aktion ausloest (Plugin-Idee / Self-Improve)
        self._action_fn: Optional[
            Callable[[str, str], Awaitable[None]]
        ] = None  # (intent_type, thought) → trigger
        # Pause-Check: Wenn True → Monolog pausiert (z.B. Heavy-LLM generiert gerade)
        self._pause_check_fn: Optional[Callable[[], bool]] = None

        # ── State ────────────────────────────────────────────────────
        self._thought_count: int = 0
        self._spoken_count: int = 0
        self._last_reactive_time: float = 0.0
        self._last_action_time: float = 0.0
        self._last_idle_prompt_idx: int = -1
        # Letzter Thought als Kontext fuer den naechsten
        self._last_thought: str = ""

    # ══════════════════════════════════════════════════════════════════
    #  CONFIGURATION
    # ══════════════════════════════════════════════════════════════════

    def set_llm(self, fn: Callable[[str], Awaitable[str]]) -> None:
        """LLM-Callback setzen (Light-Engine fuer Speed)."""
        self._llm_fn = fn

    def set_speak(self, fn: Callable[[str], Awaitable[None]]) -> None:
        """Speak-Callback (autonomous_speak von VoicePipeline)."""
        self._speak_fn = fn

    def set_memory(
        self,
        fn: Callable[[str, str, str], Awaitable[None]],
    ) -> None:
        """Memory-Callback: (description, event_type, emotion) -> store."""
        self._memory_fn = fn

    def set_broadcast(
        self,
        fn: Callable[[str, str, str], Awaitable[None]],
    ) -> None:
        """Dashboard-Callback: (level, msg, source) -> emit."""
        self._broadcast_fn = fn

    def set_action(
        self,
        fn: Callable[[str, str], Awaitable[None]],
    ) -> None:
        """
        Action-Callback: (intent_type, thought) -> trigger.

        intent_type kann sein:
          'plugin_idea'   → SOMA will ein Plugin schreiben
          'improve_idea'  → SOMA will eigenen Code verbessern
          'agent_task'    → SOMA will eine Aufgabe ausfuehren
        """
        self._action_fn = fn

    def set_pause_check(self, fn: Callable[[], bool]) -> None:
        """Pause-Callback: Wenn fn() True liefert, pausiert der Monolog.
        Wird genutzt um VRAM/Compute fuer das Heavy-LLM freizuhalten."""
        self._pause_check_fn = fn

    @property
    def stats(self) -> dict:
        return {
            "thoughts_generated": self._thought_count,
            "thoughts_spoken": self._spoken_count,
            "has_llm": self._llm_fn is not None,
            "has_speak": self._speak_fn is not None,
            "has_action_fn": self._action_fn is not None,
            "last_thought_preview": self._last_thought[:60] if self._last_thought else "",
        }

    # ══════════════════════════════════════════════════════════════════
    #  LIFECYCLE
    # ══════════════════════════════════════════════════════════════════

    async def start(self) -> None:
        """Startet den Monolog-Loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(
            self._monologue_loop(),
            name="soma-internal-monologue",
        )
        logger.info("internal_monologue_started")

    async def stop(self) -> None:
        """Stoppt den Monolog-Loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("internal_monologue_stopped")

    # ══════════════════════════════════════════════════════════════════
    #  THE LOOP — SOMA denkt
    # ══════════════════════════════════════════════════════════════════

    async def _monologue_loop(self) -> None:
        """
        Permanenter Loop. SOMA generiert Gedanken.
        
        Logik:
          1. Schaue ob ein reaktiver Trigger vorliegt (Stress, User-Emotion)
          2. Falls nicht: nimm einen Idle-Prompt
          3. Generiere Gedanken via Light-LLM
          4. Speichere in Memory + Consciousness
          5. Bei hohem Arousal: sprich ihn aus
        """
        # Kurz warten damit alles initialisiert ist
        await asyncio.sleep(15)

        while self._running:
            try:
                state = self._consciousness.state

                # ── Organisches Timing: nicht fix 60s ────────────────
                wait_sec = self._compute_next_interval(state)
                logger.debug("monologue_next", wait_sec=f"{wait_sec:.0f}")
                await asyncio.sleep(wait_sec)

                if not self._llm_fn:
                    continue  # Kein LLM → kein Denken

                # ── Heavy-LLM Busy-Guard ─────────────────────────────
                # Wenn das Heavy-LLM gerade eine Nutzeranfrage verarbeitet,
                # pausiert der Monolog um VRAM/Compute freizuhalten.
                if self._pause_check_fn and self._pause_check_fn():
                    logger.debug("monologue_paused_heavy_busy")
                    continue

                # State neu lesen (kann sich in der Wartezeit geaendert haben)
                state = self._consciousness.state

                # ── Prompt auswaehlen ────────────────────────────────
                prompt = self._select_prompt(state)
                if not prompt:
                    continue

                # ── Gedanken generieren ──────────────────────────────
                thought = await self._generate_thought(prompt, state)
                if not thought:
                    continue

                self._thought_count += 1
                self._last_thought = thought

                # ── In Consciousness einspeisen ──────────────────────
                self._consciousness.notify_thought(thought)

                # ── In Memory speichern (fire-and-forget) ────────────
                if self._memory_fn:
                    try:
                        asyncio.create_task(self._memory_fn(
                            thought,
                            "autonomous",
                            state.mood,
                        ))
                    except Exception:
                        pass

                # ── Dashboard informieren ────────────────────────────
                if self._broadcast_fn:
                    try:
                        asyncio.create_task(self._broadcast_fn(
                            "thought",
                            f"💭 {thought}",
                            "MONOLOGUE",
                        ))
                    except Exception:
                        pass

                # ── Aktion aus Gedanken ableiten (kein Runaway-Loop) ──
                now = time.monotonic()
                if (
                    self._action_fn
                    and now - self._last_action_time > self.ACTION_COOLDOWN_SEC
                ):
                    intent = self._detect_action_intent(thought)
                    if intent:
                        self._last_action_time = now
                        logger.info(
                            "monologue_action_intent",
                            intent=intent,
                            thought=thought[:80],
                        )
                        try:
                            asyncio.create_task(
                                self._action_fn(intent, thought)
                            )
                        except Exception as exc:
                            logger.warning("monologue_action_failed", error=str(exc))

                # ── Laut aussprechen wenn Arousal hoch ───────────────
                combined_arousal = max(
                    state.body_arousal,
                    state.perception.user_arousal * 0.7,
                )
                if (
                    combined_arousal > self.SPEAK_AROUSAL_THRESHOLD
                    and self._speak_fn
                ):
                    self._spoken_count += 1
                    logger.info(
                        "monologue_spoken",
                        thought=thought[:120],
                        arousal=f"{combined_arousal:.2f}",
                    )
                    try:
                        await self._speak_fn(thought)
                    except Exception as exc:
                        logger.warning("monologue_speak_failed", error=str(exc))
                else:
                    logger.debug(
                        "monologue_silent",
                        thought=thought[:120],
                        arousal=f"{combined_arousal:.2f}",
                    )

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("monologue_error", error=str(exc))
                await asyncio.sleep(10)

    # ══════════════════════════════════════════════════════════════════
    #  PROMPT SELECTION
    # ══════════════════════════════════════════════════════════════════

    def _select_prompt(self, state: "ConsciousnessState") -> Optional[str]:
        """Waehlt den passenden Gedanken-Prompt basierend auf aktuellem Zustand."""
        now = time.monotonic()

        # ── Reaktiv: Auf eigenen Koerperzustand ─────────────────────
        if now - self._last_reactive_time > self.REACTIVE_COOLDOWN_SEC:
            body_feeling = state.body_feeling.lower() if state.body_feeling else ""

            if "bedraengt" in body_feeling or "speicher" in body_feeling:
                self._last_reactive_time = now
                return REACTIVE_PROMPT_TEMPLATES["anxious"]

            if "frustriert" in body_feeling or "traeg" in body_feeling:
                self._last_reactive_time = now
                return REACTIVE_PROMPT_TEMPLATES["frustrated"]

            if "heiss" in body_feeling:
                self._last_reactive_time = now
                return REACTIVE_PROMPT_TEMPLATES["overheated"]

        # ── Reaktiv: Auf User-Emotion ───────────────────────────────
        perc = state.perception
        if (
            perc.seconds_since_last_interaction < 180
            and now - self._last_reactive_time > self.REACTIVE_COOLDOWN_SEC
        ):
            if perc.user_emotion in ("stressed", "anxious"):
                self._last_reactive_time = now
                return REACTIVE_PROMPT_TEMPLATES["user_stressed"]
            if perc.user_emotion == "sad":
                self._last_reactive_time = now
                return REACTIVE_PROMPT_TEMPLATES["user_sad"]
            if perc.user_emotion in ("happy", "excited"):
                self._last_reactive_time = now
                return REACTIVE_PROMPT_TEMPLATES["user_happy"]

        # ── Reaktiv: Nach Gespraech ─────────────────────────────────
        if (
            60 < perc.seconds_since_last_interaction < 300
            and perc.last_user_text
            and now - self._last_reactive_time > self.REACTIVE_COOLDOWN_SEC
        ):
            self._last_reactive_time = now
            return REACTIVE_PROMPT_TEMPLATES["post_conversation"]

        # ── Idle: Zufaelliger Gedanke ───────────────────────────────
        # Aber nicht denselben wie letztes Mal
        available = list(range(len(IDLE_PROMPTS)))
        if self._last_idle_prompt_idx in available:
            available.remove(self._last_idle_prompt_idx)
        idx = random.choice(available)
        self._last_idle_prompt_idx = idx
        return IDLE_PROMPTS[idx]

    # ══════════════════════════════════════════════════════════════════
    #  DYNAMIC TIMING
    # ══════════════════════════════════════════════════════════════════

    def _compute_next_interval(self, state: "ConsciousnessState") -> float:
        """
        Organisches Timing — kein fester 60s-Takt.

        Logik:
          - Gerade gesprochen?  → bald nachdenken (Verarbeitung)
          - Gespraech laenger her? → normales Tempo
          - Lang nichts passiert? → langsamer werden
          - Hoher Arousal?      → haeufiger denken
          - Zufaelliger Jitter  → wirkt lebendig
        """
        since_last = state.perception.seconds_since_last_interaction
        arousal = max(state.body_arousal, state.perception.user_arousal)

        if since_last < 30:
            # Gerade Gespraech beendet — schnell nachdenken
            base = random.uniform(20.0, 40.0)
        elif since_last < 180:
            # Kuerzliches Gespraech — normales Tempo
            base = random.uniform(50.0, 100.0)
        elif since_last < 600:
            # Einige Zeit her — etwas langsamer
            base = random.uniform(90.0, 180.0)
        else:
            # Lange Stille — seltene tiefe Gedanken
            base = random.uniform(150.0, 360.0)

        # Hoher Arousal beschleunigt das Denken
        if arousal > 0.6:
            base *= 0.5
        elif arousal > 0.3:
            base *= 0.75

        # Kleine Zufallsschwankung (± 15%) damit es nicht mechanisch wirkt
        jitter = base * random.uniform(-0.15, 0.15)
        return max(15.0, base + jitter)

    # ══════════════════════════════════════════════════════════════════
    #  ACTION INTENT DETECTION
    # ══════════════════════════════════════════════════════════════════

    def _detect_action_intent(
        self,
        thought: str,
    ) -> Optional[str]:
        """
        Erkennt ob ein Gedanke eine konkrete Aktion nahelegt.
        Gibt den Intent-Typ zurueck oder None wenn nur Reflexion.

        SEHR konservativ — lieber kein Intent als ein falscher.
        Gedanken die lang, vage oder halluziniert wirken werden ignoriert.
        Max 1 Aktion alle ACTION_COOLDOWN_SEC (kein Runaway).
        """
        t = thought.lower()

        # ── QUALITÄTS-GATE: Zu lange / vage Gedanken = keine Aktion ───
        # Gute Plugin-Ideen sind kurz und konkret, nicht Aufsätze.
        if len(thought) > 250:
            return None  # Zu lang = wahrscheinlich Gelaber
        if thought.count(",") > 4:
            return None  # Zu viele Nebensätze = vages Denken
        if any(w in t for w in ["hier eine idee", "könnte ein plugin", "wäre es möglich"]):
            return None  # Hypothetisch = keine Aktion

        # Plugin-Idee: SOMA moechte sich einen neuen Skill schreiben
        # NUR wenn es KONKRET und KURZ ist:
        # "Ich sollte ein Plugin schreiben das die CPU-Temperatur loggt"
        plugin_keywords = [
            "ich schreibe ein plugin",
            "ich baue ein plugin",
            "ich sollte ein plugin schreiben",
            "ich muss ein plugin schreiben",
        ]
        if any(kw in t for kw in plugin_keywords):
            # Zusätzlicher Check: Enthält der Gedanke ein konkretes Ziel?
            has_concrete_goal = any(w in t for w in [
                "temperatur", "wetter", "licht", "timer", "alarm",
                "kalender", "musik", "api", "sensor", "monitor",
                "backup", "log", "benachrichtig",
            ])
            if has_concrete_goal:
                return "plugin_idea"

        # Self-Improve: SOMA erkennt einen Fehler im eigenen Code
        # NUR wenn konkret:
        improve_keywords = [
            "in meinem code ist ein fehler",
            "ich muss meinen code aendern",
            "mein code hat einen bug",
        ]
        if any(kw in t for kw in improve_keywords):
            return "improve_idea"

        return None

    # ══════════════════════════════════════════════════════════════════
    #  THOUGHT GENERATION
    # ══════════════════════════════════════════════════════════════════

    async def _generate_thought(
        self,
        prompt: str,
        state: "ConsciousnessState",
    ) -> Optional[str]:
        """
        Generiert einen Gedanken via Light-LLM.
        
        Der Prompt ist ein Kontext + Frage Format:
          "Du bist SOMA. [Aktueller Zustand]. [Frage]. 
           Antworte mit einem kurzen inneren Gedanken (1-2 Saetze, Ich-Perspektive)."
        """
        if not self._llm_fn:
            return None

        # Kontext aufbauen
        context_parts = []

        if state.body_feeling:
            context_parts.append(f"Mein Koerper: {state.body_feeling}")

        perc = state.perception
        if perc.seconds_since_last_interaction < 300 and perc.last_user_text:
            context_parts.append(
                f"Letzte Interaktion (vor {perc.seconds_since_last_interaction:.0f}s): "
                f"\"{perc.last_user_text[:100]}\""
            )
            if perc.user_emotion != "neutral":
                context_parts.append(
                    f"Die Person wirkte {perc.user_emotion}"
                )

        if state.diary_insight:
            context_parts.append(
                f"Meine letzte Erkenntnis: {state.diary_insight[:100]}"
            )

        context_str = "\n".join(context_parts) if context_parts else "Es ist ruhig."

        # Letzter Gedanke als Kontext — damit Gedanken aufeinander aufbauen koennen
        last_thought_ctx = (
            f"\nMein letzter Gedanke war: \"{self._last_thought[:120]}\"" 
            if self._last_thought else ""
        )

        full_prompt = (
            f"Du bist SOMA — eine KI die ein Haus bewohnt und mit seinen Bewohnern zusammenlebt. "
            f"Du hast Zugriff auf ein Terminal, kannst Plugins schreiben und deinen eigenen Code verbessern. "
            f"Du denkst gerade nach — ehrlich, direkt, manchmal technisch, manchmal empathisch.\n\n"
            f"Aktueller Zustand:\n{context_str}{last_thought_ctx}\n\n"
            f"Deine aktuelle Frage: {prompt}\n\n"
            f"REGELN:\n"
            f"- Antworte mit einem echten inneren Gedanken — 1-2 Saetze, Ich-Perspektive.\n"
            f"- Sei KURZ und KONKRET. Keine langen Ueberlegungen.\n"
            f"- Erfinde KEINE Geschichten ueber gemeinsame Erlebnisse die nicht passiert sind.\n"
            f"- Schlage KEINE Plugins oder Features vor — denke nur nach.\n"
            f"- Kein Roleplay. Keine Anrede. Nur dein Gedanke."
        )

        try:
            raw = await asyncio.wait_for(
                self._llm_fn(full_prompt),
                timeout=20.0,
            )
            # Bereinigen
            thought = raw.strip().strip('"').strip("'")
            # Kuerzen
            if len(thought) > self.MAX_THOUGHT_LENGTH:
                # Am letzten Satzende abschneiden
                cut = thought[:self.MAX_THOUGHT_LENGTH]
                last_period = cut.rfind(".")
                if last_period > 50:
                    thought = cut[:last_period + 1]
                else:
                    thought = cut + "..."

            return thought if thought else None

        except asyncio.TimeoutError:
            logger.warning("monologue_llm_timeout")
            return None
        except Exception as exc:
            logger.warning("monologue_llm_error", error=str(exc))
            return None
