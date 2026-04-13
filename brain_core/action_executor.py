"""
SOMA-AI Action Executor
========================
Zentrale Ausführungskomponente für alle Action-Tags.
Inspiriert von Claude Code's Tool Execution Pattern.

Vereint:
  - ActionValidator → Validierung vor Ausführung
  - ActionOrchestrator → Intelligente Parallelisierung  
  - ActionResult → Strukturierte Ergebnisse
  - Retry/Recovery → Fehlerbehandlung

Datenfluss:
  [ACTION:tag params] 
       │
       ▼
  ActionExecutor.execute()
       │
       ├─ ActionValidator.validate()
       │       └─ Schema + Type + Domain-Checks
       │
       ├─ ActionValidator.check_permission()
       │       └─ Kind-Modus, Destructive, Rate-Limit
       │
       ├─ _dispatch_to_handler()
       │       └─ Ruft den tatsächlichen Handler auf
       │
       ├─ Retry-Logic (wenn is_retryable)
       │
       └─ ActionResult
               └─ success/error, tts_message, reask_content
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import (
    Callable,
    Awaitable,
    Optional,
    Dict,
    Any,
    List,
    TYPE_CHECKING,
)
from dataclasses import dataclass, field

import structlog

from brain_core.action_registry import get_tag_info, get_reask_tags
from brain_core.action_result import ActionResult
from brain_core.safety.action_validator import (
    ActionValidator,
    ValidationResult,
    PermissionResult,
    get_validator,
)

if TYPE_CHECKING:
    from brain_core.action_orchestrator import ActionOrchestrator

logger = structlog.get_logger("soma.action_executor")

# Type für Action Handler Funktionen
ActionHandler = Callable[[str, Dict[str, Any]], Awaitable[ActionResult]]


@dataclass
class ExecutionContext:
    """Kontext für eine Action-Ausführung."""
    user_id: Optional[str] = None
    room_id: Optional[str] = None
    session_id: Optional[str] = None
    is_child: bool = False
    cwd: str = field(default_factory=lambda: str(Path.home()))
    ha_entities: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_user_context(self) -> dict:
        """Konvertiert zu user_context für Validator."""
        return {
            "user_id": self.user_id,
            "room_id": self.room_id,
            "is_child": self.is_child,
        }


@dataclass
class ExecutionStats:
    """Statistiken über Action-Ausführungen."""
    total_executed: int = 0
    successful: int = 0
    failed: int = 0
    retried: int = 0
    avg_execution_ms: float = 0.0
    by_action: Dict[str, int] = field(default_factory=dict)
    _execution_times: List[float] = field(default_factory=list)

    def record(self, action_type: str, success: bool, execution_ms: float) -> None:
        """Aufzeichnung einer Ausführung."""
        self.total_executed += 1
        if success:
            self.successful += 1
        else:
            self.failed += 1
        
        self.by_action[action_type] = self.by_action.get(action_type, 0) + 1
        
        self._execution_times.append(execution_ms)
        # Rolling average über letzte 100
        if len(self._execution_times) > 100:
            self._execution_times = self._execution_times[-100:]
        self.avg_execution_ms = sum(self._execution_times) / len(self._execution_times)


class ActionExecutor:
    """
    Zentrale Action-Ausführung mit Validation, Retry und Strukturierten Results.
    
    Nutzung:
        executor = ActionExecutor()
        
        # Handler registrieren
        executor.register_handler("ha_call", ha_call_handler)
        executor.register_handler("search", search_handler)
        
        # Action ausführen
        result = await executor.execute("ha_call", {
            "domain": "light",
            "service": "turn_on",
            "entity_id": "light.wohnzimmer"
        })
        
        if result.success:
            await tts.speak(result.tts_message)
        if result.reask_content:
            # Ans LLM für Zusammenfassung
            ...
    """

    def __init__(self, context: Optional[ExecutionContext] = None):
        self._handlers: Dict[str, ActionHandler] = {}
        self._validator = get_validator()
        self._context = context or ExecutionContext()
        self._stats = ExecutionStats()
        self._result_storage = Path("/tmp/soma_results")
        self._max_result_chars = 10000
        
        # Broadcast für Dashboard
        self._broadcast_fn: Optional[Callable] = None
        
        # Register built-in handlers
        self._register_builtin_handlers()

    @property
    def stats(self) -> ExecutionStats:
        """Ausführungsstatistiken."""
        return self._stats

    def set_context(self, context: ExecutionContext) -> None:
        """Setzt den Ausführungskontext."""
        self._context = context

    def set_broadcast(self, fn: Callable) -> None:
        """Setzt die Broadcast-Funktion für Dashboard."""
        self._broadcast_fn = fn

    async def _broadcast(self, level: str, message: str, tag: str = "EXECUTOR") -> None:
        """Broadcast ans Dashboard."""
        if self._broadcast_fn:
            try:
                await self._broadcast_fn(level, message, tag)
            except Exception:
                pass

    # ══════════════════════════════════════════════════════════════════════
    # Handler Registration
    # ══════════════════════════════════════════════════════════════════════

    def register_handler(self, action_type: str, handler: ActionHandler) -> None:
        """Registriert einen Handler für einen Action-Typ."""
        self._handlers[action_type.lower()] = handler
        logger.info("action_handler_registered", action=action_type)

    def _register_builtin_handlers(self) -> None:
        """Registriert Built-in Handler (Stubs die später ersetzt werden)."""
        # Diese werden von den tatsächlichen Modulen überschrieben
        pass

    # ══════════════════════════════════════════════════════════════════════
    # Main Execution
    # ══════════════════════════════════════════════════════════════════════

    async def execute(
        self,
        action_type: str,
        params: Dict[str, Any],
        skip_validation: bool = False,
        skip_permission: bool = False,
    ) -> ActionResult:
        """
        Führt eine einzelne Action aus.
        
        Flow:
          1. Validierung (optional)
          2. Permission-Check (optional)
          3. Handler-Dispatch mit Retry
          4. Result-Processing
        
        Args:
            action_type: Der Action-Tag (z.B. "ha_call", "search")
            params: Die Parameter der Action
            skip_validation: Überspringt Validierung (für interne Calls)
            skip_permission: Überspringt Permission-Check
        
        Returns:
            ActionResult mit success/error, tts_message, etc.
        """
        start_time = time.monotonic()
        action_type = action_type.lower()
        
        await self._broadcast("info", f"Action: {action_type}", "EXECUTOR")
        
        logger.info(
            "action_execute_start",
            action=action_type,
            params=self._safe_log_params(params),
        )

        # ── 1. Validierung ───────────────────────────────────────────────
        if not skip_validation:
            validation = await self._validator.validate(action_type, params)
            if not validation.valid:
                logger.warning(
                    "action_validation_failed",
                    action=action_type,
                    error=validation.error_message,
                )
                await self._broadcast("warning", f"Validierung fehlgeschlagen: {validation.error_message}", "EXECUTOR")
                return ActionResult.error_result(
                    validation.error_message or "Validierungsfehler",
                    code=validation.error_code or "VALIDATION_ERROR",
                    tts_message="Das kann ich so nicht ausführen."
                )

        # ── 2. Permission-Check ──────────────────────────────────────────
        if not skip_permission:
            permission = await self._validator.check_permission(
                action_type,
                params,
                self._context.to_user_context()
            )
            if not permission.allowed:
                logger.warning(
                    "action_permission_denied",
                    action=action_type,
                    reason=permission.reason,
                )
                self._validator.record_denial(action_type)
                await self._broadcast("warning", f"Permission verweigert: {permission.reason}", "EXECUTOR")
                return ActionResult.error_result(
                    permission.reason or "Nicht erlaubt",
                    code="PERMISSION_DENIED",
                    tts_message="Das ist gerade nicht erlaubt."
                )
            
            # TODO: Handle requires_confirmation
            if permission.requires_confirmation:
                # In Zukunft: User-Prompt
                logger.info("action_requires_confirmation", action=action_type)

        # ── 3. Handler-Dispatch mit Retry ────────────────────────────────
        result = await self._execute_with_retry(action_type, params)
        
        # ── 4. Result-Processing ─────────────────────────────────────────
        execution_ms = (time.monotonic() - start_time) * 1000
        result = result.with_execution_time(execution_ms)
        result.action_type = action_type
        
        # Large Result → Disk
        if result.data and len(str(result.data)) > self._max_result_chars:
            result = self._store_large_result(result)
        
        # Stats
        self._stats.record(action_type, result.success, execution_ms)
        
        logger.info(
            "action_execute_complete",
            action=action_type,
            success=result.success,
            duration_ms=round(execution_ms, 2),
        )
        
        await self._broadcast(
            "info" if result.success else "warning",
            f"Action {action_type}: {'OK' if result.success else result.error_message}",
            "EXECUTOR"
        )
        
        return result

    async def _execute_with_retry(
        self,
        action_type: str,
        params: Dict[str, Any],
    ) -> ActionResult:
        """Führt Action mit Retry-Logic aus."""
        info = get_tag_info(action_type)
        retry_policy = info.get("retry_policy", {}) if info else {}
        max_retries = retry_policy.get("max_retries", 1)
        backoff_ms = retry_policy.get("backoff_ms", 500)
        timeout_ms = info.get("timeout_ms", 30000) if info else 30000
        
        last_error: Optional[str] = None
        
        for attempt in range(max_retries):
            try:
                result = await asyncio.wait_for(
                    self._dispatch_to_handler(action_type, params),
                    timeout=timeout_ms / 1000
                )
                
                if result.success:
                    return result
                
                # Retry wenn retryable
                if result.is_retryable and attempt < max_retries - 1:
                    self._stats.retried += 1
                    logger.info(
                        "action_retry",
                        action=action_type,
                        attempt=attempt + 1,
                        max=max_retries,
                    )
                    await asyncio.sleep(backoff_ms / 1000 * (attempt + 1))
                    continue
                
                return result
                
            except asyncio.TimeoutError:
                last_error = f"Timeout nach {timeout_ms}ms"
                logger.warning("action_timeout", action=action_type, timeout_ms=timeout_ms)
                
                if attempt < max_retries - 1:
                    self._stats.retried += 1
                    await asyncio.sleep(backoff_ms / 1000 * (attempt + 1))
                    continue
                    
            except Exception as exc:
                last_error = str(exc)
                logger.error("action_exception", action=action_type, error=str(exc))
                
                if attempt < max_retries - 1:
                    self._stats.retried += 1
                    await asyncio.sleep(backoff_ms / 1000 * (attempt + 1))
                    continue
        
        return ActionResult.error_result(
            last_error or "Unbekannter Fehler",
            code="EXECUTION_ERROR",
            retryable=False,
            tts_message="Da ist etwas schiefgelaufen."
        )

    async def _dispatch_to_handler(
        self,
        action_type: str,
        params: Dict[str, Any],
    ) -> ActionResult:
        """Dispatcht zum registrierten Handler."""
        handler = self._handlers.get(action_type)
        
        if not handler:
            # Fallback: Versuche dynamischen Import
            handler = await self._load_handler(action_type)
        
        if not handler:
            return ActionResult.error_result(
                f"Kein Handler für Action '{action_type}' registriert",
                code="NO_HANDLER",
                tts_message="Diese Aktion kenne ich noch nicht."
            )
        
        return await handler(action_type, params)

    async def _load_handler(self, action_type: str) -> Optional[ActionHandler]:
        """Versucht einen Handler dynamisch zu laden."""
        # Mapping von Action-Types zu Modulen
        handler_modules = {
            "ha_call": "brain_core.discovery.ha_bridge",
            "ha_tts": "brain_core.discovery.ha_bridge",
            "search": "brain_core.web_search",
            "browse": "executive_arm.browser",
            "fetch_url": "brain_core.web_search",
            "shell": "executive_arm.terminal",
            "file": "executive_arm.file_operations",
            "file_edit": "executive_arm.file_edit_handlers",
            "file_insert": "executive_arm.file_edit_handlers",
            "file_delete_lines": "executive_arm.file_edit_handlers",
            "app": "executive_arm.app_control",
            "window": "executive_arm.desktop_control",
            "bluetooth": "executive_arm.bluetooth",
            "volume": "executive_arm.system_control",
            "brightness": "executive_arm.system_control",
            "reminder": "brain_core.memory.reminder",
            "remember": "brain_core.memory.long_term",
        }
        
        module_name = handler_modules.get(action_type)
        if not module_name:
            return None
        
        try:
            import importlib
            module = importlib.import_module(module_name)
            
            # Suche nach execute_{action_type} oder handle_{action_type}
            handler_name = f"execute_{action_type}"
            if hasattr(module, handler_name):
                handler = getattr(module, handler_name)
                self._handlers[action_type] = handler
                logger.info("action_handler_loaded", action=action_type, module=module_name)
                return handler
            
            handler_name = f"handle_{action_type}"
            if hasattr(module, handler_name):
                handler = getattr(module, handler_name)
                self._handlers[action_type] = handler
                return handler
                
        except ImportError as e:
            logger.debug("action_handler_import_failed", action=action_type, error=str(e))
        except Exception as e:
            logger.warning("action_handler_load_error", action=action_type, error=str(e))
        
        return None

    # ══════════════════════════════════════════════════════════════════════
    # Batch Execution (Orchestrator Integration)
    # ══════════════════════════════════════════════════════════════════════

    async def execute_batch(
        self,
        actions: List[Dict[str, Any]],
        parallel: bool = True,
    ) -> List[ActionResult]:
        """
        Führt mehrere Actions aus.
        
        Nutzt ActionOrchestrator für intelligente Parallelisierung.
        
        Args:
            actions: Liste von {"type": "...", "params": {...}}
            parallel: Ob parallel ausgeführt werden soll
        
        Returns:
            Liste von ActionResults
        """
        if not actions:
            return []
        
        if not parallel or len(actions) == 1:
            # Seriell
            results = []
            for action in actions:
                result = await self.execute(
                    action.get("type", "unknown"),
                    action.get("params", {})
                )
                results.append(result)
            return results
        
        # Parallel via Orchestrator
        from brain_core.action_orchestrator import ActionOrchestrator
        
        orchestrator = ActionOrchestrator(
            executor=self._orchestrator_execute,
            max_concurrent=5,
            user_context=self._context.to_user_context()
        )
        
        results = []
        async for orch_result in orchestrator.execute(actions):
            if orch_result.result:
                results.append(orch_result.result)
            elif orch_result.error:
                results.append(ActionResult.error_result(orch_result.error))
        
        return results

    async def _orchestrator_execute(
        self,
        action_type: str,
        params: Dict[str, Any],
    ) -> ActionResult:
        """Wrapper für Orchestrator-Aufrufe (skipped bereits validiert)."""
        return await self.execute(
            action_type, params,
            skip_validation=True,  # Orchestrator hat schon validiert
            skip_permission=True,
        )

    # ══════════════════════════════════════════════════════════════════════
    # Utility Methods
    # ══════════════════════════════════════════════════════════════════════

    def _store_large_result(self, result: ActionResult) -> ActionResult:
        """Speichert große Ergebnisse auf Disk."""
        try:
            self._result_storage.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            file_path = self._result_storage / f"{result.action_type}_{timestamp}.json"
            
            import json
            content = json.dumps(result.data, ensure_ascii=False, indent=2)
            file_path.write_text(content, encoding="utf-8")
            
            result.large_result_path = file_path
            result.preview = content[:500] + f"\n... ({len(content)} Zeichen)"
            
            logger.info("large_result_stored", path=str(file_path), size=len(content))
            
        except Exception as e:
            logger.warning("large_result_storage_failed", error=str(e))
        
        return result

    def _safe_log_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Entfernt sensitive Daten aus Params für Logging."""
        sensitive_keys = {"password", "secret", "token", "key", "content"}
        safe = {}
        for k, v in params.items():
            if k.lower() in sensitive_keys:
                safe[k] = "***"
            elif isinstance(v, str) and len(v) > 100:
                safe[k] = v[:100] + "..."
            else:
                safe[k] = v
        return safe

    def is_reask_action(self, action_type: str) -> bool:
        """Prüft ob Action ein Re-Ask benötigt."""
        return action_type.lower() in get_reask_tags()


