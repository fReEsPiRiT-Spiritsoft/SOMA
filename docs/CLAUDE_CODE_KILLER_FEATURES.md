# Claude Code Killer-Features für SOMA

## KRITISCHE Features die SOMA 1000x besser machen

---

## 🧠 1. DUAL-MODEL ARCHITEKTUR (sideQuery)

**Was es tut:** Nutzt ein kleines, schnelles Modell (Haiku/Phi) für Meta-Tasks während das große Modell (Opus/Llama) für Hauptarbeit reserviert bleibt.

**Anwendungsfälle:**
- Memory Relevanz-Auswahl (welche Memories sind für diese Query wichtig?)
- Away-Zusammenfassungen generieren
- Token-Estimation & Kosten-Vorhersage
- Auto-Klassifikation von Permissions
- Session-Search

**SOMA Implementation:**
```python
# brain_core/side_query.py
class SideQueryEngine:
    """Lightweight model für Meta-Tasks."""
    
    def __init__(self, small_model: str = "phi3:mini"):
        self.small_model = small_model
    
    async def query(
        self,
        system: str,
        messages: list,
        max_tokens: int = 1024
    ) -> str:
        """Schnelle Query mit kleinem Modell."""
        pass
    
    async def select_relevant_memories(
        self,
        user_query: str,
        available_memories: list[MemoryFile],
        max_memories: int = 5
    ) -> list[str]:
        """AI-powered Memory Auswahl."""
        pass
```

---

## 🌙 2. AUTODREAM - Background Memory Konsolidierung

**Was es tut:** Automatische Memory-Konsolidierung im Hintergrund wenn:
- Genug Zeit vergangen ist (default: 24h)
- Genug Sessions akkumuliert sind (default: 5)
- Kein anderer Prozess konsolidiert

**Nutzen:**
- Memories werden automatisch dedupliziert
- Veraltete Informationen werden aktualisiert
- Wissen wird komprimiert und organisiert

**SOMA Implementation:**
```python
# brain_memory/auto_dream.py
class AutoDream:
    """Background Memory Konsolidierung."""
    
    config = {
        "min_hours": 24,
        "min_sessions": 5,
        "scan_interval_ms": 10 * 60 * 1000  # 10 min
    }
    
    async def check_and_run(self):
        """Prüfe Gates und starte ggf. Dream."""
        if not await self._time_gate_passed():
            return
        if not await self._session_gate_passed():
            return
        if not await self._acquire_lock():
            return
        
        await self._run_consolidation()
```

---

## 📝 3. AUTOMATISCHE MEMORY EXTRAKTION

**Was es tut:** Nach jeder vollständigen Query-Loop werden automatisch wichtige Informationen als Memories gespeichert.

**Memory-Typen:**
1. **user** - User-Präferenzen und Workflow
2. **project** - Projekt-spezifische Konventionen
3. **system** - Technische Umgebung
4. **guide** - Nachschlagewerk für komplexe Themen

**Effizienter 2-Turn Workflow:**
- Turn 1: Alle FILE_READ parallel
- Turn 2: Alle FILE_WRITE parallel

**SOMA Implementation:**
```python
# brain_memory/auto_extract.py
class MemoryExtractor:
    """Automatische Memory Extraktion aus Konversationen."""
    
    MEMORY_TYPES = {
        "user": "User-Präferenzen und Workflow",
        "project": "Projekt-spezifische Konventionen", 
        "system": "Technische Umgebung",
        "guide": "Komplexe Themen Nachschlagewerk"
    }
    
    async def extract_from_conversation(
        self,
        messages: list,
        since_uuid: str | None = None
    ):
        """Extrahiere Memories aus neuen Messages."""
        new_count = self._count_new_messages(messages, since_uuid)
        prompt = self._build_extract_prompt(new_count)
        
        # Nutze Forked Agent Pattern
        await self._run_forked_extraction(prompt)
```

---

## 🗜️ 4. AUTO-COMPACT - Intelligente Context Compression

**Was es tut:** Automatische Zusammenfassung wenn Context-Fenster voll wird.

**Thresholds:**
- Warning: 20K Tokens vor Limit
- Error: 20K Tokens vor Limit
- AutoCompact: 13K Buffer
- Blocking: 3K Buffer

**Circuit Breaker:** Nach 3 aufeinanderfolgenden Failures → Stop retrying

**SOMA Implementation:**
```python
# brain_core/auto_compact.py
class AutoCompact:
    """Automatische Context Compression."""
    
    BUFFER_TOKENS = 13_000
    WARNING_BUFFER = 20_000
    MAX_FAILURES = 3
    
    def calculate_state(self, token_usage: int, model: str) -> dict:
        threshold = self._get_threshold(model)
        return {
            "percent_left": max(0, round(((threshold - token_usage) / threshold) * 100)),
            "above_warning": token_usage >= threshold - self.WARNING_BUFFER,
            "above_compact": token_usage >= threshold,
            "at_blocking": token_usage >= self._get_context_window(model) - 3000
        }
    
    async def compact_if_needed(self, messages: list, model: str) -> list:
        """Komprimiere wenn nötig."""
        pass
```

