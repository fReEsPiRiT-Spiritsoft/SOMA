"""
SOMA-AI Health Monitor
=======================
Überwacht CPU/RAM/VRAM/Temperatur in Echtzeit.
Liefert SystemMetrics an den LogicRouter für Model-Routing-Entscheidungen.

Datenfluss:
  health_monitor.py ──► SystemMetrics ──► logic_router.py ──► Engine-Wahl
                                      ──► queue_handler.py (bei CRITICAL)
                                      ──► Dashboard via WebSocket
"""

from __future__ import annotations

import asyncio
from typing import Optional, Callable, Awaitable

import psutil
import structlog

from shared.health_schemas import (
    SystemMetrics,
    GpuMetrics,
    SystemLoadLevel,
    ServiceHealth,
    ServiceStatus,
    SystemHealthReport,
)
from brain_core.config import settings

logger = structlog.get_logger("soma.health")

# GPU monitoring (optional – graceful degradation ohne GPU)
try:
    import GPUtil
    GPU_AVAILABLE = True
except ImportError:
    GPU_AVAILABLE = False
    logger.warning("gputil_not_available", msg="GPU monitoring disabled")


class HealthMonitor:
    """
    Periodischer Health-Check mit konfigurierbaren Callbacks.
    Publiziert SystemMetrics alle N Sekunden.
    """

    def __init__(
        self,
        interval: float = 5.0,
        on_metrics: Optional[Callable[[SystemMetrics], Awaitable[None]]] = None,
        on_critical: Optional[Callable[[SystemMetrics], Awaitable[None]]] = None,
        heavy_engine: Optional[object] = None,  # HeavyLlamaEngine-Referenz für VRAM-Druck
    ):
        self.interval = interval
        self._on_metrics = on_metrics
        self._on_critical = on_critical
        self._heavy_engine = heavy_engine
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_metrics: Optional[SystemMetrics] = None

    @property
    def last_metrics(self) -> Optional[SystemMetrics]:
        return self._last_metrics

    # ── Metrics Collection ───────────────────────────────────────────────

    @staticmethod
    def collect_metrics() -> SystemMetrics:
        """Synchroner Snapshot der aktuellen Systemlast."""
        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0.1)

        metrics = SystemMetrics(
            cpu_percent=cpu,
            ram_total_mb=mem.total / (1024 * 1024),
            ram_used_mb=mem.used / (1024 * 1024),
            ram_percent=mem.percent,
        )

        # CPU-Temperatur (Linux)
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                for name, entries in temps.items():
                    if entries:
                        metrics.cpu_temp_celsius = entries[0].current
                        break
        except (AttributeError, KeyError):
            pass

        # GPU Metriken
        if GPU_AVAILABLE:
            try:
                gpus = GPUtil.getGPUs()
                if gpus:
                    g = gpus[0]
                    metrics.gpu = GpuMetrics(
                        gpu_id=g.id,
                        name=g.name,
                        vram_total_mb=g.memoryTotal,
                        vram_used_mb=g.memoryUsed,
                        vram_percent=(g.memoryUsed / g.memoryTotal * 100)
                        if g.memoryTotal > 0
                        else 0.0,
                        gpu_temp_celsius=g.temperature or 0.0,
                        gpu_utilization_percent=g.load * 100 if g.load else 0.0,
                    )
            except Exception as exc:
                logger.debug("gpu_metrics_error", error=str(exc))

        # Load Level berechnen
        metrics.load_level = HealthMonitor._calculate_load_level(metrics)

        return metrics

    @staticmethod
    def _calculate_load_level(m: SystemMetrics) -> SystemLoadLevel:
        """Bestimme Load-Level basierend auf RAM + VRAM."""
        ram = m.ram_percent
        vram = m.gpu.vram_percent if m.gpu else 0.0
        peak = max(ram, vram)

        if peak >= settings.health_ram_critical_percent:
            return SystemLoadLevel.CRITICAL
        elif peak >= settings.health_ram_warn_percent:
            return SystemLoadLevel.HIGH
        elif peak >= 60:
            return SystemLoadLevel.ELEVATED
        elif peak >= 30:
            return SystemLoadLevel.NORMAL
        return SystemLoadLevel.IDLE

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="soma-health-monitor")
        logger.info("health_monitor_started", interval=self.interval)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("health_monitor_stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                metrics = await asyncio.get_event_loop().run_in_executor(
                    None, self.collect_metrics
                )
                self._last_metrics = metrics

                # Callback: Jeder Tick
                if self._on_metrics:
                    await self._on_metrics(metrics)

                # Callback: Nur bei CRITICAL
                if (
                    metrics.load_level == SystemLoadLevel.CRITICAL
                    and self._on_critical
                ):
                    logger.warning(
                        "system_critical",
                        ram=f"{metrics.ram_percent:.1f}%",
                        vram=f"{metrics.gpu.vram_percent:.1f}%"
                        if metrics.gpu
                        else "n/a",
                    )
                    await self._on_critical(metrics)

                # VRAM-Druck: Heavy-Engine sofort entladen wenn > 90%
                if (
                    self._heavy_engine is not None
                    and metrics.gpu is not None
                    and metrics.gpu.vram_percent > settings.heavy_engine_max_vram_pct
                ):
                    self._heavy_engine.notify_vram_pressure()

            except Exception as exc:
                logger.error("health_monitor_error", error=str(exc))

            await asyncio.sleep(self.interval)

    # ── Service Health Checks ────────────────────────────────────────────

    async def check_service(
        self,
        name: str,
        check_fn: Callable[[], Awaitable[bool]],
    ) -> ServiceHealth:
        """Prüfe einen einzelnen Service (Redis, Ollama, etc.)."""
        import time

        start = time.monotonic()
        try:
            ok = await check_fn()
            latency = (time.monotonic() - start) * 1000
            return ServiceHealth(
                name=name,
                status=ServiceStatus.HEALTHY if ok else ServiceStatus.DEGRADED,
                latency_ms=round(latency, 2),
            )
        except Exception as exc:
            latency = (time.monotonic() - start) * 1000
            return ServiceHealth(
                name=name,
                status=ServiceStatus.UNAVAILABLE,
                latency_ms=round(latency, 2),
                error=str(exc),
            )