# ══════════════════════════════════════════════════════════════════════════
# Singleton & Convenience
# ══════════════════════════════════════════════════════════════════════════

_executor: Optional[ActionExecutor] = None


def get_executor() -> ActionExecutor:
    """Gibt die Singleton-Instanz zurück."""
    global _executor
    if _executor is None:
        _executor = ActionExecutor()
    return _executor


def set_executor(executor: ActionExecutor) -> None:
    """Setzt die Singleton-Instanz (für Tests)."""
    global _executor
    _executor = executor


async def execute_action(
    action_type: str,
    params: Dict[str, Any],
    context: Optional[ExecutionContext] = None,
) -> ActionResult:
    """
    Convenience-Funktion zum Ausführen einer Action.
    
    Beispiel:
        result = await execute_action("ha_call", {
            "domain": "light",
            "service": "turn_on",
            "entity_id": "light.wohnzimmer"
        })
    """
    executor = get_executor()
    if context:
        executor.set_context(context)
    return await executor.execute(action_type, params)


async def execute_actions(
    actions: List[Dict[str, Any]],
    parallel: bool = True,
) -> List[ActionResult]:
    """
    Convenience-Funktion zum Ausführen mehrerer Actions.
    """
    executor = get_executor()
    return await executor.execute_batch(actions, parallel=parallel)