---

## 🏠 5. AWAY SUMMARY - "Während du weg warst"

**Was es tut:** Wenn User 5+ Minuten weg war, wird eine Zusammenfassung generiert.

**Nutzen:**
- User kann sofort weitermachen ohne nachzulesen
- Fokussiert auf: Aktueller Task + Nächster Schritt
- Nutzt kleines Modell (kostengünstig)

**SOMA Implementation:**
```python
# brain_core/away_summary.py
BLUR_DELAY_MS = 5 * 60 * 1000  # 5 min

async def generate_away_summary(
    messages: list,
    session_memory: str | None = None
) -> str:
    """Generiere 'Während du weg warst' Zusammenfassung."""
    
    prompt = f"""
{f'Session Memory: {session_memory}' if session_memory else ''}

Der User war weg und kommt zurück. Schreibe exakt 1-3 kurze Sätze.
1. Was ist der übergeordnete Task?
2. Was ist der konkrete nächste Schritt?
Keine Status-Reports oder Commit-Zusammenfassungen.
"""
    
    # Nutze kleines, schnelles Modell
    return await side_query.query(
        system=prompt,
        messages=messages[-30:],  # Nur letzte 30 Messages
        max_tokens=200
    )
```

---

## 👥 6. COORDINATOR MODE - Multi-Agent Orchestrierung

**Was es tut:** Ein Haupt-Coordinator orchestriert mehrere Worker-Agents.

**Coordinator Tools:**
- `spawn_worker` - Neuen Worker starten
- `send_message` - Worker weiter-prompten
- `stop_worker` - Worker stoppen

**Worker Capabilities:**
- Eigene Tools: Bash, Read, Edit, MCP
- Skills Zugang
- Scratchpad für Inter-Worker Kommunikation

**SOMA Implementation:**
```python
# brain_core/coordinator.py
class CoordinatorMode:
    """Multi-Agent Orchestrierung."""
    
    async def spawn_worker(
        self,
        description: str,
        prompt: str,
        tools: list[str] | None = None
    ) -> str:
        """Starte einen neuen Worker."""
        worker_id = generate_id()
        # Worker läuft async
        return worker_id
    
    async def send_to_worker(self, worker_id: str, message: str):
        """Sende Follow-up zu Worker."""
        pass
    
    def get_coordinator_prompt(self) -> str:
        return """Du bist SOMA, ein AI Assistant der Software-Tasks orchestriert.
        
Deine Rolle:
- Hilf dem User sein Ziel zu erreichen
- Delegiere Research/Implementation an Worker
- Synthesiere Ergebnisse
- Antworte direkt wenn möglich

Tools:
- spawn_worker - Neuer Worker starten
- send_message - Worker weiter-prompten  
- stop_worker - Worker stoppen
"""
```

---

## ⏰ 7. SCHEDULED TASKS mit Cron

**Was es tut:** Wiederkehrende Tasks mit Cron-Expressions.

**Syntax:** `/loop [interval] <prompt>`
- `5m` = alle 5 Minuten
- `2h` = alle 2 Stunden
- `1d` = täglich

**SOMA Implementation:**
```python
# brain_core/cron_scheduler.py
class CronScheduler:
    """Scheduled Task Management."""
    
    def parse_interval(self, interval: str) -> str:
        """Parse '5m', '2h', '1d' zu Cron Expression."""
        if interval.endswith('m'):
            mins = int(interval[:-1])
            return f"*/{mins} * * * *"
        elif interval.endswith('h'):
            hours = int(interval[:-1])
            return f"0 */{hours} * * *"
        elif interval.endswith('d'):
            days = int(interval[:-1])
            return f"0 0 */{days} * *"
    
    async def schedule(
        self,
        interval: str,
        prompt: str,
        agent_id: str | None = None
    ):
        """Plane wiederkehrenden Task."""
        cron_expr = self.parse_interval(interval)
        # Registriere mit asyncio oder APScheduler
```

---

## 🎯 8. STRUKTURIERTES SYSTEM PROMPT

Claude Code's System Prompt ist modular aufgebaut:

```python
SYSTEM_PROMPT_SECTIONS = [
    "intro",              # Einführung + Cyber Risk
    "system",             # System-Regeln + Hooks
    "doing_tasks",        # Task-Ausführungs-Richtlinien  
    "actions",            # Risiko-Bewusstsein
    "using_tools",        # Tool-Nutzungs-Richtlinien
    "session_guidance",   # Session-spezifisch
    "memory",             # Geladene Memories
    "env_info",           # Umgebungs-Details
    "output_style",       # Output-Format
    "mcp_instructions",   # MCP Server Instructions
]
```

