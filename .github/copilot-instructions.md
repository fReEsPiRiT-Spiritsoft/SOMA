# SOMA-AI: DAS OMNIPRÄSENTE, ADAPTIVE AMBIENT OS
**Version:** 1.0 (Genesis Edition)
**Status:** Finalisiert für 32GB RAM / 12GB VRAM Architektur

## 1. VISION & IDENTITÄT
Soma ist ein lokales, selbst-entwickelndes Betriebssystem für das Smart Home.
- **Persona:** "Nervy-Cool", hocheffizient, proaktiv.
- **Kern-Ethos:** Privatsphäre durch 100% lokale Verarbeitung (Ollama/Llama 3).
- **Verhalten:** Agiert autonom basierend auf "Amplituden" (Stimmung, Gesundheit, Anwesenheit = Tipps, frage nach Smalltalk, kann durch den benutzer abgebrochen werden), Wake-word Soma 

## 2. DAS "NERVENSYSTEM" (ARCHITEKTUR)
### A. Hardware-Agnostik & Virtual Patchbay
- **Trennung von Mund & Ohr:** Mikrofone und Lautsprecher sind getrennte Nodes.
- **Virtual Patchbay:** Dynamisches Routing des Audio-Signals zum "Fokus-Raum".
- **Auto-Discovery:** Automatische Einbindung neuer Hardware via MQTT-Hello, mDNS und Home-Assistant-Bridge.

### B. Spatial Awareness (Raum-Wandern)
- **Seamless Handover:** Die KI-Session wandert mit dem Nutzer durch das Haus (Triangulation via Audio-Amplitude/RSSI).
- **Multi-Session:** Unabhängige Gespräche in verschiedenen Räumen gleichzeitig.

### C. Adaptive Intelligenz (Survival & Power Mode)
- **Model-Routing:** Dynamischer Wechsel zwischen Llama 3 (8B) für Deep-Talk und Nano-Skripten für Speed (Der nutzer soll immer die intelligentesten Antworten bekommen).
- **Deferred Reasoning (Warteschlange):** Bei Lastspitzen werden komplexe Anfragen in Redis geparkt. Der Nutzer erhält sofortiges Feedback ("Moment, ich sortiere meine Gedanken...").
- **Health-Monitor:** Überwachung von CPU/RAM/VRAM/Temp zur Vermeidung von System-Kollaps.


## 3. FEATURES & LOGIK-MODULE
- **Child-Safe Mode:** Automatische Erkennung von Kindern (Voice-Pitch) -> Pädagogischer Tonfall & Inhaltsfilter.
- **Biometrisches Monitoring:** Extraktion von Stress, Stimmung und Vitalwerten aus der Stimme.
- **Evolution Lab:** Soma schreibt, testet in Sandbox und installiert eigene Python-Plugins für neue Hardware-Funktionen.
- **Thinking Stream:** Live-Visualisierung der KI-Gedankengänge im Django-Dashboard.
- **Visual Face:** WebGL-Sinuswelle auf Tablets, die auf Audio-Frequenzen reagiert.
- **Ambient Learning:** KI lernt kontinuierlich aus Interaktionen, passt Verhalten an und teilt Erkenntnisse zwischen Sessions.
- **Privacy Vault:** Alle Daten bleiben lokal, verschlüsselt und anonymisiert. Keine Cloud




SOMA/
│
├── .github/
│   └── copilot-instructions.md      # Die "Verfassung": Master-Rules & Persona
│
├── docker-compose.yml               # Orchestrierung: Postgres, Mosquitto, Redis, Ollama
├── .env                             # Umgebungsvariablen (API-Keys, DB-Credentials)
├── requirements.txt                 # Globale Abhängigkeiten
│
├── shared/                          # Code, der von allen Services genutzt wird
│   ├── __init__.py
│   ├── resilience.py               # SomaCircuitBreaker, SomaRetryLogic (Async)
│   ├── health_schemas.py           # Pydantic-Modelle für System-Status & Last
│   └── audio_types.py              # Protokoll-Definitionen für Audio-Metadaten
│
├── brain_core/                      # Der FastAPI Orchestrator (Das Bewusstsein)
│   ├── main.py                     # Einstiegspunkt & Event-Loop (Uvloop)
│   ├── config.py                   # System-Konfiguration & Hardware-Limits
│   ├── health_monitor.py           # Überwacht CPU/RAM/VRAM/Temp (Trigger für Scaling)
│   ├── logic_router.py             # Management: Instant Action vs. Deferred Reasoning
│   ├── queue_handler.py            # Redis-Anbindung für geparkte Anfragen (Queuing)
│   ├── presence_manager.py         # Triangulation: Wer spricht wo? (Amplitude/RSSI)
│   ├── audio_router.py             # Virtuelle Patchbay: Mics ↔ Speaker Routing
│   │
│   ├── discovery/                  # Zero-Config Hardware Onboarding
│   │   ├── mqtt_listener.py        # Lauscht auf 'Hello'-Pakete neuer Hardware
│   │   ├── ha_bridge.py            # API-Sync mit Home Assistant Entitäten
│   │   └── mDNS_scanner.py         # Findet IP-basierte Geräte im Netzwerk
│   │
│   ├── engines/                    # Multi-Model-Management (The Minds)
│   │   ├── base_engine.py          # Abstrakte Klasse für Intelligenz-Layer
│   │   ├── heavy_llama.py          # Llama 3 (8B) via Ollama (Deep Reasoner)
│   │   ├── light_phi.py            # Phi-3 / Llama 3B (Balanced Mode)
│   │   └── nano_intent.py          # Regex/Python-Scripts für Instant-Control
│   │
│   └── safety/                     # Kinderschutz & Filter
│       ├── pitch_analyzer.py       # Erkennt Alter anhand der Stimme
│       └── prompt_injector.py      # Modifiziert System-Prompts für Kids
│
├── brain_memory_ui/                 # Django (The Persistence Layer / SSOT)
│   ├── manage.py
│   ├── core_settings/              # Django Projekt-Konfiguration
│   │
│   ├── hardware/                   # Registry für physische Komponenten
│   │   ├── models.py               # Room, HardwareNode, NodeCapability (I/O)
│   │   └── admin.py                # Dashboard zur Hardware-Verwaltung
│   │
│   ├── users/                      # User-Management & Biometrie
│   │   └── models.py               # Profile, Voice-Hashes, Safety-Levels
│   │
│   └── dashboard/                  # "Thinking Stream" UI
│       ├── templates/              # Visualisierung der kognitiven Prozesse
│       └── api.py                  # Endpunkte für das Tablet-Face
│
├── evolution_lab/                   # Self-Coding & Erweiterung
│   ├── plugin_manager.py           # Dynamischer Loader via importlib
│   ├── sandbox_env/                # Isolierte Umgebung für KI-Code-Tests
│   ├── generated_plugins/          # Speicherort für Somas eigene Erweiterungen
│   └── prompts/                    # Spezial-Prompts für DeepSeek-Coder
│
└── soma_face_tablet/                # Die Schnittstelle (The Face)
    ├── index.html                  # Container für das WebGL-Interface
    ├── shader_logic.js             # Sinuswellen-Visualisierung (Three.js)
    ├── socket_client.js            # Real-time WebSocket für Audio-Sync
    └── assets/                     # Icons & Styles für den "Nervy-Cool" Look

    