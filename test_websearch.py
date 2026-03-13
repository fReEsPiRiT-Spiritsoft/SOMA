#!/usr/bin/env python
"""Quick test for the new WebSearch module."""
import asyncio
import sys
sys.path.insert(0, '.')

async def main():
    from brain_core.web_search import get_web_search
    ws = get_web_search()
    print("WebSearch Modul geladen ✓")
    
    # Test 1: Suche
    print("\n--- Test: DuckDuckGo Suche ---")
    results = await ws.search("bitcoin kurs aktuell EUR", max_results=3)
    print(f"Ergebnisse: {len(results)}")
    for r in results:
        print(f"  [{r.source}] {r.title[:55]}")
        print(f"     {r.body[:80]}")
    
    # Test 2: URL-Fetch
    print("\n--- Test: URL-Fetch ---")
    result = await ws.fetch_url("https://www.heise.de")
    print(f"URL geladen: {result.success}")
    print(f"Titel: {result.title[:50]}")
    print(f"Text-Länge: {len(result.text)} Zeichen")
    print(f"Text-Preview: {result.text[:150]}")
    
    print("\n✓ Alle Tests erfolgreich!")

if __name__ == "__main__":
    asyncio.run(main())
