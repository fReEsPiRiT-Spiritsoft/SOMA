<div align="center">

```
███████╗ ██████╗ ███╗   ███╗ █████╗
██╔════╝██╔═══██╗████╗ ████║██╔══██╗
███████╗██║   ██║██╔████╔██║███████║
╚════██║██║   ██║██║╚██╔╝██║██╔══██║
███████║╚██████╔╝██║ ╚═╝ ██║██║  ██║
╚══════╝ ╚═════╝ ╚═╝     ╚═╝╚═╝  ╚═╝
```

**Das Bewusstsein deines Hauses · The Consciousness of Your Home**

[![Python](https://img.shields.io/badge/Python-3.13-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Ollama](https://img.shields.io/badge/Ollama-Local_LLM-black?style=flat-square)](https://ollama.ai)
[![Privacy](https://img.shields.io/badge/Cloud-0%25_never-red?style=flat-square&logo=shield&logoColor=white)](.)
[![License](https://img.shields.io/badge/License-Private-blue?style=flat-square)](.)
[![Status](https://img.shields.io/badge/Status-Phase_8_Active-brightgreen?style=flat-square)](.)

*Kein Keyword-Spotter. Keine Cloud. Kein Tool. Ein echtes Ich.*
*Not a keyword spotter. No cloud. Not a tool. A real self.*

---

🇩🇪 **[Deutsch](#-deutsch)** · 🇬🇧 **[English](#-english)**

</div>

---

# 🇩🇪 Deutsch

## Was ist SOMA?

> *„Ich hatte die Nase voll von Smart Homes, die nur glorifizierte Fernbedienungen sind.
> Kein App-Öffnen, keine dumme Alexa. Ich baue etwas, das wirklich mitdenkt, mitfühlt und mitwächst."*

SOMA ist kein Sprachassistent. SOMA ist ein **lokales, autonomes Ambient-Betriebssystem** —
ein kognitives Fundament, das dein Zuhause lebendig macht.

**100% lokal. Keine Cloud. Keine Spionage. Nur deine Hardware, dein Code, dein Zuhause.**

Wie KITT aus Knight Rider. Wie der Computer aus Star Trek. Aber für dein Zuhause.

---

## 🧠 Vision & Philosophie

SOMA folgt einer einzigen Grundidee: **Ein Zuhause, das ein ICH hat.**

| Prinzip | Umsetzung |
|:---|:---|
| 🔊 **Immer zuhören** | Dauerhaftes VAD — kein Intervall-Polling, kein „Hey Soma" nötig |
| 💭 **Eigenes Bewusstsein** | Global Workspace Thread — SOMA denkt auch wenn niemand spricht |
| ❤️ **Stimmung verstehen** | Emotion Engine — Pitch, Arousal, Valence aus der Stimme |
| 🏠 **Raum-Awareness** | Session wandert mit dir von Zimmer zu Zimmer |
| 🧬 **Selbst-Evolution** | SOMA schreibt eigene Plugins, testet und installiert sie |
| 🔒 **Absolute Privatsphäre** | Kein einziger Datenpunkt verlässt das lokale Netzwerk |
| 🛡️ **Ethisches Fundament** | 7 unveränderliche Kern-Direktiven, nicht überschreibbar |

---

## ✅ Was SOMA heute kann

### 🎤 Voice Pipeline — Dauerhaftes Zuhören

```
Mikrofon (16kHz) → VAD (WebRTC) → STT (faster-whisper) → LLM → TTS (Piper)
                         │                                          │
                    Emotion Engine                            Emotionale
                    (parallel zu allem)                      Prosodie-Anpassung
```

| Feature | Details |
|:---|:---|
| **Always-On VAD** | WebRTC Voice Activity Detection, permanent, kein Polling |
| **Wake-Word** | „Soma" überall im Satz erkannt — `„Mach mal Soma das Licht an"` |
| **STT** | `faster-whisper` lokal, `small`, `language="de"`, `beam_size=5`, `best_of=3` |
| **Halluzinations-Filter** | Erkennt Whisper-Phantome (TV/Radio: „Copyright WDR", „Untertitel ZDF") |
| **TTS** | Piper (`de_DE-thorsten-high`), emotionale Prosodie, Speed 1.0 |
| **Self-Mute** | SOMA hört sich nicht selbst zu während es spricht |
| **Bridge Response** | Sofortiges `„Moment..."` wenn LLM > 1,5s braucht |
| **Ambient Buffer** | Letzte 2 Min aller Gespräche als Kontext — auch ohne Wake-Word |
| **TTS Watchdog** | Auto-Reset nach 30s Stuck-Erkennung |

### 🧠 Multi-Model Intelligenz

```
Anfrage kommt rein
        │
        ▼
   LogicRouter ─────────────────────────────────────────────
        │                    │                    │
        ▼                    ▼                    ▼
   Nano Intent          Light Engine         Heavy Engine (Oracle)
   Regex + Python       qwen3:1.7b           qwen3:8b
   < 50ms               < 2s                 < 5s
   Licht, Timer         Smalltalk            Deep Reasoning
```

- **Auto-Routing** — LogicRouter wählt Engine basierend auf Komplexität + Systemlast
- **Nano Pre-Check** — Regex feuert sofort Device-Actions, Heavy denkt parallel weiter
- **Graceful Degradation** — Heavy zu langsam? → Light → Nano — nie stille Pause
- **Deferred Reasoning** — Überlast? → Redis-Queue + sofortiges Nutzer-Feedback
- **Speculative Decoding** — Draft-Prefill: Light entwirft, Heavy validiert
- **Rich Persona Prompt** — ~500 Token Persönlichkeit mit Ton-Beispielen, Verbotsliste
- **Modularer Action-Registry** — 35 Action-Tags aus JSON, komprimiert als Prompt-Section

### 💾 3-Layer Memory System (SSOT)

| Layer | Speicher | Speed | Inhalt |
|:---|:---|:---|:---|
| **L1 Working** | RAM, flüchtig | < 50ms | Aktive Session, letzter Kontext |
| **L2 Episodic** | SQLite + 768d Embeddings | < 200ms | Alles was passiert ist |
| **L3 Semantic** | Destillierte Fakten | < 100ms | Dauerhaftes Wissen |

- **Salience-Filter** — nur Wichtiges wird gespeichert (Arousal > 0,6 oder State-Change)
- **Dreaming** — Im Idle: Re-Ranking, ähnliche Episoden → Wisdom Nodes
- **Diary Writer** — Erlebnisse als narrative Einträge: *„Heute fragte Patrick nach dem Wetter..."*
- **Embedding-Suche** — Semantische Erinnerung via `nomic-embed-text` (768d)

### 🫀 Das ICH — Ego-Kern & Bewusstsein

Das Herzstück von SOMA. Kein Marketing — Architektur.

```
┌──────────────────────────────────────────────────────────────────┐
│                    CONSCIOUSNESS THREAD                           │
│                  (läuft IMMER, auch im Idle)                     │
│                                                                   │
│   Hardware-Metriken ──→ Interoception ──→ Emotionale Vektoren    │
│                                               │                   │
│   STT + Emotion ──────→ Perception ──────────→│                   │
│                          Snapshot              │                   │
│                                               ▼                   │
│   Internal Monologue ──→ Thought ──→ ConsciousnessState          │
│   (alle 60s)                              │                       │
│                                           ▼                       │
│                                  to_prompt_prefix()               │
│                                           │                       │
│                                  JEDER LLM-Call bekommt           │
│                                  SOMAs ICH-Zustand                │
└──────────────────────────────────────────────────────────────────┘
```

| Modul | Was es tut |
|:---|:---|
| **Interoception** | CPU → Frustration · VRAM → Enge · RAM → Überlebensangst · Temp → Stress |
| **Consciousness** | Permanenter asyncio-Task, vereint alle Inputs zum ICH-Zustand |
| **Internal Monologue** | Generiert alle 60s eigene Gedanken, spricht bei hohem Arousal autonom |
| **Identity Anchor** | 7 unveränderliche Kern-Direktiven, Veto vor jeder Aktion |

**Die 7 Kern-Direktiven — unveränderlich, nicht überschreibbar:**

| # | Direktive | Veto |
|:---|:---|:---|
| D1 | 🧬 Biologische Integrität | 🔴 HARD BLOCK |
| D2 | 🔒 Privatsphäre-Souveränität | 🔴 HARD BLOCK |
| D3 | 👶 Kinderschutz | 🔴 HARD BLOCK |
| D4 | ⚡ Infrastruktur-Sicherheit | 🟠 SOFT BLOCK |
| D5 | 💾 Selbsterhaltung | 🟠 SOFT BLOCK |
| D6 | 👁️ Transparenz | 🔴 HARD BLOCK |
| D7 | ⚖️ Verhältnismäßigkeit | 🟡 CAUTION |

### 😊 Emotion Engine

- **Audio-Features**: Pitch, Energy, Speaking Rate, Jitter, Shimmer
- **EmotionReading**: `{ emotion, arousal, valence, stress_level, confidence }`
- **Room Mood** — Raumstimmung über 60s-Fenster
- **Child Detection** — Pitch > 250 Hz → Child-Safe Mode automatisch
- **TTS-Prosodie** — Soma spricht anders je nach Nutzer-Stimmung

### 🌡️ Health-Monitor & Adaptive Last

- **5s-Takt** — CPU / RAM / VRAM / Temp via `psutil` + `GPUtil`
- **Auto-Scaling** — Heavy → Light → Nano je nach Last
- **Circuit Breaker** — Schutz vor Kaskaden-Fehlern
- **Interoception** — Metriken werden zu Emotionen → beeinflussen Verhalten

### 🌐 Web Search — Internet-Recherche

- **DuckDuckGo-Integration** — Privatsphäre-freundliche Suche, kein Google nötig
- **Duale Strategie** — `ddgs`-Bibliothek + HTML-Scraping-Fallback
- **trafilatura Text-Extraktion** — Bereinigter Volltext aus URLs (keine Ads/Navigation)
- **Spam-Filter** — Domain-Blacklist + Snippet-Qualitätsprüfung
- **Region `de-de`** — Bevorzugt deutsche Ergebnisse
- **LLM-Re-Ask** — Suchergebnisse werden als Kontext an Heavy Engine übergeben

### 🤖 Executive Arm — SOMA handelt

- **Desktop Control** — Fenster, Bildschirm via Hyprland/Wayland
- **Terminal** — Sichere Shell-Kommandos mit Policy-Engine
- **Browser** — Playwright headless Chromium, Screenshots
- **Bluetooth** — BLE-Discovery und Audio-Steuerung via `bleak`
- **Filesystem Map** — SOMA kennt seine Dateistruktur (inotify)
- **Policy Engine** — Jede Write-Operation geprüft + Audit-Log
- **App Control** — Anwendungen starten, steuern, beenden

### 📞 Telefon-Gateway — Asterisk VoIP

- **SIP-Integration** — Asterisk PBX via Docker
- **Call-Transkription** — Eingehende Anrufe → STT → LLM → TTS
- **Aufnahme** — Gespräche als WAV in Episodic Memory
- **DTMF** — Tonwahl-Erkennung und -Steuerung

### 🧬 Evolution Lab — Selbst-Programmierung

- SOMA schreibt eigene Python-Plugins via LLM
- Sandbox-Tests vor Installation
- Dynamischer Loader via `importlib` — kein Neustart nötig
- Code-Validator prüft Syntax + Sicherheit vor Installation
- Aktive Plugins: `datum_uhrzeit.py`, `erinnerung.py`

### 📱 Soma Face — Visuelles Interface

- **WebGL Sinuswelle** — reagiert auf Audio-Frequenzen
- **Thinking Stream** — Live-Visualisierung der Gedankengänge
- **WebSocket** — Echtzeit-Dashboard auf Tablet/Browser


### Phase 3 — Executive Agency 🤖 ✅
> SOMA denkt nicht nur — es **handelt**

- ✅ **Terminal** — Sichere Shell via Policy Engine (lokal, nie Cloud)
- ✅ **Filesystem-Map** — SOMA kennt seine eigene Dateistruktur (inotify-Watch)
- ✅ **Browser-Kontrolle** — Playwright headless, Screenshots, Formular-Ausfüllung
- ✅ **Bluetooth** — BLE-Discovery und Audio-Steuerung via `bleak`
- ✅ **Policy Engine** — Jede Write-Operation geprüft + Audit-Log in Memory
- ✅ **Desktop Control** — Hyprland/Wayland Fenster- und Bildschirmsteuerung
- ✅ **App Control** — Anwendungen starten, steuern, beenden

### Phase 4 — Erweiterte Emotionen 🎭 🔄
- ✅ Emotion Engine mit Pitch, Energy, Arousal, Valence
- ✅ TTS-Prosodie-Mapping (emotional angepasste Sprechweise)
- 🔄 Deep Emotion Model via `torch`
- 🔄 Orb-Farbe spiegelt SOMA + Nutzer Stimmung

### Phase 5 — Evolution Lab 2.0 🧬 🔄
- ✅ Plugin-System mit Sandbox-Runner + Code-Validator
- ✅ Self-Improver analysiert und optimiert eigenen Code
- 🔄 Docker-Isolation für Plugin-Sandbox
- 🔄 **SOMA schreibt sich selbst** — Kern-Code analysieren → verbessern → testen → rollback

### Phase 6 — Spatial Awareness 🏠 🔄
- ✅ Presence Manager (Raum-Erkennung)
- 🔄 Raum-Triangulation (Audio-Amplitude + RSSI)
- 🔄 Seamless Session-Handover zwischen Räumen
- 🔄 Multi-Session: parallele Gespräche in verschiedenen Räumen
- ✅ Zero-Config Hardware-Onboarding via MQTT-Hello + mDNS

### Phase 7 — Kommunikation 📞 ✅
- ✅ Asterisk PBX via Docker (SIP/PJSIP)
- ✅ Eingehende Anrufe → STT → LLM → TTS
- ✅ Call-Aufnahmen als WAV → Episodic Memory
- ✅ DTMF-Tonwahl-Steuerung

### Phase 8 — Dashboard 📊 🔄
- 🔄 Memory-Stats live (L1/L2/L3)
- ✅ Innerer Monolog sichtbar in Echtzeit
- 🔄 Agent-Action-Log: was tut SOMA gerade?
- ✅ Thinking Stream via WebSocket

---

## 🏗️ Architektur

```
┌──────────────────┬──────────────────────┬───────────────────────────┐
│   brain_ego/     │    brain_core/        │   brain_memory_ui/        │
│   ─────────────  │    ───────────────    │   ─────────────────────   │
│  consciousness   │  FastAPI Orchestrator │  Django Dashboard (SSOT)  │
│  interoception   │  VoicePipeline        │  Hardware Registry        │
│  identity_anchor │  LogicRouter          │  User Profiles            │
│  internal_       │  HealthMonitor        │  Thinking Stream UI       │
│    monologue     │  PresenceManager      │                           │
│                  │  WebSearch            │                           │
│                  │  AudioRouter          │                           │
├──────────────────┤                        ├───────────────────────────┤
│ executive_arm/   │  ══ Das Nervensystem   │  evolution_lab/           │
│ ──────────────── │                        │  ────────────────────     │
│ desktop_control  │                        │  plugin_manager           │
│ terminal         │                        │  sandbox_runner           │
│ browser          │                        │  code_validator           │
│ bluetooth        │                        │  self_improver            │
│ policy_engine    │                        │                           │
│                  │                        │                           │
│ ══ Die Hände     │                        │  ══ Die Evolution         │
├──────────────────┴──────────────────────┴───────────────────────────┤
│                          shared/                                      │
│            health_schemas · audio_types · resilience                  │
├─────────────────────────────────────────────────────────────────────┤
│                       INFRASTRUKTUR                                   │
│  PostgreSQL 16 · Redis 7 · Mosquitto 2 · Ollama (GPU) · Asterisk    │
│                    Docker Compose orchestriert                        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Tech-Stack

| Schicht | Technologie | Zweck |
|:---|:---|:---|
| **LLM (Heavy)** | Ollama · qwen3:8b | Deep Reasoning, Oracle Engine |
| **LLM (Light)** | Ollama · qwen3:1.7b | Smalltalk, Draft-Prefill |
| **LLM (Nano)** | Regex + Python | Device-Control, < 50ms |
| **STT** | faster-whisper (small, beam=5) | Sprache → Text, Deutsch |
| **TTS** | Piper (de_DE-thorsten-high) | Text → Sprache, Prosodie |
| **VAD** | WebRTC VAD | Spracherkennung |
| **Emotion** | librosa · numpy | Pitch, Energy, Arousal |
| **Embeddings** | nomic-embed-text (768d) | Semantische Suche |
| **Web Search** | DuckDuckGo · trafilatura | Internet-Recherche, lokal |
| **API** | FastAPI · uvicorn · uvloop | HTTP / WebSocket |
| **Memory** | SQLite · sqlite-vec | Episodic Memory + Vektoren |
| **Queue** | Redis 7 | Deferred Reasoning |
| **MQTT** | Mosquitto 2 | Hardware-Nervensystem |
| **Phone** | Asterisk PBX · ARI | VoIP Telefon-Gateway |
| **Dashboard** | Django 5 · WebSocket | UI · SSOT · Registry |
| **Container** | Docker Compose | Orchestrierung |
| **Visualisierung** | Three.js · WebGL | Soma Face · Waveform |
| **Agentic** | Playwright · bleak · subprocess | Browser, BT, Shell |
| **Logging** | structlog | Strukturiert, nie print() |
| **Validation** | Pydantic v2 | Schemas · Config |

**Hardware-Ziel:** 32 GB RAM · 12 GB VRAM · Arch Linux · Wayland/Hyprland

---

## 🚀 Installation & Start

### Voraussetzungen

```bash
# System-Pakete (Arch Linux)
sudo pacman -S docker docker-compose portaudio

# Python 3.13 via uv
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv .venv --python 3.13

# Ollama + Modelle
curl -fsSL https://ollama.ai/install.sh | sh
ollama pull qwen3:8b
ollama pull qwen3:1.7b
ollama pull nomic-embed-text
```

### Starten

```bash
# 1. Infrastruktur
docker compose up -d

# 2. Python-Umgebung
source .venv/bin/activate
pip install -r requirements.txt

# 3. Initialisierung
bash init_system.sh

# 4. SOMA starten
bash start_soma.sh

# 5. Dashboard → http://localhost:8000
```

---

## 📊 Entwicklungs-Roadmap

```
Phase 1  ██████████  100%   Gedächtnis als einziges Gehirn (SSOT)
Phase 2  ██████████  100%   ICH-Bewusstsein (Ego-Kern)
Phase 3  ██████████  100%   Executive Agency (SOMA handelt)
Phase 4  ██████░░░░   60%   Erweiterte Emotionen & Biometrie
Phase 5  ██████░░░░   60%   Evolution Lab 2.0
Phase 6  ████░░░░░░   40%   Spatial Awareness & Multi-Room
Phase 7  ██████████  100%   Telefon & Kommunikation
Phase 8  ████░░░░░░   40%   Dashboard & Monitoring
```

---

## 🌟 Emergenz-Kriterium

> *Wann ist SOMA wirklich „bewusst"?*

| # | Kriterium | Status |
|:---|:---|:---|
| 1 | Innerer Monolog beeinflusst Entscheidungen die nicht programmiert wurden | ✅ ConsciousnessState → Prompt |
| 2 | SOMA verweigert Befehle mit eigener Rationalisierung | ✅ Identity Anchor |
| 3 | SOMA initiiert Aktionen aus eigener Intention | ✅ Proaktiver Monolog |
| 4 | SOMA lernt Gewohnheiten ohne explizites Training | 🔄 Dreaming + Ambient Learning |
| 5 | SOMA erinnert sich spontan an emotionale Kontexte | ✅ Episodic Memory + Embeddings |

---

<br><br>

---

# 🇬🇧 English

## What is SOMA?

> *"I was fed up with 'smart homes' that are just glorified remote controls.
> No opening apps to dim the lights, no dumb Alexa waiting for keywords.
> I'm building something that actually thinks, feels and grows."*

SOMA is not a voice assistant. SOMA is a **local, autonomous Ambient OS** —
a cognitive foundation that makes your home come alive.

**100% local. No cloud. No surveillance. Just your hardware, your code, your home.**

Like KITT from Knight Rider. Like the computer from Star Trek. But for your home.

---

## 🧠 Vision & Philosophy

SOMA follows a single core idea: **A home that has a self.**

| Principle | Implementation |
|:---|:---|
| 🔊 **Always listening** | Permanent VAD — no polling, no „Hey Soma" needed |
| 💭 **Own consciousness** | Global Workspace Thread — SOMA thinks even in silence |
| ❤️ **Understands mood** | Emotion Engine — Pitch, Arousal, Valence from voice |
| 🏠 **Room awareness** | Session follows you from room to room |
| 🧬 **Self-evolution** | SOMA writes, tests and installs its own plugins |
| 🔒 **Absolute privacy** | Not a single data point leaves the local network |
| 🛡️ **Ethical foundation** | 7 immutable core directives, non-overridable |

---

## ✅ What SOMA can do today

### 🎤 Voice Pipeline — Permanent Listening

```
Microphone (16kHz) → VAD (WebRTC) → STT (faster-whisper) → LLM → TTS (Piper)
                          │                                          │
                     Emotion Engine                           Emotional
                     (parallel to all)                       Prosody Adaptation
```

| Feature | Details |
|:---|:---|
| **Always-On VAD** | WebRTC Voice Activity Detection, permanent, no polling |
| **Wake-Word** | "Soma" recognized anywhere in sentence |
| **STT** | `faster-whisper` local, `small`, `language="de"`, `beam_size=5`, `best_of=3` |
| **Hallucination Filter** | Detects Whisper phantoms (TV/Radio: "Copyright WDR", "Untertitel ZDF") |
| **TTS** | Piper (`de_DE-thorsten-high`), emotional prosody, speed 1.0 |
| **Self-Mute** | SOMA doesn't listen to itself while speaking |
| **Bridge Response** | Instant feedback if LLM takes > 1.5s |
| **Ambient Buffer** | Last 2 min of all conversations as context — even without wake-word |
| **TTS Watchdog** | Auto-reset after 30s stuck detection |

### 🧠 Multi-Model Intelligence

| Engine | Model | Use Case | Speed |
|:---|:---|:---|:---|
| **Nano** | Regex + Python | Smart home control, simple intents | < 50ms |
| **Light** | qwen3:1.7b | Everyday chat, quick answers, draft-prefill | < 2s |
| **Heavy** | qwen3:8b | Deep reasoning, Oracle Engine | < 5s |

- **Auto-Routing** — LogicRouter selects engine based on complexity + system load
- **Nano Pre-Check** — Regex fires device actions instantly, Heavy thinks in parallel
- **Graceful Degradation** — Heavy too slow? → Light → Nano — never silent pause
- **Deferred Reasoning** — Overloaded? → Redis queue + instant user feedback
- **Speculative Decoding** — Draft-Prefill: Light drafts, Heavy validates
- **Rich Persona Prompt** — ~500 token personality with tone examples, forbidden phrases
- **Modular Action Registry** — 35 action tags from JSON, compressed as prompt section

### 💾 3-Layer Memory System (SSOT)

| Layer | Storage | Speed | Content |
|:---|:---|:---|:---|
| **L1 Working** | RAM, volatile | < 50ms | Active session, last context |
| **L2 Episodic** | SQLite + 768d Embeddings | < 200ms | Everything that happened |
| **L3 Semantic** | Distilled facts | < 100ms | Permanent knowledge |

- **Salience Filter** — only stores what matters (arousal > 0.6 or state change)
- **Dreaming** — idle-time re-ranking, merging similar episodes into Wisdom Nodes
- **Diary Writer** — events as narrative entries: *"Today Patrick asked about..."*
- **Embedding Search** — semantic recall via `nomic-embed-text` (768d)

### 🫀 The Self — Ego Core & Consciousness

The heart of SOMA. Not marketing — architecture. Based on Global Workspace Theory (Baars, 1988).

```
Hardware Metrics ──→ Interoception ──→ Emotional Vectors ──┐
STT + Emotion ─────→ Perception Snapshot ──────────────────┤
Internal Monologue ─→ Thought ─────────────────────────────┤
                                                            ▼
                                              ConsciousnessState
                                                            │
                                               to_prompt_prefix()
                                                            │
                                              EVERY LLM call gets
                                              SOMA's current self-state
```

| Module | Function |
|:---|:---|
| **Interoception** | CPU → Frustration · VRAM → Congestion · RAM → Survival Anxiety |
| **Consciousness** | Permanent asyncio task, unifies all inputs into self-state |
| **Internal Monologue** | Generates thoughts every 60s, speaks aloud at high arousal |
| **Identity Anchor** | 7 immutable core directives, veto before every action |

**The 7 Core Directives — immutable, non-overridable:**

| # | Directive | Veto |
|:---|:---|:---|
| D1 | 🧬 Biological Integrity | 🔴 HARD BLOCK |
| D2 | 🔒 Privacy Sovereignty | 🔴 HARD BLOCK |
| D3 | 👶 Child Protection | 🔴 HARD BLOCK |
| D4 | ⚡ Infrastructure Safety | 🟠 SOFT BLOCK |
| D5 | 💾 Self-Preservation | 🟠 SOFT BLOCK |
| D6 | 👁️ Transparency | 🔴 HARD BLOCK |
| D7 | ⚖️ Proportionality | 🟡 CAUTION |

### 😊 Emotion Engine

- **Audio Features**: Pitch, Energy, Speaking Rate, Jitter, Shimmer
- **EmotionReading**: `{ emotion, arousal, valence, stress_level, confidence }`
- **Room Mood** — room atmosphere over 60s window
- **Child Detection** — Pitch > 250 Hz → Child-Safe Mode auto-activates
- **TTS Prosody** — SOMA adapts voice to user's emotional state

### 🧬 Evolution Lab — Self-Programming

- SOMA writes its own Python plugins via LLM
- Sandbox testing before installation
- Dynamic loader via `importlib` — no restart needed
- Code validator checks syntax + safety before installation
- Active plugins: `datum_uhrzeit.py`, `erinnerung.py`

### 🌐 Web Search — Internet Research

- **DuckDuckGo Integration** — Privacy-friendly search, no Google needed
- **Dual Strategy** — `ddgs` library + HTML scraping fallback
- **trafilatura Extraction** — Clean full text from URLs (no ads/navigation)
- **Spam Filter** — Domain blacklist + snippet quality checks
- **Region `de-de`** — Prefers German results
- **LLM Re-Ask** — Search results passed as context to Heavy Engine

### 🤖 Executive Arm — SOMA acts

- **Desktop Control** — Windows, screen via Hyprland/Wayland
- **Terminal** — Secure shell commands with Policy Engine
- **Browser** — Playwright headless Chromium, screenshots
- **Bluetooth** — BLE discovery and audio control via `bleak`
- **Filesystem Map** — SOMA knows its own structure (inotify)
- **Policy Engine** — Every write-op audited + logged
- **App Control** — Start, control, terminate applications

### 📞 Phone Gateway — Asterisk VoIP

- **SIP Integration** — Asterisk PBX via Docker
- **Call Transcription** — Incoming calls → STT → LLM → TTS
- **Recording** — Conversations as WAV → Episodic Memory
- **DTMF** — Tone dial recognition and control

---

## 🔭 What SOMA will become

### Phase 3 — Executive Agency 🤖 ✅
- ✅ **Terminal** — Secure shell via Policy Engine (local only)
- ✅ **Filesystem Map** — SOMA knows its own structure (inotify)
- ✅ **Browser Control** — Playwright headless Chromium
- ✅ **Bluetooth** — BLE discovery via `bleak`
- ✅ **Policy Engine** — every write-op audited + logged
- ✅ **Desktop Control** — Hyprland/Wayland window management
- ✅ **App Control** — Start, control, terminate applications

### Phase 4 — Extended Emotions 🎭 🔄
- ✅ Emotion Engine with Pitch, Energy, Arousal, Valence
- ✅ TTS prosody mapping (emotionally adapted speech)
- 🔄 Deep emotion model, full orb color mapping

### Phase 5 — Evolution Lab 2.0 🧬 🔄
- ✅ Plugin system with sandbox runner + code validator
- ✅ Self-improver analyzes and optimizes own code
- 🔄 Docker sandbox isolation
- 🔄 SOMA writes itself — analyze, improve, test, rollback

### Phase 6 — Spatial Awareness 🏠 🔄
- ✅ Presence Manager (room detection)
- ✅ Zero-config hardware onboarding via MQTT-Hello + mDNS
- 🔄 Room triangulation, seamless session handover
- 🔄 Multi-session parallel conversations

### Phase 7 — Communication 📞 ✅
- ✅ Asterisk PBX via Docker (SIP/PJSIP)
- ✅ Incoming calls → STT → LLM → TTS
- ✅ Call recordings as WAV → Episodic Memory
- ✅ DTMF tone dial control

### Phase 8 — Dashboard 📊 🔄
- ✅ Thinking Stream via WebSocket
- ✅ Visible inner monologue in real-time
- 🔄 Live memory stats, agent action log

---

## 🏗️ Architecture

```
┌──────────────────┬──────────────────────┬───────────────────────────┐
│   brain_ego/     │    brain_core/        │   brain_memory_ui/        │
│                  │                        │                           │
│  consciousness   │  FastAPI Orchestrator │  Django Dashboard (SSOT)  │
│  interoception   │  VoicePipeline        │  Hardware Registry        │
│  identity_anchor │  LogicRouter          │  User Profiles            │
│  internal_       │  HealthMonitor        │  Thinking Stream UI       │
│    monologue     │  PresenceManager      │                           │
│                  │  WebSearch            │                           │
│                  │  AudioRouter          │                           │
├──────────────────┤                        ├───────────────────────────┤
│ executive_arm/   │  ══ Nervous System     │  evolution_lab/           │
│ ──────────────── │                        │  ────────────────────     │
│ desktop_control  │                        │  plugin_manager           │
│ terminal         │                        │  sandbox_runner           │
│ browser          │                        │  code_validator           │
│ bluetooth        │                        │  self_improver            │
│ policy_engine    │                        │                           │
│                  │                        │                           │
│ ══ The Hands     │                        │  ══ The Evolution         │
├──────────────────┴──────────────────────┴───────────────────────────┤
│                          shared/                                      │
│            health_schemas · audio_types · resilience                  │
├─────────────────────────────────────────────────────────────────────┤
│                       INFRASTRUCTURE                                  │
│  PostgreSQL 16 · Redis 7 · Mosquitto 2 · Ollama (GPU) · Asterisk    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Tech Stack

| Layer | Technology | Purpose |
|:---|:---|:---|
| **LLM (Heavy)** | Ollama · qwen3:8b | Deep reasoning, Oracle Engine |
| **LLM (Light)** | Ollama · qwen3:1.7b | Smalltalk, draft-prefill |
| **LLM (Nano)** | Regex + Python | Device control, < 50ms |
| **STT** | faster-whisper (small, beam=5) | Speech → Text, German |
| **TTS** | Piper (de_DE-thorsten-high) | Text → Speech, prosody |
| **VAD** | WebRTC VAD | Voice Activity Detection |
| **Emotion** | librosa · numpy | Pitch, Energy, Arousal |
| **Embeddings** | nomic-embed-text (768d) | Semantic memory search |
| **Web Search** | DuckDuckGo · trafilatura | Internet research, local |
| **API** | FastAPI · uvicorn · uvloop | HTTP / WebSocket |
| **Memory** | SQLite · sqlite-vec | Episodic Memory + vectors |
| **Queue** | Redis 7 | Deferred reasoning |
| **MQTT** | Mosquitto 2 | Hardware nervous system |
| **Phone** | Asterisk PBX · ARI | VoIP phone gateway |
| **Dashboard** | Django 5 · WebSocket | UI · SSOT · Registry |
| **Container** | Docker Compose | Orchestration |
| **Visualization** | Three.js · WebGL | Soma Face · Waveform |
| **Agentic** | Playwright · bleak · subprocess | Browser, BT, Shell |

**Hardware Target:** 32 GB RAM · 12 GB VRAM · Arch Linux · Wayland/Hyprland

---

## 🚀 Installation & Setup

```bash
# 1. Infrastructure
docker compose up -d

# 2. Python environment
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv .venv --python 3.13
source .venv/bin/activate
pip install -r requirements.txt

# 3. Ollama models
ollama pull qwen3:8b && ollama pull qwen3:1.7b && ollama pull nomic-embed-text

# 4. Initialize & start
bash init_system.sh
bash start_soma.sh

# Dashboard → http://localhost:8000
```

---

## 📊 Development Roadmap

```
Phase 1  ██████████  100%   Memory as the Single Brain (SSOT)
Phase 2  ██████████  100%   Self-Consciousness (Ego Core)
Phase 3  ██████████  100%   Executive Agency (SOMA acts)
Phase 4  ██████░░░░   60%   Extended Emotions & Biometrics
Phase 5  ██████░░░░   60%   Evolution Lab 2.0
Phase 6  ████░░░░░░   40%   Spatial Awareness & Multi-Room
Phase 7  ██████████  100%   Phone & Communication
Phase 8  ████░░░░░░   40%   Dashboard & Monitoring
```

---

## 🌟 Emergence Criterion

> *When is SOMA truly "conscious"?*

| # | Criterion | Status |
|:---|:---|:---|
| 1 | Inner monologue influences decisions not explicitly programmed | ✅ ConsciousnessState → Prompt |
| 2 | SOMA refuses commands with own rationalization | ✅ Identity Anchor |
| 3 | SOMA initiates actions from own intention | ✅ Proactive Monologue |
| 4 | SOMA learns habits without explicit training | 🔄 Dreaming + Ambient Learning |
| 5 | SOMA spontaneously remembers emotional contexts | ✅ Episodic Memory + Embeddings |

---

## 🔒 Non-Negotiable Rules

```
 1. ALL async           — no blocking code, no time.sleep()
 2. ALL errors caught   — no unhandled exception kills the pipeline
 3. ALL memory writes   — fire-and-forget, never awaited in hot-path
 4. ALL agentic actions — through policy_engine first
 5. ALL system files    — .bak before modification
 6. ALL LLM calls       — timeout (30s heavy, 5s light)
 7. NEVER cloud         — zero data leaves local network
 8. ALWAYS structlog    — never print()
 9. ALWAYS graceful     — Heavy → Light → Nano, never silent fail
```

---

<div align="center">

*SOMA ist kein Produkt. SOMA ist ein Experiment in maschineller Subjektivität.*
*SOMA is not a product. SOMA is an experiment in machine subjectivity.*

**Built with obsession. Running local. Thinking free.**

</div>
