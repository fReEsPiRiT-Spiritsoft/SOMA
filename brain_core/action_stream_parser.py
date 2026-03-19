"""
SOMA-AI Action Stream Parser
==============================
Scannt den LLM-Token-Stream in Echtzeit auf [ACTION:...] Tags.
Feuert Actions SOFORT wenn ein Tag vollständig erkannt wird.
Gibt gleichzeitig den "sprechbaren" Text (ohne Tags) weiter.

State-Machine:
  NORMAL → Text sammeln, direkt ausgeben
  "[" erkannt → Puffer starten (könnte ein Tag sein)
  "[ACTION:" erkannt → Tag-Modus, sammle bis "]"
  "]" in Tag-Modus → Tag vollständig → executor() → aus Output entfernen
  Timeout/kein Match → Puffer als normalen Text ausgeben
"""

from __future__ import annotations

import asyncio
import re
from typing import Callable, Awaitable, Optional

import structlog

logger = structlog.get_logger("soma.action_stream_parser")

# Regex für Parameter-Parsing innerhalb eines fertigen Tags
PARAM_PATTERN = re.compile(r'(\w+)="([^"]*)"')


class ActionStreamParser:
    """
    Echtzeit-Token-Scanner für [ACTION:type key="value"] Tags.

    Nutzung:
        parser = ActionStreamParser(action_executor=my_callback)
        async for token in llm_stream:
            speakable = await parser.feed(token)
            if speakable:
                tts_buffer += speakable
        # Am Ende flushen
        remaining = parser.flush()
    """

    def __init__(
        self,
        action_executor: Callable[[str, str, dict], Awaitable[None]],
    ):
        """
        Args:
            action_executor: async callback(action_type, raw_tag, params_dict)
                             Wird aufgerufen wenn ein Tag vollständig erkannt wurde.
        """
        self._executor = action_executor
        self._buffer = ""           # Puffer für potenzielle Tags
        self._in_bracket = False    # Wir sind innerhalb von [...]
        self._full_text = ""        # Gesamter bisheriger Text (mit Tags)
        self._clean_text = ""       # Text OHNE Action Tags
        self._fired_tags: list[str] = []
        self._action_spoke = False  # Ob eine Action selber TTS gemacht hat

    async def feed(self, token: str) -> str:
        """
        Feed ein Token aus dem LLM-Stream.

        Returns: Sprechbarer Text (ohne Tags). Kann leer sein wenn wir
                 mitten in einem potenziellen Tag sind.
        """
        self._full_text += token
        speakable = ""

        for char in token:
            if self._in_bracket:
                self._buffer += char
                if char == "]":
                    # Bracket geschlossen — ist es ein ACTION Tag?
                    tag_content = self._buffer  # "[ACTION:type ...]" oder "[sonstiges]"
                    self._in_bracket = False
                    self._buffer = ""

                    if tag_content.startswith("[ACTION:"):
                        # Vollständiger Action Tag erkannt!
                        await self._fire_tag(tag_content)
                    else:
                        # Kein Action Tag — war normaler Text mit Klammern
                        speakable += tag_content
                        self._clean_text += tag_content

                elif len(self._buffer) > 500:
                    # Sicherheit: Zu langer Puffer = kein Tag, Buffer flushen
                    speakable += self._buffer
                    self._clean_text += self._buffer
                    self._buffer = ""
                    self._in_bracket = False

            elif char == "[":
                # Potenzieller Tag-Start
                self._in_bracket = True
                self._buffer = "["

            else:
                speakable += char
                self._clean_text += char

        return speakable

    def flush(self) -> str:
        """
        Flush am Ende des Streams — gibt übrig gebliebenen Puffer als Text zurück.
        """
        remaining = ""
        if self._buffer:
            remaining = self._buffer
            self._clean_text += self._buffer
            self._buffer = ""
            self._in_bracket = False
        return remaining

    async def _fire_tag(self, tag: str) -> None:
        """Parse und feuere einen erkannten [ACTION:type ...] Tag."""
        # Parse: [ACTION:type key="value" key2="value2"]
        match = re.match(r'\[ACTION:(\w+)(.*?)\]', tag, re.DOTALL)
        if not match:
            # Fehlformatiert — als Text ausgeben
            self._clean_text += tag
            return

        action_type = match.group(1).lower()
        params_raw = match.group(2)
        params = dict(PARAM_PATTERN.findall(params_raw))

        # Spezial-Params ohne Anführungszeichen (z.B. minutes=5)
        for kv_match in re.finditer(r'(\w+)=(\d+)', params_raw):
            key, val = kv_match.group(1), kv_match.group(2)
            if key not in params:
                params[key] = val

        logger.info(
            "stream_action_fired",
            action=action_type,
            params=params,
            tag=tag[:80],
        )

        self._fired_tags.append(tag)

        try:
            await self._executor(action_type, tag, params)
        except Exception as exc:
            logger.error(
                "stream_action_executor_error",
                action=action_type,
                error=str(exc),
            )

    def get_clean_text(self) -> str:
        """Gesamter bisheriger Text OHNE Action Tags."""
        return self._clean_text

    def get_full_text(self) -> str:
        """Gesamter bisheriger Text MIT Action Tags."""
        return self._full_text

    def get_fired_tags(self) -> list[str]:
        """Liste aller bisher gefeuerten Action Tags."""
        return list(self._fired_tags)

    @property
    def action_spoke(self) -> bool:
        """Ob eine gefeuerte Action selber TTS ausgeführt hat."""
        return self._action_spoke

    @action_spoke.setter
    def action_spoke(self, value: bool) -> None:
        self._action_spoke = value
