"""
Coordinator Mode — Multi-Agent Orchestrierung.
================================================
Inspiriert von Claude Code's coordinatorMode.ts:
Ein Coordinator-Agent delegiert Aufgaben an Worker-Agents
die parallel arbeiten können.

SOMA-spezifisch:
  - Workers sind leichtgewichtige SideQuery-Aufrufe
  - Für komplexe Tasks die mehrere Schritte brauchen
  - Coordinator entscheidet ob Worker nötig sind
  - Results werden zu einer kohärenten Antwort zusammengeführt
  - Scratchpad für Inter-Worker Kommunikation

Anwendungsfälle:
  - "Recherchiere X und mach gleichzeitig Y"
  - Komplexe SmartHome-Szenarien (mehrere Geräte parallel)
  - Parallel: Web-Suche + Datei-Analyse + Memory-Check
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any

import structlog

logger = structlog.get_logger("soma.coordinator")


class WorkerStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"


@dataclass
class WorkerTask:
    """Ein Worker-Task im Coordinator."""
    worker_id: str = field(default_factory=lambda: f"w-{uuid.uuid4().hex[:8]}")
    description: str = ""
    prompt: str = ""
    status: WorkerStatus = WorkerStatus.PENDING
    result: str = ""
    error: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    duration_ms: float = 0.0

    @property
    def is_terminal(self) -> bool:
        return self.status in (WorkerStatus.COMPLETED, WorkerStatus.FAILED, WorkerStatus.KILLED)


@dataclass
class CoordinatorResult:
    """Gesamtergebnis einer Coordinator-Session."""
    workers: list[WorkerTask] = field(default_factory=list)
    synthesis: str = ""
    total_duration_ms: float = 0.0
    success: bool = True


class CoordinatorMode:
    """
    Multi-Agent Orchestrierung für komplexe SOMA-Tasks.
    """

    def __init__(self, max_workers: int = 4):
        self._max_workers = max_workers
        self._active_workers: dict[str, WorkerTask] = {}
        self._scratchpad: dict[str, Any] = {}
        self._history: list[CoordinatorResult] = []

    # ── Worker Management ────────────────────────────────────────────────

    async def spawn_worker(
        self,
        description: str,
        prompt: str,
        side_query_engine=None,
    ) -> WorkerTask:
        """
        Starte einen neuen Worker-Task.
        Worker nutzt SideQuery (Light-Modell) für schnelle Ausführung.
        """
        if len(self._active_workers) >= self._max_workers:
            logger.warning("coordinator_max_workers", count=len(self._active_workers))
            # Ältesten fertigen Worker entfernen
            for wid, w in list(self._active_workers.items()):
                if w.is_terminal:
                    del self._active_workers[wid]
                    break

        worker = WorkerTask(
            description=description,
            prompt=prompt,
        )
        self._active_workers[worker.worker_id] = worker

        logger.info(
            "worker_spawned",
            id=worker.worker_id,
            description=description[:60],
        )

        # Worker async starten (non-blocking)
        if side_query_engine:
            asyncio.create_task(
                self._run_worker(worker, side_query_engine)
            )

        return worker

    async def _run_worker(
        self,
        worker: WorkerTask,
        side_query_engine,
    ) -> None:
        """Führe einen Worker-Task aus."""
        worker.status = WorkerStatus.RUNNING
        worker.start_time = time.time()

        try:
            result = await side_query_engine.query(
                system=(
                    "Du bist ein Worker-Agent im SOMA-System. "
                    "Führe die folgende Aufgabe aus und berichte das Ergebnis. "
                    "Sei präzise und knapp."
                ),
                user_message=worker.prompt,
                max_tokens=1024,
                temperature=0.4,
            )

            worker.end_time = time.time()
            worker.duration_ms = (worker.end_time - worker.start_time) * 1000

            if result.success:
                worker.result = result.text
                worker.status = WorkerStatus.COMPLETED
            else:
                worker.error = result.error
                worker.status = WorkerStatus.FAILED

            logger.info(
                "worker_done",
                id=worker.worker_id,
                status=worker.status.value,
                ms=round(worker.duration_ms),
            )

        except Exception as exc:
            worker.status = WorkerStatus.FAILED
            worker.error = str(exc)
            worker.end_time = time.time()
            worker.duration_ms = (worker.end_time - worker.start_time) * 1000
            logger.error("worker_error", id=worker.worker_id, error=str(exc))

    async def send_to_worker(self, worker_id: str, message: str) -> bool:
        """Sende Follow-up Message zu einem Worker (für mehrstufige Tasks)."""
        worker = self._active_workers.get(worker_id)
        if not worker or worker.is_terminal:
            return False

        # Extend prompt und re-run
        worker.prompt += f"\n\nFollow-up: {message}"
        worker.status = WorkerStatus.PENDING
        return True

    def stop_worker(self, worker_id: str) -> bool:
        """Stoppe einen laufenden Worker."""
        worker = self._active_workers.get(worker_id)
        if not worker:
            return False
        worker.status = WorkerStatus.KILLED
        worker.end_time = time.time()
        return True

    def get_worker(self, worker_id: str) -> Optional[WorkerTask]:
        return self._active_workers.get(worker_id)

    # ── Parallel Execution ───────────────────────────────────────────────

    async def execute_parallel(
        self,
        tasks: list[dict],
        side_query_engine=None,
        timeout: float = 30.0,
    ) -> CoordinatorResult:
        """
        Führe mehrere Tasks parallel aus und warte auf alle.

        Args:
            tasks: Liste von {"description": str, "prompt": str}
            side_query_engine: SideQueryEngine
            timeout: Max Wartezeit in Sekunden

        Returns:
            CoordinatorResult mit allen Worker-Ergebnissen
        """
        start = time.time()

        # Workers spawnen
        workers = []
        for task in tasks[:self._max_workers]:
            worker = WorkerTask(
                description=task.get("description", ""),
                prompt=task.get("prompt", ""),
            )
            self._active_workers[worker.worker_id] = worker
            workers.append(worker)

        # Parallel ausführen
        if side_query_engine:
            coros = [self._run_worker(w, side_query_engine) for w in workers]
            await asyncio.wait_for(
                asyncio.gather(*coros, return_exceptions=True),
                timeout=timeout,
            )

        total_ms = (time.time() - start) * 1000

        result = CoordinatorResult(
            workers=workers,
            total_duration_ms=total_ms,
            success=all(w.status == WorkerStatus.COMPLETED for w in workers),
        )

        self._history.append(result)
        return result

    async def synthesize(
        self,
        coordinator_result: CoordinatorResult,
        side_query_engine=None,
        user_query: str = "",
    ) -> str:
        """
        Synthetisiere Worker-Ergebnisse zu einer kohärenten Antwort.
        """
        if not coordinator_result.workers:
            return ""

        parts = []
        for w in coordinator_result.workers:
            status = "✓" if w.status == WorkerStatus.COMPLETED else "✗"
            parts.append(f"[{status} {w.description}]\n{w.result or w.error}")

        worker_results = "\n\n".join(parts)

        if not side_query_engine:
            return worker_results

        result = await side_query_engine.query(
            system=(
                "Du synthethisierst mehrere Worker-Ergebnisse zu einer "
                "natürlichen, kohärenten Antwort. Sei knapp und informativ. "
                "Antworte als SOMA (Sprachassistent)."
            ),
            user_message=(
                f"User fragte: {user_query}\n\n"
                f"Worker-Ergebnisse:\n{worker_results}"
            ),
            max_tokens=512,
            temperature=0.5,
        )

        if result.success:
            coordinator_result.synthesis = result.text
            return result.text

        return worker_results

    # ── Scratchpad (Inter-Worker Kommunikation) ──────────────────────────

    def write_scratchpad(self, key: str, value: Any) -> None:
        """Schreibe Daten ins Scratchpad (für Worker-Kommunikation)."""
        self._scratchpad[key] = value

    def read_scratchpad(self, key: str, default=None) -> Any:
        """Lese Daten aus dem Scratchpad."""
        return self._scratchpad.get(key, default)

    def clear_scratchpad(self) -> None:
        self._scratchpad.clear()

    # ── Task Complexity Check ────────────────────────────────────────────

    async def needs_coordination(
        self,
        user_query: str,
        side_query_engine=None,
    ) -> bool:
        """
        Prüfe ob eine Anfrage Coordinator-Mode braucht.
        Schnelle Klassifikation via SideQuery.
        """
        if not side_query_engine:
            return False

        # Einfache Heuristik zuerst (sparen wir uns den LLM-Call)
        lower = user_query.lower()
        multi_indicators = [
            " und gleichzeitig ", " parallel ", " außerdem ",
            " zusätzlich ", " nebenbei ", " während ",
        ]
        if not any(ind in lower for ind in multi_indicators):
            # Keine Multi-Task Indikatoren → kein Coordinator nötig
            return False

        result = await side_query_engine.classify(
            text=user_query,
            categories=["single_task", "multi_task"],
            context="Ist das ein einzelner Task oder mehrere unabhängige Tasks?",
        )

        return result == "multi_task"

    # ── Stats ────────────────────────────────────────────────────────────

    @property
    def active_workers_count(self) -> int:
        return sum(1 for w in self._active_workers.values() if not w.is_terminal)

    @property
    def stats(self) -> dict:
        return {
            "active_workers": self.active_workers_count,
            "total_workers": len(self._active_workers),
            "total_sessions": len(self._history),
        }


# ── Module-Level Singleton ───────────────────────────────────────────────

_coordinator: Optional[CoordinatorMode] = None


def get_coordinator() -> CoordinatorMode:
    global _coordinator
    if _coordinator is None:
        _coordinator = CoordinatorMode()
    return _coordinator
