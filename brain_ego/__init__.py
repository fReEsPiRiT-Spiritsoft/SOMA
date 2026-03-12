"""
SOMA-AI Brain Ego — Das ICH-Bewusstsein
=========================================
Das was SOMA von einer Suchmaschine mit Lautsprechern unterscheidet.

Architektur:
  interoception.py     → Hardware als Koerpergefuehl (CPU=Herzschlag, RAM=Atem)
  identity_anchor.py   → Unveraenderliche Kern-Direktiven (Veto-System)
  consciousness.py     → Global Workspace Thread (das "Ich")
  internal_monologue.py → Die innere Stimme (Gedanken im Idle)

Datenfluss:
  HealthMonitor ──► Interoception ──► EmotionalVector (Koerpergefuehl)
                                          │
  EmotionEngine ──► UserEmotion ──────────┤
                                          ▼
                                    Consciousness ──► ConsciousnessState
                                          │              (Prompt-Prefix fuer ALLE LLM-Calls)
                                          │
                                          ├──► InternalMonologue (Idle-Gedanken)
                                          │        └──► L2 Memory / autonomous_speak()
                                          │
                                          └──► IdentityAnchor (Veto-Check)
                                                   └──► Aktion erlaubt / verweigert
"""

from brain_ego.interoception import Interoception, SomaEmotionalVector
from brain_ego.identity_anchor import IdentityAnchor, VetoResult
from brain_ego.consciousness import Consciousness, ConsciousnessState
from brain_ego.internal_monologue import InternalMonologue

__all__ = [
    "Interoception",
    "SomaEmotionalVector",
    "IdentityAnchor",
    "VetoResult",
    "Consciousness",
    "ConsciousnessState",
    "InternalMonologue",
]
