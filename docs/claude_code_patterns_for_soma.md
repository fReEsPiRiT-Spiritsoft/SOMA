# Claude Code → SOMA: Übertragbare Architektur-Patterns

Analyse des Claude Code Quellcodes für die Verbesserung von SOMA-AI.

---

## 1. TOOL-DEFINITION SYSTEM

### Claude Code Pattern: Strukturierte Tool-Definition
```typescript
// Claude Code Tool Interface (Tool.ts)
export type Tool = {
  name: string;
  aliases?: string[];           // Rückwärtskompatibilität
  searchHint?: string;          // Für Tool-Suche via Keywords
  
  // Schema-Definition mit Zod
  inputSchema: z.ZodObject<...>;
  outputSchema?: z.ZodType<unknown>;
  
  // Beschreibung - DYNAMISCH basierend auf Kontext!
  description(input, options): Promise<string>;
  
  // Die eigentliche Ausführung
  call(args, context, canUseTool, parentMessage, onProgress): Promise<ToolResult>;
  
  // Validierung BEVOR Tool läuft
  validateInput?(input, context): Promise<ValidationResult>;
  
  // Permission-System
  checkPermissions(input, context): Promise<PermissionResult>;
  
  // Concurrency Control
  isConcurrencySafe(input): boolean;
  isReadOnly(input): boolean;
  isDestructive?(input): boolean;
}
```

### Für SOMA übertragen:

**Aktuelle SOMA action_registry.json → Erweitern:**

```json
{
  "ha_call": {
    "description": "Home Assistant Service Call",
    "params": {...},
    
    // NEU: Von Claude übernehmen
    "validation_rules": {
      "entity_id": "must_exist_in_ha",
      "domain": "must_be_valid_domain"
    },
    "concurrency_safe": true,
    "is_read_only": false,
    "is_destructive": false,
    "timeout_ms": 5000,
    "retry_policy": {
      "max_retries": 3,
      "backoff_ms": 1000
    }
  }
}
```

---

## 2. TOOL SEARCH & DEFERRED LOADING

### Claude Code Pattern:
```typescript
// Tools werden NICHT alle sofort geladen
// ToolSearch ermöglicht das LLM, Tools zu finden
const shouldDefer?: boolean;    // Tool erst bei Bedarf laden
const alwaysLoad?: boolean;     // Tool IMMER anzeigen

// ToolSearchTool.ts - Das LLM kann nach Tools suchen
// Keyword-basierte Suche über tool.searchHint
```

### Für SOMA übertragen:

```python
# brain_core/tool_discovery.py (NEU)
class ToolDiscovery:
    """
    Ermöglicht dynamisches Tool-Loading.
    LLM kann fragen: "Welche Tools für Musik?"
    → Nur relevante Tools werden in Prompt injected.
    """
    
    def __init__(self, registry: ActionRegistry):
        self.registry = registry
        self._loaded_tools: set[str] = set()
        self._always_available = {"ha_call", "search", "remember"}
    
    def search_tools(self, query: str) -> list[dict]:
        """Keyword-Suche über Tool-Beschreibungen."""
        results = []
        for tag, info in self.registry.get_all_tags().items():
            if self._matches_query(query, info):
                results.append({"tag": tag, **info})
        return results
    
    def get_prompt_for_context(self, context: dict) -> str:
        """
        Generiere nur relevanten Teil des System-Prompts.
        Nicht ALLE Tools, nur was gerade sinnvoll ist.
        """
        relevant_tags = self._determine_relevant_tags(context)
        return self._build_prompt_section(relevant_tags)
```

---

## 3. PERMISSION & VALIDATION SYSTEM

### Claude Code Pattern:
```typescript
// Zweistufige Prüfung:
// 1. validateInput() - Technische Validierung
// 2. checkPermissions() - Berechtigungsprüfung

// Beispiel BashTool:
checkPermissions(input, context) {
  // Prüft ob Befehl erlaubt ist
  // Kann interaktiv User fragen
  // Tracking von Ablehnungen
}
```

### Für SOMA übertragen:

