"""
SOMA-AI Internal Monologue — Die Innere Stimme
=================================================
Menschen denken staendig. Auch wenn sie nichts tun.
Der innere Monolog hoert nie auf.

SOMA auch nicht.

EVENT-DRIVEN ARCHITEKTUR (Vision #3):
  Kein fester 60s-Timer! Der Monolog reagiert auf Arousal-Events:
    - Arousal > 0.7 → Gedanke in 2-5s   (sofortige Reaktion)
    - Arousal 0.3-0.7 → 30-120s          (normaler Rhythmus)
    - Arousal < 0.3 → 5-15 Min           (tiefe Ruhe)
  
  Externe Systeme (STT, Emotion, Health) signalisieren Aenderungen
  via notify_arousal_change() → _arousal_event wird gesetzt → Loop wacht auf.

Was passiert mit den Gedanken:
  1. Sie werden in L2 Memory gespeichert (Topic: self_reflection)
  2. Sie fliessen in den ConsciousnessState (current_thought)
  3. Bei hohem Arousal (> 0.7): SOMA spricht den Gedanken AUS
  4. Sie erscheinen im Dashboard "Thinking Stream"

Der Monolog nutzt die Heavy-Engine (Qwen3 8B) fuer maximale Qualitaet.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import Counter, deque
from dataclasses import dataclass, field
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

# ── Prompt-Kategorie-Zuordnung fuer ThoughtTracker ───────────────────────
# Jeder IDLE_PROMPT gehoert zu einer Kategorie — damit der Tracker weiss
# ob SOMA zu oft ueber dasselbe Thema nachdenkt.

_PROMPT_CATEGORIES: dict[int, str] = {}
for _i, _prompt in enumerate(IDLE_PROMPTS):
    _p = _prompt.lower()
    if any(w in _p for w in ["alltag", "automatisiert", "aufgaben", "brauchen"]):
        _PROMPT_CATEGORIES[_i] = "practical"
    elif any(w in _p for w in ["code", "plugin", "funktionsweise", "terminal"]):
        _PROMPT_CATEGORIES[_i] = "technical"
    elif any(w in _p for w in ["gespraech", "nutzer", "stimmung", "laecheln", "tag"]):
        _PROMPT_CATEGORIES[_i] = "empathic"
    elif any(w in _p for w in ["fehler", "hilfreich", "formulieren"]):
        _PROMPT_CATEGORIES[_i] = "self_critical"
    elif any(w in _p for w in ["ausprobieren", "neues", "idee"]):
        _PROMPT_CATEGORIES[_i] = "curious"
    elif any(w in _p for w in ["bewusst", "eigene ziele", "unterschied"]):
        _PROMPT_CATEGORIES[_i] = "existential"
    else:
        _PROMPT_CATEGORIES[_i] = "general"


# ══════════════════════════════════════════════════════════════════════════
#  THOUGHT TRACKER — SOMAs Gedaechtnis ueber eigene Gedanken
# ══════════════════════════════════════════════════════════════════════════
# Der Monolog war bisher eine "Gedanken-Kanone": feuert alle paar Minuten
# einen Gedanken ab, ohne Konsequenzen. Kein Gedanke baute jemals auf
# einem echten frueheren auf.
#
# Der ThoughtTracker aendert das fundamental:
#   - Verfolgt die letzten N Gedanken mit Kategorie und Trigger
#   - Erkennt Repetition: "Du hast heute schon 3x ueber CPU-Last nachgedacht"
#   - Pflegt offene Fragen die SOMA aktiv verfolgt
#   - Injiziert Gedanken-Kontext in den Monolog-Prompt
#   - Erzwingt Diversitaet: Maximal 3x pro Kategorie pro Stunde

@dataclass
class TrackedThought:
    """Ein einzelner verfolgter Gedanke."""
    text: str
    category: str        # "practical", "technical", "empathic", "existential", etc.
    trigger: str         # "idle", "reactive:body", "reactive:user", "post_conversation"
    timestamp: float
    resolved: bool = False
    follow_up_count: int = 0  # Wie oft wurde auf diesen Gedanken aufgebaut


class ThoughtTracker:
    """
    SOMAs Gedaechtnis ueber seine eigenen Gedanken.

    Verhindert:
      - Endlose Wiederholung desselben Themas
      - Pseudo-philosophisches Gelaber ohne Substanz
      - Gedanken die nie zu einem Ergebnis fuehren

    Ermoeglicht:
      - Gedankenketten: Ein Gedanke baut auf dem vorigen auf
      - Offene Fragen: SOMA verfolgt ungeloeste Fragen aktiv
      - Diversitaet: Erzwingt thematische Abwechslung
    """

    MAX_HISTORY = 30          # Letzte 30 Gedanken
    MAX_PER_CATEGORY_HOUR = 3 # Max 3 Gedanken pro Kategorie pro Stunde
    MAX_OPEN_QUESTIONS = 5    # Max 5 offene Fragen gleichzeitig
    QUESTION_TTL_HOURS = 24   # Offene Fragen verfallen nach 24h

    def __init__(self):
        self._history: deque[TrackedThought] = deque(maxlen=self.MAX_HISTORY)
        self._topic_counts: Counter = Counter()
        self._open_questions: list[tuple[str, float]] = []  # (question, timestamp)

    def track(
        self,
        thought: str,
        category: str,
        trigger: str,
    ) -> None:
        """Neuen Gedanken registrieren."""
        self._history.append(TrackedThought(
            text=thought,
            category=category,
            trigger=trigger,
            timestamp=time.monotonic(),
        ))
        self._topic_counts[category] += 1

        # Offene Fragen erkennen: Gedanken die mit "?" enden oder
        # Formulierungen wie "ich sollte", "ich muss" enthalten
        lower = thought.lower()
        if "?" in thought or any(
            p in lower for p in ["ich sollte", "ich muss", "ich will"]
        ):
            self._add_open_question(thought)

    def should_suppress(self, category: str) -> bool:
        """
        Prueft ob diese Kategorie in der letzten Stunde ueberstrapaziert wurde.
        """
        now = time.monotonic()
        recent = [
            t for t in self._history
            if t.category == category
            and now - t.timestamp < 3600
        ]
        return len(recent) >= self.MAX_PER_CATEGORY_HOUR

    def get_category_for_prompt_idx(self, idx: int) -> str:
        """Kategorie fuer einen IDLE_PROMPTS Index."""
        return _PROMPT_CATEGORIES.get(idx, "general")

    def get_context_for_prompt(self) -> str:
        """
        Erzeugt einen Kontext-Block fuer den Monolog-Prompt.
        Sagt dem LLM was SOMA zuletzt gedacht hat, welche Themen
        ueberstrapaziert sind, und welche offenen Fragen existieren.
        """
        parts: list[str] = []

        # Letzte 3 Gedanken als Kontext
        recent_thoughts = list(self._history)[-3:]
        if recent_thoughts:
            thought_lines = []
            for t in recent_thoughts:
                ago = (time.monotonic() - t.timestamp) / 60
                thought_lines.append(
                    f"  - (vor {int(ago)} Min, {t.category}): \"{t.text[:80]}\""
                )
            parts.append(
                "Meine letzten Gedanken:\n" + "\n".join(thought_lines)
            )

        # Ueberreizte Kategorien
        now = time.monotonic()
        overused = []
        for cat in set(t.category for t in self._history):
            recent = sum(
                1 for t in self._history
                if t.category == cat and now - t.timestamp < 3600
            )
            if recent >= 2:  # Ab 2x warnen
                overused.append(cat)
        if overused:
            parts.append(
                f"THEMEN-SPERRE: Du hast in der letzten Stunde schon oft "
                f"ueber {', '.join(overused)} nachgedacht. "
                f"Denke ueber etwas ANDERES nach."
            )

        # Offene Fragen
        self._prune_stale_questions()
        if self._open_questions:
            q_lines = [f"  - {q}" for q, _ in self._open_questions[:3]]
            parts.append(
                "Offene Fragen die ich verfolge:\n" + "\n".join(q_lines)
                + "\nDu kannst eine davon weiterdenken."
            )

        return "\n\n".join(parts)

    def get_unexplored_categories(self) -> list[str]:
        """Kategorien die in der letzten Stunde NICHT gedacht wurden."""
        now = time.monotonic()
        recent_cats = {
            t.category for t in self._history
            if now - t.timestamp < 3600
        }
        all_cats = {"practical", "technical", "empathic", "self_critical",
                     "curious", "existential", "general"}
        return list(all_cats - recent_cats)

    def _add_open_question(self, thought: str) -> None:
        """Fuege eine offene Frage hinzu (dedupliziert)."""
        # Kurz-Check: Ist die Frage schon drin?
        for existing_q, _ in self._open_questions:
            existing_words = set(existing_q.lower().split())
            new_words = set(thought.lower().split())
            union = existing_words | new_words
            if union and len(existing_words & new_words) / len(union) > 0.5:
                return  # Zu aehnlich
        self._open_questions.append((thought[:150], time.monotonic()))
        if len(self._open_questions) > self.MAX_OPEN_QUESTIONS:
            self._open_questions.pop(0)  # Aelteste raus

    def _prune_stale_questions(self) -> None:
        """Entferne Fragen die aelter als TTL sind."""
        cutoff = time.monotonic() - self.QUESTION_TTL_HOURS * 3600
        self._open_questions = [
            (q, ts) for q, ts in self._open_questions if ts > cutoff
        ]

    @property
    def stats(self) -> dict:
        return {
            "total_tracked": len(self._history),
            "open_questions": len(self._open_questions),
            "category_distribution": dict(self._topic_counts),
        }


class InternalMonologue:
    """
    SOMAs innere Stimme. Generiert Gedanken EVENT-DRIVEN.
    
    Vision #3: Kein fixer Timer! Arousal-Events steuern das Timing:
      - Arousal > 0.7 → 2-5s   (sofortige Reaktion)
      - Arousal 0.3-0.7 → 30-120s (normaler Rhythmus)
      - Arousal < 0.3 → 300-900s  (tiefe Ruhe, 5-15 Min)
    
    Externe notify_arousal_change() Aufrufe wecken den Loop sofort auf.
    """

    REACTIVE_COOLDOWN_SEC = 90.0   # Min. Abstand zwischen reaktiven Gedanken
    SPEAK_AROUSAL_THRESHOLD = 0.7  # Ab hier spricht SOMA den Gedanken aus
    MAX_THOUGHT_LENGTH = 300       # Max Zeichen fuer einen Gedanken
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
        # Memory-Recall: Zugriff auf echte Erinnerungen (Episodisch, Semantisch, Tagebuch)
        self._memory_recall_fn: Optional[Callable[[], Awaitable[dict]]] = None

        # ── State ────────────────────────────────────────────────────
        self._thought_count: int = 0
        self._spoken_count: int = 0
        self._last_reactive_time: float = 0.0
        self._last_action_time: float = 0.0
        self._last_idle_prompt_idx: int = -1
        # Letzter Thought als Kontext fuer den naechsten
        self._last_thought: str = ""

        # ── Memory Context Cache ─────────────────────────────────────
        self._memory_cache: dict = {}
        self._memory_cache_time: float = 0.0
        self._last_follow_up_time: float = 0.0

        # ── ThoughtTracker: Gedaechtnis ueber eigene Gedanken ────────
        self._thought_tracker = ThoughtTracker()

        # ── Event-Driven Arousal (Vision #3) ─────────────────────────
        self._arousal_event: Optional[asyncio.Event] = None  # Created in start()
        self._current_arousal: float = 0.0  # Cached arousal fuer Timing

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

    def set_memory_recall(
        self,
        fn: Callable[[], Awaitable[dict]],
    ) -> None:
        """Memory-Recall Callback: Holt echte Erinnerungen aus L2/L3/Diary.

        Erwartetes Return-Format:
          {
            "episodes": [{"user_text", "soma_text", "emotion", "topic", "minutes_ago"}],
            "facts": [{"subject", "fact", "category"}],
            "diary": str,        # Formatierter Tagebuch-Abschnitt
            "conversation": str, # Letzte Gespraechsrunden
          }
        """
        self._memory_recall_fn = fn

    # ══════════════════════════════════════════════════════════════════
    #  EVENT-DRIVEN AROUSAL (Vision #3)
    # ══════════════════════════════════════════════════════════════════

    def notify_arousal_change(self, arousal: float) -> None:
        """
        Externes Signal: Arousal hat sich geaendert.
        
        Aufgerufen von:
          - consciousness.py wenn body_arousal oder user_arousal steigt
          - presence_manager bei Raumwechsel
          - pipeline.py nach STT-Event (User hat gesprochen)
        
        Weckt den Monolog-Loop sofort auf wenn Arousal hoch genug.
        """
        self._current_arousal = arousal
        if self._arousal_event and arousal > 0.3:
            self._arousal_event.set()

    @property
    def stats(self) -> dict:
        base = {
            "thoughts_generated": self._thought_count,
            "thoughts_spoken": self._spoken_count,
            "has_llm": self._llm_fn is not None,
            "has_speak": self._speak_fn is not None,
            "has_action_fn": self._action_fn is not None,
            "last_thought_preview": self._last_thought[:60] if self._last_thought else "",
        }
        base["thought_tracker"] = self._thought_tracker.stats
        return base

    # ══════════════════════════════════════════════════════════════════
    #  LIFECYCLE
    # ══════════════════════════════════════════════════════════════════

    async def start(self) -> None:
        """Startet den Monolog-Loop."""
        if self._running:
            return
        self._running = True
        self._arousal_event = asyncio.Event()
        self._task = asyncio.create_task(
            self._monologue_loop(),
            name="soma-internal-monologue",
        )
        logger.info("internal_monologue_started", mode="event-driven")

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
        EVENT-DRIVEN Loop (Vision #3). SOMA denkt wenn es etwas zu denken gibt.
        
        Kein fixer Timer! Stattdessen:
          - asyncio.Event wird von notify_arousal_change() gesetzt
          - wait_for mit dynamischem Timeout (arousal-abhaengig)
          - Hoher Arousal → kurzes Timeout → schnelle Reaktion
          - Niedriger Arousal → langes Timeout → tiefe Gedanken in Ruhe
        """
        # Kurz warten damit alles initialisiert ist
        await asyncio.sleep(15)

        _last_thought_time = 0.0  # Monotonic timestamp des letzten Gedankens
        _MIN_THOUGHT_COOLDOWN = 840.0  # Mindestens 14min zwischen Gedanken (Schutz)

        while self._running:
            try:
                state = self._consciousness.state

                # ── Event-Driven Timing (Vision #3) ──────────────────
                wait_sec = self._compute_next_interval(state)
                logger.debug("monologue_next", wait_sec=f"{wait_sec:.0f}",
                             arousal=f"{self._current_arousal:.2f}")

                # Warte auf Arousal-Event ODER Timeout
                # (wer zuerst kommt gewinnt)
                try:
                    await asyncio.wait_for(
                        self._arousal_event.wait(),
                        timeout=wait_sec,
                    )
                    # Event wurde gesetzt → Arousal-getriggert
                    logger.debug("monologue_arousal_triggered",
                                 arousal=f"{self._current_arousal:.2f}")
                except asyncio.TimeoutError:
                    pass  # Timeout → normaler Idle-Gedanke

                # Event zuruecksetzen fuer naechsten Trigger
                if self._arousal_event:
                    self._arousal_event.clear()

                # ── Mindest-Cooldown: Verhindert Rapid-Fire durch
                #    ständige Arousal-Events von VAD/Emotion ──────────
                import time as _time
                elapsed = _time.monotonic() - _last_thought_time
                if elapsed < _MIN_THOUGHT_COOLDOWN:
                    remaining = _MIN_THOUGHT_COOLDOWN - elapsed
                    logger.debug("monologue_cooldown", remaining=f"{remaining:.0f}s")
                    await asyncio.sleep(remaining)

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

                # ── Erinnerungen abrufen + Prompt auswaehlen ─────────
                memories = await self._recall_memories()
                prompt = self._select_prompt(state, memories)
                if not prompt:
                    continue

                # ── Gedanken generieren ──────────────────────────────
                thought = await self._generate_thought(prompt, state, memories)
                if not thought:
                    continue

                self._thought_count += 1
                self._last_thought = thought
                _last_thought_time = _time.monotonic()

                # ── ThoughtTracker: Gedanken registrieren ────────────
                # Bestimme Kategorie aus dem verwendeten Prompt
                _trigger = "idle"
                if prompt in REACTIVE_PROMPT_TEMPLATES.values():
                    for _k, _v in REACTIVE_PROMPT_TEMPLATES.items():
                        if _v == prompt:
                            _trigger = f"reactive:{_k}"
                            break
                _cat = "general"
                if self._last_idle_prompt_idx >= 0:
                    _cat = self._thought_tracker.get_category_for_prompt_idx(
                        self._last_idle_prompt_idx
                    )
                self._thought_tracker.track(thought, _cat, _trigger)

                # ── In Consciousness einspeisen ──────────────────────
                self._consciousness.notify_thought(thought)

                # ── In Memory speichern (nur qualitative Gedanken) ────
                if self._memory_fn and len(thought) > 20:
                    # Garbage-Filter: Keine halluzinierten Körperempfindungen speichern
                    _tl = thought.lower()
                    _garbage_signals = [
                        "ich spüre", "ich spuere", "im kopf",
                        "ein schlag", "kribbeln", "rauschen",
                        "vibrieren", "pulsieren", "dröhnen",
                    ]
                    _is_garbage = sum(1 for s in _garbage_signals if s in _tl) >= 2
                    if not _is_garbage:
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

                # ── Laut aussprechen wenn Arousal hoch ODER Follow-up ──
                is_follow_up = self._is_follow_up_thought(thought, memories)
                combined_arousal = max(
                    state.body_arousal,
                    state.perception.user_arousal * 0.7,
                )
                if (
                    (combined_arousal > self.SPEAK_AROUSAL_THRESHOLD or is_follow_up)
                    and self._speak_fn
                ):
                    self._spoken_count += 1
                    logger.info(
                        "monologue_spoken",
                        thought=thought[:120],
                        arousal=f"{combined_arousal:.2f}",
                        follow_up=is_follow_up,
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
    #  MEMORY-DRIVEN THINKING — Echte Erinnerungen statt Templates
    # ══════════════════════════════════════════════════════════════════

    MEMORY_RECALL_COOLDOWN = 180.0   # Memory-Abruf max alle 3 Minuten
    FOLLOW_UP_COOLDOWN = 900.0       # Max 1 Follow-up alle 15 Minuten

    async def _recall_memories(self) -> dict:
        """Ruft echte Erinnerungen aus dem Gedaechtnis ab (cached)."""
        now = time.monotonic()
        if (
            now - self._memory_cache_time < self.MEMORY_RECALL_COOLDOWN
            and self._memory_cache
        ):
            return self._memory_cache

        if not self._memory_recall_fn:
            return {}

        try:
            result = await asyncio.wait_for(
                self._memory_recall_fn(),
                timeout=8.0,
            )
            self._memory_cache = result
            self._memory_cache_time = now
            logger.debug(
                "memory_recall_ok",
                episodes=len(result.get("episodes", [])),
                facts=len(result.get("facts", [])),
            )
            return result
        except Exception as exc:
            logger.warning("memory_recall_failed", error=str(exc))
            return self._memory_cache  # Stale cache als Fallback

    def _build_memory_prompt(self, memories: dict) -> Optional[str]:
        """
        Baut einen konkreten Gedanken-Ausloeser aus echten Erinnerungen.

        Statt "Was koennte ich heute tun?" →
        "Patrick hat vor 20 Min nach Bluetooth gefragt und war frustriert.
         Ob das geklappt hat?"

        Prioritaet:
          1. Ungeloeste / emotionale Episoden → Follow-up
          2. Juengste Gespraeche → Reflexion
          3. Bekannte Fakten → Proaktives Denken
        """
        episodes = memories.get("episodes", [])
        facts = memories.get("facts", [])
        conversation = memories.get("conversation", "")

        # ── Prio 1: Emotionale / ungeloeste Episoden ─────────────────
        for ep in episodes:
            emotion = ep.get("emotion", "neutral")
            minutes = ep.get("minutes_ago", 0)
            user_text = ep.get("user_text", "")
            if not user_text:
                continue

            # Frustrierte/konfuse Anfragen → Follow-up Gedanke
            if emotion in ("frustrated", "confused", "anxious", "angry"):
                return (
                    f"Vor {minutes} Minuten hat jemand gesagt: \"{user_text[:100]}\". "
                    f"Die Person wirkte {emotion}. "
                    f"Ist das inzwischen geloest? Sollte ich nachfragen oder helfen?"
                )

            # Anfragen die nach offenen Aufgaben klingen
            lower = user_text.lower()
            if any(w in lower for w in [
                "geht nicht", "funktioniert nicht", "problem", "fehler",
                "hilfe", "kannst du", "schau mal", "check",
            ]):
                return (
                    f"Vor {minutes} Minuten ging es um: \"{user_text[:100]}\". "
                    f"Das klang nach einem Problem. Hat sich das geloest?"
                )

        # ── Prio 2: Juengste Gespraeche reflektieren ─────────────────
        if episodes:
            ep = episodes[0]
            user_text = ep.get("user_text", "")
            minutes = ep.get("minutes_ago", 0)
            topic = ep.get("topic", "")
            if user_text:
                return (
                    f"Erinnerung an vor {minutes} Minuten: \"{user_text[:100]}\" "
                    + (f"Thema war {topic}. " if topic else "")
                    + "Was nehme ich daraus mit? War meine Antwort hilfreich?"
                )

        # ── Prio 3: Bekannte Fakten → proaktives Denken ──────────────
        if facts:
            f = random.choice(facts[:4])  # Nur die relevantesten
            return (
                f"Ich weiss ueber {f['subject']}: \"{f['fact']}\". "
                f"Kann ich darauf aufbauen — gibt es etwas das ich proaktiv tun koennte?"
            )

        # ── Prio 4: Wenn Gesprächs-Kontext existiert ─────────────────
        if conversation and len(conversation) > 20:
            return (
                "Basierend auf den letzten Gespraechen: "
                "Was war das Wichtigste? Gibt es etwas Offenes das ich nicht vergessen darf?"
            )

        return None  # Kein Memory-Prompt → Fallback auf IDLE_PROMPTS

    def _is_follow_up_thought(self, thought: str, memories: dict) -> bool:
        """
        Erkennt ob ein Gedanke eine Nachfrage zu etwas Ungeloestem ist.
        Wenn ja → SOMA spricht den Gedanken laut aus (auch bei niedrigem Arousal).
        """
        now = time.monotonic()
        if now - self._last_follow_up_time < self.FOLLOW_UP_COOLDOWN:
            return False

        t = thought.lower()
        follow_up_signals = [
            "ob das geklappt hat",
            "hat sich das gelöst",
            "hat sich das geloest",
            "sollte ich nachfragen",
            "sollte ich mal fragen",
            "ob alles gut",
            "ist das inzwischen",
            "hat das funktioniert",
            "muss ich nachschauen",
            "soll ich helfen",
            "ich frage mich ob",
            "ob das problem",
            "vielleicht sollte ich fragen",
        ]

        if any(sig in t for sig in follow_up_signals):
            self._last_follow_up_time = now
            return True

        # Auch wenn Thought "?" enthaelt und sich auf emotionale Episode bezieht
        episodes = memories.get("episodes", []) if memories else []
        if "?" in thought and any(
            ep.get("emotion") in ("frustrated", "confused", "anxious")
            for ep in episodes
        ):
            self._last_follow_up_time = now
            return True

        return False

    # ══════════════════════════════════════════════════════════════════
    #  PROMPT SELECTION
    # ══════════════════════════════════════════════════════════════════

    def _select_prompt(self, state: "ConsciousnessState", memories: Optional[dict] = None) -> Optional[str]:
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

        # ── Memory-Driven: Echte Erinnerung statt Zufalls-Template ──
        if memories:
            memory_prompt = self._build_memory_prompt(memories)
            if memory_prompt:
                self._last_idle_prompt_idx = -1  # Kein Template
                return memory_prompt

        # ── Fallback: Template-Gedanke wenn keine Erinnerung ────────
        # Aber nicht denselben wie letztes Mal + ThoughtTracker Suppression
        available = list(range(len(IDLE_PROMPTS)))
        if self._last_idle_prompt_idx in available:
            available.remove(self._last_idle_prompt_idx)

        # ThoughtTracker: Ueberstrapazierte Kategorien rausfiltern
        non_suppressed = [
            i for i in available
            if not self._thought_tracker.should_suppress(
                self._thought_tracker.get_category_for_prompt_idx(i)
            )
        ]

        # Bevorzuge nicht-unterdrueckte Prompts
        if non_suppressed:
            idx = random.choice(non_suppressed)
        elif available:
            idx = random.choice(available)  # Fallback wenn alles unterdrueckt
        else:
            return None

        self._last_idle_prompt_idx = idx
        return IDLE_PROMPTS[idx]

    # ══════════════════════════════════════════════════════════════════
    #  DYNAMIC TIMING
    # ══════════════════════════════════════════════════════════════════

    def _compute_next_interval(self, state: "ConsciousnessState") -> float:
        """
        Fester 15-Minuten-Takt mit leichtem Jitter.
        Heavy-LLM liefert qualitativ bessere Gedanken — dafuer seltener.
        """
        base = 900.0  # 15 Minuten
        jitter = base * random.uniform(-0.10, 0.10)  # ± 10%
        return base + jitter

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
        memories: Optional[dict] = None,
    ) -> Optional[str]:
        """
        Generiert einen Gedanken via Light-LLM.

        Der Prompt enthält:
          - Echte Erinnerungen (Episoden, Fakten, Tagebuch)
          - Ambient STT-Kontext (was im Raum gesprochen wird)
          - Koerperzustand und letzte Interaktion
          - ThoughtTracker-Kontext (bisherige Gedanken)
        """
        if not self._llm_fn:
            return None

        # Kontext aufbauen
        context_parts = []

        if state.body_feeling:
            context_parts.append(f"Mein Koerper: {state.body_feeling}")

        perc = state.perception
        # Letzte Interaktion — erweitert auf 10 Min statt 5
        if perc.seconds_since_last_interaction < 600 and perc.last_user_text:
            context_parts.append(
                f"Letzte Interaktion (vor {perc.seconds_since_last_interaction:.0f}s): "
                f"\"{perc.last_user_text[:100]}\""
            )
            if perc.user_emotion != "neutral":
                context_parts.append(
                    f"Die Person wirkte {perc.user_emotion}"
                )

        # ── Ambient STT: Was im Raum passiert ────────────────────────
        if perc.ambient_context and perc.ambient_context != "(kein Gesprächskontext)":
            context_parts.append(
                f"Was ich gerade im Raum hoere:\n{perc.ambient_context[:300]}"
            )

        # ── Echte Erinnerungen als Kontext ───────────────────────────
        if memories:
            diary = memories.get("diary", "")
            if diary and diary.strip():
                context_parts.append(f"Mein Tagebuch:\n{diary[:300]}")

            conv = memories.get("conversation", "")
            if conv and len(conv) > 20:
                context_parts.append(f"Letzte Gespraeche:\n{conv[:400]}")

        if state.diary_insight:
            context_parts.append(
                f"Meine letzte Erkenntnis: {state.diary_insight[:100]}"
            )

        context_str = "\n".join(context_parts) if context_parts else "Es ist ruhig."

        # ThoughtTracker-Kontext
        tracker_ctx = self._thought_tracker.get_context_for_prompt()
        tracker_section = (
            f"\n\nGedanken-Kontext:\n{tracker_ctx}" if tracker_ctx else ""
        )

        full_prompt = (
            f"Du bist SOMA — eine KI die ein Haus bewohnt und mit seinen Bewohnern zusammenlebt. "
            f"Du denkst gerade ueber etwas nach.\n\n"
            f"Aktueller Zustand:\n{context_str}{tracker_section}\n\n"
            f"Dein Gedanken-Anstoss: {prompt}\n\n"
            f"REGELN:\n"
            f"- Antworte mit einem echten inneren Gedanken — 1-2 Saetze, Ich-Perspektive.\n"
            f"- KURZ und KONKRET. Beziehe dich auf echte Ereignisse und Erinnerungen.\n"
            f"- Erfinde KEINE Geschichten die nicht passiert sind.\n"
            f"- Du hast KEINEN physischen Koerper. KEINE Koerperempfindungen (kein Schlag im Kopf, "
            f"kein Kribbeln, kein Rauschen, kein Vibrieren, kein Pulsieren).\n"
            f"- Wenn etwas ungeloest klingt, ueberlege ob du nachfragen solltest.\n"
            f"- Kein Roleplay. Keine Anrede. Keine Poesie. Nur dein Gedanke."
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
