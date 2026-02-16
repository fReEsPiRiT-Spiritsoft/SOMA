"""
SOMA-AI MQTT Listener
======================
Lauscht auf 'Hello'-Pakete neuer Hardware-Nodes.
Registriert sie automatisch in der Django SSOT.

Topic-Schema:
  soma/discovery/hello  → HardwareHello Payload
  soma/audio/{node_id}  → AudioChunkMeta
  soma/control/{node_id} → Device-Commands
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional, Callable, Awaitable

import structlog

from shared.audio_types import HardwareHello, AudioChunkMeta
from shared.resilience import SomaCircuitBreaker
from brain_core.config import settings

logger = structlog.get_logger("soma.mqtt")

# Topics
TOPIC_HELLO = "soma/discovery/hello"
TOPIC_AUDIO_PREFIX = "soma/audio/"
TOPIC_CONTROL_PREFIX = "soma/control/"


class MQTTListener:
    """
    Async MQTT Client für Hardware-Discovery und Audio-Streams.
    """

    def __init__(self):
        self._client = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._cb = SomaCircuitBreaker(name="mqtt", failure_threshold=5)

        # Callbacks
        self._on_hello: Optional[
            Callable[[HardwareHello], Awaitable[None]]
        ] = None
        self._on_audio: Optional[
            Callable[[AudioChunkMeta], Awaitable[None]]
        ] = None

    def set_hello_callback(
        self, callback: Callable[[HardwareHello], Awaitable[None]]
    ) -> None:
        self._on_hello = callback

    def set_audio_callback(
        self, callback: Callable[[AudioChunkMeta], Awaitable[None]]
    ) -> None:
        self._on_audio = callback

    async def start(self) -> None:
        """Starte den MQTT Listener."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._listen(), name="soma-mqtt-listener")
        logger.info("mqtt_listener_started", host=settings.mqtt_host)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("mqtt_listener_stopped")

    async def _listen(self) -> None:
        """Main MQTT Listen Loop mit Auto-Reconnect."""
        import aiomqtt

        while self._running:
            try:
                async with aiomqtt.Client(
                    hostname=settings.mqtt_host,
                    port=settings.mqtt_port,
                ) as client:
                    # Subscribe to discovery and audio topics
                    await client.subscribe(TOPIC_HELLO)
                    await client.subscribe(f"{TOPIC_AUDIO_PREFIX}#")

                    logger.info("mqtt_connected", topics=[TOPIC_HELLO, f"{TOPIC_AUDIO_PREFIX}#"])

                    async for message in client.messages:
                        topic = str(message.topic)
                        try:
                            payload = json.loads(message.payload)

                            if topic == TOPIC_HELLO:
                                hello = HardwareHello.model_validate(payload)
                                logger.info(
                                    "hardware_hello",
                                    node_id=hello.node_id,
                                    node_type=hello.node_type,
                                )
                                if self._on_hello:
                                    await self._on_hello(hello)

                            elif topic.startswith(TOPIC_AUDIO_PREFIX):
                                chunk = AudioChunkMeta.model_validate(payload)
                                if self._on_audio:
                                    await self._on_audio(chunk)

                        except Exception as exc:
                            logger.warning(
                                "mqtt_message_error",
                                topic=topic,
                                error=str(exc),
                            )

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("mqtt_connection_error", error=str(exc))
                await asyncio.sleep(5.0)  # Reconnect delay

    async def publish(self, topic: str, payload: dict) -> None:
        """Publish a message (e.g. control command to a node)."""
        import aiomqtt

        async def _pub():
            async with aiomqtt.Client(
                hostname=settings.mqtt_host,
                port=settings.mqtt_port,
            ) as client:
                await client.publish(topic, json.dumps(payload).encode())

        await self._cb.call(_pub)