```python
# brain_core/safety/action_validator.py (NEU)
from typing import Tuple

class ActionValidator:
    """
    Validiert Action-Tags BEVOR sie ausgeführt werden.
    Kombiniert Claude's validateInput + checkPermissions Konzept.
    """
    
    def __init__(self, registry: ActionRegistry):
        self.registry = registry
        self._denied_actions: dict[str, int] = {}  # Tracking
    
    async def validate(self, tag_type: str, params: dict) -> Tuple[bool, str]:
        """
        Schritt 1: Technische Validierung.
        """
        info = self.registry.get_tag_info(tag_type)
        if not info:
            return False, f"Unbekannter Tag: {tag_type}"
        
        # Required params prüfen
        for param, spec in info.get("params", {}).items():
            if spec.get("required") and param not in params:
                return False, f"Parameter '{param}' fehlt"
        
        # Type-Validierung
        for param, value in params.items():
            expected_type = info.get("params", {}).get(param, {}).get("type")
            if expected_type and not self._check_type(value, expected_type):
                return False, f"Parameter '{param}' hat falschen Typ"
        
        return True, ""
    
    async def check_permission(
        self, 
        tag_type: str, 
        params: dict,
        user_context: dict
    ) -> Tuple[bool, str]:
        """
        Schritt 2: Berechtigungsprüfung (Kind vs Erwachsener etc.)
        """
        info = self.registry.get_tag_info(tag_type)
        
        # Destruktive Actions bei Kind-Modus blockieren
        if user_context.get("is_child") and info.get("is_destructive"):
            return False, "Diese Aktion ist im Kindermodus nicht erlaubt"
        
        # Denial-Tracking (wie Claude Code)
        if tag_type in self._denied_actions:
            if self._denied_actions[tag_type] > 3:
                # Nach 3 Ablehnungen: Automatisch ablehnen
                return False, "Diese Aktion wurde zu oft abgelehnt"
        
        return True, ""
```

---

## 4. CONCURRENCY & TOOL ORCHESTRATION

### Claude Code Pattern:
```typescript
// toolOrchestration.ts
// Tools werden intelligent parallel oder seriell ausgeführt:

function partitionToolCalls(toolUseMessages, context): Batch[] {
  // Concurrency-safe Tools → Parallel
  // Nicht-safe Tools → Seriell
}

async function* runToolsConcurrently(blocks, ...) {
  // Parallele Ausführung mit Limits
}

async function* runToolsSerially(blocks, ...) {
  // Serielle Ausführung mit Context-Updates
}
```

### Für SOMA übertragen:

```python
# brain_core/action_orchestrator.py (NEU)
import asyncio
from typing import AsyncGenerator, List

class ActionOrchestrator:
    """
    Intelligente Ausführung mehrerer Actions.
    Parallelisiert wo möglich, serialisiert wo nötig.
    """
    
    def __init__(self, registry: ActionRegistry, executor):
        self.registry = registry
        self.executor = executor
        self._max_concurrent = 5
    
    async def execute_actions(
        self, 
        actions: List[dict]
    ) -> AsyncGenerator[dict, None]:
        """
        Führt Actions optimal aus.
        """
        # Partitioniere nach Concurrency-Safety
        batches = self._partition_actions(actions)
        
        for batch in batches:
            if batch["concurrent"]:
                # Parallel ausführen
                async for result in self._run_concurrent(batch["actions"]):
                    yield result
            else:
                # Seriell ausführen
                async for result in self._run_serial(batch["actions"]):
                    yield result
    
    def _partition_actions(self, actions: List[dict]) -> List[dict]:
        """
        Gruppiert Actions nach Ausführbarkeit.
        """
        batches = []
        current_batch = {"concurrent": True, "actions": []}
        
        for action in actions:
            info = self.registry.get_tag_info(action["type"])
            is_safe = info.get("concurrency_safe", False)
            
            if is_safe and current_batch["concurrent"]:
                current_batch["actions"].append(action)
            else:
                if current_batch["actions"]:
                    batches.append(current_batch)
                current_batch = {"concurrent": is_safe, "actions": [action]}
        
        if current_batch["actions"]:
            batches.append(current_batch)
        
        return batches
    
    async def _run_concurrent(self, actions: List[dict]):
        """Parallel mit Semaphore für Limit."""
        sem = asyncio.Semaphore(self._max_concurrent)
        
        async def with_sem(action):
            async with sem:
                return await self.executor(action)
        
        tasks = [with_sem(a) for a in actions]
        for coro in asyncio.as_completed(tasks):
            result = await coro
            yield result
```

---

## 5. STREAMING TOOL EXECUTOR

