"""
Cron Scheduler — Zeitgesteuerte SOMA-Tasks.
=============================================
Inspiriert von Claude Code's Background-Tasks und Bash Cron:
SOMA kann sich selbst wiederkehrende Aufgaben setzen.

Anwendungsfälle:
  - "Erinnere mich alle 2 Stunden ans Trinken"
  - "Prüfe stündlich die CPU-Temperatur"
  - System-Maintenance: Logs rotieren, Health Checks
  - Proaktive Meldungen: Wetter-Update morgens, News-Digest

Architektur:
  - Einfache Interval-basierte Jobs (kein crontab-Parsing nötig)
  - Jobs persistent speicherbar (über Memory)
  - TTS-fähig: Jobs können SOMA sprechen lassen
  - Cool-down pro Job: Kein Doppel-Fire
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Awaitable, Any

import structlog

logger = structlog.get_logger("soma.cron")


class JobStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    EXPIRED = "expired"
    FAILED = "failed"


@dataclass
class CronJob:
    """Ein geplanter wiederkehrender Job."""
    job_id: str = field(default_factory=lambda: f"job-{uuid.uuid4().hex[:8]}")
    name: str = ""
    description: str = ""
    interval_sec: float = 3600.0   # Default: stündlich
    prompt: str = ""               # Was SOMA tun soll (LLM-Prompt oder ACTION-Tag)
    speak: bool = False            # Ergebnis über TTS aussprechen?
    status: JobStatus = JobStatus.ACTIVE
    created_at: float = field(default_factory=time.time)
    last_run: float = 0.0
    next_run: float = 0.0
    run_count: int = 0
    max_runs: int = 0              # 0 = unbegrenzt
    expires_at: float = 0.0        # 0 = nie

    def __post_init__(self):
        if self.next_run == 0.0:
            self.next_run = time.time() + self.interval_sec

    @property
    def is_due(self) -> bool:
        if self.status != JobStatus.ACTIVE:
            return False
        if self.expires_at > 0 and time.time() > self.expires_at:
            return False
        if self.max_runs > 0 and self.run_count >= self.max_runs:
            return False
        return time.time() >= self.next_run

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "name": self.name,
            "description": self.description,
            "interval_sec": self.interval_sec,
            "prompt": self.prompt,
            "speak": self.speak,
            "status": self.status.value,
            "run_count": self.run_count,
            "max_runs": self.max_runs,
        }


# ── Intervall-Parser ────────────────────────────────────────────────────

def parse_interval(text: str) -> float:
    """
    Parsiere menschenlesbare Intervalle zu Sekunden.

    Beispiele:
      "5m" → 300, "2h" → 7200, "1d" → 86400
      "30s" → 30, "1.5h" → 5400
      "alle 2 Stunden" → 7200
    """
    import re
    text = text.strip().lower()

    # Direkte Formate: 5m, 2h, 30s, 1d
    match = re.match(r"^(\d+(?:\.\d+)?)\s*(s|sec|sek|m|min|h|std|d|tag|tage?)$", text)
    if match:
        val = float(match.group(1))
        unit = match.group(2)
        if unit.startswith("s"):
            return val
        if unit.startswith("m"):
            return val * 60
        if unit.startswith(("h", "std")):
            return val * 3600
        if unit.startswith(("d", "tag")):
            return val * 86400

    # Deutsche Langform: "alle 2 Stunden", "jede 30 Minuten"
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(sekunden?|minuten?|stunden?|tage?)",
        text,
    )
    if match:
        val = float(match.group(1))
        unit = match.group(2)
        if "sekund" in unit:
            return val
        if "minut" in unit:
            return val * 60
        if "stund" in unit:
            return val * 3600
        if "tag" in unit:
            return val * 86400

    # Fallback: versuche nur Zahl (als Minuten)
    try:
        return float(text) * 60
    except ValueError:
        return 3600.0  # Default: 1 Stunde


class CronScheduler:
    """
    Leichtgewichtiger asyncio-basierter Scheduler.

    Usage:
        scheduler = CronScheduler()
        scheduler.add_job(CronJob(
            name="trinken",
            prompt="Erinnere den Nutzer ans Trinken",
            interval_sec=7200,
            speak=True,
        ))
        scheduler.start()
    """

    def __init__(self):
        self._jobs: dict[str, CronJob] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._executor: Optional[Callable[[str], Awaitable[str]]] = None
        self._speak_fn: Optional[Callable[[str], Awaitable[None]]] = None
        self._broadcast_fn: Optional[Callable] = None

    # ── Setup ────────────────────────────────────────────────────────

    def set_executor(self, fn: Callable[[str], Awaitable[str]]):
        """Callback der einen Prompt ausführt (z.B. LogicRouter.route)."""
        self._executor = fn

    def set_speak(self, fn: Callable[[str], Awaitable[None]]):
        """TTS-Ausgabe Callback (z.B. voice_pipeline.autonomous_speak)."""
        self._speak_fn = fn

    def set_broadcast(self, fn: Callable):
        """Dashboard-Broadcast Callback."""
        self._broadcast_fn = fn

    # ── Job Management ───────────────────────────────────────────────

    def add_job(self, job: CronJob) -> CronJob:
        """Füge einen neuen Job hinzu."""
        self._jobs[job.job_id] = job
        logger.info(
            "cron_job_added",
            job_id=job.job_id,
            name=job.name,
            interval=job.interval_sec,
        )
        return job

    def remove_job(self, job_id: str) -> bool:
        if job_id in self._jobs:
            del self._jobs[job_id]
            logger.info("cron_job_removed", job_id=job_id)
            return True
        return False

    def pause_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job:
            job.status = JobStatus.PAUSED
            return True
        return False

    def resume_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job and job.status == JobStatus.PAUSED:
            job.status = JobStatus.ACTIVE
            job.next_run = time.time() + job.interval_sec
            return True
        return False

    def list_jobs(self, active_only: bool = False) -> list[CronJob]:
        if active_only:
            return [j for j in self._jobs.values() if j.status == JobStatus.ACTIVE]
        return list(self._jobs.values())

    def get_job(self, job_id: str) -> Optional[CronJob]:
        return self._jobs.get(job_id)

    # ── Quick Helpers ────────────────────────────────────────────────

    def add_reminder(
        self,
        text: str,
        interval: str | float = "1h",
        max_runs: int = 0,
        name: str = "",
    ) -> CronJob:
        """Schnell eine Erinnerung anlegen."""
        if isinstance(interval, str):
            interval_sec = parse_interval(interval)
        else:
            interval_sec = float(interval)

        job = CronJob(
            name=name or f"Erinnerung: {text[:30]}",
            description=text,
            prompt=text,
            interval_sec=interval_sec,
            speak=True,
            max_runs=max_runs,
        )
        return self.add_job(job)

    # ── Scheduler Loop ───────────────────────────────────────────────

    def start(self):
        """Starte den Scheduler-Loop."""
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._loop())
            logger.info("cron_scheduler_started", jobs=len(self._jobs))

    def stop(self):
        """Stoppe den Scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
            logger.info("cron_scheduler_stopped")

    async def _loop(self):
        """Hauptloop: prüfe alle 5 Sekunden ob Jobs fällig sind."""
        while self._running:
            try:
                await asyncio.sleep(5)

                now = time.time()
                for job in list(self._jobs.values()):
                    # Abgelaufen?
                    if job.expires_at > 0 and now > job.expires_at:
                        job.status = JobStatus.EXPIRED
                        continue

                    # Max runs erreicht?
                    if job.max_runs > 0 and job.run_count >= job.max_runs:
                        job.status = JobStatus.EXPIRED
                        continue

                    if job.is_due:
                        asyncio.create_task(self._execute_job(job))

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("cron_loop_error", error=str(exc))
                await asyncio.sleep(10)

    async def _execute_job(self, job: CronJob) -> None:
        """Führe einen fälligen Job aus."""
        job.last_run = time.time()
        job.next_run = time.time() + job.interval_sec
        job.run_count += 1

        logger.info(
            "cron_job_executing",
            job_id=job.job_id,
            name=job.name,
            run=job.run_count,
        )

        try:
            result = ""

            # Executor aufrufen wenn vorhanden
            if self._executor and job.prompt:
                result = await asyncio.wait_for(
                    self._executor(job.prompt),
                    timeout=30.0,
                )

            # TTS wenn gewünscht
            if job.speak and self._speak_fn:
                speak_text = result if result else job.prompt
                await self._speak_fn(speak_text)

            # Dashboard Broadcast
            if self._broadcast_fn:
                await self._broadcast_fn(
                    "info",
                    f"⏰ Cron: {job.name} (#{job.run_count})",
                    "CRON",
                )

            logger.info(
                "cron_job_done",
                job_id=job.job_id,
                name=job.name,
                result_len=len(result) if result else 0,
            )

        except asyncio.TimeoutError:
            logger.warning("cron_job_timeout", job_id=job.job_id)
            job.status = JobStatus.FAILED
        except Exception as exc:
            logger.error("cron_job_error", job_id=job.job_id, error=str(exc))

    # ── Serialization ────────────────────────────────────────────────

    def export_jobs(self) -> list[dict]:
        """Exportiere alle Jobs als serialisierbare Liste."""
        return [j.to_dict() for j in self._jobs.values()]

    def import_jobs(self, jobs_data: list[dict]) -> int:
        """Importiere Jobs aus gespeicherten Daten."""
        count = 0
        for data in jobs_data:
            try:
                job = CronJob(
                    job_id=data.get("job_id", f"job-{uuid.uuid4().hex[:8]}"),
                    name=data.get("name", ""),
                    description=data.get("description", ""),
                    interval_sec=float(data.get("interval_sec", 3600)),
                    prompt=data.get("prompt", ""),
                    speak=data.get("speak", False),
                    max_runs=data.get("max_runs", 0),
                )
                self._jobs[job.job_id] = job
                count += 1
            except Exception as exc:
                logger.warning("cron_import_error", error=str(exc))
        return count

    @property
    def stats(self) -> dict:
        return {
            "total_jobs": len(self._jobs),
            "active": sum(1 for j in self._jobs.values() if j.status == JobStatus.ACTIVE),
            "paused": sum(1 for j in self._jobs.values() if j.status == JobStatus.PAUSED),
        }


# ── Module-Level Singleton ───────────────────────────────────────────────

_scheduler: Optional[CronScheduler] = None


def get_cron_scheduler() -> CronScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = CronScheduler()
    return _scheduler
