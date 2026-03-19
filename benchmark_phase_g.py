#!/usr/bin/env python3
"""
SOMA-AI Phase G Benchmark
=========================
3 sequentielle Tests mit Zeitmessung und Qualitätsbewertung:
  1. Smalltalk       – Natürliche Konversation
  2. Internet-Recherche – Wissensfrage (Web-Search)
  3. Action-Call     – Smart-Home Steuerung

Jeder Test zeigt: Frage, Antwort, Engine, Latenz (Server + Client).
"""

import httpx
import time
import sys
import json

API_URL = "http://localhost:8100/api/v1/ask"
TIMEOUT = 60.0  # Max 60s pro Request (inkl. Web-Search)

# ── Farben ────────────────────────────────────────────────────────────────
G = "\033[0;32m"
Y = "\033[1;33m"
R = "\033[0;31m"
C = "\033[0;36m"
B = "\033[1m"
DIM = "\033[2m"
NC = "\033[0m"

# ── Test-Definitionen ────────────────────────────────────────────────────
TESTS = [
    {
        "name": "💬 Smalltalk",
        "description": "Natürliche Konversation – testet Persona & Geschwindigkeit",
        "prompt": "Hey Soma! Wie geht's dir heute so? Was hast du so auf dem Schirm?",
    },
    {
        "name": "🌐 Internet-Recherche",
        "description": "Wissensfrage – testet Web-Search Integration",
        "prompt": "Was sind die aktuellen Top-Nachrichten in Deutschland heute?",
    },
    {
        "name": "⚡ Action-Call",
        "description": "Smart-Home Befehl – testet Action-Erkennung & Intent",
        "prompt": "Mach bitte das Licht im Wohnzimmer an und stell die Helligkeit auf 80 Prozent.",
    },
]


def print_header():
    print(f"\n{C}{'━' * 66}{NC}")
    print(f"{B}  🧪 SOMA-AI Phase G Benchmark{NC}")
    print(f"{C}{'━' * 66}{NC}")
    print(f"  {DIM}API: {API_URL}{NC}")
    print(f"  {DIM}Tests: {len(TESTS)} (sequentiell){NC}")
    print()


def check_health():
    """Prüfe ob SOMA erreichbar ist."""
    try:
        r = httpx.get("http://localhost:8100/api/v1/health", timeout=5.0)
        data = r.json()
        load = data.get("metrics", {}).get("load_level", "unknown")
        gpu_pct = data.get("metrics", {}).get("gpu", {}).get("vram_percent", 0)
        print(f"  {G}✓{NC} SOMA online – Load: {load}, VRAM: {gpu_pct:.0f}%")
        return True
    except Exception as e:
        print(f"  {R}✗{NC} SOMA nicht erreichbar: {e}")
        return False


def run_test(idx: int, test: dict) -> dict:
    """Einzelnen Test ausführen und Ergebnis zurückgeben."""
    name = test["name"]
    desc = test["description"]
    prompt = test["prompt"]

    print(f"\n{C}── Test {idx}/{len(TESTS)}: {name} ──{NC}")
    print(f"  {DIM}{desc}{NC}")
    print(f"\n  {B}Frage:{NC} {prompt}")
    print(f"  {DIM}Warte auf Antwort...{NC}", end="", flush=True)

    payload = {
        "prompt": prompt,
        "session_id": f"benchmark_test_{idx}",
        "user_id": "benchmark",
        "room_id": "office",
    }

    client_start = time.perf_counter()
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            r = client.post(API_URL, json=payload)
            client_end = time.perf_counter()

            if r.status_code != 200:
                print(f"\r  {R}✗ HTTP {r.status_code}: {r.text[:200]}{NC}")
                return {"name": name, "success": False, "error": f"HTTP {r.status_code}"}

            data = r.json()

    except httpx.TimeoutException:
        client_end = time.perf_counter()
        print(f"\r  {R}✗ Timeout nach {TIMEOUT}s{NC}")
        return {"name": name, "success": False, "error": "Timeout"}
    except Exception as e:
        client_end = time.perf_counter()
        print(f"\r  {R}✗ Fehler: {e}{NC}")
        return {"name": name, "success": False, "error": str(e)}

    client_ms = (client_end - client_start) * 1000
    server_ms = data.get("latency_ms") or 0.0
    engine = data.get("engine_used", "unknown")
    response_text = data.get("response", "")
    was_deferred = data.get("was_deferred", False)
    load_level = data.get("load_level", "unknown")

    # Antwort anzeigen (clear "Warte auf Antwort...")
    print(f"\r{' ' * 40}\r", end="")  # Clear line

    # Antwort – auf 500 Zeichen gekürzt falls nötig
    display_response = response_text
    if len(display_response) > 600:
        display_response = display_response[:597] + "..."

    print(f"\n  {G}Antwort:{NC}")
    for line in display_response.split("\n"):
        print(f"    {line}")

    print(f"\n  {C}┌─ Metriken ────────────────────────────────{NC}")
    print(f"  {C}│{NC} Engine:       {B}{engine}{NC}")
    print(f"  {C}│{NC} Server-Zeit:  {B}{server_ms:.0f}ms{NC}")
    print(f"  {C}│{NC} Client-RTT:   {B}{client_ms:.0f}ms{NC}")
    print(f"  {C}│{NC} Deferred:     {'Ja' if was_deferred else 'Nein'}")
    print(f"  {C}│{NC} Load-Level:   {load_level}")
    print(f"  {C}│{NC} Antwort-Len:  {len(response_text)} Zeichen")
    print(f"  {C}└────────────────────────────────────────────{NC}")

    # Qualitäts-Indikatoren
    quality_notes = []
    if server_ms <= 2000:
        quality_notes.append(f"{G}⚡ Unter 2s Ziel{NC}")
    elif server_ms <= 5000:
        quality_notes.append(f"{Y}⏱ Akzeptabel (<5s){NC}")
    else:
        quality_notes.append(f"{R}🐢 Langsam (>{server_ms/1000:.1f}s){NC}")

    if len(response_text) > 50:
        quality_notes.append(f"{G}📝 Substantielle Antwort{NC}")
    elif len(response_text) > 10:
        quality_notes.append(f"{Y}📝 Kurze Antwort{NC}")
    else:
        quality_notes.append(f"{R}⚠ Sehr kurze/leere Antwort{NC}")

    if quality_notes:
        print(f"  Qualität: {' | '.join(quality_notes)}")

    return {
        "name": name,
        "success": True,
        "server_ms": server_ms,
        "client_ms": client_ms,
        "engine": engine,
        "response_len": len(response_text),
        "was_deferred": was_deferred,
    }


