"""
SOMA-AI Action Orchestrator
============================
Intelligente Ausführung mehrerer Actions.
Inspiriert von Claude Code's toolOrchestration.ts.

Features:
  - Parallelisiert concurrency-safe Actions
  - Serialisiert Zustandsändernde Actions
  - Respektiert Dependencies zwischen Actions
  - Error-Recovery mit Fallbacks
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import (
    AsyncGenerator, 
    Callable, 
    Awaitable, 
    Optional, 
    List, 
    Dict, 
    Any
)
from enum import Enum

import structlog

from brain_core.action_registry import get_tag_info
from brain_core.action_result import ActionResult
from brain_core.safety.action_validator import (
    get_validator,
    ValidationResult,
    PermissionResult
)

logger = structlog.get_logger("soma.action_orchestrator")


class ActionStatus(Enum):
    """Status einer Action in der Queue."""
    QUEUED = "queued"
    VALIDATING = "validating"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class TrackedAction:
    """Eine Action in der Orchestration-Queue."""
    id: str
    action_type: str
    params: dict
    raw_tag: str = ""
    status: ActionStatus = ActionStatus.QUEUED
    is_concurrency_safe: bool = False
    result: Optional[ActionResult] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None


@dataclass
class Batch:
    """Eine Gruppe von Actions die zusammen ausgeführt werden."""
    concurrent: bool
    actions: List[TrackedAction]


@dataclass
class OrchestrationResult:
    """Ergebnis des Orchestrators - wird gestreamt."""
    action_id: str
    action_type: str
    status: ActionStatus
    result: Optional[ActionResult] = None
    error: Optional[str] = None


ActionExecutor = Callable[[str, dict], Awaitable[ActionResult]]


class ActionOrchestrator:
    """
    Führt Actions intelligent aus.
    
    Nutzung:
        orchestrator = ActionOrchestrator(executor=my_executor)
        
        actions = [
            {"id": "1", "type": "search", "params": {"query": "wetter"}},
            {"id": "2", "type": "search", "params": {"query": "news"}},
            {"id": "3", "type": "ha_call", "params": {"domain": "light", ...}},
        ]
        
        async for result in orchestrator.execute(actions):
            print(f"{result.action_id}: {result.status}")
    """
    
    def __init__(
        self,
        executor: ActionExecutor,
        max_concurrent: int = 5,
        user_context: Optional[dict] = None
    ):
        self._executor = executor
        self._max_concurrent = max_concurrent
        self._user_context = user_context or {}
        self._validator = get_validator()
        self._running: Dict[str, TrackedAction] = {}
        self._completed: Dict[str, TrackedAction] = {}

    # ══════════════════════════════════════════════════════════════════════
    # Main Execution
    # ══════════════════════════════════════════════════════════════════════

    async def execute(
        self, 
        actions: List[dict],
        stop_on_error: bool = False
    ) -> AsyncGenerator[OrchestrationResult, None]:
        """
        Führt Actions optimal aus.
        Yieldet Ergebnisse sobald sie fertig sind.
        """
        if not actions:
            return
        
        # Actions tracken
        tracked = [self._track_action(a) for a in actions]
        
        # In Batches partitionieren
        batches = self._partition_actions(tracked)
        
        logger.info(
            "orchestration_started",
            total_actions=len(tracked),
            batches=len(batches),
            concurrent_batches=sum(1 for b in batches if b.concurrent)
        )
        
        # Batches ausführen
        for batch in batches:
            if batch.concurrent:
                async for result in self._execute_concurrent(batch.actions):
                    yield result
                    if stop_on_error and result.status == ActionStatus.FAILED:
                        return
            else:
                async for result in self._execute_serial(batch.actions):
                    yield result
                    if stop_on_error and result.status == ActionStatus.FAILED:
                        return

    def _track_action(self, action: dict) -> TrackedAction:
        """Erstellt ein TrackedAction Objekt."""
        action_type = action.get("type", "unknown")
        info = get_tag_info(action_type)
        
        return TrackedAction(
            id=action.get("id", str(id(action))),
            action_type=action_type,
            params=action.get("params", {}),
            raw_tag=action.get("raw_tag", ""),
            is_concurrency_safe=info.get("concurrency_safe", False) if info else False
        )

    # ══════════════════════════════════════════════════════════════════════
    # Partitioning Logic
    # ══════════════════════════════════════════════════════════════════════

    def _partition_actions(self, actions: List[TrackedAction]) -> List[Batch]:
        """
        Partitioniert Actions in Batches.
        
        Logik (wie Claude Code):
          - Konkurrente consecutive Actions → Ein paralleler Batch
          - Nicht-konkurrente Action → Eigener serieller Batch
        """
        if not actions:
            return []
        
        batches: List[Batch] = []
        current_batch = Batch(concurrent=actions[0].is_concurrency_safe, actions=[])
        
        for action in actions:
            if action.is_concurrency_safe and current_batch.concurrent:
                # Zur aktuellen parallelen Batch hinzufügen
                current_batch.actions.append(action)
            elif action.is_concurrency_safe and not current_batch.concurrent:
                # Neuen parallelen Batch starten
                if current_batch.actions:
                    batches.append(current_batch)
                current_batch = Batch(concurrent=True, actions=[action])
            else:
                # Nicht-konkurrente Action
                if current_batch.actions:
                    batches.append(current_batch)
                # Eigener serieller Batch
                batches.append(Batch(concurrent=False, actions=[action]))
                current_batch = Batch(concurrent=True, actions=[])  # Reset für nächste
        
        if current_batch.actions:
            batches.append(current_batch)
        
        return batches

    # ══════════════════════════════════════════════════════════════════════
    # Concurrent Execution
    # ══════════════════════════════════════════════════════════════════════

    async def _execute_concurrent(
        self, 
        actions: List[TrackedAction]
    ) -> AsyncGenerator[OrchestrationResult, None]:
        """Führt Actions parallel mit Semaphore aus."""
        sem = asyncio.Semaphore(self._max_concurrent)
        results_queue: asyncio.Queue[OrchestrationResult] = asyncio.Queue()
        
        async def execute_with_sem(action: TrackedAction):
            async with sem:
                result = await self._execute_single(action)
                await results_queue.put(result)
        
        # Alle Tasks starten
        tasks = [asyncio.create_task(execute_with_sem(a)) for a in actions]
        
        # Ergebnisse yielden sobald verfügbar
        for _ in range(len(actions)):
            result = await results_queue.get()
            yield result
        
        # Auf alle Tasks warten (sollten schon fertig sein)
        await asyncio.gather(*tasks, return_exceptions=True)

    # ══════════════════════════════════════════════════════════════════════
    # Serial Execution
    # ══════════════════════════════════════════════════════════════════════

    async def _execute_serial(
        self, 
        actions: List[TrackedAction]
    ) -> AsyncGenerator[OrchestrationResult, None]:
        """Führt Actions nacheinander aus."""
        for action in actions:
            result = await self._execute_single(action)
            yield result

    # ══════════════════════════════════════════════════════════════════════
    # Single Action Execution
    # ══════════════════════════════════════════════════════════════════════

    async def _execute_single(self, action: TrackedAction) -> OrchestrationResult:
        """
        Führt eine einzelne Action aus.
        
        Flow:
          1. Validierung
          2. Permission-Check
          3. Ausführung
          4. Result verarbeiten
        """
        action.started_at = time.time()
        action.status = ActionStatus.VALIDATING
        
        try:
            # 1. Validierung
            validation = await self._validator.validate(
                action.action_type, 
                action.params
            )
            
            if not validation.valid:
                action.status = ActionStatus.FAILED
                action.error = validation.error_message
                return OrchestrationResult(
                    action_id=action.id,
                    action_type=action.action_type,
                    status=ActionStatus.FAILED,
                    error=validation.error_message
                )
            
            # 2. Permission-Check
            permission = await self._validator.check_permission(
                action.action_type,
                action.params,
                self._user_context
            )
            
            if not permission.allowed:
                action.status = ActionStatus.SKIPPED
                action.error = permission.reason
                self._validator.record_denial(action.action_type)
                return OrchestrationResult(
                    action_id=action.id,
                    action_type=action.action_type,
                    status=ActionStatus.SKIPPED,
                    error=permission.reason
                )
            
            # TODO: Handle requires_confirmation
            
            # 3. Ausführung
            action.status = ActionStatus.EXECUTING
            self._running[action.id] = action
            
            result = await self._executor(action.action_type, action.params)
            
            # 4. Result verarbeiten
            action.status = ActionStatus.COMPLETED
            action.result = result
            action.completed_at = time.time()
            
            del self._running[action.id]
            self._completed[action.id] = action
            
            logger.info(
                "action_completed",
                action_id=action.id,
                type=action.action_type,
                success=result.success,
                duration_ms=(action.completed_at - action.started_at) * 1000
            )
            
            return OrchestrationResult(
                action_id=action.id,
                action_type=action.action_type,
                status=ActionStatus.COMPLETED,
                result=result
            )
            
        except asyncio.CancelledError:
            action.status = ActionStatus.SKIPPED
            action.error = "Cancelled"
            raise
            
        except Exception as exc:
            action.status = ActionStatus.FAILED
            action.error = str(exc)
            action.completed_at = time.time()
            
            logger.error(
                "action_failed",
                action_id=action.id,
                type=action.action_type,
                error=str(exc)
            )
            
            return OrchestrationResult(
                action_id=action.id,
                action_type=action.action_type,
                status=ActionStatus.FAILED,
                error=str(exc)
            )

    # ══════════════════════════════════════════════════════════════════════
    # Utility Methods
    # ══════════════════════════════════════════════════════════════════════

    def get_running_actions(self) -> List[TrackedAction]:
        """Gibt aktuell laufende Actions zurück."""
        return list(self._running.values())

    def get_completed_actions(self) -> List[TrackedAction]:
        """Gibt abgeschlossene Actions zurück."""
        return list(self._completed.values())

    async def cancel_running(self) -> None:
        """Bricht alle laufenden Actions ab."""
        # In einer echten Implementierung würden wir hier
        # die Tasks canceln. Für jetzt setzen wir nur den Status.
        for action in self._running.values():
            action.status = ActionStatus.SKIPPED
            action.error = "Cancelled by user"
        self._running.clear()


# ══════════════════════════════════════════════════════════════════════════
# Convenience Function
# ══════════════════════════════════════════════════════════════════════════

async def execute_actions(
    actions: List[dict],
    executor: ActionExecutor,
    user_context: Optional[dict] = None,
    max_concurrent: int = 5
) -> List[ActionResult]:
    """
    Convenience-Funktion: Führt Actions aus und sammelt alle Ergebnisse.
    
    Args:
        actions: Liste von {"type": "...", "params": {...}}
        executor: async Funktion(action_type, params) -> ActionResult
        user_context: Optional User-Kontext (is_child, etc.)
        max_concurrent: Max parallele Ausführungen
    
    Returns:
        Liste aller ActionResults
    """
    orchestrator = ActionOrchestrator(
        executor=executor,
        max_concurrent=max_concurrent,
        user_context=user_context
    )
    
    results = []
    async for orch_result in orchestrator.execute(actions):
        if orch_result.result:
            results.append(orch_result.result)
        elif orch_result.error:
            results.append(ActionResult.error_result(orch_result.error))
    
    return results
