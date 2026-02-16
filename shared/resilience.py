"""
SOMA-AI Resilience Layer
=========================
Asynchroner Circuit Breaker + Retry-Logik für ALLE externen Calls.
Jeder Service (Ollama, Postgres, MQTT, Redis, HA) wird hierdurch geschützt.

Datenfluss:
  brain_core/* ──► SomaCircuitBreaker.call(fn) ──► Externer Service
                          │
                          ├─ CLOSED:   Call geht durch, Fehler werden gezählt
                          ├─ OPEN:     Call wird sofort abgelehnt (Fallback)
                          └─ HALF_OPEN: Probe-Call, bei Erfolg → CLOSED
"""

from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Any, Callable, Awaitable, Optional, TypeVar
from functools import wraps

import structlog

logger = structlog.get_logger("soma.resilience")

T = TypeVar("T")


# ── Circuit Breaker States ───────────────────────────────────────────────

class CircuitState(str, Enum):
    CLOSED = "closed"        # Normal – Calls gehen durch
    OPEN = "open"            # Gesperrt – Sofort-Fallback
    HALF_OPEN = "half_open"  # Probe – Ein Call wird durchgelassen


# ── Circuit Breaker ──────────────────────────────────────────────────────

class SomaCircuitBreaker:
    """
    Async Circuit Breaker für SOMA-AI.
    Schützt vor Kaskaden-Ausfällen wenn Ollama, DB oder MQTT offline gehen.

    Usage:
        cb = SomaCircuitBreaker(name="ollama", failure_threshold=3)
        result = await cb.call(ollama_client.generate, prompt="Hallo")
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 1,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._failure_count: int = 0
        self._success_count: int = 0
        self._last_failure_time: float = 0.0
        self._half_open_calls: int = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                logger.info("circuit_half_open", breaker=self.name)
        return self._state

    async def call(
        self,
        func: Callable[..., Awaitable[T]],
        *args: Any,
        fallback: Optional[Callable[..., Awaitable[T]]] = None,
        **kwargs: Any,
    ) -> T:
        """Execute func through the circuit breaker."""
        async with self._lock:
            current_state = self.state

            if current_state == CircuitState.OPEN:
                logger.warning(
                    "circuit_open_rejected",
                    breaker=self.name,
                    failures=self._failure_count,
                )
                if fallback:
                    return await fallback(*args, **kwargs)
                raise CircuitOpenError(
                    f"[{self.name}] Circuit OPEN – {self._failure_count} Fehler, "
                    f"Retry in {self.recovery_timeout}s"
                )

            if current_state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.half_open_max_calls:
                    if fallback:
                        return await fallback(*args, **kwargs)
                    raise CircuitOpenError(
                        f"[{self.name}] HALF_OPEN max probes reached"
                    )
                self._half_open_calls += 1

        # Call ausführen (außerhalb des Locks)
        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result
        except Exception as exc:
            await self._on_failure(exc)
            if fallback:
                return await fallback(*args, **kwargs)
            raise

    async def _on_success(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                logger.info("circuit_recovered", breaker=self.name)
            self._failure_count = 0
            self._success_count += 1

    async def _on_failure(self, exc: Exception) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            logger.error(
                "circuit_failure",
                breaker=self.name,
                failures=self._failure_count,
                threshold=self.failure_threshold,
                error=str(exc),
            )
            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.critical(
                    "circuit_opened",
                    breaker=self.name,
                    failures=self._failure_count,
                )

    def reset(self) -> None:
        """Manual reset (e.g. from admin dashboard)."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_calls = 0
        logger.info("circuit_manual_reset", breaker=self.name)


# ── Retry Logic ──────────────────────────────────────────────────────────

class SomaRetryLogic:
    """
    Exponential Backoff Retry mit Jitter.

    Usage:
        retry = SomaRetryLogic(max_retries=3, base_delay=0.5)
        result = await retry.execute(my_async_func, arg1, kwarg1=val)
    """

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 30.0,
        exponential_base: float = 2.0,
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base

    async def execute(
        self,
        func: Callable[..., Awaitable[T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        last_exception: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                return await func(*args, **kwargs)
            except Exception as exc:
                last_exception = exc
                if attempt < self.max_retries:
                    delay = min(
                        self.base_delay * (self.exponential_base ** attempt),
                        self.max_delay,
                    )
                    # Jitter: ±25%
                    import random
                    jitter = delay * 0.25 * (2 * random.random() - 1)
                    actual_delay = max(0, delay + jitter)
                    logger.warning(
                        "retry_attempt",
                        attempt=attempt + 1,
                        max_retries=self.max_retries,
                        delay=round(actual_delay, 2),
                        error=str(exc),
                    )
                    await asyncio.sleep(actual_delay)

        raise last_exception  # type: ignore[misc]


# ── Decorator für einfache Nutzung ───────────────────────────────────────

def with_circuit_breaker(
    breaker: SomaCircuitBreaker,
    fallback: Optional[Callable] = None,
):
    """Decorator: Wraps async function in a circuit breaker."""
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            return await breaker.call(func, *args, fallback=fallback, **kwargs)
        wrapper._breaker = breaker  # type: ignore[attr-defined]
        return wrapper
    return decorator


# ── Exceptions ───────────────────────────────────────────────────────────

class CircuitOpenError(Exception):
    """Raised when a circuit breaker is open and no fallback is provided."""
    pass


class SomaServiceUnavailable(Exception):
    """Raised when a critical SOMA service is unreachable after retries."""
    pass
