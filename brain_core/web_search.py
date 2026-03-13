"""
SOMA-AI WebSearch — Echter Internet-Zugriff
=============================================
Gibt SOMA echte, aktuelle Informationen aus dem Internet.
Kein Halluzinieren mehr bei Fragen nach aktuellen Ereignissen, Preisen, Wetter etc.

Architektur:
  ┌──────────────────────────────────────────────────────────┐
  │  WebSearch.search(query)                                 │
  │      │                                                   │
  │      ├─ DuckDuckGo (duckduckgo-search Bibliothek)        │
  │      │     → strukturierte Ergebnisse (Titel+Snippet+URL)│
  │      │                                                   │
  │      └─ Fallback: DDG HTML-Scraping (httpx)              │
  │                                                          │
  │  WebSearch.fetch_url(url)                                │
  │      │                                                   │
  │      ├─ trafilatura (Clean-Text-Extraktion)              │
  │      └─ Fallback: BeautifulSoup HTML-Parser              │
  └──────────────────────────────────────────────────────────┘

Privacy:
  - Keine API-Keys, keine Accounts
  - Kein Tracking (keine Google/Bing direkt)
  - DuckDuckGo als privacy-freundliche Basis
  - Alle Anfragen bleiben lokal protokolliert

Non-Negotiable:
  - Timeout: max 10s pro Anfrage (UI muss reaktiv bleiben)
  - Max Content: 5000 Zeichen pro URL (RAM sparen)
  - Graceful Degradation: Immer ein sinnvolles Ergebnis oder leere Liste
"""

from __future__ import annotations

import asyncio
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional

import httpx
import structlog

logger = structlog.get_logger("soma.web_search")

# ── Konstanten ────────────────────────────────────────────────────────────

SEARCH_TIMEOUT_SEC: float = 10.0
FETCH_TIMEOUT_SEC: float = 12.0
MAX_CONTENT_CHARS: int = 5000
MAX_SEARCH_RESULTS: int = 8
USER_AGENT: str = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0"
)


# ── Datenmodell ───────────────────────────────────────────────────────────

@dataclass
class SearchResult:
    """Ein einzelnes Suchergebnis."""
    title: str
    body: str          # Snippet / Beschreibung
    url: str
    source: str = ""   # Domainname zur Orientierung


@dataclass
class FetchResult:
    """Geholter und bereinigter Seiteninhalt."""
    url: str
    title: str
    text: str          # Bereinigter Volltext
    success: bool = True
    error: str = ""
    duration_ms: float = 0.0


# ══════════════════════════════════════════════════════════════════════════
#  WEBSEARCH ENGINE
# ══════════════════════════════════════════════════════════════════════════

