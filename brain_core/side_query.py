"""
Side Query Engine — Schnelle Meta-Tasks mit dem Light-Modell.
==============================================================
Inspiriert von Claude Code's sideQuery-Pattern:
Nutzt das kleine, schnelle Modell (Qwen3 1.7B) für Meta-Tasks,
während das große Modell (Qwen3 8B) für User-Antworten reserviert bleibt.

Anwendungsfälle:
  - Memory Relevanz-Auswahl
  - Away-Zusammenfassungen
  - Intent-Klassifikation
  - Permission-Erklärungen
  - Context-Kompression (Auto-Compact)

Architektur:
  SideQueryEngine ─── uses ──► LightPhiEngine (Qwen3 1.7B, permanent im VRAM)
                                    │
                                    ├─ query()           → Freitext-Antwort
                                    ├─ select_memories() → AI-powered Memory-Auswahl
                                    ├─ classify()        → Strukturierte Klassifikation
                                    └─ summarize()       → Komprimierung

Non-negotiable:
  - Max 2s Response-Time (Light-Modell ist schnell)
  - Blockiert NIE den Haupt-Loop
  - Fehler → graceful fallback, kein Crash
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
import structlog

from brain_core.config import settings

logger = structlog.get_logger("soma.side_query")

# ── Timeouts ─────────────────────────────────────────────────────────────
SIDE_QUERY_TIMEOUT_SEC: float = 8.0   # Max Wartezeit
SIDE_QUERY_MAX_TOKENS: int = 512      # Kurze Antworten
SIDE_QUERY_TEMPERATURE: float = 0.3   # Deterministische Meta-Tasks


@dataclass
class SideQueryResult:
    """Ergebnis einer Side Query."""
    text: str
    model: str = ""
    latency_ms: float = 0.0
    success: bool = True
    error: str = ""
    from_cache: bool = False


class SideQueryEngine:
    """
    Lightweight Query Engine für Meta-Tasks.
    Nutzt das Light-Modell (permanent im VRAM) für schnelle Nebenaufgaben.
    """

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._model = settings.ollama_light_model
        self._stats = {"queries": 0, "errors": 0, "avg_ms": 0.0}
        # Simple LRU Cache für häufige Queries
        self._cache: dict[str, tuple[str, float]] = {}
        self._cache_max: int = 50
        self._cache_ttl: float = 300.0  # 5 min

    async def initialize(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.ollama_url,
            timeout=SIDE_QUERY_TIMEOUT_SEC,
        )
        logger.info("side_query_init", model=self._model)

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()

    # ── Core Query ───────────────────────────────────────────────────────

    async def query(
        self,
        system: str,
        user_message: str,
        max_tokens: int = SIDE_QUERY_MAX_TOKENS,
        temperature: float = SIDE_QUERY_TEMPERATURE,
        cache_key: Optional[str] = None,
    ) -> SideQueryResult:
        """
        Schnelle Query mit dem Light-Modell.
        Für Meta-Tasks die keine Heavy-Engine brauchen.
        """
        # Cache Check
        if cache_key and cache_key in self._cache:
            cached_text, cached_time = self._cache[cache_key]
            if time.time() - cached_time < self._cache_ttl:
                return SideQueryResult(
                    text=cached_text, model=self._model,
                    latency_ms=0.1, from_cache=True,
                )

        if not self._client:
            return SideQueryResult(
                text="", success=False, error="SideQuery nicht initialisiert",
            )

        start = time.monotonic()
        try:
            resp = await self._client.post(
                "/api/chat",
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_message},
                    ],
                    "stream": False,
                    "think": False,
                    "keep_alive": settings.ollama_light_keep_alive,
                    "options": {
                        "num_ctx": 2048,
                        "num_predict": max_tokens,
                        "temperature": temperature,
                    },
                },
            )
            resp.raise_for_status()
            text = resp.json()["message"]["content"]
            latency = (time.monotonic() - start) * 1000

            # Cache speichern
            if cache_key:
                self._cache[cache_key] = (text, time.time())
                if len(self._cache) > self._cache_max:
                    oldest = min(self._cache, key=lambda k: self._cache[k][1])
                    del self._cache[oldest]

            # Stats
            self._stats["queries"] += 1
            n = self._stats["queries"]
            self._stats["avg_ms"] = (
                self._stats["avg_ms"] * (n - 1) + latency
            ) / n

            logger.debug(
                "side_query_ok", latency_ms=round(latency, 1),
                tokens=max_tokens, cached=False,
            )
            return SideQueryResult(
                text=text, model=self._model, latency_ms=latency,
            )

        except Exception as exc:
            self._stats["errors"] += 1
            latency = (time.monotonic() - start) * 1000
            logger.warning("side_query_failed", error=str(exc), ms=round(latency))
            return SideQueryResult(
                text="", success=False, error=str(exc),
                model=self._model, latency_ms=latency,
            )

    # ── Memory Relevanz-Auswahl ──────────────────────────────────────────

    async def select_relevant_memories(
        self,
        user_query: str,
        memory_manifest: list[dict],
        max_memories: int = 5,
    ) -> list[str]:
        """
        AI-powered Memory-Auswahl (wie Claude Code's findRelevantMemories).
        Wählt die relevantesten Memories für die aktuelle Query aus.

        Args:
            user_query: Die User-Anfrage
            memory_manifest: Liste von {path, description, type}
            max_memories: Max Anzahl zu ladender Memories

        Returns:
            Liste von Memory-Pfaden die geladen werden sollen
        """
        if not memory_manifest:
            return []

        # Manifest als Text formatieren
        manifest_text = "\n".join(
            f"- [{m.get('type', '?')}] {m.get('path', '?')}: {m.get('description', 'keine Beschreibung')}"
            for m in memory_manifest
        )

        system = (
            "Du wählst Memories aus die für eine User-Anfrage nützlich sind. "
            "Antworte NUR mit den Pfaden der relevanten Memories, einer pro Zeile. "
            f"Maximal {max_memories} Memories. Wenn keine relevant ist, antworte mit KEINE."
        )

        user_msg = (
            f"User-Anfrage: {user_query}\n\n"
            f"Verfügbare Memories:\n{manifest_text}"
        )

        result = await self.query(
            system=system,
            user_message=user_msg,
            max_tokens=256,
            cache_key=f"mem_select:{hash(user_query)}",
        )

        if not result.success or "KEINE" in result.text.upper():
            return []

        paths = []
        for line in result.text.strip().split("\n"):
            line = line.strip().lstrip("- ")
            # Pfad aus der Antwort extrahieren
            for m in memory_manifest:
                if m.get("path", "") in line:
                    paths.append(m["path"])
                    break

        return paths[:max_memories]

    # ── Klassifikation ───────────────────────────────────────────────────

    async def classify(
        self,
        text: str,
        categories: list[str],
        context: str = "",
    ) -> str:
        """
        Klassifiziere Text in eine Kategorie.
        Für Intent-Erkennung, Permission-Checks, etc.
        """
        cats = ", ".join(categories)
        system = (
            f"Klassifiziere den folgenden Text in GENAU EINE dieser Kategorien: {cats}\n"
            "Antworte NUR mit dem Kategorienamen, nichts anderes."
        )

        if context:
            system += f"\nKontext: {context}"

        result = await self.query(
            system=system,
            user_message=text,
            max_tokens=32,
            temperature=0.1,
            cache_key=f"classify:{hash(text + cats)}",
        )

        if result.success:
            # Beste Kategorie finden
            answer = result.text.strip().lower()
            for cat in categories:
                if cat.lower() in answer:
                    return cat
        return categories[0]  # Fallback: erste Kategorie

    # ── Zusammenfassung ──────────────────────────────────────────────────

    async def summarize(
        self,
        text: str,
        max_sentences: int = 3,
        focus: str = "",
    ) -> str:
        """
        Schnelle Zusammenfassung eines Textes.
        Für Context-Kompression, Away-Summaries, etc.
        """
        system = (
            f"Fasse den folgenden Text in maximal {max_sentences} Sätzen zusammen. "
            "Sei präzise und informativ. Nur die Zusammenfassung, nichts anderes."
        )
        if focus:
            system += f"\nFokus: {focus}"

        result = await self.query(
            system=system,
            user_message=text,
            max_tokens=max_sentences * 50,
            temperature=0.3,
        )

        return result.text if result.success else text[:200] + "..."

    # ── Stats ────────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    def clear_cache(self) -> None:
        self._cache.clear()


# ── Module-Level Singleton ───────────────────────────────────────────────

_engine: Optional[SideQueryEngine] = None


def get_side_query() -> SideQueryEngine:
    """Globale SideQueryEngine-Instanz."""
    global _engine
    if _engine is None:
        _engine = SideQueryEngine()
    return _engine


async def side_query(
    system: str,
    user_message: str,
    **kwargs,
) -> SideQueryResult:
    """Convenience-Funktion für schnelle Side Queries."""
    return await get_side_query().query(system, user_message, **kwargs)
