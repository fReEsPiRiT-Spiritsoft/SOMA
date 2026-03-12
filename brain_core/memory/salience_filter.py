"""
Salience Filter — Entscheidet ob ein Event wert ist, gespeichert zu werden.
===========================================================================
Das menschliche Gehirn speichert nicht alles — nur was emotional bedeutsam,
unerwartet oder verhaltensrelevant ist.

SOMA entscheidet anhand von:
  - Emotionaler Arousal (Aufregung, Stress, Freude)
  - State-Change (hat sich etwas Neues ergeben?)
  - Explizite Wichtigkeit (User sagt "merk dir das")
  - Semantische Neuheit (wurde so etwas schon gesagt?)
  - Interaktionslaenge (kurze "ja/nein" → unwichtig)

Regel: Event wird NUR gespeichert wenn salience > THRESHOLD.
"""

from __future__ import annotations

import re
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger("soma.memory.salience")

# ── Thresholds ───────────────────────────────────────────────────────────

SALIENCE_THRESHOLD = 0.35          # Minimum-Score um gespeichert zu werden
HIGH_SALIENCE_THRESHOLD = 0.70     # Ab hier → Diary-Eintrag + hohe Importance
EXPLICIT_MARKERS = [
    "merk dir", "vergiss nicht", "wichtig", "erinner dich",
    "speicher das", "notier dir", "remember", "don't forget",
    "merke", "behalte", "denk dran",
]
TRIVIAL_PATTERNS = [
    r"^(ja|nein|ok|okay|hmm|achso|aha|mhm|gut|klar|alles klar|danke|bitte)[\.\!\?]?$",
    r"^(stop|stopp|abbrechen|cancel)$",
    r"^soma[\.\!\?]?$",
]


@dataclass
class SalienceScore:
    """Ergebnis der Salience-Bewertung."""
    total: float                    # 0.0 - 1.0 Gesamtscore
    emotional_arousal: float = 0.0  # Wie aufgeregt / emotional
    state_change: float = 0.0       # Wie neu / unerwartet
    explicit_importance: float = 0.0 # User hat es explizit als wichtig markiert
    semantic_novelty: float = 0.0   # Wie anders als bisherige Episoden
    interaction_depth: float = 0.0  # Wie tiefgehend die Interaktion
    is_salient: bool = False        # Ueber Threshold?
    is_highly_salient: bool = False # Ueber High-Threshold?
    reason: str = ""                # Menschenlesbare Begruendung