### Claude Code Pattern:
```typescript
// StreamingToolExecutor.ts
class StreamingToolExecutor {
  // Tools starten sobald sie im Stream erscheinen
  // Nicht warten bis Response komplett ist!
  
  addTool(block, assistantMessage): void {
    // Tool sofort zur Queue hinzufügen
    void this.processQueue()
  }
  
  private async processQueue() {
    // Starte Tools wenn Concurrency erlaubt
  }
}
```

### Für SOMA übertragen:

SOMA's `action_stream_parser.py` macht das bereits gut! Aber erweiterbar:

```python
# brain_core/action_stream_parser.py - ERWEITERUNG
class ActionStreamParser:
    """
    Bereits gut: Feuert Actions sofort wenn Tag vollständig.
    
    NEU: Concurrency-Aware Queue
    """
    
    def __init__(self, action_executor, orchestrator: ActionOrchestrator):
        self._executor = action_executor
        self._orchestrator = orchestrator
        self._pending_actions: list[dict] = []
        self._running_actions: set[str] = set()
    
    async def _fire_tag(self, tag: str) -> None:
        """Erweitert: Nutzt Orchestrator für intelligente Ausführung."""
        action_type, params = self._parse_tag(tag)
        
        action = {
            "id": str(uuid.uuid4()),
            "type": action_type,
            "params": params,
            "tag": tag
        }
        
        # Prüfe ob wir sofort starten können
        info = self._registry.get_tag_info(action_type)
        
        if info.get("concurrency_safe") or not self._running_actions:
            # Sofort starten
            await self._start_action(action)
        else:
            # In Queue, warte auf laufende Actions
            self._pending_actions.append(action)
```

---

## 6. TOOL RESULT HANDLING

### Claude Code Pattern:
```typescript
// Tools können:
// - Normale Ergebnisse zurückgeben
// - Große Ergebnisse auf Disk speichern (maxResultSizeChars)
// - Progress während Ausführung melden
// - Context für nächste Tools modifizieren

type ToolResult<T> = {
  data: T;
  newMessages?: Message[];
  contextModifier?: (context) => ToolUseContext;
}
```

### Für SOMA übertragen:

```python
# brain_core/action_result.py (NEU)
from dataclasses import dataclass
from typing import Callable, Optional, Any
from pathlib import Path

@dataclass
class ActionResult:
    """
    Strukturiertes Ergebnis einer Action.
    """
    success: bool
    data: Any
    error_message: Optional[str] = None
    
    # Für große Ergebnisse → Disk
    large_result_path: Optional[Path] = None
    preview: Optional[str] = None  # Kurze Vorschau
    
    # Für Re-Ask Tags (search, browse)
    reask_content: Optional[str] = None
    
    # Context-Modifikation für Folge-Actions
    context_updates: Optional[dict] = None
    
    # TTS-Nachricht (was SOMA sagen soll)
    tts_message: Optional[str] = None
    
    @classmethod
    def from_search(cls, results: list[dict]) -> "ActionResult":
        """Factory für Search-Ergebnisse."""
        preview = "\n".join(r["title"] for r in results[:3])
        full_content = json.dumps(results, ensure_ascii=False)
        
        return cls(
            success=True,
            data=results,
            preview=preview,
            reask_content=full_content,
            tts_message="Ich habe einige Ergebnisse gefunden."
        )
```

---

## 7. PROMPT ENGINEERING PATTERNS

### Claude Code Pattern:

Die Tool-Beschreibungen sind SEHR detailliert:

```typescript
// BashTool prompt.ts
- Klare DO / DON'T Listen
- Konkrete Beispiele
- Sicherheitsregeln
- Kontextabhängige Anpassungen

// FileEditTool prompt.ts
- Präzise Format-Anforderungen
- Fehlervermeidungs-Hinweise
```

### Für SOMA übertragen:

