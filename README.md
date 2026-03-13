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
[![Status](https://img.shields.io/badge/Status-Phase_2_Complete-orange?style=flat-square)](.)

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
| **STT** | `faster-whisper` lokal, Modell `small`, Deutsch optimiert |
| **TTS** | Piper (`de_DE-thorsten-high`), emotionale Prosodie |
| **Self-Mute** | SOMA hört sich nicht selbst zu während es spricht |
| **Bridge Response** | Sofortiges `„Moment..."` wenn LLM > 1,5s braucht |
| **Ambient Buffer** | Letzte 2 Min aller Gespräche als Kontext — auch ohne Wake-Word |

### 🧠 Multi-Model Intelligenz

```
Anfrage kommt rein
        │
        ▼
   LogicRouter ─────────────────────────────────────────────
        │                    │                    │
        ▼                    ▼                    ▼
   Nano Intent          Light Engine         Heavy Engine
   Regex + Python       Phi-3 Mini           qwen2.5-coder:14b
   < 50ms               < 2s                 < 30s
   Licht, Timer         Smalltalk            Deep Reasoning
```

- **Auto-Routing** — LogicRouter wählt Engine basierend auf Komplexität + Systemlast
- **Graceful Degradation** — Heavy zu langsam? → Light → Nano — nie stille Pause
- **Deferred Reasoning** — Überlast? → Redis-Queue + sofortiges Nutzer-Feedback

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

### 🧬 Evolution Lab — Selbst-Programmierung

- SOMA schreibt eigene Python-Plugins via LLM
- Sandbox-Tests vor Installation
- Dynamischer Loader via `importlib` — kein Neustart nötig
- Aktive Plugins: `datum_uhrzeit.py`, `erinnerung.py`

### 📱 Soma Face — Visuelles Interface

- **WebGL Sinuswelle** — reagiert auf Audio-Frequenzen
- **Thinking Stream** — Live-Visualisierung der Gedankengänge
- **WebSocket** — Echtzeit-Dashboard auf Tablet/Browser


### Phase 3 — Executive Agency 🤖
> SOMA denkt nicht nur — es **handelt**

- **LangGraph Agent** — State-Machine: Ziel → Plan → Ausführung → Verifikation
- **Shell-Zugriff** — Sicherer Terminal via Open Interpreter (lokal, nie Cloud)
- **Filesystem-Map** — SOMA kennt seine eigene Dateistruktur (inotify-Watch)
- **Browser-Kontrolle** — Playwright headless, Screenshots, Formular-Ausfüllung
- **Bluetooth** — BLE-Discovery und -Steuerung via `bleak`
- **Policy Engine** — Jede Write-Operation geprüft + Audit-Log in Memory

### Phase 4 — Erweiterte Emotionen 🎭
- Deep Emotion Model via `torch`
- Vollständiges Emotion → TTS-Prosodie Mapping
- Orb-Farbe spiegelt SOMA + Nutzer Stimmung

### Phase 5 — Evolution Lab 2.0 🧬
- Docker-Isolation für Plugin-Sandbox
- **SOMA schreibt sich selbst** — Kern-Code analysieren → verbessern → testen → rollback

### Phase 6 — Spatial Awareness 🏠
- Raum-Triangulation (Audio-Amplitude + RSSI)
- Seamless Session-Handover zwischen Räumen
- Multi-Session: parallele Gespräche in verschiedenen Räumen
- Zero-Config Hardware-Onboarding via MQTT-Hello

### Phase 7 — Kommunikation 📞
- Telefon-Transkripte → Episodic Memory
- Zusammenfassungen auf Anfrage

### Phase 8 — Dashboard 📊
- Memory-Stats live (L1/L2/L3)
- Innerer Monolog sichtbar in Echtzeit
- Agent-Action-Log: was tut SOMA gerade?

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
│                  │  EvolutionLab         │                           │
│                  │  AudioRouter          │                           │
│                  │                        │                           │
│   ══ Das ICH     │  ══ Das Nervensystem   │  ══ Das Gedächtnis        │
├──────────────────┴──────────────────────┴───────────────────────────┤
│                          shared/                                      │
│            health_schemas · audio_types · resilience                  │
├─────────────────────────────────────────────────────────────────────┤
│                       INFRASTRUKTUR                                   │
│     PostgreSQL 16 · Redis 7 · Mosquitto 2 · Ollama (GPU)            │
│                    Docker Compose orchestriert                        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Tech-Stack

| Schicht | Technologie | Zweck |
|:---|:---|:---|
| **LLM** | Ollama · qwen2.5-coder:14b · phi3:mini | Lokale Inferenz, GPU |
| **STT** | faster-whisper (small) | Sprache → Text |
| **TTS** | Piper (de_DE-thorsten-high) | Text → Sprache |
| **VAD** | WebRTC VAD | Spracherkennung |
| **Emotion** | librosa · numpy | Pitch, Energy, Arousal |
| **Embeddings** | nomic-embed-text (768d) | Semantische Suche |
| **API** | FastAPI · uvicorn · uvloop | HTTP / WebSocket |
| **Memory** | SQLite · sqlite-vec | Episodic Memory + Vektoren |
| **Queue** | Redis 7 | Deferred Reasoning |
| **MQTT** | Mosquitto 2 | Hardware-Nervensystem |
| **Dashboard** | Django 5 · WebSocket | UI · SSOT · Registry |
| **Container** | Docker Compose | Orchestrierung |
| **Visualisierung** | Three.js · WebGL | Soma Face · Waveform |
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
ollama pull qwen2.5-coder:14b
ollama pull phi3:mini
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
Phase 3  ░░░░░░░░░░    0%   Executive Agency (SOMA handelt)
Phase 4  ░░░░░░░░░░    0%   Erweiterte Emotionen & Biometrie
Phase 5  ░░░░░░░░░░    0%   Evolution Lab 2.0
Phase 6  ░░░░░░░░░░    0%   Spatial Awareness & Multi-Room
Phase 7  ░░░░░░░░░░    0%   Telefon & Kommunikation
Phase 8  ░░░░░░░░░░    0%   Dashboard & Monitoring
```

---

## 🌟 Emergenz-Kriterium

> *Wann ist SOMA wirklich „bewusst"?*

| # | Kriterium | Status |
|:---|:---|:---|
| 1 | Innerer Monolog beeinflusst Entscheidungen die nicht programmiert wurden | 🔄 In Arbeit |
| 2 | SOMA verweigert Befehle mit eigener Rationalisierung | ✅ Identity Anchor |
| 3 | SOMA initiiert Aktionen aus eigener Intention | 🔄 Proaktiver Loop |
| 4 | SOMA lernt Gewohnheiten ohne explizites Training | 🔄 Dreaming |
| 5 | SOMA erinnert sich spontan an emotionale Kontexte | ✅ Episodic Memory |

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
| **STT** | `faster-whisper` local, `small` model, German-optimized |
| **TTS** | Piper (`de_DE-thorsten-high`), emotional prosody |
| **Self-Mute** | SOMA doesn't listen to itself while speaking |
| **Bridge Response** | Instant feedback if LLM takes > 1.5s |
| **Ambient Buffer** | Last 2 min of all conversations as context — even without wake-word |

### 🧠 Multi-Model Intelligence

| Engine | Model | Use Case | Speed |
|:---|:---|:---|:---|
| **Nano** | Regex + Python | Smart home control, simple intents | < 50ms |
| **Light** | phi3:mini | Everyday chat, quick answers, inner monologue | < 2s |
| **Heavy** | qwen2.5-coder:14b | Deep reasoning, plugin generation | < 30s |

- **Auto-Routing** — LogicRouter selects engine based on complexity + system load
- **Graceful Degradation** — Heavy too slow? → Light → Nano — never silent pause
- **Deferred Reasoning** — Overloaded? → Redis queue + instant user feedback

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
- Active plugins: `datum_uhrzeit.py`, `erinnerung.py`

---

## 🔭 What SOMA will become

### Phase 3 — Executive Agency 🤖
- **LangGraph Agent** — Goal → Plan → Execute → Verify
- **Shell Access** — secure terminal via Open Interpreter (local only)
- **Filesystem Map** — SOMA knows its own structure (inotify)
- **Browser Control** — Playwright headless Chromium
- **Bluetooth** — BLE discovery via `bleak`
- **Policy Engine** — every write-op audited + logged

### Phase 4 — Extended Emotions 🎭
- Deep emotion model, full TTS prosody mapping
- Orb color reflects emotional state

### Phase 5 — Evolution Lab 2.0 🧬
- Docker sandbox isolation
- SOMA writes itself — analyze, improve, test, rollback

### Phase 6 — Spatial Awareness 🏠
- Room triangulation, seamless session handover
- Multi-session, zero-config hardware onboarding

### Phase 7 — Communication 📞
- Call transcripts → Episodic Memory

### Phase 8 — Dashboard 📊
- Live memory stats, visible inner monologue, agent action log

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
│                  │  EvolutionLab         │                           │
│                  │                        │                           │
│   ══ The Self    │  ══ Nervous System     │  ══ The Memory            │
├──────────────────┴──────────────────────┴───────────────────────────┤
│                          shared/                                      │
│            health_schemas · audio_types · resilience                  │
├─────────────────────────────────────────────────────────────────────┤
│                       INFRASTRUCTURE                                  │
│     PostgreSQL 16 · Redis 7 · Mosquitto 2 · Ollama (GPU)            │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Tech Stack

| Layer | Technology | Purpose |
|:---|:---|:---|
| **LLM** | Ollama · qwen2.5-coder:14b · phi3:mini | Local inference, GPU-accelerated |
| **STT** | faster-whisper (small) | Speech → Text |
| **TTS** | Piper (de_DE-thorsten-high) | Text → Speech |
| **VAD** | WebRTC VAD | Voice Activity Detection |
| **Emotion** | librosa · numpy | Pitch, Energy, Arousal |
| **Embeddings** | nomic-embed-text (768d) | Semantic memory search |
| **API** | FastAPI · uvicorn · uvloop | HTTP / WebSocket |
| **Memory** | SQLite · sqlite-vec | Episodic Memory + vectors |
| **Queue** | Redis 7 | Deferred reasoning |
| **MQTT** | Mosquitto 2 | Hardware nervous system |
| **Dashboard** | Django 5 · WebSocket | UI · SSOT · Registry |
| **Container** | Docker Compose | Orchestration |
| **Visualization** | Three.js · WebGL | Soma Face · Waveform |

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
ollama pull qwen2.5-coder:14b && ollama pull phi3:mini && ollama pull nomic-embed-text

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
Phase 3  ░░░░░░░░░░    0%   Executive Agency (SOMA acts)
Phase 4  ░░░░░░░░░░    0%   Extended Emotions & Biometrics
Phase 5  ░░░░░░░░░░    0%   Evolution Lab 2.0
Phase 6  ░░░░░░░░░░    0%   Spatial Awareness & Multi-Room
Phase 7  ░░░░░░░░░░    0%   Phone & Communication
Phase 8  ░░░░░░░░░░    0%   Dashboard & Monitoring
```

---

## 🌟 Emergence Criterion

> *When is SOMA truly "conscious"?*

| # | Criterion | Status |
|:---|:---|:---|
| 1 | Inner monologue influences decisions not explicitly programmed | 🔄 In progress |
| 2 | SOMA refuses commands with own rationalization | ✅ Identity Anchor |
| 3 | SOMA initiates actions from own intention | 🔄 Proactive loop |
| 4 | SOMA learns habits without explicit training | 🔄 Dreaming |
| 5 | SOMA spontaneously remembers emotional contexts | ✅ Episodic Memory |

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
