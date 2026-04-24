"""
Auto Memory Extraction — SOMA lernt automatisch aus Gesprächen.
================================================================
Inspiriert von Claude Code's extractMemories.ts:
Nach jeder Konversation werden automatisch wichtige Informationen
als persistente Memories gespeichert.

Architektur:
  after_response() hook
       │
       ├─ Genug neue Messages seit letzter Extraktion?
       │     └─ Nein → Skip
       │
       ├─ Hat User explizit "merk dir..." gesagt?
       │     └─ Ja → Sofort extrahieren
       │
       └─ SideQuery analysiert neue Messages
             │
             ├─ User-Präferenz erkannt? → Memory speichern
             ├─ Wichtiger Fakt? → Memory speichern
             └─ Nichts Relevantes → Skip

Memory-Typen:
  - user: User-Präferenzen und Workflow
  - project: Haushalt/System-spezifische Konventionen
  - system: Technische Umgebung
  - fact: Fakten über den User oder Haushalt

Non-negotiable:
  - Blockiert NIE den Response-Flow (Fire-and-forget)
  - Nutzt Light-Modell (SideQuery) → kein VRAM-Kampf mit Heavy
  - Max 5 Memories pro Extraktion
  - Duplikate werden erkannt und übersprungen
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

import structlog

logger = structlog.get_logger("soma.memory.auto_extract")

# ── Konfiguration ────────────────────────────────────────────────────────

MIN_MESSAGES_BETWEEN_EXTRACTIONS: int = 6    # Mindestens 6 neue Messages (3 Turns)
MIN_SECONDS_BETWEEN_EXTRACTIONS: float = 120.0  # Mindestens 2 Min zwischen Extraktionen
MAX_MEMORIES_PER_EXTRACTION: int = 5


# ── Extraction Prompt ────────────────────────────────────────────────────

EXTRACT_SYSTEM_PROMPT = """Du bist der Memory-Extraktions-Agent von SOMA.
Analysiere die letzten Konversations-Messages und extrahiere WICHTIGE Informationen.

Extrahiere NUR:
1. PERSÖNLICHE DATEN (HÖCHSTE PRIORITÄT!): Name, Alter, Geburtstag, Familienmitglieder, Beziehungsstatus, Haustiere, Beruf, Wohnort, Allergien, Gesundheit
2. FAKTEN ÜBER ANDERE PERSONEN: Lieblingslied, Lieblingsessen, Hobbys, Vorlieben von Familienmitgliedern, Freunden, Bekannten
3. User-Präferenzen (z.B. Licht-Helligkeit, Weckzeiten, Musikgeschmack)
4. Haushalt-Fakten (z.B. Geräte, Räume, Ausstattung)
5. Routinen (z.B. Weckzeit, Schlafenszeit)
6. Technische Vorlieben (z.B. Browser, Tools)
7. Explizite Aufforderungen ("Merk dir...", "Vergiss nicht...")

NICHT extrahieren:
- Smalltalk ohne Informationsgehalt
- Bereits bekannte Informationen
- Temporäre Zustände ("Mir ist gerade kalt")
- Befehls-Details ("User hat Licht angemacht")
- KEINE Koerperempfindungen oder Emotionen von SOMA selbst

WICHTIG — KOMPAKTFORMAT:
Speichere als kurze Key = Value Paare, NICHT als ganze Saetze!

WICHTIG — PERSONEN-ZUORDNUNG:
Wenn es um eine ANDERE Person geht (Mutter, Schwester, Freund etc.),
verwende deren NAMEN als @Subject. NICHT "Owner"!
Verwende "Owner" NUR für den sprechenden Hauptnutzer selbst.

Format deiner Antwort (GENAU eine Zeile pro Memory):
TYPE|@Subject|Key = Value

Typen: user, relationship, person, project, system, fact
@Subject: Name der Person (Owner, Sarah, Katrin, Max, etc.)

