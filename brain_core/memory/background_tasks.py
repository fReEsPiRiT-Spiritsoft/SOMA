"""
Background Tasks — Stilles Denken wenn SOMA idle ist.
  • Konsolidierung: Episoden → Fakten
  • Vergessen: Alte unwichtige Episoden ausdünnen
"""

from __future__ import annotations

import time
import asyncio
import logging
from typing import Optional, Callable, Awaitable, TYPE_CHECKING

if TYPE_CHECKING:
    from brain_core.memory.memory_orchestrator import MemoryOrchestrator

logger = logging.getLogger("soma.memory.background")

IDLE_THRESHOLD_SEC = 60          # 60s ohne Interaktion → idle
CONSOLIDATION_COOLDOWN_SEC = 900 # Max alle 15 Minuten


class BackgroundConsolidator:

    def __init__(
        self,
        memory_orchestrator: MemoryOrchestrator,
        llm_callable: Optional[Callable[[str], Awaitable[str]]] = None,
    ):
        self._memory = memory_orchestrator
        self._llm_callable = llm_callable
        self._last_activity: float = time.time()
        self._last_consolidation: float = 0
        self._running = False
        self._task: Optional[asyncio.Task] = None

    # ── Public ───────────────────────────────────────────────────────

    def touch(self):
        """Bei jeder User-Interaktion aufrufen → idle-Timer reset."""
        self._last_activity = time.time()

    def set_llm(self, llm_callable: Callable[[str], Awaitable[str]]):
        self._llm_callable = llm_callable

    def start(self):
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._loop())
            logger.info("Background consolidator started")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    # ── Loop ─────────────────────────────────────────────────────────

    async def _loop(self):
        while self._running:
            try:
                await asyncio.sleep(10)

                idle_time = time.time() - self._last_activity
                since_last = time.time() - self._last_consolidation

                if (
                    idle_time > IDLE_THRESHOLD_SEC
                    and since_last > CONSOLIDATION_COOLDOWN_SEC
                ):
                    logger.info(
                        f"SOMA idle for {idle_time:.0f}s — "
                        "background consolidation"
                    )
                    await self._run_consolidation()
                    self._last_consolidation = time.time()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Background task error: {e}")
                await asyncio.sleep(30)

    async def _run_consolidation(self):
        if self._llm_callable:
            await self._memory.consolidate(llm_callable=self._llm_callable)
        await self._prune_old_episodes()

    async def _prune_old_episodes(self):
        """Löscht Episoden > 30 Tage mit Importance < 0.4."""
        try:
            conn = self._memory.episodic._conn
            if not conn:
                return
            cutoff = time.time() - (30 * 86400)
            loop = asyncio.get_event_loop()
            deleted = await loop.run_in_executor(
                None, self._do_prune, conn, cutoff,
            )
            if deleted > 0:
                logger.info(f"Pruned {deleted} old low-importance episodes")
        except Exception as e:
            logger.warning(f"Prune failed: {e}")

    @staticmethod
    def _do_prune(conn, cutoff: float) -> int:
        cur = conn.execute(
            "DELETE FROM episodes WHERE timestamp < ? AND importance < 0.4",
            (cutoff,),
        )
        conn.commit()
        return cur.rowcount