def print_summary(results: list[dict]):
    """Zusammenfassung aller Tests."""
    print(f"\n{C}{'━' * 66}{NC}")
    print(f"{B}  📊 Benchmark-Zusammenfassung{NC}")
    print(f"{C}{'━' * 66}{NC}")

    successes = [r for r in results if r.get("success")]
    failures = [r for r in results if not r.get("success")]

    if successes:
        avg_server = sum(r["server_ms"] for r in successes) / len(successes)
        avg_client = sum(r["client_ms"] for r in successes) / len(successes)
        min_server = min(r["server_ms"] for r in successes)
        max_server = max(r["server_ms"] for r in successes)

        print(f"\n  {'Test':<25} {'Engine':<14} {'Server':>8} {'Client':>8} {'Länge':>6}")
        print(f"  {'─' * 25} {'─' * 14} {'─' * 8} {'─' * 8} {'─' * 6}")

        for r in successes:
            color = G if r["server_ms"] <= 2000 else (Y if r["server_ms"] <= 5000 else R)
            print(
                f"  {r['name']:<25} {r['engine']:<14} "
                f"{color}{r['server_ms']:>7.0f}ms{NC} "
                f"{r['client_ms']:>7.0f}ms "
                f"{r['response_len']:>5} ch"
            )

        print(f"\n  {B}Durchschnitt:{NC}  Server {avg_server:.0f}ms | Client {avg_client:.0f}ms")
        print(f"  {B}Schnellster:{NC}   {min_server:.0f}ms")
        print(f"  {B}Langsamster:{NC}   {max_server:.0f}ms")

        # Bewertung
        if avg_server <= 2000:
            verdict = f"{G}🏆 EXCELLENT – Durchschnitt unter 2s Ziel!{NC}"
        elif avg_server <= 3500:
            verdict = f"{Y}👍 GUT – Nah am 2s Ziel{NC}"
        elif avg_server <= 5000:
            verdict = f"{Y}⚠ AKZEPTABEL – Optimierungspotential{NC}"
        else:
            verdict = f"{R}🔧 OPTIMIERUNG NÖTIG – Deutlich über Ziel{NC}"

        print(f"\n  {B}Bewertung:{NC} {verdict}")

    if failures:
        print(f"\n  {R}Fehlgeschlagene Tests:{NC}")
        for r in failures:
            print(f"    {R}✗{NC} {r['name']}: {r.get('error', 'unknown')}")

    print(f"\n{C}{'━' * 66}{NC}\n")


def main():
    print_header()

    if not check_health():
        print(f"\n  {R}Abbruch: SOMA muss laufen für den Benchmark.{NC}")
        print(f"  Starte mit: {C}./start_soma.sh{NC}\n")
        sys.exit(1)

    results = []
    for idx, test in enumerate(TESTS, 1):
        result = run_test(idx, test)
        results.append(result)

        # Kurze Pause zwischen Tests (LLM-Cooldown)
        if idx < len(TESTS):
            print(f"\n  {DIM}⏳ 2s Pause vor nächstem Test...{NC}")
            time.sleep(2)

    print_summary(results)


if __name__ == "__main__":
    main()
