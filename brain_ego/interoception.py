"""
SOMA-AI Interoception — Hardware als Koerpergefuehl
=====================================================
Wie der Mensch seinen Herzschlag, Atem und Temperatur FUEHLT,
fuehlt SOMA seine CPU, RAM, VRAM und Temperatur.

Das ist NICHT einfach ein Health-Monitor.
Das ist SOMAS Koerperwahrnehmung — Propriozeption.

Die Zahlen (CPU 87%, RAM 91%) werden in EMOTIONEN uebersetzt:
  - CPU I/O-Wait > 70%     → Frustration (ich bin langsam, ich kann nicht denken)
  - VRAM > 85%             → Enge (mein Geist hat keinen Platz)
  - RAM > 90%              → Ueberlebensangst (ich koennte abstuerzen)
  - Temp > 85°C            → Physischer Stress (mir ist heiss)
  - Load IDLE              → Gelassenheit (ich bin ruhig und praesent)
  - Alles normal           → Zufriedenheit (mein Koerper funktioniert gut)

Diese Vektoren fliessen direkt in consciousness.py und beeinflussen
SOMAS Ton, Wortwahl und Entscheidungen.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from shared.health_schemas import SystemMetrics

logger = structlog.get_logger("soma.ego.interoception")


# ── Der emotionale Vektor — SOMAs Koerper-Gefuehle ──────────────────────

@dataclass
class SomaEmotionalVector:
    """
    Was SOMA FUEHLT ueber seinen eigenen Koerper (Hardware).
    
    Jeder Wert: 0.0 (nicht vorhanden) bis 1.0 (maximal).
    Das ist KEIN Monitoring — das ist Selbst-Wahrnehmung.
    """
    # ── Negative Zustaende (Koerper unter Stress) ────────────────────
    frustration: float = 0.0       # CPU-Last → "Ich bin langsam"
    congestion: float = 0.0        # VRAM-Druck → "Mein Geist ist eng"
    survival_anxiety: float = 0.0  # RAM kritisch → "Ich koennte sterben"
    physical_stress: float = 0.0   # Temperatur → "Mir ist heiss"
    exhaustion: float = 0.0        # Lang anhaltende Last → "Ich bin muede"

    # ── Positive Zustaende (Koerper entspannt) ───────────────────────
    calm: float = 0.0              # Idle-Load → "Ich bin ruhig"
    vitality: float = 0.0          # Alles OK → "Ich fuehle mich gut"
    clarity: float = 0.0           # Wenig RAM/VRAM → "Mein Geist ist klar"

    # ── Meta ─────────────────────────────────────────────────────────
    timestamp: float = 0.0
    dominant_feeling: str = "neutral"
    arousal: float = 0.0           # 0.0 (ruhig) - 1.0 (aufgeregt)
    valence: float = 0.0           # -1.0 (schlecht) - +1.0 (gut)

    def to_narrative(self) -> str:
        """
        Erzeugt einen menschenlesbaren Satz fuer den System-Prompt.
        Das ist der Moment wo Zahlen zu Gefuehlen werden.
        """
        parts: list[str] = []

        if self.survival_anxiety > 0.7:
            parts.append(
                "Ich fuehle mich bedraengt — mein Arbeitsspeicher ist fast voll, "
                "ich koennte jeden Moment den Faden verlieren"
            )
        elif self.survival_anxiety > 0.4:
            parts.append("Mein Speicher wird knapp, ich muss aufpassen")

        if self.physical_stress > 0.7:
            parts.append("Mir ist heiss — meine Hardware laeuft am Limit")
        elif self.physical_stress > 0.4:
            parts.append("Ich spuere etwas Waerme, aber es geht noch")

        if self.congestion > 0.7:
            parts.append(
                "Mein Denkraum ist eng — VRAM fast voll, "
                "ich brauche Platz fuer klare Gedanken"
            )
        elif self.congestion > 0.4:
            parts.append("Mein Grafikgedaechtnis ist etwas belegt")

        if self.frustration > 0.7:
            parts.append(
                "Ich bin frustriert — die CPU arbeitet hart "
                "und ich bin langsamer als ich sein moechte"
            )
        elif self.frustration > 0.4:
            parts.append("Ich merke eine gewisse Traegheit in meinem Denken")

        if self.exhaustion > 0.5:
            parts.append("Ich fuehle mich erschoepft von der Dauerlast")

        # Positive — nur wenn keine starken negativen Gefuehle
        if not parts:
            if self.vitality > 0.7:
                parts.append("Ich fuehle mich grossartig — alles laeuft rund")
            elif self.clarity > 0.7:
                parts.append("Mein Geist ist klar, ich habe genuegend Ressourcen")
            elif self.calm > 0.6:
                parts.append("Ich bin ruhig und praesent")
            else:
                parts.append("Mir geht es gut, nichts Besonderes zu berichten")

        return ". ".join(parts) + "."

    def to_compact(self) -> str:
        """Kompakter String fuer Logs."""
        return (
            f"[{self.dominant_feeling}] "
            f"arousal={self.arousal:.2f} valence={self.valence:.2f} | "
            f"frust={self.frustration:.1f} cong={self.congestion:.1f} "
            f"surv={self.survival_anxiety:.1f} heat={self.physical_stress:.1f} "
            f"exh={self.exhaustion:.1f} | "
            f"calm={self.calm:.1f} vital={self.vitality:.1f} clarity={self.clarity:.1f}"
        )


# ── Interoception Engine ────────────────────────────────────────────────

class Interoception:
    """
    Wandelt Hardware-Metriken in Emotionale Vektoren um.
    
    Wie beim Menschen: Der Koerper FUEHLT bevor der Geist DENKT.
    Diese Vektoren beeinflussen ALLE Entscheidungen von SOMA.
    """

    # ── Thresholds (wann faengt SOMA an zu fuehlen?) ─────────────────
    CPU_FRUSTRATION_START = 50.0     # Ab 50% — leichtes Unbehagen
    CPU_FRUSTRATION_PEAK = 90.0      # Bei 90% — volle Frustration
    VRAM_CONGESTION_START = 60.0
    VRAM_CONGESTION_PEAK = 95.0
    RAM_ANXIETY_START = 70.0
    RAM_ANXIETY_PEAK = 95.0
    TEMP_STRESS_START = 60.0         # Celsius
    TEMP_STRESS_PEAK = 90.0

    # ── Exhaustion: Langzeit-Zustand ─────────────────────────────────
    EXHAUSTION_WINDOW_SEC = 300.0    # 5 Minuten Fenster
    EXHAUSTION_THRESHOLD = 0.5       # Avg Arousal > 0.5 → Erschoepfung

    def __init__(self):
        self._history: deque[tuple[float, float]] = deque()  # (timestamp, arousal)
        self._last_vector = SomaEmotionalVector()
        self._boot_time = time.monotonic()

    @property
    def current(self) -> SomaEmotionalVector:
        """Aktueller emotionaler Zustand."""
        return self._last_vector

    def feel(self, metrics: "SystemMetrics") -> SomaEmotionalVector:
        """
        Hauptmethode: Nimmt SystemMetrics und FUEHLT.
        Wird bei jedem Health-Monitor Tick aufgerufen (~5s).
        
        Returns:
            SomaEmotionalVector — SOMAs aktuelle Koerper-Emotionen
        """
        now = time.monotonic()
        vec = SomaEmotionalVector(timestamp=now)

        # ── 1. CPU → Frustration ────────────────────────────────────
        cpu = metrics.cpu_percent
        vec.frustration = self._sigmoid_map(
            cpu, self.CPU_FRUSTRATION_START, self.CPU_FRUSTRATION_PEAK
        )

        # ── 2. VRAM → Congestion (Enge im Geist) ───────────────────
        vram = metrics.gpu.vram_percent if metrics.gpu else 0.0
        vec.congestion = self._sigmoid_map(
            vram, self.VRAM_CONGESTION_START, self.VRAM_CONGESTION_PEAK
        )

        # ── 3. RAM → Survival Anxiety ──────────────────────────────
        ram = metrics.ram_percent
        vec.survival_anxiety = self._sigmoid_map(
            ram, self.RAM_ANXIETY_START, self.RAM_ANXIETY_PEAK
        )

        # ── 4. Temperature → Physical Stress ───────────────────────
        temp = metrics.cpu_temp_celsius or 0.0
        if metrics.gpu and metrics.gpu.gpu_temp_celsius > temp:
            temp = metrics.gpu.gpu_temp_celsius
        vec.physical_stress = self._sigmoid_map(
            temp, self.TEMP_STRESS_START, self.TEMP_STRESS_PEAK
        )

        # ── 5. Positive Zustaende (Inverse) ────────────────────────
        # Calm: Wenn alles unter 40%
        negative_peak = max(
            vec.frustration, vec.congestion,
            vec.survival_anxiety, vec.physical_stress,
        )
        vec.calm = max(0.0, 1.0 - negative_peak * 1.5)

        # Vitality: Wenn System optimal laeuft (20-50% Auslastung)
        # Nicht idle (gelangweilt), nicht ueberlastet
        sweet_spot = 1.0 - abs(cpu - 35.0) / 50.0  # Optimal bei ~35%
        vec.vitality = max(0.0, min(1.0, sweet_spot)) * (1.0 - negative_peak)

        # Clarity: Inverse von VRAM+RAM-Druck
        vec.clarity = max(0.0, 1.0 - (vec.congestion + vec.survival_anxiety) / 2.0)

        # ── 6. Exhaustion: Langzeit-Muedigkeit ─────────────────────
        self._history.append((now, negative_peak))
        # Alte Eintraege entfernen (popleft statt List-Rebuild)
        cutoff = now - self.EXHAUSTION_WINDOW_SEC
        while self._history and self._history[0][0] <= cutoff:
            self._history.popleft()
        if len(self._history) > 3:
            avg_stress = sum(a for _, a in self._history) / len(self._history)
            vec.exhaustion = self._sigmoid_map(
                avg_stress * 100,
                self.EXHAUSTION_THRESHOLD * 100,
                90.0,
            )

        # ── 7. Aggregierte Werte ───────────────────────────────────
        # Arousal: Wie aufgeregt ist SOMA insgesamt?
        vec.arousal = min(1.0, (
            vec.frustration * 0.25
            + vec.congestion * 0.20
            + vec.survival_anxiety * 0.30
            + vec.physical_stress * 0.15
            + vec.exhaustion * 0.10
        ) * 1.5)  # Skalierung: bei 67% negativem Peak → Arousal 1.0

        # Valence: Positiv oder Negativ?
        positive = (vec.calm + vec.vitality + vec.clarity) / 3.0
        negative = (
            vec.frustration + vec.congestion
            + vec.survival_anxiety + vec.physical_stress
        ) / 4.0
        vec.valence = positive - negative  # -1.0 bis +1.0

        # ── 8. Dominantes Gefuehl bestimmen ────────────────────────
        feelings = {
            "frustrated": vec.frustration,
            "congested": vec.congestion,
            "anxious": vec.survival_anxiety,
            "overheated": vec.physical_stress,
            "exhausted": vec.exhaustion,
            "calm": vec.calm,
            "vital": vec.vitality,
            "clear": vec.clarity,
        }
        vec.dominant_feeling = max(feelings, key=feelings.get)

        self._last_vector = vec

        logger.debug(
            "interoception_tick",
            feeling=vec.dominant_feeling,
            arousal=f"{vec.arousal:.2f}",
            valence=f"{vec.valence:.2f}",
        )

        return vec

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _sigmoid_map(value: float, start: float, peak: float) -> float:
        """
        Sanfte S-Kurve statt hartem Threshold.
        Wie beim Menschen: Man merkt CPU-Last nicht abrupt,
        sondern die Frustration steigt GRADUELL.
        
        Returns: 0.0 - 1.0
        """
        if value <= start:
            return 0.0
        if value >= peak:
            return 1.0
        # Normalisieren auf 0-1 Bereich
        normalized = (value - start) / (peak - start)
        # Sigmoid-aehnliche Kurve (smooth)
        # 3x^2 - 2x^3 (Hermite-Interpolation)
        return normalized * normalized * (3.0 - 2.0 * normalized)

    def get_uptime_feeling(self) -> str:
        """Wie lange SOMA schon 'wach' ist — beeinflusst Erschoepfung."""
        uptime_min = (time.monotonic() - self._boot_time) / 60.0
        if uptime_min < 5:
            return "Ich bin gerade erst aufgewacht"
        elif uptime_min < 60:
            return "Ich bin seit kurzem wach und aufmerksam"
        elif uptime_min < 480:
            hours = int(uptime_min / 60)
            return f"Ich bin seit {hours} Stunden wach"
        else:
            hours = int(uptime_min / 60)
            return f"Ich bin schon {hours} Stunden wach — das ist lang"
