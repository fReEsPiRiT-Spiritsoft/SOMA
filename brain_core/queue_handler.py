"""
SOMA-AI Queue Handler
======================
Redis-basierte Warteschlange für Deferred Reasoning.
Wenn das System unter Last steht (CRITICAL), werden Anfragen hier geparkt
und asynchron abgearbeitet sobald Ressourcen frei werden.

Datenfluss:
  User-Request ──► logic_router (CRITICAL?) ──► queue_handler.enqueue()
                                                     │
                                                     ▼
                                               Redis Queue
                                                     │
                                              queue_handler._worker()
                                                     │
                                                     ▼
                                              engine.generate()
                                                     │
                                                     ▼
                                              Callback / WebSocket
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Optional, Callable, Awaitable

import redis.asyncio as aioredis
import structlog

from shared.health_schemas import DeferredRequest
from shared.resilience import SomaCircuitBreaker, SomaRetryLogic
from brain_core.config import settings

logger = structlog.get_logger("soma.queue")

QUEUE_KEY = "soma:deferred_queue"
PROCESSING_KEY = "soma:processing"
RESULTS_PREFIX = "soma:result:"


class QueueHandler:
    """
    Async Redis Queue für geparkte Anfragen.
    Priority-Queue via Sorted Sets.
    """

    def __init__(self):
        self._redis: Optional[aioredis.Redis] = None
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None
        self._process_callback: Optional[
            Callable[[DeferredRequest], Awaitable[str]]
        ] = None
        self._result_callback: Optional[
            Callable[[str, str], Awaitable[None]]
        ] = None
        self._ready_check: Optional[Callable[[], bool]] = None

        # Circuit Breaker für Redis
        self._cb = SomaCircuitBreaker(
            name="redis-queue",
            failure_threshold=5,
            recovery_timeout=15.0,
        )
        self._retry = SomaRetryLogic(max_retries=3, base_delay=0.5)

    async def connect(self) -> None:
        """Verbindung zu Redis herstellen."""
        self._redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=10,
        )
        # Connection test
        await self._cb.call(self._redis.ping)
        logger.info("queue_connected", redis_url=settings.redis_host)

    async def disconnect(self) -> None:
        await self.stop_worker()
        if self._redis:
            await self._redis.aclose()
            logger.info("queue_disconnected")

    # ── Enqueue / Dequeue ────────────────────────────────────────────────

    async def enqueue(self, request: DeferredRequest) -> str:
        """
        Anfrage in die Priority-Queue schieben.
        Returns: request_id
        """
        if not request.request_id:
            request.request_id = str(uuid.uuid4())

        payload = request.model_dump_json()

        async def _push():
            # Sorted Set: Score = priority (niedriger = wichtiger)
            await self._redis.zadd(QUEUE_KEY, {payload: request.priority})

        await self._cb.call(_push)

        logger.info(
            "request_enqueued",
            request_id=request.request_id,
            priority=request.priority,
            queue_size=await self.queue_size(),
        )
        return request.request_id

    async def dequeue(self) -> Optional[DeferredRequest]:
        """Nächste Anfrage aus der Queue holen (niedrigste Priority = wichtigst)."""

        async def _pop() -> Optional[str]:
            # Atomares Pop vom Sorted Set
            results = await self._redis.zpopmin(QUEUE_KEY, count=1)
            if results:
                return results[0][0]  # (member, score)
            return None

        raw = await self._cb.call(_pop)
        if raw:
            return DeferredRequest.model_validate_json(raw)
        return None

    async def queue_size(self) -> int:
        try:
            return await self._redis.zcard(QUEUE_KEY)
        except Exception:
            return -1

    async def store_result(self, request_id: str, result: str, ttl: int = 300) -> None:
        """Ergebnis eines deferred Requests speichern."""
        key = f"{RESULTS_PREFIX}{request_id}"
        await self._redis.setex(key, ttl, result)

    async def get_result(self, request_id: str) -> Optional[str]:
        key = f"{RESULTS_PREFIX}{request_id}"
        return await self._redis.get(key)

    # ── Background Worker ────────────────────────────────────────────────

    def set_process_callback(
        self,
        callback: Callable[[DeferredRequest], Awaitable[str]],
    ) -> None:
        """Setze die Funktion die geparkte Anfragen abarbeitet."""
        self._process_callback = callback

    def set_result_callback(
        self,
        callback: Callable[[str, str], Awaitable[None]],
    ) -> None:
        """Callback wenn Ergebnis fertig (z.B. WebSocket-Push)."""
        self._result_callback = callback

    def set_ready_check(
        self,
        check: Callable[[], bool],
    ) -> None:
        """Setze eine Funktion die prüft ob Verarbeitung möglich ist.

        Der Worker wartet bis diese Funktion True zurückgibt bevor er
        den nächsten Request aus der Queue verarbeitet.
        Typisch: Prüfe ob die Heavy-Engine gerade frei ist.
        """
        self._ready_check = check

    async def start_worker(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(
            self._worker_loop(), name="soma-queue-worker"
        )
        logger.info("queue_worker_started")

    async def stop_worker(self) -> None:
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("queue_worker_stopped")

    async def _worker_loop(self) -> None:
        """Verarbeite geparkte Anfragen wenn Ressourcen frei werden.

        Wartet bis:
          1. Ein Request in der Queue liegt
          2. Die ready_check-Funktion True zurückgibt (Heavy-Engine frei)
        Erst dann wird der Request verarbeitet.
        """
        _wait_logged = False

        while self._running:
            try:
                # Prüfe ob Heavy-Engine frei ist bevor wir etwas verarbeiten
                if self._ready_check and not self._ready_check():
                    if not _wait_logged:
                        logger.debug("queue_worker_waiting", reason="engine_busy")
                        _wait_logged = True
                    await asyncio.sleep(1.0)
                    continue
                _wait_logged = False

                request = await self.dequeue()
                if request and self._process_callback:
                    logger.info(
                        "processing_deferred",
                        request_id=request.request_id,
                    )
                    try:
                        result = await self._process_callback(request)
                        await self.store_result(request.request_id, result)
                        if self._result_callback:
                            await self._result_callback(request.request_id, result)
                        logger.info(
                            "deferred_completed",
                            request_id=request.request_id,
                            result_len=len(result) if result else 0,
                        )
                    except Exception as exc:
                        logger.error(
                            "deferred_processing_failed",
                            request_id=request.request_id,
                            error=str(exc),
                        )
                else:
                    # Keine Arbeit → kurz warten
                    await asyncio.sleep(2.0)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("queue_worker_error", error=str(exc))
                await asyncio.sleep(5.0)