Beispiele:
user|@Owner|Lieblingslicht = gedimmt am Abend
user|@Owner|Name = Max
user|@Owner|Alter = 28
user|@Owner|Geburtstag = 15.03.1998
relationship|@Owner|Schwester = Anna
relationship|@Owner|Hund = Bello
person|@Anna|Lieblingslied = Bohemian Rhapsody
person|@Anna|Alter = 25
person|@Anna|Hobby = Malen
person|@Katrin|Lieblingsessen = Spaghetti
fact|@Haushalt|Kueche = Philips Hue Lampen
project|@Haushalt|Anlage Kueche = Phantom
system|@System|Browser = Firefox

Wenn nichts zu extrahieren ist, antworte mit: KEINE"""


@dataclass
class ExtractedMemory:
    """Eine extrahierte Memory."""
    memory_type: str  # user, relationship, person, project, system, fact
    content: str
    subject: str = "Owner"  # Ziel-Person: Owner, Sarah, Katrin, etc.
    confidence: float = 0.8
    timestamp: float = field(default_factory=time.time)


class MemoryExtractor:
    """
    Automatische Memory-Extraktion aus Konversationen.
    Nutzt SideQuery (Light-Modell) für schnelle Analyse.
    """

    def __init__(self):
        self._last_extraction_time: float = 0.0
        self._last_message_count: int = 0
        self._total_extractions: int = 0
        self._total_memories_saved: int = 0
        self._running: bool = False

    async def maybe_extract(
        self,
        messages: list[dict],
        side_query_engine=None,
        memory_orchestrator=None,
        force: bool = False,
    ) -> list[ExtractedMemory]:
        """
        Prüfe ob Extraktion nötig ist und führe sie ggf. durch.
        Fire-and-forget — blockiert nie den Response-Flow.

        Args:
            messages: Liste von {role, text} dicts (Working Memory Turns)
            side_query_engine: SideQueryEngine für Analyse
            memory_orchestrator: MemoryOrchestrator zum Speichern
            force: Extraktion erzwingen (z.B. bei "merk dir...")

        Returns:
            Liste extrahierter Memories (leer wenn nichts extrahiert)
        """
        if self._running:
            return []

        # Gate checks
        current_count = len(messages)
        new_messages = current_count - self._last_message_count

        if not force:
            if new_messages < MIN_MESSAGES_BETWEEN_EXTRACTIONS:
                return []
            if time.time() - self._last_extraction_time < MIN_SECONDS_BETWEEN_EXTRACTIONS:
                return []

        if not side_query_engine:
            return []

        self._running = True
        try:
            extracted = await self._extract(messages, side_query_engine)

            if extracted and memory_orchestrator:
                await self._save_memories(extracted, memory_orchestrator)

            self._last_extraction_time = time.time()
            self._last_message_count = current_count
            self._total_extractions += 1

            return extracted

        except Exception as exc:
            logger.warning("memory_extraction_error", error=str(exc))
            return []
        finally:
            self._running = False

    async def _extract(
        self,
        messages: list[dict],
        side_query_engine,
    ) -> list[ExtractedMemory]:
        """Analysiere Messages und extrahiere Memories."""
        # Letzte Messages als Text
        recent = messages[-MIN_MESSAGES_BETWEEN_EXTRACTIONS * 2:]
        text_parts = []
        for msg in recent:
            role = msg.get("role", "?")
            text = msg.get("text", str(msg))
            prefix = "User" if role == "user" else "SOMA"
            text_parts.append(f"{prefix}: {text}")

        conversation = "\n".join(text_parts)

        result = await side_query_engine.query(
            system=EXTRACT_SYSTEM_PROMPT,
            user_message=conversation,
            max_tokens=512,
            temperature=0.2,
        )

        if not result.success or "KEINE" in result.text.upper():
            return []

        # Antwort parsen — Neues Format: TYPE|@Subject|Key = Value
        # Fallback: TYPE|Key = Value (altes Format)
        memories = []
        for line in result.text.strip().split("\n"):
            line = line.strip()
            if "|" not in line:
                continue
            parts = line.split("|")

            if len(parts) == 3:
                # Neues Format: TYPE|@Subject|Key = Value
                mem_type = parts[0].strip().lower()
                raw_subject = parts[1].strip()
                content = parts[2].strip()
                # @Subject → Subject
                subject = raw_subject.lstrip("@").strip()
            elif len(parts) == 2:
                # Altes Format: TYPE|Key = Value
                mem_type = parts[0].strip().lower()
                content = parts[1].strip()
                subject = "Owner"
            else:
                continue

            if mem_type not in ("user", "relationship", "person", "project", "system", "fact"):
                continue
            if len(content) < 5:
                continue
            # Sanitize subject
            if not subject or subject.lower() in ("", "none", "null"):
                subject = "Owner"

            memories.append(ExtractedMemory(
                memory_type=mem_type,
                content=content,
                subject=subject,
            ))

        return memories[:MAX_MEMORIES_PER_EXTRACTION]

    async def _save_memories(
        self,
        memories: list[ExtractedMemory],
        memory_orchestrator,
    ) -> None:
        """Speichere extrahierte Memories als strukturierte Fakten."""
        for mem in memories:
            try:
                # Subject kommt direkt aus der Extraktion (Person-aware!)
                subject = mem.subject
                # Fallback-Map nur wenn kein explizites Subject
                if subject == "Owner" and mem.memory_type in ("project", "system", "fact"):
                    subject_map = {
                        "project": "Haushalt",
                        "system": "System",
                        "fact": "Haushalt",
                    }
                    subject = subject_map.get(mem.memory_type, subject)

                # Type "person" → category "user" (gleiche Tabelle, anderes Subject)
                category = mem.memory_type
                if category == "person":
                    category = "user"

                fact_text = mem.content

                # Direkt als strukturierten Fakt speichern (learn_fact → UPSERT)
                if hasattr(memory_orchestrator, "semantic") and memory_orchestrator.semantic:
                    sem = memory_orchestrator.semantic
                    if hasattr(sem, "learn_fact"):
                        await sem.learn_fact(
                            category=category,
                            subject=subject,
                            fact=fact_text,
                            confidence=0.8,
                        )
                        self._total_memories_saved += 1
                        logger.info(
                            "memory_auto_extracted",
                            type=mem.memory_type,
                            subject=subject,
                            content=fact_text[:80],
                        )
                        continue

                # Fallback: alte Methode
                if hasattr(memory_orchestrator, "store_semantic"):
                    await memory_orchestrator.store_semantic(
                        text=fact_text,
                        metadata={
                            "type": mem.memory_type,
                            "source": "auto_extract",
                            "timestamp": mem.timestamp,
                        },
                    )
                elif hasattr(memory_orchestrator, "semantic") and memory_orchestrator.semantic:
                    await memory_orchestrator.semantic.store(
                        text=fact_text,
                        emotion="neutral",
                        importance=0.7,
                    )

                self._total_memories_saved += 1
                logger.info(
                    "memory_auto_extracted",
                    type=mem.memory_type,
                    content=fact_text[:80],
                )

            except Exception as exc:
                logger.warning(
                    "memory_save_error",
                    content=mem.content[:50],
                    error=str(exc),
                )

    def check_explicit_remember(self, user_text: str) -> bool:
        """Erkennt explizite 'merk dir' Aufforderungen."""
        triggers = [
            "merk dir", "merke dir", "vergiss nicht",
            "remember", "denk dran", "denke daran",
            "speicher", "notier", "merken",
        ]
        lower = user_text.lower()
        return any(t in lower for t in triggers)

    @property
    def stats(self) -> dict:
        return {
            "total_extractions": self._total_extractions,
            "total_memories_saved": self._total_memories_saved,
            "last_extraction": self._last_extraction_time,
        }


# ── Module-Level Singleton ───────────────────────────────────────────────

_extractor: Optional[MemoryExtractor] = None


def get_memory_extractor() -> MemoryExtractor:
    global _extractor
    if _extractor is None:
        _extractor = MemoryExtractor()
    return _extractor
