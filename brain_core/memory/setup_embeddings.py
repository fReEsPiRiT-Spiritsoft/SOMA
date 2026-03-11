"""
Stellt sicher dass das Embedding-Model (nomic-embed-text) in Ollama vorhanden ist.
Einmaliger ~274 MB Download beim ersten Start.
"""

from __future__ import annotations

import os
import asyncio
import logging

import aiohttp

logger = logging.getLogger("soma.memory.setup")

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = "nomic-embed-text"


async def ensure_embedding_model() -> bool:
    """Prüft ob nomic-embed-text installiert ist, pullt es sonst."""
    try:
        async with aiohttp.ClientSession() as session:
            # Check ob Model bereits vorhanden
            async with session.post(
                f"{OLLAMA_URL}/api/show",
                json={"name": EMBED_MODEL},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    logger.info(
                        f"Embedding model '{EMBED_MODEL}' already available"
                    )
                    return True

            # Nicht da → pullen
            logger.info(
                f"Pulling embedding model '{EMBED_MODEL}' "
                "(einmalig, ~274 MB) ..."
            )
            async with session.post(
                f"{OLLAMA_URL}/api/pull",
                json={"name": EMBED_MODEL},
                timeout=aiohttp.ClientTimeout(total=600),
            ) as resp:
                if resp.status == 200:
                    async for _ in resp.content:
                        pass  # Stream lesen bis fertig
                    logger.info(
                        f"Embedding model '{EMBED_MODEL}' "
                        "pulled successfully"
                    )
                    return True
                logger.error(
                    f"Failed to pull embedding model: {resp.status}"
                )
                return False

    except Exception as e:
        logger.error(f"Embedding model setup failed: {e}")
        return False
