"""
SOMA-AI Action Stream Parser (Enhanced)
=========================================
Scannt den LLM-Token-Stream in Echtzeit auf [ACTION:...] Tags.
Feuert Actions SOFORT wenn ein Tag vollständig erkannt wird.
Gibt gleichzeitig den "sprechbaren" Text (ohne Tags) weiter.

Enhanced Features (Claude Code Pattern):
  - Integration mit ActionExecutor für Validierung & Orchestrierung
  - ActionResult-basierte Ergebnisse
  - Batch-Execution für Concurrency-Safe Actions
  - Strukturierte Fehlerbehandlung

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
from typing import Callable, Awaitable, Optional, Dict, Any, List, TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from brain_core.action_executor import ActionExecutor, ActionResult

logger = structlog.get_logger("soma.action_stream_parser")

# Regex für Parameter-Parsing innerhalb eines fertigen Tags
PARAM_PATTERN = re.compile(r'(\w+)="([^"]*)"')


class ActionStreamParser:
    """
    Echtzeit-Token-Scanner für [ACTION:type key="value"] Tags.

    Nutzung (Legacy):
        parser = ActionStreamParser(action_executor=my_callback)
        async for token in llm_stream:
            speakable = await parser.feed(token)
            if speakable:
                tts_buffer += speakable
        # Am Ende flushen
        remaining = parser.flush()
    
    Nutzung (Enhanced mit ActionExecutor):
        from brain_core.action_executor import get_executor
        
        parser = ActionStreamParser.with_executor(get_executor())
        async for token in llm_stream:
            speakable = await parser.feed(token)
            ...
        
        # Ergebnisse abrufen
        results = parser.get_results()
        reask_content = parser.get_reask_content()
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
        self._enhanced_executor: Optional["ActionExecutor"] = None
        self._buffer = ""           # Puffer für potenzielle Tags
        self._in_bracket = False    # Wir sind innerhalb von [...]
        self._full_text = ""        # Gesamter bisheriger Text (mit Tags)
        self._clean_text = ""       # Text OHNE Action Tags
        self._fired_tags: List[str] = []
        self._action_spoke = False  # Ob eine Action selber TTS gemacht hat
        
        # Enhanced: Ergebnis-Tracking
        self._results: List["ActionResult"] = []
        self._pending_actions: List[Dict[str, Any]] = []
        self._batch_mode = False

    @classmethod
    def with_executor(
        cls,
        executor: "ActionExecutor",
        batch_mode: bool = False,
    ) -> "ActionStreamParser":
        """
        Factory für Enhanced Mode mit ActionExecutor.
        
        Args:
            executor: ActionExecutor-Instanz
            batch_mode: Wenn True, werden Actions gesammelt und am Ende
                        als Batch ausgeführt (für bessere Parallelisierung)
        """
        async def enhanced_callback(action_type: str, raw_tag: str, params: dict) -> None:
            pass  # Wird überschrieben
        
        parser = cls(action_executor=enhanced_callback)
        parser._enhanced_executor = executor
        parser._batch_mode = batch_mode
        return parser

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

    async def flush_and_execute(self) -> List["ActionResult"]:
        """
        Enhanced: Flush und führe pending Actions aus (Batch-Mode).
        
        Returns:
            Liste aller ActionResults
        """
        self.flush()
        
        if self._batch_mode and self._pending_actions:
            # Batch-Ausführung über Orchestrator
            if self._enhanced_executor:
                results = await self._enhanced_executor.execute_batch(
                    self._pending_actions,
                    parallel=True
                )
                self._results.extend(results)
            self._pending_actions.clear()
        
        return self._results

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

        # Enhanced Mode: Nutze ActionExecutor
        if self._enhanced_executor:
            await self._fire_enhanced(action_type, params)
        else:
            # Legacy Mode: Nutze alten Callback
            try:
                await self._executor(action_type, tag, params)
            except Exception as exc:
                logger.error(
                    "stream_action_executor_error",
                    action=action_type,
                    error=str(exc),
                )

    async def _fire_enhanced(self, action_type: str, params: Dict[str, Any]) -> None:
        """
        Enhanced: Führe Action via ActionExecutor aus.
        
        - Batch-Mode: Sammle für spätere parallele Ausführung
        - Sofort-Mode: Führe sofort aus und speichere Result
        """
        if self._batch_mode:
            # Sammeln für Batch-Execution
            self._pending_actions.append({
                "type": action_type,
                "params": params
            })
            logger.debug("action_queued_for_batch", action=action_type)
        else:
            # Sofortige Ausführung
            try:
                result = await self._enhanced_executor.execute(action_type, params)
                self._results.append(result)
                
                # Wenn Action TTS hat
                if result.tts_message:
                    self._action_spoke = True
                    
            except Exception as exc:
                from brain_core.action_result import ActionResult
                self._results.append(ActionResult.error_result(
                    str(exc),
                    code="EXECUTION_ERROR"
                ))
                logger.error(
                    "enhanced_action_error",
                    action=action_type,
                    error=str(exc),
                )

    # ══════════════════════════════════════════════════════════════════════
    # Result Access
    # ══════════════════════════════════════════════════════════════════════

    def get_clean_text(self) -> str:
        """Gesamter bisheriger Text OHNE Action Tags."""
        return self._clean_text

    def get_full_text(self) -> str:
        """Gesamter bisheriger Text MIT Action Tags."""
        return self._full_text

    def get_fired_tags(self) -> List[str]:
        """Liste aller bisher gefeuerten Action Tags."""
        return list(self._fired_tags)

    def get_results(self) -> List["ActionResult"]:
        """
        Enhanced: Alle ActionResults der gefeuerten Actions.
        """
        return list(self._results)

    def get_reask_content(self) -> Optional[str]:
        """
        Enhanced: Kombinierter Re-Ask Content aller Actions.
        
        Wird ans LLM zurückgegeben für Zusammenfassung.
        """
        reask_parts = []
        for result in self._results:
            if result.reask_content:
                reask_parts.append(result.reask_content)
        
        return "\n\n---\n\n".join(reask_parts) if reask_parts else None

    def get_tts_messages(self) -> List[str]:
        """
        Enhanced: Alle TTS-Nachrichten der gefeuerten Actions.
        """
        return [
            r.tts_message for r in self._results
            if r.tts_message
        ]

    def has_errors(self) -> bool:
        """Enhanced: Ob eine Action fehlgeschlagen ist."""
        return any(not r.success for r in self._results)

    def get_errors(self) -> List[str]:
        """Enhanced: Alle Fehlermeldungen."""
        return [
            r.error_message for r in self._results
            if not r.success and r.error_message
        ]

    @property
    def action_spoke(self) -> bool:
        """Ob eine gefeuerte Action selber TTS ausgeführt hat."""
        return self._action_spoke

    @action_spoke.setter
    def action_spoke(self, value: bool) -> None:
        self._action_spoke = value

    @property
    def pending_count(self) -> int:
        """Anzahl pending Actions (Batch-Mode)."""
        return len(self._pending_actions)
