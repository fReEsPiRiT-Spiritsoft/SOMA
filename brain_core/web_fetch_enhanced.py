"""
Web Fetch Enhanced — Intelligente Web-Inhalte-Verarbeitung.
=============================================================
Inspiriert von Claude Code's WebFetchTool.ts:
Erweitert den bestehenden WebSearch um:

1. **Prompt-basiertes Fetching**: "Hole diese URL und beantworte X"
2. **Content-Compaction**: LLM reduziert auf relevante Teile
3. **Multi-Page Aggregation**: Mehrere URLs parallel holen
4. **Structured Extraction**: JSON/Tabellen aus HTML extrahieren
5. **Cache**: Gleiche URL nicht doppelt holen

Baut auf brain_core/web_search.py auf — erweitert, ersetzt nicht.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from typing import Optional

import structlog

from brain_core.web_search import WebSearch, FetchResult

logger = structlog.get_logger("soma.web_fetch")

# ── Config ───────────────────────────────────────────────────────────────

FETCH_CACHE_TTL_SEC = 300         # Cache: 5 Minuten
FETCH_CACHE_MAX_SIZE = 20         # Max Cache-Einträge
MAX_PARALLEL_FETCHES = 3          # Max parallele URL-Fetches
COMPACT_THRESHOLD_CHARS = 3000    # Ab so vielen Zeichen → Kompaktierung
COMPACT_MAX_OUTPUT_CHARS = 1500   # Kompaktiertes Ergebnis max so lang


@dataclass
class EnhancedFetchResult:
    """Erweitertes Fetch-Ergebnis mit optionaler LLM-Verarbeitung."""
    url: str
    title: str
    raw_text: str              # Originaler Text
    processed_text: str = ""    # LLM-verarbeiteter Text (wenn Prompt gegeben)
    success: bool = True
    error: str = ""
    duration_ms: float = 0.0
    from_cache: bool = False
    chars_saved: int = 0        # Zeichen gespart durch Kompaktierung


@dataclass
class _CacheEntry:
    text: str
    title: str
    timestamp: float
    url: str


class WebFetchEnhanced:
    """
    Intelligente Web-Inhalte-Verarbeitung.

    Nutzt bestehenden WebSearch.fetch_url() + SideQuery für Kompaktierung.

    Usage:
        fetcher = WebFetchEnhanced()
        result = await fetcher.fetch_with_prompt(
            url="https://example.com/article",
            prompt="Was sind die Hauptargumente?",
        )
    """

    def __init__(self):
        self._web = WebSearch()
        self._cache: dict[str, _CacheEntry] = {}
        self._side_query_fn: Optional = None  # SideQuery.query()

    def set_side_query(self, fn):
        """SideQuery-Funktion für LLM-Verarbeitung."""
        self._side_query_fn = fn

    # ── Fetch with Prompt ────────────────────────────────────────────

    async def fetch_with_prompt(
        self,
        url: str,
        prompt: str = "",
        max_chars: int = COMPACT_MAX_OUTPUT_CHARS,
    ) -> EnhancedFetchResult:
        """
        Hole URL und verarbeite Inhalt mit LLM-Prompt.

        Args:
            url: Die zu holende URL
            prompt: Was soll aus dem Inhalt extrahiert werden?
            max_chars: Max Zeichen für die Antwort
        """
        start = time.time()

        # Cache prüfen
        cache_key = self._cache_key(url)
        cached = self._get_cached(cache_key)

        if cached:
            raw_text = cached.text
            title = cached.title
            from_cache = True
        else:
            # Fetch via bestehenden WebSearch
            result = await self._web.fetch_url(url)
            if not result.success:
                return EnhancedFetchResult(
                    url=url, title="", raw_text="",
                    success=False, error=result.error,
                    duration_ms=(time.time() - start) * 1000,
                )

            raw_text = result.text
            title = result.title
            from_cache = False

            # In Cache legen
            self._put_cache(cache_key, raw_text, title, url)

        # Ohne Prompt → nur Raw-Text zurückgeben (ggf. kompaktiert)
        if not prompt:
            processed = raw_text[:max_chars] if len(raw_text) > max_chars else raw_text
            return EnhancedFetchResult(
                url=url, title=title,
                raw_text=raw_text,
                processed_text=processed,
                success=True,
                duration_ms=(time.time() - start) * 1000,
                from_cache=from_cache,
            )

        # Mit Prompt → LLM verarbeiten
        processed = await self._process_with_prompt(
            raw_text, prompt, title, max_chars
        )

        duration = (time.time() - start) * 1000
        chars_saved = max(0, len(raw_text) - len(processed))

        return EnhancedFetchResult(
            url=url, title=title,
            raw_text=raw_text,
            processed_text=processed,
            success=True,
            duration_ms=duration,
            from_cache=from_cache,
            chars_saved=chars_saved,
        )

    # ── Multi-Page Fetch ─────────────────────────────────────────────

    async def fetch_multiple(
        self,
        urls: list[str],
        prompt: str = "",
        max_chars_per_page: int = 1000,
    ) -> list[EnhancedFetchResult]:
        """
        Hole mehrere URLs parallel und verarbeite sie.
        """
        # Limitiere Parallelität
        sem = asyncio.Semaphore(MAX_PARALLEL_FETCHES)

        async def _fetch_one(url: str) -> EnhancedFetchResult:
            async with sem:
                return await self.fetch_with_prompt(
                    url=url, prompt=prompt, max_chars=max_chars_per_page,
                )

        results = await asyncio.gather(
            *[_fetch_one(u) for u in urls[:10]],  # Max 10 URLs
            return_exceptions=True,
        )

        # Exceptions in Ergebnisse umwandeln
        clean = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                clean.append(EnhancedFetchResult(
                    url=urls[i], title="", raw_text="",
                    success=False, error=str(r),
                ))
            else:
                clean.append(r)

        return clean

    # ── Search + Fetch Combo ─────────────────────────────────────────

    async def search_and_extract(
        self,
        query: str,
        prompt: str = "",
        max_urls: int = 3,
        max_chars: int = 2000,
    ) -> str:
        """
        Suche + Top-Ergebnisse fetchen + LLM-Verarbeitung.
        Convenience-Methode für "recherchiere X und fasse zusammen".
        """
        # Web-Suche
        results = await self._web.search(query, max_results=max_urls + 2)

        if not results:
            return f"Keine Ergebnisse für: {query}"

        # Top-URLs fetchen
        urls = [r.url for r in results[:max_urls] if r.url]
        if not urls:
            # Nur Snippets zurückgeben
            return self._web.format_results_for_llm(query, results, max_chars)

        extract_prompt = prompt or f"Extrahiere die wichtigsten Informationen zu: {query}"

        fetched = await self.fetch_multiple(
            urls=urls,
            prompt=extract_prompt,
            max_chars_per_page=max_chars // max(len(urls), 1),
        )

        # Ergebnisse zusammenführen
        parts = []
        for fr in fetched:
            if fr.success and (fr.processed_text or fr.raw_text):
                text = fr.processed_text or fr.raw_text[:500]
                parts.append(f"[{fr.title or fr.url}]\n{text}")

        if not parts:
            return self._web.format_results_for_llm(query, results, max_chars)

        return "\n\n---\n\n".join(parts)[:max_chars]

    # ── LLM Processing ───────────────────────────────────────────────

    async def _process_with_prompt(
        self,
        text: str,
        prompt: str,
        title: str,
        max_chars: int,
    ) -> str:
        """Verarbeite Text mit LLM through SideQuery."""

        # Wenn kein SideQuery → Fallback: einfaches Truncating
        if not self._side_query_fn:
            return text[:max_chars]

        # Text zu lang → erst auf 4000 Zeichen kürzen (LLM Input-Limit)
        input_text = text[:4000] if len(text) > 4000 else text

        try:
            result = await self._side_query_fn(
                system=(
                    "Du verarbeitest den Inhalt einer Webseite. "
                    "Extrahiere NUR die relevanten Informationen. "
                    "Antworte knapp und in Deutsch."
                ),
                user_message=(
                    f"Webseite: {title}\n\n"
                    f"Aufgabe: {prompt}\n\n"
                    f"Inhalt:\n{input_text}"
                ),
                max_tokens=512,
                temperature=0.3,
            )

            if result.success and result.text:
                return result.text[:max_chars]

        except Exception as exc:
            logger.warning("web_fetch_llm_error", error=str(exc))

        # Fallback
        return text[:max_chars]

    # ── Cache ────────────────────────────────────────────────────────

    @staticmethod
    def _cache_key(url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()[:12]

    def _get_cached(self, key: str) -> Optional[_CacheEntry]:
        entry = self._cache.get(key)
        if entry and (time.time() - entry.timestamp) < FETCH_CACHE_TTL_SEC:
            return entry
        if entry:
            del self._cache[key]
        return None

    def _put_cache(self, key: str, text: str, title: str, url: str):
        # Evict wenn zu voll
        if len(self._cache) >= FETCH_CACHE_MAX_SIZE:
            oldest_key = min(
                self._cache, key=lambda k: self._cache[k].timestamp,
            )
            del self._cache[oldest_key]

        self._cache[key] = _CacheEntry(
            text=text, title=title, timestamp=time.time(), url=url,
        )

    def clear_cache(self):
        self._cache.clear()

    @property
    def cache_stats(self) -> dict:
        return {
            "entries": len(self._cache),
            "max_size": FETCH_CACHE_MAX_SIZE,
        }


# ── Module-Level Singleton ───────────────────────────────────────────────

_fetcher: Optional[WebFetchEnhanced] = None


def get_web_fetch() -> WebFetchEnhanced:
    global _fetcher
    if _fetcher is None:
        _fetcher = WebFetchEnhanced()
    return _fetcher
