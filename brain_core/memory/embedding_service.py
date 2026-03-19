"""
Shared Embedding Service — Eliminiert 3 redundante _embed() Methoden.
=====================================================================
VORHER: Jeder Memory-Layer (Episodic, Semantic, Diary) hatte seine eigene
        _embed()-Methode, jede erstellte eine NEUE aiohttp.ClientSession
        pro Call = hunderte TCP-Handshakes pro Stunde.

NACHHER: Ein shared Service mit:
  ✅ Persistenter aiohttp.ClientSession (eine TCP-Verbindung)
  ✅ LRU-Cache (500 Einträge, ~1.5MB) — gleiche Queries = 0ms
  ✅ Verwendet von allen 3 Memory-Layern
"""

from __future__ import annotations

import os
import hashlib
from collections import OrderedDict
from typing import Optional

import numpy as np
import aiohttp
import logging

logger = logging.getLogger("soma.memory.embedding")

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = "nomic-embed-text"
CACHE_MAX_SIZE = 500
TEXT_MAX_LEN = 500  # Ollama embedding truncation


class EmbeddingService:
    """
    Zentraler Embedding-Service für alle Memory-Layer.
    Singleton — wird einmal erstellt, von allen geteilt.
    """

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        # LRU-Cache via OrderedDict (move_to_end bei Zugriff)
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()

    async def initialize(self):
        """Persistente HTTP-Session erstellen."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5),
            )

    async def shutdown(self):
        """Session sauber schließen."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def embed(self, text: str) -> Optional[np.ndarray]:
        """
        Text → 768-dim normalized Embedding.
        Cached — gleiche Queries kosten 0ms.
        """
        if not text or not text.strip():
            return None

        # Cache-Key: MD5 des getrimmten Texts
        trimmed = text[:TEXT_MAX_LEN]
        cache_key = hashlib.md5(trimmed.encode()).hexdigest()

        # LRU-Cache Hit → move to end + return
        if cache_key in self._cache:
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key]

        # Session lazy-init
        if self._session is None or self._session.closed:
            await self.initialize()

        try:
            async with self._session.post(
                f"{OLLAMA_URL}/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": trimmed},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    vec = np.array(data["embedding"], dtype=np.float32)
                    norm = np.linalg.norm(vec)
                    if norm > 0:
                        vec /= norm

                    # LRU-Eviction
                    self._cache[cache_key] = vec
                    if len(self._cache) > CACHE_MAX_SIZE:
                        self._cache.popitem(last=False)  # Ältestes raus

                    return vec
        except Exception as e:
            logger.debug(f"Embedding failed: {e}")

        return None

    @property
    def cache_size(self) -> int:
        return len(self._cache)


# ── Module-Level Singleton ───────────────────────────────────────────
_instance: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    """Gibt den Singleton zurück (erstellt ihn beim ersten Aufruf)."""
    global _instance
    if _instance is None:
        _instance = EmbeddingService()
    return _instance