```python
# brain_core/action_registry.py - generate_prompt_section() ERWEITERN

def generate_prompt_section(self) -> str:
    """
    Generiert Tool-Prompt nach Claude Code Patterns.
    """
    sections = []
    
    for cat_name, cat_data in self._registry["categories"].items():
        section = f"\n## {cat_data['icon']} {cat_data['label']}\n"
        
        for tag, info in cat_data.get("tags", {}).items():
            section += f"\n### {tag}\n"
            section += f"**Beschreibung:** {info['description']}\n"
            
            # Parameter mit Hinweisen
            if info.get("params"):
                section += "\n**Parameter:**\n"
                for param, spec in info["params"].items():
                    req = "✓ Pflicht" if spec.get("required") else "○ Optional"
                    hint = f" — {spec.get('hint', '')}" if spec.get("hint") else ""
                    section += f"- `{param}` ({req}): {spec.get('type', 'string')}{hint}\n"
            
            # Beispiele (WICHTIG für zuverlässige Nutzung!)
            if info.get("examples"):
                section += "\n**Beispiele:**\n```\n"
                section += "\n".join(info["examples"])
                section += "\n```\n"
            
            # NEU: DO / DON'T (von Claude übernommen)
            if info.get("do_rules"):
                section += "\n**✓ DO:**\n"
                for rule in info["do_rules"]:
                    section += f"- {rule}\n"
            
            if info.get("dont_rules"):
                section += "\n**✗ DON'T:**\n"
                for rule in info["dont_rules"]:
                    section += f"- {rule}\n"
        
        sections.append(section)
    
    return "\n".join(sections)
```

---

## 8. ERROR HANDLING & RECOVERY

### Claude Code Pattern:
```typescript
// Mehrere Ebenen:
// 1. ValidationResult - Vor Ausführung
// 2. Tool-interne Fehler mit is_error Flag
// 3. Retry-Logik in toolExecution.ts
// 4. Fallback auf andere Tools/Engines
```

### Für SOMA übertragen:

```python
# brain_core/action_executor.py - ERWEITERT

class ActionExecutor:
    async def execute_with_recovery(
        self, 
        tag_type: str, 
        params: dict
    ) -> ActionResult:
        """
        Ausführung mit Retry und Fallback.
        """
        info = self._registry.get_tag_info(tag_type)
        retry_policy = info.get("retry_policy", {"max_retries": 3})
        
        last_error = None
        for attempt in range(retry_policy.get("max_retries", 3)):
            try:
                result = await self._execute_single(tag_type, params)
                if result.success:
                    return result
                    
                # Retry bei bestimmten Fehlern
                if self._is_retryable(result.error_message):
                    await asyncio.sleep(
                        retry_policy.get("backoff_ms", 1000) / 1000 * (attempt + 1)
                    )
                    continue
                    
                # Nicht retryable → Fallback versuchen
                return await self._try_fallback(tag_type, params, result)
                
            except Exception as e:
                last_error = e
                logger.warning(
                    "action_attempt_failed",
                    tag=tag_type,
                    attempt=attempt,
                    error=str(e)
                )
        
        # Alle Retries fehlgeschlagen
        return ActionResult(
            success=False,
            data=None,
            error_message=f"Action failed after {retry_policy['max_retries']} attempts: {last_error}",
            tts_message="Das hat leider nicht geklappt. Versuchen wir es anders?"
        )
```

---

## ZUSAMMENFASSUNG: Sofort Umsetzbare Verbesserungen

### Priorität 1 (Schnell umsetzbar):
1. **Tool-Validation erweitern** → `brain_core/safety/action_validator.py`
2. **DO/DON'T Regeln in action_registry.json** → Bessere Beispiele
3. **ActionResult-Klasse** → Strukturierte Rückgaben

### Priorität 2 (Mittelfristig):
4. **ActionOrchestrator** → Parallele Tool-Ausführung
5. **ToolDiscovery** → Dynamisches Tool-Loading
6. **Retry & Recovery** → Robustere Ausführung

### Priorität 3 (Längerfristig):
7. **Permission-System** mit User-Prompts
8. **Progress-Reporting** während langer Actions
9. **Context-Modifikation** zwischen Actions

---

## Code-Referenzen aus Claude Code

| Feature | Claude Code Datei | Für SOMA |
|---------|------------------|----------|
| Tool Definition | `src/Tool.ts` | `action_registry.json` erweitern |
| Tool Orchestration | `src/services/tools/toolOrchestration.ts` | Neuer `ActionOrchestrator` |
| Streaming Executor | `src/services/tools/StreamingToolExecutor.ts` | `action_stream_parser.py` erweitern |
| Bash Tool Prompt | `src/tools/BashTool/prompt.ts` | System-Prompt verbessern |
| Permissions | `src/tools/*/bashPermissions.ts` | `safety/action_validator.py` |
| Query Engine | `src/QueryEngine.ts` | `logic_router.py` erweitern |
