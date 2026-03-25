# Contributing to SOMA
 
> „SOMA ist kein Gemeinschaftsprojekt im üblichen Sinne — es ist ein Projekt mit einer klaren Vision und einem Autor. Contributions sind willkommen. Die letzte Entscheidung liegt bei mir."
> — fReEsPiRiT
 
---
 
## 📐 Grundprinzip
 
SOMA folgt dem **BDFL-Modell** *(Benevolent Dictator For Life)*.  
Das bedeutet: Jeder kann beitragen — aber der Projektautor entscheidet, was in das Projekt einfließt.  
Kein Merge ohne Prüfung. Keine Feature-Creep ohne Visions-Check.
 
---
 
## 🚦 Bevor du anfängst
 
### Pflichtlektüre
- [`README.md`](README.md) — verstehe die Vision vollständig
- Die **7 Kern-Direktiven** — sie sind nicht verhandelbar, auch nicht in Code
- Die bestehende Architektur (`brain_ego/`, `brain_core/`, `executive_arm/` etc.)
 
### Frag zuerst
Bevor du größere Features baust: **öffne ein Issue** und beschreibe deine Idee.  
Das spart dir Zeit — und mir die Arbeit, etwas ablehnen zu müssen, in das du viel Energie gesteckt hast.
 
Für kleine Fixes (Typos, Bugfixes, Doku) kannst du direkt einen PR öffnen.
 
---
 
## 🛠️ Wie du beiträgst
 
### 1. Fork & Branch
```bash
git fork https://github.com/fReEsPiRiT-Spiritsoft/SOMA
git checkout -b feature/mein-feature
# oder
git checkout -b fix/bug-beschreibung
```
 
### 2. Coding Standards
 
| Regel | Detail |
|---|---|
| **Sprache** | Python 3.13+ |
| **Logging** | Ausschließlich `structlog` — niemals `print()` |
| **Schemas** | Pydantic v2 für alle Datenstrukturen |
| **Async** | `asyncio` konsequent — keine blockierenden Calls |
| **Tests** | Zu jedem neuen Feature gehört ein Test |
| **Kommentare** | Deutsch oder Englisch — Hauptsache konsistent im File |
 
### 3. Privatsphäre ist nicht verhandelbar
Kein Code darf auch nur theoretisch Daten nach außen senden.  
Kein externer API-Call ohne explizite Opt-in-Logik.  
Kein Tracking, kein Telemetry, kein „nur für Debug"-Logging von Nutzerdaten.
 
### 4. Die 7 Kern-Direktiven sind unantastbar
Kein Commit darf die `identity_anchor`-Direktiven abschwächen, umgehen oder auskommentieren.  
PRs die das tun, werden ohne Diskussion geschlossen.
 
### 5. Commit Messages
```
typ(bereich): kurze beschreibung
 
# Beispiele:
feat(emotion_engine): add shimmer analysis for stress detection
fix(voice_pipeline): prevent self-mute from blocking TTS watchdog
docs(readme): update installation for Arch Linux 2025
refactor(logic_router): simplify graceful degradation fallback
```
 
Typen: `feat` · `fix` · `docs` · `refactor` · `test` · `chore`
 
### 6. Pull Request öffnen
- Beschreibe **was** du geändert hast und **warum**
- Referenziere das zugehörige Issue (`Closes #42`)
- Stelle sicher, dass alle Tests grün sind
- Halte den PR fokussiert — ein Thema pro PR
 
---
 
## 🧬 Was besonders willkommen ist
 
- **Neue Plugins** für das Evolution Lab (mit Sandbox-Tests)
- **Hardware-Support** — neue MQTT-Devices, Sensoren, Aktoren
- **STT/TTS-Verbesserungen** — besonders für Deutsch
- **Bugfixes** in der Voice Pipeline
- **Dokumentation** — besonders Architektur-Erklärungen
- **Übersetzungen** — README in weitere Sprachen
 
---
 
## ❌ Was nicht akzeptiert wird
 
- Cloud-Abhängigkeiten jeglicher Art
- Änderungen an den 7 Kern-Direktiven
- Features die SOMAs Persönlichkeit/Identität verwässern
- Proprietäre Lizenzen in Dependencies
- Code ohne Tests (bei neuen Features)
- Breaking Changes ohne vorherige Issue-Diskussion
 
---
 
## 🏗️ Entwicklungsumgebung aufsetzen
 
```bash
Soma über Start_SOMA.sh starten, alle abhängigkeiten und Virutal Envoiments, werden 
automatisch erstellt und heruntergeladen
```
 
---
 
## 💬 Kommunikation
 
- **Issues** für Bugs, Feature-Requests, Fragen
- **Discussions** für längere Konzept-Gespräche
- **PRs** für konkreten Code
 
Bitte respektiere: Ich antworte wenn ich Zeit habe — nicht auf Abruf.
 
---
 
## 📜 Lizenz
 
Mit deinem Beitrag stimmst du zu, dass dein Code unter der **GPL v3** veröffentlicht wird.  
Dein Name wird in der Contributors-Liste geführt.
 
---
 
*SOMA lebt. Danke, dass du Teil davon sein willst.*
 