class SalienceFilter:
    """
    Entscheidet ob ein Event ins Langzeitgedaechtnis gehoert.
    Verhindert Memory-Overflow durch unwichtige Interaktionen.
    """

    def __init__(self):
        self._recent_topics: list[str] = []   # Letzte 20 Topics
        self._last_state_hash: str = ""       # Fuer State-Change Detection
        self._eval_count: int = 0

    def evaluate(
        self,
        user_text: str,
        soma_text: str = "",
        emotion: str = "neutral",
        arousal: float = 0.0,
        valence: float = 0.0,
        stress: float = 0.0,
        system_state_delta: Optional[dict] = None,
        query_embedding: Optional[np.ndarray] = None,
        recent_embeddings: Optional[list[np.ndarray]] = None,
    ) -> SalienceScore:
        """
        Bewertet die Salience eines Events.

        Args:
            user_text: Was der User gesagt hat
            soma_text: Was SOMA geantwortet hat
            emotion: Erkannte Emotion als String
            arousal: 0.0-1.0 emotionale Aufregung
            valence: -1.0 bis +1.0 positive/negative Stimmung
            stress: 0.0-1.0 Stress-Level
            system_state_delta: Dict mit geaenderten System-Zustaenden
            query_embedding: Embedding des aktuellen Textes
            recent_embeddings: Embeddings der letzten Episoden

        Returns:
            SalienceScore mit Bewertung
        """
        self._eval_count += 1
        score = SalienceScore(total=0.0)
        reasons: list[str] = []

        # ── 0. Trivial-Check: Sofort rausfiltern ────────────────────
        text_lower = user_text.strip().lower()
        for pattern in TRIVIAL_PATTERNS:
            if re.match(pattern, text_lower):
                score.total = 0.05
                score.reason = "trivial_response"
                return score

        # ── 1. Emotionaler Arousal (Gewicht: 30%) ───────────────────
        emotional_score = 0.0

        # Arousal direkt nutzen
        emotional_score += arousal * 0.5

        # Stress erhoeht Salience
        emotional_score += stress * 0.3

        # Extreme Valence (sehr positiv ODER sehr negativ)
        emotional_score += abs(valence) * 0.2

        # Bestimmte Emotionen sind inherent salient
        high_salience_emotions = {
            "angry", "sad", "stressed", "anxious", "excited",
        }
        if emotion in high_salience_emotions:
            emotional_score = max(emotional_score, 0.5)

        score.emotional_arousal = min(1.0, emotional_score)
        if score.emotional_arousal > 0.4:
            reasons.append(f"emotion:{emotion}({arousal:.1f})")

        # ── 2. Explizite Wichtigkeit (Gewicht: 25%) ─────────────────
        explicit_score = 0.0
        for marker in EXPLICIT_MARKERS:
            if marker in text_lower:
                explicit_score = 1.0
                reasons.append(f"explicit:{marker}")
                break

        # Fragen sind meist wichtiger als Aussagen
        if "?" in user_text:
            explicit_score = max(explicit_score, 0.3)
            reasons.append("question")

        # Lange Texte sind tendenziell wichtiger
        word_count = len(user_text.split())
        if word_count > 20:
            explicit_score = max(explicit_score, 0.4)
            reasons.append(f"long_input({word_count}w)")

        score.explicit_importance = min(1.0, explicit_score)

        # ── 3. State-Change (Gewicht: 20%) ──────────────────────────
        state_score = 0.0
        if system_state_delta:
            changed_keys = [
                k for k, v in system_state_delta.items() if v
            ]
            if changed_keys:
                state_score = min(1.0, len(changed_keys) * 0.3)
                reasons.append(f"state_change:{','.join(changed_keys[:3])}")

        # Topic-Wechsel: Wenn das Thema neu ist
        topic_words = set(text_lower.split()[:5])
        recent_words = set()
        for t in self._recent_topics[-5:]:
            recent_words.update(t.lower().split()[:5])
        overlap = topic_words & recent_words
        if len(overlap) < 2 and len(topic_words) > 2:
            state_score = max(state_score, 0.4)
            reasons.append("new_topic")

        score.state_change = min(1.0, state_score)

        # ── 4. Semantische Neuheit (Gewicht: 15%) ───────────────────
        novelty_score = 0.5  # Default: mittel
        if (
            query_embedding is not None
            and recent_embeddings
            and len(recent_embeddings) > 0
        ):
            # Cosine similarity mit letzten Episoden
            similarities = []
            for emb in recent_embeddings[-10:]:
                if emb is not None:
                    sim = float(np.dot(query_embedding, emb))
                    similarities.append(sim)
            if similarities:
                max_sim = max(similarities)
                # Hohe Aehnlichkeit = niedrige Neuheit
                novelty_score = max(0.0, 1.0 - max_sim)
                if novelty_score > 0.6:
                    reasons.append(f"novel({novelty_score:.1f})")

        score.semantic_novelty = novelty_score

        # ── 5. Interaktionstiefe (Gewicht: 10%) ─────────────────────
        depth_score = 0.0
        combined_len = len(user_text) + len(soma_text)
        if combined_len > 500:
            depth_score = 0.8
        elif combined_len > 200:
            depth_score = 0.5
        elif combined_len > 80:
            depth_score = 0.3

        # Soma hat ausfuehrlich geantwortet → war wohl wichtig
        if len(soma_text) > 200:
            depth_score = max(depth_score, 0.6)

        score.interaction_depth = min(1.0, depth_score)

        # ── Gewichteter Gesamtscore ─────────────────────────────────
        score.total = (
            0.30 * score.emotional_arousal
            + 0.25 * score.explicit_importance
            + 0.20 * score.state_change
            + 0.15 * score.semantic_novelty
            + 0.10 * score.interaction_depth
        )

        # Explicit importance Override: Wenn der User es will, IMMER speichern
        if score.explicit_importance >= 1.0:
            score.total = max(score.total, 0.9)

        score.is_salient = score.total >= SALIENCE_THRESHOLD
        score.is_highly_salient = score.total >= HIGH_SALIENCE_THRESHOLD
        score.reason = " | ".join(reasons) if reasons else "below_threshold"

        # Topic-Tracking aktualisieren
        self._recent_topics.append(user_text[:60])
        if len(self._recent_topics) > 20:
            self._recent_topics.pop(0)

        logger.debug(
            "salience_eval",
            total=round(score.total, 3),
            salient=score.is_salient,
            high=score.is_highly_salient,
            reason=score.reason,
            user_text=user_text[:50],
        )

        return score

    def force_salient(self, reason: str = "forced") -> SalienceScore:
        """
        Erzwingt hohe Salience — fuer Phone-Calls, Interventionen, etc.
        """
        return SalienceScore(
            total=0.95,
            emotional_arousal=0.7,
            explicit_importance=0.9,
            is_salient=True,
            is_highly_salient=True,
            reason=f"forced:{reason}",
        )

    @property
    def stats(self) -> dict:
        return {
            "evaluations": self._eval_count,
            "recent_topics": len(self._recent_topics),
        }