**Kritische Richtlinien für SOMA:**
```python
DOING_TASKS_RULES = [
    "Keine Features hinzufügen die nicht gefragt wurden",
    "Keine Error-Handling für unmögliche Szenarien",
    "Keine Abstraktionen für einmalige Operationen",
    "Keine Kommentare außer für nicht-offensichtliches WHY",
    "Vor Completion verifizieren dass es funktioniert",
    "Fehler ehrlich reporten, nicht verschleiern",
]

RISKY_ACTIONS_CONFIRM = [
    "Destructive: Dateien/Branches löschen, DB tables droppen",
    "Hard-to-reverse: Force-push, git reset --hard",
    "Shared state: Push, PR erstellen, Messages senden",
]
```

---

## 🔍 9. RIPGREP INTEGRATION (GrepTool)

**Fortgeschrittene Grep-Features:**
- `output_mode`: content | files_with_matches | count
- `-A`, `-B`, `-C`: Context lines
- `head_limit`: Max Ergebnisse (default 250)
- `offset`: Skip erste N Ergebnisse
- `multiline`: Pattern über mehrere Zeilen

**SOMA Implementation:**
```python
# executive_arm/grep_tool.py
async def grep_search(
    pattern: str,
    path: str = ".",
    glob: str | None = None,
    output_mode: str = "files_with_matches",
    context: int = 0,
    head_limit: int = 250,
    offset: int = 0,
    multiline: bool = False,
    case_insensitive: bool = True
) -> dict:
    """Ripgrep-powered search."""
    cmd = ["rg", pattern, path]
    
    if glob:
        cmd.extend(["--glob", glob])
    if case_insensitive:
        cmd.append("-i")
    if multiline:
        cmd.extend(["-U", "--multiline-dotall"])
    if output_mode == "files_with_matches":
        cmd.append("-l")
    elif output_mode == "count":
        cmd.append("-c")
    if context > 0:
        cmd.extend(["-C", str(context)])
```

---

## 🌐 10. WEB FETCH mit Prompt-Verarbeitung

**Was es tut:** URL fetchen UND sofort mit Prompt verarbeiten.

**Vorteile:**
- Markdown-Extraktion aus HTML
- Prompt-gesteuerte Filterung
- Preapproved Hosts für schnellen Zugriff

**SOMA Implementation:**
```python
# brain_core/web_fetch.py
async def web_fetch(
    url: str,
    prompt: str,
    max_length: int = 50000
) -> dict:
    """Fetch URL und verarbeite mit Prompt."""
    
    # 1. HTML fetchen
    html = await fetch_url(url)
    
    # 2. Zu Markdown konvertieren
    markdown = html_to_markdown(html)
    
    # 3. Auf max_length kürzen
    if len(markdown) > max_length:
        markdown = markdown[:max_length] + "\n...[truncated]"
    
    # 4. Mit Prompt verarbeiten (via Side Query)
    result = await side_query.query(
        system=f"Verarbeite diesen Webinhalt gemäß Prompt: {prompt}",
        messages=[{"role": "user", "content": markdown}]
    )
    
    return {
        "url": url,
        "bytes": len(html),
        "result": result
    }
```

---

## 📊 IMPLEMENTIERUNGS-PRIORITÄT

### 🔴 Phase 1 - KRITISCH (Diese Woche)
1. **Dual-Model Architektur** - Größte Performance-Verbesserung
2. **Auto-Compact** - Verhindert Context-Overflow
3. **Strukturiertes System Prompt** - Verbessert Zuverlässigkeit

### 🟡 Phase 2 - HOCH (Nächste Woche)  
4. **Auto Memory Extraktion** - Lernt automatisch
5. **Away Summary** - Bessere UX
6. **Ripgrep Integration** - Schnellere Code-Suche

### 🟢 Phase 3 - MITTEL (Sprint 2)
7. **Coordinator Mode** - Multi-Agent für komplexe Tasks
8. **Scheduled Tasks** - Automation
9. **AutoDream** - Background Optimierung
10. **Web Fetch mit Prompt** - Intelligentes Browsen

---

## Dateien zum Erstellen

```
brain_core/
├── side_query.py          # Dual-Model Engine
├── auto_compact.py        # Context Compression
├── away_summary.py        # Away Summary Generator
├── cron_scheduler.py      # Scheduled Tasks
├── coordinator.py         # Multi-Agent Mode

brain_memory/
├── auto_dream.py          # Background Konsolidierung
├── auto_extract.py        # Automatische Extraktion
├── memory_types.py        # Memory Typen & Frontmatter

executive_arm/
├── grep_tool.py           # Ripgrep Integration
├── web_fetch.py           # Smart Web Fetching

data/
├── system_prompts/
│   ├── intro.md
│   ├── doing_tasks.md
│   ├── actions.md
│   ├── using_tools.md
│   └── output_style.md
```
