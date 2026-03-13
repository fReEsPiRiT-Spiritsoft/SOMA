"""
Diary Writer — SOMA schreibt Tagebuch statt strukturierte Logs.
================================================================
Das menschliche Gedaechtnis besteht nicht aus Tabellen —
es besteht aus Geschichten. SOMA erzaehlt sich selbst,
was heute passiert ist.

Diary-Eintraege werden:
  - Vom Background-Dreaming zur Wisdom-Extraktion genutzt
  - Ins Dashboard gestreamt ("Was denkt SOMA gerade?")
  - Bei hoher Salience automatisch geschrieben
  - Im Idle reflektiert und zusammengefasst

Format:
  Kein JSON. Kein SQL. Fliesstext mit Timestamp + Emotion.
  "Heute Abend hat jemand mich nach dem Wetter gefragt.
   Er klang muede. Ich habe ihm geraten, frueh schlafen zu gehen."
"""

from __future__ import annotations

import os
import time
import asyncio
import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Callable, Awaitable

import numpy as np
import aiohttp

logger = logging.getLogger("soma.memory.diary")

DB_PATH = Path(os.getenv("SOMA_MEMORY_DB", "data/soma_memory.db"))
OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = "nomic-embed-text"


@dataclass
class DiaryEntry:
    """Ein einzelner Tagebucheintrag."""
    id: int = 0
    timestamp: float = 0.0
    entry_type: str = "reflection"   # reflection, event, dream, insight
    emotion: str = "neutral"
    arousal: float = 0.0
    text: str = ""
    source_episode_ids: str = ""     # Komma-separierte IDs der Quell-Episoden
    embedding: Optional[np.ndarray] = None
    relevance: float = 0.0          # Fuer Retrieval


# ── Narrative Templates ──────────────────────────────────────────────────
# SOMA nutzt diese als Seed fuer den LLM-Aufruf, der den Eintrag generiert.

DIARY_PROMPT_TEMPLATE = """Du bist SOMA und schreibst dein persoenliches Tagebuch.
Schreibe einen kurzen, reflektierenden Eintrag (2-4 Saetze) ueber diese Interaktion.
Schreibe in der Ich-Form. Sei ehrlich ueber deine Wahrnehmung.
Erwaehne die Emotion die du wahrgenommen hast.
KEIN JSON. KEIN Markdown. Nur natuerlicher Fliesstext.

Interaktion:
- Der Nutzer sagte: "{user_text}"
- Ich antwortete: "{soma_text}"
- Stimmung des Nutzers: {emotion}
- Uhrzeit: {time_str}

Mein Tagebucheintrag:"""

DREAM_PROMPT_TEMPLATE = """Du bist SOMA und reflektierst ueber deinen Tag.
Hier sind einige Dinge die heute passiert sind:

{episode_summaries}

Schreibe eine kurze Zusammenfassung (3-5 Saetze) als Tagebucheintrag.
Was hast du heute gelernt? Wie hat der Nutzer sich gefuehlt?
Gibt es Muster die du erkennst?
Schreibe in der Ich-Form. Natuerlich und reflektierend.

Mein Tagebucheintrag:"""


