"""
SOMA Plugin: Web Search
========================
Echter Internet-Zugriff via DuckDuckGo + trafilatura.
Keine API-Keys, keine Cloud, 100% lokal verarbeitet.

Dieses Plugin delegiert an brain_core.web_search — das zentrale Such-Modul.
Kann direkt von SOMA via Plugin-System aufgerufen werden.
"""
__version__ = "2.0.0"
__author__ = "soma-ai"
__description__ = "Echter Internet-Zugriff: Suche + URL-Inhalt abrufen (DuckDuckGo + trafilatura)"

import logging

logger = logging.getLogger("soma.plugin.web_search")


async def on_load() -> None:
    logger.info("Web Search Plugin v2 geladen — echter Internet-Zugriff aktiv")


async def execute(*args, **kwargs) -> str:
    """
    Führt eine echte Web-Suche durch und gibt formatierte Ergebnisse zurück.

    Kwargs:
        query (str):       Suchbegriff
        url   (str):       Alternativ: direkte URL zum Abrufen
        question (str):    Bei url-Modus: Frage die beantwortet werden soll
        max_results (int): Anzahl Suchergebnisse (default: 6)
    """
    query = kwargs.get("query", "").strip()
    url = kwargs.get("url", "").strip()
    question = kwargs.get("question", "Fasse den wichtigsten Inhalt zusammen.")
    max_results = int(kwargs.get("max_results", 6))

    try:
        from brain_core.web_search import get_web_search
        ws = get_web_search()

        # URL-Modus: direkte Seite abrufen
        if url and not query:
            result = await ws.fetch_url(url)
            if result.success:
                return f"[{result.title}]\n{result.text[:2000]}"
            return f"Fehler beim Laden von {url}: {result.error}"

        # Such-Modus: DuckDuckGo-Suche
        if not query:
            return "Kein Suchbegriff angegeben."

        results = await ws.search(query, max_results=max_results)
        if not results:
            return f"Keine Ergebnisse für '{query}' gefunden."

        return ws.format_results_for_llm(query, results)

    except Exception as e:
        logger.error(f"Fehler in Web Search Plugin: {e}")
        return f"Fehler bei der Suche: {str(e)}"


async def on_unload() -> None:
    logger.info("Web Search Plugin entladen")
