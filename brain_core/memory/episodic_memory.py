"""
L2: Episodic Memory — Konkrete Erlebnisse mit Zeitstempel + Embedding.
Gespeichert in SQLite. Vector-Search via numpy Dot-Product.

"Der Nutzer war am Montag gestresst und hat über Arbeit geredet."
"""

from __future__ import annotations

import os
import hashlib
import asyncio
import time
import sqlite3
import logging
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import numpy as np
import aiohttp

logger = logging.getLogger("soma.memory.episodic")

DB_PATH = Path(os.getenv("SOMA_MEMORY_DB", "data/soma_memory.db"))
OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = "nomic-embed-text"   # 768-dim, schnell, lokal via Ollama


# Event-Types fuer strukturiertes Tracking
EVENT_TYPE_CONVERSATION = "conversation"   # Normales Gespraech
EVENT_TYPE_PHONE_CALL = "phone_call"       # Telefon-Session
EVENT_TYPE_PLUGIN = "plugin"               # Plugin-Generierung/Ausfuehrung
EVENT_TYPE_INTERVENTION = "intervention"   # Ambient-Intervention (Streit, Stress)
EVENT_TYPE_REMINDER = "reminder"           # Erinnerung ausgeloest
EVENT_TYPE_SYSTEM = "system"               # System-Events (Boot, Fehler)
EVENT_TYPE_AUTONOMOUS = "autonomous"       # SOMA hat von selbst gesprochen


@dataclass
class Episode:
    id: int
    timestamp: float
    user_text: str
    soma_text: str
    emotion: str
    arousal: float = 0.0
    valence: float = 0.0
    event_type: str = EVENT_TYPE_CONVERSATION
    topic: str = ""
    summary: str = ""
    embedding: Optional[np.ndarray] = None
    relevance: float = 0.0