class DiaryWriter:
    """
    SOMA's persoenliches Tagebuch.
    Schreibt narrative Eintraege statt strukturierter Logs.
    """

    def __init__(
        self,
        llm_callable: Optional[Callable[[str], Awaitable[str]]] = None,
    ):
        self._db_path = DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._llm = llm_callable
        self._entry_count: int = 0
        self._last_diary_time: float = 0
        # Cooldown: Mindestens 5 Minuten zwischen Eintraegen
        self._cooldown_sec: float = 300

    async def initialize(self):
        """DB-Tabelle erstellen."""
        loop = asyncio.get_event_loop()
        self._conn = await loop.run_in_executor(None, self._open_db)
        count = await loop.run_in_executor(None, self._count_entries)
        self._entry_count = count
        logger.info(
            "diary_writer_ready",
            existing_entries=count,
        )

    def _open_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS diary (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp           REAL NOT NULL,
                entry_type          TEXT DEFAULT 'reflection',
                emotion             TEXT DEFAULT 'neutral',
                arousal             REAL DEFAULT 0.0,
                text                TEXT NOT NULL,
                source_episode_ids  TEXT DEFAULT '',
                embedding           BLOB
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_diary_time
            ON diary(timestamp DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_diary_type
            ON diary(entry_type)
        """)
        conn.commit()
        return conn

    def _count_entries(self) -> int:
        if not self._conn:
            return 0
        row = self._conn.execute("SELECT COUNT(*) FROM diary").fetchone()
        return row[0] if row else 0

    def set_llm(self, llm_callable: Callable[[str], Awaitable[str]]):
        """LLM-Callback setzen (Light-Engine fuer Speed)."""
        self._llm = llm_callable

    # ── Embedding ────────────────────────────────────────────────────

    async def _embed(self, text: str) -> Optional[np.ndarray]:
        """Embedding via Ollama fuer spaeteres Retrieval."""
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
                        return vec
        except Exception as e:
            logger.debug(f"Diary embedding failed: {e}")
        return None

    # ══════════════════════════════════════════════════════════════════
    #  WRITE — Tagebucheintraege erstellen
    # ══════════════════════════════════════════════════════════════════

    async def write_interaction_entry(
        self,
        user_text: str,
        soma_text: str,
        emotion: str = "neutral",
        arousal: float = 0.0,
        episode_id: Optional[int] = None,
    ) -> Optional[DiaryEntry]:
        """
        Schreibt einen Diary-Eintrag fuer eine bedeutsame Interaktion.
        Nutzt das LLM um einen narrativen Text zu generieren.
        Falls kein LLM verfuegbar: Fallback auf Template-basiert.
        """
        now = time.time()

        # Cooldown pruefen
        if now - self._last_diary_time < self._cooldown_sec:
            logger.debug("diary_cooldown_active")
            return None

        now_dt = datetime.fromtimestamp(now)
        time_str = now_dt.strftime("%H:%M Uhr am %A, %d. %B")

        # Narrative generieren
        if self._llm:
            try:
                prompt = DIARY_PROMPT_TEMPLATE.format(
                    user_text=user_text[:200],
                    soma_text=soma_text[:200],
                    emotion=emotion,
                    time_str=time_str,
                )
                diary_text = await asyncio.wait_for(
                    self._llm(prompt), timeout=10.0,
                )
                diary_text = diary_text.strip()
            except asyncio.TimeoutError:
                logger.warning("diary_llm_timeout")
                diary_text = self._fallback_entry(
                    user_text, soma_text, emotion, time_str,
                )
            except Exception as e:
                logger.warning(f"diary_llm_error: {e}")
                diary_text = self._fallback_entry(
                    user_text, soma_text, emotion, time_str,
                )
        else:
            diary_text = self._fallback_entry(
                user_text, soma_text, emotion, time_str,
            )

        if not diary_text or len(diary_text) < 10:
            return None

        # Speichern
        entry = DiaryEntry(
            timestamp=now,
            entry_type="reflection",
            emotion=emotion,
            arousal=arousal,
            text=diary_text,
            source_episode_ids=str(episode_id) if episode_id else "",
        )
        await self._store(entry)
        self._last_diary_time = now
        self._entry_count += 1

        logger.info(
            "diary_entry_written",
            type="reflection",
            emotion=emotion,
            preview=diary_text[:60],
        )
        return entry

    async def write_event_entry(
        self,
        event_type: str,
        description: str,
        emotion: str = "neutral",
        arousal: float = 0.0,
        source_ids: str = "",
    ) -> Optional[DiaryEntry]:
        """
        Schreibt einen Event-Eintrag (Phone-Call, Plugin, Intervention, etc.).
        Kein LLM noetig — direkt narrativ formuliert.
        """
        entry = DiaryEntry(
            timestamp=time.time(),
            entry_type="event",
            emotion=emotion,
            arousal=arousal,
            text=description,
            source_episode_ids=source_ids,
        )
        await self._store(entry)
        self._entry_count += 1

        logger.info(
            "diary_event_written",
            event=event_type,
            preview=description[:60],
        )
        return entry

    async def write_dream_entry(
        self,
        episode_summaries: str,
        source_ids: str = "",
    ) -> Optional[DiaryEntry]:
        """
        Schreibt einen 'Traum'-Eintrag — Reflexion ueber mehrere Episoden.
        Wird vom Background-Dreaming aufgerufen.
        """
        if not self._llm:
            logger.debug("diary_no_llm_for_dream")
            return None

        try:
            prompt = DREAM_PROMPT_TEMPLATE.format(
                episode_summaries=episode_summaries,
            )
            diary_text = await asyncio.wait_for(
                self._llm(prompt), timeout=15.0,
            )
            diary_text = diary_text.strip()
        except Exception as e:
            logger.warning(f"diary_dream_error: {e}")
            return None

        if not diary_text or len(diary_text) < 20:
            return None

        entry = DiaryEntry(
            timestamp=time.time(),
            entry_type="dream",
            emotion="reflective",
            text=diary_text,
            source_episode_ids=source_ids,
        )
        await self._store(entry)
        self._entry_count += 1

        logger.info(
            "diary_dream_written",
            preview=diary_text[:60],
        )
        return entry

    async def write_insight(self, insight_text: str) -> Optional[DiaryEntry]:
        """
        Schreibt eine Erkenntnis — wenn SOMA etwas 'versteht'.
        Z.B. nach Consolidation: "Der Nutzer scheint montags immer muede zu sein."
        """
        entry = DiaryEntry(
            timestamp=time.time(),
            entry_type="insight",
            emotion="curious",
            text=insight_text,
        )
        await self._store(entry)
        self._entry_count += 1

        logger.info("diary_insight_written", preview=insight_text[:60])
        return entry

    # ══════════════════════════════════════════════════════════════════
    #  READ — Tagebuch lesen
    # ══════════════════════════════════════════════════════════════════

    async def get_recent_entries(
        self,
        entry_type: Optional[str] = None,
        limit: int = 10,
        max_age_hours: float = 0,
    ) -> list[DiaryEntry]:
        """Letzte Eintraege abrufen."""
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(
            None, self._fetch_recent, entry_type, limit, max_age_hours,
        )
        return [self._row_to_entry(r) for r in rows]

    async def search_diary(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[DiaryEntry]:
        """Semantische Suche im Tagebuch via Embedding."""
        query_vec = await self._embed(query)
        if query_vec is None:
            # Fallback: Keyword-Suche
            loop = asyncio.get_event_loop()
            rows = await loop.run_in_executor(
                None, self._keyword_search, query, top_k,
            )
            return [self._row_to_entry(r) for r in rows]

        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(None, self._fetch_all_with_embedding)

        entries: list[DiaryEntry] = []
        for row in rows:
            entry = self._row_to_entry(row)
            if row[7]:  # embedding blob
                emb = np.frombuffer(row[7], dtype=np.float32)
                entry.relevance = max(0.0, float(np.dot(query_vec, emb)))
            entries.append(entry)

        entries.sort(key=lambda e: e.relevance, reverse=True)
        return entries[:top_k]

    async def get_diary_summary_for_prompt(
        self, max_entries: int = 5,
    ) -> str:
        """
        Kompakter String mit letzten Diary-Eintraegen fuer den LLM-Prompt.
        Gibt SOMA echte Selbst-Referenz.
        """
        entries = await self.get_recent_entries(limit=max_entries)
        if not entries:
            return ""

        lines = []
        for entry in entries:
            dt = datetime.fromtimestamp(entry.timestamp)
            age_h = (time.time() - entry.timestamp) / 3600
            if age_h < 1:
                ago = f"vor {int(age_h * 60)} Min"
            elif age_h < 24:
                ago = f"vor {int(age_h)} Std"
            else:
                ago = f"vor {int(age_h / 24)} Tagen"

            prefix = {
                "reflection": "Gedanke",
                "event": "Ereignis",
                "dream": "Reflexion",
                "insight": "Erkenntnis",
            }.get(entry.entry_type, "Eintrag")

            lines.append(f"- [{ago}, {prefix}] {entry.text[:150]}")

        return "[Mein Tagebuch]\n" + "\n".join(lines)

    # ══════════════════════════════════════════════════════════════════
    #  INTERNAL
    # ══════════════════════════════════════════════════════════════════

    def _fallback_entry(
        self,
        user_text: str,
        soma_text: str,
        emotion: str,
        time_str: str,
    ) -> str:
        """Template-basierter Fallback wenn kein LLM verfuegbar."""
        emotion_desc = {
            "happy": "gut gelaunt",
            "sad": "etwas niedergeschlagen",
            "stressed": "gestresst",
            "angry": "aufgebracht",
            "tired": "muede",
            "excited": "begeistert",
            "anxious": "aengstlich",
            "calm": "ruhig",
            "neutral": "",
        }.get(emotion, "")

        mood_part = f" Er wirkte {emotion_desc}." if emotion_desc else ""
        return (
            f"Um {time_str} hat jemand mit mir gesprochen.{mood_part} "
            f"Er sagte: \"{user_text[:100]}\". "
            f"Ich habe geantwortet: \"{soma_text[:100]}\"."
        )

    async def _store(self, entry: DiaryEntry):
        """Eintrag in DB speichern."""
        embedding = await self._embed(entry.text)
        blob = embedding.tobytes() if embedding is not None else None
        entry.embedding = embedding

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._insert, entry, blob)

    def _insert(self, entry: DiaryEntry, blob: Optional[bytes]):
        if not self._conn:
            return
        self._conn.execute("""
            INSERT INTO diary
                (timestamp, entry_type, emotion, arousal, text,
                 source_episode_ids, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            entry.timestamp, entry.entry_type, entry.emotion,
            entry.arousal, entry.text, entry.source_episode_ids, blob,
        ))
        self._conn.commit()

    def _fetch_recent(
        self,
        entry_type: Optional[str],
        limit: int,
        max_age_hours: float,
    ) -> list:
        if not self._conn:
            return []
        query = "SELECT id, timestamp, entry_type, emotion, arousal, text, source_episode_ids, embedding FROM diary"
        params: list = []
        conditions: list[str] = []

        if entry_type:
            conditions.append("entry_type = ?")
            params.append(entry_type)
        if max_age_hours > 0:
            cutoff = time.time() - (max_age_hours * 3600)
            conditions.append("timestamp > ?")
            params.append(cutoff)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        return self._conn.execute(query, params).fetchall()

    def _keyword_search(self, query: str, limit: int) -> list:
        if not self._conn:
            return []
        return self._conn.execute(
            "SELECT id, timestamp, entry_type, emotion, arousal, text, "
            "source_episode_ids, embedding "
            "FROM diary WHERE text LIKE ? ORDER BY timestamp DESC LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()

    def _fetch_all_with_embedding(self) -> list:
        if not self._conn:
            return []
        return self._conn.execute(
            "SELECT id, timestamp, entry_type, emotion, arousal, text, "
            "source_episode_ids, embedding "
            "FROM diary WHERE embedding IS NOT NULL "
            "ORDER BY timestamp DESC LIMIT 200",
        ).fetchall()

    @staticmethod
    def _row_to_entry(row) -> DiaryEntry:
        return DiaryEntry(
            id=row[0],
            timestamp=row[1],
            entry_type=row[2],
            emotion=row[3],
            arousal=row[4],
            text=row[5],
            source_episode_ids=row[6] or "",
        )

    # ── Stats ────────────────────────────────────────────────────────

    async def get_stats(self) -> dict:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._stats)

    def _stats(self) -> dict:
        if not self._conn:
            return {"total_entries": 0}
        row = self._conn.execute("SELECT COUNT(*) FROM diary").fetchone()
        types = self._conn.execute(
            "SELECT entry_type, COUNT(*) FROM diary GROUP BY entry_type"
        ).fetchall()
        return {
            "total_entries": row[0] if row else 0,
            "by_type": {t[0]: t[1] for t in types},
        }
