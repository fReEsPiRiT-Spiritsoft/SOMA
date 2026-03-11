"""
Speculative Pre-Loader — Lädt Kontext während STT noch arbeitet.
Wird beim Wake-Word getriggert; wenn die Frage fertig ist, ist der
Gedächtnis-Kontext bereits bereit.
"""

from __future__ import annotations

import time
import asyncio
import logging
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from brain_core.memory.memory_orchestrator import MemoryOrchestrator

logger = logging.getLogger("soma.memory.preloader")


class SpeculativePreloader:

    def __init__(self, memory_orchestrator: MemoryOrchestrator):
        self._memory = memory_orchestrator
        self._preloaded_context: Optional[str] = None
        self._preload_time: float = 0
        self._preload_task: Optional[asyncio.Task] = None

    async def on_wake_word(self):
        """Sofort aufrufen wenn Wake-Word / VAD-Segment erkannt wird."""
        self._preloaded_context = None
        self._preload_task = asyncio.create_task(self._preload())

    async def _preload(self):
        start = time.time()
        try:
            last_topic = (
                self._memory.working.last_user_text or "allgemein"
            )

            personality, recent = await asyncio.gather(
                self._memory.semantic.get_personality_snapshot(),
                self._memory.episodic.recall(
                    last_topic, top_k=3, max_age_hours=48,
                ),
                return_exceptions=True,
            )

            blocks: list[str] = []
            now = datetime.now()
            blocks.append(
                f"[Zeitkontext] {now.strftime('%A, %d. %B %Y, %H:%M Uhr')}"
            )

            if isinstance(personality, str) and personality:
                blocks.append(f"[Langzeit-Wissen]\n{personality}")

            if isinstance(recent, list) and recent:
                ep_lines = []
                for ep in recent[:3]:
                    age_h = (time.time() - ep.timestamp) / 3600
                    ago = (
                        f"vor {int(age_h)}h"
                        if age_h >= 1
                        else f"vor {int(age_h * 60)}min"
                    )
                    ep_lines.append(f"- [{ago}] {ep.summary[:80]}")
                if ep_lines:
                    blocks.append(
                        "[Letzte Themen]\n" + "\n".join(ep_lines)
                    )

            self._preloaded_context = "\n\n".join(blocks)
            self._preload_time = time.time() - start
            logger.debug(
                f"Pre-loaded context in {self._preload_time * 1000:.0f}ms"
            )

        except Exception as e:
            logger.warning(f"Preload failed: {e}")
            self._preloaded_context = None

    async def get_preloaded_context(self) -> Optional[str]:
        """Wartet max 100ms auf den Preload-Task."""
        if self._preload_task and not self._preload_task.done():
            try:
                await asyncio.wait_for(self._preload_task, timeout=0.1)
            except asyncio.TimeoutError:
                pass
        return self._preloaded_context

    def invalidate(self):
        self._preloaded_context = None
        if self._preload_task and not self._preload_task.done():
            self._preload_task.cancel()