class EpisodicMemory:
    """
    Speichert jede Interaktion als Episode mit Embedding.
    Retrieval via Cosine-Similarity (normalisierte Vektoren → Dot-Product).
    """

    def __init__(self):
        self._db_path = DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._embed_cache: dict[str, np.ndarray] = {}
        self._ready = False

    async def initialize(self):
        """DB erstellen, Embedding-Model warmup."""
        loop = asyncio.get_event_loop()
        self._conn = await loop.run_in_executor(None, self._open_db)
        try:
            await self._embed("warmup")
            self._ready = True
            logger.info("Episodic memory ready (nomic-embed-text loaded)")
        except Exception as e:
            logger.warning(
                f"Embedding model not available: {e}. "
                "Episodic memory runs in degraded mode (keyword fallback)."
            )
            self._ready = True

    def _open_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   REAL NOT NULL,
                user_text   TEXT NOT NULL,
                soma_text   TEXT DEFAULT '',
                emotion     TEXT DEFAULT 'neutral',
                arousal     REAL DEFAULT 0.0,
                valence     REAL DEFAULT 0.0,
                event_type  TEXT DEFAULT 'conversation',
                topic       TEXT DEFAULT '',
                summary     TEXT DEFAULT '',
                embedding   BLOB,
                importance  REAL DEFAULT 0.5
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_episodes_time
            ON episodes(timestamp DESC)
        """)
        # ── Schema-Migration: Spalten nachtraeglich hinzufuegen ──
        self._migrate_columns(conn)
        # Index fuer event_type NACH Migration erstellen
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_episodes_event_type
            ON episodes(event_type)
        """)
        conn.commit()
        return conn

    @staticmethod
    def _migrate_columns(conn):
        """Fuegt neue Spalten hinzu falls sie fehlen (bestehende DB)."""
        existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(episodes)").fetchall()
        }
        migrations = [
            ("arousal", "REAL DEFAULT 0.0"),
            ("valence", "REAL DEFAULT 0.0"),
            ("event_type", "TEXT DEFAULT 'conversation'"),
        ]
        for col_name, col_def in migrations:
            if col_name not in existing:
                conn.execute(
                    f"ALTER TABLE episodes ADD COLUMN {col_name} {col_def}"
                )
                logging.getLogger("soma.memory.episodic").info(
                    f"Migrated episodes table: added {col_name}"
                )

    # ── Embedding via Ollama ─────────────────────────────────────────

    async def _embed(self, text: str) -> Optional[np.ndarray]:
        """Lokales Embedding via Ollama — ~10ms pro Call."""
        cache_key = hashlib.md5(text[:500].encode()).hexdigest()
        if cache_key in self._embed_cache:
            return self._embed_cache[cache_key]

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{OLLAMA_URL}/api/embeddings",
                    json={"model": EMBED_MODEL, "prompt": text[:500]},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        vec = np.array(data["embedding"], dtype=np.float32)
                        norm = np.linalg.norm(vec)
                        if norm > 0:
                            vec /= norm
                        self._embed_cache[cache_key] = vec
                        if len(self._embed_cache) > 500:
                            oldest = next(iter(self._embed_cache))
                            del self._embed_cache[oldest]
                        return vec
        except Exception as e:
            logger.debug(f"Embedding failed: {e}")
        return None

    # ── Store ────────────────────────────────────────────────────────

    async def store_episode(
        self,
        user_text: str,
        soma_text: str,
        emotion: str = "neutral",
        arousal: float = 0.0,
        valence: float = 0.0,
        event_type: str = EVENT_TYPE_CONVERSATION,
        topic: str = "",
        summary: str = "",
        importance: float = 0.5,
    ):
        """Speichert eine Interaktion. Embedding wird async berechnet."""
        combined = f"User: {user_text}\nSOMA: {soma_text[:300]}"
        if topic:
            combined = f"[{topic}] {combined}"

        embedding = await self._embed(combined)
        blob = embedding.tobytes() if embedding is not None else None

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._insert, {
            "timestamp": time.time(),
            "user_text": user_text,
            "soma_text": soma_text[:2000],
            "emotion": emotion,
            "arousal": arousal,
            "valence": valence,
            "event_type": event_type,
            "topic": topic,
            "summary": summary or user_text[:200],
            "embedding": blob,
            "importance": importance,
        })

    def _insert(self, row: dict):
        if not self._conn:
            return
        self._conn.execute("""
            INSERT INTO episodes
                (timestamp, user_text, soma_text, emotion, arousal, valence,
                 event_type, topic, summary, embedding, importance)
            VALUES
                (:timestamp, :user_text, :soma_text, :emotion, :arousal, :valence,
                 :event_type, :topic, :summary, :embedding, :importance)
        """, row)
        self._conn.commit()

    # ── Retrieve ─────────────────────────────────────────────────────

    async def recall(
        self,
        query: str,
        top_k: int = 5,
        max_age_hours: float = 0,
    ) -> list[Episode]:
        """
        Top-K relevanteste Episoden für eine Frage.
        Hybrid: 70% Semantik + 20% Recency + 10% Importance.
        """
        query_vec = await self._embed(query) if query else None

        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(
            None, self._fetch_candidates, max_age_hours
        )
        if not rows:
            return []

        episodes: list[Episode] = []
        now = time.time()

        for row in rows:
            ep = Episode(
                id=row[0], timestamp=row[1],
                user_text=row[2], soma_text=row[3],
                emotion=row[4],
                arousal=row[5] if row[5] else 0.0,
                valence=row[6] if row[6] else 0.0,
                event_type=row[7] or "conversation",
                topic=row[8] or "", summary=row[9] or "",
                embedding=(
                    np.frombuffer(row[10], dtype=np.float32) if row[10] else None
                ),
            )
            # Score: 70% Semantik + 20% Recency + 10% Importance
            sem_score = 0.0
            if query_vec is not None and ep.embedding is not None:
                sem_score = max(0.0, float(np.dot(query_vec, ep.embedding)))

            age_hours = (now - ep.timestamp) / 3600
            recency_score = 2.0 ** (-age_hours / 24.0)   # Halbwertszeit 24h

            importance = row[11] if len(row) > 11 else 0.5
            ep.relevance = (
                0.70 * sem_score
                + 0.20 * recency_score
                + 0.10 * importance
            )
            episodes.append(ep)

        episodes.sort(key=lambda e: e.relevance, reverse=True)
        return episodes[:top_k]

    def _fetch_candidates(self, max_age_hours: float) -> list:
        if not self._conn:
            return []
        cols = (
            "id, timestamp, user_text, soma_text, emotion, "
            "arousal, valence, event_type, "
            "topic, summary, embedding, importance"
        )
        if max_age_hours > 0:
            cutoff = time.time() - (max_age_hours * 3600)
            return self._conn.execute(
                f"SELECT {cols} "
                "FROM episodes WHERE timestamp > ? "
                "ORDER BY timestamp DESC LIMIT 200",
                (cutoff,),
            ).fetchall()
        return self._conn.execute(
            f"SELECT {cols} "
            "FROM episodes ORDER BY timestamp DESC LIMIT 200",
        ).fetchall()

    # ── Stats ────────────────────────────────────────────────────────

    async def get_stats(self) -> dict:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._stats)

    def _stats(self) -> dict:
        if not self._conn:
            return {"total_episodes": 0}
        row = self._conn.execute(
            "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM episodes"
        ).fetchone()
        return {
            "total_episodes": row[0],
            "oldest_timestamp": row[1],
            "newest_timestamp": row[2],
        }
