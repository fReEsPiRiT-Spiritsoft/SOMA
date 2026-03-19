"""
L3: Semantic Memory — Abstrahierte Fakten über die Welt.
Fakten wie "Der Nutzer trinkt morgens Kaffee" werden durch
Background-Consolidation aus Episoden destilliert.
"""

from __future__ import annotations

import os
import time
import sqlite3
import asyncio
import logging
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import numpy as np

from brain_core.memory.embedding_service import get_embedding_service

logger = logging.getLogger("soma.memory.semantic")

DB_PATH = Path(os.getenv("SOMA_MEMORY_DB", "data/soma_memory.db"))
OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = "nomic-embed-text"


@dataclass
class Fact:
    id: int
    category: str    # preference, habit, relationship, knowledge, personality
    subject: str     # Nutzername, "SOMA", "Wohnzimmer"
    fact: str        # "trinkt morgens Kaffee"
    confidence: float
    source_count: int
    last_confirmed: float
    embedding: Optional[np.ndarray] = None
    relevance: float = 0.0


class SemanticMemory:
    """
    Langzeit-Faktenspeicher.  Nicht bei jedem Turn geschrieben —
    sondern durch Background-Consolidation aus Episoden destilliert.
    """

    def __init__(self):
        self._db_path = DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        # TTL-Cache fuer haeufig abgefragte Daten (60s)
        self._prefs_cache: Optional[list] = None
        self._prefs_ts: float = 0.0
        self._personality_cache: dict[str, str] = {}
        self._personality_ts: dict[str, float] = {}
        self._CACHE_TTL = 60.0  # Sekunden

    async def initialize(self):
        loop = asyncio.get_event_loop()
        self._conn = await loop.run_in_executor(None, self._open_db)
        logger.info("Semantic memory ready")

    def _open_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                category        TEXT NOT NULL,
                subject         TEXT NOT NULL,
                fact            TEXT NOT NULL,
                confidence      REAL DEFAULT 0.5,
                source_count    INTEGER DEFAULT 1,
                first_learned   REAL NOT NULL,
                last_confirmed  REAL NOT NULL,
                embedding       BLOB,
                UNIQUE(subject, fact)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category)"
        )
        conn.commit()
        return conn

    # ── Embedding ────────────────────────────────────────────────────

    async def _embed(self, text: str) -> Optional[np.ndarray]:
        """Embedding via shared EmbeddingService (persistent session + LRU cache)."""
        return await get_embedding_service().embed(text)

    # ── Store / Reinforce ────────────────────────────────────────────

    async def learn_fact(
        self,
        category: str,
        subject: str,
        fact: str,
        confidence: float = 0.6,
    ):
        """Speichert oder verstärkt einen Fakt (UPSERT)."""
        embedding = await self._embed(f"{subject}: {fact}")
        blob = embedding.tobytes() if embedding is not None else None
        now = time.time()

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self._upsert_fact, category, subject, fact, confidence, blob, now,
        )
        # Cache invalidieren bei neuen Fakten
        self._prefs_cache = None
        self._personality_cache.pop(subject, None)

    def _upsert_fact(self, category, subject, fact, confidence, blob, now):
        if not self._conn:
            return
        cur = self._conn.execute(
            "UPDATE facts SET "
            "confidence = MIN(1.0, confidence + 0.1), "
            "source_count = source_count + 1, "
            "last_confirmed = ?, "
            "embedding = COALESCE(?, embedding) "
            "WHERE subject = ? AND fact = ?",
            (now, blob, subject, fact),
        )
        if cur.rowcount == 0:
            self._conn.execute(
                "INSERT INTO facts "
                "(category, subject, fact, confidence, source_count, "
                " first_learned, last_confirmed, embedding) "
                "VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
                (category, subject, fact, confidence, now, now, blob),
            )
        self._conn.commit()

    # ── Retrieve ─────────────────────────────────────────────────────

    async def recall_facts(
        self,
        query: str,
        subject: Optional[str] = None,
        top_k: int = 8,
    ) -> list[Fact]:
        """Hybrid: Embedding-Similarity + Confidence + Recency."""
        query_vec = await self._embed(query) if query else None

        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(None, self._fetch_facts, subject)

        facts: list[Fact] = []
        for row in rows:
            f = Fact(
                id=row[0], category=row[1], subject=row[2], fact=row[3],
                confidence=row[4], source_count=row[5], last_confirmed=row[6],
                embedding=(
                    np.frombuffer(row[7], dtype=np.float32) if row[7] else None
                ),
            )
            sem = 0.0
            if query_vec is not None and f.embedding is not None:
                sem = max(0.0, float(np.dot(query_vec, f.embedding)))
            age_days = (time.time() - f.last_confirmed) / 86400
            recency = 2.0 ** (-age_days / 30.0)  # Halbwertszeit 30 Tage
            f.relevance = 0.60 * sem + 0.25 * f.confidence + 0.15 * recency
            facts.append(f)

        facts.sort(key=lambda f: f.relevance, reverse=True)
        return facts[:top_k]

    def _fetch_facts(self, subject: Optional[str]) -> list:
        if not self._conn:
            return []
        if subject:
            return self._conn.execute(
                "SELECT id, category, subject, fact, confidence, "
                "source_count, last_confirmed, embedding "
                "FROM facts WHERE subject = ? "
                "ORDER BY confidence DESC LIMIT 100",
                (subject,),
            ).fetchall()
        return self._conn.execute(
            "SELECT id, category, subject, fact, confidence, "
            "source_count, last_confirmed, embedding "
            "FROM facts ORDER BY confidence DESC LIMIT 100",
        ).fetchall()

    def _fetch_by_category(self, category: str) -> list:
        """Alle Fakten einer bestimmten Kategorie laden (ohne Embedding-BLOBs)."""
        if not self._conn:
            return []
        return self._conn.execute(
            "SELECT id, category, subject, fact, confidence, "
            "source_count, last_confirmed "
            "FROM facts WHERE category = ? "
            "ORDER BY confidence DESC LIMIT 50",
            (category,),
        ).fetchall()

    # ── Personality Snapshot ─────────────────────────────────────────

    async def get_user_preferences(self) -> list[Fact]:
        """Alle Nutzer-Präferenzen — mit 60s TTL-Cache."""
        now = time.time()
        if self._prefs_cache is not None and (now - self._prefs_ts) < self._CACHE_TTL:
            return self._prefs_cache
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(None, self._fetch_by_category, "preference")
        facts = []
        for row in rows:
            facts.append(Fact(
                id=row[0], category=row[1], subject=row[2], fact=row[3],
                confidence=row[4], source_count=row[5], last_confirmed=row[6],
                embedding=None,
            ))
        self._prefs_cache = facts
        self._prefs_ts = now
        return facts

    async def get_personality_snapshot(self, subject: str = "Nutzer") -> str:
        """Alle Fakten über eine Person — mit 60s TTL-Cache pro Subject."""
        now = time.time()
        cached_ts = self._personality_ts.get(subject, 0.0)
        if subject in self._personality_cache and (now - cached_ts) < self._CACHE_TTL:
            return self._personality_cache[subject]
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(None, self._fetch_facts, subject)
        if not rows:
            self._personality_cache[subject] = ""
            self._personality_ts[subject] = now
            return ""
        lines = []
        for row in rows:
            conf = row[4]
            cat = row[1]
            if cat == "preference":
                continue
            if conf >= 0.4:
                lines.append(f"- {row[3]} (Sicherheit: {conf:.0%})")
        result = ""
        if lines:
            result = f"Bekannte Fakten über {subject}:\n" + "\n".join(lines[:20])
        self._personality_cache[subject] = result
        self._personality_ts[subject] = now
        return result
    # ── Category Query & Delete (für Plugins) ─────────────────────────────

    async def get_facts_by_category(self, category: str) -> list["Fact"]:
        """Alle Fakten einer Kategorie — für Plugin-eigene Datenhaltung (z.B. Reminder)."""
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(None, self._fetch_by_category_recent, category)
        facts = []
        for row in rows:
            facts.append(Fact(
                id=row[0], category=row[1], subject=row[2], fact=row[3],
                confidence=row[4], source_count=row[5], last_confirmed=row[6],
                embedding=None,
            ))
        return facts

    def _fetch_by_category_recent(self, category: str) -> list:
        """Fuer Plugins: nach last_confirmed sortiert, ohne BLOBs."""
        if not self._conn:
            return []
        return self._conn.execute(
            "SELECT id, category, subject, fact, confidence, "
            "source_count, last_confirmed "
            "FROM facts WHERE category = ? "
            "ORDER BY last_confirmed DESC LIMIT 200",
            (category,),
        ).fetchall()

    async def forget_fact(self, subject: str, fact: str = None):
        """Löscht einen Fakt — invalidiert Cache."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._delete_fact, subject, fact)
        # Cache invalidieren
        self._prefs_cache = None
        self._personality_cache.pop(subject, None)

    def _delete_fact(self, subject: str, fact_text):
        if not self._conn:
            return
        if fact_text:
            self._conn.execute(
                "DELETE FROM facts WHERE subject = ? AND fact = ?",
                (subject, fact_text),
            )
        else:
            self._conn.execute(
                "DELETE FROM facts WHERE subject = ?",
                (subject,),
            )
        self._conn.commit()
    # ── Stats ────────────────────────────────────────────────────────

    async def get_stats(self) -> dict:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._stats)

    def _stats(self) -> dict:
        if not self._conn:
            return {"total_facts": 0}
        row = self._conn.execute("SELECT COUNT(*) FROM facts").fetchone()
        return {"total_facts": row[0]}