class WebSearch:
    """
    Echter Internet-Zugriff für SOMA.

    Methoden:
        search(query, max_results) → list[SearchResult]
        fetch_url(url, question)   → FetchResult
        search_and_summarize(query) → str   ← Hauptmethode für Pipeline
    """

    # ── Öffentliche API ───────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        max_results: int = MAX_SEARCH_RESULTS,
    ) -> list[SearchResult]:
        """
        Führe eine Web-Suche durch.

        Strategie:
          1. duckduckgo-search Bibliothek (strukturiert, robust)
          2. DDG HTML-Scraping via httpx (Fallback)

        Returns leere Liste wenn alle Methoden fehlschlagen.
        """
        query = query.strip()
        if not query:
            return []

        logger.info("web_search_start", query=query[:80])
        start = time.monotonic()

        # Methode 1: duckduckgo-search Bibliothek
        results = await self._ddg_library_search(query, max_results)

        # Methode 2: HTML-Scraping Fallback
        if not results:
            logger.warning("ddg_library_failed_fallback_scraping", query=query[:40])
            results = await self._ddg_scrape_search(query, max_results)

        duration = (time.monotonic() - start) * 1000
        logger.info(
            "web_search_done",
            query=query[:60],
            results=len(results),
            duration_ms=round(duration, 1),
        )
        return results

    async def fetch_url(self, url: str) -> FetchResult:
        """
        Rufe eine URL ab und extrahiere bereinigten Text.

        Strategie:
          1. trafilatura (beste Qualität, entfernt Navigation/Ads)
          2. BeautifulSoup Fallback

        Returns FetchResult mit .text als bereinigtem Inhalt.
        """
        url = url.strip()
        if not url:
            return FetchResult(url=url, title="", text="", success=False, error="Keine URL")

        logger.info("web_fetch_start", url=url[:80])
        start = time.monotonic()

        try:
            async with httpx.AsyncClient(
                timeout=FETCH_TIMEOUT_SEC,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text
                final_url = str(resp.url)

            # Text extrahieren: trafilatura bevorzugt
            title, text = await asyncio.to_thread(self._extract_text, html, final_url)

            text = text[:MAX_CONTENT_CHARS]
            duration = (time.monotonic() - start) * 1000

            logger.info(
                "web_fetch_done",
                url=url[:60],
                chars=len(text),
                duration_ms=round(duration, 1),
            )
            return FetchResult(
                url=final_url,
                title=title,
                text=text,
                success=True,
                duration_ms=round(duration, 1),
            )

        except httpx.TimeoutException:
            return FetchResult(url=url, title="", text="", success=False,
                               error=f"Timeout nach {FETCH_TIMEOUT_SEC}s")
        except httpx.HTTPStatusError as e:
            return FetchResult(url=url, title="", text="", success=False,
                               error=f"HTTP {e.response.status_code}")
        except Exception as exc:
            logger.warning("web_fetch_failed", url=url[:60], error=str(exc))
            return FetchResult(url=url, title="", text="", success=False,
                               error=str(exc))

    def format_results_for_llm(
        self,
        query: str,
        results: list[SearchResult],
        max_chars: int = 3000,
    ) -> str:
        """
        Formatiere Suchergebnisse als lesbaren Text für den LLM-Re-Ask.
        Kompakt aber informativ.
        """
        if not results:
            return f"Keine Suchergebnisse für: {query}"

        lines = [f"Aktuelle Internet-Suchergebnisse für: \"{query}\"", ""]
        total = 0
        for i, r in enumerate(results, 1):
            source = _extract_domain(r.url) or r.source
            entry = f"{i}. [{source}] {r.title}\n   {r.body}"
            if r.url:
                entry += f"\n   🔗 {r.url}"
            if total + len(entry) > max_chars:
                lines.append(f"... ({len(results) - i + 1} weitere Ergebnisse)")
                break
            lines.append(entry)
            total += len(entry)

        return "\n".join(lines)

    # ── Interne Suchmethoden ──────────────────────────────────────────────

    async def _ddg_library_search(
        self, query: str, max_results: int
    ) -> list[SearchResult]:
        """DuckDuckGo via `ddgs` Bibliothek (robusteste Methode)."""
        try:
            # Neues Paket 'ddgs' (umbenannt von 'duckduckgo-search')
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS  # Fallback auf alten Namen

            def _sync_search() -> list[dict]:
                with DDGS() as ddgs:
                    return list(ddgs.text(
                        query,
                        max_results=max_results,
                        safesearch="off",
                    ))

            raw = await asyncio.wait_for(
                asyncio.to_thread(_sync_search),
                timeout=SEARCH_TIMEOUT_SEC,
            )

            results = []
            for r in raw:
                results.append(SearchResult(
                    title=r.get("title", ""),
                    body=r.get("body", ""),
                    url=r.get("href", ""),
                    source=_extract_domain(r.get("href", "")),
                ))
            return results

        except asyncio.TimeoutError:
            logger.warning("ddg_library_timeout", query=query[:40])
            return []
        except ImportError:
            logger.warning("ddg_library_not_installed")
            return []
        except Exception as exc:
            logger.warning("ddg_library_error", error=str(exc)[:100])
            return []

    async def _ddg_scrape_search(
        self, query: str, max_results: int
    ) -> list[SearchResult]:
        """DuckDuckGo HTML-Scraping via httpx + BeautifulSoup (Fallback)."""
        try:
            encoded = urllib.parse.quote_plus(query)
            url = f"https://html.duckduckgo.com/html/?q={encoded}"

            async with httpx.AsyncClient(
                timeout=SEARCH_TIMEOUT_SEC,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text

            # BeautifulSoup für robustes Parsing
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "lxml")
                results = []
                for div in soup.select(".result")[:max_results]:
                    title_el = div.select_one(".result__title a")
                    snippet_el = div.select_one(".result__snippet")
                    url_el = div.select_one(".result__url")

                    title = title_el.get_text(strip=True) if title_el else ""
                    body = snippet_el.get_text(strip=True) if snippet_el else ""
                    href = title_el.get("href", "") if title_el else ""
                    display_url = url_el.get_text(strip=True) if url_el else ""

                    # DDG redirects entfernen
                    if href and href.startswith("/"):
                        parsed = urllib.parse.parse_qs(
                            urllib.parse.urlparse(href).query
                        )
                        href = parsed.get("uddg", [href])[0]

                    if title or body:
                        results.append(SearchResult(
                            title=title,
                            body=body,
                            url=href,
                            source=display_url,
                        ))
                return results

            except ImportError:
                # Regex-Fallback ohne BS4
                titles = re.findall(
                    r'class="result__a"[^>]*>([^<]+)<', html
                )
                snippets = re.findall(
                    r'class="result__snippet"[^>]*>([^<]+)<', html
                )
                results = []
                for i in range(min(max_results, len(titles))):
                    results.append(SearchResult(
                        title=titles[i].strip(),
                        body=snippets[i].strip() if i < len(snippets) else "",
                        url="",
                    ))
                return results

        except Exception as exc:
            logger.warning("ddg_scrape_failed", error=str(exc)[:100])
            return []

    # ── Text-Extraktion ───────────────────────────────────────────────────

    @staticmethod
    def _extract_text(html: str, url: str = "") -> tuple[str, str]:
        """
        Extrahiere Titel und bereinigten Text aus HTML.
        Versucht trafilatura zuerst (beste Qualität), dann BeautifulSoup.

        Returns (title, text)
        """
        title = ""
        text = ""

        # Methode 1: trafilatura (entfernt Menus, Ads, Navigation automatisch)
        try:
            import trafilatura

            # Metadata (Titel)
            meta = trafilatura.extract_metadata(html, default_url=url)
            if meta:
                title = meta.title or ""

            # Haupttext
            extracted = trafilatura.extract(
                html,
                include_links=False,
                include_images=False,
                include_tables=True,
                no_fallback=False,
                favor_recall=True,
            )
            if extracted and len(extracted) > 100:
                text = extracted
                return title, text
        except Exception:
            pass

        # Methode 2: BeautifulSoup
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")

            # Titel
            if soup.title:
                title = soup.title.get_text(strip=True)

            # Unnötige Elemente entfernen
            for tag in soup(["script", "style", "nav", "header",
                              "footer", "aside", "form", "iframe"]):
                tag.decompose()

            text = soup.get_text(separator=" ", strip=True)
            text = re.sub(r"\s+", " ", text).strip()
            return title, text
        except Exception:
            pass

        # Methode 3: Einfaches Regex-Stripping
        title_m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
        if title_m:
            title = title_m.group(1).strip()
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return title, text


# ── Hilfsfunktionen ───────────────────────────────────────────────────────

def _extract_domain(url: str) -> str:
    """Extrahiere Domainnamen aus URL für lesbare Quellenangabe."""
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc
        # www. entfernen
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


# ── Singleton ─────────────────────────────────────────────────────────────

_web_search_instance: Optional[WebSearch] = None


def get_web_search() -> WebSearch:
    """Gibt die globale WebSearch-Instanz zurück (Singleton)."""
    global _web_search_instance
    if _web_search_instance is None:
        _web_search_instance = WebSearch()
    return _web_search_instance
