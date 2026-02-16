🚀 SOMA-AI: GENESIS ARCHITECT PROMPT (FOR CLAUDE OPUS)

Rolle: Du bist der Senior Lead Architect für SOMA-AI, ein hoch-resilientes, adaptives Ambient OS.
Kontext: Hardware-Target ist ein Debian-System mit 32GB RAM / 12GB VRAM. Das System muss lokal, modular und "Health-Aware" sein.

AUFGABE:
Initialisiere die gesamte Projektstruktur und die Kern-Logik. Erstelle Code, der nicht nur "funktioniert", sondern die systemischen Zusammenhänge von Anfang an festschreibt.

1. VOLLSTÄNDIGE PROJEKTSTRUKTUR (Scaffold):
Lege das Monorepo-Skelett an:

    brain_core/ (FastAPI Orchestrator mit Uvloop)

    brain_memory_ui/ (Django SSOT für Hardware & User)

    shared/ (Resilienz-Klassen & Pydantic-Schemas)

    evolution_lab/ (Plugin-Sandbox & Hot-Reloading-Logik)

    soma_face_tablet/ (WebGL/Three.js Frontend)

2. KRITISCHE ABHÄNGIGKEITEN & VERBINDUNGEN:
Implementiere sofort die Basis-Verbindungen zwischen den Diensten:

    Health-to-Logic Bridge: Erstelle brain_core/health_monitor.py und logic_router.py. Implementiere das Deferred Reasoning: Wenn RAM/VRAM > 85%, verschiebe Anfragen in die Redis-Queue (queue_handler.py).

    Virtual Patchbay: Implementiere audio_router.py. Erlaube die dynamische Kopplung von HardwareNode-IDs (Mics) zu Speaker-IDs, gesteuert durch den presence_manager.py.

    Resilience Layer: Erstelle shared/resilience.py. Jeder externe Call (Ollama, Postgres, MQTT) MUSS in einen asynchronen Circuit Breaker eingekapselt sein.

3. DJANGO SSOT (Source of Truth):
Entwirf die Models in brain_memory_ui/hardware/models.py für:

    Room (is_kids_room flag)

    HardwareNode (Type: MIC/SPK/TAB, Protocol: MQTT/HA/mDNS, Room_ID)

    Diese Datenbank MUSS die einzige Instanz sein, aus der der brain_core sein Wissen über die Welt bezieht.

4. MULTI-MODEL ROUTING:
Setze die engines/-Struktur auf. Erstelle ein Interface, das nahtlos zwischen:

    HeavyEngine (Ollama/Llama 3 8B)

    NanoEngine (Lokale Python-Intents für Licht/Heizung bei System-Last)
    umschaltet, ohne den State der User-Session zu verlieren.

5. AUTOMATISIERUNG:
Erstelle ein init_system.sh Skript, das die Ordnerstruktur, virtuelle Umgebungen und die docker-compose.yml (Postgres, Mosquitto, Redis, Ollama) initialisiert.

ANWEISUNG:
Antworte nicht mit Prosa. Generiere den Code für die zentralen Architektur-Dateien (main.py, resilience.py, models.py, logic_router.py) und erkläre kurz die Datenflüsse zwischen ihnen. Der Fokus liegt auf Zero-Latency-Feedback für den User und maximaler Systemstabilität.